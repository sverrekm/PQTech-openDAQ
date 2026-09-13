# -*- coding: utf-8 -*-
"""PASV-medviten FTP-proxy — gjer eit instrument sin FTP tilgjengeleg over
Tailscale.

BlackBox-en (og andre instrument) står på eit isolert målenett. node1 når
FTP-en via NAT (10.99.0.1), men hub-sida gjer ikkje. Denne proxyen lyttar på
node1 sin tailscale-adresse og relayar til instrument-FTP-en, so ein
FTP-klient (eller PQSCADA) på tailnettet kan nå han.

Kvifor ikkje berre ein rå port-forward: FTP sin PASV-modus får serveren til
å annonsere SI EIGA adresse (t.d. 192.168.1.1) i 227-svaret. Gjennom NAT er
den adressa ubrukeleg for klienten. Proxyen fangar 227/229, skriv om til
sin eigen adresse, og opnar ein datakanal-proxy som koplar til instrumentet
på adressa NODEN når det på (10.99.0.1) — same insikt som instrument_ftp sin
makepasv-overstyring.

Rein stdlib. Bind til tailscale-adressa so han berre er open på tailnettet,
og berre mot ein PRIVAT målvert.
"""
from __future__ import annotations

import json
import logging
import os
import re
import socket
import subprocess
import threading

log = logging.getLogger("ftp_proxy")

KONFIG_FIL = "/data/konfig/ftp_proxy.json"

STANDARD = {
    "aktivert": False,
    "lytt_port": 2121,
    "maal_vert": "",      # tomt = bruk instrument_ftp sin vert
    "maal_port": 21,
    "berre_tailscale": True,   # bind berre tailscale-adressa
    # Eksplisitt bind-adresse. Overstyrer berre_tailscale når sett. Brukt på
    # HUBBEN: bind kontor-LAN-IP-en so office-maskiner kan FTP-e til hubben,
    # UTAN å eksponere relayet på den offentlege adressa (0.0.0.0).
    "bind_ip": "",
}

_227 = re.compile(rb"227[^\d]*\(?(\d+),(\d+),(\d+),(\d+),(\d+),(\d+)\)?")
_229 = re.compile(rb"229[^(]*\(([!-~])\1\1(\d+)\1\)")


def les_konfig() -> dict:
    k = dict(STANDARD)
    try:
        with open(KONFIG_FIL, "r", encoding="utf-8") as f:
            k.update(json.load(f) or {})
    except Exception:
        pass
    return k


def lagre_konfig(data: dict) -> tuple:
    k = les_konfig()
    try:
        lp = int(data.get("lytt_port", k["lytt_port"]))
        mp = int(data.get("maal_port", k["maal_port"]))
    except (TypeError, ValueError):
        return False, "Invalid port"
    if not (1 <= lp <= 65535 and 1 <= mp <= 65535):
        return False, "Port out of range"
    mv = str(data.get("maal_vert", k["maal_vert"])).strip()
    if mv and not _privat(mv):
        return False, "'%s' is not a private IP address" % mv
    bind = str(data.get("bind_ip", k["bind_ip"])).strip()
    if bind:
        try:
            import ipaddress
            a = ipaddress.ip_address(bind)
            if a.is_global:
                return False, "Refusing to bind a public address (%s)" % bind
        except ValueError:
            return False, "Invalid bind IP"
    k.update({
        "aktivert": bool(data.get("aktivert", k["aktivert"])),
        "lytt_port": lp, "maal_port": mp, "maal_vert": mv, "bind_ip": bind,
        "berre_tailscale": bool(data.get("berre_tailscale", k["berre_tailscale"])),
    })
    try:
        os.makedirs(os.path.dirname(KONFIG_FIL), exist_ok=True)
        with open(KONFIG_FIL, "w", encoding="utf-8") as f:
            json.dump(k, f, indent=2)
    except Exception as e:
        return False, "Could not save: %s" % e
    _behov_omstart.set()
    return True, "Saved"


def _privat(vert: str) -> bool:
    try:
        import instrument_proxy
        return instrument_proxy.tillat_vert(vert)
    except Exception:
        try:
            import ipaddress
            return ipaddress.ip_address(vert).is_private
        except Exception:
            return False


def _maalvert() -> str:
    v = les_konfig()["maal_vert"].strip()
    if v:
        return v
    try:
        import instrument_ftp
        return instrument_ftp.les_konfig().get("vert", "").strip()
    except Exception:
        return ""


def _tailscale_ip() -> str:
    try:
        r = subprocess.run(["tailscale", "ip", "-4"], capture_output=True,
                           text=True, timeout=5)
        for ln in r.stdout.splitlines():
            ln = ln.strip()
            if ln.startswith("100."):
                return ln
    except Exception:
        pass
    return ""


def _lokale_ip() -> list:
    """Ikkje-loopback IPv4 på denne maskina (for bind-val i GUI-et)."""
    ut = []
    try:
        r = subprocess.run(["ip", "-4", "-o", "addr"], capture_output=True,
                           text=True, timeout=5)
        for ln in r.stdout.splitlines():
            delar = ln.split()
            if len(delar) >= 4 and "/" in delar[3]:
                ip = delar[3].split("/")[0]
                if not ip.startswith("127."):
                    ut.append(ip)
    except Exception:
        pass
    return ut


def konfig_offentleg() -> dict:
    k = les_konfig()
    k["status"] = status()
    k["maal_vert_effektiv"] = _maalvert()
    k["tailscale_ip"] = _tailscale_ip()
    k["lokale_ip"] = _lokale_ip()
    return k


_tilstand = {"tilstand": "", "melding": "", "lytt": "", "maal": "",
             "aktive_okter": 0, "totalt_okter": 0}
_las = threading.Lock()
_stopp = threading.Event()
_behov_omstart = threading.Event()
_traad = None


def status() -> dict:
    with _las:
        return dict(_tilstand)


# --- Datakanal-proxy (per PASV) -------------------------------------
def _data_proxy(bind_ip: str, maal_vert: str, maal_port: int) -> int:
    """Opne ein eingongs data-lyttar. Returnerer porten den lyttar på.

    Første klient som koplar til blir relaya til instrumentet sin
    datakanal (på adressa NODEN når instrumentet på — ikkje den PASV
    annonserte). Lyttaren lukkar seg etter éi tilkopling.
    """
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((bind_ip, 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def kjoer():
        srv.settimeout(30)
        try:
            klient, _ = srv.accept()
        except Exception:
            srv.close()
            return
        srv.close()
        try:
            maal = socket.create_connection((maal_vert, maal_port), timeout=15)
        except Exception:
            klient.close()
            return
        _pump_begge(klient, maal)

    threading.Thread(target=kjoer, daemon=True).start()
    return port


def _pump_begge(a: socket.socket, b: socket.socket) -> None:
    def pump(src, dst):
        try:
            while True:
                d = src.recv(65536)
                if not d:
                    break
                dst.sendall(d)
        except Exception:
            pass
        finally:
            for s in (src, dst):
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass
    t = threading.Thread(target=pump, args=(a, b), daemon=True)
    t.start()
    pump(b, a)
    t.join(timeout=2)
    for s in (a, b):
        try:
            s.close()
        except Exception:
            pass


# --- Kontrollkanal per klient ---------------------------------------
def _handter(klient: socket.socket, bind_ip: str, maal_vert: str, maal_port: int) -> None:
    try:
        server = socket.create_connection((maal_vert, maal_port), timeout=15)
    except Exception as e:
        try:
            klient.sendall(("421 Cannot reach instrument FTP: %s\r\n" % e).encode())
        except Exception:
            pass
        klient.close()
        return
    with _las:
        _tilstand["aktive_okter"] += 1
        _tilstand["totalt_okter"] += 1

    def klient_til_server():
        try:
            while True:
                d = klient.recv(4096)
                if not d:
                    break
                server.sendall(d)
        except Exception:
            pass
        finally:
            for s in (klient, server):
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass

    def server_til_klient():
        buf = b""
        try:
            while True:
                d = server.recv(4096)
                if not d:
                    break
                buf += d
                # Prosesser komplette linjer for 227/229-omskriving
                while b"\r\n" in buf:
                    linje, buf = buf.split(b"\r\n", 1)
                    klient.sendall(_kanskje_skriv_om(linje, bind_ip,
                                                     maal_vert) + b"\r\n")
        except Exception:
            pass
        finally:
            for s in (klient, server):
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass

    t = threading.Thread(target=klient_til_server, daemon=True)
    t.start()
    server_til_klient()
    t.join(timeout=2)
    for s in (klient, server):
        try:
            s.close()
        except Exception:
            pass
    with _las:
        _tilstand["aktive_okter"] = max(0, _tilstand["aktive_okter"] - 1)


def _kanskje_skriv_om(linje: bytes, bind_ip: str, maal_vert: str) -> bytes:
    """Skriv om 227 (PASV) og 229 (EPSV) til å peike på proxyen."""
    m = _227.search(linje)
    if m:
        h = ".".join(m.group(i).decode() for i in range(1, 5))
        p = int(m.group(5)) * 256 + int(m.group(6))
        # Instrumentet annonserte h:p — men vi koplar datakanalen til
        # adressa NODEN når det på (maal_vert), same port.
        proxy_port = _data_proxy(bind_ip, maal_vert, p)
        b1, b2 = proxy_port // 256, proxy_port % 256
        ip_kommaer = bind_ip.replace(".", ",")
        return ("227 Entering Passive Mode (%s,%d,%d)."
                % (ip_kommaer, b1, b2)).encode()
    m = _229.search(linje)
    if m:
        p = int(m.group(2))
        proxy_port = _data_proxy(bind_ip, maal_vert, p)
        return ("229 Entering Extended Passive Mode (|||%d|)" % proxy_port).encode()
    return linje


def _server_loop() -> None:
    while not _stopp.is_set():
        _behov_omstart.clear()
        k = les_konfig()
        maal_vert = _maalvert()
        if not k["aktivert"] or not maal_vert:
            with _las:
                _tilstand.update(tilstand="av", melding=(
                    "" if k["aktivert"] else "disabled")
                    or ("no target host" if not maal_vert else ""))
            if _stopp.is_set():
                return
            _behov_omstart.wait(5)
            continue
        # Eksplisitt bind_ip vinn (hubben sin kontor-LAN); elles tailscale;
        # elles alle grensesnitt.
        if k.get("bind_ip"):
            bind_ip = k["bind_ip"]
        elif k.get("berre_tailscale", True):
            bind_ip = _tailscale_ip() or "0.0.0.0"
        else:
            bind_ip = "0.0.0.0"
        srv = socket.socket()
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            srv.bind((bind_ip, int(k["lytt_port"])))
            srv.listen(8)
            srv.settimeout(1.0)
        except Exception as e:
            with _las:
                _tilstand.update(tilstand="feil",
                                 melding="bind %s:%d — %s" % (bind_ip, k["lytt_port"], e))
            _behov_omstart.wait(15)
            continue
        with _las:
            _tilstand.update(tilstand="koeyrer",
                             lytt="%s:%d" % (bind_ip, k["lytt_port"]),
                             maal="%s:%d" % (maal_vert, k["maal_port"]),
                             melding="")
        log.info("FTP-proxy: %s:%d -> %s:%d", bind_ip, k["lytt_port"],
                 maal_vert, k["maal_port"])
        try:
            while not _stopp.is_set() and not _behov_omstart.is_set():
                try:
                    klient, _ = srv.accept()
                except socket.timeout:
                    continue
                except Exception:
                    break
                threading.Thread(target=_handter,
                                 args=(klient, bind_ip, maal_vert, int(k["maal_port"])),
                                 daemon=True).start()
        finally:
            try:
                srv.close()
            except Exception:
                pass


def start() -> None:
    global _traad
    if _traad is not None and _traad.is_alive():
        return
    _stopp.clear()
    _traad = threading.Thread(target=_server_loop, name="ftp-proxy", daemon=True)
    _traad.start()


def stopp() -> None:
    _stopp.set()
    _behov_omstart.set()

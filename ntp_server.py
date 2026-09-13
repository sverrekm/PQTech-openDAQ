# -*- coding: utf-8 -*-
"""Ein enkel NTP/SNTP-tidsserver på noden.

Instrument på eit isolert målenett (PQube 3, Elspec BlackBox m.fl.) treng
rett klokke for at tidsstempla på målingane skal stemme — men dei har ofte
ingen veg ut til ein NTP-server på internett. Noden står på same nett og har
klokka si (synka via uplink/Tailscale), so han kan vere tidsserveren.

Svarar på SNTP-førespurnader (RFC 4330) frå klientar. Rein stdlib: UDP-socket
+ struct. Serverar berre tid ut — han endrar ikkje si eiga klokke.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import struct
import subprocess
import sys
import threading
import time

log = logging.getLogger("ntp_server")

KONFIG_FIL = "/data/konfig/ntp.json"
NTP_EPOKE = 2208988800  # sekund mellom 1900-01-01 og 1970-01-01

STANDARD = {
    "aktivert": False,
    "port": 123,
    "stratum": 3,       # vi er ein sekundærserver som fylgjer uplinken vår
    "berre_privat": True,
    # Køyr responsen i VERTENS nettverk-namespace (via nsenter), ikkje berre i
    # containeren. Naudsynt for instrument som når noden på vertens wifi
    # (t.d. G4500 → 192.168.1.50): containeren kan ikkje binde vertens wlan0.
    "paa_vert": True,
}

# Markør i argv so vi finn (og kan drepe) host-prosessen att.
_VERT_MARKOR = "__ntp_host_serve__"


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
        port = int(data.get("port", k["port"]))
        stratum = int(data.get("stratum", k["stratum"]))
    except (TypeError, ValueError):
        return False, "Invalid port or stratum"
    if not 1 <= port <= 65535:
        return False, "Port %d out of range" % port
    if not 1 <= stratum <= 15:
        return False, "Stratum must be 1-15"
    k.update({
        "aktivert": bool(data.get("aktivert", k["aktivert"])),
        "port": port, "stratum": stratum,
        "berre_privat": bool(data.get("berre_privat", k["berre_privat"])),
        "paa_vert": bool(data.get("paa_vert", k["paa_vert"])),
    })
    try:
        os.makedirs(os.path.dirname(KONFIG_FIL), exist_ok=True)
        with open(KONFIG_FIL, "w", encoding="utf-8") as f:
            json.dump(k, f, indent=2)
    except Exception as e:
        return False, "Could not save: %s" % e
    _behov_omstart.set()
    return True, "Saved"


def konfig_offentleg() -> dict:
    k = les_konfig()
    k["status"] = status()
    return k


_tilstand = {"tilstand": "", "port": 0, "melding": "", "svar": 0,
             "sist_ts": None, "sist_klient": ""}
_las = threading.Lock()
_stopp = threading.Event()
_behov_omstart = threading.Event()
_traad = None


def status() -> dict:
    with _las:
        return dict(_tilstand)


def _privat(ip: str) -> bool:
    try:
        import ipaddress
        a = ipaddress.ip_address(ip)
        return a.is_private or a.is_loopback or a.is_link_local
    except Exception:
        return False


def _ntp_tid(t: float) -> tuple:
    """(sekund, fraksjon) i NTP-format frå eit Unix-tidspunkt."""
    sek = int(t) + NTP_EPOKE
    frac = int((t - int(t)) * (2 ** 32)) & 0xFFFFFFFF
    return sek & 0xFFFFFFFF, frac


def _svar(data: bytes, mottak: float, konfig: dict) -> bytes:
    """Bygg eit 48-byte SNTP server-svar (mode 4)."""
    stratum = int(konfig.get("stratum", 3))
    # LI=0 (ingen skotsekund-varsel), VN=4, Mode=4 (server)
    li_vn_mode = (0 << 6) | (4 << 3) | 4
    poll = data[2] if len(data) > 2 and data[2] else 6
    if poll > 17:            # poll er signert int8; hald det fornuftig
        poll = 6
    presisjon = -20          # ~mikrosekund-oppløysing (signert int8)
    root_delay = 0
    root_disp = 0
    ref_id = b"LOCL"  # lokal/uspesifisert referanse
    n = time.time()
    ref_s, ref_f = _ntp_tid(n - 1.0)      # sist "synka" ~ no
    # Originate = klienten sitt transmit-felt (byte 40..48) ekko-a tilbake
    orig = data[40:48] if len(data) >= 48 else b"\x00" * 8
    rec_s, rec_f = _ntp_tid(mottak)
    tx_s, tx_f = _ntp_tid(time.time())
    return (
        struct.pack("!BBbb", li_vn_mode, stratum, poll, presisjon)
        + struct.pack("!I", root_delay)
        + struct.pack("!I", root_disp)
        + ref_id
        + struct.pack("!II", ref_s, ref_f)   # reference
        + orig                                # originate (klienten sitt tx)
        + struct.pack("!II", rec_s, rec_f)    # receive
        + struct.pack("!II", tx_s, tx_f)      # transmit
    )


# --- Host-modus: køyr responsen i vertens netns via nsenter --------------
_vert_proc = None


def _vert_lyttar(port: int) -> bool:
    """Lyttar noko på UDP <port> i vertens netns? (ss via nsenter)"""
    try:
        r = subprocess.run(["nsenter", "-t", "1", "-n", "ss", "-uln"],
                           capture_output=True, text=True, timeout=6)
        return (":%d " % port) in r.stdout or (":%d\n" % port) in r.stdout
    except Exception:
        return False


def _drep_vert_prosess() -> None:
    try:
        subprocess.run(["nsenter", "-t", "1", "-n", "pkill", "-f", _VERT_MARKOR],
                       capture_output=True, timeout=6)
    except Exception:
        pass


def _start_vert_prosess(port: int, stratum: int) -> None:
    """Start NTP-responsen i VERTENS netns.

    `nsenter -t 1 -n` byter berre NETTVERK-namespace til verten; mount-ns er
    framleis containeren sin, so vi køyrer containeren sin python + denne
    fila, men bunden til vertens grensesnitt (wlan0 osv.).
    """
    global _vert_proc
    _drep_vert_prosess()
    try:
        _vert_proc = subprocess.Popen(
            ["nsenter", "-t", "1", "-n", sys.executable, os.path.abspath(__file__),
             _VERT_MARKOR, str(port), str(stratum)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
        log.info("NTP: starta host-responder i vertens netns (:%d)", port)
    except Exception as e:
        log.warning("NTP: kunne ikkje starte host-responder: %s", e)


def _blocking_server(port: int, stratum: int) -> None:
    """Enkel blokkerande SNTP-server (brukt av host-prosessen)."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", port))
    konf = {"stratum": stratum}
    while True:
        try:
            data, adr = srv.recvfrom(1024)
        except Exception:
            continue
        mottak = time.time()
        if not _privat(adr[0]):
            continue
        try:
            srv.sendto(_svar(data, mottak, konf), adr)
        except Exception:
            pass


def _server_loop() -> None:
    while not _stopp.is_set():
        _behov_omstart.clear()
        k = les_konfig()
        if not k["aktivert"]:
            _drep_vert_prosess()
            with _las:
                _tilstand.update(tilstand="av", melding="", port=k["port"])
            if _stopp.is_set():
                return
            _behov_omstart.wait(5)
            continue
        port = int(k["port"])
        # Host-modus: start responsen i vertens netns (så instrument på
        # vertens wifi kan nå han). Køyrer i tillegg til container-lyttaren.
        if k.get("paa_vert", True):
            _start_vert_prosess(port, int(k["stratum"]))
        srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            srv.bind(("0.0.0.0", port))
            srv.settimeout(1.0)
        except Exception as e:
            with _las:
                _tilstand.update(tilstand="feil", port=port,
                                 melding="Could not bind :%d — %s" % (port, e)
                                 + (" (needs privilege for <1024)" if port < 1024 else ""))
            log.warning("NTP: bind :%d feila: %s", port, e)
            _behov_omstart.wait(15)
            continue
        vert_ok = _vert_lyttar(port) if k.get("paa_vert", True) else None
        with _las:
            _tilstand.update(tilstand="koeyrer", port=port,
                             paa_vert=bool(k.get("paa_vert", True)),
                             vert_lyttar=vert_ok,
                             melding="Serving time"
                             + (" (host wlan0)" if vert_ok else
                                " — container only; host responder not listening"
                                if k.get("paa_vert", True) else ""))
        log.info("NTP-server lyttar på :%d (stratum %d, paa_vert=%s vert_ok=%s)",
                 port, k["stratum"], k.get("paa_vert", True), vert_ok)
        _sist_sjekk = time.time()
        try:
            while not _stopp.is_set() and not _behov_omstart.is_set():
                # Vakt: hald host-prosessen i live og oppdater status.
                if k.get("paa_vert", True) and time.time() - _sist_sjekk > 20:
                    _sist_sjekk = time.time()
                    if _vert_proc is not None and _vert_proc.poll() is not None:
                        _start_vert_prosess(port, int(k["stratum"]))
                    with _las:
                        _tilstand["vert_lyttar"] = _vert_lyttar(port)
                try:
                    data, adr = srv.recvfrom(1024)
                except socket.timeout:
                    continue
                except Exception:
                    break
                mottak = time.time()
                if k.get("berre_privat", True) and not _privat(adr[0]):
                    continue
                try:
                    srv.sendto(_svar(data, mottak, k), adr)
                    with _las:
                        _tilstand["svar"] += 1
                        _tilstand["sist_ts"] = mottak
                        _tilstand["sist_klient"] = adr[0]
                except Exception:
                    pass
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
    _traad = threading.Thread(target=_server_loop, name="ntp-server", daemon=True)
    _traad.start()


def stopp() -> None:
    _stopp.set()
    _behov_omstart.set()
    _drep_vert_prosess()


# Køyrd av host-prosessen: `nsenter -t 1 -n python3 ntp_server.py
# __ntp_host_serve__ <port> <stratum>` — bind vertens grensesnitt.
if __name__ == "__main__" and len(sys.argv) >= 2 and sys.argv[1] == _VERT_MARKOR:
    _p = int(sys.argv[2]) if len(sys.argv) > 2 else 123
    _s = int(sys.argv[3]) if len(sys.argv) > 3 else 3
    try:
        _blocking_server(_p, _s)
    except Exception as _e:
        log.warning("NTP host-responder stoppa: %s", _e)

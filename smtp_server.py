# -*- coding: utf-8 -*-
"""Ein liten SMTP-server på noden — tek imot e-post frå instrument.

Mange måleinstrument (PQube 3, Elspec BlackBox m.fl.) er SMTP-KLIENTAR: dei
mailar periodiske rapportar og hendingsvarsel til ein server. Lèt noden vere
den serveren, so fangar vi rapportane utan at dei må innom ei sky.

Ein motteken e-post blir arkivert som .eml, vedlegg lagra for seg, og
CSV-vedlegg mata inn i same kanal-pipeline som FTP-rapportane
([[instrument_ftp]]): då blir ein innmaila rapport til openDAQ-kanalar.

Rein stdlib: rå socket + `email`-parsing. Ingen TLS (instrument på eit
lukka målenett bruker som regel klartekst); valfri AUTH LOGIN/PLAIN.
"""
from __future__ import annotations

import base64
import email
import json
import logging
import os
import re
import socket
import threading
import time
from email.header import decode_header

log = logging.getLogger("smtp_server")

KONFIG_FIL = "/data/konfig/smtp.json"
LOGG_FIL = "/data/konfig/smtp_meldingar.json"

STANDARD = {
    "aktivert": False,
    "port": 25,                 # instrument mailar typisk til 25
    "krev_auth": False,
    "brukar": "",
    "passord": "",
    "maalkatalog": "/data/maalingar/epost",
    # CSV-vedlegg -> kanalar via instrument_ftp sin pipeline
    "trekk_kanalar": True,
    "maks_mb": 25,
    # Berre ta imot frå private IP-ar (instrument på målenettet)
    "berre_privat": True,
    "hugs_meldingar": 200,
}


# --- Konfig ----------------------------------------------------------
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
    except (TypeError, ValueError):
        return False, "Invalid port"
    if not 1 <= port <= 65535:
        return False, "Port %d out of range" % port
    k.update({
        "aktivert": bool(data.get("aktivert", k["aktivert"])),
        "port": port,
        "krev_auth": bool(data.get("krev_auth", k["krev_auth"])),
        "brukar": str(data.get("brukar", k["brukar"])),
        "maalkatalog": str(data.get("maalkatalog", k["maalkatalog"])) or STANDARD["maalkatalog"],
        "trekk_kanalar": bool(data.get("trekk_kanalar", k["trekk_kanalar"])),
        "berre_privat": bool(data.get("berre_privat", k["berre_privat"])),
    })
    nytt = data.get("passord")
    if nytt:                       # tomt = ikkje endra
        k["passord"] = str(nytt)
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
    k["passord_sett"] = bool(k.pop("passord", ""))
    k["status"] = status()
    return k


def _privat(ip: str) -> bool:
    try:
        import ipaddress
        a = ipaddress.ip_address(ip)
        return a.is_private or a.is_loopback or a.is_link_local
    except Exception:
        return False


# --- Tilstand + meldingslogg ----------------------------------------
_tilstand = {"tilstand": "", "port": 0, "melding": "",
             "motteke": 0, "sist_ts": None, "sist_frå": "", "sist_emne": ""}
_meldingar: list = []     # nyaste sist
_las = threading.Lock()
_stopp = threading.Event()
_behov_omstart = threading.Event()
_traad = None


def status() -> dict:
    with _las:
        return dict(_tilstand)


def meldingar() -> list:
    try:
        with open(LOGG_FIL, "r", encoding="utf-8") as f:
            return json.load(f) or []
    except Exception:
        with _las:
            return list(_meldingar)


def _logg_melding(m: dict) -> None:
    k = les_konfig()
    maks = int(k.get("hugs_meldingar", 200))
    with _las:
        _meldingar.append(m)
        del _meldingar[:-maks]
        _tilstand["motteke"] += 1
        _tilstand["sist_ts"] = m["ts"]
        _tilstand["sist_frå"] = m.get("frå", "")
        _tilstand["sist_emne"] = m.get("emne", "")
        snap = list(_meldingar)
    try:
        os.makedirs(os.path.dirname(LOGG_FIL), exist_ok=True)
        with open(LOGG_FIL, "w", encoding="utf-8") as f:
            json.dump(snap, f)
    except Exception:
        pass


# --- Lagring + parsing av ei melding --------------------------------
_UGYLDIG = re.compile(r"[^A-Za-z0-9._-]+")


def _trygt_namn(namn: str) -> str:
    namn = _UGYLDIG.sub("_", namn or "").strip("_")
    return namn[:120] or "vedlegg"


def _dekod(verdi: str) -> str:
    try:
        bitar = decode_header(verdi or "")
        ut = ""
        for tekst, kod in bitar:
            ut += tekst.decode(kod or "utf-8", "replace") if isinstance(tekst, bytes) else tekst
        return ut.strip()
    except Exception:
        return verdi or ""


def _lagre_melding(raa: bytes, frå_ip: str) -> dict:
    k = les_konfig()
    kat = k["maalkatalog"]
    os.makedirs(kat, exist_ok=True)
    ts = time.time()
    stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(ts))
    eml = os.path.join(kat, "%s_%s.eml" % (stamp, _trygt_namn(frå_ip)))
    try:
        with open(eml, "wb") as f:
            f.write(raa)
    except Exception as e:
        log.warning("SMTP: kunne ikkje lagre .eml: %s", e)

    frå = emne = ""
    vedlegg = []
    csv_til_kanalar = []
    try:
        msg = email.message_from_bytes(raa)
        frå = _dekod(msg.get("From", ""))
        emne = _dekod(msg.get("Subject", ""))
        for del_ in msg.walk():
            if del_.is_multipart():
                continue
            filnamn = del_.get_filename()
            if not filnamn:
                continue
            filnamn = _dekod(filnamn)
            data = del_.get_payload(decode=True) or b""
            sti = os.path.join(kat, "%s_%s" % (stamp, _trygt_namn(filnamn)))
            try:
                with open(sti, "wb") as f:
                    f.write(data)
                vedlegg.append({"namn": filnamn, "bytes": len(data)})
                if filnamn.lower().endswith(".csv"):
                    csv_til_kanalar.append((filnamn, data))
            except Exception as e:
                log.warning("SMTP: vedlegg %s feila: %s", filnamn, e)
    except Exception as e:
        log.warning("SMTP: parse feila: %s", e)

    # CSV-rapportar -> kanalar via instrument_ftp sin pipeline
    if k.get("trekk_kanalar") and csv_til_kanalar:
        _mat_csv_til_kanalar(csv_til_kanalar)

    m = {"ts": ts, "frå": frå or frå_ip, "frå_ip": frå_ip, "emne": emne,
         "vedlegg": vedlegg, "eml": os.path.basename(eml)}
    _logg_melding(m)
    log.info("SMTP: e-post frå %s (%s), %d vedlegg", frå or frå_ip, emne, len(vedlegg))
    return m


def _mat_csv_til_kanalar(csv_filer: list) -> None:
    """Legg emailede CSV-rapportar i instrument_ftp sin målekatalog og lat
    same kanal-pipeline plukke dei opp."""
    try:
        import instrument_ftp
        kat = instrument_ftp.les_konfig()["maalkatalog"]
        os.makedirs(kat, exist_ok=True)
        for filnamn, data in csv_filer:
            sti = os.path.join(kat, _trygt_namn(filnamn))
            with open(sti, "wb") as f:
                f.write(data)
        instrument_ftp.oppdater_kanalar()
    except Exception as e:
        log.warning("SMTP: CSV->kanalar feila: %s", e)


# --- SMTP-protokoll (rå socket) -------------------------------------
def _handter(konn: socket.socket, adr, konfig: dict) -> None:
    frå_ip = adr[0]
    maks = int(konfig.get("maks_mb", 25)) * 1024 * 1024
    vert = socket.gethostname() or "opendaq"

    def send(linje: str):
        try:
            konn.sendall((linje + "\r\n").encode("utf-8", "replace"))
        except Exception:
            pass

    if konfig.get("berre_privat", True) and not _privat(frå_ip):
        send("554 Only private senders allowed")
        konn.close()
        return

    f = konn.makefile("rb")
    autentisert = not konfig.get("krev_auth", False)

    def les() -> str:
        return f.readline().decode("utf-8", "replace").rstrip("\r\n")

    try:
        konn.settimeout(30)
        send("220 %s PQTech openDAQ SMTP" % vert)
        while not _stopp.is_set():
            linje = les()
            if not linje:
                break
            kmd = linje.split(" ", 1)[0].upper()
            arg = linje[len(kmd):].strip()

            if kmd == "EHLO":
                send("250-%s" % vert)
                if konfig.get("krev_auth"):
                    send("250-AUTH LOGIN PLAIN")
                send("250 SIZE %d" % maks)
            elif kmd == "HELO":
                send("250 %s" % vert)
            elif kmd == "AUTH":
                autentisert = _auth(arg, les, send, konfig)
            elif kmd == "MAIL":
                send("250 OK")
            elif kmd == "RCPT":
                if not autentisert:
                    send("530 Authentication required")
                else:
                    send("250 OK")
            elif kmd == "DATA":
                if not autentisert:
                    send("530 Authentication required")
                    continue
                send("354 End data with <CR><LF>.<CR><LF>")
                raa = bytearray()
                overflow = False
                while True:
                    ln = f.readline()
                    if not ln:
                        break
                    if ln in (b".\r\n", b".\n"):
                        break
                    if ln.startswith(b".."):
                        ln = ln[1:]
                    if len(raa) < maks:
                        raa.extend(ln)
                    else:
                        overflow = True
                if overflow:
                    send("552 Message too large")
                else:
                    try:
                        _lagre_melding(bytes(raa), frå_ip)
                        send("250 OK message stored")
                    except Exception as e:
                        log.warning("SMTP: lagring feila: %s", e)
                        send("451 Local error storing message")
            elif kmd == "RSET":
                send("250 OK")
            elif kmd == "NOOP":
                send("250 OK")
            elif kmd == "QUIT":
                send("221 Bye")
                break
            else:
                send("502 Command not implemented")
    except Exception:
        pass
    finally:
        try:
            f.close()
        except Exception:
            pass
        try:
            konn.close()
        except Exception:
            pass


def _auth(arg: str, les, send, konfig: dict) -> bool:
    bruk = konfig.get("brukar", "")
    pas = konfig.get("passord", "")

    def sjekk(u: str, p: str) -> bool:
        return u == bruk and p == pas

    try:
        delar = arg.split()
        mek = (delar[0].upper() if delar else "")
        if mek == "PLAIN":
            b64 = delar[1] if len(delar) > 1 else les()
            if not delar[1:]:
                send("334 ")
                b64 = les()
            raw = base64.b64decode(b64).split(b"\x00")
            if len(raw) >= 3 and sjekk(raw[1].decode(), raw[2].decode()):
                send("235 2.7.0 Authentication successful")
                return True
        elif mek == "LOGIN":
            send("334 " + base64.b64encode(b"Username:").decode())
            u = base64.b64decode(les()).decode("utf-8", "replace")
            send("334 " + base64.b64encode(b"Password:").decode())
            p = base64.b64decode(les()).decode("utf-8", "replace")
            if sjekk(u, p):
                send("235 2.7.0 Authentication successful")
                return True
    except Exception:
        pass
    send("535 5.7.8 Authentication failed")
    return False


def _server_loop() -> None:
    global _traad
    while not _stopp.is_set():
        _behov_omstart.clear()
        k = les_konfig()
        if not k["aktivert"]:
            with _las:
                _tilstand.update(tilstand="av", melding="", port=k["port"])
            _behov_omstart.wait(5) if not _stopp.is_set() else None
            if _stopp.is_set():
                return
            continue
        port = int(k["port"])
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            srv.bind(("0.0.0.0", port))
            srv.listen(8)
            srv.settimeout(1.0)
        except Exception as e:
            with _las:
                _tilstand.update(tilstand="feil", port=port,
                                 melding="Could not bind :%d — %s"
                                 % (port, e) + (" (needs privilege for <1024)"
                                                if port < 1024 else ""))
            log.warning("SMTP: bind :%d feila: %s", port, e)
            # Vent på konfig-endring før nytt forsøk
            _behov_omstart.wait(15)
            continue
        with _las:
            _tilstand.update(tilstand="koeyrer", port=port, melding="Listening")
        log.info("SMTP-server lyttar på :%d", port)
        try:
            while not _stopp.is_set() and not _behov_omstart.is_set():
                try:
                    konn, adr = srv.accept()
                except socket.timeout:
                    continue
                except Exception:
                    break
                threading.Thread(target=_handter, args=(konn, adr, k),
                                 daemon=True).start()
        finally:
            try:
                srv.close()
            except Exception:
                pass


def start() -> None:
    """Start server-løkka. Trygg å kalle fleire gonger."""
    global _traad
    if _traad is not None and _traad.is_alive():
        return
    _stopp.clear()
    _traad = threading.Thread(target=_server_loop, name="smtp-server", daemon=True)
    _traad.start()


def stopp() -> None:
    _stopp.set()
    _behov_omstart.set()

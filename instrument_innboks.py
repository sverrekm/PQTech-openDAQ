#!/usr/bin/env python3
"""
FTP-innboks: instrument som PUSHAR filene sine hit
==================================================
Nokre instrument tek ikkje imot FTP-henting, men PUSHAR data. PQube 3 har
INGA SFTP-push — berre FTP-push: ved hendings-trigger lagar han ein .tar.gz av
event-filene (PQDIF-bølgeform + CSV) og skyv han til ein FTP-server. Difor
køyrer vi ein liten, LÅST FTP-server (pyftpdlib) på noden: éin virtuell brukar,
heime-katalog = opplastingsmappa, passive porter, kun privat LAN. Ein vaktetråd
tek imot filene og matar dei inn i same pipeline som FTP-henting/e-post:

  - .tar.gz / .tgz     → pakkast ut, medlemmane handterast under
  - .csv               → kanalar (via smtp_server._mat_csv_til_kanalar)
  - alt (PQDIF/bølgeform/…) → arkivert til NAS/SSD (per kunde/node)

Konfig: /data/konfig/innboks.json
"""

import os
import io
import json
import time
import string
import secrets
import shutil
import tarfile
import logging
import threading
import subprocess

log = logging.getLogger("instrument_innboks")

KONFIG_FIL = "/data/konfig/innboks.json"
BRUKAR = "pqinnboks"
OPPLAST = "/data/innkomande/opplasting"   # FTP heime-katalog (instrumentet skriv hit)
FERDIG = "/data/innkomande_ferdig"        # prosesserte filer
FTP_KONTROLLPORT = 21
FTP_PASSIVE_FRA = 30000
FTP_PASSIVE_TIL = 30009

# Rest frå SFTP-varianten — vert rydda bort (PQube kan ikkje SFTP-pushe).
SSHD_CONFIG = "/etc/ssh/sshd_config"
MATCH_MARKER = "# --- pqtech instrument-innboks (auto) ---"
MATCH_SLUTT = "# --- slutt pqtech instrument-innboks ---"

STANDARD = {
    "aktivert": False,
    "brukar": BRUKAR,
    "passord": "",
    "kunde": "",
    "kanal_prefiks": "",
}

_stopp = threading.Event()
_traad = None                 # vaktetråd
_ftp_server = None            # pyftpdlib FTPServer
_ftp_traad = None
_storleik_cache = {}
_tilstand = {"tilstand": "", "melding": "", "mottatt_totalt": 0,
             "sist_fil": "", "sist_ts": None, "ftp": "av"}


# --- Konfig ----------------------------------------------------------
def les_konfig() -> dict:
    k = dict(STANDARD)
    try:
        with open(KONFIG_FIL, "r", encoding="utf-8") as f:
            k.update(json.load(f) or {})
    except Exception:
        pass
    k["brukar"] = BRUKAR
    return k


def lagre_konfig(k: dict) -> bool:
    try:
        os.makedirs(os.path.dirname(KONFIG_FIL), exist_ok=True)
        with open(KONFIG_FIL, "w", encoding="utf-8") as f:
            json.dump(k, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        log.error("Kunne ikkje lagre innboks-konfig: %s", e)
        return False


def generer_passord(n: int = 20) -> str:
    alfabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alfabet) for _ in range(n))


def konfig_offentleg() -> dict:
    """Konfig + tilkoblingsdetaljar for GUI (host/bruker/passord/port) som skal
    skrivast inn på instrumentet sine FTP-push-innstillingar."""
    k = les_konfig()
    return {
        "aktivert": k["aktivert"],
        "protokoll": "FTP",
        "brukar": k["brukar"],
        "passord": k["passord"],
        "kunde": k.get("kunde", ""),
        "kanal_prefiks": k.get("kanal_prefiks", ""),
        "vert": _lan_ip(),
        "port": FTP_KONTROLLPORT,
        "fjern_sti": "/",
        "status": status(),
    }


# --- Hjelparar -------------------------------------------------------
def _lan_ip() -> str:
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.168.1.1", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        try:
            import socket
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return ""


def _sikre_pyftpdlib() -> bool:
    """pyftpdlib er rein Python — installer ved oppstart viss han manglar
    (fleet-update byggjer ikkje imaget på nytt)."""
    try:
        import pyftpdlib  # noqa: F401
        return True
    except Exception:
        pass
    try:
        log.info("Installerer pyftpdlib (fyrste gong)...")
        subprocess.run(["pip", "install", "--no-cache-dir", "pyftpdlib"],
                       check=False, capture_output=True, timeout=180)
        import pyftpdlib  # noqa: F401
        return True
    except Exception as e:
        log.error("Kunne ikkje installere pyftpdlib: %s", e)
        return False


def _fjern_sshd_match() -> None:
    """Rydd bort SFTP Match-blokka frå den tidlegare SFTP-varianten (PQube kan
    ikkje SFTP-pushe, so vi treng han ikkje)."""
    try:
        with open(SSHD_CONFIG, "r") as f:
            innhald = f.read()
    except Exception:
        return
    if MATCH_MARKER not in innhald:
        return
    linjer = innhald.splitlines(keepends=True)
    ut, hopp = [], False
    for ln in linjer:
        if ln.strip() == MATCH_MARKER.strip():
            hopp = True
            continue
        if hopp:
            if ln.strip() == MATCH_SLUTT.strip():
                hopp = False
            continue
        ut.append(ln)
    try:
        with open(SSHD_CONFIG, "w") as f:
            f.write("".join(ut))
        subprocess.run(
            ["bash", "-c",
             "kill -HUP $(cat /run/sshd.pid 2>/dev/null || pidof sshd) 2>/dev/null"],
            check=False)
        log.info("Rydda bort gamal SFTP Match-blokk")
    except Exception:
        pass


# --- FTP-server (pyftpdlib) ------------------------------------------
def _start_ftp(k: dict) -> None:
    global _ftp_server, _ftp_traad
    if not _sikre_pyftpdlib():
        _tilstand["ftp"] = "feil: pyftpdlib manglar"
        return
    from pyftpdlib.authorizers import DummyAuthorizer
    from pyftpdlib.handlers import FTPHandler
    from pyftpdlib.servers import FTPServer

    # Stopp evt. eksisterande server (t.d. passord endra)
    _stopp_ftp()

    os.makedirs(OPPLAST, exist_ok=True)
    aut = DummyAuthorizer()
    # perm: e=cd, l=list, r=hent, a=append, d=slett, f=rename, m=mkdir,
    #       w=lagre(STOR), M=chmod, T=set mtime — full opplasting
    aut.add_user(k["brukar"], k["passord"], OPPLAST, perm="elradfmwMT")
    handler = FTPHandler
    handler.authorizer = aut
    # Ikkje set masquerade_address: node2 er direkte nåbar på både LAN-IP
    # (PQuben) og tailscale-IP (fjern-tilgang), utan NAT imellom. Då vel
    # pyftpdlib rett interface-IP per tilkopling for PASV — feil hardkoda IP
    # ville broten passiv dataoverføring frå den andre vegen.
    handler.passive_ports = range(FTP_PASSIVE_FRA, FTP_PASSIVE_TIL + 1)
    handler.banner = "PQTech openDAQ FTP-innboks"
    try:
        srv = FTPServer(("0.0.0.0", FTP_KONTROLLPORT), handler)
        srv.max_cons = 32
        srv.max_cons_per_ip = 8
    except Exception as e:
        log.error("Kunne ikkje binde FTP-port %d: %s", FTP_KONTROLLPORT, e)
        _tilstand["ftp"] = f"feil: port {FTP_KONTROLLPORT} ({e})"
        return
    _ftp_server = srv

    def _kjor():
        try:
            srv.serve_forever(timeout=1, handle_exit=True)
        except Exception as e:
            log.warning("FTP-server avslutta: %s", e)

    _ftp_traad = threading.Thread(target=_kjor, name="innboks-ftp", daemon=True)
    _ftp_traad.start()
    _tilstand["ftp"] = f"lyttar :{FTP_KONTROLLPORT}"
    log.info("FTP-innboks lyttar på :%d (bruker %s, passive %d-%d)",
             FTP_KONTROLLPORT, k["brukar"], FTP_PASSIVE_FRA, FTP_PASSIVE_TIL)


def _stopp_ftp() -> None:
    global _ftp_server
    if _ftp_server is not None:
        try:
            _ftp_server.close_all()
        except Exception:
            pass
        _ftp_server = None
        _tilstand["ftp"] = "av"


def oppsett() -> tuple:
    """Sørg for katalogar + (re)start FTP-server. Krev root for port 21."""
    k = les_konfig()
    _fjern_sshd_match()   # rydd bort SFTP-rest uansett
    if not k["aktivert"]:
        _stopp_ftp()
        return True, "deaktivert"
    if not k["passord"]:
        k["passord"] = generer_passord()
        lagre_konfig(k)
    try:
        os.makedirs(OPPLAST, exist_ok=True)
        os.makedirs(FERDIG, exist_ok=True)
    except Exception as e:
        return False, f"katalog-oppsett feila: {e}"
    _start_ftp(k)
    return True, "oppsett ok"


# --- Ingest ----------------------------------------------------------
def _nas_maal() -> str:
    if os.path.ismount("/data/nas") or os.path.isdir("/data/nas"):
        return "/data/nas/innboks"
    return "/data/maalingar/innboks"


def _node_namn() -> str:
    try:
        from push_konfig import les_push_konfig
        n = les_push_konfig().node_namn
        if n:
            return n
    except Exception:
        pass
    try:
        import socket
        return socket.gethostname()
    except Exception:
        return "node"


def _trygt(namn: str) -> str:
    ut = "".join(c for c in os.path.basename(namn)
                 if c.isalnum() or c in "._- ").strip()
    return ut[:150] or "fil"


def _arkivkatalog(k: dict) -> str:
    delar = [_nas_maal()]
    kunde = (k.get("kunde") or "").strip()
    if kunde:
        delar.append(_trygt(kunde))
    delar.append(_trygt(_node_namn()))
    kat = os.path.join(*delar)
    os.makedirs(kat, exist_ok=True)
    return kat


def _handter_fil(namn: str, data: bytes, k: dict) -> None:
    """Arkiver ei enkelt-fil + CSV → kanalar."""
    kat = _arkivkatalog(k)
    try:
        with open(os.path.join(kat, _trygt(namn)), "wb") as f:
            f.write(data)
    except Exception as e:
        log.warning("Innboks-arkivering av %s feila: %s", namn, e)
    if namn.lower().endswith(".csv"):
        try:
            import smtp_server
            pfx = (k.get("kanal_prefiks") or "").strip()
            smtp_server._mat_csv_til_kanalar(
                [((pfx + os.path.basename(namn)) if pfx else os.path.basename(namn), data)])
        except Exception as e:
            log.warning("Innboks CSV->kanalar (%s) feila: %s", namn, e)


def _prosesser_fil(sti: str, k: dict) -> None:
    namn = os.path.basename(sti)
    low = namn.lower()
    if low.endswith((".tar.gz", ".tgz", ".tar")):
        # PQube event-pakke: pakk ut og handter kvart medlem
        try:
            with tarfile.open(sti, "r:*") as tar:
                for m in tar.getmembers():
                    if not m.isfile():
                        continue
                    try:
                        f = tar.extractfile(m)
                        if f is None:
                            continue
                        _handter_fil(os.path.basename(m.name), f.read(), k)
                    except Exception as e:
                        log.warning("Innboks tar-medlem %s feila: %s", m.name, e)
        except Exception as e:
            log.warning("Innboks kunne ikkje pakke ut %s: %s", namn, e)
        # Arkiver sjølve .tar.gz òg (rå)
        try:
            with open(sti, "rb") as f:
                _handter_raa(namn, f.read(), k)
        except Exception:
            pass
    else:
        try:
            with open(sti, "rb") as f:
                _handter_fil(namn, f.read(), k)
        except Exception as e:
            log.warning("Innboks-fil %s feila: %s", namn, e)


def _handter_raa(namn: str, data: bytes, k: dict) -> None:
    """Arkiver ei rå-fil (t.d. sjølve .tar.gz) utan CSV-parsing."""
    kat = _arkivkatalog(k)
    try:
        with open(os.path.join(kat, _trygt(namn)), "wb") as f:
            f.write(data)
    except Exception:
        pass


def _prosesser(k: dict) -> None:
    try:
        filer = [f for f in os.listdir(OPPLAST)
                 if os.path.isfile(os.path.join(OPPLAST, f))]
    except Exception:
        return
    no = time.time()
    for f in filer:
        sti = os.path.join(OPPLAST, f)
        try:
            st = os.stat(sti)
        except Exception:
            continue
        forrige = _storleik_cache.get(f)
        _storleik_cache[f] = (st.st_size, no)
        stabil = (forrige is not None and forrige[0] == st.st_size
                  and (no - st.st_mtime) >= 8)
        if not stabil:
            continue
        _prosesser_fil(sti, k)
        try:
            os.makedirs(FERDIG, exist_ok=True)
            shutil.move(sti, os.path.join(FERDIG, _trygt(f)))
        except Exception:
            try:
                os.remove(sti)
            except Exception:
                pass
        _storleik_cache.pop(f, None)
        _tilstand.update(tilstand="ok", melding="Fil mottatt",
                         mottatt_totalt=_tilstand["mottatt_totalt"] + 1,
                         sist_fil=f, sist_ts=no)
        log.info("Innboks: tok imot + handterte %s", f)


def status() -> dict:
    ut = dict(_tilstand)
    ut["kjorer"] = _traad is not None and _traad.is_alive()
    ut["ftp_kjorer"] = _ftp_traad is not None and _ftp_traad.is_alive()
    return ut


def _loop() -> None:
    _stopp.wait(15)
    while not _stopp.is_set():
        k = les_konfig()
        if k["aktivert"]:
            try:
                _prosesser(k)
            except Exception as e:
                log.warning("Innboks-loop feil: %s", e)
        _stopp.wait(15)


def start() -> None:
    """Kjør oppsett (FTP-server) + start vaktetråd. Trygg å kalle fleire gonger."""
    global _traad
    try:
        ok, m = oppsett()
        if not ok:
            log.warning("Innboks-oppsett: %s", m)
    except Exception as e:
        log.warning("Innboks-oppsett unntak: %s", e)
    if _traad is not None and _traad.is_alive():
        return
    _stopp.clear()
    _traad = threading.Thread(target=_loop, name="instrument-innboks", daemon=True)
    _traad.start()


def stopp() -> None:
    _stopp.set()
    _stopp_ftp()

#!/usr/bin/env python3
"""
SFTP-innboks: instrument som PUSHAR filene sine hit
===================================================
Nokre instrument (t.d. PQube 3) har inga FTP-teneste å hente frå, men kan
PUSHE data (hendings-/bølgeform-/trend-filer) via SFTP. Containeren køyrer
alt sshd, so her set vi opp ein LÅST sftp-berre-brukar (chroot, internal-sftp,
ingen shell, ingen port-forwarding) + ein vaktetråd som tek imot nye filer og
matar dei inn i same pipeline som FTP-henting/e-post:

  - .csv               → kanalar (via smtp_server._mat_csv_til_kanalar)
  - alt (inkl. bølgeform/PQDIF/PQZip) → arkivert til NAS/SSD

Oppsettet (brukar + sshd Match-blokk) gjerast frå Python ved oppstart og ved
aktivering — containeren køyrer som root. Idempotent og sjølv-lækjande: køyrer
på nytt kvar oppstart, so det overlever container-recreate.

Konfig: /data/konfig/innboks.json
"""

import os
import json
import time
import string
import secrets
import shutil
import logging
import threading
import subprocess

log = logging.getLogger("instrument_innboks")

KONFIG_FIL = "/data/konfig/innboks.json"
BRUKAR = "pqinnboks"
CHROOT = "/data/innkomande"              # chroot-rot (root-eigd, chroot-krav)
OPPLAST = CHROOT + "/opplasting"         # her skriv instrumentet (inne: /opplasting)
FERDIG = "/data/innkomande_ferdig"       # prosesserte filer (utanfor chroot)
SSHD_CONFIG = "/etc/ssh/sshd_config"
MATCH_MARKER = "# --- pqtech instrument-innboks (auto) ---"
MATCH_SLUTT = "# --- slutt pqtech instrument-innboks ---"

STANDARD = {
    "aktivert": False,
    "brukar": BRUKAR,
    "passord": "",           # auto-generert ved fyrste aktivering
    "kunde": "",             # for NAS-gruppering (valfritt)
    "kanal_prefiks": "",     # prefiks på kanalnamn frå CSV (valfritt)
}

_stopp = threading.Event()
_traad = None
_tilstand = {"tilstand": "", "melding": "", "mottatt_totalt": 0,
             "sist_fil": "", "sist_ts": None}
_storleik_cache = {}         # filnamn -> (storleik, sist_sett) for stabilitetssjekk


# --- Konfig ----------------------------------------------------------
def les_konfig() -> dict:
    k = dict(STANDARD)
    try:
        with open(KONFIG_FIL, "r", encoding="utf-8") as f:
            k.update(json.load(f) or {})
    except Exception:
        pass
    k["brukar"] = BRUKAR  # alltid fast
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
    """Konfig + tilkoblingsdetaljar for GUI. Passordet SKAL visast her — det er
    nodens eigen innboks-passord som operatøren må skrive inn på instrumentet."""
    k = les_konfig()
    return {
        "aktivert": k["aktivert"],
        "brukar": k["brukar"],
        "passord": k["passord"],
        "kunde": k.get("kunde", ""),
        "kanal_prefiks": k.get("kanal_prefiks", ""),
        "vert": _lan_ip(),
        "port": 22,
        "fjern_sti": "/opplasting",
        "status": status(),
    }


# --- Oppsett (root): brukar + chroot + sshd Match --------------------
def _lan_ip() -> str:
    """LAN-IP instrumentet skal pushe til (same nett som instrumentet)."""
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.168.1.1", 1))   # rutar ikkje, berre for å velje iface
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        try:
            import socket
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return ""


def _sikre_sshd_match() -> None:
    """Legg til ein låst Match-blokk for innboks-brukaren (idempotent).
    Validerer med `sshd -t` FØR den byter fila, so vi aldri knekk sshd."""
    try:
        with open(SSHD_CONFIG, "r") as f:
            innhald = f.read()
    except Exception as e:
        log.warning("Les sshd_config feila: %s", e)
        return
    if MATCH_MARKER in innhald:
        return
    blokk = (
        f"\n{MATCH_MARKER}\n"
        f"Match User {BRUKAR}\n"
        f"    ChrootDirectory {CHROOT}\n"
        f"    ForceCommand internal-sftp\n"
        f"    AllowTcpForwarding no\n"
        f"    X11Forwarding no\n"
        f"    PermitTunnel no\n"
        f"    PasswordAuthentication yes\n"
        f"{MATCH_SLUTT}\n"
    )
    tmp = SSHD_CONFIG + ".pqny"
    try:
        with open(tmp, "w") as f:
            f.write(innhald.rstrip() + "\n" + blokk)
        r = subprocess.run(["/usr/sbin/sshd", "-t", "-f", tmp],
                           capture_output=True)
        if r.returncode != 0:
            log.error("sshd_config-validering feila — hoppar over Match: %s",
                      r.stderr.decode("utf-8", "replace")[:200])
            os.remove(tmp)
            return
        os.replace(tmp, SSHD_CONFIG)
    except Exception as e:
        log.warning("Skriv sshd Match feila: %s", e)
        try:
            os.remove(tmp)
        except Exception:
            pass
        return
    # Reload (HUP) — droppar ikkje eksisterande økter (DewesoftX/deploy trygt)
    try:
        subprocess.run(
            ["bash", "-c",
             "kill -HUP $(cat /run/sshd.pid 2>/dev/null || pidof sshd) 2>/dev/null"],
            check=False)
        log.info("sshd Match-blokk for %s lagt til + reloada", BRUKAR)
    except Exception as e:
        log.warning("sshd reload feila: %s", e)


def oppsett() -> tuple:
    """Sørg for brukar + chroot + sshd Match. Krev root. (ok, melding)."""
    k = les_konfig()
    if not k["aktivert"]:
        return True, "deaktivert"
    if os.geteuid() != 0:
        return False, "krev root for SFTP-oppsett"
    if not k["passord"]:
        k["passord"] = generer_passord()
        lagre_konfig(k)
    # Chroot-struktur: rot MÅ vere root-eigd og ikkje skrivbar for andre.
    try:
        os.makedirs(CHROOT, exist_ok=True)
        os.chown(CHROOT, 0, 0)
        os.chmod(CHROOT, 0o755)
        os.makedirs(OPPLAST, exist_ok=True)
        os.makedirs(FERDIG, exist_ok=True)
    except Exception as e:
        return False, f"chroot-struktur feila: {e}"
    # Brukar
    try:
        finst = subprocess.run(["id", BRUKAR], capture_output=True).returncode == 0
        if not finst:
            subprocess.run(["useradd", "-M", "-N", "-s", "/usr/sbin/nologin",
                            BRUKAR], check=False, capture_output=True)
        subprocess.run(["chpasswd"], input=f"{BRUKAR}:{k['passord']}".encode(),
                       check=False, capture_output=True)
        import pwd
        pw = pwd.getpwnam(BRUKAR)
        os.chown(OPPLAST, pw.pw_uid, pw.pw_gid)
    except Exception as e:
        return False, f"brukar-oppsett feila: {e}"
    _sikre_sshd_match()
    return True, "oppsett ok"


# --- Ingest av mottekne filer ----------------------------------------
def _nas_maal() -> str:
    """Arkiv-katalog: NAS om montert/tilgjengeleg, elles lokal SSD."""
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


def _arkiver(sti: str, k: dict) -> None:
    """Kopier fila til NAS/SSD-arkiv, og CSV → kanalar. Original vert flytta
    til FERDIG etterpå av kallaren."""
    namn = _trygt(os.path.basename(sti))
    node = _node_namn()
    kunde = (k.get("kunde") or "").strip()
    delar = [_nas_maal()]
    if kunde:
        delar.append(_trygt(kunde))
    delar.append(_trygt(node))
    maalkat = os.path.join(*delar)
    try:
        os.makedirs(maalkat, exist_ok=True)
        shutil.copy2(sti, os.path.join(maalkat, namn))
    except Exception as e:
        log.warning("Innboks-arkivering av %s feila: %s", namn, e)
    # CSV → kanalar (best-effort, same pipeline som e-post/FTP)
    if namn.lower().endswith(".csv"):
        try:
            import smtp_server
            with open(sti, "rb") as f:
                data = f.read()
            pfx = (k.get("kanal_prefiks") or "").strip()
            smtp_server._mat_csv_til_kanalar([((pfx + namn) if pfx else namn, data)])
        except Exception as e:
            log.warning("Innboks CSV->kanalar (%s) feila: %s", namn, e)


def _prosesser(k: dict) -> None:
    """Plukk opp STABILE (ferdig-opplasta) filer i OPPLAST og handter dei."""
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
        # Stabil = same storleik som førre skann OG minst 8s gammal mtime.
        forrige = _storleik_cache.get(f)
        _storleik_cache[f] = (st.st_size, no)
        stabil = (forrige is not None and forrige[0] == st.st_size
                  and (no - st.st_mtime) >= 8)
        if not stabil:
            continue
        _arkiver(sti, k)
        # Flytt unna so vi ikkje prosesserer på nytt
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
        log.info("Innboks: tok imot + arkiverte %s", f)


def status() -> dict:
    ut = dict(_tilstand)
    ut["kjorer"] = _traad is not None and _traad.is_alive()
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
    """Kjør oppsett + start vaktetråden. Trygg å kalle fleire gonger."""
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
    _traad = threading.Thread(target=_loop, name="instrument-innboks",
                              daemon=True)
    _traad.start()


def stopp() -> None:
    _stopp.set()

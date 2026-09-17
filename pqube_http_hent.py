#!/usr/bin/env python3
"""
HTTP-henting av PQube 3 event-filer
===================================
PQube 3 leverer ikkje FTP-henting, og FTP-push er upåliteleg (hendings-styrt +
config-avhengig). Men PQubens web-server gir KATALOG-LISTER (autoindex), so vi
kan bla event-treet direkte over HTTP og dra ned både eksisterande og nye
hendingar:

  /{AAR}/Month_{MM}/Day_{DD}/{T_hh-mm-ss_Type}/{Graphs,PQDIF,Spreadsheets,Summaries}/fil

Noden når PQuben direkte på LAN-et (same som Modbus-pollinga). Nye event-filer
arkiverast til NAS/SSD (per kunde/node, med same date/event-struktur), og CSV
matast inn i kanal-pipelinen. Alt vi har henta hugsast so vi ikkje hentar på
nytt.

Konfig: /data/konfig/http_hent.json
"""

import os
import re
import json
import time
import shutil
import logging
import threading
import datetime
import urllib.request
import urllib.error

log = logging.getLogger("pqube_http_hent")

KONFIG_FIL = "/data/konfig/http_hent.json"
HENTA_FIL = "/data/konfig/http_hent_henta.json"

STANDARD = {
    "aktivert": False,
    "vert": "",              # PQube-IP, t.d. 192.168.1.204
    "port": 80,
    "brukar": "",            # valfri basic-auth
    "passord": "",
    "intervall_min": 10,
    "dagar_tilbake": 14,     # hent berre event nyare enn dette
    "kunde": "",
    "kanal_prefiks": "",
    "hent_gif": False,       # GIF-grafar er store; av som standard
    "hugs_maks": 20000,      # maks tal filnamn vi hugsar
}

# Filtypar vi alltid hentar (utanom GIF som er valfri)
_TYPAR = (".pqd", ".pqdif", ".csv", ".txt", ".xml", ".htm", ".html")

_stopp = threading.Event()
_traad = None
_tilstand = {"tilstand": "", "melding": "", "henta_totalt": 0,
             "sist_fil": "", "sist_ts": None, "nye_sist": 0}


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
    vert = str(data.get("vert", k["vert"])).strip()
    if vert and not _privat(vert):
        return False, "'%s' er ikkje ein privat IP-adresse" % vert
    for felt in ("aktivert", "vert", "brukar", "kunde", "kanal_prefiks", "hent_gif"):
        if felt in data:
            k[felt] = data[felt]
    if data.get("passord"):
        k["passord"] = str(data["passord"])
    try:
        k["port"] = int(data.get("port", k["port"]))
        k["intervall_min"] = float(data.get("intervall_min", k["intervall_min"]))
        k["dagar_tilbake"] = int(data.get("dagar_tilbake", k["dagar_tilbake"]))
    except (TypeError, ValueError):
        return False, "Ugyldig tal (port/intervall/dagar)"
    k["vert"] = vert
    try:
        os.makedirs(os.path.dirname(KONFIG_FIL), exist_ok=True)
        with open(KONFIG_FIL, "w", encoding="utf-8") as f:
            json.dump(k, f, indent=2, ensure_ascii=False)
    except Exception as e:
        return False, "Lagring feila: %s" % e
    return True, "Lagra"


def konfig_offentleg() -> dict:
    k = les_konfig()
    k["passord_sett"] = bool(k.pop("passord", ""))
    k["status"] = status()
    return k


def _privat(vert: str) -> bool:
    try:
        import instrument_proxy
        return instrument_proxy.tillat_vert(vert)
    except Exception:
        # Fallback: enkel privat-sjekk
        return bool(re.match(r"^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)", vert))


# --- Henta-minne -----------------------------------------------------
def _les_henta() -> set:
    try:
        with open(HENTA_FIL, "r", encoding="utf-8") as f:
            return set(json.load(f) or [])
    except Exception:
        return set()


def _skriv_henta(henta: set, maks: int) -> None:
    try:
        arr = list(henta)[-maks:]
        with open(HENTA_FIL, "w", encoding="utf-8") as f:
            json.dump(arr, f)
    except Exception:
        pass


# --- HTTP ------------------------------------------------------------
def _opna(k: dict, path: str, timeout: float = 40):
    url = "http://%s:%d%s" % (k["vert"], int(k["port"]), path)
    req = urllib.request.Request(url)
    if k.get("brukar"):
        import base64
        tok = base64.b64encode(
            ("%s:%s" % (k["brukar"], k.get("passord", ""))).encode()).decode()
        req.add_header("Authorization", "Basic " + tok)
    return urllib.request.urlopen(req, timeout=timeout)


def _listing(k: dict, path: str) -> list:
    """Returner (rel-)stiar under `path` frå autoindex-lista."""
    try:
        txt = _opna(k, path).read(200000).decode("utf-8", "replace")
    except Exception as e:
        log.debug("listing %s feila: %s", path, e)
        return []
    ut = []
    for h in re.findall(r'href=["\']([^"\']+)["\']', txt):
        # normaliser: dropp evt. proxy-prefiks, behald absolutte PQube-stiar
        if h.startswith("http"):
            continue
        if not h.startswith("/"):
            h = path.rstrip("/") + "/" + h
        if h.rstrip("/") == path.rstrip("/"):
            continue
        if not h.startswith(path.rstrip("/")):
            # berre gå nedover i treet
            if path not in ("/",) and not h.startswith(path):
                continue
        ut.append(h)
    return ut


def _er_fil(p: str) -> bool:
    import posixpath
    return "." in posixpath.basename(p.rstrip("/"))


_DATO_RE = re.compile(r"/Month_(\d{2})/Day_(\d{2})")


def _for_gammal(path: str, aar: int, cutoff: datetime.date) -> bool:
    m = _DATO_RE.search(path)
    if not m:
        return False
    try:
        d = datetime.date(aar, int(m.group(1)), int(m.group(2)))
        return d < cutoff
    except ValueError:
        return False


# --- Arkivering + kanalar --------------------------------------------
def _nas_maal() -> str:
    if os.path.ismount("/data/nas") or os.path.isdir("/data/nas"):
        return "/data/nas/pqube"
    return "/data/maalingar/pqube"


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


def _arkiver(k: dict, rel: str, data: bytes) -> str:
    delar = [_nas_maal()]
    if (k.get("kunde") or "").strip():
        delar.append(re.sub(r"[^A-Za-z0-9._ -]", "_", k["kunde"].strip()))
    delar.append(re.sub(r"[^A-Za-z0-9._ -]", "_", _node_namn()))
    maal = os.path.join(os.path.join(*delar), rel.lstrip("/").replace("/", os.sep))
    os.makedirs(os.path.dirname(maal), exist_ok=True)
    with open(maal, "wb") as f:
        f.write(data)
    if rel.lower().endswith(".csv"):
        try:
            import smtp_server
            import posixpath
            pfx = (k.get("kanal_prefiks") or "").strip()
            namn = posixpath.basename(rel)
            smtp_server._mat_csv_til_kanalar([((pfx + namn) if pfx else namn, data)])
        except Exception as e:
            log.debug("CSV->kanalar (%s) feila: %s", rel, e)
    return maal


# --- Hovudløkke ------------------------------------------------------
def _vil_ha(k: dict, path: str) -> bool:
    low = path.lower()
    if low.endswith(_TYPAR):
        return True
    if k.get("hent_gif") and low.endswith(".gif"):
        return True
    return False


def _skann(k: dict) -> int:
    """Gå gjennom PQube-treet, hent nye event-filer. Returner tal nye."""
    henta = _les_henta()
    nye = 0
    cutoff = datetime.date.today() - datetime.timedelta(days=int(k["dagar_tilbake"]))
    # Rot: finn år-katalogar (4 siffer)
    aar_dirs = []
    for h in _listing(k, "/"):
        m = re.search(r"/(\d{4})/?$", h.rstrip("/"))
        if m:
            aar_dirs.append((int(m.group(1)), h.rstrip("/") + "/"))
    for aar, aar_path in sorted(aar_dirs):
        if aar < cutoff.year:
            continue
        stakk = [aar_path]
        while stakk:
            if _stopp.is_set():
                break
            cur = stakk.pop()
            for h in _listing(k, cur):
                if _er_fil(h):
                    if not _vil_ha(k, h):
                        continue
                    if _for_gammal(h, aar, cutoff):
                        continue
                    if h in henta:
                        continue
                    try:
                        data = _opna(k, h).read()
                        rel = h[len(aar_path) - 5:] if h.startswith(aar_path) else h
                        _arkiver(k, h, data)
                        henta.add(h)
                        nye += 1
                        _tilstand.update(tilstand="ok", melding="Henta event-fil",
                                         henta_totalt=_tilstand["henta_totalt"] + 1,
                                         sist_fil=os.path.basename(h),
                                         sist_ts=time.time())
                        log.info("HTTP-hent: %s (%d B)", h, len(data))
                    except Exception as e:
                        log.warning("HTTP-hent %s feila: %s", h, e)
                else:
                    # katalog: hopp over for gamle dagar tidleg
                    if _for_gammal(h, aar, cutoff):
                        continue
                    stakk.append(h.rstrip("/") + "/")
    _skriv_henta(henta, int(k.get("hugs_maks", 20000)))
    return nye


def status() -> dict:
    ut = dict(_tilstand)
    ut["kjorer"] = _traad is not None and _traad.is_alive()
    ut["henta_kjende"] = len(_les_henta())
    return ut


def _loop() -> None:
    _stopp.wait(30)
    while not _stopp.is_set():
        k = les_konfig()
        if k["aktivert"] and k["vert"]:
            try:
                _tilstand.update(tilstand="koeyrer", melding="Skannar PQube-tre")
                nye = _skann(k)
                _tilstand["nye_sist"] = nye
                _tilstand.update(tilstand="ok",
                                 melding=("Henta %d nye" % nye) if nye else "Ingen nye")
            except Exception as e:
                _tilstand.update(tilstand="feil", melding=str(e)[:120])
                log.warning("HTTP-hent skann feila: %s", e)
        vent = max(60.0, float(k.get("intervall_min", 10)) * 60.0)
        _stopp.wait(vent)


def hent_no() -> tuple:
    """Køyr eit skann med ein gong (for GUI-knapp)."""
    k = les_konfig()
    if not k["aktivert"] or not k["vert"]:
        return False, "ikkje aktivert / manglar vert"
    try:
        nye = _skann(k)
        return True, "Henta %d nye filer" % nye
    except Exception as e:
        return False, str(e)


def start() -> None:
    global _traad
    if _traad is not None and _traad.is_alive():
        return
    _stopp.clear()
    _traad = threading.Thread(target=_loop, name="pqube-http-hent", daemon=True)
    _traad.start()


def stopp() -> None:
    _stopp.set()

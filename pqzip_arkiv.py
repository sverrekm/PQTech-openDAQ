# -*- coding: utf-8 -*-
"""Arkiver PQZIP-filer frå eit instrument sin FTP.

Elspec BlackBox skriv kontinuerleg bølgjeform som PQZIP (.PQZip) under
/CF_UPMB/PQZIPDATA_. Formatet er Elspec sitt lukka, komprimerte format —
vi kan IKKJE gjere det om til live kanalar (sjå README/kommentar nedst), men
vi kan arkivere filene, so dei kan opnast i PQSCADA (t.d. via FTP-relayet
frå kontoret) og ikkje går tapt om BlackBox-disken fyllest.

Gjenbrukar instrument_ftp sin robuste FTP-tilkopling (PASV-fiks + retry).
Eige mål, intervall, og — viktig — RETENSJON + storleikstak, sidan
kontinuerleg PQZIP elles fyller disken (node1/NAS har avgrensa plass).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time

log = logging.getLogger("pqzip_arkiv")

KONFIG_FIL = "/data/konfig/pqzip_arkiv.json"
HENTA_FIL = "/data/konfig/pqzip_henta.json"

STANDARD = {
    "aktivert": False,
    "rot": "/CF_UPMB/PQZIPDATA_",
    "monster": "*.PQZip",
    "maalkatalog": "/data/nas/pqzip",   # NAS om montert; elles SSD
    "intervall_min": 15,
    "retensjon_dagar": 14,
    "maks_mb": 2000,                     # hardt tak — vern mot full disk
    "hugs_filer": 20000,
}

_synk_las = threading.Lock()
_stopp = threading.Event()
_traad = None
_tilstand = {
    "tilstand": "", "melding": "", "sist_forsok": None, "sist_ok": None,
    "nye_sist": 0, "totalt_henta": 0, "lokalt_tal": 0, "lokalt_mb": 0.0,
    "neste_om_s": None,
}


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
        intervall = float(data.get("intervall_min", k["intervall_min"]))
        reten = int(data.get("retensjon_dagar", k["retensjon_dagar"]))
        maks = int(data.get("maks_mb", k["maks_mb"]))
    except (TypeError, ValueError):
        return False, "Invalid number"
    if not 1 <= intervall <= 1440:
        return False, "Interval must be 1–1440 minutes"
    if reten < 0 or maks < 50:
        return False, "Bad retention/size cap"
    k.update({
        "aktivert": bool(data.get("aktivert", k["aktivert"])),
        "rot": str(data.get("rot", k["rot"])) or STANDARD["rot"],
        "monster": str(data.get("monster", k["monster"])) or "*.PQZip",
        "maalkatalog": str(data.get("maalkatalog", k["maalkatalog"])) or STANDARD["maalkatalog"],
        "intervall_min": intervall, "retensjon_dagar": reten, "maks_mb": maks,
    })
    try:
        os.makedirs(os.path.dirname(KONFIG_FIL), exist_ok=True)
        with open(KONFIG_FIL, "w", encoding="utf-8") as f:
            json.dump(k, f, indent=2)
    except Exception as e:
        return False, "Could not save: %s" % e
    return True, "Saved"


def konfig_offentleg() -> dict:
    k = les_konfig()
    k["status"] = status()
    return k


def status() -> dict:
    return dict(_tilstand)


def _les_henta() -> dict:
    try:
        with open(HENTA_FIL, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _skriv_henta(d: dict) -> None:
    maks = int(les_konfig().get("hugs_filer", 20000))
    if len(d) > maks:
        for nk in sorted(d, key=lambda x: d[x][1])[:len(d) - maks]:
            d.pop(nk, None)
    try:
        os.makedirs(os.path.dirname(HENTA_FIL), exist_ok=True)
        with open(HENTA_FIL, "w", encoding="utf-8") as f:
            json.dump(d, f)
    except Exception:
        pass


def _lokal_oversikt(maalkat: str):
    """(tal, sum_bytes, [(sti, mtime, storleik) sortert eldst først])."""
    filer = []
    for rot, _dirs, namn in os.walk(maalkat):
        for n in namn:
            if n.endswith(".del"):
                continue
            p = os.path.join(rot, n)
            try:
                st = os.stat(p)
                filer.append((p, st.st_mtime, st.st_size))
            except OSError:
                pass
    filer.sort(key=lambda x: x[1])
    total = sum(f[2] for f in filer)
    return len(filer), total, filer


def _rydd(maalkat: str, retensjon_dagar: int, maks_mb: int) -> int:
    """Slett eldste lokale filer utover retensjon/tak. Returnerer talet sletta."""
    sletta = 0
    _tal, total, filer = _lokal_oversikt(maalkat)
    grense = maks_mb * 1024 * 1024
    no = time.time()
    maks_alder = retensjon_dagar * 86400 if retensjon_dagar > 0 else 0
    for p, mtid, sz in filer:
        for_gammal = maks_alder and (no - mtid) > maks_alder
        for_stort = total > grense
        if not (for_gammal or for_stort):
            break            # sortert eldst først; resten er nyare og under tak
        try:
            os.unlink(p)
            total -= sz
            sletta += 1
        except OSError:
            pass
    return sletta


def synk_ein_gong() -> dict:
    k = les_konfig()
    try:
        import instrument_ftp
    except Exception as e:
        return {"suksess": False, "melding": "instrument_ftp manglar: %s" % e}
    if not instrument_ftp.les_konfig().get("vert"):
        return {"suksess": False, "melding": "No instrument FTP host configured"}
    if not _synk_las.acquire(blocking=False):
        return {"suksess": False, "melding": "A sync is already running"}
    try:
        return _synk_innmat(k, instrument_ftp)
    finally:
        _synk_las.release()


def _synk_innmat(k: dict, instrument_ftp) -> dict:
    import ftplib
    henta = _les_henta()
    maalkat = k["maalkatalog"]
    os.makedirs(maalkat, exist_ok=True)
    nye, feila, filer = [], [], []
    with instrument_ftp._las:            # del FTP-låsen med rapport-synken
        f = instrument_ftp._opne()
        try:
            filer = instrument_ftp._finn_filer(f, k["rot"], k["monster"],
                                               0, 4)
            for n_gjort, fil in enumerate(filer):
                nokkel = "%s|%d" % (fil["sti"], fil["storleik"])
                if nokkel in henta:
                    continue
                maal = os.path.join(maalkat, fil["sti"].lstrip("/"))
                os.makedirs(os.path.dirname(maal) or ".", exist_ok=True)
                mellom = maal + ".del"
                ok = False
                siste_feil = "unknown"
                for forsok in range(3):
                    try:
                        with open(mellom, "wb") as ut:
                            f.retrbinary("RETR " + fil["sti"], ut.write, 32768)
                        os.replace(mellom, maal)
                        ok = True
                        break
                    except ftplib.all_errors as e:
                        siste_feil = str(e)
                        try:
                            os.unlink(mellom)
                        except Exception:
                            pass
                        if forsok < 2:
                            time.sleep(1.5 + forsok)
                            instrument_ftp._lukk(f)
                            try:
                                f = instrument_ftp._opne()
                            except Exception:
                                break
                if not ok:
                    feila.append({"sti": fil["sti"], "feil": siste_feil})
                    continue
                henta[nokkel] = [fil["storleik"], time.time()]
                nye.append(fil["sti"])
                if n_gjort % 4 == 3:
                    time.sleep(0.4)
        finally:
            instrument_ftp._lukk(f)
    _skriv_henta(henta)
    sletta = _rydd(maalkat, int(k["retensjon_dagar"]), int(k["maks_mb"]))
    tal, total, _ = _lokal_oversikt(maalkat)
    _tilstand["totalt_henta"] += len(nye)
    _tilstand["lokalt_tal"] = tal
    _tilstand["lokalt_mb"] = round(total / 1024 / 1024, 1)
    melding = "%d new, %d pruned — %d files, %.0f MB local" % (
        len(nye), sletta, tal, total / 1024 / 1024)
    if feila:
        melding += ", %d failed" % len(feila)
    return {"suksess": True, "nye": nye, "feila": feila, "sett": len(filer),
            "melding": melding}


def _loop() -> None:
    _stopp.wait(50)
    while not _stopp.is_set():
        k = les_konfig()
        if not k["aktivert"]:
            _stopp.wait(60)
            continue
        _tilstand.update(tilstand="koeyrer", melding="Archiving PQZIP",
                         sist_forsok=time.time())
        try:
            res = synk_ein_gong()
            _tilstand.update(tilstand="ok" if res["suksess"] else "feil",
                             melding=res.get("melding", ""),
                             nye_sist=len(res.get("nye", [])),
                             sist_ok=time.time() if res["suksess"] else _tilstand["sist_ok"])
        except Exception as e:
            _tilstand.update(tilstand="feil", melding=str(e))
        vent = max(60.0, float(k["intervall_min"]) * 60.0)
        _tilstand["neste_om_s"] = vent
        _stopp.wait(vent)


def start_synk() -> None:
    global _traad
    if _traad is not None and _traad.is_alive():
        return
    _stopp.clear()
    _traad = threading.Thread(target=_loop, name="pqzip-arkiv", daemon=True)
    _traad.start()


def stopp_synk() -> None:
    _stopp.set()

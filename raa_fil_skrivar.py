#!/usr/bin/env python3
"""
Rå-fil-skrivar — arkiver måledata som CSV-filer (NAS/CIFS-vennleg)
=================================================================
Hub-side komponent som skriv mottatt måledata til rå CSV-filer i ein
konfigurerbar katalog — typisk ein NAS montert via CIFS/SMB. Dette er
langtidsarkivet («mye data»), ved sida av InfluxDB (som gir raske
dashboard-/rapport-spørjingar).

Kvifor CSV på NAS og ikkje SQLite: SQLite over nettverks-FS (CIFS/NFS) kan
korrumperast pga fil-låsing. Rein CSV-append toler nettverks-FS fint.

Filoppsett: {katalog}/{node}/{YYYY-MM-DD}.csv  (dagleg rotasjon per node)
  header: tid_iso,ts_ms,node,channel,unit,value

Skrivinga skjer i ein bakgrunnstråd via ein kø, så NAS-latens aldri
blokkerer ingest-endepunktet. Konfig i /data/konfig/raa_fil.json.
"""

import os
import re
import csv
import json
import time
import queue
import logging
import threading
from pathlib import Path
from datetime import datetime

log = logging.getLogger("raa_fil_skrivar")

# Split på mellomrom som IKKJE er escapa (ikkje har \ føre seg)
_USESC_SPACE = re.compile(r"(?<!\\) ")

KONFIG_STI = Path("/data/konfig/raa_fil.json")

_STANDARD = {
    "aktivert": False,
    # Standard /data/nas (mount NAS hit via docker-compose NAS_DIR). Kan vere
    # kva sti som helst inne i containeren (t.d. /data/maalinger/arkiv).
    "katalog": "/data/nas/maalingar",
    # Maks storleik per CSV-fil (MB). Når ei fil når dette, rullar arkivet til
    # ein ny del-fil (..._2.csv, _3.csv). 0 = inga storleiks-rotasjon (berre
    # dagleg). Standard 1024 MB (1 GB) — innanfor 500 MB–2 GB.
    "maks_fil_mb": 1024,
}

_konfig_cache = None
_konfig_mtime = -1.0


def les_konfig() -> dict:
    global _konfig_cache, _konfig_mtime
    try:
        mtime = KONFIG_STI.stat().st_mtime if KONFIG_STI.exists() else 0.0
    except OSError:
        mtime = 0.0
    if _konfig_cache is not None and mtime == _konfig_mtime:
        return dict(_konfig_cache)
    d = dict(_STANDARD)
    try:
        if KONFIG_STI.exists():
            d.update(json.loads(KONFIG_STI.read_text(encoding="utf-8")))
    except Exception as e:
        log.warning(f"Kunne ikkje lese raa_fil-konfig: {e}")
    if not d.get("katalog"):
        d["katalog"] = _STANDARD["katalog"]
    try:
        d["maks_fil_mb"] = max(0, int(d.get("maks_fil_mb", 1024)))
    except (TypeError, ValueError):
        d["maks_fil_mb"] = 1024
    _konfig_cache = dict(d)
    _konfig_mtime = mtime
    return d


def lagre_konfig(data: dict) -> dict:
    d = les_konfig()
    if "aktivert" in data:
        d["aktivert"] = bool(data["aktivert"])
    if data.get("katalog"):
        d["katalog"] = str(data["katalog"]).strip()
    if "maks_fil_mb" in data:
        d["maks_fil_mb"] = data["maks_fil_mb"]
    KONFIG_STI.parent.mkdir(parents=True, exist_ok=True)
    KONFIG_STI.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
    global _konfig_cache, _konfig_mtime
    _konfig_cache = None
    _konfig_mtime = -1.0
    return les_konfig()


# ---------------------------------------------------------------
#  Kø + skrivartråd
# ---------------------------------------------------------------
_kø: "queue.Queue" = queue.Queue(maxsize=200000)
_stats = {"skrive": 0, "droppa": 0, "siste_feil": "", "siste_skriv_ts": 0.0}
_traad_starta = False
_trygt_namn_tabell = str.maketrans({c: "_" for c in '<>:"/\\|?*'})


def _trygt(namn: str) -> str:
    """Filsystem-trygt namn for node-/filnamn."""
    s = str(namn or "ukjend").translate(_trygt_namn_tabell).strip()
    return s or "ukjend"


def skriv_punkt(punkt: list) -> None:
    """Legg måledata-punkt i kø for CSV-skriving (ikkje-blokkerande).

    punkt: liste av dict {node, channel, unit, value, ts_ms}.
    No-op når deaktivert.
    """
    if not punkt or not les_konfig().get("aktivert"):
        return
    for p in punkt:
        try:
            _kø.put_nowait(p)
        except queue.Full:
            _stats["droppa"] += 1
            if _stats["droppa"] % 1000 == 1:
                log.warning(f"Rå-fil kø full — droppar (totalt {_stats['droppa']})")


def parse_line_protocol(linjer: list) -> list:
    """Parse pqtech_channel line-protocol → punkt-dict-ar.

    Linje: 'pqtech_channel,node=X,channel=Y,unit=Z value=1.23 1719000000'
    Berre pqtech_channel-måling vert arkivert (harmoniske/THD/V/I/effekt går
    alle som pqtech_channel frå modbus_lager)."""
    punkt = []
    for ln in linjer:
        ln = str(ln).strip()
        if not ln.startswith("pqtech_channel,"):
            continue
        try:
            # Del på U-ESCAPA mellomrom (line-protocol escapar mellomrom i
            # tag-verdiar som "\ "). Gir [<meas,tags>, <fieldset>, <ts?>].
            delar = _USESC_SPACE.split(ln)
            if len(delar) < 2:
                continue
            hovud = delar[0]
            felt_del = delar[1]
            ts_del = delar[2] if len(delar) > 2 else ""
            tags = {}
            for kv in hovud.split(",")[1:]:
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    # av-escape line-protocol-tag
                    tags[k] = v.replace("\\ ", " ").replace("\\,", ",").replace("\\=", "=")
            verdi = None
            if felt_del.startswith("value="):
                verdi = float(felt_del[6:])
            ts_ms = int(ts_del) * 1000 if ts_del.strip().isdigit() else int(time.time() * 1000)
            if verdi is None:
                continue
            punkt.append({
                "node": tags.get("node", "ukjend"),
                "channel": tags.get("channel", "ukjend"),
                "unit": tags.get("unit", ""),
                "value": verdi,
                "ts_ms": ts_ms,
            })
        except Exception:
            continue
    return punkt


# Gjeldande del-fil-nummer per (node, dato) — unngår å skanne ved kvart skriv.
_part_cache: dict = {}


def _filnamn(node: str, dato: str, part: int) -> str:
    """Filnamn med node + dato. Del 1 utan suffiks, del >1 med _N."""
    return f"{node}_{dato}.csv" if part <= 1 else f"{node}_{dato}_{part}.csv"


def _finn_part(mappe: Path, node: str, dato: str, maks_bytes: int) -> Path:
    """Finn fila å appende til for (node, dato), med storleiks-rotasjon.
    Rullar til neste del-fil når gjeldande har nådd maks_bytes."""
    key = (node, dato)
    part = _part_cache.get(key)
    if part is None:
        # Oppdag høgaste eksisterande del-fil (etter restart)
        part = 1
        p = 1
        while (mappe / _filnamn(node, dato, p)).exists():
            part = p
            p += 1
    if maks_bytes > 0:
        try:
            if (mappe / _filnamn(node, dato, part)).stat().st_size >= maks_bytes:
                part += 1
        except OSError:
            pass
    _part_cache[key] = part
    return mappe / _filnamn(node, dato, part)


def _skriv_loop():
    while True:
        try:
            d = les_konfig()
            if not d.get("aktivert"):
                # Tøm køen og kvil
                try:
                    while True:
                        _kø.get_nowait()
                except queue.Empty:
                    pass
                time.sleep(2)
                continue

            # Saml ein batch
            batch = []
            try:
                batch.append(_kø.get(timeout=2))
            except queue.Empty:
                continue
            while len(batch) < 5000:
                try:
                    batch.append(_kø.get_nowait())
                except queue.Empty:
                    break

            # Grupper per (node, dato). Filnamnet inneheld node + dato, og
            # arkivet rullar til ny del-fil når makstorleiken er nådd.
            rot = Path(d["katalog"])
            maks_bytes = int(d.get("maks_fil_mb", 1024)) * 1024 * 1024
            per_grp = {}   # (node_trygt, node_orig, dato) -> [rader]
            for p in batch:
                try:
                    dt = datetime.fromtimestamp(p["ts_ms"] / 1000.0)
                    iso = dt.isoformat()
                    dato = dt.strftime("%Y-%m-%d")
                except (OSError, OverflowError, ValueError):
                    iso = ""
                    dato = "ukjend"
                node_orig = p.get("node", "")
                nt = _trygt(node_orig)
                per_grp.setdefault((nt, dato), []).append(
                    (iso, p["ts_ms"], node_orig, p.get("channel", ""),
                     p.get("unit", ""), p.get("value")))

            for (nt, dato), rader in per_grp.items():
                try:
                    mappe = rot / nt
                    mappe.mkdir(parents=True, exist_ok=True)
                    fil = _finn_part(mappe, nt, dato, maks_bytes)
                    ny = not fil.exists()
                    with open(fil, "a", newline="", encoding="utf-8") as f:
                        w = csv.writer(f)
                        if ny:
                            w.writerow(["tid_iso", "ts_ms", "node", "channel", "unit", "value"])
                        w.writerows(rader)
                    _stats["skrive"] += len(rader)
                    _stats["siste_skriv_ts"] = time.time()
                    _stats["siste_feil"] = ""
                    # Rull til ny del-fil ved neste skriv viss denne er full
                    if maks_bytes > 0:
                        try:
                            if fil.stat().st_size >= maks_bytes:
                                _part_cache[(nt, dato)] = _part_cache.get((nt, dato), 1) + 1
                        except OSError:
                            pass
                except Exception as e:
                    _stats["siste_feil"] = str(e)
                    log.warning(f"Rå-fil skriv feila ({nt}/{dato}): {e}")
                    # NAS nede? Vent litt; rader er alt tatt frå køen (tapt) —
                    time.sleep(5)
        except Exception as e:
            _stats["siste_feil"] = f"loop: {e}"
            log.warning(f"Rå-fil loop-feil: {e}")
            time.sleep(5)


def status() -> dict:
    d = les_konfig()
    ut = {
        "aktivert": bool(d.get("aktivert")),
        "katalog": d.get("katalog", ""),
        "skrive_totalt": _stats["skrive"],
        "droppa": _stats["droppa"],
        "kø_lengd": _kø.qsize(),
        "siste_skriv_ts": _stats["siste_skriv_ts"],
        "siste_feil": _stats["siste_feil"],
        "katalog_finst": False,
        "skrivbar": False,
    }
    try:
        p = Path(d.get("katalog", ""))
        ut["katalog_finst"] = p.exists()
        ut["skrivbar"] = os.access(str(p), os.W_OK) if p.exists() else False
    except Exception:
        pass
    return ut


def konfig_offentleg() -> dict:
    d = les_konfig()
    d.update(status())
    return d


def start() -> None:
    """Start CSV-skrivartråd (idempotent). Passiv til aktivert."""
    global _traad_starta
    if _traad_starta:
        return
    _traad_starta = True
    threading.Thread(target=_skriv_loop, daemon=True, name="raa_fil_skrivar").start()
    log.info("Rå-fil-skrivar starta")

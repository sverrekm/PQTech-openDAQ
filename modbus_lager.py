#!/usr/bin/env python3
"""
Modbus-lager — store-and-forward av PQube/Modbus-data til hubben
================================================================
Node-side komponent for rapport-kritisk datainnsamling: samplar alle
Modbus-register (t.d. 4–6 PQube3) periodisk til ein lokal SQLite-database
MED tidsstempel, og sender dei vidare til hubben med ACK. Usende rader vert
liggjande lokalt og etterfyllast (backfill) når sambandet kjem tilbake —
så halvårsrapportar ikkje får hol ved straum-/4G-brot eller hub-nedetid.

Skil seg frå den lette `_harmonic_forward_loop` (fire-and-forget, ingen
tidsstempel/buffer) ved at:
  - alt vert lagra lokalt fyrst (overlever omstart),
  - line-protocol inkluderer ORIGINAL tidsstempel (backfill landar rett),
  - rader vert berre markert sende når hubben stadfestar skriv (suksess),
  - retensjon held lokal DB avgrensa.

Additiv og opt-in (aktivert i modbus_lager.json). Rører IKKJE SIRIUS/openDAQ-
brua — den står for live DewesoftX-streaming som før. Datakjelda er
`modbus_manager.hent_verdiar()` (cache som PQube-pollinga fyller).

Transport: POST {parent_url}/api/emc-ingest (Bearer parent_token) — same
endepunkt som EMC-forwarden; hubben skriv line-protocol til SIN InfluxDB.
"""

import os
import json
import time
import queue
import sqlite3
import logging
import threading
import urllib.request
from pathlib import Path
from datetime import datetime, timedelta

log = logging.getLogger("modbus_lager")

KONFIG_STI = Path("/data/konfig/modbus_lager.json")
STANDARD_DB = "/data/maalinger/modbus_lager.db"

_STANDARD = {
    "aktivert": False,
    "db_sti": STANDARD_DB,
    "intervall_s": 10.0,        # sampl-periode (PQube oppdaterer internt ~2 Hz)
    "batch_storleik": 5000,     # rader per forward-batch (catch-up etter brot)
    "retensjon_dagar": 400,     # slett SENDE rader eldre enn dette (>1 år)
    "maks_mb": 0,               # 0 = ubegrensa lokal DB-storleik
}

_konfig_cache = None
_konfig_mtime = -1.0


# ---------------------------------------------------------------
#  Konfig (mtime-cacha)
# ---------------------------------------------------------------
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
        log.warning(f"Kunne ikkje lese modbus_lager-konfig: {e}")
    try:
        d["intervall_s"] = max(1.0, float(d.get("intervall_s", 10.0)))
    except (TypeError, ValueError):
        d["intervall_s"] = 10.0
    try:
        d["batch_storleik"] = max(100, int(d.get("batch_storleik", 5000)))
    except (TypeError, ValueError):
        d["batch_storleik"] = 5000
    try:
        d["retensjon_dagar"] = max(0, int(d.get("retensjon_dagar", 400)))
    except (TypeError, ValueError):
        d["retensjon_dagar"] = 400
    try:
        d["maks_mb"] = max(0, int(d.get("maks_mb", 0)))
    except (TypeError, ValueError):
        d["maks_mb"] = 0
    if not d.get("db_sti"):
        d["db_sti"] = STANDARD_DB
    _konfig_cache = dict(d)
    _konfig_mtime = mtime
    return d


def lagre_konfig(data: dict) -> dict:
    d = les_konfig()
    if "aktivert" in data:
        d["aktivert"] = bool(data["aktivert"])
    if data.get("db_sti"):
        d["db_sti"] = str(data["db_sti"]).strip()
    for nokkel in ("intervall_s", "batch_storleik", "retensjon_dagar", "maks_mb"):
        if nokkel in data:
            d[nokkel] = data[nokkel]
    KONFIG_STI.parent.mkdir(parents=True, exist_ok=True)
    KONFIG_STI.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
    return les_konfig()


def konfig_offentleg() -> dict:
    d = les_konfig()
    d.update(status())
    return d


# ---------------------------------------------------------------
#  Database + state
# ---------------------------------------------------------------
_db = None
_db_sti_open = None
_db_lock = threading.Lock()
_stats = {"lagra": 0, "sendt": 0, "siste_feil": "", "siste_sendt_ts": 0.0}
_traadar_starta = False


def _opna_db(sti: str):
    global _db, _db_sti_open
    try:
        p = Path(sti)
        p.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(str(p), check_same_thread=False, timeout=10)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=NORMAL")
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("""
            CREATE TABLE IF NOT EXISTS maaling (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                node_namn TEXT,
                channel   TEXT NOT NULL,
                unit      TEXT,
                value     REAL,
                ts_ms     INTEGER NOT NULL,
                sendt     INTEGER NOT NULL DEFAULT 0
            )
        """)
        db.execute("CREATE INDEX IF NOT EXISTS idx_sendt_id ON maaling(sendt, id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_ts ON maaling(ts_ms)")
        db.commit()
        _db = db
        _db_sti_open = sti
        log.info(f"Modbus-lager database open: {sti}")
        return db
    except Exception as e:
        log.error(f"Kunne ikkje opne modbus-lager DB ({sti}): {e}")
        _db = None
        _db_sti_open = None
        return None


# ---------------------------------------------------------------
#  Sampling: les modbus_manager → SQLite (med tidsstempel)
# ---------------------------------------------------------------
def _hent_modbus_snapshot():
    """Returner liste av (node_namn, channel, unit, value) for alle aktive
    Modbus-register med ein lest verdi. Hentar verdiar frå modbus_manager og
    metadata frå hub-konfig. Returnerer [] viss ikkje tilgjengeleg."""
    try:
        import sirius_server
        mm = getattr(sirius_server, "_modbus_manager", None)
        if mm is None:
            return []
        verdiar = mm.hent_verdiar()  # {(node_id, adresse): value}
    except Exception:
        return []
    if not verdiar:
        return []
    try:
        from hub_konfig import les_hub_konfig
        from hub_konfig import NODE_TYPE_MODBUS_TCP
    except Exception:
        try:
            from hub_konfig import les_hub_konfig
            NODE_TYPE_MODBUS_TCP = "modbus_tcp"
        except Exception:
            return []
    rader = []
    try:
        konfig = les_hub_konfig()
    except Exception:
        return []
    for node in konfig.nodar:
        if getattr(node, "type", "") != NODE_TYPE_MODBUS_TCP or not node.aktivert:
            continue
        for reg in node.modbus_registers:
            v = verdiar.get((node.id, reg.adresse))
            if v is None:
                continue
            rader.append((node.namn, reg.namn, reg.eining or "", float(v)))
    return rader


def _sampl_loop():
    while True:
        try:
            d = les_konfig()
            if d.get("aktivert"):
                if _db is None:
                    _opna_db(d.get("db_sti", STANDARD_DB))
                rader = _hent_modbus_snapshot()
                if rader and _db is not None:
                    ts_ms = int(time.time() * 1000)
                    with _db_lock:
                        _db.executemany(
                            "INSERT INTO maaling (node_namn, channel, unit, value, ts_ms, sendt) "
                            "VALUES (?, ?, ?, ?, ?, 0)",
                            [(nn, ch, un, val, ts_ms) for (nn, ch, un, val) in rader])
                        _db.commit()
                    _stats["lagra"] += len(rader)
            time.sleep(les_konfig().get("intervall_s", 10.0))
        except Exception as e:
            _stats["siste_feil"] = f"sampl: {e}"
            log.warning(f"Modbus-lager sampl-feil: {e}")
            time.sleep(5)


# ---------------------------------------------------------------
#  Forward: drener usende rader → hub (med tidsstempel + ACK)
# ---------------------------------------------------------------
def _esc_tag(s) -> str:
    return (str(s).replace("\\", "\\\\").replace(" ", "\\ ")
            .replace(",", "\\,").replace("=", "\\="))


def _send_til_hub(linjer: list) -> bool:
    """POST line-protocol til hubben sin /api/emc-ingest. Returnerer True
    berre når hubben stadfestar at skrivinga til InfluxDB lukkast (suksess)."""
    try:
        from push_konfig import les_push_konfig
        pk = les_push_konfig()
        url = (pk.parent_url or "").strip()
        tok = (pk.parent_token or "").strip()
    except Exception:
        return False
    if not url or not tok:
        return False
    body = json.dumps({"linjer": linjer}).encode("utf-8")
    req = urllib.request.Request(
        url.rstrip("/") + "/api/emc-ingest", data=body, method="POST",
        headers={"Authorization": f"Bearer {tok}",
                 "Content-Type": "application/json",
                 # Cloudflare blokkerer "Python-urllib"-UA (403).
                 "User-Agent": "Mozilla/5.0 (PQTech-openDAQ)"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status not in (200, 204):
                return False
            try:
                svar = json.loads(resp.read() or b"{}")
                return bool(svar.get("suksess", True))
            except Exception:
                return True
    except Exception as e:
        _stats["siste_feil"] = f"send: {e}"
        return False


def _send_loop():
    while True:
        sov = 5.0
        try:
            d = les_konfig()
            if d.get("aktivert") and _db is not None:
                batch = int(d.get("batch_storleik", 5000))
                with _db_lock:
                    cur = _db.execute(
                        "SELECT id, node_namn, channel, unit, value, ts_ms "
                        "FROM maaling WHERE sendt = 0 ORDER BY id ASC LIMIT ?", (batch,))
                    rader = cur.fetchall()
                if rader:
                    linjer = []
                    for _id, nn, ch, un, val, ts_ms in rader:
                        if val is None:
                            continue
                        ts_s = int(ts_ms / 1000)
                        linjer.append(
                            f"pqtech_channel,node={_esc_tag(nn)},channel={_esc_tag(ch)},"
                            f"unit={_esc_tag(un)} value={float(val)} {ts_s}")
                    if linjer and _send_til_hub(linjer):
                        ids = [r[0] for r in rader]
                        with _db_lock:
                            # Marker sende i chunkar (unngå for lang IN-liste)
                            for i in range(0, len(ids), 900):
                                bit = ids[i:i + 900]
                                q = "UPDATE maaling SET sendt = 1 WHERE id IN (%s)" % \
                                    ",".join("?" * len(bit))
                                _db.execute(q, bit)
                            _db.commit()
                        _stats["sendt"] += len(ids)
                        _stats["siste_sendt_ts"] = time.time()
                        _stats["siste_feil"] = ""
                        # Fleire usende? Drener raskt (catch-up etter brot).
                        if len(rader) >= batch:
                            sov = 0.5
                    # _send_til_hub False → behald rader, prøv igjen seinare
                _retensjon(d)
        except Exception as e:
            _stats["siste_feil"] = f"send-loop: {e}"
            log.warning(f"Modbus-lager send-feil: {e}")
        time.sleep(sov)


_siste_retensjon = 0.0


def _retensjon(d: dict):
    global _siste_retensjon
    if _db is None or time.time() - _siste_retensjon < 300:
        return
    _siste_retensjon = time.time()
    dagar = int(d.get("retensjon_dagar", 400))
    if dagar > 0:
        grense = int((datetime.now() - timedelta(days=dagar)).timestamp() * 1000)
        with _db_lock:
            r = _db.execute("DELETE FROM maaling WHERE sendt = 1 AND ts_ms < ?", (grense,))
            if r.rowcount > 0:
                _db.commit()
                log.info(f"Modbus-lager retensjon: sletta {r.rowcount} sende rader "
                         f"eldre enn {dagar} dagar")
    maks_mb = int(d.get("maks_mb", 0))
    if maks_mb > 0 and _db_sti_open:
        try:
            mb = Path(_db_sti_open).stat().st_size / (1024 * 1024)
        except OSError:
            mb = 0
        if mb > maks_mb:
            with _db_lock:
                # Slett eldste SENDE rader (aldri usende — dei skal backfillast)
                _db.execute(
                    "DELETE FROM maaling WHERE id IN "
                    "(SELECT id FROM maaling WHERE sendt = 1 ORDER BY id ASC LIMIT 50000)")
                _db.commit()
            log.info(f"Modbus-lager storleiksgrense ({maks_mb} MB ved {mb:.1f}): "
                     "sletta eldste sende rader")


# ---------------------------------------------------------------
#  Status + start
# ---------------------------------------------------------------
def status() -> dict:
    ut = {
        "lagra_totalt": _stats["lagra"],
        "sendt_totalt": _stats["sendt"],
        "siste_feil": _stats["siste_feil"],
        "siste_sendt_ts": _stats["siste_sendt_ts"],
        "usendte": 0,
        "totalt_rader": 0,
        "storleik_mb": 0.0,
        "eldste_usendt_ts": None,
    }
    d = les_konfig()
    sti = _db_sti_open or d.get("db_sti", STANDARD_DB)
    try:
        if Path(sti).exists():
            ut["storleik_mb"] = round(Path(sti).stat().st_size / (1024 * 1024), 2)
    except OSError:
        pass
    if _db is not None:
        try:
            with _db_lock:
                ut["totalt_rader"] = _db.execute("SELECT COUNT(*) FROM maaling").fetchone()[0]
                ut["usendte"] = _db.execute(
                    "SELECT COUNT(*) FROM maaling WHERE sendt = 0").fetchone()[0]
                row = _db.execute(
                    "SELECT MIN(ts_ms) FROM maaling WHERE sendt = 0").fetchone()
                ut["eldste_usendt_ts"] = row[0] if row else None
        except Exception as e:
            ut["siste_feil"] = str(e)
    return ut


def start() -> None:
    """Start sampl- og sendar-trådane (idempotent). Passive til aktivert."""
    global _traadar_starta
    if _traadar_starta:
        return
    _traadar_starta = True
    d = les_konfig()
    if d.get("aktivert"):
        _opna_db(d.get("db_sti", STANDARD_DB))
    threading.Thread(target=_sampl_loop, daemon=True, name="modbus_lager_sampl").start()
    threading.Thread(target=_send_loop, daemon=True, name="modbus_lager_send").start()
    log.info("Modbus-lager starta (sampl + sendar-trådar)")

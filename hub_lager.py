#!/usr/bin/env python3
"""
Hub-lager — persistent lagring av kanaldata på hubben
=====================================================
Lagrar kanalverdiar som nodane pushar til hubben (POST /api/ingest) i ein
SQLite-database, slik at hub-data overlever omstart og kan hentast ut i
ettertid (CSV-eksport, Grafana, analyse).

I motsetnad til `hub_buffer.py` (som *pollar* LAN-nodar) hektar denne seg på
*push*-strøymen: web_ui kallar `lagre(...)` for kvar ingest-batch. Skrivinga
skjer i ein eigen tråd via ein kø, så ingest-endepunktet aldri blokkerer.

Lagringsformat (langt format — robust for vilkårlege kanalnamn/-tal):
    kanalverdi(node_id, node_namn, kanal, verdi, ts_ms)

Konfig i /data/konfig/hub_lager.json:
    aktivert         på/av
    db_sti           filsti (standard /data/maalinger/hub_kanaldata.db)
    retensjon_dagar  slett data eldre enn dette
    min_intervall_s  minste tid mellom lagra punkt per node (nedsampling)
    maks_mb          maks DB-storleik (0 = ubegrensa); eldste rader slettast

Bruk:
    import hub_lager
    hub_lager.start()
    hub_lager.lagre(node_id, node_namn, ts, {"AI 0": 1.23, ...})
"""

import json
import time
import queue
import sqlite3
import logging
import threading
from pathlib import Path
from datetime import datetime, timedelta

log = logging.getLogger("hub_lager")

KONFIG_STI = Path("/data/konfig/hub_lager.json")
STANDARD_DB = "/data/maalinger/hub_kanaldata.db"

_STANDARD = {
    "aktivert": False,
    "db_sti": STANDARD_DB,
    "retensjon_dagar": 30,
    "min_intervall_s": 1.0,
    "maks_mb": 0,
}


# ---------------------------------------------------------------
#  Konfig
# ---------------------------------------------------------------
_konfig_cache: dict = None
_konfig_mtime: float = -1.0


def les_konfig() -> dict:
    """Les konfig med mtime-cache (kallast på kvar ingest-batch — hot path)."""
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
        log.warning(f"Kunne ikkje lese hub_lager-konfig: {e}")
    # Normaliser
    try:
        d["retensjon_dagar"] = max(0, int(d.get("retensjon_dagar", 30)))
    except (TypeError, ValueError):
        d["retensjon_dagar"] = 30
    try:
        d["min_intervall_s"] = max(0.0, float(d.get("min_intervall_s", 1.0)))
    except (TypeError, ValueError):
        d["min_intervall_s"] = 1.0
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
    for nokkel in ("retensjon_dagar", "min_intervall_s", "maks_mb"):
        if nokkel in data:
            d[nokkel] = data[nokkel]
    KONFIG_STI.parent.mkdir(parents=True, exist_ok=True)
    KONFIG_STI.write_text(json.dumps(d, indent=2, ensure_ascii=False),
                          encoding="utf-8")
    # Be skrivaren plukke opp ny konfig (t.d. ny db_sti / av-på)
    global _ny_konfig
    _ny_konfig = True
    return les_konfig()


def konfig_offentleg() -> dict:
    """Konfig + køyrestatus for GUI."""
    d = les_konfig()
    d.update(status())
    return d


# ---------------------------------------------------------------
#  Skrivar (singleton)
# ---------------------------------------------------------------
_kø: "queue.Queue" = queue.Queue(maxsize=20000)
_siste_lagra: dict = {}          # node_id -> ts_ms for sist lagra punkt
_siste_lås = threading.Lock()
_stats = {"lagra": 0, "droppa_full": 0, "droppa_throttle": 0, "siste_feil": ""}
_traad = None
_ny_konfig = False
_db = None
_db_sti_open = None


def _opna_db(sti: str):
    """Opna/opprett SQLite-databasen på gjeven sti."""
    global _db, _db_sti_open
    try:
        p = Path(sti)
        p.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(str(p), check_same_thread=False, timeout=10)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=NORMAL")
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("""
            CREATE TABLE IF NOT EXISTS kanalverdi (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                node_id   TEXT NOT NULL,
                node_namn TEXT,
                kanal     TEXT NOT NULL,
                verdi     REAL,
                ts_ms     INTEGER NOT NULL
            )
        """)
        db.execute("CREATE INDEX IF NOT EXISTS idx_kv_node_ts "
                   "ON kanalverdi(node_id, ts_ms)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_kv_node_kanal_ts "
                   "ON kanalverdi(node_id, kanal, ts_ms)")
        db.commit()
        _db = db
        _db_sti_open = sti
        log.info(f"Hub-lager database open: {sti}")
        return db
    except Exception as e:
        log.error(f"Kunne ikkje opne hub-lager database ({sti}): {e}")
        _db = None
        _db_sti_open = None
        return None


def lagre(node_id: str, node_namn: str, ts: float, kanalar: dict) -> None:
    """Legg ein ingest-batch i kø for persistering (ikkje-blokkerande).

    Berre numeriske skalarverdiar vert lagra; sample-arrays (rå waveform)
    vert hoppa over. Nedsampling per node styrt av min_intervall_s.
    """
    d = les_konfig()
    if not d.get("aktivert") or not kanalar:
        return

    ts_ms = int((ts if ts and ts > 0 else time.time()) * 1000)

    # Throttle per node
    min_ms = int(d.get("min_intervall_s", 1.0) * 1000)
    if min_ms > 0:
        with _siste_lås:
            forrige = _siste_lagra.get(node_id, 0)
            if ts_ms - forrige < min_ms:
                _stats["droppa_throttle"] += 1
                return
            _siste_lagra[node_id] = ts_ms

    # Berre numeriske skalarar
    reine = {}
    for namn, verdi in kanalar.items():
        if isinstance(verdi, bool):
            continue
        if isinstance(verdi, (int, float)):
            reine[str(namn)] = float(verdi)
    if not reine:
        return

    try:
        _kø.put_nowait((node_id, node_namn or node_id, ts_ms, reine))
    except queue.Full:
        _stats["droppa_full"] += 1
        if _stats["droppa_full"] % 100 == 1:
            log.warning("Hub-lager kø full — droppar batchar "
                        f"(totalt {_stats['droppa_full']})")


def _skriv_loop():
    """Drenér køen, batch-insert, retensjon + storleiksgrense periodisk."""
    global _ny_konfig
    siste_vedlikehald = 0.0
    d = les_konfig()
    if d.get("aktivert"):
        _opna_db(d.get("db_sti", STANDARD_DB))

    while True:
        # Plukk opp konfig-endringar (av/på, ny db-sti)
        if _ny_konfig:
            _ny_konfig = False
            d = les_konfig()
            if not d.get("aktivert"):
                _lukk_db()
            elif d.get("db_sti") != _db_sti_open:
                _lukk_db()
                _opna_db(d.get("db_sti", STANDARD_DB))

        d = les_konfig()
        if not d.get("aktivert"):
            # Tøm køen og kvil
            _tøm_kø()
            time.sleep(2)
            continue
        if _db is None:
            _opna_db(d.get("db_sti", STANDARD_DB))
            if _db is None:
                time.sleep(5)
                continue

        # Saml ein batch (vent på fyrste, drenér resten utan å blokkere)
        batch = []
        try:
            batch.append(_kø.get(timeout=2))
        except queue.Empty:
            pass
        while len(batch) < 2000:
            try:
                batch.append(_kø.get_nowait())
            except queue.Empty:
                break

        if batch:
            rader = []
            for node_id, node_namn, ts_ms, reine in batch:
                for kanal, verdi in reine.items():
                    rader.append((node_id, node_namn, kanal, verdi, ts_ms))
            try:
                _db.executemany(
                    "INSERT INTO kanalverdi (node_id, node_namn, kanal, verdi, ts_ms) "
                    "VALUES (?, ?, ?, ?, ?)", rader)
                _db.commit()
                _stats["lagra"] += len(rader)
            except Exception as e:
                _stats["siste_feil"] = str(e)
                log.warning(f"Hub-lager insert feila: {e}")

        # Vedlikehald (retensjon + storleiksgrense) ~kvart 60. sek
        no = time.time()
        if no - siste_vedlikehald > 60:
            siste_vedlikehald = no
            try:
                _vedlikehald(d)
            except Exception as e:
                log.warning(f"Hub-lager vedlikehald feila: {e}")


def _tøm_kø():
    while True:
        try:
            _kø.get_nowait()
        except queue.Empty:
            return


def _lukk_db():
    global _db, _db_sti_open
    if _db is not None:
        try:
            _db.close()
        except Exception:
            pass
    _db = None
    _db_sti_open = None


def _vedlikehald(d: dict):
    """Retensjon (alder) + storleiksgrense (eldste rader)."""
    if _db is None:
        return
    dagar = int(d.get("retensjon_dagar", 30))
    if dagar > 0:
        grense_ms = int((datetime.now() - timedelta(days=dagar)).timestamp() * 1000)
        r = _db.execute("DELETE FROM kanalverdi WHERE ts_ms < ?", (grense_ms,))
        if r.rowcount > 0:
            _db.commit()
            log.info(f"Hub-lager retensjon: sletta {r.rowcount} rader "
                     f"eldre enn {dagar} dagar")

    maks_mb = int(d.get("maks_mb", 0))
    if maks_mb > 0 and _db_sti_open:
        try:
            mb = Path(_db_sti_open).stat().st_size / (1024 * 1024)
        except OSError:
            mb = 0
        if mb > maks_mb:
            # Slett dei eldste 10 % radene til vi er under grensa
            totalt = _db.execute("SELECT COUNT(*) FROM kanalverdi").fetchone()[0]
            slett = max(1000, totalt // 10)
            _db.execute(
                "DELETE FROM kanalverdi WHERE id IN "
                "(SELECT id FROM kanalverdi ORDER BY id ASC LIMIT ?)", (slett,))
            _db.commit()
            _db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            log.info(f"Hub-lager storleiksgrense ({maks_mb} MB nådd ved "
                     f"{mb:.1f} MB): sletta {slett} eldste rader")


def status() -> dict:
    """Køyrestatus for GUI/API."""
    ut = {
        "kø_lengd": _kø.qsize(),
        "lagra_totalt": _stats["lagra"],
        "droppa_full": _stats["droppa_full"],
        "droppa_throttle": _stats["droppa_throttle"],
        "siste_feil": _stats["siste_feil"],
        "rader": 0,
        "storleik_mb": 0.0,
        "eldste_ts": None,
        "nyaste_ts": None,
        "nodar": [],
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
            ut["rader"] = _db.execute("SELECT COUNT(*) FROM kanalverdi").fetchone()[0]
            row = _db.execute("SELECT MIN(ts_ms), MAX(ts_ms) FROM kanalverdi").fetchone()
            ut["eldste_ts"], ut["nyaste_ts"] = row[0], row[1]
            cur = _db.execute(
                "SELECT node_id, node_namn, COUNT(*), MAX(ts_ms) "
                "FROM kanalverdi GROUP BY node_id ORDER BY node_id")
            for r in cur:
                ut["nodar"].append({
                    "node_id": r[0], "node_namn": r[1],
                    "rader": r[2], "siste_ts": r[3],
                })
        except Exception as e:
            ut["siste_feil"] = str(e)
    return ut


def hent_data(node_id: str = "", kanal: str = "", frå_ms: int = 0,
              til_ms: int = 0, limit: int = 1000) -> list:
    """Hent lagra punkt (for visning/eksport)."""
    if _db is None:
        return []
    limit = max(1, min(int(limit or 1000), 100000))
    vilkår, arg = [], []
    if node_id:
        vilkår.append("node_id = ?"); arg.append(node_id)
    if kanal:
        vilkår.append("kanal = ?"); arg.append(kanal)
    if frå_ms:
        vilkår.append("ts_ms >= ?"); arg.append(int(frå_ms))
    if til_ms:
        vilkår.append("ts_ms <= ?"); arg.append(int(til_ms))
    where = ("WHERE " + " AND ".join(vilkår)) if vilkår else ""
    arg.append(limit)
    try:
        cur = _db.execute(
            f"SELECT node_id, node_namn, kanal, verdi, ts_ms FROM kanalverdi "
            f"{where} ORDER BY ts_ms DESC LIMIT ?", arg)
        return [{"node_id": r[0], "node_namn": r[1], "kanal": r[2],
                 "verdi": r[3], "ts_ms": r[4]} for r in cur]
    except Exception as e:
        log.warning(f"hent_data feila: {e}")
        return []


def eksport_csv(node_id: str = "", kanal: str = "", frå_ms: int = 0,
                til_ms: int = 0, limit: int = 100000):
    """Generator som gir CSV-linjer (for streaming-nedlasting)."""
    yield "tid_iso,ts_ms,node_id,node_namn,kanal,verdi\n"
    rader = hent_data(node_id, kanal, frå_ms, til_ms, limit)
    rader.reverse()  # eldste først i fila
    for r in rader:
        try:
            iso = datetime.fromtimestamp(r["ts_ms"] / 1000.0).isoformat()
        except (OSError, OverflowError, ValueError):
            iso = ""
        namn = str(r["node_namn"] or "").replace(",", " ")
        kan = str(r["kanal"] or "").replace(",", " ")
        yield f'{iso},{r["ts_ms"]},{r["node_id"]},{namn},{kan},{r["verdi"]}\n'


def start() -> None:
    """Start skrivartråden (idempotent)."""
    global _traad
    if _traad is not None and _traad.is_alive():
        return
    _traad = threading.Thread(target=_skriv_loop, daemon=True, name="hub_lager")
    _traad.start()
    log.info("Hub-lager skrivar starta")

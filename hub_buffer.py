#!/usr/bin/env python3
"""
Hub Buffer — Synkroniser måledata frå fjern-nodar
==================================================
Pollar kvar fjern-node periodisk via GET /api/buffer/data, batch-insert til
lokal hub_buffer.db, og sender ACK tilbake.

Synkroniserer også hendingar (rå waveforms) og MQTT-logg frå kvar node.

Retensjon: slett data eldre enn hub_retensjon_dagar.

Bruk:
    from hub_buffer import HubBuffer
"""

import json
import time
import base64
import sqlite3
import logging
import threading
from pathlib import Path
from datetime import datetime, timedelta
from urllib.request import urlopen, Request
from urllib.error import URLError
from urllib.parse import urlencode

from buffer_konfig import BufferKonfig, les_buffer_konfig

log = logging.getLogger('hub_buffer')

HUB_BUFFER_DB_STI = Path("/data/maalinger/hub_buffer.db")


class HubBuffer:
    """Hub-side sync-motor for måledata frå fjern-nodar."""

    def __init__(self, konfig: BufferKonfig = None):
        self._konfig = konfig or les_buffer_konfig()
        self._db: sqlite3.Connection = None
        self._lock = threading.Lock()
        self._stopp = threading.Event()
        self._sync_traad = None
        self._init_db()

    def _init_db(self):
        """Opprett hub-buffer SQLite-database."""
        try:
            HUB_BUFFER_DB_STI.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(
                str(HUB_BUFFER_DB_STI),
                check_same_thread=False,
                timeout=10,
            )
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=NORMAL")
            self._db.execute("PRAGMA busy_timeout=5000")

            self._db.execute("""
                CREATE TABLE IF NOT EXISTS maaledata (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    node_id TEXT NOT NULL,
                    fjern_id INTEGER NOT NULL,
                    tidsstempel_ms INTEGER NOT NULL,
                    kanal_0 REAL, kanal_1 REAL, kanal_2 REAL, kanal_3 REAL,
                    kanal_4 REAL, kanal_5 REAL, kanal_6 REAL, kanal_7 REAL
                )
            """)
            self._db.execute("""
                CREATE INDEX IF NOT EXISTS idx_node_tid
                ON maaledata(node_id, tidsstempel_ms)
            """)

            self._db.execute("""
                CREATE TABLE IF NOT EXISTS sync_framgang (
                    node_id TEXT PRIMARY KEY,
                    siste_fjern_id INTEGER DEFAULT 0,
                    siste_sync TEXT,
                    antal_henta INTEGER DEFAULT 0
                )
            """)

            # Hendingar frå fjern-nodar
            self._db.execute("""
                CREATE TABLE IF NOT EXISTS hendingar (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    node_id TEXT NOT NULL,
                    fjern_id INTEGER NOT NULL,
                    tidsstempel_ms INTEGER NOT NULL,
                    type TEXT NOT NULL,
                    skildring TEXT,
                    varigheit_ms INTEGER,
                    kanalar INTEGER,
                    sample_rate INTEGER,
                    raa_data BLOB
                )
            """)
            self._db.execute("""
                CREATE INDEX IF NOT EXISTS idx_hendingar_node_ts
                ON hendingar(node_id, tidsstempel_ms)
            """)

            # Sync-framgang for hendingar (per node)
            self._db.execute("""
                CREATE TABLE IF NOT EXISTS sync_framgang_hendingar (
                    node_id TEXT PRIMARY KEY,
                    siste_fjern_id INTEGER DEFAULT 0
                )
            """)

            # MQTT-logg frå fjern-nodar
            self._db.execute("""
                CREATE TABLE IF NOT EXISTS mqtt_logg (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    node_id TEXT NOT NULL,
                    fjern_id INTEGER NOT NULL,
                    tidsstempel_ms INTEGER NOT NULL,
                    topic TEXT NOT NULL,
                    verdi REAL NOT NULL
                )
            """)
            self._db.execute("""
                CREATE INDEX IF NOT EXISTS idx_mqtt_node_ts
                ON mqtt_logg(node_id, tidsstempel_ms)
            """)

            # Sync-framgang for MQTT-logg (per node)
            self._db.execute("""
                CREATE TABLE IF NOT EXISTS sync_framgang_mqtt (
                    node_id TEXT PRIMARY KEY,
                    siste_fjern_id INTEGER DEFAULT 0
                )
            """)

            self._db.commit()
            log.info(f"Hub-buffer database oppretta: {HUB_BUFFER_DB_STI}")
        except Exception as e:
            log.error(f"Kunne ikkje opprette hub-buffer database: {e}")
            self._db = None

    def start_sync(self, nodar):
        """Start bakgrunns-sync-tråd.

        Args:
            nodar: Liste av FjernNode-objekt frå hub_konfig
        """
        self._nodar = nodar
        self._sync_traad = threading.Thread(target=self._sync_loop, daemon=True)
        self._sync_traad.start()
        log.info(f"Hub buffer sync starta: intervall={self._konfig.hub_sync_intervall_sek}s, "
                 f"nodar={len(nodar)}")

    def oppdater_nodar(self, nodar):
        """Oppdater liste av nodar (t.d. etter konfig-endring)."""
        self._nodar = nodar

    def _sync_loop(self):
        """Synkroniser frå alle nodar periodisk."""
        while not self._stopp.is_set():
            for node in self._nodar:
                if self._stopp.is_set():
                    break
                if not node.aktivert:
                    continue
                try:
                    self._sync_node(node)
                except Exception as e:
                    log.warning(f"Sync feil for node '{node.namn}' ({node.adresse}): {e}")

                # Sync hendingar og MQTT-logg
                try:
                    self._sync_node_hendingar(node)
                except Exception as e:
                    log.debug(f"Hendingar sync feil for '{node.namn}': {e}")

                try:
                    self._sync_node_mqtt(node)
                except Exception as e:
                    log.debug(f"MQTT sync feil for '{node.namn}': {e}")

            # Retensjon etter kvar sync-runde
            try:
                self._retensjon()
            except Exception as e:
                log.warning(f"Retensjon feil: {e}")

            self._stopp.wait(timeout=self._konfig.hub_sync_intervall_sek)

    def _sync_node(self, node):
        """Synkroniser éin node: hent data, insert, ACK."""
        if self._db is None:
            return

        # Hent siste sync-framgang
        cur = self._db.execute(
            "SELECT siste_fjern_id FROM sync_framgang WHERE node_id = ?",
            (node.id,)
        )
        row = cur.fetchone()
        siste_id = row[0] if row else 0

        # Hent data frå remote node
        qs = urlencode({"etter_id": siste_id, "limit": self._konfig.hub_batch_storleik})
        url = f"http://{node.adresse}:8080/api/buffer/data?{qs}"

        try:
            with urlopen(url, timeout=30) as resp:
                data = json.loads(resp.read())
        except (URLError, OSError) as e:
            log.debug(f"Kunne ikkje hente buffer frå {node.namn}: {e}")
            return

        rader = data.get("rader", [])
        if not rader:
            return

        # Batch-insert til hub-buffer
        with self._lock:
            max_fjern_id = siste_id
            for rad in rader:
                fjern_id = rad["id"]
                ts = rad["ts"]
                kanalar = rad["k"]
                # Pad kanalar til 8 viss kortare
                while len(kanalar) < 8:
                    kanalar.append(None)

                self._db.execute(
                    """INSERT INTO maaledata
                       (node_id, fjern_id, tidsstempel_ms,
                        kanal_0, kanal_1, kanal_2, kanal_3,
                        kanal_4, kanal_5, kanal_6, kanal_7)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (node.id, fjern_id, ts,
                     kanalar[0], kanalar[1], kanalar[2], kanalar[3],
                     kanalar[4], kanalar[5], kanalar[6], kanalar[7])
                )
                if fjern_id > max_fjern_id:
                    max_fjern_id = fjern_id

            # Oppdater sync-framgang
            self._db.execute(
                """INSERT INTO sync_framgang (node_id, siste_fjern_id, siste_sync, antal_henta)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(node_id) DO UPDATE SET
                     siste_fjern_id = excluded.siste_fjern_id,
                     siste_sync = excluded.siste_sync,
                     antal_henta = antal_henta + excluded.antal_henta""",
                (node.id, max_fjern_id, datetime.now().isoformat(), len(rader))
            )
            self._db.commit()

        # Send ACK til remote node
        ack_url = f"http://{node.adresse}:8080/api/buffer/ack"
        try:
            ack_data = json.dumps({"opp_til_id": max_fjern_id}).encode('utf-8')
            ack_req = Request(ack_url, data=ack_data,
                              headers={"Content-Type": "application/json"},
                              method="POST")
            with urlopen(ack_req, timeout=10):
                pass
        except (URLError, OSError) as e:
            log.warning(f"ACK feil for {node.namn}: {e}")

        log.info(f"Synkronisert {len(rader)} rader frå '{node.namn}' "
                 f"(id {siste_id}→{max_fjern_id})")

    def _sync_node_hendingar(self, node):
        """Synkroniser hendingar frå éin node."""
        if self._db is None:
            return

        # Hent siste sync-framgang for hendingar
        cur = self._db.execute(
            "SELECT siste_fjern_id FROM sync_framgang_hendingar WHERE node_id = ?",
            (node.id,)
        )
        row = cur.fetchone()
        siste_id = row[0] if row else 0

        qs = urlencode({"etter_id": siste_id, "limit": 100})
        url = f"http://{node.adresse}:8080/api/buffer/hendingar?{qs}"

        try:
            with urlopen(url, timeout=30) as resp:
                data = json.loads(resp.read())
        except (URLError, OSError):
            return

        hendingar = data.get("hendingar", [])
        if not hendingar:
            return

        with self._lock:
            max_fjern_id = siste_id
            for h in hendingar:
                fjern_id = h["id"]
                # Dekod rå-data frå base64 viss tilgjengeleg
                raa_data = None
                if "raa_data_b64" in h:
                    try:
                        raa_data = base64.b64decode(h["raa_data_b64"])
                    except Exception:
                        pass

                self._db.execute(
                    """INSERT INTO hendingar
                       (node_id, fjern_id, tidsstempel_ms, type, skildring,
                        varigheit_ms, kanalar, sample_rate, raa_data)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (node.id, fjern_id, h.get("ts", h.get("tidsstempel_ms", 0)),
                     h["type"], h.get("skildring", ""),
                     h.get("varigheit_ms"), h.get("kanalar"),
                     h.get("sample_rate"), raa_data)
                )
                if fjern_id > max_fjern_id:
                    max_fjern_id = fjern_id

            self._db.execute(
                """INSERT INTO sync_framgang_hendingar (node_id, siste_fjern_id)
                   VALUES (?, ?)
                   ON CONFLICT(node_id) DO UPDATE SET
                     siste_fjern_id = excluded.siste_fjern_id""",
                (node.id, max_fjern_id)
            )
            self._db.commit()

        log.info(f"Synkronisert {len(hendingar)} hendingar frå '{node.namn}'")

    def _sync_node_mqtt(self, node):
        """Synkroniser MQTT-logg frå éin node."""
        if self._db is None:
            return

        cur = self._db.execute(
            "SELECT siste_fjern_id FROM sync_framgang_mqtt WHERE node_id = ?",
            (node.id,)
        )
        row = cur.fetchone()
        siste_id = row[0] if row else 0

        qs = urlencode({"etter_tid": 0, "limit": self._konfig.hub_batch_storleik})
        url = f"http://{node.adresse}:8080/api/buffer/mqtt-logg?{qs}"

        try:
            with urlopen(url, timeout=30) as resp:
                data = json.loads(resp.read())
        except (URLError, OSError):
            return

        rader = data.get("rader", [])
        if not rader:
            return

        # Filter: berre rader med id > siste_id
        nye_rader = [r for r in rader if r.get("id", 0) > siste_id]
        if not nye_rader:
            return

        with self._lock:
            max_fjern_id = siste_id
            for r in nye_rader:
                fjern_id = r["id"]
                self._db.execute(
                    """INSERT INTO mqtt_logg
                       (node_id, fjern_id, tidsstempel_ms, topic, verdi)
                       VALUES (?, ?, ?, ?, ?)""",
                    (node.id, fjern_id,
                     r.get("tidsstempel_ms", r.get("ts", 0)),
                     r["topic"], r["verdi"])
                )
                if fjern_id > max_fjern_id:
                    max_fjern_id = fjern_id

            self._db.execute(
                """INSERT INTO sync_framgang_mqtt (node_id, siste_fjern_id)
                   VALUES (?, ?)
                   ON CONFLICT(node_id) DO UPDATE SET
                     siste_fjern_id = excluded.siste_fjern_id""",
                (node.id, max_fjern_id)
            )
            self._db.commit()

        log.info(f"Synkronisert {len(nye_rader)} MQTT-logg rader frå '{node.namn}'")

    def _retensjon(self):
        """Slett data eldre enn hub_retensjon_dagar."""
        if self._db is None:
            return

        grense_ms = int(
            (datetime.now() - timedelta(days=self._konfig.hub_retensjon_dagar))
            .timestamp() * 1000
        )

        with self._lock:
            result = self._db.execute(
                "DELETE FROM maaledata WHERE tidsstempel_ms < ?",
                (grense_ms,)
            )
            if result.rowcount > 0:
                self._db.commit()
                log.info(f"Hub-retensjon: sletta {result.rowcount} maaledata-rader "
                         f"eldre enn {self._konfig.hub_retensjon_dagar} dagar")

            # Retensjon for hendingar
            result2 = self._db.execute(
                "DELETE FROM hendingar WHERE tidsstempel_ms < ?",
                (grense_ms,)
            )
            if result2.rowcount > 0:
                self._db.commit()
                log.info(f"Hub-retensjon: sletta {result2.rowcount} hendingar")

            # Retensjon for MQTT-logg
            result3 = self._db.execute(
                "DELETE FROM mqtt_logg WHERE tidsstempel_ms < ?",
                (grense_ms,)
            )
            if result3.rowcount > 0:
                self._db.commit()
                log.info(f"Hub-retensjon: sletta {result3.rowcount} mqtt_logg-rader")

    def hent_status(self) -> dict:
        """Hent hub-buffer status for web API."""
        if self._db is None:
            return {"aktivert": False, "totalt_rader": 0}

        try:
            totalt = self._db.execute(
                "SELECT COUNT(*) FROM maaledata"
            ).fetchone()[0]

            # Per-node statistikk
            node_stats = []
            cur = self._db.execute(
                """SELECT sf.node_id, sf.siste_fjern_id, sf.siste_sync, sf.antal_henta,
                          (SELECT COUNT(*) FROM maaledata m WHERE m.node_id = sf.node_id)
                   FROM sync_framgang sf"""
            )
            for row in cur:
                node_stats.append({
                    "node_id": row[0],
                    "siste_fjern_id": row[1],
                    "siste_sync": row[2],
                    "antal_henta": row[3],
                    "rader_i_hub": row[4],
                })

            # DB-storleik
            storleik_mb = 0.0
            if HUB_BUFFER_DB_STI.exists():
                storleik_mb = round(
                    HUB_BUFFER_DB_STI.stat().st_size / (1024 * 1024), 2
                )

            eldste = self._db.execute(
                "SELECT MIN(tidsstempel_ms) FROM maaledata"
            ).fetchone()[0]
            nyaste = self._db.execute(
                "SELECT MAX(tidsstempel_ms) FROM maaledata"
            ).fetchone()[0]

            # Hendingar og MQTT-logg tal
            hendingar_totalt = 0
            mqtt_logg_rader = 0
            try:
                hendingar_totalt = self._db.execute(
                    "SELECT COUNT(*) FROM hendingar"
                ).fetchone()[0]
                mqtt_logg_rader = self._db.execute(
                    "SELECT COUNT(*) FROM mqtt_logg"
                ).fetchone()[0]
            except Exception:
                pass

            return {
                "aktivert": True,
                "totalt_rader": totalt,
                "storleik_mb": storleik_mb,
                "eldste_ts": eldste,
                "nyaste_ts": nyaste,
                "retensjon_dagar": self._konfig.hub_retensjon_dagar,
                "sync_intervall_sek": self._konfig.hub_sync_intervall_sek,
                "nodar": node_stats,
                "hendingar_totalt": hendingar_totalt,
                "mqtt_logg_rader": mqtt_logg_rader,
            }
        except Exception as e:
            log.error(f"Feil ved henting av hub-buffer status: {e}")
            return {"aktivert": True, "feil": str(e)}

    def stopp(self):
        """Stopp sync-tråd og lukk database."""
        self._stopp.set()
        if self._sync_traad:
            self._sync_traad.join(timeout=10)
        if self._db:
            try:
                self._db.close()
            except Exception:
                pass
            self._db = None
        log.info("Hub-buffer stoppa")

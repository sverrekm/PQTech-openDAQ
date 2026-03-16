#!/usr/bin/env python3
"""
Buffer-skrivar — Lokal SQLite ringbuffer for måledata
=====================================================
Akkumulerer rå int16 ADC-samples i minne. Kvart intervall_ms (standard 100ms)
vert RMS berekna per kanal og skriven som éi rad til SQLite.

Synkronisering:
    Hub hentar usynkroniserte rader via hent_usynkronisert() og
    stadfester med marker_synkronisert(). Eldste *synkroniserte* data
    vert automatisk sletta når lagring nærmar seg grensa.

Bruk:
    from buffer_skrivar import BufferSkrivar
"""

import os
import time
import sqlite3
import logging
import threading
from pathlib import Path

import numpy as np

from buffer_konfig import BufferKonfig, les_buffer_konfig

log = logging.getLogger('buffer_skrivar')

BUFFER_DB_STI = Path("/data/maalingar/buffer.db")
ANTAL_KANALAR = 8
# Ved 20kHz sample rate og 100ms intervall = 2000 samples per intervall
SAMPLES_PER_100MS = 2000


class BufferSkrivar:
    """Lokal ringbuffer for aggregert måledata (RMS per 100ms)."""

    def __init__(self, konfig: BufferKonfig = None):
        self._konfig = konfig or les_buffer_konfig()
        self._lock = threading.Lock()
        self._db: sqlite3.Connection = None

        # Akkumulerings-buffer i minne (per kanal)
        self._akk_data = {}      # key → list of float arrays
        self._akk_count = 0      # Antal samples akkumulert

        # Skalering (vert sett frå opendaq_bro etter nullpunkt-kalibrering)
        self._kanal_skala = []   # Skaleringsfaktor per kanal
        self._kanal_offset = []  # Offset per kanal
        self._adc_nullpunkt = {} # key → float (raw int16 DC offset)
        self._skalering_klar = False

        # Samples per intervall (vert berekna frå sample_rate og intervall_ms)
        self._samples_per_intervall = int(
            (self._konfig.intervall_ms / 1000.0) * 20000  # 20kHz standard
        )

        # Opprydding-tråd
        self._opprydding_stopp = threading.Event()
        self._opprydding_traad = None

        # Statistikk
        self._totalt_skrive = 0
        self._siste_skriv_tid = 0.0

        if self._konfig.aktivert:
            self._init_db()
            self._start_opprydding()

    def _init_db(self):
        """Opprett SQLite-database med WAL-modus."""
        try:
            BUFFER_DB_STI.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(
                str(BUFFER_DB_STI),
                check_same_thread=False,
                timeout=10,
            )
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=NORMAL")
            self._db.execute("PRAGMA busy_timeout=5000")

            self._db.execute("""
                CREATE TABLE IF NOT EXISTS maaledata (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tidsstempel_ms INTEGER NOT NULL,
                    kanal_0 REAL, kanal_1 REAL, kanal_2 REAL, kanal_3 REAL,
                    kanal_4 REAL, kanal_5 REAL, kanal_6 REAL, kanal_7 REAL,
                    synkronisert INTEGER DEFAULT 0
                )
            """)
            self._db.execute("""
                CREATE INDEX IF NOT EXISTS idx_ts
                ON maaledata(tidsstempel_ms)
            """)
            self._db.execute("""
                CREATE INDEX IF NOT EXISTS idx_usynk
                ON maaledata(synkronisert, tidsstempel_ms)
                WHERE synkronisert = 0
            """)
            self._db.commit()
            log.info(f"Buffer-database oppretta: {BUFFER_DB_STI}")
        except Exception as e:
            log.error(f"Kunne ikkje opprette buffer-database: {e}")
            self._db = None

    def oppdater_skalering(self, kanal_skala: list, kanal_offset: list,
                           adc_nullpunkt: dict):
        """Oppdater skaleringsparametrar frå opendaq_bro etter nullpunkt-kalibrering."""
        with self._lock:
            self._kanal_skala = list(kanal_skala)
            self._kanal_offset = list(kanal_offset)
            self._adc_nullpunkt = dict(adc_nullpunkt)
            self._skalering_klar = True
            log.info(f"Buffer-skalering oppdatert: {len(kanal_skala)} kanalar")

    def mottak_data(self, kanal_data: dict):
        """Motta rå ADC-data frå _global_data_callback.

        Akkumulerer samples i minne. Når nok samples er samla
        (intervall_ms ved gitt sample rate), berekn RMS og skriv til SQLite.

        Args:
            kanal_data: dict med "kanal_0" → np.array(int16), osv.
        """
        if not self._konfig.aktivert or self._db is None:
            return

        # Akkumuler data
        for key, data in kanal_data.items():
            if data is None or len(data) == 0:
                continue
            if key not in self._akk_data:
                self._akk_data[key] = []
            self._akk_data[key].append(data)

        # Tell samples (bruk fyrste kanal som referanse)
        for v in kanal_data.values():
            if v is not None and len(v) > 0:
                self._akk_count += len(v)
                break

        # Sjekk om vi har nok for eitt intervall
        if self._akk_count >= self._samples_per_intervall:
            self._flush_intervall()

    def _flush_intervall(self):
        """Berekn RMS per kanal og skriv til SQLite."""
        tidsstempel = int(time.time() * 1000)

        # Berekn RMS per kanal med skalering
        rms_verdiar = [None] * ANTAL_KANALAR
        for i in range(ANTAL_KANALAR):
            key = f"kanal_{i}"
            if key not in self._akk_data or not self._akk_data[key]:
                continue

            # Konkatener alle akkumulerte arrays
            raw = np.concatenate(self._akk_data[key])

            if self._skalering_klar:
                # Skaler til fysiske einingar (same logikk som opendaq_bro)
                skala = (self._kanal_skala[i]
                         if i < len(self._kanal_skala) else 1.0)
                offset = (self._kanal_offset[i]
                          if i < len(self._kanal_offset) else 0.0)
                nullpunkt = self._adc_nullpunkt.get(key, 0.0)

                fdata = raw.astype(np.float64) * skala
                if nullpunkt != 0.0:
                    fdata -= nullpunkt * skala
                if offset != 0.0:
                    fdata += offset
            else:
                # Før kalibrering: bruk rå verdiar normalisert til ±1.0
                fdata = raw.astype(np.float64) / 32768.0

            rms = float(np.sqrt(np.mean(fdata ** 2)))
            rms_verdiar[i] = round(rms, 6)

        # Skriv til SQLite
        try:
            self._db.execute(
                """INSERT INTO maaledata
                   (tidsstempel_ms, kanal_0, kanal_1, kanal_2, kanal_3,
                    kanal_4, kanal_5, kanal_6, kanal_7)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (tidsstempel, *rms_verdiar)
            )
            self._db.commit()
            self._totalt_skrive += 1
            self._siste_skriv_tid = time.time()
        except Exception as e:
            if self._totalt_skrive == 0 or self._totalt_skrive % 1000 == 0:
                log.error(f"Buffer-skriving feila: {e}")

        # Nullstill akkumulator
        self._akk_data = {}
        self._akk_count = 0

    def hent_usynkronisert(self, limit: int = 10000, etter_id: int = 0) -> list:
        """Hent usynkroniserte rader for hub-synkronisering.

        Returns:
            Liste av dict med id, tidsstempel_ms, k (liste av 8 verdiar)
        """
        if self._db is None:
            return []

        try:
            cur = self._db.execute(
                """SELECT id, tidsstempel_ms,
                          kanal_0, kanal_1, kanal_2, kanal_3,
                          kanal_4, kanal_5, kanal_6, kanal_7
                   FROM maaledata
                   WHERE synkronisert = 0 AND id > ?
                   ORDER BY id ASC
                   LIMIT ?""",
                (etter_id, limit)
            )
            rader = []
            for row in cur:
                rader.append({
                    "id": row[0],
                    "ts": row[1],
                    "k": list(row[2:10]),
                })
            return rader
        except Exception as e:
            log.error(f"Feil ved henting av usynkronisert data: {e}")
            return []

    def marker_synkronisert(self, opp_til_id: int) -> bool:
        """Marker rader som synkroniserte (opp til og inkludert gitt id)."""
        if self._db is None:
            return False

        try:
            self._db.execute(
                "UPDATE maaledata SET synkronisert = 1 WHERE id <= ? AND synkronisert = 0",
                (opp_til_id,)
            )
            self._db.commit()
            return True
        except Exception as e:
            log.error(f"Feil ved synkroniserings-markering: {e}")
            return False

    def hent_status(self) -> dict:
        """Hent buffer-status for web API."""
        if self._db is None:
            return {
                "aktivert": self._konfig.aktivert,
                "totalt_rader": 0,
                "usynkroniserte": 0,
                "synkroniserte": 0,
                "storleik_mb": 0.0,
                "eldste_ts": None,
                "nyaste_ts": None,
                "skalering_klar": self._skalering_klar,
                "skriv_per_sek": 0.0,
            }

        try:
            totalt = self._db.execute(
                "SELECT COUNT(*) FROM maaledata"
            ).fetchone()[0]

            usynk = self._db.execute(
                "SELECT COUNT(*) FROM maaledata WHERE synkronisert = 0"
            ).fetchone()[0]

            eldste = self._db.execute(
                "SELECT MIN(tidsstempel_ms) FROM maaledata"
            ).fetchone()[0]

            nyaste = self._db.execute(
                "SELECT MAX(tidsstempel_ms) FROM maaledata"
            ).fetchone()[0]

            # DB-storleik frå fil
            storleik_mb = 0.0
            if BUFFER_DB_STI.exists():
                storleik_mb = round(
                    BUFFER_DB_STI.stat().st_size / (1024 * 1024), 2
                )

            return {
                "aktivert": self._konfig.aktivert,
                "totalt_rader": totalt,
                "usynkroniserte": usynk,
                "synkroniserte": totalt - usynk,
                "storleik_mb": storleik_mb,
                "maks_storleik_mb": self._konfig.maks_storleik_mb,
                "eldste_ts": eldste,
                "nyaste_ts": nyaste,
                "skalering_klar": self._skalering_klar,
                "skriv_per_sek": round(1000.0 / self._konfig.intervall_ms, 1),
            }
        except Exception as e:
            log.error(f"Feil ved henting av buffer-status: {e}")
            return {
                "aktivert": self._konfig.aktivert,
                "totalt_rader": 0,
                "feil": str(e),
            }

    def _start_opprydding(self):
        """Start bakgrunns-tråd for opprydding."""
        self._opprydding_traad = threading.Thread(
            target=self._opprydding_loop, daemon=True
        )
        self._opprydding_traad.start()

    def _opprydding_loop(self):
        """Sjekk DB-storleik kvart 60s og slett eldste synkroniserte rader."""
        while not self._opprydding_stopp.is_set():
            self._opprydding_stopp.wait(timeout=60)
            if self._opprydding_stopp.is_set():
                break
            self._opprydding()

    def _opprydding(self):
        """Slett eldste synkroniserte rader om DB > 90% av maks."""
        if self._db is None:
            return

        try:
            if not BUFFER_DB_STI.exists():
                return

            storleik_mb = BUFFER_DB_STI.stat().st_size / (1024 * 1024)
            grense = self._konfig.maks_storleik_mb * 0.9

            if storleik_mb < grense:
                return

            log.info(f"Buffer-opprydding: {storleik_mb:.1f}MB / "
                     f"{self._konfig.maks_storleik_mb}MB (grense {grense:.0f}MB)")

            if self._konfig.bevar_usynkronisert:
                # Slett berre synkroniserte rader (eldste fyrst)
                self._db.execute(
                    """DELETE FROM maaledata WHERE id IN (
                        SELECT id FROM maaledata
                        WHERE synkronisert = 1
                        ORDER BY id ASC
                        LIMIT 50000
                    )"""
                )
            else:
                # Slett eldste rader uansett
                self._db.execute(
                    """DELETE FROM maaledata WHERE id IN (
                        SELECT id FROM maaledata
                        ORDER BY id ASC
                        LIMIT 50000
                    )"""
                )

            self._db.commit()
            # VACUUM for å frigjere plass (kan ta litt tid)
            self._db.execute("PRAGMA incremental_vacuum")
            log.info("Buffer-opprydding fullført")

        except Exception as e:
            log.error(f"Feil under buffer-opprydding: {e}")

    def oppdater_konfig(self, ny_konfig: BufferKonfig):
        """Oppdater konfig under køyring."""
        with self._lock:
            var_aktivert = self._konfig.aktivert
            self._konfig = ny_konfig
            self._samples_per_intervall = int(
                (ny_konfig.intervall_ms / 1000.0) * 20000
            )

            # Start/stopp DB viss aktivert-status endra
            if ny_konfig.aktivert and not var_aktivert and self._db is None:
                self._init_db()
                self._start_opprydding()
            elif not ny_konfig.aktivert and var_aktivert:
                log.info("Buffer deaktivert")

    def stopp(self):
        """Stopp buffer-skrivar og lukk database."""
        self._opprydding_stopp.set()
        if self._opprydding_traad:
            self._opprydding_traad.join(timeout=5)
        if self._db:
            try:
                self._db.close()
            except Exception:
                pass
            self._db = None
        log.info("Buffer-skrivar stoppa")

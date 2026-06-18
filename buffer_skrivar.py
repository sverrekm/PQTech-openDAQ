#!/usr/bin/env python3
"""
Buffer-skrivar — Lokal SQLite ringbuffer for måledata
=====================================================
Akkumulerer rå int16 ADC-samples i minne. Kvart intervall_ms (standard 100ms)
vert RMS berekna per kanal og skriven som éi rad til SQLite.

Lag 2: RAM ringbuffer held rå samples for hendingsdeteksjon.
Hendingar (RMS-terskel, dV/dt, MQTT-endring) triggar lagring av rå waveforms.
MQTT-verdiar vert logga til eigen tabell for felles tidslinje.

Synkronisering:
    Hub hentar usynkroniserte rader via hent_usynkronisert() og
    stadfester med marker_synkronisert(). Eldste *synkroniserte* data
    vert automatisk sletta når lagring nærmar seg grensa.

Bruk:
    from buffer_skrivar import BufferSkrivar
"""

import os
import time
import zlib
import shutil
import sqlite3
import logging
import threading
from pathlib import Path

import numpy as np

from buffer_konfig import BufferKonfig, les_buffer_konfig

try:
    from kanal_konfig import les_konfig as _les_kanal_konfig
except Exception:  # pragma: no cover
    _les_kanal_konfig = None

log = logging.getLogger('buffer_skrivar')

ANTAL_KANALAR = 8


class RingBuffer:
    """Sirkulær numpy-buffer for rå ADC-data.

    O(1) skriving, ingen allokering etter init.
    Trådtrygg: éin skrivar + éin lesar (hendingsdeteksjon).
    """

    def __init__(self, kapasitet: int, kanalar: int = ANTAL_KANALAR):
        self.kapasitet = kapasitet
        self.kanalar = kanalar
        self._buf = np.zeros((kapasitet, kanalar), dtype=np.float64)
        self._skrive_pos = 0
        self._fyllt = False  # True etter fyrste wrap-around
        self._lock = threading.Lock()

    def skriv(self, data: np.ndarray):
        """Skriv N samples (shape: N x kanalar). Handterer wrap-around."""
        n = data.shape[0]
        if n == 0:
            return
        with self._lock:
            if n >= self.kapasitet:
                # Data er større enn buffer — ta berre siste kapasitet samples
                self._buf[:] = data[-self.kapasitet:]
                self._skrive_pos = 0
                self._fyllt = True
                return

            slutt = self._skrive_pos + n
            if slutt <= self.kapasitet:
                self._buf[self._skrive_pos:slutt] = data
            else:
                # Wrap-around
                fyrste_del = self.kapasitet - self._skrive_pos
                self._buf[self._skrive_pos:] = data[:fyrste_del]
                resten = n - fyrste_del
                self._buf[:resten] = data[fyrste_del:]
                self._fyllt = True

            self._skrive_pos = slutt % self.kapasitet
            if slutt >= self.kapasitet:
                self._fyllt = True

    def hent_siste(self, n: int) -> np.ndarray:
        """Hent dei siste N samples."""
        with self._lock:
            tilgjengeleg = self.kapasitet if self._fyllt else self._skrive_pos
            n = min(n, tilgjengeleg)
            if n == 0:
                return np.zeros((0, self.kanalar), dtype=np.float64)

            start = self._skrive_pos - n
            if start >= 0:
                return self._buf[start:self._skrive_pos].copy()
            else:
                # Wrap-around
                return np.concatenate([
                    self._buf[start:],  # start er negativ → frå slutten
                    self._buf[:self._skrive_pos]
                ])

    @property
    def tilgjengeleg(self) -> int:
        """Antal samples tilgjengeleg i bufferen."""
        with self._lock:
            return self.kapasitet if self._fyllt else self._skrive_pos


def _finn_lagringssti(konfig: BufferKonfig) -> Path:
    """Finn beste lagringssti: SSD > SD-kort."""
    if konfig.ssd_sti:
        sti = Path(konfig.ssd_sti)
        if sti.exists():
            return sti / "buffer.db"
    # Auto-detect SSD
    ssd = Path("/data/ssd")
    if ssd.exists() and ssd.is_dir():
        return ssd / "buffer.db"
    return Path("/data/maalinger/buffer.db")


class BufferSkrivar:
    """Lokal ringbuffer for aggregert måledata (RMS per 100ms) +
    RAM ringbuffer for hendingsdeteksjon + MQTT-logging."""

    def __init__(self, konfig: BufferKonfig = None):
        self._konfig = konfig or les_buffer_konfig()
        self._lock = threading.Lock()
        self._db: sqlite3.Connection = None
        self._db_sti: Path = None

        # Akkumulerings-buffer i minne (per kanal)
        self._akk_data = {}      # key → list of float arrays
        self._akk_count = 0      # Antal samples akkumulert

        # Skalering (vert sett frå opendaq_bro etter nullpunkt-kalibrering)
        self._kanal_skala = []   # Skaleringsfaktor per kanal
        self._kanal_offset = []  # Offset per kanal
        self._adc_nullpunkt = {} # key → float (raw int16 DC offset)
        self._skalering_klar = False

        # Samples per intervall (berekna frå sample_rate og intervall_ms)
        self._samples_per_intervall = int(
            (self._konfig.intervall_ms / 1000.0) * self._konfig.sample_rate
        )

        # RAM ringbuffer for rå data
        ring_kapasitet = self._konfig.sample_rate * self._konfig.ram_buffer_sekund
        self._ring = RingBuffer(ring_kapasitet, ANTAL_KANALAR)

        # Hendingsdeteksjon
        self._stopp = threading.Event()
        self._hendingsdeteksjon_traad = None
        self._glidande_rms = np.zeros(ANTAL_KANALAR, dtype=np.float64)
        self._glidande_rms_n = 0
        self._siste_mqtt_verdiar = {}  # topic → verdi (for endring-deteksjon)
        self._mqtt_endring_flagg = False

        # Opprydding-tråd
        self._opprydding_traad = None

        # Statistikk
        self._totalt_skrive = 0
        self._siste_skriv_tid = 0.0

        if self._konfig.aktivert:
            self._init_db()
            self._start_opprydding()
            if self._konfig.hendingar_aktivert:
                self._start_hendingsdeteksjon()

    def _init_db(self):
        """Opprett SQLite-database med WAL-modus."""
        try:
            self._db_sti = _finn_lagringssti(self._konfig)
            self._db_sti.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(
                str(self._db_sti),
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

            # Hendingar-tabell
            self._db.execute("""
                CREATE TABLE IF NOT EXISTS hendingar (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tidsstempel_ms INTEGER NOT NULL,
                    type TEXT NOT NULL,
                    skildring TEXT,
                    varigheit_ms INTEGER,
                    kanalar INTEGER DEFAULT 8,
                    sample_rate INTEGER,
                    raa_data BLOB,
                    synkronisert INTEGER DEFAULT 0
                )
            """)
            self._db.execute("""
                CREATE INDEX IF NOT EXISTS idx_hendingar_ts
                ON hendingar(tidsstempel_ms)
            """)

            # MQTT-logg tabell
            self._db.execute("""
                CREATE TABLE IF NOT EXISTS mqtt_logg (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tidsstempel_ms INTEGER NOT NULL,
                    topic TEXT NOT NULL,
                    verdi REAL NOT NULL,
                    synkronisert INTEGER DEFAULT 0
                )
            """)
            self._db.execute("""
                CREATE INDEX IF NOT EXISTS idx_mqtt_ts
                ON mqtt_logg(tidsstempel_ms)
            """)

            self._db.commit()
            log.info(f"Buffer-database oppretta: {self._db_sti}")
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
        Skriv alltid til RAM ringbuffer for hendingsdeteksjon.

        Args:
            kanal_data: dict med "kanal_0" → np.array(int16), osv.
        """
        if not self._konfig.aktivert or self._db is None:
            return

        # Bygg skalert data-matrise for RAM ringbuffer
        fyrste_key = None
        sample_count = 0
        for key, data in kanal_data.items():
            if data is not None and len(data) > 0:
                fyrste_key = key
                sample_count = len(data)
                break

        if sample_count > 0:
            # Skaler og skriv til RAM ringbuffer
            skalert = np.zeros((sample_count, ANTAL_KANALAR), dtype=np.float64)
            for i in range(ANTAL_KANALAR):
                key = f"kanal_{i}"
                raw = kanal_data.get(key)
                if raw is None or len(raw) == 0:
                    continue
                if self._skalering_klar:
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
                    skalert[:len(fdata), i] = fdata
                else:
                    skalert[:len(raw), i] = raw.astype(np.float64) / 32768.0

            self._ring.skriv(skalert)

        # Akkumuler for RMS-flush
        for key, data in kanal_data.items():
            if data is None or len(data) == 0:
                continue
            if key not in self._akk_data:
                self._akk_data[key] = []
            self._akk_data[key].append(data)

        # Tell samples (bruk fyrste kanal som referanse)
        if sample_count > 0:
            self._akk_count += sample_count

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

            # Oppdater glidande RMS for hendingsdeteksjon
            alpha = 0.01  # Glattingsfaktor (~10s ved 10 Hz)
            if self._glidande_rms_n < 100:
                # Bootstrap: bruk snitt av fyrste 100 verdiar
                self._glidande_rms[i] = (
                    (self._glidande_rms[i] * self._glidande_rms_n + rms)
                    / (self._glidande_rms_n + 1)
                )
            else:
                self._glidande_rms[i] = (
                    (1 - alpha) * self._glidande_rms[i] + alpha * rms
                )

        self._glidande_rms_n += 1

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

    # --- MQTT-logging ---

    def mottak_mqtt(self, topic: str, verdi: float, tidsstempel_ms: int = None):
        """Logg éin MQTT-verdi til mqtt_logg-tabell.

        Kallar også endring-deteksjon for hendingsdeteksjon.
        """
        if not self._konfig.mqtt_logg_aktivert or self._db is None:
            return

        ts = tidsstempel_ms or int(time.time() * 1000)

        # Endring-deteksjon for hendingsdeteksjon
        if topic in self._siste_mqtt_verdiar:
            diff = abs(verdi - self._siste_mqtt_verdiar[topic])
            if diff >= self._konfig.mqtt_endring_terskel:
                self._mqtt_endring_flagg = True
        self._siste_mqtt_verdiar[topic] = verdi

        try:
            self._db.execute(
                "INSERT INTO mqtt_logg (tidsstempel_ms, topic, verdi) VALUES (?, ?, ?)",
                (ts, topic, verdi)
            )
            self._db.commit()
        except Exception as e:
            log.debug(f"MQTT-logg skriving feila: {e}")

    # --- Hendingsdeteksjon ---

    def _start_hendingsdeteksjon(self):
        """Start bakgrunnstråd for hendingsdeteksjon."""
        self._hendingsdeteksjon_traad = threading.Thread(
            target=self._hendingsdeteksjon_loop, daemon=True
        )
        self._hendingsdeteksjon_traad.start()
        log.info("Hendingsdeteksjon starta")

    def _hendingsdeteksjon_loop(self):
        """Sjekk for hendingar basert på RAM-buffer."""
        while not self._stopp.is_set():
            self._stopp.wait(timeout=0.1)  # Sjekk 10 gonger per sek
            if self._stopp.is_set():
                break
            if not self._konfig.hendingar_aktivert:
                continue
            if self._glidande_rms_n < 100:
                continue  # Vent til glidande snitt er stabil

            try:
                self._sjekk_hendingar()
            except Exception as e:
                log.debug(f"Hendingsdeteksjon feil: {e}")

    def _aktive_kanalar(self) -> set:
        """Set av aktive kanal-indeksar (cacha ~5s).

        Hendingsdeteksjon (RMS/dV/dt) skal berre køyre på kanalar som er
        aktiverte i kanal-konfigen — elles triggar floating/usette inngangar
        (t.d. straum-kanalar utan sensor) kontinuerleg på støy. Tomt set =>
        ingen kanalar => ingen RMS/dV/dt-hendingar.
        """
        no = time.time()
        if (getattr(self, "_aktive_cache", None) is not None
                and no - getattr(self, "_aktive_cache_ts", 0.0) < 5.0):
            return self._aktive_cache
        aktive = set(range(ANTAL_KANALAR))  # fallback: gamal åtferd (alle)
        if _les_kanal_konfig is not None:
            try:
                aktive = {int(k.indeks) for k in _les_kanal_konfig()
                          if getattr(k, "aktiv", True)}
            except Exception:
                pass
        self._aktive_cache = aktive
        self._aktive_cache_ts = no
        return aktive

    def _sjekk_hendingar(self):
        """Sjekk alle trigger-typar."""
        sr = self._konfig.sample_rate
        aktive = self._aktive_kanalar()

        # 1. RMS-terskel: samanlikn siste 100ms RMS med glidande snitt
        siste_n = int(sr * 0.1)  # 100ms
        if siste_n > 0 and self._ring.tilgjengeleg >= siste_n:
            siste_data = self._ring.hent_siste(siste_n)
            for i in range(ANTAL_KANALAR):
                if i not in aktive:
                    continue
                if self._glidande_rms[i] <= 0:
                    continue
                kanal_rms = float(np.sqrt(np.mean(siste_data[:, i] ** 2)))
                terskel = self._glidande_rms[i] * (self._konfig.rms_terskel_prosent / 100.0)
                if kanal_rms > terskel:
                    self._lagre_hending(
                        "rms_terskel",
                        f"Kanal {i}: RMS {kanal_rms:.4f} > terskel {terskel:.4f} "
                        f"({self._konfig.rms_terskel_prosent}% av snitt)"
                    )
                    return  # Berre éi hending per sjekk-runde

        # 2. dV/dt: rate-of-change over siste 1ms
        dvdt_n = max(int(sr * 0.001), 2)  # 1ms
        if dvdt_n > 0 and self._ring.tilgjengeleg >= dvdt_n * 2:
            siste_data = self._ring.hent_siste(dvdt_n * 2)
            for i in range(ANTAL_KANALAR):
                if i not in aktive:
                    continue
                diff = np.diff(siste_data[-dvdt_n:, i])
                if len(diff) > 0:
                    max_dvdt = float(np.max(np.abs(diff))) * (sr / 1000.0)  # V/ms
                    if max_dvdt > self._konfig.dvdt_terskel:
                        self._lagre_hending(
                            "dvdt",
                            f"Kanal {i}: dV/dt {max_dvdt:.4f} V/ms > "
                            f"terskel {self._konfig.dvdt_terskel} V/ms"
                        )
                        return

        # 3. MQTT-endring
        if self._mqtt_endring_flagg:
            self._mqtt_endring_flagg = False
            self._lagre_hending(
                "mqtt_endring",
                f"MQTT-verdi endra meir enn {self._konfig.mqtt_endring_terskel}"
            )

    def _lagre_hending(self, type_str: str, skildring: str):
        """Lagre ei hending med rå waveform frå RAM ringbuffer."""
        if self._db is None:
            return

        sr = self._konfig.sample_rate
        pre_samples = int(sr * self._konfig.pre_trigger_ms / 1000.0)
        post_samples = int(sr * self._konfig.post_trigger_ms / 1000.0)
        totalt_samples = pre_samples + post_samples
        varigheit_ms = self._konfig.pre_trigger_ms + self._konfig.post_trigger_ms

        # Hent data frå RAM ringbuffer (pre_trigger er i fortida)
        data = self._ring.hent_siste(min(totalt_samples, self._ring.tilgjengeleg))

        # Komprimer med zlib
        raa_bytes = data.tobytes()
        komprimert = zlib.compress(raa_bytes, level=6)

        tidsstempel = int(time.time() * 1000)

        try:
            self._db.execute(
                """INSERT INTO hendingar
                   (tidsstempel_ms, type, skildring, varigheit_ms,
                    kanalar, sample_rate, raa_data)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (tidsstempel, type_str, skildring, varigheit_ms,
                 ANTAL_KANALAR, sr, komprimert)
            )
            self._db.commit()
            log.info(f"Hending lagra: {type_str} — {skildring} "
                     f"({len(komprimert)} bytes komprimert, "
                     f"{len(raa_bytes)} bytes rå)")
        except Exception as e:
            log.error(f"Kunne ikkje lagre hending: {e}")

    # --- Eksisterande API ---

    def hent_usynkronisert(self, limit: int = 10000, etter_id: int = 0) -> list:
        """Hent usynkroniserte rader for hub-synkronisering."""
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

    def tom_alt(self) -> dict:
        """Tøm heile målebufferen: lukk DB, slett filene og opprett på nytt.

        Brukt av GUI-knappen «Tøm buffer». Fil-sletting er minne-trygt for
        ALLE storleikar — i motsetnad til VACUUM, som byggjer om heile fila
        og kan sprenge minnet (container med 1 GB grense krasja på 13 GB
        buffer). Frigjer all diskplass momentant.
        """
        if self._db is None:
            return {"suksess": False, "melding": "Buffer ikkje aktiv"}
        try:
            with self._lock:
                sti = self._db_sti
                try:
                    self._db.close()
                except Exception:
                    pass
                self._db = None
                if sti is not None:
                    for suf in ("", "-wal", "-shm"):
                        try:
                            Path(str(sti) + suf).unlink()
                        except FileNotFoundError:
                            pass
                        except Exception as e:
                            log.warning(f"Kunne ikkje slette {sti}{suf}: {e}")
                # Opprett tom DB på nytt (set self._db + self._db_sti)
                self._init_db()
            log.info("Buffer tømt (DB-filer sletta + oppretta på nytt)")
            return {"suksess": True, "melding": "Buffer tømt"}
        except Exception as e:
            log.error(f"Feil ved tømming av buffer: {e}")
            try:
                if self._db is None:
                    self._init_db()  # ikkje la noden stå utan buffer-DB
            except Exception:
                pass
            return {"suksess": False, "melding": str(e)}

    # --- Nye API-funksjonar ---

    def hent_hendingar(self, limit: int = 50, etter_id: int = 0) -> list:
        """Hent hendingar for web API og hub-sync."""
        if self._db is None:
            return []
        try:
            cur = self._db.execute(
                """SELECT id, tidsstempel_ms, type, skildring,
                          varigheit_ms, sample_rate
                   FROM hendingar
                   WHERE id > ?
                   ORDER BY id DESC
                   LIMIT ?""",
                (etter_id, limit)
            )
            return [
                {
                    "id": row[0],
                    "tidsstempel_ms": row[1],
                    "type": row[2],
                    "skildring": row[3],
                    "varigheit_ms": row[4],
                    "sample_rate": row[5],
                }
                for row in cur
            ]
        except Exception as e:
            log.error(f"Feil ved henting av hendingar: {e}")
            return []

    def hent_hendingar_for_sync(self, limit: int = 100, etter_id: int = 0) -> list:
        """Hent usynkroniserte hendingar med rå-data (for hub-sync)."""
        if self._db is None:
            return []
        try:
            cur = self._db.execute(
                """SELECT id, tidsstempel_ms, type, skildring,
                          varigheit_ms, kanalar, sample_rate, raa_data
                   FROM hendingar
                   WHERE synkronisert = 0 AND id > ?
                   ORDER BY id ASC
                   LIMIT ?""",
                (etter_id, limit)
            )
            import base64
            rader = []
            for row in cur:
                rad = {
                    "id": row[0],
                    "ts": row[1],
                    "type": row[2],
                    "skildring": row[3],
                    "varigheit_ms": row[4],
                    "kanalar": row[5],
                    "sample_rate": row[6],
                }
                if row[7]:
                    rad["raa_data_b64"] = base64.b64encode(row[7]).decode('ascii')
                rader.append(rad)
            return rader
        except Exception as e:
            log.error(f"Feil ved henting av hendingar for sync: {e}")
            return []

    def marker_hendingar_synkronisert(self, opp_til_id: int) -> bool:
        """Marker hendingar som synkroniserte."""
        if self._db is None:
            return False
        try:
            self._db.execute(
                "UPDATE hendingar SET synkronisert = 1 WHERE id <= ? AND synkronisert = 0",
                (opp_til_id,)
            )
            self._db.commit()
            return True
        except Exception as e:
            log.error(f"Feil ved hendingar synk-markering: {e}")
            return False

    def hent_mqtt_logg(self, limit: int = 100, etter_tid: int = 0) -> list:
        """Hent MQTT-logg rader for web API."""
        if self._db is None:
            return []
        try:
            cur = self._db.execute(
                """SELECT id, tidsstempel_ms, topic, verdi
                   FROM mqtt_logg
                   WHERE tidsstempel_ms > ?
                   ORDER BY id DESC
                   LIMIT ?""",
                (etter_tid, limit)
            )
            return [
                {
                    "id": row[0],
                    "tidsstempel_ms": row[1],
                    "topic": row[2],
                    "verdi": row[3],
                }
                for row in cur
            ]
        except Exception as e:
            log.error(f"Feil ved henting av MQTT-logg: {e}")
            return []

    def hent_mqtt_logg_for_sync(self, limit: int = 10000, etter_id: int = 0) -> list:
        """Hent usynkroniserte MQTT-logg rader for hub-sync."""
        if self._db is None:
            return []
        try:
            cur = self._db.execute(
                """SELECT id, tidsstempel_ms, topic, verdi
                   FROM mqtt_logg
                   WHERE synkronisert = 0 AND id > ?
                   ORDER BY id ASC
                   LIMIT ?""",
                (etter_id, limit)
            )
            return [
                {"id": row[0], "ts": row[1], "topic": row[2], "verdi": row[3]}
                for row in cur
            ]
        except Exception as e:
            log.error(f"Feil ved henting av MQTT-logg for sync: {e}")
            return []

    def marker_mqtt_logg_synkronisert(self, opp_til_id: int) -> bool:
        """Marker MQTT-logg rader som synkroniserte."""
        if self._db is None:
            return False
        try:
            self._db.execute(
                "UPDATE mqtt_logg SET synkronisert = 1 WHERE id <= ? AND synkronisert = 0",
                (opp_til_id,)
            )
            self._db.commit()
            return True
        except Exception as e:
            log.error(f"Feil ved MQTT-logg synk-markering: {e}")
            return False

    def hent_lagringsinfo(self) -> dict:
        """Hent lagringsinfo: SSD/SD, ledig plass, faktisk sti."""
        sti = self._db_sti or _finn_lagringssti(self._konfig)
        ssd_aktiv = "/ssd/" in str(sti)

        ledig_mb = 0.0
        brukt_mb = 0.0
        try:
            usage = shutil.disk_usage(str(sti.parent))
            ledig_mb = round(usage.free / (1024 * 1024), 1)
            brukt_mb = round(usage.used / (1024 * 1024), 1)
        except Exception:
            pass

        return {
            "sti": str(sti),
            "ssd_aktiv": ssd_aktiv,
            "ledig_mb": ledig_mb,
            "brukt_mb": brukt_mb,
        }

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
            if self._db_sti and self._db_sti.exists():
                storleik_mb = round(
                    self._db_sti.stat().st_size / (1024 * 1024), 2
                )

            # Hendingar og MQTT-logg tal
            hendingar_totalt = self._db.execute(
                "SELECT COUNT(*) FROM hendingar"
            ).fetchone()[0]

            mqtt_logg_rader = self._db.execute(
                "SELECT COUNT(*) FROM mqtt_logg"
            ).fetchone()[0]

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
                "lagringssti": str(self._db_sti) if self._db_sti else "",
                "ssd_aktiv": "/ssd/" in str(self._db_sti) if self._db_sti else False,
                "ram_buffer_storleik_mb": round(
                    self._ring.kapasitet * ANTAL_KANALAR * 8 / 1e6, 1
                ),
                "hendingar_totalt": hendingar_totalt,
                "mqtt_logg_rader": mqtt_logg_rader,
                "sample_rate": self._konfig.sample_rate,
            }
        except Exception as e:
            log.error(f"Feil ved henting av buffer-status: {e}")
            return {
                "aktivert": self._konfig.aktivert,
                "totalt_rader": 0,
                "feil": str(e),
            }

    # --- Opprydding ---

    def _start_opprydding(self):
        """Start bakgrunns-tråd for opprydding."""
        self._opprydding_traad = threading.Thread(
            target=self._opprydding_loop, daemon=True
        )
        self._opprydding_traad.start()

    def _opprydding_loop(self):
        """Sjekk DB-storleik kvart 60s og slett eldste synkroniserte rader."""
        while not self._stopp.is_set():
            self._stopp.wait(timeout=60)
            if self._stopp.is_set():
                break
            self._opprydding()

    def _opprydding(self):
        """Slett eldste synkroniserte rader om DB > 90% av maks.
        Ryddar også mqtt_logg og hendingar."""
        if self._db is None:
            return

        try:
            if not self._db_sti or not self._db_sti.exists():
                return

            storleik_mb = self._db_sti.stat().st_size / (1024 * 1024)
            grense = self._konfig.maks_storleik_mb * 0.9

            if storleik_mb < grense:
                return

            log.info(f"Buffer-opprydding: {storleik_mb:.1f}MB / "
                     f"{self._konfig.maks_storleik_mb}MB (grense {grense:.0f}MB)")

            if self._konfig.bevar_usynkronisert:
                self._db.execute(
                    """DELETE FROM maaledata WHERE id IN (
                        SELECT id FROM maaledata
                        WHERE synkronisert = 1
                        ORDER BY id ASC
                        LIMIT 50000
                    )"""
                )
                # Rydd synkroniserte hendingar
                self._db.execute(
                    """DELETE FROM hendingar WHERE id IN (
                        SELECT id FROM hendingar
                        WHERE synkronisert = 1
                        ORDER BY id ASC
                        LIMIT 1000
                    )"""
                )
                # Rydd synkroniserte MQTT-logg
                self._db.execute(
                    """DELETE FROM mqtt_logg WHERE id IN (
                        SELECT id FROM mqtt_logg
                        WHERE synkronisert = 1
                        ORDER BY id ASC
                        LIMIT 50000
                    )"""
                )
            else:
                self._db.execute(
                    """DELETE FROM maaledata WHERE id IN (
                        SELECT id FROM maaledata
                        ORDER BY id ASC
                        LIMIT 50000
                    )"""
                )
                self._db.execute(
                    """DELETE FROM hendingar WHERE id IN (
                        SELECT id FROM hendingar
                        ORDER BY id ASC
                        LIMIT 1000
                    )"""
                )
                self._db.execute(
                    """DELETE FROM mqtt_logg WHERE id IN (
                        SELECT id FROM mqtt_logg
                        ORDER BY id ASC
                        LIMIT 50000
                    )"""
                )

            self._db.commit()
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
                (ny_konfig.intervall_ms / 1000.0) * ny_konfig.sample_rate
            )

            # Oppdater RAM ringbuffer viss sample_rate eller buffer-sekund endra
            ny_kapasitet = ny_konfig.sample_rate * ny_konfig.ram_buffer_sekund
            if ny_kapasitet != self._ring.kapasitet:
                self._ring = RingBuffer(ny_kapasitet, ANTAL_KANALAR)
                log.info(f"RAM ringbuffer re-allokert: {ny_kapasitet} samples "
                         f"({ny_kapasitet * ANTAL_KANALAR * 8 / 1e6:.1f} MB)")

            # Start/stopp DB viss aktivert-status endra
            if ny_konfig.aktivert and not var_aktivert and self._db is None:
                self._init_db()
                self._start_opprydding()
                if ny_konfig.hendingar_aktivert:
                    self._start_hendingsdeteksjon()
            elif not ny_konfig.aktivert and var_aktivert:
                log.info("Buffer deaktivert")

    def stopp(self):
        """Stopp buffer-skrivar og lukk database."""
        self._stopp.set()
        if self._opprydding_traad:
            self._opprydding_traad.join(timeout=5)
        if self._hendingsdeteksjon_traad:
            self._hendingsdeteksjon_traad.join(timeout=5)
        if self._db:
            try:
                self._db.close()
            except Exception:
                pass
            self._db = None
        log.info("Buffer-skrivar stoppa")

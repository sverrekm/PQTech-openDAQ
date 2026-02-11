#!/usr/bin/env python3
"""
SIRIUS Autonom Server (Lag 3)
===============================
Erstatter opendaq_server.py for direkte SIRIUS-bruk uten openDAQ SDK.

Kommuniserer direkte med SIRIUS over USB via den reverse-engineered
protokollen (0xAD/0xB1/0xAE).

Bruk:
  python3 sirius_server.py
  python3 sirius_server.py --maale-intervall 60 --maale-varighet 5
  python3 sirius_server.py --sample-rate 2000
"""

import sys
import os
import json
import csv
import time
import signal
import logging
import argparse
import threading
from datetime import datetime
from pathlib import Path

import numpy as np

from sirius_driver import SiriusDriver, SiriusFeil, SiriusIkkeFunnet

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger('sirius_server')


# --- Logg-ringbuffer for fjern-tilgang via web API ---

class LoggRingBuffer(logging.Handler):
    """Lagrar dei siste N logg-linjene i minnet for web-API."""

    def __init__(self, kapasitet=500):
        super().__init__()
        self._linjer = []
        self._kapasitet = kapasitet
        self._lock = threading.Lock()

    def emit(self, record):
        linje = self.format(record)
        with self._lock:
            self._linjer.append(linje)
            if len(self._linjer) > self._kapasitet:
                self._linjer = self._linjer[-self._kapasitet:]

    def hent_linjer(self, antall=200):
        with self._lock:
            return list(self._linjer[-antall:])


_logg_buffer = LoggRingBuffer(kapasitet=500)
_logg_buffer.setFormatter(logging.Formatter(
    '%(asctime)s [%(name)s/%(levelname)s] %(message)s', datefmt='%H:%M:%S'
))
logging.getLogger().addHandler(_logg_buffer)


# Global status delt med web UI (samme moenster som opendaq_server.py)
server_status = {
    "kjorer": False,
    "enhet_navn": "",
    "serienummer": "",
    "tilkobling": "USB direkte",
    "kanaler": [],
    "slot_info": [],
    "startet": None,
    "feil": None,
    "siste_maaling": None,
    "antall_maalinger": 0,
    "autonom": False,
    "tilkoblet": False,
    "streamer": False,
    "sample_rate": 0,
    "data_rate_kbs": 0.0,
}

# Globale referanser for styring fra web UI
_driver: SiriusDriver = None
_maaler = None
_args = None
_lock = threading.Lock()


class SiriusAutonomMaaler:
    """
    Maaler autonomt i bakgrunnen og lagrer data lokalt.
    Basert paa AutonomMaaler i opendaq_server.py, tilpasset for direkte SIRIUS USB.
    """

    def __init__(self, driver, utmappe, intervall, varighet, sample_rate, prefiks):
        self.driver = driver
        self.utmappe = Path(utmappe)
        self.utmappe.mkdir(parents=True, exist_ok=True)
        self.intervall = intervall
        self.varighet = varighet
        self.sample_rate = sample_rate
        self.prefiks = prefiks
        self._stopp = threading.Event()
        self._traad = None

    def start(self):
        """Start autonom maaling i bakgrunnstraad."""
        if self.intervall <= 0:
            log.info("Autonom maaling deaktivert (intervall=0)")
            return
        self._traad = threading.Thread(target=self._maal_loop, daemon=True)
        self._traad.start()
        server_status["autonom"] = True
        log.info(
            f"Autonom maaling startet: hvert {self.intervall}s, "
            f"varighet {self.varighet}s"
        )

    def stopp(self):
        """Stopp autonom maaling."""
        self._stopp.set()
        if self._traad:
            self._traad.join(timeout=10)
        server_status["autonom"] = False

    def _maal_loop(self):
        """Hovedloekke for autonom maaling."""
        while not self._stopp.is_set():
            try:
                self._gjor_maaling()
            except Exception as e:
                log.error(f"Feil under maaling: {e}")

            # Vent til neste maaling
            self._stopp.wait(timeout=self.intervall)

    def _gjor_maaling(self):
        """Utfoer en maaling: start streaming, samle data, stopp, lagre."""
        maaling_nr = server_status['antall_maalinger'] + 1
        log.info(f"--- Autonom maaling #{maaling_nr} ---")

        if not self.driver.er_tilkoblet():
            log.warning("Enhet ikke tilkoblet - proever rekobling")
            if not self.driver.rekoble():
                log.error("Rekobling feilet - hopper over maaling")
                return

        # Samle data via streaming
        samlet_data = {}
        data_lock = threading.Lock()

        def _samle_callback(kanal_data):
            with data_lock:
                for k, v in kanal_data.items():
                    if k not in samlet_data:
                        samlet_data[k] = []
                    samlet_data[k].append(v)

        # Start streaming, samle i X sekunder, stopp
        try:
            self.driver.start_streaming(callback=_samle_callback)
            self._stopp.wait(timeout=self.varighet)
            self.driver.stopp_streaming()
        except SiriusFeil as e:
            log.error(f"Streaming-feil: {e}")
            try:
                self.driver.stopp_streaming()
            except Exception:
                pass
            return

        # Konsolider data
        if not samlet_data:
            log.warning("Ingen data samlet")
            return

        kanal_arrays = {}
        for k, blokker in samlet_data.items():
            if blokker:
                kanal_arrays[k] = np.concatenate(blokker)

        if not kanal_arrays:
            log.warning("Ingen gyldige kanal-data")
            return

        # Statistikk
        antall_samples = max(len(v) for v in kanal_arrays.values())
        log.info(f"Samlet {antall_samples} samples over {len(kanal_arrays)} kanaler")

        for k, data in kanal_arrays.items():
            if len(data) > 0:
                rms = float(np.sqrt(np.mean(data.astype(float) ** 2)))
                log.info(
                    f"  {k}: RMS={rms:.1f}, "
                    f"min={int(np.min(data))}, "
                    f"maks={int(np.max(data))}"
                )

        # Lagre filer
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        self._lagre_csv(kanal_arrays, ts)
        self._lagre_npz(kanal_arrays, ts)
        self._lagre_metadata(kanal_arrays, ts)

        server_status["antall_maalinger"] += 1
        server_status["siste_maaling"] = datetime.now().isoformat()

    def _lagre_csv(self, kanal_arrays, ts):
        """Lagre maaling til CSV med tidsstempel-kolonne + en kolonne per kanal."""
        filnavn = self.utmappe / f"{self.prefiks}_{ts}.csv"
        dt = 1.0 / self.sample_rate

        # Finn maks lengde
        maks_len = max(len(v) for v in kanal_arrays.values())
        kanaler = sorted(kanal_arrays.keys())

        with open(filnavn, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(
                [f"# SIRIUS autonom maaling - {datetime.now().isoformat()}"]
            )
            writer.writerow(["Tid (s)"] + kanaler)
            for i in range(maks_len):
                rad = [f"{i * dt:.6f}"]
                for k in kanaler:
                    data = kanal_arrays[k]
                    rad.append(str(int(data[i])) if i < len(data) else "")
                writer.writerow(rad)

        log.info(f"  CSV: {filnavn}")

    def _lagre_npz(self, kanal_arrays, ts):
        """Lagre maaling til NPZ med per-kanal arrays."""
        filnavn = self.utmappe / f"{self.prefiks}_{ts}.npz"
        dt = 1.0 / self.sample_rate

        lagre_data = {}
        for k, data in kanal_arrays.items():
            safe_k = k.replace(" ", "_").replace("/", "_")
            lagre_data[safe_k] = data
            lagre_data[f"{safe_k}_tid"] = np.arange(len(data)) * dt

        np.savez_compressed(filnavn, **lagre_data)
        log.info(f"  NPZ: {filnavn}")

    def _lagre_metadata(self, kanal_arrays, ts):
        """Lagre metadata-JSON."""
        filnavn = self.utmappe / f"{self.prefiks}_{ts}_metadata.json"

        statistikk = {}
        for k, data in kanal_arrays.items():
            fdata = data.astype(float)
            valid = fdata[~np.isnan(fdata)] if len(fdata) > 0 else fdata
            if len(valid) > 0:
                statistikk[k] = {
                    "antall": len(valid),
                    "rms": float(np.sqrt(np.mean(valid ** 2))),
                    "snitt": float(np.mean(valid)),
                    "std": float(np.std(valid)),
                    "min": float(np.min(valid)),
                    "maks": float(np.max(valid)),
                }

        meta = {
            "tidspunkt": datetime.now().isoformat(),
            "enhet": server_status["enhet_navn"],
            "serienummer": server_status["serienummer"],
            "tilkobling": "USB direkte",
            "varighet_sekunder": self.varighet,
            "sample_rate": self.sample_rate,
            "antall_kanaler": len(kanal_arrays),
            "kanaler": list(kanal_arrays.keys()),
            "statistikk": statistikk,
        }
        with open(filnavn, 'w', encoding='utf-8') as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)
        log.info(f"  Metadata: {filnavn}")


def hent_logg(antall=200):
    """Hent dei siste N logg-linjene for web API."""
    return _logg_buffer.hent_linjer(antall)


def send_debug_kommando(hex_kommando, poll=False):
    """
    Send ein raa USB-kommando og returner svar som hex.

    Args:
        hex_kommando: Kommando som hex-streng (f.eks. "AE1F0C")
        poll: Viss True, bruk AD+B1 poll-mekanismen

    Returns:
        dict med sendt, svar, lengde, feil
    """
    with _lock:
        if _driver is None or not _driver.er_tilkoblet():
            return {"feil": "Ikkje tilkobla"}
        proto = _driver._proto
        if proto is None:
            return {"feil": "Protokoll ikkje klar"}

    try:
        kommando_bytes = bytes.fromhex(hex_kommando)
    except ValueError as e:
        return {"feil": f"Ugyldig hex: {e}"}

    resultat = {"sendt": hex_kommando.lower(), "lengde_sendt": len(kommando_bytes)}

    try:
        if poll and len(kommando_bytes) >= 15 and kommando_bytes[0] == 0xAD:
            # AD+B1 poll: parse op, slot, reg fraa kommandoen
            op = kommando_bytes[6]
            slot = kommando_bytes[10]
            reg = kommando_bytes[11]
            data = kommando_bytes[12:15] if len(kommando_bytes) >= 15 else None
            svar = proto.send_ad_og_poll(op, slot, reg, data, maks_forsok=5)
            resultat["modus"] = "ad_poll"
        else:
            # Enkel send+les
            svar = proto.send_raa_kommando(kommando_bytes, timeout=2000)
            resultat["modus"] = "enkel"

        resultat["svar"] = svar.hex()
        resultat["svar_ascii"] = ''.join(
            chr(b) if 0x20 <= b <= 0x7E else '.' for b in svar
        )
        resultat["lengde_svar"] = len(svar)
        resultat["all_ff"] = all(b == 0xFF for b in svar)
        resultat["all_00"] = all(b == 0x00 for b in svar)

    except Exception as e:
        resultat["feil"] = str(e)

    return resultat


def frigjor_usb():
    """Frigjor SIRIUS USB-enheten slik at USB/IP kan bruke den.

    Stoppar streaming og koplar fraa driveren fullstendig.
    Returns:
        (suksess, melding)
    """
    global _driver, _maaler
    with _lock:
        if _driver is None:
            return True, "Driver ikkje aktiv"

        try:
            # Stopp autonom maaling
            if _maaler is not None:
                try:
                    _maaler.stopp()
                except Exception:
                    pass
                _maaler = None

            # Koble fraa USB
            _driver.koble_fra()
            _driver = None

            server_status.update({
                "tilkoblet": False,
                "streamer": False,
                "feil": None,
                "enhet_navn": "",
                "serienummer": "",
                "kanaler": [],
                "slot_info": [],
            })

            log.info("USB frigjort for USB/IP-deling")
            return True, "USB frigjort - klar for USB/IP"

        except Exception as e:
            log.error(f"Feil ved frigjering av USB: {e}")
            return False, str(e)


def hent_driver_status():
    """Hent driver-status for web API."""
    with _lock:
        if _driver is not None:
            drv_status = _driver.hent_status()
            server_status.update({
                "tilkoblet": drv_status.get("tilkoblet", False),
                "streamer": drv_status.get("streamer", False),
                "data_rate_kbs": drv_status.get("data_rate_kbs", 0.0),
                "slot_info": drv_status.get("slotter", []),
                "serienummer": drv_status.get("serienummer", ""),
                "enhet_navn": drv_status.get("enhetsstreng", "") or server_status.get("enhet_navn", ""),
            })
        else:
            server_status["tilkoblet"] = False
        return dict(server_status)


def hent_enhetsinfo():
    """Hent detaljert enhetsinfo for web API."""
    with _lock:
        if _driver is not None:
            return _driver.hent_status()
        return {}


def start_driver_streaming(sample_rate=None, kanaler=None):
    """Start streaming fra web API."""
    global _driver
    with _lock:
        if _driver is None:
            return False, "Driver ikke initialisert - klikk Rekoble foerst"
        if not _driver.er_tilkoblet():
            return False, "Ikke tilkoblet - klikk Rekoble foerst"
        if _driver.streamer:
            return False, "Streaming kjorer allerede"
        try:
            _driver.start_streaming()
            server_status["streamer"] = True
            return True, "Streaming startet"
        except SiriusFeil as e:
            return False, str(e)


def stopp_driver_streaming():
    """Stopp streaming fra web API."""
    global _driver
    with _lock:
        if _driver is None:
            return False, "Driver ikke initialisert"
        if not _driver.streamer:
            return False, "Streaming kjorer ikke"
        try:
            _driver.stopp_streaming()
            server_status["streamer"] = False
            return True, "Streaming stoppet"
        except SiriusFeil as e:
            return False, str(e)


def hent_siste_data():
    """Hent siste data-snapshot fra web API."""
    with _lock:
        if _driver is not None:
            data = _driver.siste_data
            resultat = {}
            for k, v in data.items():
                if hasattr(v, 'tolist'):
                    verdier = v.tolist()
                    resultat[k] = {
                        "verdier": verdier[-100:],  # Siste 100 samples
                        "antall": len(verdier),
                        "siste": verdier[-1] if verdier else None,
                    }
                else:
                    resultat[k] = {"verdier": [], "antall": 0, "siste": None}
            return resultat
        return {}


def rekoble_driver():
    """Proev rekobling fra web API. Oppretter driver hvis den ikke finnes."""
    global _driver
    with _lock:
        try:
            if _driver is None:
                log.info("Oppretter ny SiriusDriver for rekobling...")
                _driver = SiriusDriver()

            ok = _driver.rekoble()
            if ok:
                info = _driver.hent_status()
                server_status.update({
                    "kjorer": True,
                    "tilkoblet": True,
                    "enhet_navn": info.get("enhetsstreng", ""),
                    "serienummer": info.get("serienummer", ""),
                    "slot_info": info.get("slotter", []),
                    "kanaler": [
                        f"Kanal {s['kanal']}" for s in info.get("slotter", []) if s.get("aktiv")
                    ],
                    "feil": None,
                })
                return True, "Rekoblet"
            else:
                server_status["feil"] = "Rekobling feilet"
                return False, "Rekobling feilet"
        except SiriusFeil as e:
            server_status["feil"] = str(e)
            return False, str(e)


def start_server(args):
    """Start SIRIUS server med direkte USB-tilkobling."""
    global server_status, _driver, _maaler, _args

    log.info("=" * 60)
    log.info("  SIRIUS Server - Direkte USB")
    log.info("=" * 60)

    _args = args

    # Opprett og koble til driver
    _driver = SiriusDriver()
    enhet_tilkoblet = False

    try:
        _driver.koble_til()
        enhet_tilkoblet = True
    except SiriusIkkeFunnet:
        server_status["feil"] = "SIRIUS ikke funnet paa USB"
        log.error("SIRIUS ikke funnet paa USB!")
        log.error("Sjekk at enheten er koblet til og at USB-tilgang er gitt")
        log.error("Web UI starter likevel - bruk Rekoble-knappen naar enheten er klar")
    except SiriusFeil as e:
        server_status["feil"] = str(e)
        log.error(f"Tilkoblingsfeil: {e}")
        log.error("Web UI starter likevel - bruk Rekoble-knappen naar enheten er klar")

    # Oppdater global status
    if enhet_tilkoblet:
        info = _driver.hent_status()
        kanaler = [
            f"Kanal {s['kanal']}" for s in info.get('slotter', []) if s.get('aktiv')
        ]
        server_status.update({
            "kjorer": True,
            "tilkoblet": True,
            "enhet_navn": info.get("enhetsstreng", "SIRIUS"),
            "serienummer": info.get("serienummer", ""),
            "tilkobling": "USB direkte",
            "kanaler": kanaler,
            "slot_info": info.get("slotter", []),
            "startet": datetime.now().isoformat(),
            "feil": None,
            "sample_rate": args.sample_rate,
        })
    else:
        server_status.update({
            "kjorer": True,
            "tilkoblet": False,
            "tilkobling": "USB direkte",
            "startet": datetime.now().isoformat(),
            "sample_rate": args.sample_rate,
        })

    log.info("")
    log.info(f"  Enhet:       {server_status['enhet_navn'] or '(ikke tilkoblet)'}")
    log.info(f"  Serienr:     {server_status['serienummer'] or '-'}")
    log.info(f"  Kanaler:     {len(server_status['kanaler'])}")
    log.info(f"  Sample rate: {args.sample_rate} Hz")
    if args.maale_intervall > 0:
        log.info(f"  Maaling:     hvert {args.maale_intervall}s, varighet {args.maale_varighet}s")
        log.info(f"  Utmappe:     {args.utmappe}")
    log.info("=" * 60)
    log.info("")

    # Start web-grensesnitt i bakgrunnstraad (starter ALLTID, ogsaa uten enhet)
    def _start_web():
        from web_ui import app as flask_app
        web_port = int(os.environ.get("WEB_PORT", 8080))
        log.info(f"Web UI startet paa port {web_port}")
        flask_app.run(host="0.0.0.0", port=web_port, use_reloader=False)

    web_traad = threading.Thread(target=_start_web, daemon=True)
    web_traad.start()

    # Start autonom maaling (kun hvis tilkoblet)
    if enhet_tilkoblet:
        _maaler = SiriusAutonomMaaler(
            driver=_driver,
            utmappe=args.utmappe,
            intervall=args.maale_intervall,
            varighet=args.maale_varighet,
            sample_rate=args.sample_rate,
            prefiks=args.prefiks,
        )
        _maaler.start()
    else:
        log.info("Autonom maaling utsatt til enhet kobles til")

    # Hold serveren kjorende
    stopp = False

    def signal_handler(sig, frame):
        nonlocal stopp
        log.info("Mottok stoppsignal...")
        stopp = True

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    try:
        while not stopp:
            # Oppdater daterate i status
            if _driver and _driver.streamer:
                server_status["data_rate_kbs"] = _driver.data_rate_kbs
            time.sleep(1)
    except KeyboardInterrupt:
        pass

    log.info("Stopper...")
    if _maaler:
        _maaler.stopp()
    if _driver:
        _driver.koble_fra()
    server_status["kjorer"] = False


def main():
    parser = argparse.ArgumentParser(
        description='SIRIUS Server - Direkte USB-kommunikasjon med Dewesoft SIRIUS'
    )
    parser.add_argument(
        '--maale-intervall', type=int, default=60,
        help='Sekunder mellom autonome maalinger (0=deaktivert, standard: 60)'
    )
    parser.add_argument(
        '--maale-varighet', type=float, default=5.0,
        help='Varighet per maaling i sekunder (standard: 5)'
    )
    parser.add_argument(
        '--sample-rate', type=int, default=1000,
        help='Samples per sekund (standard: 1000)'
    )
    parser.add_argument(
        '--utmappe', default='/data/maalinger',
        help='Mappe for lagring av maalinger'
    )
    parser.add_argument(
        '--prefiks', default='sirius_maaling',
        help='Filnavn-prefiks (standard: sirius_maaling)'
    )
    parser.add_argument(
        '--debug', action='store_true',
        help='Vis debug-meldinger'
    )

    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    start_server(args)


if __name__ == "__main__":
    main()

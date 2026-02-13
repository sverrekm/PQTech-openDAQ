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
from opendaq_bro import OpenDAQBro

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


_logg_buffer = LoggRingBuffer(kapasitet=2000)
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
_opendaq_bro: OpenDAQBro = None
_args = None
_lock = threading.Lock()


class SiriusAutonomMaaler:
    """
    Lagrar periodiske snapshots av data frå kontinuerleg streaming.

    VIKTIG: Startar/stoppar IKKJE streaming sjølv. Streaming køyrer
    kontinuerleg frå oppstart. Denne klassen samlar data frå driveren sin
    buffer (hent_data) med jamne mellomrom og lagrar til CSV/NPZ.
    """

    def __init__(self, driver, utmappe, intervall, varighet, sample_rate, prefiks,
                 opendaq_bro=None):
        self.driver = driver
        self.utmappe = Path(utmappe)
        self.utmappe.mkdir(parents=True, exist_ok=True)
        self.intervall = intervall
        self.varighet = varighet
        self.sample_rate = sample_rate
        self.prefiks = prefiks
        self._opendaq_bro = opendaq_bro
        self._stopp = threading.Event()
        self._traad = None

    def start(self):
        """Start autonom snapshot-lagring i bakgrunnstraad."""
        if self.intervall <= 0:
            log.info("Autonom maaling deaktivert (intervall=0)")
            return
        self._traad = threading.Thread(target=self._maal_loop, daemon=True)
        self._traad.start()
        server_status["autonom"] = True
        log.info(
            f"Autonom snapshot-lagring startet: hvert {self.intervall}s, "
            f"samlevindu {self.varighet}s"
        )

    def stopp(self):
        """Stopp autonom snapshot-lagring."""
        self._stopp.set()
        if self._traad:
            self._traad.join(timeout=10)
        server_status["autonom"] = False

    def _maal_loop(self):
        """Hovedloekke for autonom snapshot-lagring."""
        while not self._stopp.is_set():
            try:
                self._gjor_maaling()
            except Exception as e:
                log.error(f"Feil under maaling: {e}")

            # Vent til neste maaling
            self._stopp.wait(timeout=self.intervall)

    def _gjor_maaling(self):
        """Samle data frå kontinuerleg stream og lagre til fil.

        IKKJE start/stopp streaming - det er allereie køyrande.
        Berre les frå driveren sin buffer i eit tidsvindu.
        """
        maaling_nr = server_status['antall_maalinger'] + 1
        log.info(f"--- Autonom snapshot #{maaling_nr} ---")

        if not self.driver.er_tilkoblet():
            log.warning("Enhet ikkje tilkobla - hoppar over")
            return

        if not self.driver.streamer:
            log.warning("Streaming ikkje aktiv - hoppar over")
            return

        # Tøm buffer før vi startar samling
        self.driver.hent_data()

        # Samle data i varighet sekunder
        self._stopp.wait(timeout=self.varighet)

        # Hent alt som er samla opp
        rammer = self.driver.hent_data()

        if not rammer:
            log.warning("Ingen data samla i snapshotvinduet")
            return

        # Konsolider rammer til per-kanal arrays
        kanal_arrays = {}
        for ramme in rammer:
            for k, v in ramme.items():
                if k not in kanal_arrays:
                    kanal_arrays[k] = []
                kanal_arrays[k].append(v)

        # Concatener arrays per kanal
        for k in kanal_arrays:
            kanal_arrays[k] = np.concatenate(kanal_arrays[k])

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
                "ep2_ok": drv_status.get("ep2_ok", False),
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


_opendaq_feil = None


def hent_opendaq_status():
    """Hent openDAQ bridge status for web API."""
    if _opendaq_bro is not None:
        return _opendaq_bro.hent_status()
    return {"tilgjengelig": False, "aktiv": False, "feil": _opendaq_feil or "Ikkje starta"}


def hent_opendaq_verdiar():
    """Hent siste kanal-verdiar frå openDAQ bridge for live-visning."""
    if _opendaq_bro is not None:
        return _opendaq_bro.hent_siste_verdiar()
    return {}


def restart_opendaq_bro():
    """Manuell restart av openDAQ bridge (for debugging/retry)."""
    global _opendaq_bro, _opendaq_feil
    if _opendaq_bro is not None:
        try:
            _opendaq_bro.stopp()
        except Exception:
            pass

    try:
        _opendaq_bro = OpenDAQBro()
        ok = _opendaq_bro.start()
        if ok:
            _opendaq_feil = None
            log.info("openDAQ bridge restarta OK")
            return True, "openDAQ bridge starta"
        else:
            _opendaq_feil = _opendaq_bro.hent_status().get("feil", "start() returnerte False")
            log.warning(f"openDAQ bridge restart feila: {_opendaq_feil}")
            return False, _opendaq_feil
    except Exception as e:
        _opendaq_feil = str(e)
        log.error(f"openDAQ bridge restart exception: {e}")
        _opendaq_bro = None
        return False, _opendaq_feil


def _global_data_callback(kanal_data):
    """Global callback for kontinuerleg streaming.

    Matar openDAQ-brua med data slik at DewesoftX ser verdiar.
    Streaming køyrer alltid - aldri stopp/start-syklus.
    """
    if _opendaq_bro and _opendaq_bro.tilgjengelig:
        try:
            _opendaq_bro.oppdater_data(kanal_data)
        except Exception:
            pass


def gjenoppliv_ep2():
    """Forsøk å gjenopplive EP2 via kommando-strategiar (web UI-knapp)."""
    global _driver
    with _lock:
        if _driver is None:
            return False, "Driver ikkje initialisert - klikk Rekoble"
        if not _driver.er_tilkoblet():
            return False, "Ikkje tilkobla - klikk Rekoble fyrst"
        if _driver.ep2_ok:
            return True, "EP2 fungerer allereie"

        try:
            ok = _driver.forsok_gjenoppliv_ep2()
            if ok:
                server_status["feil"] = None
                # Start streaming automatisk etter vellukka gjenoppliving
                if not _driver.streamer:
                    try:
                        _driver.start_streaming(callback=_global_data_callback)
                        server_status["streamer"] = True
                        log.info("Streaming starta etter EP2 gjenoppliving")
                    except SiriusFeil as e:
                        log.warning(f"Kunne ikkje starte streaming: {e}")
                return True, "EP2 gjenoppliva! Streaming starta."
            else:
                server_status["feil"] = "EP2 gjenoppliving feilet - alle strategiar prøvd"
                return False, "Alle EP2-strategiar feilet. Instrumentet kan trenge full power-cycle."
        except Exception as e:
            return False, f"Feil under gjenoppliving: {e}"


def start_driver_streaming(sample_rate=None, kanaler=None):
    """Start streaming (eller stadfest at det allereie køyrer).

    Streaming startar automatisk ved oppstart. Denne funksjonen
    er for web UI-knappen og startar streaming viss det ikkje køyrer.
    """
    global _driver
    with _lock:
        if _driver is None:
            return False, "Driver ikkje initialisert - klikk Rekoble"
        if not _driver.er_tilkoblet():
            return False, "Ikkje tilkobla - klikk Rekoble"
        if not _driver.ep2_ok:
            return False, "EP2 (ADC) ikkje klar - klikk Gjenoppliv EP2"
        if _driver.streamer:
            return True, "Streaming køyrer allereie"

        try:
            _driver.start_streaming(callback=_global_data_callback)
            server_status["streamer"] = True
            return True, "Streaming starta"
        except SiriusFeil as e:
            return False, str(e)


def stopp_driver_streaming():
    """Stopp streaming frå web API."""
    global _driver
    with _lock:
        if _driver is None:
            return False, "Driver ikkje initialisert"
        if not _driver.streamer:
            return False, "Streaming køyrer ikkje"
        try:
            _driver.stopp_streaming()
            server_status["streamer"] = False
        except SiriusFeil as e:
            return False, str(e)

    return True, "Streaming stoppa"


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
                # Restart kontinuerleg streaming etter rekonnektering
                if _driver.ep2_ok and not _driver.streamer:
                    try:
                        _driver.start_streaming(callback=_global_data_callback)
                        server_status["streamer"] = True
                        log.info("Streaming restarta etter rekobling")
                    except SiriusFeil as e:
                        log.warning(f"Kunne ikkje starte streaming etter rekobling: {e}")
                elif not _driver.ep2_ok:
                    server_status["feil"] = "EP2 timeout - klikk Gjenoppliv EP2"
                    log.warning("EP2 svarte ikkje etter rekobling - prøv Gjenoppliv EP2 i web UI")

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

    # Start openDAQ nettverksservere (referanse-enhet for DewesoftX-tilkobling)
    global _opendaq_bro, _opendaq_feil
    try:
        log.info("Startar openDAQ nettverksbro...")
        _opendaq_bro = OpenDAQBro()
        ok = _opendaq_bro.start()
        if ok:
            log.info("openDAQ bridge starta OK")
        else:
            _opendaq_feil = _opendaq_bro.hent_status().get("feil", "start() returnerte False")
            log.warning(f"openDAQ bridge ikkje tilgjengeleg: {_opendaq_feil}")
    except Exception as e:
        _opendaq_feil = str(e)
        log.error(f"openDAQ bridge exception: {_opendaq_feil}")
        import traceback
        log.error(traceback.format_exc())
        _opendaq_bro = None

    # Start kontinuerleg streaming + autonom snapshot-lagring
    if enhet_tilkoblet and _driver.ep2_ok:
        # Start streaming ÉIN GONG og hald det køyrande.
        # Aldri stopp/start-syklus - det skapar EBUSY ved rekonnektering.
        try:
            _driver.start_streaming(callback=_global_data_callback)
            server_status["streamer"] = True
            log.info("Kontinuerleg streaming starta (EP2 → openDAQ + web UI)")
        except SiriusFeil as e:
            log.warning(f"Kunne ikkje starte streaming: {e}")

        _maaler = SiriusAutonomMaaler(
            driver=_driver,
            utmappe=args.utmappe,
            intervall=args.maale_intervall,
            varighet=args.maale_varighet,
            sample_rate=args.sample_rate,
            prefiks=args.prefiks,
            opendaq_bro=_opendaq_bro,
        )
        _maaler.start()
    elif enhet_tilkoblet and not _driver.ep2_ok:
        log.error("EP2 (ADC) svarte ikkje - streaming IKKJE starta")
        log.error("Bruk 'Gjenoppliv EP2'-knappen i web UI for å prøve kommando-strategiar")
        server_status["feil"] = "EP2 timeout - klikk Gjenoppliv EP2"
    else:
        log.info("Streaming og autonom lagring utsett til enhet er tilkobla")

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
            if _opendaq_bro and _opendaq_bro.tilgjengelig:
                server_status["opendaq_bro"] = _opendaq_bro.hent_status()
            time.sleep(1)
    except KeyboardInterrupt:
        pass

    log.info("Stopper...")
    if _maaler:
        _maaler.stopp()
    if _opendaq_bro:
        _opendaq_bro.stopp()
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

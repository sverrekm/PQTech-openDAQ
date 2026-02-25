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
# Fiks dual-modul-problem: `python3 -m sirius_server` lastar modulen som
# `__main__`, men `web_ui.py` importerer `from sirius_server import ...`
# som skapar ein ANDRE modul med separate globale variablar (_driver,
# _opendaq_bro, server_status osv). Denne linja sikrar at begge brukar
# same modul-instans.
if __name__ == '__main__':
    sys.modules.setdefault('sirius_server', sys.modules[__name__])

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
from mqtt_konfig import les_mqtt_konfig, lagre_mqtt_konfig, valider_mqtt_konfig, MqttKonfig
from mqtt_klient import MqttKlient
import usbip_manager

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
_mqtt_klient: MqttKlient = None
_mqtt_konfig: MqttKonfig = None
_args = None
_lock = threading.Lock()

# Stopp-event frå førre driver — brukast til å stoppe foreldrelause
# ADC/heartbeat-trådar når _driver vert sett til None uforventa.
_siste_stopp_event: threading.Event = None


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

    Stoppar streaming FYRST (utanfor lock for å unngaa deadlock med
    ADC-traaden sin callback), deretter koplar fraa driveren.
    Returns:
        (suksess, melding)
    """
    global _driver, _maaler

    # Stopp streaming UTANFOR lock — ADC-traaden sin callback kan halde _lock
    # via oppdater_data, og stopp_streaming ventar på join().
    try:
        if _driver is not None and _driver.streamer:
            log.info("frigjor_usb: stoppar streaming fyrst...")
            _driver.stopp_streaming()
    except Exception as e:
        log.warning(f"frigjor_usb: stopp_streaming feilet: {e}")

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

            # Koble fraa USB (stopp_streaming allereie gjort ovanfor)
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
                "ep2_strategi": drv_status.get("ep2_strategi", {}),
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


def hent_mqtt_status():
    """Hent MQTT-klient status for web API."""
    if _mqtt_klient is not None:
        return _mqtt_klient.hent_status()
    return {"tilkobla": False, "aktivert": False, "feil": "MQTT ikkje starta"}


def hent_mqtt_konfig_dict():
    """Hent MQTT-konfig som dict for web API."""
    if _mqtt_konfig is not None:
        return _mqtt_konfig.til_dict()
    konfig = les_mqtt_konfig()
    return konfig.til_dict()


def oppdater_mqtt(ny_konfig: MqttKonfig):
    """Oppdater MQTT-konfig, lagre, og restart klienten.

    Krev bridge-restart for å endre openDAQ-kanaltallet.
    """
    global _mqtt_klient, _mqtt_konfig

    lagre_mqtt_konfig(ny_konfig)
    _mqtt_konfig = ny_konfig

    if _mqtt_klient is not None:
        _mqtt_klient.restart(ny_konfig)
        log.info("MQTT-klient restarta med ny konfig")
    elif ny_konfig.broker.aktivert:
        _mqtt_klient = MqttKlient(ny_konfig)
        _mqtt_klient.start()
        log.info("MQTT-klient starta")

    return True, ("MQTT konfig lagra. Restart openDAQ bridge for å oppdatere "
                   "kanaltallet i DewesoftX.")


_port_probe_cache = {"ts": 0.0, "result": {}}


def hent_opendaq_status():
    """Hent openDAQ bridge status for web API.

    Inkluderer live port-verifisering med cache (maks kvar 10. sekund)
    for å unngå at konstante TCP-probar forstyrrar serverane.
    """
    if _opendaq_bro is not None:
        status = _opendaq_bro.hent_status()

        # Live port-verifisering — BERRE OPC-UA (4840).
        # TCP-probe mot port 7420 forstyrrar NativeStreaming-serveren:
        #   - Skapar "Failed to read connect request headers" kvar gong
        #   - Kan blokkere DewesoftX si daq.nd:// tilkopling
        # NativeStreaming-status vert utleidd frå at broen køyrer.
        now = time.time()
        cached_positive = all(_port_probe_cache["result"].values()) if _port_probe_cache["result"] else False
        ttl = 10.0 if cached_positive else 2.0
        if now - _port_probe_cache["ts"] > ttl:
            import socket as _sock
            probe_host = (status.get("ip", "") or os.environ.get("OPENDAQ_IP", "")).strip() or "127.0.0.1"
            port_status = {}
            # Berre probe OPC-UA — NativeStreaming antas oppe viss broen er aktiv
            try:
                s = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
                s.settimeout(0.5)
                s.connect((probe_host, 4840))
                s.close()
                port_status['opcua'] = True
            except (ConnectionRefusedError, OSError):
                port_status['opcua'] = False
            # NativeStreaming: antar same status som broen (unngår TCP-probe)
            port_status['native_streaming'] = status.get("aktiv", False)
            _port_probe_cache.update({"ts": now, "result": port_status})

        port_status = _port_probe_cache["result"]
        if port_status:
            status["port_status"] = port_status
            status["alle_portar_oppe"] = all(port_status.values())
            if any(port_status.values()) and not status.get("aktiv"):
                status["aktiv"] = True
        return status
    return {"tilgjengelig": False, "aktiv": False, "feil": _opendaq_feil or "Ikkje starta"}


def hent_opendaq_verdiar():
    """Hent siste kanal-verdiar frå openDAQ bridge for live-visning."""
    if _opendaq_bro is not None:
        return _opendaq_bro.hent_siste_verdiar()
    return {}


def restart_opendaq_bro():
    """Manuell restart av openDAQ bridge (for debugging/retry).

    Stoppar serverar eksplisitt, ventar på port-frigjering, og startar på nytt.

    VIKTIG: C++ openDAQ server-objekt held TCP-sockets som Python GC ikkje
    kan frigjere paalidelig. Viss portane ikkje vert ledige innan 5 sekund,
    triggar vi os._exit(1) slik at Docker restartar containeren friskt.
    restart: unless-stopped i docker-compose.yml sikrar automatisk omstart.
    """
    global _opendaq_bro, _opendaq_feil
    if _opendaq_bro is not None:
        try:
            _opendaq_bro.stopp()
        except Exception as e:
            log.warning(f"  opendaq_bro.stopp() feilet: {e}")
        # Eksplisitt del for å fjerne siste Python-referanse til C++-objekta.
        # Utan dette kan GC utsette destruktor-kall til neste samling.
        try:
            del _opendaq_bro
        except Exception:
            pass
        _opendaq_bro = None

    # Tving GC fleire gonger for å trigge C++ destruktor-kjeder.
    # openDAQ server-objekt → TCP acceptor → OS socket.
    import gc
    gc.collect()
    gc.collect()
    gc.collect()
    # Kort pause for at OS-kjernen skal frigjere socket-bindingar
    time.sleep(1)

    # Vent til portane faktisk er ledige (maks 5s).
    # Viss C++ destruktorane ikkje køyrde, er portane framleis opptekne
    # og den einaste løysinga er container-restart.
    import socket as _sock
    portar = [4840, 7420]
    alle_ledige = False
    opptekne = []
    for forsok in range(10):
        alle_ledige = True
        opptekne = []
        for port in portar:
            try:
                s = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
                s.setsockopt(_sock.SOL_SOCKET, _sock.SO_REUSEADDR, 1)
                s.bind(("0.0.0.0", port))
                s.close()
            except OSError:
                alle_ledige = False
                opptekne.append(port)
        if alle_ledige:
            log.info(f"  Alle portar ledige etter {forsok * 0.5:.1f}s")
            break
        if forsok % 4 == 0 and forsok > 0:
            log.info(f"  Ventar på portar {opptekne} ({forsok * 0.5:.0f}s)...")
            gc.collect()
        time.sleep(0.5)

    if not alle_ledige:
        # C++ zombie-objekt held portane. GC kan ikkje frigjere dei.
        # Einaste løysing: restart container slik at OS frigjer alt.
        log.warning(f"  Portar {opptekne} opptekne av zombie C++-objekt")
        log.warning("  Restartar container for rein port-frigjering...")
        log.warning("  (Docker restart: unless-stopped startar oss automatisk)")

        # Returner melding til web UI FYRST, deretter exit i bakgrunnstraad.
        def _delayed_exit():
            time.sleep(0.5)  # La Flask sende response
            log.info("=== CONTAINER RESTART (port-frigjering) ===")
            os._exit(1)

        exit_traad = threading.Thread(target=_delayed_exit, daemon=True)
        exit_traad.start()
        return True, "Container restartar for å frigjere portar (10-15s)..."

    try:
        sn = server_status.get("serienummer", "")
        enamn = server_status.get("enhet_navn", "")
        # Les MQTT-konfig på nytt (kan ha endra seg sidan sist)
        _mqtt_konfig_fresh = les_mqtt_konfig()
        mqtt_k = _mqtt_konfig_fresh.kanalar if _mqtt_konfig_fresh.broker.aktivert else []
        _opendaq_bro = OpenDAQBro(serienummer=sn, enhetsnamn=enamn,
                                  mqtt_kanalar=mqtt_k)
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


_callback_feil_teller = 0


def _global_data_callback(kanal_data):
    """Global callback for kontinuerleg streaming.

    Matar openDAQ-brua med data slik at DewesoftX ser verdiar.
    Streaming køyrer alltid - aldri stopp/start-syklus.
    Injiserer MQTT-verdiar i tillegg til SIRIUS ADC-data.
    """
    global _callback_feil_teller
    if _opendaq_bro and _opendaq_bro.tilgjengelig:
        try:
            _opendaq_bro.oppdater_data(kanal_data)
        except Exception as e:
            _callback_feil_teller += 1
            if _callback_feil_teller <= 3 or _callback_feil_teller % 1000 == 0:
                log.warning(f"oppdater_data feil #{_callback_feil_teller}: {e}")

        # Injiser MQTT-verdiar synkronisert med SIRIUS-datablokker
        if _mqtt_klient is not None:
            try:
                mqtt_verdiar = _mqtt_klient.hent_verdiar()
                if mqtt_verdiar:
                    # Bruk same blokkstorleik som SIRIUS-data
                    blokk = 1024
                    for v in kanal_data.values():
                        if v is not None and len(v) > 0:
                            blokk = len(v)
                            break
                    _opendaq_bro.oppdater_mqtt_data(mqtt_verdiar, blokk)
            except Exception as e:
                _callback_feil_teller += 1
                if _callback_feil_teller <= 3 or _callback_feil_teller % 1000 == 0:
                    log.warning(f"oppdater_mqtt_data feil #{_callback_feil_teller}: {e}")


def gjenoppliv_ep2():
    """Forsøk å gjenopplive EP2 via kommando-strategiar (web UI-knapp).

    Stoppar streaming fyrst for å frigjere EP2, deretter prøver recovery.
    """
    global _driver, _siste_stopp_event

    # Guard: USB/IP og lokal driver er gjensidig ekskluderande
    if usbip_manager.usbip_status.get("deling_aktiv"):
        return False, "USB/IP deling er aktiv - stopp deling fyrst"

    with _lock:
        if _driver is None:
            return False, "Driver ikkje initialisert - klikk Rekoble"
        if not _driver.er_tilkoblet():
            return False, "Ikkje tilkobla - klikk Rekoble fyrst"
        if _driver.ep2_ok:
            return True, "EP2 fungerer allereie"

        # Stopp streaming FYRST (frigjer EP2 frå leser-traaden)
        if _driver.streamer:
            log.info("Gjenoppliv EP2: stoppar aktiv streaming fyrst...")
            try:
                _driver.stopp_streaming()
            except Exception as e:
                log.warning(f"stopp_streaming feilet: {e}")
            server_status["streamer"] = False

        try:
            ok = _driver.forsok_gjenoppliv_ep2()
            if ok:
                server_status["feil"] = None
                # Start streaming automatisk etter vellukka gjenoppliving
                if not _driver.streamer:
                    try:
                        _driver.start_streaming(callback=_global_data_callback)
                        _siste_stopp_event = _driver._stopp_event
                        server_status["streamer"] = True
                        log.info("Streaming starta etter EP2 gjenoppliving")
                    except SiriusFeil as e:
                        log.warning(f"Kunne ikkje starte streaming: {e}")
                return True, "EP2 gjenoppliva! Streaming starta."
            else:
                # Strategi 3/4 (dev.reset, uhubctl) øydelegg USB-tilkoplinga.
                # Nullstill _driver slik at rekoble_driver() opprettar heilt
                # ny driver med fersk USB-enumerering i staden for å gjenbruke
                # den korrupte instansen.
                #
                # VIKTIG: Ikkje kall koble_fra() her — den gjer dev.reset()
                # som er meiningslaust (og skadeleg) etter at strategi 3/4
                # allereie har resetta/power-cycla eininga.  Bruk _frigjer_dev()
                # som berre gjer release_interface + dispose_resources.
                log.warning("EP2-gjenoppliving feilet — nullstiller driver for rein rekobling")
                _siste_stopp_event = _driver._stopp_event
                try:
                    _driver._stopp_event.set()  # Signal orphan-trådar
                    _driver._frigjer_dev()
                except Exception:
                    pass
                _driver = None
                server_status.update({
                    "feil": "EP2 gjenoppliving feilet - klikk Rekoble",
                    "tilkoblet": False,
                    "streamer": False,
                })
                return False, "Alle EP2-strategiar feilet. Klikk Rekoble for å prøve på nytt."
        except Exception as e:
            # Uventa feil — same opprydding (utan dev.reset)
            log.error(f"Exception under EP2-gjenoppliving: {e}")
            if _driver is not None:
                _siste_stopp_event = _driver._stopp_event
                try:
                    _driver._stopp_event.set()
                    _driver._frigjer_dev()
                except Exception:
                    pass
            _driver = None
            server_status.update({
                "tilkoblet": False,
                "streamer": False,
            })
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
    """Proev rekobling fra web API.  Inkluderer automatisk EP2-recovery.

    1. Stoppar streaming og orphaned trådar
    2. Rekoble (eller opprett ny driver)
    3. Viss EP2 ikkje fungerer: køyr start-acquisition automatisk
    4. Start streaming
    """
    global _driver, _siste_stopp_event

    # Guard: USB/IP og lokal driver er gjensidig ekskluderande
    if usbip_manager.usbip_status.get("deling_aktiv"):
        return False, "USB/IP deling er aktiv - stopp deling fyrst"

    # Guard: Ikkje drep aktiv, fungerande streaming.
    # Brukaren MÅ eksplisitt stoppe streaming fyrst.
    if _driver is not None and _driver.streamer and _driver.ep2_ok:
        return True, "Streaming er aktiv — stopp streaming fyrst viss du vil rekoble"

    with _lock:
        try:
            if _driver is None:
                # Stopp foreldrelause trådar frå tidlegare driver.
                # Bruk BÅDE instans-event (_siste_stopp_event) og klasse-event
                # (_orphan_kill).  Klasse-eventet fungerer ALLTID, sjølv når
                # instans-eventet gjekk tapt (t.d. _driver sett til None utan
                # at _siste_stopp_event vart lagra).
                if _siste_stopp_event is not None:
                    log.info("Rekoble: set instans-stopp-event for orphan-trådar")
                    _siste_stopp_event.set()
                    _siste_stopp_event = None

                # Vent til eventuelle orphan sirius-trådar er ferdige.
                sirius_threads = [
                    t for t in threading.enumerate()
                    if t.name in ("sirius-adc", "sirius-heartbeat") and t.is_alive()
                ]
                if sirius_threads:
                    # Set klasse-nivå orphan-kill — alle SiriusDriver-løkker
                    # sjekkar dette i tillegg til instansens _stopp_event.
                    SiriusDriver._orphan_kill.set()
                    log.info(f"Rekoble: orphan-kill sett, ventar på {len(sirius_threads)} "
                             f"tråd(ar) ({', '.join(t.name for t in sirius_threads)})...")
                    for t in sirius_threads:
                        t.join(timeout=3)
                    framleis = [t for t in sirius_threads if t.is_alive()]
                    if framleis:
                        # Trådar blokkerer på USB I/O — send USB reset for ENODEV.
                        log.warning(
                            f"Rekoble: {len(framleis)} tråd(ar) lever framleis — "
                            "tvingar avslutning med USB reset..."
                        )
                        try:
                            import usb.core as _usb
                            import usb.util as _usb_util
                            from usb.backend import libusb1 as _libusb1
                            _fresh_be = _libusb1.get_backend()
                            _tmp_dev = _usb.find(
                                idVendor=0x1CED, idProduct=0x1002,
                                backend=_fresh_be,
                            )
                            if _tmp_dev is not None:
                                _tmp_dev.reset()
                                log.info("USB reset sendt — ventar på orphan-trådar...")
                                try:
                                    _usb_util.dispose_resources(_tmp_dev)
                                except Exception:
                                    pass
                                time.sleep(1)
                                for t in framleis:
                                    t.join(timeout=3)
                                framleis2 = [t for t in framleis if t.is_alive()]
                                if framleis2:
                                    log.error(f"Rekoble: {len(framleis2)} tråd(ar) nektar å dø!")
                                else:
                                    log.info("Rekoble: orphan-trådar avslutta etter USB reset")
                            else:
                                log.warning("Rekoble: USB-enhet ikkje funnen for reset")
                        except Exception as e:
                            log.error(f"Rekoble: USB reset feilet: {e}")
                    else:
                        log.info("Rekoble: alle orphan-trådar avslutta")
                    # Nullstill orphan-kill slik at nye trådar ikkje vert drepne.
                    SiriusDriver._orphan_kill.clear()

            if _driver is not None:
                # Stopp streaming eksplisitt FYRST.
                # koble_fra() i driveren brukar dev.reset() for å tvinge
                # orphan-trådar til å avslutte ENODEV (løyser EBUSY).
                if _driver.streamer:
                    log.info("Rekoble: stoppar aktiv streaming fyrst...")
                    try:
                        _driver.stopp_streaming()
                    except Exception as e:
                        log.warning(f"Rekoble: stopp_streaming feilet: {e}")
                    server_status["streamer"] = False

                ok = _driver.rekoble()
            else:
                log.info("Oppretter ny SiriusDriver (driver var None)...")
                _driver = SiriusDriver()
                try:
                    _driver.koble_til()
                    ok = True
                except SiriusFeil as e:
                    log.error(f"Ny tilkobling feilet: {e}")
                    ok = False

            if not ok:
                server_status["feil"] = "Rekobling feilet"
                return False, "Rekobling feilet"

            # Oppdater status
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

            # Oppdater openDAQ bridge med nytt serienummer
            if _opendaq_bro and info.get("serienummer"):
                _opendaq_bro.oppdater_enhetsinfo(
                    serienummer=info.get("serienummer", ""),
                    enhetsnamn=info.get("enhetsstreng", ""),
                )

            # Start streaming (EP2 recovery skjer automatisk i koble_til)
            if _driver.ep2_ok and not _driver.streamer:
                try:
                    _driver.start_streaming(callback=_global_data_callback)
                    _siste_stopp_event = _driver._stopp_event
                    server_status["streamer"] = True
                    log.info("Streaming restarta etter rekobling")
                except SiriusFeil as e:
                    log.warning(f"Kunne ikkje starte streaming etter rekobling: {e}")
            elif not _driver.ep2_ok:
                server_status["feil"] = "EP2 ikkje klar - prøv igjen om litt"
                log.warning("EP2 svarte ikkje etter rekobling")

            return True, "Rekoblet" + (" + streaming" if _driver.streamer else "")
        except SiriusFeil as e:
            server_status["feil"] = str(e)
            return False, str(e)


def start_server(args):
    """Start SIRIUS server med direkte USB-tilkobling."""
    global server_status, _driver, _maaler, _args, _siste_stopp_event

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

    # Last MQTT-konfig og start klient
    global _opendaq_bro, _opendaq_feil, _mqtt_klient, _mqtt_konfig
    _mqtt_konfig = les_mqtt_konfig()
    mqtt_kanalar = _mqtt_konfig.kanalar if _mqtt_konfig.broker.aktivert else []
    if _mqtt_konfig.broker.aktivert and _mqtt_konfig.kanalar:
        _mqtt_klient = MqttKlient(_mqtt_konfig)
        _mqtt_klient.start()
        log.info(f"MQTT-klient starta: {_mqtt_konfig.broker.host}:"
                 f"{_mqtt_konfig.broker.port}, {len(_mqtt_konfig.kanalar)} kanalar")
    else:
        log.info("MQTT deaktivert eller ingen kanalar konfigurert")

    # Start openDAQ nettverksservere (referanse-enhet for DewesoftX-tilkobling)
    try:
        log.info("Startar openDAQ nettverksbro...")
        # Send SIRIUS serienummer og enhetsnamn til openDAQ bridge
        # slik at DewesoftX kan sjaa dei via OPC-UA device info.
        sn = server_status.get("serienummer", "")
        enamn = server_status.get("enhet_namn", "")
        _opendaq_bro = OpenDAQBro(serienummer=sn, enhetsnamn=enamn,
                                  mqtt_kanalar=mqtt_kanalar)
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
            _siste_stopp_event = _driver._stopp_event  # For orphan-cleanup
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
    if _mqtt_klient:
        _mqtt_klient.stopp()
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

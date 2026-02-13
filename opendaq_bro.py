#!/usr/bin/env python3
"""
openDAQ Nettverksbro - DewesoftX-tilkobling via openDAQ-servere
================================================================
Startar openDAQ Instance med referanse-enhet som ROOT og standard
servere (OPC-UA, Native Streaming, WebSocket) slik at DewesoftX kan
koble til via openDAQ-protokollen.

Fase 1: Referanse-enhet (daqref://device0) med simulerte kanalar
Fase 2: Reelle SIRIUS-data injisert via kanal-eigenskapar (Amplitude/Offset)

Bruk:
    from opendaq_bro import OpenDAQBro

    bro = OpenDAQBro()
    bro.start()
    print(bro.hent_status())
    # ...
    bro.stopp()
"""

import os
import socket
import logging
import threading
from datetime import datetime

import numpy as np

from kanal_konfig import les_konfig

log = logging.getLogger('opendaq_bro')

# Grasioes openDAQ-import
_daq = None
_daq_import_feil = None
try:
    import opendaq as _daq
except Exception as e:
    _daq_import_feil = str(e)
    log.warning(f"opendaq Python-bindingar ikkje tilgjengelege: {e}")


class OpenDAQBro:
    """
    openDAQ nettverksbro.

    Opprettar openDAQ Instance med referanse-enhet som ROOT DEVICE
    og startar OPC-UA + Native Streaming + WebSocket servere.
    DewesoftX finn eininga via openDAQ mDNS-oppdaging.
    """

    def __init__(self, module_path=None):
        self._instance = None
        self._device = None
        self._tilgjengelig = False
        self._module_path = module_path or os.environ.get(
            "OPENDAQ_MODULE_PATH", "/usr/local/lib"
        )
        self._lock = threading.Lock()
        self._kanal_signal = []     # Liste av (channel, signal) tupler for data-injeksjon
        self._siste_verdiar = {}    # Siste verdi per kanal for live-visning i web UI
        self._data_teller = 0       # Totalt antal datapunkt motteke
        self._sirius_aktiv = False  # True når reell SIRIUS-data strøymer
        self._sirius_ts = 0.0       # Tidsstempel for siste SIRIUS-data
        self._leser_traad = None
        self._stopp_event = threading.Event()
        self._status = {
            "tilgjengelig": False,
            "aktiv": False,
            "enhet_namn": "",
            "tilkobling": "",
            "kanalar": [],
            "servere": [],
            "ip": "",
            "porter": {
                "opcua": 4840,
                "native_streaming": 7420,
                "websocket": 7414,
            },
            "startet": None,
            "feil": None,
        }

    @property
    def tilgjengelig(self) -> bool:
        return self._tilgjengelig

    def hent_status(self) -> dict:
        """Returner status-dict for web API."""
        with self._lock:
            return dict(self._status)

    def start(self) -> bool:
        """Start openDAQ instance med root device, deretter servere."""
        if _daq is None:
            feil = f"opendaq ikkje tilgjengeleg: {_daq_import_feil or 'import feila'}"
            with self._lock:
                self._status["feil"] = feil
            log.warning(f"openDAQ bridge: {feil}")
            return False

        try:
            log.info("Startar openDAQ nettverksbro...")
            log.info(f"  Modulsti: {self._module_path}")

            # Bruk add_device() i staden for set_root_device().
            # Med set_root_device er referanse-eininga rota, og getDomain()
            # på den kastar C++ exception som krasjar DewesoftX:
            #   "External exception E06D7363 at GetDomain"
            # Med add_device er openDAQ Instance rota (gyldig domain),
            # og referanse-eininga er ei sub-eining under den.
            builder = _daq.InstanceBuilder()
            builder.add_module_path(self._module_path)
            self._instance = builder.build()
            log.info("  Instance oppretta")

            # Legg til referanse-eining som sub-device
            self._device = self._instance.add_device("daqref://device0")
            enhet_namn = ""
            try:
                enhet_namn = self._device.name if hasattr(self._device, 'name') else str(self._device)
            except Exception:
                enhet_namn = "RefDevice0"
            log.info(f"  Referanse-enhet (sub-device): {enhet_namn}")

            # Konfigurer 8 kanalar som matchar SIRIUS Sundet-oppsett
            self._konfig_kanalar()

            # Hent signal-referansar frå kvar kanal for data-injeksjon
            self._kanal_signal = []
            try:
                for ch in self._device.channels:
                    sigs = list(ch.signals)
                    if sigs:
                        self._kanal_signal.append((ch, sigs[0]))
                    else:
                        self._kanal_signal.append((ch, None))
                log.info(f"  Signal-referansar: {len(self._kanal_signal)} kanalar, "
                         f"{sum(1 for _, s in self._kanal_signal if s is not None)} med signal")
            except Exception as e:
                log.warning(f"  Signal-henting feilet: {e}")

            # List kanalar
            kanalar = []
            try:
                for ch in self._device.channels:
                    namn = ch.name if hasattr(ch, 'name') else str(ch)
                    kanalar.append(namn)
                log.info(f"  Kanalar: {len(kanalar)}")
            except Exception as e:
                log.warning(f"  Kanallisting feilet: {e}")

            # Start servere på instance (som er rota).
            # IKKJE bruk add_standard_servers() - Native Streaming handshake
            # er broten (server lukkar tilkoblinga under handshake).
            # Start berre OPC-UA + LT Streaming (WebSocket).
            servere = []
            for srv_type in ['OpenDAQOPCUA', 'OpenDAQLTStreaming',
                             'OpenDAQNewLTStreaming']:
                try:
                    self._instance.add_server(srv_type, None)
                    servere.append(srv_type)
                    log.info(f"  Server: {srv_type}")
                except Exception as e2:
                    log.warning(f"  {srv_type} feilet: {e2}")

            ip = self._hent_ip()

            # Fiks tomme server capability-adresser.
            # openDAQ sin mDNS-baserte interface-oppdaging fungerer ikkje
            # paalidelig i Docker (fleire bridge-interface). Sett adresser
            # manuelt slik at DewesoftX-klienten finn streaming-endepunkta.
            self._fiks_server_capabilities(ip)

            with self._lock:
                self._status.update({
                    "tilgjengelig": True,
                    "aktiv": True,
                    "enhet_namn": enhet_namn,
                    "tilkobling": "daqref://device0",
                    "kanalar": kanalar,
                    "servere": servere,
                    "ip": ip,
                    "startet": datetime.now().isoformat(),
                    "feil": None,
                })

            self._tilgjengelig = True

            # Logg hostname-oppslag (viktig for OPC-UA endpoint URL)
            try:
                import socket as _sock
                hostname = _sock.gethostname()
                resolved = _sock.gethostbyname(hostname)
                log.info(f"  Hostname: {hostname} -> {resolved}")
                if resolved.startswith("127."):
                    log.warning(f"  ADVARSEL: hostname resolver til {resolved}!")
                    log.warning("  OPC-UA vil annonsere localhost. Fiks /etc/hosts.")
            except Exception:
                pass

            # Start bakgrunnstraad for å lese signal-verdiar (for web UI)
            self._stopp_event.clear()
            self._leser_traad = threading.Thread(
                target=self._les_signal_loop, daemon=True
            )
            self._leser_traad.start()

            log.info("")
            log.info("  openDAQ nettverksbro aktiv:")
            log.info(f"    OPC-UA:           {ip}:4840")
            log.info(f"    Native Streaming: {ip}:7420")
            log.info(f"    WebSocket:        {ip}:7414")
            log.info(f"    DewesoftX: HW Settings > + > openDAQ device")
            log.info("")
            return True

        except Exception as e:
            feil_msg = str(e)
            log.error(f"openDAQ bridge feilet: {feil_msg}")
            with self._lock:
                self._status["feil"] = feil_msg
            return False

    def oppdater_data(self, kanal_data):
        """
        Injiser ADC-data frå SIRIUS inn i openDAQ-signalar.

        Oppdaterer kanal-eigenskapar (Amplitude, Offset) basert på reelle
        ADC-verdiar slik at referanse-eininga reflekterer faktiske maaleverdiar.
        Lagrar ogsaa siste verdiar for live-visning i web UI.

        Args:
            kanal_data: dict {"kanal_0": np.array(int16), ...} fraa SiriusDriver
        """
        if not self._tilgjengelig or not self._kanal_signal:
            return

        import time
        self._sirius_aktiv = True
        self._sirius_ts = time.time()

        try:
            for kanal_idx, (key, data) in enumerate(sorted(kanal_data.items())):
                if kanal_idx >= len(self._kanal_signal):
                    break

                ch, sig = self._kanal_signal[kanal_idx]
                if data is None or len(data) == 0:
                    continue

                # Konverter int16 ADC-verdiar til float
                fdata = data.astype(np.float64)

                # Berekn statistikk
                snitt = float(np.mean(fdata))
                rms = float(np.sqrt(np.mean(fdata ** 2)))
                topp = float(np.max(np.abs(fdata)))

                # Lagre siste verdiar for web UI
                self._siste_verdiar[key] = {
                    "snitt": round(snitt, 2),
                    "rms": round(rms, 2),
                    "topp": round(topp, 2),
                    "siste": int(data[-1]),
                    "antall": len(data),
                    "kjelde": "sirius",
                }

                # Oppdater referanse-eininga sine kanal-eigenskapar
                # slik at genererte signal matchar reelle verdiar
                try:
                    ch.set_property_value("Amplitude", topp)
                    ch.set_property_value("Offset", snitt)
                except Exception:
                    pass  # Eigenskapane finst kanskje ikkje

            self._data_teller += 1

        except Exception as e:
            if self._data_teller % 100 == 0:
                log.warning(f"oppdater_data feil: {e}")

    def hent_siste_verdiar(self) -> dict:
        """Returner siste kanal-verdiar for web UI live-visning."""
        result = dict(self._siste_verdiar)
        result["_debug"] = {
            "data_teller": self._data_teller,
            "sirius_aktiv": self._sirius_aktiv,
            "sirius_ts": self._sirius_ts,
        }
        return result

    def _les_signal_loop(self):
        """Bakgrunnstraad som genererer simulerte verdiar.

        Brukar SUNDET_KANALAR-konfig direkte (ikkje openDAQ-eigenskapar)
        for å vise realistiske simulerte verdiar i web UI.
        Hopper over når SIRIUS leverer reelle data.
        """
        import time
        import math
        log.info("  Signal-leser-traad starta")

        t0 = time.time()
        while not self._stopp_event.is_set():
            # Sjekk om SIRIUS er aktiv (data motteke siste 5 sekund)
            sirius_nyleg = (
                self._sirius_aktiv and
                (time.time() - self._sirius_ts) < 5.0
            )

            if not sirius_nyleg:
                self._sirius_aktiv = False
                t = time.time() - t0
                # Generer simulerte verdiar frå SUNDET_KANALAR
                for i, cfg in enumerate(self.SUNDET_KANALAR):
                    key = f"kanal_{i}"
                    amp = cfg["amplitude"]
                    freq = cfg["freq"]
                    # Forskyv fase per kanal (120° mellom fasane)
                    faseforskyvning = 0.0
                    if i < 3:
                        faseforskyvning = i * (2.0 * math.pi / 3.0)  # L1, L2, L3
                    elif 4 <= i <= 6:
                        faseforskyvning = (i - 4) * (2.0 * math.pi / 3.0)

                    verdi = amp * math.sin(2 * math.pi * freq * t + faseforskyvning)
                    rms = amp / math.sqrt(2) if amp > 0 else 0.0
                    self._siste_verdiar[key] = {
                        "snitt": 0.0,
                        "rms": round(rms, 2),
                        "topp": round(abs(amp), 2),
                        "siste": round(verdi, 2),
                        "antall": 100,
                        "kjelde": "simulert",
                    }

            self._stopp_event.wait(timeout=1.0)

        log.info("  Signal-leser-traad stoppa")

    def stopp(self):
        """Stopp openDAQ instance og servere."""
        self._tilgjengelig = False
        self._stopp_event.set()
        if self._leser_traad and self._leser_traad.is_alive():
            self._leser_traad.join(timeout=3)
        with self._lock:
            self._status["aktiv"] = False
        # Instance-opprydding handtert av Python GC
        self._instance = None
        self._device = None
        log.info("openDAQ nettverksbro stoppa")

    # Kanalopsett fraa Sundet-prosjektet (SIRIUSi-HS, 8xAI)
    # AI 1-3: Spenning (HV, 1600V range, ±500V brukarskala, 50 Hz sinus)
    # AI 4:   Spenning (HV, inaktiv)
    # AI 5-7: Straum via CT (LV, 5V range, skaleringsfaktor 2000, ±100A)
    # AI 8:   Spenning (LV, inaktiv)
    SUNDET_KANALAR = [
        {"namn": "AI 1", "amplitude": 325.0, "freq": 50.0, "range": (-500, 500)},
        {"namn": "AI 2", "amplitude": 325.0, "freq": 50.0, "range": (-500, 500)},
        {"namn": "AI 3", "amplitude": 325.0, "freq": 50.0, "range": (-500, 500)},
        {"namn": "AI 4", "amplitude": 0.0,   "freq": 50.0, "range": (-500, 500)},
        {"namn": "AI 5", "amplitude": 70.0,  "freq": 50.0, "range": (-100, 100)},
        {"namn": "AI 6", "amplitude": 70.0,  "freq": 50.0, "range": (-100, 100)},
        {"namn": "AI 7", "amplitude": 70.0,  "freq": 50.0, "range": (-100, 100)},
        {"namn": "AI 8", "amplitude": 0.0,   "freq": 50.0, "range": (-100, 100)},
    ]

    def _konfig_kanalar(self):
        """Sett 8 kanalar med namn og eigenskapar frå kanal_konfig (eller fallback til SUNDET)."""
        try:
            self._device.set_property_value("NumberOfChannels", 8)
            log.info("  NumberOfChannels sett til 8")
        except Exception as e:
            log.warning(f"  Kunne ikkje sette NumberOfChannels: {e}")
            return

        # Les persistert konfig (fallback til standard)
        kanal_konfig = les_konfig()

        channels = list(self._device.channels)
        for i, ch in enumerate(channels):
            if i >= len(kanal_konfig):
                break
            kk = kanal_konfig[i]
            # Bruk SUNDET_KANALAR for amplitude/freq som fallback
            sundet = self.SUNDET_KANALAR[i] if i < len(self.SUNDET_KANALAR) else None
            try:
                ch.name = kk.namn
                amp = sundet["amplitude"] if sundet else 0.0
                freq = sundet["freq"] if sundet else 50.0
                ch.set_property_value("Amplitude", amp if kk.aktiv else 0.0)
                ch.set_property_value("Frequency", freq)
                ch.set_property_value("CustomRange", _daq.Range(kk.range_min, kk.range_max))
                # Sinus-boelgje for simulering
                ch.set_property_value("Waveform", 0)
                log.info(f"  {kk.namn}: aktiv={kk.aktiv}, "
                         f"range=[{kk.range_min}, {kk.range_max}], type={kk.type}")
            except Exception as e:
                log.warning(f"  Kanal {i} konfig feilet: {e}")

    def _fiks_server_capabilities(self, ip):
        """Sett server capability-adresser manuelt for Docker-miljoe."""
        try:
            # Server capabilities er på instance (root), ikkje sub-device
            info = self._instance.info
            caps = info.server_capabilities
            for cap in caps:
                proto_id = cap.protocol_id
                prefix = cap.prefix
                port = cap.port
                conn_str = cap.connection_string
                if conn_str:
                    continue  # Allereie satt, ikkje overskriv

                # Bygg connection string: daq.ns://ip:port/
                ny_conn = f"{prefix}://{ip}:{port}/"
                try:
                    cap.set_property_value("PrimaryConnectionString", ny_conn)
                    log.info(f"  Cap {proto_id}: PrimaryConnectionString = {ny_conn}")
                except Exception as e:
                    log.warning(f"  Cap {proto_id}: set PrimaryConnectionString feilet: {e}")

                try:
                    cap.set_property_value("Addresses", [ip])
                    log.info(f"  Cap {proto_id}: Addresses = [{ip}]")
                except Exception as e:
                    log.warning(f"  Cap {proto_id}: set Addresses feilet: {e}")

                try:
                    cap.set_property_value("ConnectionStrings", [ny_conn])
                except Exception as e:
                    log.warning(f"  Cap {proto_id}: set ConnectionStrings feilet: {e}")
        except Exception as e:
            log.warning(f"  Fiks server capabilities feilet: {e}")

    @staticmethod
    def _hent_ip():
        """Finn maskinens IP-adresse."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

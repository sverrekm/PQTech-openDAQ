#!/usr/bin/env python3
"""
openDAQ Nettverksbro - DewesoftX-tilkobling via openDAQ-servere
================================================================
Startar openDAQ Instance med referanse-enhet som ROOT og standard
servere (OPC-UA, Native Streaming, WebSocket) slik at DewesoftX kan
koble til via openDAQ-protokollen.

Fase 1: Referanse-enhet (daqref://device0) med simulerte kanalar
Fase 2: Reelle SIRIUS-data via MockSignal/OPC-UA (framtidig)

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

            # Bruk InstanceBuilder med set_root_device slik at servere
            # startar med eininga som rot (ikkje add_device som sub-eining).
            # Same moenster som openDAQ sine eigne testar.
            builder = _daq.InstanceBuilder()
            builder.add_module_path(self._module_path)
            builder.set_root_device("daqref://device0")
            log.info("  Root device: daqref://device0")

            self._instance = builder.build()
            log.info("  Instance oppretta med root device")

            # Hent root device
            self._device = self._instance.root_device
            enhet_namn = ""
            try:
                enhet_namn = self._device.name if hasattr(self._device, 'name') else str(self._device)
            except Exception:
                enhet_namn = "RefDevice0"
            log.info(f"  Referanse-enhet: {enhet_namn}")

            # Konfigurer 8 kanalar som matchar SIRIUS Sundet-oppsett
            self._konfig_kanalar()

            # List kanalar
            kanalar = []
            try:
                for ch in self._device.channels:
                    namn = ch.name if hasattr(ch, 'name') else str(ch)
                    kanalar.append(namn)
                log.info(f"  Kanalar: {len(kanalar)}")
            except Exception as e:
                log.warning(f"  Kanallisting feilet: {e}")

            # Start servere eksplisitt ETTER root device er satt.
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
        Placeholder for Fase 2: mat reelle SIRIUS-data inn i openDAQ-signalar.

        Args:
            kanal_data: dict {"kanal_0": np.array(int16), ...} fraa SiriusDriver
        """
        # Fase 2: MockSignal-injeksjon eller OPC-UA variabel-oppdatering
        pass

    def stopp(self):
        """Stopp openDAQ instance og servere."""
        self._tilgjengelig = False
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
        """Sett 8 kanalar med namn og eigenskapar fraa Sundet-oppsettet."""
        try:
            self._device.set_property_value("NumberOfChannels", 8)
            log.info("  NumberOfChannels sett til 8")
        except Exception as e:
            log.warning(f"  Kunne ikkje sette NumberOfChannels: {e}")
            return

        channels = list(self._device.channels)
        for i, ch in enumerate(channels):
            if i >= len(self.SUNDET_KANALAR):
                break
            cfg = self.SUNDET_KANALAR[i]
            try:
                ch.name = cfg["namn"]
                ch.set_property_value("Amplitude", cfg["amplitude"])
                ch.set_property_value("Frequency", cfg["freq"])
                lo, hi = cfg["range"]
                ch.set_property_value("CustomRange", _daq.Range(lo, hi))
                # Sinus-boelgje for simulering
                ch.set_property_value("Waveform", 0)
                log.info(f"  {cfg['namn']}: amp={cfg['amplitude']}, "
                         f"freq={cfg['freq']}, range=[{lo}, {hi}]")
            except Exception as e:
                log.warning(f"  Kanal {i} konfig feilet: {e}")

    def _fiks_server_capabilities(self, ip):
        """Sett server capability-adresser manuelt for Docker-miljoe."""
        try:
            info = self._device.info
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

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
            # (same moenster som openDAQ quickstart-test)
            servere = []
            try:
                srv_list = self._instance.add_standard_servers()
                for s in srv_list:
                    srv_id = s.id if hasattr(s, 'id') else str(s)
                    servere.append(srv_id)
                    log.info(f"  Server: {srv_id}")
            except Exception as e:
                log.warning(f"  add_standard_servers feilet: {e}")
                for srv_type in ['OpenDAQOPCUA', 'OpenDAQNativeStreaming',
                                 'OpenDAQLTStreaming']:
                    try:
                        self._instance.add_server(srv_type, None)
                        servere.append(srv_type)
                        log.info(f"  Server (fallback): {srv_type}")
                    except Exception as e2:
                        log.warning(f"  {srv_type} feilet: {e2}")

            ip = self._hent_ip()

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

#!/usr/bin/env python3
"""
openDAQ Server - Dewesoft SIRIUS over nettverk
================================================
Dobbel funksjon:
  1. Eksponerer SIRIUS over nettverket for DewesoftX
  2. Maaler autonomt og lagrer data lokalt

DewesoftX kan koble til, konfigurere kanaler og analyse.
Naar DewesoftX kobles fra, fortsetter Pi aa maale paa egenhaand
med sist lagrede konfigurasjon.

Bruk:
  python3 opendaq_server.py
  python3 opendaq_server.py --simulator
  python3 opendaq_server.py --maale-intervall 60 --maale-varighet 5
"""

import sys
import os
import json
import csv
import time
import signal
import logging
import argparse
import socket
import threading
from datetime import datetime
from pathlib import Path

import numpy as np

try:
    import opendaq as daq
except ImportError:
    build_path = os.path.join(os.path.dirname(__file__), 'build', 'bin', 'Release')
    if os.path.exists(build_path):
        sys.path.insert(0, build_path)
        import opendaq as daq
    else:
        print("[FEIL] opendaq ikke funnet.")
        sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger('opendaq_server')

# Global status delt med web UI
server_status = {
    "kjorer": False,
    "enhet_navn": "",
    "tilkobling": "",
    "kanaler": [],
    "servere": [],
    "startet": None,
    "feil": None,
    "siste_maaling": None,
    "antall_maalinger": 0,
    "autonom": False,
}

KONFIG_STI = Path("/data/konfig/enhet_konfig.json")

# Globale referanser for enhetsstyring fra web UI
_instance = None
_device = None
_maaler = None
_args = None
_lock = threading.Lock()


def hent_ip():
    """Finn maskinens IP-adresse."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def finn_sirius(instance):
    """Sok etter SIRIUS blant tilgjengelige enheter."""
    log.info("Soker etter tilgjengelige enheter...")
    enheter = instance.available_devices

    sirius = None
    for info in enheter:
        navn = info.name if hasattr(info, 'name') else str(info)
        conn = info.connection_string if hasattr(info, 'connection_string') else ""
        log.info(f"  Funnet: {navn} ({conn})")

        navn_lower = navn.lower() if isinstance(navn, str) else ""
        if any(k in navn_lower for k in ["sirius", "dewesoft", "dewetron"]):
            sirius = info
            log.info(f"  --> SIRIUS identifisert!")

    return sirius


def list_kanaler(device):
    """List alle kanaler paa en enhet."""
    kanaler = []
    try:
        for ch in device.channels:
            navn = ch.name if hasattr(ch, 'name') else str(ch)
            kanaler.append(navn)
    except Exception as e:
        log.debug(f"Feil ved kanallisting: {e}")
    return kanaler


def hent_tilgjengelige_enheter():
    """Returnerer liste over oppdagede openDAQ-enheter."""
    with _lock:
        if _instance is None:
            return []
        try:
            enheter = []
            for info in _instance.available_devices:
                navn = info.name if hasattr(info, 'name') else str(info)
                conn = info.connection_string if hasattr(info, 'connection_string') else ""
                enheter.append({"navn": navn, "tilkobling": conn})
            # Legg til kjente enhetstyper som kan kobles til direkte
            for key in _instance.available_device_types:
                if key not in [e.get("tilkobling", "").split("://")[0] for e in enheter]:
                    enheter.append({"navn": f"[{key}]", "tilkobling": f"{key}://device0"})
            return enheter
        except Exception as e:
            log.warning(f"Feil ved enhetssok: {e}")
            return []


def koble_til_enhet(tilkobling_str):
    """Bytt til en ny enhet. Returnerer (suksess, melding)."""
    global _device, _maaler

    with _lock:
        if _instance is None:
            return False, "Server ikke startet"

        log.info(f"Bytter enhet til: {tilkobling_str}")

        # Stopp autonom maaling
        if _maaler:
            _maaler.stopp()
            _maaler = None

        # Fjern gammel enhet
        if _device:
            try:
                _instance.remove_device(_device)
            except Exception as e:
                log.warning(f"Kunne ikke fjerne gammel enhet: {e}")
            _device = None

        # Koble til ny enhet
        try:
            _device = _instance.add_device(tilkobling_str)
        except Exception as e:
            server_status.update({
                "enhet_navn": "",
                "tilkobling": "",
                "kanaler": [],
                "feil": str(e),
            })
            log.error(f"Kunne ikke koble til {tilkobling_str}: {e}")
            return False, f"Tilkobling feilet: {e}"

        enhet_navn = _device.name if hasattr(_device, 'name') else str(_device)
        kanaler = list_kanaler(_device)

        # Oppdater status
        server_status.update({
            "enhet_navn": enhet_navn,
            "tilkobling": tilkobling_str,
            "kanaler": kanaler,
            "feil": None,
        })

        log.info(f"Tilkoblet: {enhet_navn} ({len(kanaler)} kanaler)")

        # Start ny autonom maaling
        if _args and _args.maale_intervall > 0:
            _maaler = AutonomMaaler(
                instance=_instance,
                device=_device,
                utmappe=_args.utmappe,
                intervall=_args.maale_intervall,
                varighet=_args.maale_varighet,
                sample_rate=_args.sample_rate,
                prefiks=_args.prefiks,
            )
            _maaler.start()

        lagre_konfig(_instance)
        return True, f"Tilkoblet {enhet_navn}"


def lagre_konfig(instance):
    """Lagre gjeldende enhetskonfigurasjon til fil."""
    try:
        KONFIG_STI.parent.mkdir(parents=True, exist_ok=True)
        konfig_str = instance.save_configuration()
        with open(KONFIG_STI, 'w', encoding='utf-8') as f:
            f.write(konfig_str)
        log.info(f"Konfigurasjon lagret: {KONFIG_STI}")
    except Exception as e:
        log.warning(f"Kunne ikke lagre konfigurasjon: {e}")


def last_konfig(instance):
    """Last lagret enhetskonfigurasjon fra fil."""
    if not KONFIG_STI.exists():
        log.info("Ingen lagret konfigurasjon funnet")
        return False
    try:
        with open(KONFIG_STI, 'r', encoding='utf-8') as f:
            konfig_str = f.read()
        instance.load_configuration(konfig_str)
        log.info(f"Konfigurasjon lastet fra: {KONFIG_STI}")
        return True
    except Exception as e:
        log.warning(f"Kunne ikke laste konfigurasjon: {e}")
        return False


class AutonomMaaler:
    """
    Maaler autonomt i bakgrunnen og lagrer data lokalt.
    Kjorer uavhengig av om DewesoftX er tilkoblet.
    """

    def __init__(self, instance, device, utmappe, intervall, varighet,
                 sample_rate, prefiks):
        self.instance = instance
        self.device = device
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
        log.info(f"Autonom maaling startet: hvert {self.intervall}s, "
                 f"varighet {self.varighet}s")

    def stopp(self):
        """Stopp autonom maaling."""
        self._stopp.set()
        if self._traad:
            self._traad.join(timeout=10)
        server_status["autonom"] = False

    def _maal_loop(self):
        """Hovedloop for autonom maaling."""
        while not self._stopp.is_set():
            try:
                self._gjor_maaling()
            except Exception as e:
                log.error(f"Feil under maaling: {e}")

            # Lagre konfigurasjon periodisk
            try:
                lagre_konfig(self.instance)
            except Exception:
                pass

            # Vent til neste maaling
            self._stopp.wait(timeout=self.intervall)

    def _gjor_maaling(self):
        """Utfor en enkelt maaling og lagre til fil."""
        log.info(f"--- Autonom maaling #{server_status['antall_maalinger'] + 1} ---")

        # Finn signaler aa lese
        signaler = []
        signal_navn = []
        try:
            for ch in self.device.channels:
                for sig in ch.signals:
                    signaler.append(sig)
                    ch_navn = ch.name if hasattr(ch, 'name') else str(ch)
                    sig_navn = sig.name if hasattr(sig, 'name') else str(sig)
                    signal_navn.append(f"{ch_navn}/{sig_navn}")
        except Exception as e:
            log.warning(f"Feil ved signalsok: {e}")

        if not signaler:
            # Fallback: bruk toppnivaa-signaler
            try:
                for sig in self.device.signals:
                    signaler.append(sig)
                    signal_navn.append(sig.name if hasattr(sig, 'name') else str(sig))
            except Exception:
                pass

        if not signaler:
            log.warning("Ingen signaler aa lese")
            return

        # Opprett StreamReader
        antall_samples = int(self.sample_rate * self.varighet)
        try:
            reader = daq.StreamReader(signaler[0])
        except Exception as e:
            log.error(f"Kunne ikke opprette StreamReader: {e}")
            return

        # Les data
        log.info(f"Leser {antall_samples} samples ({self.varighet}s)...")
        time.sleep(self.varighet)

        try:
            verdier = reader.read(antall_samples)
        except Exception as e:
            log.error(f"Feil ved lesing: {e}")
            return

        if verdier is None or len(verdier) == 0:
            log.warning("Ingen data lest")
            return

        data = np.array(verdier, dtype=float)
        log.info(f"Lest {len(data)} samples")

        # Statistikk
        if len(data) > 0:
            rms = float(np.sqrt(np.mean(data**2)))
            snitt = float(np.mean(data))
            log.info(f"  RMS: {rms:.4f}, Snitt: {snitt:.4f}, "
                     f"Min: {float(np.min(data)):.4f}, "
                     f"Maks: {float(np.max(data)):.4f}")

        # Lagre filer
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        self._lagre_csv(data, signal_navn[0] if signal_navn else "signal", ts)
        self._lagre_npz(data, signal_navn[0] if signal_navn else "signal", ts)
        self._lagre_metadata(data, signal_navn, ts)

        server_status["antall_maalinger"] += 1
        server_status["siste_maaling"] = datetime.now().isoformat()

    def _lagre_csv(self, data, navn, ts):
        """Lagre maaling til CSV."""
        filnavn = self.utmappe / f"{self.prefiks}_{ts}.csv"
        dt = 1.0 / self.sample_rate
        with open(filnavn, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow([f"# Autonom maaling - {datetime.now().isoformat()}"])
            writer.writerow(["Tid (s)", navn])
            for i, v in enumerate(data):
                writer.writerow([f"{i * dt:.6f}", f"{v:.6f}"])
        log.info(f"  CSV: {filnavn}")

    def _lagre_npz(self, data, navn, ts):
        """Lagre maaling til NPZ."""
        filnavn = self.utmappe / f"{self.prefiks}_{ts}.npz"
        dt = 1.0 / self.sample_rate
        tid = np.arange(len(data)) * dt
        safe_navn = navn.replace(" ", "_").replace("/", "_")
        np.savez_compressed(
            filnavn,
            **{f"{safe_navn}_tid": tid, f"{safe_navn}_verdier": data}
        )
        log.info(f"  NPZ: {filnavn}")

    def _lagre_metadata(self, data, signal_navn, ts):
        """Lagre metadata-JSON."""
        filnavn = self.utmappe / f"{self.prefiks}_{ts}_metadata.json"
        valid = data[~np.isnan(data)] if len(data) > 0 else data
        meta = {
            "tidspunkt": datetime.now().isoformat(),
            "enhet": server_status["enhet_navn"],
            "varighet_sekunder": self.varighet,
            "sample_rate": self.sample_rate,
            "antall_samples": len(data),
            "signaler": signal_navn,
            "statistikk": {
                "rms": float(np.sqrt(np.mean(valid**2))) if len(valid) > 0 else None,
                "snitt": float(np.mean(valid)) if len(valid) > 0 else None,
                "std": float(np.std(valid)) if len(valid) > 0 else None,
                "min": float(np.min(valid)) if len(valid) > 0 else None,
                "maks": float(np.max(valid)) if len(valid) > 0 else None,
            }
        }
        with open(filnavn, 'w', encoding='utf-8') as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)
        log.info(f"  Metadata: {filnavn}")


def start_server(args):
    """Start openDAQ server med autonom maaling."""
    global server_status, _instance, _device, _maaler, _args

    log.info("=" * 60)
    log.info("  openDAQ Server - Dewesoft SIRIUS")
    log.info("=" * 60)

    # openDAQ laster moduler (.module.so) fra CWD
    module_path = os.environ.get("OPENDAQ_MODULE_PATH", "/usr/local/lib")
    if os.path.isdir(module_path):
        os.chdir(module_path)
        log.info(f"Modulsok: {module_path}")

    # Opprett openDAQ-instans
    _instance = daq.Instance()
    _args = args

    # Finn og koble til enhet
    tilkobling = ""

    if args.tilkobling:
        tilkobling = args.tilkobling
        log.info(f"Kobler til: {tilkobling}")
        _device = _instance.add_device(tilkobling)
    elif args.simulator:
        tilkobling = "daqref://device0"
        log.info(f"Starter simulator: {tilkobling}")
        _device = _instance.add_device(tilkobling)
    else:
        sirius_info = finn_sirius(_instance)
        if sirius_info:
            tilkobling = sirius_info.connection_string
            log.info(f"Kobler til SIRIUS: {tilkobling}")
            _device = _instance.add_device(tilkobling)
        else:
            log.warning("SIRIUS ikke funnet - bruker simulator")
            tilkobling = "daqref://device0"
            _device = _instance.add_device(tilkobling)

    if not _device:
        server_status["feil"] = "Kunne ikke koble til enhet"
        log.error("Kunne ikke koble til enhet")
        return

    enhet_navn = _device.name if hasattr(_device, 'name') else str(_device)
    log.info(f"Tilkoblet: {enhet_navn}")

    # Last lagret konfigurasjon (fra forrige DewesoftX-sesjon)
    last_konfig(_instance)

    # List kanaler
    kanaler = list_kanaler(_device)
    for k in kanaler:
        log.info(f"  Kanal: {k}")

    # Start nettverksservere (OPC-UA + native streaming)
    log.info("")
    log.info("Starter nettverksservere...")

    servere = []
    try:
        srv_list = _instance.add_standard_servers()
        for s in srv_list:
            srv_navn = s.id if hasattr(s, 'id') else str(s)
            servere.append(srv_navn)
            log.info(f"  Server startet: {srv_navn}")
    except Exception as e:
        log.warning(f"add_standard_servers feilet: {e}")
        for srv_type in ['OpenDAQOPCUA', 'OpenDAQLTStreaming']:
            try:
                s = _instance.add_server(srv_type, None)
                servere.append(srv_type)
                log.info(f"  Server startet: {srv_type}")
            except Exception as e2:
                log.warning(f"  Kunne ikke starte {srv_type}: {e2}")

    ip = hent_ip()

    # Oppdater global status
    server_status.update({
        "kjorer": True,
        "enhet_navn": enhet_navn,
        "tilkobling": tilkobling,
        "kanaler": kanaler,
        "servere": servere,
        "startet": datetime.now().isoformat(),
        "feil": None,
    })

    log.info("")
    log.info("=" * 60)
    log.info(f"  openDAQ Server kjorer!")
    log.info(f"  IP:     {ip}")
    log.info(f"  Enhet:  {enhet_navn}")
    log.info(f"  Kanaler: {len(kanaler)}")
    log.info(f"  Servere: {', '.join(servere)}")
    log.info("")
    log.info(f"  I DewesoftX paa Windows:")
    log.info(f"    Settings > Devices > Dewesoft NET")
    log.info(f"    Manually add measurement unit")
    log.info(f"    Address: {ip}")
    if args.maale_intervall > 0:
        log.info("")
        log.info(f"  Autonom maaling: hvert {args.maale_intervall}s")
        log.info(f"  Varighet: {args.maale_varighet}s")
        log.info(f"  Utmappe: {args.utmappe}")
    log.info("=" * 60)

    # Start web-grensesnitt i bakgrunnstraad (same prosess = delt _instance)
    def _start_web():
        from web_ui import app as flask_app
        web_port = int(os.environ.get("WEB_PORT", 8080))
        log.info(f"Web UI startet paa port {web_port}")
        flask_app.run(host="0.0.0.0", port=web_port, use_reloader=False)

    web_traad = threading.Thread(target=_start_web, daemon=True)
    web_traad.start()

    # Start autonom maaling i bakgrunnen
    _maaler = AutonomMaaler(
        instance=_instance,
        device=_device,
        utmappe=args.utmappe,
        intervall=args.maale_intervall,
        varighet=args.maale_varighet,
        sample_rate=args.sample_rate,
        prefiks=args.prefiks,
    )
    _maaler.start()

    # Hold serveren kjorende
    stopp = False

    def signal_handler(sig, frame):
        nonlocal stopp
        log.info("Mottok stoppsignal - lagrer konfigurasjon...")
        lagre_konfig(_instance)
        stopp = True

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    try:
        while not stopp:
            time.sleep(1)
    except KeyboardInterrupt:
        pass

    log.info("Stopper...")
    if _maaler:
        _maaler.stopp()
    lagre_konfig(_instance)
    server_status["kjorer"] = False


def main():
    parser = argparse.ArgumentParser(
        description='openDAQ Server - Eksponerer Dewesoft SIRIUS over nettverket '
                    'og maaler autonomt'
    )
    parser.add_argument(
        '--tilkobling', '-t',
        help='Connection string (f.eks. daq.opcua://192.168.1.100)'
    )
    parser.add_argument(
        '--simulator', '-s',
        action='store_true',
        help='Bruk simulator (daqref://device0)'
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
        '--prefiks', default='maaling',
        help='Filnavn-prefiks (standard: maaling)'
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

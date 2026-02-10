#!/usr/bin/env python3
"""
openDAQ Server - Dewesoft SIRIUS over nettverk
================================================
Oppdager SIRIUS via USB og eksponerer den over nettverket
via OPC-UA og native streaming. DewesoftX kan deretter
koble til via "Dewesoft NET" med Pi sin IP-adresse.

Bruk:
  python3 opendaq_server.py
  python3 opendaq_server.py --simulator
  python3 opendaq_server.py --tilkobling daq.opcua://192.168.1.100
"""

import sys
import os
import json
import time
import signal
import logging
import argparse
import socket
from datetime import datetime

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
    "enhet": None,
    "enhet_navn": "",
    "tilkobling": "",
    "kanaler": [],
    "servere": [],
    "startet": None,
    "feil": None,
}


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


def list_kanaler(device, prefix=""):
    """Rekursivt list alle kanaler paa en enhet."""
    kanaler = []
    try:
        for ch in device.channels:
            navn = ch.name if hasattr(ch, 'name') else str(ch)
            kanaler.append(f"{prefix}{navn}")
        for sig in device.signals:
            navn = sig.name if hasattr(sig, 'name') else str(sig)
            kanaler.append(f"{prefix}[sig] {navn}")
    except Exception as e:
        log.debug(f"Feil ved kanallisting: {e}")
    return kanaler


def start_server(args):
    """Start openDAQ server som eksponerer enhet over nettverket."""
    global server_status

    log.info("=" * 60)
    log.info("  openDAQ Server - Dewesoft SIRIUS")
    log.info("=" * 60)

    # Opprett openDAQ-instans
    instance = daq.Instance()

    # Finn og koble til enhet
    device = None
    tilkobling = ""

    if args.tilkobling:
        # Bruk spesifisert tilkoblingsstreng
        tilkobling = args.tilkobling
        log.info(f"Kobler til: {tilkobling}")
        device = instance.add_device(tilkobling)

    elif args.simulator:
        # Bruk referanseenhet (simulator)
        tilkobling = "daqref://device0"
        log.info(f"Starter simulator: {tilkobling}")
        device = instance.add_device(tilkobling)

    else:
        # Auto-oppdagelse: sok etter SIRIUS
        sirius_info = finn_sirius(instance)
        if sirius_info:
            tilkobling = sirius_info.connection_string
            log.info(f"Kobler til SIRIUS: {tilkobling}")
            device = instance.add_device(tilkobling)
        else:
            # Fallback til simulator
            log.warning("SIRIUS ikke funnet - bruker simulator")
            tilkobling = "daqref://device0"
            device = instance.add_device(tilkobling)

    if not device:
        server_status["feil"] = "Kunne ikke koble til enhet"
        log.error("Kunne ikke koble til enhet")
        return

    enhet_navn = device.name if hasattr(device, 'name') else str(device)
    log.info(f"Tilkoblet: {enhet_navn}")

    # List kanaler
    kanaler = list_kanaler(device)
    for k in kanaler:
        log.info(f"  Kanal: {k}")

    # Start servere (OPC-UA + native streaming)
    log.info("")
    log.info("Starter nettverksservere...")

    servere = []
    try:
        srv_list = instance.add_standard_servers()
        for s in srv_list:
            srv_navn = s.id if hasattr(s, 'id') else str(s)
            servere.append(srv_navn)
            log.info(f"  Server startet: {srv_navn}")
    except Exception as e:
        log.warning(f"add_standard_servers feilet: {e}")
        # Proov individuelt
        for srv_type in ['OpenDAQOPCUA', 'OpenDAQLTStreaming']:
            try:
                s = instance.add_server(srv_type, None)
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
    log.info("=" * 60)

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
            time.sleep(1)
    except KeyboardInterrupt:
        pass

    log.info("Stopper servere...")
    server_status["kjorer"] = False


def main():
    parser = argparse.ArgumentParser(
        description='openDAQ Server - Eksponerer Dewesoft SIRIUS over nettverket'
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
        '--debug',
        action='store_true',
        help='Vis debug-meldinger'
    )

    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    start_server(args)


if __name__ == "__main__":
    main()

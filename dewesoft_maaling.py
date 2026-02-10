#!/usr/bin/env python3
"""
Dewesoft SIRIUS Målesystem - PQTECH
====================================
Kobler til Dewesoft SIRIUS instrument, laster forhåndsinnstilt konfigurasjon,
henter målinger og lagrer data for senere analyse.

Tilkoblingsmetoder:
  - Simulator:  For utvikling/testing uten hardware
  - OPC-UA:     SIRIUS via nettverk (daq.opcua://ip-adresse)
  - Native:     SIRIUS via openDAQ native streaming (daq.ns://ip-adresse)
  - Direkte:    Egendefinert connection string

Bruk:
  python dewesoft_maaling.py --konfig konfig_strom_spenning.json
  python dewesoft_maaling.py --konfig konfig.json --tilkobling daq.opcua://192.168.1.100
  python dewesoft_maaling.py --konfig konfig.json --simulator
  python dewesoft_maaling.py --lagre-konfig ny_konfig.json --simulator
"""

import sys
import os
import json
import time
import argparse
import csv
import logging
from datetime import datetime
from pathlib import Path

import numpy as np

# Finn openDAQ — prøv installert pakke først, deretter lokal build
try:
    import opendaq as daq
except ImportError:
    build_path = os.path.join(os.path.dirname(__file__), 'build', 'bin', 'Release')
    if os.path.exists(build_path):
        sys.path.insert(0, build_path)
        import opendaq as daq
    else:
        print("[FEIL] opendaq ikke funnet. Installer med 'pip install opendaq'")
        print("       eller bygg fra kildekode (se bygg_linux.sh)")
        sys.exit(1)

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger('dewesoft_maaling')


class MaaleKonfig:
    """Håndterer konfigurasjonsfiler for måleoppsett."""

    STANDARD = {
        "navn": "Standard strøm/spenning",
        "beskrivelse": "Standardoppsett for strøm- og spenningsmåling",
        "tilkobling": {
            "type": "simulator",
            "adresse": ""
        },
        "sampling": {
            "sample_rate": 10000,
            "varighet_sekunder": 5.0,
            "pre_trigger_sekunder": 0.0
        },
        "kanaler": [
            {
                "indeks": 0,
                "navn": "Strøm L1",
                "type": "stroem",
                "enhet": "A",
                "egenskaper": {}
            }
        ],
        "eksport": {
            "format": "csv",
            "utmappe": "./maalinger",
            "inkluder_tidsstempler": True,
            "inkluder_metadata": True
        },
        "opendaq_konfig": None
    }

    def __init__(self, filsti=None):
        if filsti and os.path.exists(filsti):
            self.last(filsti)
        else:
            self.data = dict(self.STANDARD)

    def last(self, filsti):
        """Laster konfigurasjon fra JSON-fil."""
        with open(filsti, 'r', encoding='utf-8') as f:
            self.data = json.load(f)
        log.info(f"Konfigurasjon lastet: {filsti}")
        log.info(f"  Navn: {self.data.get('navn', 'Ukjent')}")

    def lagre(self, filsti):
        """Lagrer konfigurasjon til JSON-fil."""
        with open(filsti, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)
        log.info(f"Konfigurasjon lagret: {filsti}")

    def __getitem__(self, key):
        return self.data[key]

    def __setitem__(self, key, value):
        self.data[key] = value

    def get(self, key, default=None):
        return self.data.get(key, default)


class DewesoftMaaling:
    """
    Hovedklasse for måling med Dewesoft SIRIUS via openDAQ.

    Støtter:
      - Tilkobling via simulator, OPC-UA eller native streaming
      - Lasting av forhåndsinnstilt konfigurasjon (JSON)
      - Lagring av enhetskonfig fra openDAQ (save_configuration)
      - Datainnsamling med konfigurerbar varighet
      - Eksport til CSV for senere analyse
    """

    def __init__(self, konfig: MaaleKonfig):
        self.konfig = konfig
        self.instance = None
        self.device = None
        self.readers = []
        self.kanal_info = []

    def koble_til(self, tilkobling_override=None):
        """
        Kobler til DAQ-enhet basert på konfigurasjon.

        Args:
            tilkobling_override: Overstyr connection string fra CLI
        """
        log.info("=" * 60)
        log.info("Dewesoft SIRIUS Målesystem - Tilkobling")
        log.info("=" * 60)

        # Opprett openDAQ instance
        log.info("Oppretter openDAQ instance...")
        self.instance = daq.Instance()

        # Bestem connection string
        tilkobling_type = self.konfig["tilkobling"]["type"]
        adresse = self.konfig["tilkobling"].get("adresse", "")

        if tilkobling_override:
            conn_str = tilkobling_override
            log.info(f"Bruker CLI-tilkobling: {conn_str}")
        elif tilkobling_type == "simulator":
            conn_str = self._finn_simulator()
        elif tilkobling_type == "opcua":
            conn_str = f"daq.opcua://{adresse}"
            log.info(f"OPC-UA tilkobling: {conn_str}")
        elif tilkobling_type == "native":
            conn_str = f"daq.ns://{adresse}"
            log.info(f"Native streaming tilkobling: {conn_str}")
        elif tilkobling_type == "direkte":
            conn_str = adresse
            log.info(f"Direkte tilkobling: {conn_str}")
        else:
            raise ValueError(f"Ukjent tilkoblingstype: {tilkobling_type}")

        # Koble til enhet
        log.info(f"Kobler til: {conn_str}")
        self.device = self.instance.add_device(conn_str)
        log.info(f"Tilkoblet: {self.device.name}")

        # Last openDAQ-konfigurasjon hvis tilgjengelig
        opendaq_konfig = self.konfig.get("opendaq_konfig")
        if opendaq_konfig:
            log.info("Laster openDAQ enhetskonfigurasjon...")
            self.instance.load_configuration(json.dumps(opendaq_konfig))
            log.info("openDAQ konfigurasjon lastet")

        # Konfigurer kanaler
        self._konfigurer_kanaler()

        # Vis enhetsinformasjon
        self._vis_enhetsinfo()

    def _finn_simulator(self):
        """Finner og returnerer simulator connection string.
        Foretrekker daqref:// (ref-device) som har konfigurerbare kanaler,
        faller tilbake til daq.simulator:// hvis ref-device ikke finnes.
        """
        log.info("Søker etter simulator/ref-device...")
        available = self.instance.available_devices

        # Foretrekk ref-device (støtter Waveform, Frequency, Amplitude)
        for dev in available:
            if dev.connection_string.startswith('daqref://'):
                log.info(f"Ref-device funnet: {dev.connection_string}")
                return dev.connection_string

        # Fallback til simulator
        for dev in available:
            if 'simulator' in dev.name.lower():
                log.info(f"Simulator funnet: {dev.connection_string}")
                return dev.connection_string

        raise RuntimeError("Ingen simulator funnet. Sjekk at openDAQ er riktig installert.")

    def _konfigurer_kanaler(self):
        """Konfigurerer kanaler basert på konfigurasjon."""
        channels = self.device.channels_recursive
        log.info(f"Fant {len(channels)} kanaler på enheten")

        konfig_kanaler = self.konfig.get("kanaler", [])
        sample_rate = self.konfig["sampling"]["sample_rate"]

        for kanal_konfig in konfig_kanaler:
            idx = kanal_konfig["indeks"]
            if idx >= len(channels):
                log.warning(f"Kanal {idx} finnes ikke (maks: {len(channels)-1}), hopper over")
                continue

            channel = channels[idx]
            log.info(f"Konfigurerer kanal {idx}: {kanal_konfig['navn']}")

            # Sett sample rate (deaktiver global rate først hvis mulig)
            try:
                channel.set_property_value("UseGlobalSampleRate", False)
            except Exception:
                pass
            try:
                channel.set_property_value("SampleRate", sample_rate)
                log.info(f"  SampleRate: {sample_rate} Hz")
            except Exception as e:
                log.debug(f"  Kunne ikke sette SampleRate: {e}")

            # Sett egendefinerte egenskaper
            for prop_navn, prop_verdi in kanal_konfig.get("egenskaper", {}).items():
                try:
                    channel.set_property_value(prop_navn, prop_verdi)
                    log.info(f"  {prop_navn}: {prop_verdi}")
                except Exception as e:
                    log.warning(f"  Kunne ikke sette {prop_navn}: {e}")

            # Opprett reader for denne kanalen
            signal = channel.signals[0]
            reader = daq.StreamReader(signal)
            self.readers.append(reader)
            self.kanal_info.append({
                "indeks": idx,
                "navn": kanal_konfig["navn"],
                "enhet": kanal_konfig.get("enhet", ""),
                "channel": channel,
                "signal": signal,
                "reader": reader
            })

        if not self.readers:
            raise RuntimeError("Ingen kanaler konfigurert. Sjekk konfigurasjonsfilen.")

        log.info(f"{len(self.readers)} kanaler klare for måling")

    def _vis_enhetsinfo(self):
        """Viser informasjon om tilkoblet enhet."""
        info = self.device.info
        log.info("-" * 40)
        log.info(f"Enhet:      {info.name}")
        log.info(f"Tilkobling: {info.connection_string}")
        log.info(f"Kanaler:    {len(self.device.channels_recursive)}")
        log.info(f"Sample rate: {self.konfig['sampling']['sample_rate']} Hz")
        log.info(f"Varighet:   {self.konfig['sampling']['varighet_sekunder']} s")
        log.info("-" * 40)

    def lagre_enhetskonfig(self, filsti):
        """
        Lagrer gjeldende openDAQ-konfigurasjon til JSON-fil.
        Nyttig for å fange opp riktig oppsett etter manuell konfigurering.
        """
        if not self.instance:
            raise RuntimeError("Ikke tilkoblet. Koble til først.")

        opendaq_json = self.instance.save_configuration()
        konfig = dict(self.konfig.data)
        konfig["opendaq_konfig"] = json.loads(opendaq_json)

        with open(filsti, 'w', encoding='utf-8') as f:
            json.dump(konfig, f, indent=2, ensure_ascii=False)

        log.info(f"Enhetskonfigurasjon lagret til: {filsti}")
        log.info("Denne filen kan lastes neste gang for identisk oppsett.")

    def hent_maalinger(self):
        """
        Henter målinger fra alle konfigurerte kanaler.

        Returns:
            dict med kanal-data: {kanalnavn: {"tid": array, "verdier": array, "enhet": str}}
        """
        varighet = self.konfig["sampling"]["varighet_sekunder"]
        sample_rate = self.konfig["sampling"]["sample_rate"]
        antall_samples = int(sample_rate * varighet)

        log.info(f"Starter datainnsamling: {antall_samples} samples over {varighet}s")

        # Vent litt for å la buffere fylles
        time.sleep(0.2)

        resultater = {}
        for ki in self.kanal_info:
            navn = ki["navn"]
            reader = ki["reader"]
            enhet = ki["enhet"]

            log.info(f"  Leser {navn}...")

            # Les med timeout
            samples = self._les_med_timeout(reader, antall_samples, timeout=varighet + 5)

            # Lag tidsarray
            tid = np.arange(len(samples)) / sample_rate

            resultater[navn] = {
                "tid": tid,
                "verdier": samples,
                "enhet": enhet,
                "sample_rate": sample_rate,
                "antall": len(samples)
            }

            log.info(f"    Leste {len(samples)} samples "
                     f"(snitt: {np.mean(samples):.4f}, "
                     f"std: {np.std(samples):.4f}, "
                     f"min: {np.min(samples):.4f}, "
                     f"max: {np.max(samples):.4f})")

        return resultater

    def _les_med_timeout(self, reader, antall, timeout=10):
        """Leser samples med timeout-håndtering."""
        start = time.time()
        alle_samples = np.array([], dtype=np.float64)

        while len(alle_samples) < antall:
            gjenstaar = antall - len(alle_samples)
            nye = reader.read(gjenstaar)

            if len(nye) > 0:
                alle_samples = np.concatenate([alle_samples, nye])

            if time.time() - start > timeout:
                log.warning(f"Timeout — fikk {len(alle_samples)} av {antall} samples")
                break

            if len(alle_samples) < antall:
                time.sleep(0.05)

        return alle_samples

    def eksporter(self, resultater, prefiks=None):
        """
        Eksporterer målinger til fil(er) for senere analyse.

        Args:
            resultater: Dict fra hent_maalinger()
            prefiks: Valgfritt filnavn-prefiks
        """
        eksport = self.konfig.get("eksport", {})
        utmappe = Path(eksport.get("utmappe", "./maalinger"))
        utmappe.mkdir(parents=True, exist_ok=True)

        tidsstempel = datetime.now().strftime('%Y%m%d_%H%M%S')
        prefiks = prefiks or "maaling"
        inkluder_ts = eksport.get("inkluder_tidsstempler", True)
        inkluder_meta = eksport.get("inkluder_metadata", True)

        # CSV-eksport
        filnavn = utmappe / f"{prefiks}_{tidsstempel}.csv"
        self._eksporter_csv(resultater, filnavn, inkluder_ts, inkluder_meta)

        # Numpy-eksport (for rask lasting i Python)
        npz_filnavn = utmappe / f"{prefiks}_{tidsstempel}.npz"
        self._eksporter_numpy(resultater, npz_filnavn)

        # Metadata-fil
        meta_filnavn = utmappe / f"{prefiks}_{tidsstempel}_metadata.json"
        self._eksporter_metadata(resultater, meta_filnavn)

        log.info(f"Alle filer lagret i: {utmappe}")
        return {
            "csv": str(filnavn),
            "npz": str(npz_filnavn),
            "metadata": str(meta_filnavn)
        }

    def _eksporter_csv(self, resultater, filnavn, inkluder_ts, inkluder_meta):
        """Eksporterer til CSV-format."""
        with open(filnavn, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)

            if inkluder_meta:
                writer.writerow([f"# Dewesoft måling - {datetime.now().isoformat()}"])
                writer.writerow([f"# Konfig: {self.konfig.get('navn', 'Ukjent')}"])
                writer.writerow([f"# Sample rate: {self.konfig['sampling']['sample_rate']} Hz"])
                writer.writerow([])

            # Header
            kanalnavn = list(resultater.keys())
            header = []
            if inkluder_ts:
                header.append("Tid (s)")
            for navn in kanalnavn:
                enhet = resultater[navn]["enhet"]
                header.append(f"{navn} ({enhet})" if enhet else navn)
            writer.writerow(header)

            # Data — bruk lengste kanal som referanse
            maks_len = max(r["antall"] for r in resultater.values())
            sample_rate = self.konfig["sampling"]["sample_rate"]

            for i in range(maks_len):
                rad = []
                if inkluder_ts:
                    rad.append(f"{i / sample_rate:.6f}")
                for navn in kanalnavn:
                    verdier = resultater[navn]["verdier"]
                    if i < len(verdier):
                        rad.append(f"{verdier[i]:.6f}")
                    else:
                        rad.append("")
                writer.writerow(rad)

        log.info(f"CSV eksportert: {filnavn}")

    def _eksporter_numpy(self, resultater, filnavn):
        """Eksporterer til numpy .npz for rask lasting."""
        arrays = {}
        for navn, data in resultater.items():
            safe_navn = navn.replace(" ", "_").replace("/", "_")
            arrays[f"{safe_navn}_tid"] = data["tid"]
            arrays[f"{safe_navn}_verdier"] = data["verdier"]

        np.savez_compressed(filnavn, **arrays)
        log.info(f"Numpy eksportert: {filnavn}")

    def _eksporter_metadata(self, resultater, filnavn):
        """Eksporterer metadata om målingen."""
        meta = {
            "tidspunkt": datetime.now().isoformat(),
            "konfig_navn": self.konfig.get("navn", ""),
            "sampling": self.konfig["sampling"],
            "kanaler": {}
        }

        for navn, data in resultater.items():
            verdier = data["verdier"]
            meta["kanaler"][navn] = {
                "enhet": data["enhet"],
                "antall_samples": data["antall"],
                "sample_rate": data["sample_rate"],
                "statistikk": {
                    "snitt": float(np.mean(verdier)),
                    "std": float(np.std(verdier)),
                    "min": float(np.min(verdier)),
                    "maks": float(np.max(verdier)),
                    "rms": float(np.sqrt(np.mean(verdier**2)))
                }
            }

        with open(filnavn, 'w', encoding='utf-8') as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)

        log.info(f"Metadata eksportert: {filnavn}")

    def koble_fra(self):
        """Kobler fra enhet og rydder opp."""
        self.readers.clear()
        self.kanal_info.clear()
        self.device = None
        self.instance = None
        log.info("Frakoblet")


def opprett_cli():
    """Oppretter CLI-argumentparser."""
    parser = argparse.ArgumentParser(
        description='Dewesoft SIRIUS Målesystem - PQTECH',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Eksempler:
  # Kjør med simulator og standardkonfig
  python dewesoft_maaling.py --simulator

  # Kjør med konfigurasjonsfil
  python dewesoft_maaling.py --konfig konfig_strom_spenning.json

  # Koble til SIRIUS via OPC-UA
  python dewesoft_maaling.py --konfig konfig.json --tilkobling daq.opcua://192.168.1.100

  # Koble til SIRIUS via native streaming
  python dewesoft_maaling.py --konfig konfig.json --tilkobling daq.ns://192.168.1.100

  # Lagre enhetskonfigurasjon etter tilkobling
  python dewesoft_maaling.py --simulator --lagre-konfig min_konfig.json

  # Kjør kontinuerlig (gjenta hver 30 sek)
  python dewesoft_maaling.py --konfig konfig.json --gjenta 30

Tilkoblingstyper:
  simulator              Innebygd openDAQ simulator (for utvikling)
  daq.opcua://IP         OPC-UA tilkobling (SIRIUS via nettverk)
  daq.ns://IP            Native streaming (SIRIUS via nettverk)
  daqref://device0       Referanseenhet
        """)

    parser.add_argument('--konfig', '-k',
                        help='Sti til konfigurasjonsfil (JSON)')
    parser.add_argument('--tilkobling', '-t',
                        help='Overstyr connection string (f.eks. daq.opcua://192.168.1.100)')
    parser.add_argument('--simulator', '-s', action='store_true',
                        help='Bruk innebygd simulator')
    parser.add_argument('--varighet', '-v', type=float,
                        help='Overstyr målevarighet i sekunder')
    parser.add_argument('--sample-rate', '-r', type=int,
                        help='Overstyr sample rate i Hz')
    parser.add_argument('--utmappe', '-u',
                        help='Overstyr utmappe for eksporterte filer')
    parser.add_argument('--lagre-konfig',
                        help='Lagre enhetskonfigurasjon til fil etter tilkobling')
    parser.add_argument('--gjenta', type=int, default=0,
                        help='Gjenta måling hvert N sekund (0 = én gang)')
    parser.add_argument('--prefiks', default='maaling',
                        help='Filnavn-prefiks for eksporterte filer')
    parser.add_argument('--vis-enheter', action='store_true',
                        help='Vis tilgjengelige enheter og avslutt')
    parser.add_argument('--debug', action='store_true',
                        help='Vis debug-meldinger')

    return parser


def vis_enheter():
    """Viser alle tilgjengelige openDAQ-enheter."""
    log.info("Søker etter enheter...")
    instance = daq.Instance()
    enheter = instance.available_devices

    print(f"\nFant {len(enheter)} enheter:\n")
    print(f"  {'Navn':<30} {'Connection String'}")
    print(f"  {'-'*30} {'-'*40}")
    for dev in enheter:
        print(f"  {dev.name:<30} {dev.connection_string}")

    print(f"\nBruk --tilkobling <connection_string> for å koble til.")


def main():
    parser = opprett_cli()
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    print()
    print("=" * 60)
    print("  Dewesoft SIRIUS Målesystem - PQTECH")
    print("=" * 60)
    print()

    # Vis enheter og avslutt
    if args.vis_enheter:
        vis_enheter()
        return 0

    # Last konfigurasjon
    konfig = MaaleKonfig(args.konfig)

    # CLI-overstyringer
    if args.simulator:
        konfig["tilkobling"] = {"type": "simulator", "adresse": ""}
    if args.varighet:
        konfig["sampling"]["varighet_sekunder"] = args.varighet
    if args.sample_rate:
        konfig["sampling"]["sample_rate"] = args.sample_rate
    if args.utmappe:
        konfig["eksport"]["utmappe"] = args.utmappe

    # Opprett målesystem
    system = DewesoftMaaling(konfig)

    try:
        # Koble til
        system.koble_til(tilkobling_override=args.tilkobling)

        # Lagre enhetskonfig hvis forespurt
        if args.lagre_konfig:
            system.lagre_enhetskonfig(args.lagre_konfig)
            log.info("Konfigurasjon lagret. Kan brukes med --konfig neste gang.")

        # Hent målinger (én gang eller gjentakende)
        kjoring_nr = 0
        while True:
            kjoring_nr += 1
            if args.gjenta > 0:
                log.info(f"--- Måling #{kjoring_nr} ---")

            resultater = system.hent_maalinger()
            filer = system.eksporter(resultater, prefiks=args.prefiks)

            # Vis oppsummering
            print()
            print("-" * 40)
            print("Resultater:")
            for navn, data in resultater.items():
                v = data["verdier"]
                print(f"  {navn}:")
                print(f"    RMS:  {np.sqrt(np.mean(v**2)):.4f} {data['enhet']}")
                print(f"    Snitt: {np.mean(v):.4f} {data['enhet']}")
                print(f"    Min:  {np.min(v):.4f} {data['enhet']}")
                print(f"    Maks: {np.max(v):.4f} {data['enhet']}")
            print("-" * 40)
            print(f"Filer: {filer['csv']}")
            print()

            if args.gjenta <= 0:
                break

            log.info(f"Venter {args.gjenta} sekunder til neste måling...")
            time.sleep(args.gjenta)

    except KeyboardInterrupt:
        log.info("\nAvbrutt av bruker")
    except Exception as e:
        log.error(f"Feil: {e}")
        if args.debug:
            import traceback
            traceback.print_exc()
        return 1
    finally:
        system.koble_fra()

    log.info("Ferdig.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

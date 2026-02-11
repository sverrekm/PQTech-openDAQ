#!/usr/bin/env python3
"""
SIRIUS USB Sniffer - Passiv trafikkfangst
==========================================
Fanger USB-trafikk mellom DewesoftX og SIRIUS UTEN aa forstyrre
USB/IP-forbindelsen. Bruker Linux usbmon for passiv overvaakning.

Flyten:
  SIRIUS --USB--> Pi (usbmon fanger her) --USB/IP--> Windows (DewesoftX)

usbmon gir oss tilgang til ALL USB-trafikk paa bussen uten aa ta
kontroll over enheten - perfekt for protokollanalyse mens DewesoftX
kommuniserer normalt.

Bruk:
  python3 sirius_sniffer.py                    # Fang 30 sekunder
  python3 sirius_sniffer.py --varighet 60      # Fang 60 sekunder
  python3 sirius_sniffer.py --analyser fil.json # Analyser lagret fangst

Forutsetning:
  - SIRIUS er koblet til Pi via USB
  - USB/IP-deling er aktiv (DewesoftX er tilkoblet)
  - Kjores som root (eller med debugfs-tilgang)

Alternativ metode (hvis usbmon ikke er tilgjengelig):
  - Bruker pyshark/tcpdump med usbmon
  - Eller leser direkte fra /sys/kernel/debug/usb/usbmon/
"""

import sys
import os
import json
import struct
import time
import argparse
import logging
import subprocess
import threading
from datetime import datetime
from collections import defaultdict, Counter

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger('sirius_sniffer')

DEWESOFT_VID = 0x1CED
SIRIUS_PID = 0x1002


def finn_sirius_businfo():
    """Finn SIRIUS sin USB bus-nummer og device-nummer."""
    sysfs = "/sys/bus/usb/devices"
    if not os.path.isdir(sysfs):
        return None, None, None

    for entry in os.listdir(sysfs):
        dev_path = os.path.join(sysfs, entry)
        vendor_file = os.path.join(dev_path, "idVendor")
        product_file = os.path.join(dev_path, "idProduct")
        busnum_file = os.path.join(dev_path, "busnum")
        devnum_file = os.path.join(dev_path, "devnum")

        if not os.path.isfile(vendor_file):
            continue

        try:
            with open(vendor_file) as f:
                vid = f.read().strip()
            with open(product_file) as f:
                pid = f.read().strip()

            if vid == "1ced" and pid == "1002":
                busnum = None
                devnum = None
                try:
                    with open(busnum_file) as f:
                        busnum = int(f.read().strip())
                    with open(devnum_file) as f:
                        devnum = int(f.read().strip())
                except (IOError, ValueError):
                    pass

                log.info(f"SIRIUS funnet: busid={entry}, bus={busnum}, dev={devnum}")
                return entry, busnum, devnum
        except (IOError, OSError):
            continue

    return None, None, None


def sjekk_usbmon():
    """Sjekk om usbmon er tilgjengelig, proov aa aktivere hvis ikke."""
    # Steg 1: Monter debugfs hvis det mangler
    if not os.path.exists("/sys/kernel/debug/usb"):
        log.info("Monterer debugfs...")
        try:
            subprocess.run(["mount", "-t", "debugfs", "none", "/sys/kernel/debug"],
                          capture_output=True, timeout=5)
            log.info("  debugfs montert")
        except Exception as e:
            log.debug(f"  debugfs-montering feilet: {e}")

    # Steg 2: Last usbmon kernel-modul
    if not os.path.exists("/sys/kernel/debug/usb/usbmon"):
        log.info("Laster usbmon kernel-modul...")
        try:
            r = subprocess.run(["modprobe", "usbmon"],
                              capture_output=True, text=True, timeout=5)
            if r.returncode == 0:
                log.info("  usbmon lastet")
            else:
                log.warning(f"  modprobe usbmon feilet: {r.stderr.strip()}")
        except FileNotFoundError:
            # modprobe finnes ikke, proov insmod
            log.debug("modprobe ikke funnet, proover insmod...")
            # Finn modulen
            try:
                r = subprocess.run(["find", "/lib/modules", "-name", "usbmon.ko*"],
                                  capture_output=True, text=True, timeout=5)
                if r.stdout.strip():
                    mod_path = r.stdout.strip().split('\n')[0]
                    subprocess.run(["insmod", mod_path],
                                  capture_output=True, timeout=5)
                    log.info(f"  usbmon lastet via insmod: {mod_path}")
            except Exception:
                pass
        except Exception as e:
            log.debug(f"  usbmon-lasting feilet: {e}")

    # Steg 3: Verifiser
    tilgjengelig = os.path.exists("/sys/kernel/debug/usb/usbmon")

    if tilgjengelig:
        # List tilgjengelige busser
        try:
            busser = [f for f in os.listdir("/sys/kernel/debug/usb/usbmon")
                     if f.endswith('t') or f.endswith('u')]
            log.info(f"  usbmon busser: {busser}")
        except Exception:
            pass

    return tilgjengelig


def fang_med_usbmon(bus_num, varighet_sek=30, dev_num=None):
    """Fang USB-trafikk med usbmon (tekst-grensesnitt).

    Leser fra /sys/kernel/debug/usb/usbmon/{bus}t
    Format per linje:
      urb_tag timestamp event_type address endpoint_num data_type
      ...etterfulgt av data
    """
    usbmon_path = f"/sys/kernel/debug/usb/usbmon/{bus_num}t"

    if not os.path.exists(usbmon_path):
        # Proov med usbmon0t (fanger alle busser)
        usbmon_path = "/sys/kernel/debug/usb/usbmon/0t"

    if not os.path.exists(usbmon_path):
        log.error(f"usbmon ikke tilgjengelig: {usbmon_path}")
        log.error("Proov: modprobe usbmon && mount -t debugfs none /sys/kernel/debug")
        return None

    log.info(f"Fanger fra {usbmon_path} i {varighet_sek}s...")

    pakker = []
    start = time.time()

    try:
        with open(usbmon_path, 'r') as f:
            while time.time() - start < varighet_sek:
                try:
                    linje = f.readline()
                    if not linje:
                        time.sleep(0.001)
                        continue

                    pakke = parse_usbmon_linje(linje.strip())
                    if pakke:
                        pakker.append(pakke)
                except Exception as e:
                    log.debug(f"Parse-feil: {e}")
                    continue
    except PermissionError:
        log.error(f"Ingen tilgang til {usbmon_path} - kjor som root")
        return None
    except Exception as e:
        log.error(f"Feil ved lesing: {e}")
        return None

    elapsed = time.time() - start
    log.info(f"Fanget {len(pakker)} pakker i {elapsed:.1f}s")

    return pakker


def fang_med_tcpdump(bus_num, varighet_sek=30):
    """Alternativ: Fang med tcpdump paa usbmon-interface."""
    interface = f"usbmon{bus_num}"
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    pcap_fil = f"/tmp/sirius_usb_{ts}.pcap"

    log.info(f"Fanger med tcpdump paa {interface} i {varighet_sek}s...")
    log.info(f"Lagrer til {pcap_fil}")

    try:
        proc = subprocess.Popen(
            ["tcpdump", "-i", interface, "-w", pcap_fil, "-G", str(int(varighet_sek)),
             "-W", "1"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )

        time.sleep(varighet_sek + 1)
        proc.terminate()
        proc.wait(timeout=5)

        if os.path.exists(pcap_fil) and os.path.getsize(pcap_fil) > 0:
            log.info(f"Fangst lagret: {pcap_fil} ({os.path.getsize(pcap_fil)} bytes)")
            return pcap_fil
        else:
            log.warning("Ingen data fanget")
            return None

    except FileNotFoundError:
        log.warning("tcpdump ikke installert")
        return None
    except Exception as e:
        log.error(f"tcpdump feilet: {e}")
        return None


def fang_med_cat(bus_num, varighet_sek=30):
    """Enkleste metode: Les raa usbmon-data med cat og timeout."""
    usbmon_path = f"/sys/kernel/debug/usb/usbmon/{bus_num}u"

    if not os.path.exists(usbmon_path):
        usbmon_path = f"/sys/kernel/debug/usb/usbmon/{bus_num}t"

    if not os.path.exists(usbmon_path):
        return None

    log.info(f"Leser {usbmon_path} i {varighet_sek}s...")

    linjer = []
    try:
        proc = subprocess.Popen(
            ["cat", usbmon_path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )

        start = time.time()
        while time.time() - start < varighet_sek:
            try:
                linje = proc.stdout.readline()
                if linje:
                    linjer.append(linje.decode('ascii', errors='replace').strip())
            except Exception:
                break

        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()

    except PermissionError:
        log.error("Ingen tilgang - kjor som root")
        return None
    except Exception as e:
        log.error(f"Feil: {e}")
        return None

    log.info(f"Lest {len(linjer)} linjer")
    return linjer


def parse_usbmon_linje(linje):
    """Parse en usbmon tekst-linje.

    Format (tekst-API):
      URB_TAG TIMESTAMP EVENT_TYPE ADDRESS:BUS_ID EP_NUM STATUS LENGTH DATA_TAG = DATA

    Eksempel:
      ffff8880 3060475 S Ci:3:002:0 s 80 06 0100 0000 0012 18 <
      ffff8880 3060480 C Ci:3:002:0 0 18 = 12010002 00000040 ed1c0210 ...
    """
    if not linje:
        return None

    deler = linje.split()
    if len(deler) < 5:
        return None

    try:
        urb_tag = deler[0]
        timestamp = deler[1]
        event_type = deler[2]  # S=Submit, C=Complete, E=Error
        adresse = deler[3]     # Type:Bus:Dev:EP

        pakke = {
            "tid": timestamp,
            "hendelse": event_type,
            "adresse": adresse,
            "raa": linje,
        }

        # Parse adresse
        if ':' in adresse:
            addr_deler = adresse.split(':')
            if len(addr_deler) >= 4:
                pakke["transfer_type"] = addr_deler[0]  # Ci, Co, Bi, Bo, Ii, Io
                pakke["bus"] = addr_deler[1]
                pakke["dev"] = addr_deler[2]
                pakke["ep"] = addr_deler[3]

        # Finn data (etter '=' tegn)
        if '=' in linje:
            data_idx = linje.index('=') + 1
            data_hex = linje[data_idx:].strip().replace(' ', '')
            pakke["data"] = data_hex

        return pakke

    except Exception:
        return None


def analyser_fangst(pakker, dev_num=None):
    """Analyser fanget trafikk og vis protokollmoenstre."""
    if not pakker:
        print("  Ingen pakker aa analysere")
        return

    print(f"\n{'='*70}")
    print(f"  Trafikkanalyse ({len(pakker)} pakker)")
    print(f"{'='*70}")

    # Filtrer paa SIRIUS device_num hvis kjent
    if dev_num:
        sirius_pakker = [p for p in pakker if p.get("dev") == str(dev_num)]
        print(f"  SIRIUS-pakker (dev={dev_num}): {len(sirius_pakker)}")
    else:
        sirius_pakker = pakker

    if not sirius_pakker:
        print("  Ingen SIRIUS-pakker funnet")
        # Vis alle enheter
        enheter = Counter()
        for p in pakker:
            dev = p.get("dev", "?")
            enheter[dev] += 1
        print(f"  Enheter i fangsten: {dict(enheter)}")
        return

    # Grupper etter endepunkt
    ep_stats = defaultdict(lambda: {"count": 0, "bytes": 0, "eksempler": []})

    for p in sirius_pakker:
        ep = p.get("ep", "?")
        transfer = p.get("transfer_type", "?")
        hendelse = p.get("hendelse", "?")
        data = p.get("data", "")

        noekkel = f"{transfer}:EP{ep}"
        ep_stats[noekkel]["count"] += 1
        ep_stats[noekkel]["bytes"] += len(data) // 2 if data else 0

        if len(ep_stats[noekkel]["eksempler"]) < 10:
            ep_stats[noekkel]["eksempler"].append({
                "hendelse": hendelse,
                "data": data[:64] if data else "",
                "lengde": len(data) // 2 if data else 0,
            })

    print(f"\n  Per endepunkt:")
    print(f"  {'Endepunkt':<20s} {'Pakker':>8s} {'Bytes':>10s}")
    print(f"  {'-'*42}")

    for ep, stats in sorted(ep_stats.items()):
        print(f"  {ep:<20s} {stats['count']:>8d} {stats['bytes']:>10d}")

    # Vis eksempler per endepunkt
    for ep, stats in sorted(ep_stats.items()):
        if stats["eksempler"]:
            print(f"\n  {ep} - Eksempler:")
            for ex in stats["eksempler"][:5]:
                h = ex["hendelse"]
                d = ex["data"][:48] if ex["data"] else "(tom)"
                print(f"    [{h}] {ex['lengde']:>4d}B: {d}")

    return ep_stats


def analyser_raa_linjer(linjer, dev_filter=None):
    """Analyser raa usbmon-linjer."""
    if not linjer:
        print("  Ingen linjer aa analysere")
        return

    print(f"\n{'='*70}")
    print(f"  Raa usbmon-analyse ({len(linjer)} linjer)")
    print(f"{'='*70}")

    # Grupper og tell
    hendelser = Counter()
    endepunkter = Counter()
    data_pakker = []

    for linje in linjer:
        deler = linje.split()
        if len(deler) < 4:
            continue

        hendelse = deler[2] if len(deler) > 2 else "?"
        adresse = deler[3] if len(deler) > 3 else "?"

        hendelser[hendelse] += 1
        endepunkter[adresse] += 1

        # Filtrer paa device
        if dev_filter and f":{dev_filter}:" not in adresse:
            continue

        # Samle data-pakker
        if '=' in linje:
            data_pakker.append(linje)

    print(f"\n  Hendelsestyper: {dict(hendelser)}")
    print(f"\n  Topp 10 endepunkter:")
    for addr, count in endepunkter.most_common(10):
        print(f"    {addr}: {count}")

    if data_pakker:
        print(f"\n  Data-pakker med innhold ({len(data_pakker)} stk):")
        for p in data_pakker[:20]:
            # Begrens visning
            if len(p) > 120:
                print(f"    {p[:120]}...")
            else:
                print(f"    {p}")

    return data_pakker


def main():
    parser = argparse.ArgumentParser(
        description='SIRIUS USB Sniffer - Passiv trafikkfangst mens DewesoftX kommuniserer'
    )
    parser.add_argument('--varighet', type=float, default=30,
                        help='Fangstvarighet i sekunder (standard: 30)')
    parser.add_argument('--metode', choices=['usbmon', 'tcpdump', 'cat', 'auto'],
                        default='auto',
                        help='Fangstmetode (standard: auto)')
    parser.add_argument('--analyser', type=str, default=None,
                        help='Analyser en lagret fangstfil (JSON)')
    parser.add_argument('--debug', action='store_true')

    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    print(f"{'='*70}")
    print(f"  SIRIUS USB Sniffer (passiv)")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}")

    # Analyser eksisterende fil
    if args.analyser:
        with open(args.analyser) as f:
            data = json.load(f)
        if "pakker" in data:
            analyser_fangst(data["pakker"])
        elif "linjer" in data:
            analyser_raa_linjer(data["linjer"])
        return

    # Finn SIRIUS
    busid, busnum, devnum = finn_sirius_businfo()
    if not busid:
        print("\n  SIRIUS ikke funnet paa USB!")
        print("  Sjekk at SIRIUS er koblet til Pi med USB-kabel")
        sys.exit(1)

    print(f"\n  SIRIUS: busid={busid}, bus={busnum}, dev={devnum}")

    # Sjekk usbmon
    usbmon_ok = sjekk_usbmon()
    print(f"  usbmon: {'tilgjengelig' if usbmon_ok else 'IKKE tilgjengelig'}")

    if not usbmon_ok:
        print("\n  usbmon er noedvendig for passiv fangst.")
        print("  Paa Pi-hosten (ikke i Docker), kjor:")
        print("    sudo modprobe usbmon")
        print("    sudo mount -t debugfs none /sys/kernel/debug")
        print("\n  Docker-containeren trenger ogsaa tilgang:")
        print("    volumes:")
        print("      - /sys/kernel/debug:/sys/kernel/debug")
        print("  og 'privileged: true' i docker-compose.yml")
        sys.exit(1)

    # Fang trafikk
    print(f"\n  Starter passiv fangst i {args.varighet}s...")
    print(f"  (DewesoftX-kommunikasjon fortsetter uforstyrret)")
    print()

    metode = args.metode
    resultat = None

    if metode == 'auto':
        # Proov usbmon foerst, deretter cat, deretter tcpdump
        for m in ['usbmon', 'cat', 'tcpdump']:
            metode = m
            break

    if metode == 'usbmon':
        pakker = fang_med_usbmon(busnum or 0, args.varighet, devnum)
        if pakker:
            analyser_fangst(pakker, devnum)
            resultat = {"pakker": pakker}

    elif metode == 'cat':
        linjer = fang_med_cat(busnum or 0, args.varighet)
        if linjer:
            analyser_raa_linjer(linjer, str(devnum) if devnum else None)
            resultat = {"linjer": linjer}

    elif metode == 'tcpdump':
        pcap_fil = fang_med_tcpdump(busnum or 0, args.varighet)
        if pcap_fil:
            print(f"\n  Fangst lagret: {pcap_fil}")
            print(f"  Analyser med Wireshark eller:")
            print(f"    tshark -r {pcap_fil} -Y 'usb.idVendor == 0x1ced'")
            resultat = {"pcap_fil": pcap_fil}

    # Lagre resultat
    if resultat:
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        filnavn = f"sirius_sniffer_{ts}.json"
        with open(filnavn, 'w', encoding='utf-8') as f:
            json.dump({
                "tidspunkt": datetime.now().isoformat(),
                "varighet_sek": args.varighet,
                "metode": metode,
                "busid": busid,
                "busnum": busnum,
                "devnum": devnum,
                **resultat,
            }, f, indent=2)
        print(f"\n  Rapport: {filnavn}")

    print(f"\n  Ferdig!")


if __name__ == "__main__":
    main()

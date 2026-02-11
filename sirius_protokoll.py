#!/usr/bin/env python3
"""
SIRIUS Protokoll-utforsker
===========================
Basert paa probe-resultater fra sirius_usb_probe.py.

SIRIUS bruker IKKE Trenz TE-API. Den har sin egen proprietaere
pakkeprotokoll paa EP1 bulk-endepunktene:

Kjente observasjoner:
  - EP1 OUT (0x01): Kommandoer, 512B bulk
  - EP1 IN  (0x81): Svar, 512B bulk
  - EP2 IN  (0x82): ADC datastreaming (aktiv uten kommandoer)
  - EP4 IN  (0x84): Ukjent (ekstra endepunkt)
  - EP6 IN  (0x86): Synk/kontrolldata (repeterende moenstre)
  - EP8 OUT (0x08): Data til enhet

Svarmoenster:
  cmd 0x00 -> svar starter med 0x02 0x0B (info/versjon?)
  cmd 0xFF -> svar starter med 0x0C 0xA5 (lengde 12?)
  4x 0x00 -> strukturert svar: 00000000 13000000 045A0000

Maal: Kartlegge kommandosettet ved aa systematisk sende
      byte-verdier 0x00-0xFF og analysere svar.

Bruk:
  python3 sirius_protokoll.py --scan       # Skann alle kommandoer 0x00-0xFF
  python3 sirius_protokoll.py --stream     # Les datastroemmer
  python3 sirius_protokoll.py --cmd 0x00   # Send enkeltkommando
  python3 sirius_protokoll.py --capture N  # Fang N sekunder USB-trafikk
"""

import sys
import os
import json
import struct
import time
import argparse
import logging
from datetime import datetime
from collections import defaultdict

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger('sirius_proto')

DEWESOFT_VID = 0x1CED
SIRIUS_PID = 0x1002

# Endepunkter (bekreftet fra probe)
EP_CMD_OUT = 0x01
EP_CMD_IN = 0x81
EP_DATA_IN1 = 0x82  # ADC-data
EP_DATA_IN2 = 0x84  # Ukjent (ekstra)
EP_DATA_IN3 = 0x86  # Synk/kontrolldata
EP_DATA_OUT = 0x08

TIMEOUT_MS = 1000
TIMEOUT_SHORT_MS = 200


def koble_til_sirius():
    """Finn og konfigurer SIRIUS USB-enhet."""
    import usb.core
    import usb.util

    dev = usb.core.find(idVendor=DEWESOFT_VID, idProduct=SIRIUS_PID)
    if dev is None:
        log.error("SIRIUS ikke funnet paa USB")
        log.error("Sjekk at USB/IP-deling er stoppet og SIRIUS er tilkoblet")
        sys.exit(1)

    log.info(f"SIRIUS funnet: {DEWESOFT_VID:04x}:{SIRIUS_PID:04x}")

    try:
        if dev.is_kernel_driver_active(0):
            dev.detach_kernel_driver(0)
        dev.set_configuration()
    except usb.core.USBError as e:
        log.error(f"Kunne ikke konfigurere USB: {e}")
        sys.exit(1)

    # Les enhetsstrenger
    try:
        produsent = usb.util.get_string(dev, dev.iManufacturer)
        produkt = usb.util.get_string(dev, dev.iProduct)
        serienr = usb.util.get_string(dev, dev.iSerialNumber)
        log.info(f"  Produsent:    {produsent}")
        log.info(f"  Produkt:      {produkt}")
        log.info(f"  Serienummer:  {serienr}")
    except Exception:
        pass

    return dev


def send_og_les(dev, data, timeout=TIMEOUT_MS):
    """Send data paa EP1 OUT og les svar fra EP1 IN.

    Args:
        dev: USB-enhet
        data: bytes aa sende (paddes til 512)
        timeout: timeout i ms

    Returns:
        bytes: svar, eller None ved timeout/feil
    """
    import usb.core

    # Pad til 512 bytes (SIRIUS bruker full pakkestr.)
    pkt = bytearray(512)
    pkt[:len(data)] = data

    try:
        dev.write(EP_CMD_OUT, bytes(pkt), timeout=timeout)
    except usb.core.USBError as e:
        log.debug(f"Send feilet: {e}")
        return None

    try:
        svar = dev.read(EP_CMD_IN, 512, timeout=timeout)
        return bytes(svar)
    except usb.core.USBTimeoutError:
        return None
    except usb.core.USBError as e:
        log.debug(f"Les feilet: {e}")
        return None


def flush_ep(dev, ep, antall=5):
    """Les og forkast ventende data fra et endepunkt."""
    import usb.core

    for _ in range(antall):
        try:
            dev.read(ep, 512, timeout=50)
        except (usb.core.USBTimeoutError, usb.core.USBError):
            break


def skann_kommandoer(dev, start=0x00, slutt=0xFF):
    """Skann alle kommando-bytes og kartlegg svar.

    Sender byte 0x00-0xFF som foerste byte i en 512-byte pakke
    og logger hvilke som gir svar.
    """
    print(f"\n{'='*70}")
    print(f"  Kommandoskanning: 0x{start:02X} - 0x{slutt:02X}")
    print(f"{'='*70}")

    resultater = {}
    svar_grupper = defaultdict(list)  # Grupper kommandoer etter svarmoenster

    for cmd in range(start, slutt + 1):
        # Flush eventuelle ventende data
        flush_ep(dev, EP_CMD_IN, antall=2)

        # Send kommando
        svar = send_og_les(dev, bytes([cmd]), timeout=TIMEOUT_SHORT_MS)

        if svar:
            # Finn signifikant del (fjern trailing 0xFF)
            signifikant = svar.rstrip(b'\xff')
            if len(signifikant) == 0:
                # Alt 0xFF - kommandoen ble ikke gjenkjent
                resultater[cmd] = {"status": "ukjent", "svar": None}
                continue

            svar_hex = signifikant.hex()
            foerste_bytes = svar[:min(16, len(signifikant))]

            resultater[cmd] = {
                "status": "svar",
                "foerste_bytes": foerste_bytes.hex(),
                "lengde": len(signifikant),
                "raa": svar_hex[:64],  # Maks 32 bytes hex
            }

            # Grupper etter foerste svar-byte
            svar_grupper[svar[0]].append(cmd)

            print(f"  0x{cmd:02X} -> [{len(signifikant):3d}B] {foerste_bytes.hex()}")
        else:
            resultater[cmd] = {"status": "timeout", "svar": None}
            # Vis timeout kun for naermere analyse
            if cmd < 0x10 or cmd in [0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0xAF]:
                print(f"  0x{cmd:02X} -> TIMEOUT")

        # Liten pause mellom kommandoer
        time.sleep(0.02)

    # Oppsummering
    print(f"\n{'='*70}")
    print(f"  Skanneresultat")
    print(f"{'='*70}")

    svar_cmds = [c for c, r in resultater.items() if r["status"] == "svar"]
    timeout_cmds = [c for c, r in resultater.items() if r["status"] == "timeout"]
    ukjent_cmds = [c for c, r in resultater.items() if r["status"] == "ukjent"]

    print(f"  Kommandoer med svar:    {len(svar_cmds)}")
    print(f"  Kommandoer med timeout: {len(timeout_cmds)}")
    print(f"  Kommandoer med 0xFF:    {len(ukjent_cmds)}")

    if svar_cmds:
        print(f"\n  Kommandoer som ga svar:")
        for cmd in sorted(svar_cmds):
            r = resultater[cmd]
            print(f"    0x{cmd:02X}: [{r['lengde']:3d}B] {r['foerste_bytes']}")

    if svar_grupper:
        print(f"\n  Gruppert etter foerste svar-byte:")
        for fb, cmds in sorted(svar_grupper.items()):
            cmds_hex = ", ".join(f"0x{c:02X}" for c in cmds[:10])
            if len(cmds) > 10:
                cmds_hex += f" ... ({len(cmds)} totalt)"
            print(f"    Svar 0x{fb:02X}: {cmds_hex}")

    return resultater


def les_datastroemmer(dev, varighet_sek=3):
    """Les og analyser datastroemmer fra EP2, EP4 og EP6.

    Samler data i angitt varighet og viser statistikk.
    """
    import usb.core

    print(f"\n{'='*70}")
    print(f"  Datastroemanalyse ({varighet_sek} sekunder)")
    print(f"{'='*70}")

    endepunkter = [
        (EP_DATA_IN1, "EP2 IN (0x82) - ADC-data"),
        (EP_DATA_IN2, "EP4 IN (0x84) - Ukjent"),
        (EP_DATA_IN3, "EP6 IN (0x86) - Synk/kontroll"),
    ]

    for ep_addr, beskrivelse in endepunkter:
        print(f"\n  {beskrivelse}:")
        print(f"  {'-'*50}")

        data_buffer = bytearray()
        pakker = 0
        feil_count = 0
        start_tid = time.time()

        while time.time() - start_tid < varighet_sek:
            try:
                data = dev.read(ep_addr, 512, timeout=100)
                data_buffer.extend(data)
                pakker += 1
            except usb.core.USBTimeoutError:
                feil_count += 1
            except usb.core.USBError as e:
                feil_count += 1
                log.debug(f"  {beskrivelse}: {e}")

        elapsed = time.time() - start_tid
        total_bytes = len(data_buffer)
        rate_kbs = (total_bytes / 1024) / elapsed if elapsed > 0 else 0

        print(f"    Pakker:       {pakker}")
        print(f"    Total data:   {total_bytes} bytes ({total_bytes/1024:.1f} KB)")
        print(f"    Hastighet:    {rate_kbs:.1f} KB/s")
        print(f"    Timeouts:     {feil_count}")

        if total_bytes > 0:
            # Vis foerste og siste pakke
            print(f"    Foerste 32B:  {data_buffer[:32].hex()}")
            if total_bytes > 512:
                print(f"    Siste 32B:    {data_buffer[-32:].hex()}")

            # Analyser data-moenstre
            analyser_datastroem(data_buffer, beskrivelse)


def analyser_datastroem(data, beskrivelse):
    """Analyser datamoenstre i en stroem."""
    import numpy as np

    if len(data) < 16:
        return

    # Proov aa tolke som 16-bit signed samples (vanlig for ADC)
    if len(data) >= 2:
        samples_16 = np.frombuffer(bytes(data[:len(data) - len(data) % 2]),
                                    dtype=np.int16)
        if len(samples_16) > 0:
            print(f"\n    Tolket som int16 ({len(samples_16)} samples):")
            print(f"      Min:    {samples_16.min()}")
            print(f"      Max:    {samples_16.max()}")
            print(f"      Mean:   {samples_16.mean():.1f}")
            print(f"      Std:    {samples_16.std():.1f}")

            # Sjekk om det er reel signaldata (variasjon)
            if samples_16.std() > 10:
                print(f"      -> Ser ut som signaldata (variasjon > 10)")
            elif samples_16.std() < 1:
                print(f"      -> Ser ut som null/DC-data (nesten ingen variasjon)")

    # Sjekk for repeterende moenstre
    if len(data) >= 16:
        for moenster_len in [4, 8, 16, 32]:
            if len(data) >= moenster_len * 3:
                moenster = data[:moenster_len]
                repeats = True
                for i in range(moenster_len, min(len(data), moenster_len * 10), moenster_len):
                    if data[i:i+moenster_len] != moenster:
                        repeats = False
                        break
                if repeats:
                    print(f"      Repeterende {moenster_len}-byte moenster: {bytes(moenster).hex()}")
                    break

    # Sjekk for strukturerte pakker (header-moenstre)
    if len(data) >= 512:
        # Sammenlign starten av hver 512-byte blokk
        headers = []
        for i in range(0, min(len(data), 512*5), 512):
            if i + 8 <= len(data):
                headers.append(data[i:i+8])

        if len(headers) >= 2:
            like_headers = all(h[:2] == headers[0][:2] for h in headers)
            if like_headers:
                print(f"      Pakke-header moenster: {bytes(headers[0][:8]).hex()}")
            else:
                print(f"      Ulike pakke-headers:")
                for j, h in enumerate(headers[:3]):
                    print(f"        Pakke {j}: {bytes(h).hex()}")


def send_kommando(dev, cmd_bytes, beskrivelse=""):
    """Send en spesifikk kommando og vis svaret."""
    print(f"\n{'='*70}")
    print(f"  Kommando: {beskrivelse or cmd_bytes.hex()}")
    print(f"{'='*70}")

    # Flush
    flush_ep(dev, EP_CMD_IN, antall=3)

    print(f"  Sendt:   {cmd_bytes.hex()} ({len(cmd_bytes)} bytes, paddet til 512)")

    svar = send_og_les(dev, cmd_bytes, timeout=TIMEOUT_MS)

    if svar is None:
        print(f"  Svar:    TIMEOUT")
        return None

    signifikant = svar.rstrip(b'\xff')
    print(f"  Svar:    {signifikant.hex() if signifikant else '(bare 0xFF)'}")
    print(f"  Lengde:  {len(signifikant)} signifikante bytes (av {len(svar)})")

    if signifikant:
        # Vis som bytes
        print(f"\n  Byte-for-byte:")
        for i in range(0, min(len(signifikant), 64), 16):
            chunk = signifikant[i:i+16]
            hex_str = ' '.join(f'{b:02X}' for b in chunk)
            ascii_str = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
            print(f"    {i:4d}: {hex_str:<48s}  {ascii_str}")

        # Proov forskjellige tolkninger
        print(f"\n  Tolkninger:")
        if len(signifikant) >= 2:
            print(f"    u16 LE: {[struct.unpack_from('<H', signifikant, i)[0] for i in range(0, min(len(signifikant)-1, 16), 2)]}")
        if len(signifikant) >= 4:
            print(f"    u32 LE: {[struct.unpack_from('<I', signifikant, i)[0] for i in range(0, min(len(signifikant)-3, 16), 4)]}")

    return svar


def fang_trafikk(dev, varighet_sek=5):
    """Fang all USB-trafikk fra alle endepunkter samtidig.

    Lagrer til JSON-fil for analyse.
    """
    import usb.core

    print(f"\n{'='*70}")
    print(f"  USB-trafikkfangst ({varighet_sek} sekunder)")
    print(f"{'='*70}")

    endepunkter = {
        EP_CMD_IN: "cmd_svar",
        EP_DATA_IN1: "adc_data",
        EP_DATA_IN2: "ekstra_data",
        EP_DATA_IN3: "synk_data",
    }

    fangst = {navn: [] for navn in endepunkter.values()}
    start_tid = time.time()

    print(f"  Fanger trafikk fra {len(endepunkter)} endepunkter...")

    pakke_total = 0
    while time.time() - start_tid < varighet_sek:
        for ep_addr, navn in endepunkter.items():
            try:
                data = dev.read(ep_addr, 512, timeout=10)
                ts = time.time() - start_tid
                fangst[navn].append({
                    "tid": round(ts, 6),
                    "lengde": len(data),
                    "data": bytes(data).hex(),
                })
                pakke_total += 1
            except (usb.core.USBTimeoutError, usb.core.USBError):
                pass

    elapsed = time.time() - start_tid
    print(f"  Fanget {pakke_total} pakker paa {elapsed:.1f}s")

    # Oppsummering
    for navn, pakker in fangst.items():
        total_bytes = sum(p["lengde"] for p in pakker)
        print(f"    {navn}: {len(pakker)} pakker, {total_bytes} bytes")

    # Lagre til fil
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    filnavn = f"sirius_fangst_{ts}.json"
    with open(filnavn, 'w', encoding='utf-8') as f:
        json.dump({
            "tidspunkt": datetime.now().isoformat(),
            "varighet_sek": elapsed,
            "pakker_totalt": pakke_total,
            "fangst": fangst,
        }, f, indent=2)

    print(f"  Lagret: {filnavn}")
    return fangst


def utforsk_multibye_kommandoer(dev):
    """Test strukturerte kommandopakker basert paa observasjoner fra proben.

    Fra proben vet vi:
    - 4x null-byte ga strukturert svar: 00000000 13000000 045A0000
    - Dette tyder paa 32-bit word-basert protokoll
    """
    print(f"\n{'='*70}")
    print(f"  Multibyte-kommandoutforskning")
    print(f"{'='*70}")

    # Strukturerte pakker aa teste (32-bit word-basert)
    tester = [
        # (data, beskrivelse)
        (struct.pack('<I', 0x00000000), "Word 0x00000000"),
        (struct.pack('<I', 0x00000001), "Word 0x00000001"),
        (struct.pack('<I', 0x00000002), "Word 0x00000002"),
        (struct.pack('<I', 0x00000003), "Word 0x00000003"),
        (struct.pack('<I', 0x00000004), "Word 0x00000004"),
        (struct.pack('<I', 0x00000010), "Word 0x00000010"),
        (struct.pack('<I', 0x00000100), "Word 0x00000100"),
        (struct.pack('<I', 0x00001000), "Word 0x00001000"),
        # Doble words
        (struct.pack('<II', 0x00000000, 0x00000000), "2x Word null"),
        (struct.pack('<II', 0x00000001, 0x00000000), "Word 1 + null"),
        (struct.pack('<II', 0x00000000, 0x00000001), "Word null + 1"),
        # Mulige kommandostrukturer: [cmd] [lengde] [data...]
        (struct.pack('<BBH', 0x00, 0x00, 0x0000), "Byte cmd=0, len=0"),
        (struct.pack('<BBH', 0x01, 0x00, 0x0000), "Byte cmd=1, len=0"),
        (struct.pack('<BBH', 0x02, 0x00, 0x0000), "Byte cmd=2, len=0"),
        # Mulig: [cmd_16bit] [param_16bit]
        (struct.pack('<HH', 0x0000, 0x0000), "H: cmd=0, param=0"),
        (struct.pack('<HH', 0x0001, 0x0000), "H: cmd=1, param=0"),
        (struct.pack('<HH', 0x0100, 0x0000), "H: cmd=256, param=0"),
    ]

    resultater = []
    for data, beskrivelse in tester:
        flush_ep(dev, EP_CMD_IN, antall=2)
        svar = send_og_les(dev, data, timeout=TIMEOUT_SHORT_MS)

        if svar:
            signifikant = svar.rstrip(b'\xff')
            if len(signifikant) > 0:
                print(f"  {beskrivelse:30s} -> [{len(signifikant):3d}B] {signifikant[:16].hex()}")
                resultater.append((beskrivelse, data.hex(), signifikant.hex()))
            else:
                print(f"  {beskrivelse:30s} -> (bare 0xFF)")
        else:
            print(f"  {beskrivelse:30s} -> TIMEOUT")

        time.sleep(0.02)

    return resultater


def wireshark_instruksjoner():
    """Vis instruksjoner for aa fange USB-trafikk med Wireshark paa Windows."""
    print(f"""
{'='*70}
  USB-trafikkfangst med Wireshark (Windows)
{'='*70}

  For aa forstaa SIRIUS-protokollen fullt ut, fang trafikk
  mens DewesoftX kommuniserer med SIRIUS:

  1. Installer Wireshark + USBPcap:
     https://www.wireshark.org/download.html
     (Huk av for USBPcap under installasjonen)

  2. Koble SIRIUS til Windows-PC via USB

  3. Start Wireshark, velg "USBPcap" som capture interface

  4. Filtrer paa SIRIUS (VID 1ced):
     usb.idVendor == 0x1ced

  5. Start DewesoftX og la den koble til SIRIUS

  6. Observer initialiseringssekvensen:
     - Oppstarts-handshake
     - Kanalkonfigurasjon
     - Start av datastroemming

  7. Eksporter som JSON:
     File > Export Packet Dissections > As JSON

  Tips for analyse:
  - EP1 OUT/IN: Kommandoer og svar (kontrollplan)
  - EP2/EP4/EP6 IN: Datastroem (dataplan)
  - EP8 OUT: Konfigurasjon til enhet
  - Se etter moenstre i foerste bytes av hver pakke
  - Kommandoer sendes sannsynligvis som 32-bit words
""")


def main():
    parser = argparse.ArgumentParser(
        description='SIRIUS Protokoll-utforsker - Kartlegg Dewesoft USB-protokoll'
    )
    parser.add_argument('--scan', action='store_true',
                        help='Skann alle kommandoer 0x00-0xFF')
    parser.add_argument('--scan-range', type=str, default=None,
                        help='Skann et omraade, f.eks. "0x00-0x1F"')
    parser.add_argument('--stream', action='store_true',
                        help='Les og analyser datastroemmer')
    parser.add_argument('--stream-varighet', type=float, default=3.0,
                        help='Varighet for stroemmlesing (sekunder)')
    parser.add_argument('--cmd', type=str, default=None,
                        help='Send enkeltkommando (hex, f.eks. "00" eller "00010203")')
    parser.add_argument('--multi', action='store_true',
                        help='Test multibyte-kommandostrukturer')
    parser.add_argument('--capture', type=float, default=0,
                        help='Fang all USB-trafikk i N sekunder')
    parser.add_argument('--wireshark', action='store_true',
                        help='Vis Wireshark-instruksjoner')
    parser.add_argument('--full', action='store_true',
                        help='Kjor alle tester')
    parser.add_argument('--debug', action='store_true',
                        help='Vis debug-meldinger')

    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.wireshark:
        wireshark_instruksjoner()
        return

    # Sjekk at minst en modus er valgt
    if not any([args.scan, args.scan_range, args.stream, args.cmd,
                args.multi, args.capture, args.full]):
        parser.print_help()
        return

    print(f"{'='*70}")
    print(f"  SIRIUS Protokoll-utforsker")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}")

    dev = koble_til_sirius()

    alle_resultater = {}

    if args.cmd:
        cmd_bytes = bytes.fromhex(args.cmd.replace('0x', '').replace(' ', ''))
        send_kommando(dev, cmd_bytes, f"Bruker-kommando: {args.cmd}")

    if args.scan or args.full:
        alle_resultater["skanning"] = skann_kommandoer(dev)

    if args.scan_range:
        start_str, slutt_str = args.scan_range.split('-')
        start = int(start_str, 0)
        slutt = int(slutt_str, 0)
        alle_resultater["skanning"] = skann_kommandoer(dev, start, slutt)

    if args.multi or args.full:
        alle_resultater["multibyte"] = utforsk_multibye_kommandoer(dev)

    if args.stream or args.full:
        varighet = args.stream_varighet if not args.full else 2.0
        les_datastroemmer(dev, varighet)

    if args.capture > 0:
        alle_resultater["fangst"] = fang_trafikk(dev, args.capture)

    # Lagre samlet rapport
    if alle_resultater:
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        filnavn = f"sirius_protokoll_{ts}.json"

        # Konverter ikke-serialiserbare data
        def serialize(obj):
            if isinstance(obj, bytes):
                return obj.hex()
            if isinstance(obj, bytearray):
                return bytes(obj).hex()
            return str(obj)

        with open(filnavn, 'w', encoding='utf-8') as f:
            json.dump(alle_resultater, f, indent=2, default=serialize)
        print(f"\n  Rapport lagret: {filnavn}")

    # Frigjor USB
    import usb.util
    usb.util.dispose_resources(dev)

    print(f"\n  Ferdig!")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
SIRIUS Protokoll-dekoder
=========================
Basert paa funn fra protokoll-skanningen:

Kjente kommandoer (foerste byte = opkode, 512B bulk paa EP1):
  0xE3: Enhetsidentifikasjon ("DEWEUSB7" + serienummer)
  0xE4: Enhetstype (4 bytes)
  0xE5: HW-konfigurasjon (16 bytes)
  0xE6: HW-info (12 bytes)
  0xE7: Enhetsinfo (16 bytes)
  0xE8: (Tom slot, 12 bytes nullfylt)
  0xE9: Statusdata (8 bytes)
  0xEB: Statusdata med verdier (8 bytes)
  0xEC: Konfigurasjon (12 bytes)

Standard-svar (for ukjente kommandoer): 02 0B 01 01
  - 0x02: Svartype (status/klar)
  - 0x0B: Enhets-ID/klasse
  - 0x01 0x01: Firmwareversjon 1.1?

Endepunkter:
  EP1 OUT/IN (0x01/0x81): Kommandokanal (512B bulk)
  EP2 IN    (0x82): ADC-data (streames naa data er startet)
  EP4 IN    (0x84): Kontrolldata
  EP6 IN    (0x86): Synk/tidsstempel-data
  EP8 OUT   (0x08): Data/konfig til enhet

Bruk:
  python3 sirius_dekoder.py --info        # Les all enhetsinformasjon
  python3 sirius_dekoder.py --explore     # Utforsk payload-varianter
  python3 sirius_dekoder.py --stream      # Proov aa starte datastroemming
  python3 sirius_dekoder.py --wireshark   # Generer Wireshark-tips
"""

import sys
import os
import json
import struct
import time
import argparse
import logging
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger('sirius_dekoder')

DEWESOFT_VID = 0x1CED
SIRIUS_PID = 0x1002

EP_CMD_OUT = 0x01
EP_CMD_IN = 0x81
EP_DATA_IN1 = 0x82  # ADC
EP_DATA_IN2 = 0x84  # Kontroll
EP_DATA_IN3 = 0x86  # Synk
EP_DATA_OUT = 0x08

TIMEOUT_MS = 1000
TIMEOUT_SHORT = 300

# Kjente kommandoer
CMD_IDENT = 0xE3        # "DEWEUSB7" + serienummer
CMD_DEVICE_TYPE = 0xE4  # Enhetstype
CMD_HW_CONFIG = 0xE5    # HW-konfigurasjon
CMD_HW_INFO = 0xE6      # HW-info
CMD_DEVICE_INFO = 0xE7  # Enhetsinfo
CMD_SLOT_INFO = 0xE8    # Slot-info (tom?)
CMD_STATUS_A = 0xE9     # Status A
CMD_STATUS_B = 0xEB     # Status B
CMD_CONFIG = 0xEC       # Konfigurasjon

# Standardsvar (ukjent kommando)
DEFAULT_RESPONSE = bytes([0x02, 0x0B, 0x01, 0x01])


def koble_til():
    """Koble til SIRIUS."""
    import usb.core
    import usb.util

    dev = usb.core.find(idVendor=DEWESOFT_VID, idProduct=SIRIUS_PID)
    if dev is None:
        log.error("SIRIUS ikke funnet paa USB")
        sys.exit(1)

    try:
        if dev.is_kernel_driver_active(0):
            dev.detach_kernel_driver(0)
        dev.set_configuration()
    except usb.core.USBError as e:
        log.error(f"USB-feil: {e}")
        sys.exit(1)

    return dev


def send(dev, data, timeout=TIMEOUT_MS):
    """Send kommando og les svar."""
    import usb.core

    pkt = bytearray(512)
    if isinstance(data, int):
        pkt[0] = data
    else:
        pkt[:len(data)] = data

    # Flush
    for _ in range(3):
        try:
            dev.read(EP_CMD_IN, 512, timeout=20)
        except (usb.core.USBTimeoutError, usb.core.USBError):
            break

    try:
        dev.write(EP_CMD_OUT, bytes(pkt), timeout=timeout)
    except usb.core.USBError as e:
        return None

    try:
        svar = dev.read(EP_CMD_IN, 512, timeout=timeout)
        return bytes(svar)
    except (usb.core.USBTimeoutError, usb.core.USBError):
        return None


def er_standard_svar(svar):
    """Sjekk om svar er standard 020b0101-moenster."""
    if svar is None:
        return True
    signifikant = svar.rstrip(b'\xff')
    return signifikant == DEFAULT_RESPONSE


def dekod_ident(svar):
    """Dekod 0xE3 identifikasjonssvaret."""
    if svar is None or len(svar) < 16:
        return None

    signifikant = svar.rstrip(b'\xff')
    if len(signifikant) < 8:
        return None

    # Foerste 8 bytes = ASCII enhetsstreng
    enhet_str = signifikant[:8].decode('ascii', errors='replace').rstrip('\x00')

    # Bytes 8-15 = Serienummer (raa bytes)
    serial_raa = signifikant[8:16] if len(signifikant) >= 16 else b''
    # Serienummeret D019274CF6 finnes som: d0 19 27 4c f6 (noen steder i sekvensen)
    serial_hex = serial_raa.hex()

    return {
        "enhetsstreng": enhet_str,
        "serial_raa": serial_hex,
        "serial_bytes": [f"0x{b:02X}" for b in serial_raa],
    }


def dekod_enhetstype(svar):
    """Dekod 0xE4 enhetstype."""
    if svar is None:
        return None
    signifikant = svar.rstrip(b'\xff')
    if len(signifikant) < 4:
        return None

    val_le = struct.unpack_from('<I', signifikant, 0)[0]
    val_be = struct.unpack_from('>I', signifikant, 0)[0]

    return {
        "raa": signifikant[:4].hex(),
        "verdi_le": val_le,
        "verdi_be": val_be,
        "bytes": [f"0x{b:02X}" for b in signifikant[:4]],
    }


def dekod_generisk(svar, cmd_navn):
    """Dekod et generisk svar med multiple tolkninger."""
    if svar is None:
        return None

    signifikant = svar.rstrip(b'\xff')
    if len(signifikant) == 0:
        return None

    resultat = {
        "raa": signifikant.hex(),
        "lengde": len(signifikant),
        "bytes": [f"0x{b:02X}" for b in signifikant],
    }

    # 16-bit tolkninger
    if len(signifikant) >= 2:
        u16_le = []
        for i in range(0, len(signifikant) - 1, 2):
            u16_le.append(struct.unpack_from('<H', signifikant, i)[0])
        resultat["u16_le"] = u16_le

    # 32-bit tolkninger
    if len(signifikant) >= 4:
        u32_le = []
        for i in range(0, len(signifikant) - 3, 4):
            u32_le.append(struct.unpack_from('<I', signifikant, i)[0])
        resultat["u32_le"] = u32_le

    # ASCII-tolkning
    ascii_str = signifikant.decode('ascii', errors='replace')
    if any(c.isprintable() and c != '?' for c in ascii_str[:8]):
        resultat["ascii"] = ascii_str

    return resultat


def les_enhetsinformasjon(dev):
    """Les all kjent enhetsinformasjon."""
    print(f"\n{'='*70}")
    print(f"  SIRIUS Enhetsinformasjon")
    print(f"{'='*70}")

    # 0xE3: Identifikasjon
    print(f"\n  [0xE3] Identifikasjon:")
    svar = send(dev, CMD_IDENT)
    ident = dekod_ident(svar)
    if ident:
        print(f"    Enhetsstreng:  {ident['enhetsstreng']}")
        print(f"    Serial (hex):  {ident['serial_raa']}")
        print(f"    Serial bytes:  {' '.join(ident['serial_bytes'])}")
    else:
        print(f"    Feilet")

    # 0xE4: Enhetstype
    print(f"\n  [0xE4] Enhetstype:")
    svar = send(dev, CMD_DEVICE_TYPE)
    etype = dekod_enhetstype(svar)
    if etype:
        print(f"    Raa:           {etype['raa']}")
        print(f"    Verdi (LE):    {etype['verdi_le']} (0x{etype['verdi_le']:08X})")
        print(f"    Bytes:         {' '.join(etype['bytes'])}")

    # 0xE5 - 0xEC: Andre info-kommandoer
    info_cmds = [
        (CMD_HW_CONFIG, "HW-konfigurasjon"),
        (CMD_HW_INFO, "HW-info"),
        (CMD_DEVICE_INFO, "Enhetsinfo"),
        (CMD_SLOT_INFO, "Slot-info"),
        (CMD_STATUS_A, "Status A"),
        (CMD_STATUS_B, "Status B"),
        (CMD_CONFIG, "Konfigurasjon"),
    ]

    all_info = {}
    for cmd, navn in info_cmds:
        print(f"\n  [0x{cmd:02X}] {navn}:")
        svar = send(dev, cmd)
        info = dekod_generisk(svar, navn)
        if info:
            print(f"    Raa:    {info['raa']}")
            print(f"    Lengde: {info['lengde']} bytes")
            if 'u16_le' in info:
                print(f"    u16 LE: {info['u16_le']}")
            if 'u32_le' in info:
                print(f"    u32 LE: {info['u32_le']}")
            if 'ascii' in info and info['ascii'].isprintable():
                print(f"    ASCII:  {info['ascii']}")
            all_info[f"0x{cmd:02X}"] = info
        else:
            print(f"    (ingen data)")

    return ident, all_info


def utforsk_payload_varianter(dev):
    """Test kjente kommandoer med forskjellige payloads.

    Hypotese: Kommandoene 0xE3-0xEC kan vaere "les register"-type
    kommandoer der byte 1+ spesifiserer HVILKEN data som skal leses.
    """
    print(f"\n{'='*70}")
    print(f"  Payload-varianter for kjente kommandoer")
    print(f"{'='*70}")

    # Test kjente kommandoer med ulike parameter-bytes
    kjente_cmds = [CMD_IDENT, CMD_DEVICE_TYPE, CMD_HW_CONFIG, CMD_HW_INFO,
                   CMD_DEVICE_INFO, CMD_STATUS_A, CMD_STATUS_B, CMD_CONFIG]

    for cmd in kjente_cmds:
        cmd_navn = f"0x{cmd:02X}"
        print(f"\n  Kommando {cmd_navn} med varierende payload:")

        # Baseline (uten payload)
        baseline = send(dev, cmd)
        baseline_sig = baseline.rstrip(b'\xff').hex() if baseline else "TIMEOUT"
        print(f"    Baseline:      {baseline_sig[:40]}")

        # Varier byte 1
        varianter = [0x00, 0x01, 0x02, 0x03, 0x04, 0x10, 0x20, 0xFF]
        for val in varianter:
            data = bytes([cmd, val])
            svar = send(dev, data, timeout=TIMEOUT_SHORT)
            if svar:
                sig = svar.rstrip(b'\xff')
                if sig.hex() != baseline_sig:
                    print(f"    [byte1=0x{val:02X}]: {sig.hex()[:40]} <-- FORSKJELLIG!")
                else:
                    pass  # Samme som baseline, skip
            else:
                print(f"    [byte1=0x{val:02X}]: TIMEOUT")

        # Varier byte 2
        for val in [0x01, 0x02, 0x10]:
            data = bytes([cmd, 0x00, val])
            svar = send(dev, data, timeout=TIMEOUT_SHORT)
            if svar:
                sig = svar.rstrip(b'\xff')
                if sig.hex() != baseline_sig:
                    print(f"    [byte2=0x{val:02X}]: {sig.hex()[:40]} <-- FORSKJELLIG!")

        time.sleep(0.05)


def utforsk_mellomrom(dev):
    """Utforsk kommandoer rundt det kjente omraadet (0xE0-0xF0).

    Probe viste at 0xEA og 0xED-0xF1 ikke ga svar.
    La oss proeve mer grundig.
    """
    print(f"\n{'='*70}")
    print(f"  Utforskning av omraadet 0xE0-0xFF")
    print(f"{'='*70}")

    for cmd in range(0xE0, 0x100):
        # Test med ulike payloads
        for payload in [bytes([cmd]),
                        bytes([cmd, 0x01]),
                        bytes([cmd, 0x00, 0x01]),
                        bytes([cmd, 0x01, 0x00, 0x00, 0x00])]:
            svar = send(dev, payload, timeout=TIMEOUT_SHORT)
            if svar and not er_standard_svar(svar):
                sig = svar.rstrip(b'\xff')
                print(f"  0x{cmd:02X} payload={payload.hex():16s} -> [{len(sig):3d}B] {sig.hex()[:40]}")

        time.sleep(0.02)


def proev_start_streaming(dev):
    """Proov forskjellige kommandosekvenser for aa starte datastroemming.

    Hypoteser for aa starte ADC-stroemming:
    1. Send en spesifikk "start"-kommando
    2. Skriv til EP8 OUT for aa konfigurere
    3. En sekvens av kommandoer (init -> konfig -> start)
    """
    import usb.core

    print(f"\n{'='*70}")
    print(f"  Proev aa starte datastroemming")
    print(f"{'='*70}")

    # Foerst sjekk om data allerede stroemmer
    print(f"\n  [1] Sjekker om EP2 allerede streamer...")
    try:
        data = dev.read(EP_DATA_IN1, 512, timeout=500)
        print(f"      JA! {len(data)} bytes tilgjengelig")
        print(f"      Foerste 16B: {bytes(data[:16]).hex()}")
    except usb.core.USBTimeoutError:
        print(f"      Nei, ingen data")
    except usb.core.USBError as e:
        print(f"      Feil: {e}")

    # Proov mulige start-kommandosekvenser
    print(f"\n  [2] Proever start-kommandoer...")

    start_kandidater = [
        # Mulige "start acquisition"-kommandoer
        (bytes([0xE0]), "0xE0"),
        (bytes([0xE1]), "0xE1"),
        (bytes([0xE2]), "0xE2"),
        (bytes([0xEA]), "0xEA"),
        (bytes([0xED]), "0xED"),
        (bytes([0xEE]), "0xEE"),
        (bytes([0xEF]), "0xEF"),
        (bytes([0xF0]), "0xF0"),
        (bytes([0xF1]), "0xF1"),
        # Mulige start med parameter
        (bytes([0xE3, 0x01]), "0xE3 param=1"),
        (bytes([0xEC, 0x01]), "0xEC param=1"),
        (bytes([0xEC, 0x01, 0x00, 0x00, 0x01]), "0xEC start?"),
    ]

    for cmd_data, beskrivelse in start_kandidater:
        # Send kommando
        svar = send(dev, cmd_data, timeout=TIMEOUT_SHORT)
        svar_tekst = "standard" if er_standard_svar(svar) else (
            svar.rstrip(b'\xff').hex()[:32] if svar else "TIMEOUT"
        )

        # Sjekk om EP2 har data naa
        has_data = False
        try:
            data = dev.read(EP_DATA_IN1, 512, timeout=200)
            has_data = True
            data_hex = bytes(data[:16]).hex()
        except (usb.core.USBTimeoutError, usb.core.USBError):
            data_hex = "-"

        status = "DATA!" if has_data else "ingen"
        print(f"    {beskrivelse:20s} svar={svar_tekst:16s}  EP2={status} {data_hex if has_data else ''}")

        time.sleep(0.05)

    # Proov aa skrive til EP8 OUT
    print(f"\n  [3] Proever EP8 OUT (data til enhet)...")

    ep8_tester = [
        (bytes(512), "512 null-bytes"),
        (bytes([0x01]) + bytes(511), "0x01 + nuller"),
        (bytes([0x01, 0x00, 0x00, 0x00]) + bytes(508), "Word 0x00000001"),
        (bytes([0xE3]) + bytes(511), "0xE3 paa EP8"),
    ]

    for data, beskrivelse in ep8_tester:
        try:
            dev.write(EP_DATA_OUT, data, timeout=TIMEOUT_SHORT)
            print(f"    EP8 {beskrivelse:25s} -> OK (sendt)")

            # Sjekk om noe skjedde
            try:
                ep2_data = dev.read(EP_DATA_IN1, 512, timeout=200)
                print(f"       EP2 data: {bytes(ep2_data[:16]).hex()}")
            except (usb.core.USBTimeoutError, usb.core.USBError):
                pass

            # Les eventuelt kommandosvar
            try:
                ep1_svar = dev.read(EP_CMD_IN, 512, timeout=100)
                sig = bytes(ep1_svar).rstrip(b'\xff')
                if sig and sig != DEFAULT_RESPONSE:
                    print(f"       EP1 svar: {sig.hex()[:32]}")
            except (usb.core.USBTimeoutError, usb.core.USBError):
                pass

        except usb.core.USBError as e:
            print(f"    EP8 {beskrivelse:25s} -> FEIL: {e}")

        time.sleep(0.1)

    # Les alle endepunkter etter alle testene
    print(f"\n  [4] Endepunkt-status etter tester:")
    for ep, navn in [(EP_DATA_IN1, "EP2"), (EP_DATA_IN2, "EP4"), (EP_DATA_IN3, "EP6")]:
        try:
            data = dev.read(ep, 512, timeout=300)
            print(f"    {navn}: {len(data)} bytes - {bytes(data[:16]).hex()}")
        except usb.core.USBTimeoutError:
            print(f"    {navn}: ingen data")
        except usb.core.USBError as e:
            print(f"    {navn}: feil ({e})")


def utforsk_status_endring(dev):
    """Les statuskommandoer (0xE9, 0xEB) gjentatte ganger for aa se om de endrer seg."""
    print(f"\n{'='*70}")
    print(f"  Status-overvaakning (0xE9, 0xEB) - 10 lesinger")
    print(f"{'='*70}")

    for i in range(10):
        svar_e9 = send(dev, CMD_STATUS_A, timeout=TIMEOUT_SHORT)
        svar_eb = send(dev, CMD_STATUS_B, timeout=TIMEOUT_SHORT)

        e9_hex = svar_e9.rstrip(b'\xff').hex() if svar_e9 else "TIMEOUT"
        eb_hex = svar_eb.rstrip(b'\xff').hex() if svar_eb else "TIMEOUT"

        print(f"    [{i:2d}] E9={e9_hex:20s}  EB={eb_hex:20s}")
        time.sleep(0.5)


def generer_wireshark_filter():
    """Generer Wireshark display-filter for SIRIUS-analyse."""
    print(f"""
{'='*70}
  Wireshark USB-fangst for SIRIUS
{'='*70}

  For aa forstaa protokollen fullt ut, fang USB-trafikk paa Windows
  mens DewesoftX kommuniserer med SIRIUS.

  1. Installer Wireshark + USBPcap
  2. Start capture paa USBPcap-interface
  3. Start DewesoftX

  Nyttige display-filter:

  # Alle SIRIUS-pakker:
  usb.idVendor == 0x1ced

  # Kun kommandokanal (EP1):
  usb.endpoint_address == 0x01 || usb.endpoint_address == 0x81

  # Kun datastroemmer:
  usb.endpoint_address == 0x82 || usb.endpoint_address == 0x84 || usb.endpoint_address == 0x86

  # Kun skriving til enhet:
  usb.endpoint_address == 0x08

  # Kommandoer som ikke er "standard-svar":
  usb.endpoint_address == 0x81 && !(usb.capdata[0:4] == 02:0b:01:01)

  Hva aa se etter:
  - Oppstarts-sekvens (foerste 20-50 pakker)
  - Kommandoer paa EP1 OUT (0x01) -> dette er initialiseringen
  - Start av datastroemming -> naar EP2 begynner aa sende data
  - Konfigurasjonspakker paa EP8 OUT (0x08)

  Kjente kommandoer funnet saa langt:
    0xE3: Identifikasjon ("DEWEUSB7" + serial)
    0xE4: Enhetstype (0x00000904)
    0xE5: HW-konfig
    0xE6: HW-info
    0xE7: Enhetsinfo
    0xE9: Status A
    0xEB: Status B
    0xEC: Konfigurasjon

  Tips: Eksporter som JSON for enkel analyse:
    File > Export Packet Dissections > As JSON

  Eller bruk tshark:
    tshark -r capture.pcapng -Y "usb.idVendor == 0x1ced" \\
           -T fields -e frame.number -e usb.endpoint_address \\
           -e usb.data_len -e usb.capdata > sirius_trafikk.txt
""")


def main():
    parser = argparse.ArgumentParser(
        description='SIRIUS Protokoll-dekoder - Analyser Dewesoft kommandoer'
    )
    parser.add_argument('--info', action='store_true',
                        help='Les all enhetsinformasjon (0xE3-0xEC)')
    parser.add_argument('--explore', action='store_true',
                        help='Utforsk payload-varianter')
    parser.add_argument('--range', action='store_true',
                        help='Utforsk 0xE0-0xFF med ulike payloads')
    parser.add_argument('--stream', action='store_true',
                        help='Proov aa starte datastroemming')
    parser.add_argument('--status', action='store_true',
                        help='Overvaaak status-registre')
    parser.add_argument('--wireshark', action='store_true',
                        help='Vis Wireshark-filter og tips')
    parser.add_argument('--full', action='store_true',
                        help='Kjor alle tester')
    parser.add_argument('--cmd', type=str, default=None,
                        help='Send spesifikk kommando (hex)')
    parser.add_argument('--debug', action='store_true')

    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.wireshark:
        generer_wireshark_filter()
        return

    if not any([args.info, args.explore, args.range, args.stream,
                args.status, args.cmd, args.full]):
        parser.print_help()
        return

    print(f"{'='*70}")
    print(f"  SIRIUS Protokoll-dekoder")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}")

    dev = koble_til()
    rapport = {}

    if args.cmd:
        cmd_bytes = bytes.fromhex(args.cmd.replace('0x', '').replace(' ', ''))
        svar = send(dev, cmd_bytes)
        if svar:
            sig = svar.rstrip(b'\xff')
            print(f"\n  Kommando: {cmd_bytes.hex()}")
            print(f"  Svar:     {sig.hex() if sig else '(bare 0xFF)'}")
            if sig:
                for i in range(0, min(len(sig), 64), 16):
                    chunk = sig[i:i+16]
                    hex_str = ' '.join(f'{b:02X}' for b in chunk)
                    ascii_str = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
                    print(f"    {i:4d}: {hex_str:<48s}  {ascii_str}")
        else:
            print(f"\n  Kommando: {cmd_bytes.hex()} -> TIMEOUT")

    if args.info or args.full:
        ident, info = les_enhetsinformasjon(dev)
        rapport["info"] = info

    if args.explore or args.full:
        utforsk_payload_varianter(dev)

    if args.range or args.full:
        utforsk_mellomrom(dev)

    if args.stream or args.full:
        proev_start_streaming(dev)

    if args.status or args.full:
        utforsk_status_endring(dev)

    # Lagre rapport
    if rapport:
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        filnavn = f"sirius_dekoder_{ts}.json"
        with open(filnavn, 'w', encoding='utf-8') as f:
            json.dump(rapport, f, indent=2, default=str)
        print(f"\n  Rapport: {filnavn}")

    import usb.util
    usb.util.dispose_resources(dev)
    print(f"\n  Ferdig!")


if __name__ == "__main__":
    main()

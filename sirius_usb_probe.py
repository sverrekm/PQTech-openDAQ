#!/usr/bin/env python3
"""
SIRIUS USB Probe - Direkte USB-kommunikasjon med Dewesoft SIRIUS
================================================================
Bruker pyusb/libusb til aa kommunisere direkte med SIRIUS via USB.
Basert paa Trenz Electronic TE-USB-Suite (FX2LP/CY7C68013A).

SIRIUS bruker en Cypress EZ-USB FX2LP som USB-kontroller.
Kjent endepunkt-layout (fra Trenz TE-API):
  EP1 OUT (0x01): Kommandoer til FPGA (64 bytes maks)
  EP1 IN  (0x81): Svar fra FPGA (64 bytes maks)
  EP2 IN  (0x82): Data-streaming fra FPGA (512 bytes)
  EP6 IN  (0x86): Alternativ data-streaming (512 bytes)
  EP8 OUT (0x08): Data til FPGA (512 bytes)

Kjente kommandoer (Trenz TE-API):
  0x00: READ_VERSION
  0xA1: READ_STATUS
  0xA2: WRITE_REGISTER
  0xA3: READ_REGISTER
  0xA4: READ_DATA
  0xA5: WRITE_DATA
  0xAF: RESET

Bruk:
  python3 sirius_usb_probe.py           # Full probe
  python3 sirius_usb_probe.py --info    # Kun USB-deskriptorer
  python3 sirius_usb_probe.py --test    # Test kommandokanal
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
log = logging.getLogger('sirius_probe')

# Dewesoft USB-identifikatorer
DEWESOFT_VID = 0x1CED
SIRIUS_PID = 0x1002
BOX_POWER_PID = 0x2000
BATTERY_PID = 0x3001

DEWESOFT_PIDS = {
    SIRIUS_PID: "SIRIUS",
    BOX_POWER_PID: "BOX Power",
    BATTERY_PID: "Battery Pack",
}

# FX2 Endepunkter (Trenz TE-API)
EP_CMD_OUT = 0x01   # Kommandoer til enhet (bulk, 64B)
EP_CMD_IN = 0x81    # Svar fra enhet (bulk, 64B)
EP_DATA_IN1 = 0x82  # Data-streaming (bulk, 512B)
EP_DATA_IN2 = 0x86  # Alternativ data (bulk, 512B)
EP_DATA_OUT = 0x08  # Data til enhet (bulk, 512B)

# Trenz TE-API Kommandoer
CMD_READ_VERSION = 0x00
CMD_READ_STATUS = 0xA1
CMD_WRITE_REGISTER = 0xA2
CMD_READ_REGISTER = 0xA3
CMD_READ_DATA = 0xA4
CMD_WRITE_DATA = 0xA5
CMD_RESET = 0xAF

# FX2 Vendor Requests (kontrolloverforinger)
FX2_VENDOR_FIRMWARE_LOAD = 0xA0  # Cypress standard
FX2_VENDOR_RW_INTERNAL = 0xA0   # Read/Write intern RAM

# Timeout i millisekunder
TIMEOUT_MS = 2000


def finn_dewesoft_enheter():
    """Finn alle Dewesoft USB-enheter."""
    try:
        import usb.core
        import usb.util
    except ImportError:
        log.error("pyusb ikke installert. Kjor: pip install pyusb")
        sys.exit(1)

    enheter = []
    for pid, navn in DEWESOFT_PIDS.items():
        devs = usb.core.find(find_all=True, idVendor=DEWESOFT_VID, idProduct=pid)
        for dev in devs:
            enheter.append({
                "enhet": dev,
                "vid": DEWESOFT_VID,
                "pid": pid,
                "navn": navn,
            })

    return enheter


def vis_usb_deskriptorer(dev):
    """Vis detaljerte USB-deskriptorer for enheten."""
    import usb.util

    print(f"\n{'='*60}")
    print(f"  USB Enhetsdeskriptor")
    print(f"{'='*60}")
    print(f"  VID:PID:         {dev.idVendor:04x}:{dev.idProduct:04x}")
    print(f"  USB-versjon:     {(dev.bcdUSB >> 8) & 0xFF}.{(dev.bcdUSB >> 4) & 0xF}{dev.bcdUSB & 0xF}")
    print(f"  Enhetsklasse:    0x{dev.bDeviceClass:02X} ({usb_klasse_navn(dev.bDeviceClass)})")
    print(f"  Underklasse:     0x{dev.bDeviceSubClass:02X}")
    print(f"  Protokoll:       0x{dev.bDeviceProtocol:02X}")
    print(f"  Max pakke EP0:   {dev.bMaxPacketSize0}")
    print(f"  Konfigurasjoner: {dev.bNumConfigurations}")

    # Proov aa lese strenger
    try:
        print(f"  Produsent:       {usb.util.get_string(dev, dev.iManufacturer)}")
    except Exception:
        print(f"  Produsent:       (indeks {dev.iManufacturer}, kunne ikke lese)")

    try:
        print(f"  Produkt:         {usb.util.get_string(dev, dev.iProduct)}")
    except Exception:
        print(f"  Produkt:         (indeks {dev.iProduct}, kunne ikke lese)")

    try:
        print(f"  Serienummer:     {usb.util.get_string(dev, dev.iSerialNumber)}")
    except Exception:
        print(f"  Serienummer:     (indeks {dev.iSerialNumber}, kunne ikke lese)")

    # Konfigurasjoner og endepunkter
    for cfg in dev:
        print(f"\n  Konfigurasjon {cfg.bConfigurationValue}:")
        print(f"    Antall grensesnitt: {cfg.bNumInterfaces}")

        for intf in cfg:
            print(f"\n    Grensesnitt {intf.bInterfaceNumber} (alt {intf.bAlternateSetting}):")
            print(f"      Klasse:      0x{intf.bInterfaceClass:02X} ({usb_klasse_navn(intf.bInterfaceClass)})")
            print(f"      Underklasse: 0x{intf.bInterfaceSubClass:02X}")
            print(f"      Protokoll:   0x{intf.bInterfaceProtocol:02X}")
            print(f"      Endepunkter: {intf.bNumEndpoints}")

            for ep in intf:
                retning = "IN" if usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_IN else "OUT"
                ep_type = usb.util.endpoint_type(ep.bmAttributes)
                type_navn = {0: "Control", 1: "Isochronous", 2: "Bulk", 3: "Interrupt"}.get(ep_type, "Ukjent")

                print(f"\n      Endepunkt 0x{ep.bEndpointAddress:02X} ({retning}):")
                print(f"        Type:       {type_navn}")
                print(f"        Max pakke:  {ep.wMaxPacketSize}")
                print(f"        Intervall:  {ep.bInterval}")

                # Sammenlign med forventede endepunkter
                forventet = {
                    EP_CMD_OUT: "Kommandokanal (Trenz EP1 OUT)",
                    EP_CMD_IN: "Svarkanal (Trenz EP1 IN)",
                    EP_DATA_IN1: "Datastreaming (Trenz EP2 IN)",
                    EP_DATA_IN2: "Datastreaming alt (Trenz EP6 IN)",
                    EP_DATA_OUT: "Data til enhet (Trenz EP8 OUT)",
                }
                if ep.bEndpointAddress in forventet:
                    print(f"        Forventet:  {forventet[ep.bEndpointAddress]}")


def usb_klasse_navn(klasse):
    """Konverter USB-klassekode til navn."""
    klasser = {
        0x00: "Definert per grensesnitt",
        0x01: "Audio",
        0x02: "CDC",
        0x03: "HID",
        0x05: "Physical",
        0x06: "Image",
        0x07: "Printer",
        0x08: "Mass Storage",
        0x09: "Hub",
        0x0A: "CDC-Data",
        0x0B: "Smart Card",
        0x0D: "Content Security",
        0x0E: "Video",
        0x0F: "Personal Healthcare",
        0x10: "Audio/Video",
        0xDC: "Diagnostic",
        0xE0: "Wireless",
        0xEF: "Miscellaneous",
        0xFE: "Application Specific",
        0xFF: "Vendor Specific",
    }
    return klasser.get(klasse, f"Ukjent (0x{klasse:02X})")


def bygg_kommando(cmd_id, data=None):
    """Bygg en kommandopakke for FX2 bulk-overforing (Trenz TE-API format).

    Format (Trenz Gen 2):
      Byte 0:    Kommando-ID
      Byte 1-3:  Lengde (24-bit, little-endian) - lengde paa data som folger
      Byte 4-63: Data (valgfritt)
    """
    if data is None:
        data = b''

    pkt = bytearray(64)
    pkt[0] = cmd_id
    data_len = len(data)
    pkt[1] = data_len & 0xFF
    pkt[2] = (data_len >> 8) & 0xFF
    pkt[3] = (data_len >> 16) & 0xFF
    pkt[4:4 + data_len] = data
    return bytes(pkt)


def send_kommando(dev, cmd_id, data=None, forvent_svar=True):
    """Send en kommando til SIRIUS via EP1 og les eventuelt svar.

    Returnerer svar-bytes eller None ved feil.
    """
    pkt = bygg_kommando(cmd_id, data)
    cmd_navn = {
        CMD_READ_VERSION: "READ_VERSION",
        CMD_READ_STATUS: "READ_STATUS",
        CMD_WRITE_REGISTER: "WRITE_REGISTER",
        CMD_READ_REGISTER: "READ_REGISTER",
        CMD_READ_DATA: "READ_DATA",
        CMD_WRITE_DATA: "WRITE_DATA",
        CMD_RESET: "RESET",
    }.get(cmd_id, f"0x{cmd_id:02X}")

    log.info(f"Sender kommando: {cmd_navn} ({len(pkt)} bytes)")
    log.debug(f"  Data: {pkt[:16].hex()}")

    try:
        skrevet = dev.write(EP_CMD_OUT, pkt, timeout=TIMEOUT_MS)
        log.info(f"  Sendt: {skrevet} bytes")
    except Exception as e:
        log.error(f"  Sending feilet: {e}")
        return None

    if not forvent_svar:
        return b''

    try:
        svar = dev.read(EP_CMD_IN, 64, timeout=TIMEOUT_MS)
        svar_bytes = bytes(svar)
        log.info(f"  Mottatt: {len(svar_bytes)} bytes")
        log.debug(f"  Svar: {svar_bytes[:32].hex()}")
        return svar_bytes
    except Exception as e:
        log.warning(f"  Ingen svar (timeout/feil): {e}")
        return None


def test_vendor_request(dev):
    """Test FX2 vendor control requests (standard Cypress kontrolloverforinger)."""
    import usb.core
    import usb.util

    print(f"\n{'='*60}")
    print(f"  FX2 Vendor Control Requests")
    print(f"{'='*60}")

    # Les FX2 intern RAM (adresse 0xE600 = CPUCS register)
    # Hvis dette er en ekte FX2, bor den svare paa dette
    tests = [
        # (bmRequestType, bRequest, wValue, wIndex, wLength, beskrivelse)
        (0xC0, 0xA0, 0xE600, 0, 1, "FX2 CPUCS register (CPU kontrollstatus)"),
        (0xC0, 0xA0, 0xE601, 0, 3, "FX2 IFCONFIG + flagg"),
        (0xC0, 0xA0, 0xE680, 0, 2, "FX2 EP1 config"),
    ]

    for bmReqType, bReq, wVal, wIdx, wLen, beskrivelse in tests:
        try:
            result = dev.ctrl_transfer(bmReqType, bReq, wVal, wIdx, wLen, timeout=TIMEOUT_MS)
            data = bytes(result)
            print(f"\n  {beskrivelse}:")
            print(f"    Adresse:  0x{wVal:04X}")
            print(f"    Data:     {data.hex()}")
            print(f"    Verdier:  {[f'0x{b:02X}' for b in data]}")
        except usb.core.USBError as e:
            print(f"\n  {beskrivelse}:")
            print(f"    Feilet: {e}")
            if "pipe" in str(e).lower() or "stall" in str(e).lower():
                print(f"    (STALL - enheten stotter ikke denne foresporselen)")


def test_kommandokanal(dev):
    """Test kommandokanalen med kjente Trenz TE-API kommandoer."""
    print(f"\n{'='*60}")
    print(f"  Kommandokanal-test (EP1 bulk)")
    print(f"{'='*60}")

    resultater = {}

    # Test 1: READ_VERSION (0x00)
    print(f"\n  [Test 1] READ_VERSION (0x00):")
    svar = send_kommando(dev, CMD_READ_VERSION)
    if svar:
        print(f"    Raaadata: {svar.hex()}")
        # Trenz format: svar[0] = cmd echo, svar[1..] = versjondata
        if len(svar) >= 4:
            print(f"    Byte 0-3: {[f'0x{b:02X}' for b in svar[:4]]}")
            # Proov aa tolke som versjonsnummer
            if svar[0] == CMD_READ_VERSION:
                print(f"    Kommando-echo OK (0x{svar[0]:02X})")
                print(f"    Versjonsdata: {svar[1:8].hex()}")
        resultater["READ_VERSION"] = svar.hex()
    else:
        resultater["READ_VERSION"] = None
        print(f"    Ingen svar")

    # Test 2: READ_STATUS (0xA1)
    print(f"\n  [Test 2] READ_STATUS (0xA1):")
    svar = send_kommando(dev, CMD_READ_STATUS)
    if svar:
        print(f"    Raadata: {svar.hex()}")
        if len(svar) >= 4:
            print(f"    Byte 0-3: {[f'0x{b:02X}' for b in svar[:4]]}")
            if svar[0] == CMD_READ_STATUS:
                print(f"    Kommando-echo OK (0x{svar[0]:02X})")
                status_byte = svar[1] if len(svar) > 1 else 0
                print(f"    Status-byte: 0x{status_byte:02X} ({status_byte:08b}b)")
        resultater["READ_STATUS"] = svar.hex()
    else:
        resultater["READ_STATUS"] = None
        print(f"    Ingen svar")

    # Test 3: READ_REGISTER (0xA3) - les register 0
    print(f"\n  [Test 3] READ_REGISTER (0xA3) - register 0x0000:")
    reg_data = struct.pack('<H', 0x0000)  # Register-adresse 0, little-endian
    svar = send_kommando(dev, CMD_READ_REGISTER, data=reg_data)
    if svar:
        print(f"    Raadata: {svar.hex()}")
        if len(svar) >= 4:
            print(f"    Byte 0-3: {[f'0x{b:02X}' for b in svar[:4]]}")
        resultater["READ_REGISTER_0"] = svar.hex()
    else:
        resultater["READ_REGISTER_0"] = None
        print(f"    Ingen svar")

    return resultater


def test_alternativ_protokoll(dev):
    """Test alternative kommandoformater (SIRIUS bruker kanskje ikke ren Trenz-protokoll)."""
    import usb.core

    print(f"\n{'='*60}")
    print(f"  Alternative protokoll-tester")
    print(f"{'='*60}")

    # Test: Send raa byte-sekvenser paa EP1 og se hva som skjer
    tester = [
        # Enkelt-byte kommandoer
        (bytes([0x00]), "Null-byte"),
        (bytes([0x01]), "Byte 0x01"),
        (bytes([0xFF]), "Byte 0xFF"),
        # Mulige Dewesoft-spesifikke kommandoer
        (b"ID?\n", "ASCII ID-forespoorsel"),
        (b"*IDN?\n", "SCPI identifikasjon"),
        (b"\x00\x00\x00\x00", "Fire null-bytes"),
    ]

    for data, beskrivelse in tester:
        print(f"\n  Sender: {beskrivelse} ({data.hex()})")
        try:
            pkt = bytearray(64)
            pkt[:len(data)] = data
            skrevet = dev.write(EP_CMD_OUT, bytes(pkt), timeout=1000)
            print(f"    Sendt: {skrevet} bytes")

            try:
                svar = dev.read(EP_CMD_IN, 64, timeout=1000)
                svar_bytes = bytes(svar)
                print(f"    Svar ({len(svar_bytes)} bytes): {svar_bytes[:16].hex()}")
                # Proov ASCII-tolkning
                ascii_svar = svar_bytes.rstrip(b'\x00').decode('ascii', errors='replace')
                if ascii_svar.isprintable() and len(ascii_svar) > 0:
                    print(f"    ASCII: {ascii_svar}")
            except usb.core.USBTimeoutError:
                print(f"    Timeout (inget svar)")
            except usb.core.USBError as e:
                print(f"    Feil: {e}")

        except usb.core.USBTimeoutError:
            print(f"    Timeout ved sending")
        except usb.core.USBError as e:
            print(f"    Feil: {e}")


def proober_endepunkter(dev):
    """Proov aa lese fra alle IN-endepunkter for aa se om det er data tilgjengelig."""
    import usb.core

    print(f"\n{'='*60}")
    print(f"  Endepunkt-probing")
    print(f"{'='*60}")

    ep_liste = [
        (EP_CMD_IN, "EP1 IN (kommando-svar)"),
        (EP_DATA_IN1, "EP2 IN (data stream 1)"),
        (EP_DATA_IN2, "EP6 IN (data stream 2)"),
    ]

    for ep_addr, beskrivelse in ep_liste:
        print(f"\n  {beskrivelse} (0x{ep_addr:02X}):")
        try:
            data = dev.read(ep_addr, 512, timeout=500)
            data_bytes = bytes(data)
            print(f"    Data tilgjengelig: {len(data_bytes)} bytes")
            print(f"    Forste bytes: {data_bytes[:32].hex()}")
        except usb.core.USBTimeoutError:
            print(f"    Ingen data (timeout)")
        except usb.core.USBError as e:
            print(f"    Feil: {e}")


def lagre_rapport(enhetsinfo, deskriptorer, kommando_resultater):
    """Lagre probe-rapport til JSON-fil."""
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    filnavn = f"sirius_probe_{ts}.json"

    rapport = {
        "tidspunkt": datetime.now().isoformat(),
        "enhet": enhetsinfo,
        "kommando_resultater": kommando_resultater,
    }

    with open(filnavn, 'w', encoding='utf-8') as f:
        json.dump(rapport, f, indent=2, ensure_ascii=False)

    log.info(f"Rapport lagret: {filnavn}")
    return filnavn


def main():
    parser = argparse.ArgumentParser(
        description='SIRIUS USB Probe - Direkte USB-kommunikasjon med Dewesoft SIRIUS'
    )
    parser.add_argument(
        '--info', action='store_true',
        help='Vis kun USB-deskriptorer (ingen kommandoer)'
    )
    parser.add_argument(
        '--test', action='store_true',
        help='Test kommandokanal med Trenz TE-API kommandoer'
    )
    parser.add_argument(
        '--alt', action='store_true',
        help='Test alternative protokoller'
    )
    parser.add_argument(
        '--full', action='store_true',
        help='Kjor full probe (alle tester)'
    )
    parser.add_argument(
        '--debug', action='store_true',
        help='Vis debug-meldinger'
    )
    parser.add_argument(
        '--pid', type=lambda x: int(x, 0), default=SIRIUS_PID,
        help=f'USB Product ID (standard: 0x{SIRIUS_PID:04x})'
    )

    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    # Hvis ingen modus er valgt, kjor full probe
    if not any([args.info, args.test, args.alt]):
        args.full = True

    print("=" * 60)
    print("  SIRIUS USB Probe")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # 1. Finn enheter
    log.info("Soker etter Dewesoft USB-enheter...")
    enheter = finn_dewesoft_enheter()

    if not enheter:
        print("\n  INGEN Dewesoft-enheter funnet!")
        print("  Sjekk at:")
        print("    - SIRIUS er koblet til med USB-kabel")
        print("    - udev-regler er installert (99-dewesoft.rules)")
        print("    - USB-enheten ikke er bundet til usbip (stopp deling forst)")
        print("\n  Tips: Kjor 'lsusb | grep 1ced' for aa verifisere")
        sys.exit(1)

    print(f"\n  Funnet {len(enheter)} Dewesoft-enhet(er):")
    for e in enheter:
        print(f"    {e['navn']} (VID:PID = {e['vid']:04x}:{e['pid']:04x})")

    # Velg enhet
    dev_info = None
    for e in enheter:
        if e['pid'] == args.pid:
            dev_info = e
            break

    if not dev_info:
        dev_info = enheter[0]
        log.info(f"PID 0x{args.pid:04x} ikke funnet, bruker {dev_info['navn']}")

    dev = dev_info['enhet']
    print(f"\n  Bruker: {dev_info['navn']}")

    # 2. Konfigurer USB-enhet
    import usb.core
    import usb.util

    try:
        # Sjekk og frigjor fra kernel-driver
        if dev.is_kernel_driver_active(0):
            log.info("Frigjor enhet fra kernel-driver...")
            dev.detach_kernel_driver(0)

        dev.set_configuration()
        log.info("USB-konfigurasjon satt")
    except usb.core.USBError as e:
        if "resource busy" in str(e).lower() or "access" in str(e).lower():
            log.warning(f"Kunne ikke konfigurere: {e}")
            log.warning("Proov: stopp usbip-deling, eller kjor som root")
        else:
            log.error(f"USB-feil: {e}")
            sys.exit(1)

    # Hent konfigurasjonsinfo
    enhetsinfo = {
        "vid": f"0x{dev.idVendor:04x}",
        "pid": f"0x{dev.idProduct:04x}",
        "navn": dev_info['navn'],
        "usb_versjon": f"{(dev.bcdUSB >> 8) & 0xFF}.{(dev.bcdUSB >> 4) & 0xF}",
        "klasse": f"0x{dev.bDeviceClass:02X}",
    }
    try:
        enhetsinfo["produsent"] = usb.util.get_string(dev, dev.iManufacturer)
    except Exception:
        enhetsinfo["produsent"] = None
    try:
        enhetsinfo["produkt"] = usb.util.get_string(dev, dev.iProduct)
    except Exception:
        enhetsinfo["produkt"] = None
    try:
        enhetsinfo["serienummer"] = usb.util.get_string(dev, dev.iSerialNumber)
    except Exception:
        enhetsinfo["serienummer"] = None

    kommando_resultater = {}

    # 3. Kjor tester
    if args.info or args.full:
        vis_usb_deskriptorer(dev)

    if args.full:
        test_vendor_request(dev)

    if args.test or args.full:
        kommando_resultater = test_kommandokanal(dev)

    if args.alt or args.full:
        test_alternativ_protokoll(dev)

    if args.full:
        proober_endepunkter(dev)

    # 4. Lagre rapport
    rapport_fil = lagre_rapport(enhetsinfo, None, kommando_resultater)

    # 5. Oppsummering
    print(f"\n{'='*60}")
    print(f"  Oppsummering")
    print(f"{'='*60}")
    print(f"  Enhet:         {dev_info['navn']}")
    print(f"  VID:PID:       {dev.idVendor:04x}:{dev.idProduct:04x}")
    print(f"  Produsent:     {enhetsinfo.get('produsent', 'ukjent')}")
    print(f"  Produkt:       {enhetsinfo.get('produkt', 'ukjent')}")
    print(f"  Serienummer:   {enhetsinfo.get('serienummer', 'ukjent')}")
    print(f"  Rapport:       {rapport_fil}")

    if kommando_resultater:
        print(f"\n  Kommandoresultater:")
        for cmd, res in kommando_resultater.items():
            status = "SVAR" if res else "INGEN SVAR"
            print(f"    {cmd}: {status}")

    print(f"\n  Neste steg:")
    print(f"    1. Analyser rapporten ({rapport_fil})")
    print(f"    2. Sammenlign endepunkter med Trenz TE-API")
    print(f"    3. Hvis kommandoer svarer: bygg openDAQ-modul")
    print(f"    4. Hvis ikke: fang USB-trafikk med Wireshark/USBPcap")
    print()

    # Frigjor USB-enhet
    usb.util.dispose_resources(dev)


if __name__ == "__main__":
    main()

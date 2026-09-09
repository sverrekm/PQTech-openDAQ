#!/usr/bin/env python3
"""Fase 2: Parse EEPROM-data (A8-svar) og register 0x15 frå pcapng-filer.

Leita etter TEDS-data som kan vere innbaka i EEPROM eller i ukjende register.
Sjekk også sirius3-6.pcapng for andre kommandotypar.
"""
import struct
from collections import defaultdict

def parse_pcapng(filepath):
    """Parse pcapng og returner liste av pakkar."""
    packets = []
    with open(filepath, 'rb') as f:
        raw = f.read()

    pos = 0
    pkt_idx = 0
    while pos < len(raw) - 12:
        block_type = struct.unpack_from('<I', raw, pos)[0]
        block_len = struct.unpack_from('<I', raw, pos + 4)[0]
        if block_len < 12 or pos + block_len > len(raw):
            break

        if block_type == 0x00000006:
            if block_len > 28 + 27:
                epb_data_start = pos + 28
                cap_len = struct.unpack_from('<I', raw, pos + 20)[0]

                if cap_len >= 27:
                    usb_start = epb_data_start
                    hdr_len = struct.unpack_from('<H', raw, usb_start)[0]

                    if 27 <= hdr_len <= 64 and usb_start + hdr_len + 4 <= len(raw):
                        irp_id = struct.unpack_from('<Q', raw, usb_start + 2)[0]
                        info = raw[usb_start + 16]
                        endpoint = raw[usb_start + 21]
                        transfer = raw[usb_start + 22]
                        data_len = struct.unpack_from('<I', raw, usb_start + 23)[0]

                        ts_hi = struct.unpack_from('<I', raw, pos + 12)[0]
                        ts_lo = struct.unpack_from('<I', raw, pos + 16)[0]
                        ts = (ts_hi << 32) | ts_lo

                        ep_num = endpoint & 0x7F
                        ep_dir = 'IN' if (endpoint & 0x80) else 'OUT'
                        is_completion = bool(info & 0x01)

                        payload_start = usb_start + hdr_len
                        payload_end = min(payload_start + data_len, usb_start + cap_len)
                        payload = raw[payload_start:payload_end]

                        if transfer == 3 and len(payload) > 0:
                            packets.append({
                                'idx': pkt_idx,
                                'ts': ts,
                                'ep': ep_num,
                                'dir': ep_dir,
                                'completion': is_completion,
                                'data': payload,
                                'irp_id': irp_id,
                            })
                            pkt_idx += 1

        block_len_padded = (block_len + 3) & ~3
        pos += block_len_padded

    return packets


def match_requests_responses(packets):
    """Match EP1 OUT requests med deira EP1 IN responses via IRP ID."""
    pending = {}  # irp_id -> request packet
    pairs = []

    for p in packets:
        if p['ep'] == 1:
            if p['dir'] == 'OUT' and not p['completion']:
                pending[p['irp_id']] = p
            elif p['completion'] and p['irp_id'] in pending:
                req = pending.pop(p['irp_id'])
                pairs.append((req, p))
    return pairs


def analyze_eeprom(packets, name):
    """Analyser A8 (EEPROM) kommandoar og svar."""
    print(f"\n{'='*70}")
    print(f"EEPROM-ANALYSE (A8): {name}")
    print(f"{'='*70}")

    pairs = match_requests_responses(packets)

    # Finn A8-par
    a8_pairs = [(req, resp) for req, resp in pairs if req['data'][0] == 0xA8]
    print(f"A8 EEPROM-lesingar: {len(a8_pairs)}")

    eeprom_data = bytearray()
    for i, (req, resp) in enumerate(a8_pairs):
        rd = resp['data']
        # A8 svar er 32 bytes EEPROM-data per sektor
        # Fjern trailing 0xFF
        trimmed = rd.rstrip(b'\xff')
        if len(trimmed) > 0:
            print(f"  Sektor {i:2d}: {rd[:32].hex()}")
            # Prøv ASCII-dekoding
            ascii_parts = []
            for b in rd[:32]:
                if 0x20 <= b <= 0x7e:
                    ascii_parts.append(chr(b))
                else:
                    ascii_parts.append('.')
            print(f"             {''.join(ascii_parts)}")
        eeprom_data.extend(rd[:32])

    # Leita etter TEDS-moenster i EEPROM
    print(f"\nTotal EEPROM: {len(eeprom_data)} bytes")

    # Sjekk for kjende strengar
    for offset in range(len(eeprom_data) - 4):
        chunk = eeprom_data[offset:offset+32]
        # Sjekk for serienummer-liknande data
        try:
            text = chunk.decode('ascii', errors='ignore')
            if any(kw in text for kw in ['TEDS', 'Rogowski', 'sensor', 'probe', '6kA', '1000A']):
                print(f"  TEDS-hint ved offset {offset}: {chunk.hex()}")
                print(f"    ASCII: {text}")
        except:
            pass


def analyze_reg14_responses(packets, name):
    """Analyser register 0x14 (slot-les) request/response par for Lo-LV."""
    print(f"\n{'='*70}")
    print(f"REGISTER 0x14 SVAR (slot-lesingar): {name}")
    print(f"{'='*70}")

    pairs = match_requests_responses(packets)

    # Finn AD reg 0x14 par for Lo-LV slottar
    reg14_pairs = []
    for req, resp in pairs:
        d = req['data']
        if d[0] == 0xAD and len(d) >= 15 and d[6] == 0x14:
            slot = d[10] if len(d) > 10 else 0
            if 4 <= slot <= 7:
                reg14_pairs.append((req, resp, slot))

    print(f"Register 0x14 lesingar for slot 4-7: {len(reg14_pairs)}")

    # Grupper unike svar per slot
    slot_responses = defaultdict(set)
    for req, resp, slot in reg14_pairs:
        rd = resp['data']
        slot_responses[slot].add(rd[:32].hex())

    for slot in sorted(slot_responses):
        resps = sorted(slot_responses[slot])
        print(f"\n  Slot {slot}: {len(resps)} unike svar")
        for r in resps[:20]:
            d = bytes.fromhex(r)
            # Dekod svar
            print(f"    {r[:40]}...")
            # Sjekk om svar inneheld A5-status
            if len(d) >= 4:
                if d[0] == 0x01:
                    # Suksess-svar
                    status_byte = d[4] if len(d) > 4 else 0
                    payload = d[4:12]
                    # Leita etter TEDS-data i payload
                    trimmed = payload.rstrip(b'\x00').rstrip(b'\xff')
                    if len(trimmed) > 0:
                        print(f"      status={d[0]:02x} payload={payload.hex()}")


def analyze_reg15(packets, name):
    """Analyser register 0x15 kommandoar og svar."""
    print(f"\n{'='*70}")
    print(f"REGISTER 0x15 ANALYSE: {name}")
    print(f"{'='*70}")

    pairs = match_requests_responses(packets)

    reg15_pairs = [(req, resp) for req, resp in pairs
                   if req['data'][0] == 0xAD and len(req['data']) >= 15 and req['data'][6] == 0x15]

    print(f"Register 0x15 kommandoar: {len(reg15_pairs)}")

    for req, resp in reg15_pairs:
        rd = req['data']
        rr = resp['data']
        print(f"  CMD: {rd[:15].hex()}")
        print(f"  RSP: {rr[:32].hex()}")
        # Dekod svar
        trimmed = rr.rstrip(b'\xff').rstrip(b'\x00')
        if len(trimmed) > 2:
            ascii_parts = ''.join(chr(b) if 0x20 <= b <= 0x7e else '.' for b in rr[:32])
            print(f"       ASCII: {ascii_parts}")
        print()


def analyze_all_responses(packets, name):
    """Vis ALLE unike svar som kan innehalde TEDS-data."""
    print(f"\n{'='*70}")
    print(f"ALLE UNIKE EP1 IN SVAR > 8 bytes nyttelast: {name}")
    print(f"{'='*70}")

    pairs = match_requests_responses(packets)
    seen = set()

    for req, resp in pairs:
        rd = resp['data']
        trimmed = rd.rstrip(b'\xff').rstrip(b'\x00')
        if len(trimmed) > 8:
            key = rd[:32].hex()
            if key not in seen:
                seen.add(key)
                cmd = req['data']
                cmd_name = f"0x{cmd[0]:02X}"
                if cmd[0] == 0xAD:
                    cmd_name = f"AD reg=0x{cmd[6]:02X}"
                elif cmd[0] == 0xA8:
                    cmd_name = "A8 EEPROM"
                elif cmd[0] == 0xB1:
                    cmd_name = "B1 poll"

                ascii_parts = ''.join(chr(b) if 0x20 <= b <= 0x7e else '.' for b in rd[:32])
                print(f"  [{cmd_name:16s}] {rd[:32].hex()}")
                print(f"  {' '*19} {ascii_parts}")


def analyze_b1_responses(packets, name):
    """Analyser B1 (poll) svar - desse returnerer data frå slot-register."""
    print(f"\n{'='*70}")
    print(f"B1 POLL-SVAR (slot register readback): {name}")
    print(f"{'='*70}")

    pairs = match_requests_responses(packets)

    # B1 svar som har meir enn berre status
    interesting_b1 = []
    for req, resp in pairs:
        if req['data'][0] == 0xB1:
            rd = resp['data']
            trimmed = rd.rstrip(b'\xff').rstrip(b'\x00')
            if len(trimmed) > 4:  # Meir enn berre statusbyte
                interesting_b1.append((req, resp))

    print(f"B1 svar med payload > 4 bytes: {len(interesting_b1)}")

    seen = set()
    for req, resp in interesting_b1:
        rd = resp['data']
        key = rd[:16].hex()
        if key not in seen:
            seen.add(key)
            ascii_parts = ''.join(chr(b) if 0x20 <= b <= 0x7e else '.' for b in rd[:32])
            print(f"  {rd[:32].hex()}")
            print(f"  {ascii_parts}")


def main():
    all_pcaps = [
        ("D:/Koding/dewesoft/sirius2.pcapng", "sirius2 (Sundet, Lo-LV)"),
        ("D:/Koding/dewesoft/sirius1.pcapng", "sirius1 (original)"),
        ("D:/Koding/dewesoft/sirius3.pcapng", "sirius3"),
        ("D:/Koding/dewesoft/sirius4.pcapng", "sirius4"),
        ("D:/Koding/dewesoft/sirius5.pcapng", "sirius5"),
        ("D:/Koding/dewesoft/sirius6.pcapng", "sirius6"),
    ]

    # Fyrst: EEPROM + reg15 frå sirius2
    for filepath, name in all_pcaps[:2]:
        try:
            print(f"\nLes {filepath}...")
            packets = parse_pcapng(filepath)
            if packets:
                analyze_eeprom(packets, name)
                analyze_reg15(packets, name)
                analyze_b1_responses(packets, name)
        except Exception as e:
            print(f"  FEIL: {e}")
            import traceback
            traceback.print_exc()

    # Sjekk sirius3-6 for andre kommando-typar
    for filepath, name in all_pcaps[2:]:
        try:
            print(f"\n\nLes {filepath}...")
            packets = parse_pcapng(filepath)
            if not packets:
                print(f"  Ingen pakkar")
                continue

            print(f"  {len(packets)} bulk-pakkar")
            ep1_out = [p for p in packets if p['ep'] == 1 and p['dir'] == 'OUT' and not p['completion']]
            cmd_counts = defaultdict(int)
            for p in ep1_out:
                cmd_counts[p['data'][0]] += 1
            print(f"  Kommandoar:")
            for op in sorted(cmd_counts):
                print(f"    0x{op:02X}: {cmd_counts[op]}")

            # Sjekk for AD-kommandoar med nye register
            ad_regs = defaultdict(int)
            for p in ep1_out:
                d = p['data']
                if d[0] == 0xAD and len(d) >= 15:
                    ad_regs[d[6]] += 1
            if ad_regs:
                print(f"  AD register:")
                for r in sorted(ad_regs):
                    print(f"    0x{r:02X}: {ad_regs[r]}")

            # Vis alle unike svar med innhald
            analyze_all_responses(packets, name)

        except Exception as e:
            print(f"  FEIL: {e}")
            import traceback
            traceback.print_exc()


if __name__ == '__main__':
    main()

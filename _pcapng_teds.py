#!/usr/bin/env python3
"""Parse USBPcap pcapng-filer og leita etter TEDS-relaterte kommandoar.

Fokus: EP1 OUT-kommandoar (host→device) og EP1 IN-svar,
spesielt A5-kommandoar til Lo-LV slottar (4-7) som kan vere 1-Wire/TEDS.
"""
import struct
import sys
from collections import defaultdict

def parse_pcapng(filepath):
    """Parse pcapng og returner liste av (timestamp, endpoint, direction, data)."""
    packets = []
    with open(filepath, 'rb') as f:
        raw = f.read()

    pos = 0
    while pos < len(raw) - 12:
        block_type = struct.unpack_from('<I', raw, pos)[0]
        block_len = struct.unpack_from('<I', raw, pos + 4)[0]
        if block_len < 12 or pos + block_len > len(raw):
            break

        # Enhanced Packet Block (0x00000006)
        if block_type == 0x00000006:
            # EPB: type(4) + len(4) + iface(4) + ts_hi(4) + ts_lo(4) + cap_len(4) + orig_len(4) = 28 bytes header
            if block_len > 28 + 27:  # minimum for USBPcap header
                epb_data_start = pos + 28
                cap_len = struct.unpack_from('<I', raw, pos + 20)[0]

                if cap_len >= 27:
                    usb_start = epb_data_start
                    hdr_len = struct.unpack_from('<H', raw, usb_start)[0]

                    if 27 <= hdr_len <= 64 and usb_start + hdr_len + 4 <= len(raw):
                        # USBPcap header fields
                        irp_id = struct.unpack_from('<Q', raw, usb_start + 2)[0]
                        usbd_status = struct.unpack_from('<I', raw, usb_start + 10)[0]
                        function = struct.unpack_from('<H', raw, usb_start + 14)[0]
                        info = raw[usb_start + 16]
                        bus = struct.unpack_from('<H', raw, usb_start + 17)[0]
                        device = struct.unpack_from('<H', raw, usb_start + 19)[0]
                        endpoint = raw[usb_start + 21]
                        transfer = raw[usb_start + 22]
                        data_len = struct.unpack_from('<I', raw, usb_start + 23)[0]

                        ts_hi = struct.unpack_from('<I', raw, pos + 12)[0]
                        ts_lo = struct.unpack_from('<I', raw, pos + 16)[0]
                        ts = (ts_hi << 32) | ts_lo  # microseconds

                        # direction: bit 0 of info = 1 means PDO (device→host completion)
                        # endpoint bit 7 = direction (0=OUT, 1=IN)
                        ep_num = endpoint & 0x7F
                        ep_dir = 'IN' if (endpoint & 0x80) else 'OUT'
                        is_completion = bool(info & 0x01)

                        # Extract payload data
                        payload_start = usb_start + hdr_len
                        payload_end = min(payload_start + data_len, usb_start + cap_len)
                        payload = raw[payload_start:payload_end]

                        if transfer == 3 and len(payload) > 0:  # BULK
                            packets.append({
                                'ts': ts,
                                'ep': ep_num,
                                'dir': ep_dir,
                                'completion': is_completion,
                                'data': payload,
                                'irp_id': irp_id,
                            })

        # Move to next block (padded to 4 bytes)
        block_len_padded = (block_len + 3) & ~3
        pos += block_len_padded

    return packets


def decode_a5(data, slot_byte):
    """Decode A5 sub-command."""
    if len(data) < 4:
        return f"A5 (kort)"
    sub = data[1]  # sub-opcode etter A5
    reg = data[2]  # register
    val = data[3] if len(data) > 3 else 0
    return f"A5 sub={sub:02x} reg={reg:02x} val={val:02x}"


def analyze_for_teds(packets, pcap_name):
    """Analyser pakkar for TEDS-relaterte kommandoar."""
    print(f"\n{'='*70}")
    print(f"ANALYSE: {pcap_name}")
    print(f"{'='*70}")
    print(f"Totalt {len(packets)} bulk-pakkar med payload\n")

    # Filtrer EP1 kommandoar (OUT til device, ikkje completion)
    ep1_out = [p for p in packets if p['ep'] == 1 and p['dir'] == 'OUT' and not p['completion']]
    ep1_in = [p for p in packets if p['ep'] == 1 and p['dir'] == 'IN' and p['completion']]

    print(f"EP1 OUT (kommandoar): {len(ep1_out)}")
    print(f"EP1 IN (svar):        {len(ep1_in)}")

    # Kategoriser kommandoar
    cmd_counts = defaultdict(int)
    ad_cmds = []  # Alle AD-kommandoar
    a5_cmds = []  # Alle A5-kommandoar (slot-spesifikke)
    unknown_cmds = []

    for p in ep1_out:
        d = p['data']
        opcode = d[0]
        cmd_counts[opcode] += 1

        if opcode == 0xAD and len(d) >= 15:
            # AD-kommando: ad 3f 0c 00 00 00 REG 00 00 00 SLOT DATA...
            reg = d[6]
            slot = d[10] if len(d) > 10 else 0
            ad_cmds.append({
                'ts': p['ts'],
                'raw': d[:15],
                'reg': reg,
                'slot': slot,
                'data': d[6:15],
            })

            # Sjekk for A5-liknande payload i AD-kommandoar til Lo-LV slottar
            # Register 0x13 = slot-spesifikk skriving (same som init)
            # Register 0x14 = slot-spesifikk lesing
            if reg in (0x13, 0x14) and slot >= 4:
                payload = d[10:15]
                if len(payload) >= 2 and payload[0] in (0x04, 0x05, 0x06, 0x07):
                    # Slot 4-7, sjekk A5-subkommando
                    if len(payload) >= 3 and payload[1] == 0xA5:
                        sub = payload[2] if len(payload) > 2 else 0
                        a5_cmds.append({
                            'ts': p['ts'],
                            'raw': d[:15],
                            'slot': payload[0],
                            'a5_sub': sub,
                            'reg': reg,
                            'payload': payload,
                        })

    print(f"\nKommando-fordeling:")
    for opcode in sorted(cmd_counts):
        print(f"  0x{opcode:02X}: {cmd_counts[opcode]:5d}")

    # Vis alle AD-kommandoar med register 0x13/0x14 for Lo-LV slottar
    lv_cmds = [c for c in ad_cmds if c['reg'] in (0x13, 0x14) and c['slot'] >= 4]
    print(f"\nAD-kommandoar til Lo-LV slottar (reg 0x13/0x14, slot>=4): {len(lv_cmds)}")

    # Grupper etter slot og register
    for slot in range(4, 8):
        slot_cmds_13 = [c for c in lv_cmds if c['slot'] == slot and c['reg'] == 0x13]
        slot_cmds_14 = [c for c in lv_cmds if c['slot'] == slot and c['reg'] == 0x14]
        if slot_cmds_13 or slot_cmds_14:
            print(f"\n  Slot {slot}: {len(slot_cmds_13)} skriv (0x13), {len(slot_cmds_14)} les (0x14)")

    # Sjekk for alle unike A5 sub-kommandoar per slot
    if a5_cmds:
        print(f"\nA5-kommandoar til Lo-LV slottar: {len(a5_cmds)}")
        a5_subs = defaultdict(lambda: defaultdict(int))
        for c in a5_cmds:
            a5_subs[c['slot']][c['a5_sub']] += 1
        for slot in sorted(a5_subs):
            print(f"  Slot {slot}:")
            for sub in sorted(a5_subs[slot]):
                print(f"    A5 sub=0x{sub:02X}: {a5_subs[slot][sub]} gonger")

    # --- Detaljert: ALLE unike AD-register+payload for Lo-LV ---
    print(f"\n--- DETALJERT: Alle unike AD-kommandoar til Lo-LV slottar ---")
    seen = set()
    for c in lv_cmds:
        key = c['raw'][:15].hex()
        if key not in seen:
            seen.add(key)
            d = c['raw']
            reg = c['reg']
            slot_area = d[10:15]
            rw = "SKRIV" if reg == 0x13 else "LES"
            print(f"  [{rw}] slot={c['slot']} {d[:15].hex()}")
            # Dekod payload
            if len(slot_area) >= 2:
                first = slot_area[0]
                second = slot_area[1] if len(slot_area) > 1 else 0
                # Sjekk for kjende moenster
                if second == 0xA5:
                    sub = slot_area[2] if len(slot_area) > 2 else 0
                    reg2 = slot_area[3] if len(slot_area) > 3 else 0
                    val2 = slot_area[4] if len(slot_area) > 4 else 0
                    print(f"         -> slot_byte={first:02x} A5 sub={sub:02x} reg={reg2:02x} val={val2:02x}")

    # --- Sjekk SVAR frå device for Lo-LV data ---
    print(f"\n--- EP1 IN svar (fyrste 50 unike) ---")
    seen_resp = set()
    resp_count = 0
    for p in ep1_in:
        d = p['data']
        if len(d) >= 4:
            key = d[:min(16, len(d))].hex()
            if key not in seen_resp:
                seen_resp.add(key)
                resp_count += 1
                if resp_count <= 50:
                    print(f"  {d[:min(32, len(d))].hex()}")

    # --- Leita etter kommandoar som IKKJE er i init-sekvensane ---
    # Dvs. kommandoar som skjer i idle/polling-fasen
    print(f"\n--- Kronologisk: AD reg 0x14 (LES) for slot 4-7 ---")
    read_cmds = [c for c in ad_cmds if c['reg'] == 0x14]
    lv_reads = [c for c in read_cmds if c['slot'] >= 4 and c['slot'] <= 7]
    print(f"Totalt {len(lv_reads)} register-lesingar for Lo-LV slottar")
    seen_reads = set()
    for c in lv_reads:
        key = c['raw'][:15].hex()
        if key not in seen_reads:
            seen_reads.add(key)
            print(f"  {c['raw'][:15].hex()}")

    # --- Leita etter register-adresser vi ikkje kjenner ---
    print(f"\n--- Alle unike register-adresser brukt ---")
    reg_counts = defaultdict(int)
    for c in ad_cmds:
        reg_counts[c['reg']] += 1
    for reg in sorted(reg_counts):
        print(f"  reg 0x{reg:02X}: {reg_counts[reg]:5d} gonger")

    # --- Leita spesifikt etter mogleg TEDS/1-Wire ---
    # TEDS-lesing kan bruke:
    # - Ein dedikert register (t.d. 0xF0-0xFF)
    # - A5 med spesielle sub-kommandoar (0x05, 0x06, 0x07, 0x08...)
    # - Kommandoar med store datablokker (>4 bytes payload)
    print(f"\n--- Mogleg TEDS: A5 sub-kommandoar utover init (01,02,03,04,06) ---")
    teds_candidates = [c for c in a5_cmds if c['a5_sub'] not in (0x01, 0x02, 0x03, 0x04, 0x06)]
    if teds_candidates:
        for c in teds_candidates:
            print(f"  slot={c['slot']} A5 sub=0x{c['a5_sub']:02X} {c['raw'][:15].hex()}")
    else:
        print("  (ingen funne)")

    # Sjekk om det finst AD-kommandoar UTANOM 0x13/0x14 for Lo-LV slottar
    print(f"\n--- AD-kommandoar med andre register enn 0x13/0x14 der slot>=4 ---")
    other_lv = [c for c in ad_cmds if c['reg'] not in (0x13, 0x14) and c['slot'] >= 4]
    if other_lv:
        seen2 = set()
        for c in other_lv:
            key = c['raw'][:15].hex()
            if key not in seen2:
                seen2.add(key)
                print(f"  reg=0x{c['reg']:02X} slot={c['slot']} {c['raw'][:15].hex()}")
    else:
        print("  (ingen funne)")

    return ad_cmds, a5_cmds


def find_teds_in_responses(packets):
    """Sjekk EP1 IN-svar for mogleg TEDS-data (1-Wire EEPROM content)."""
    print(f"\n--- Leita etter TEDS-data i EP1 IN svar ---")
    # TEDS data er typisk IEEE 1451.4 format:
    # - Fyrste byte: template ID
    # - Deretter: sensor-data (type, range, kalibrering, etc.)
    # DS2431+ har 128 bytes, organisert i 4 pages á 32 bytes

    ep1_in = [p for p in packets if p['ep'] == 1 and (p['endpoint_raw'] if 'endpoint_raw' in p else 0x81) and p['completion']]

    # Sjekk for store svar (>16 bytes ikkje-null) som kan vere EEPROM-dumps
    for p in ep1_in:
        d = p['data']
        # Fjern trailing nullar
        trimmed = d.rstrip(b'\x00')
        if len(trimmed) > 20:  # Potensielt interessant data
            # Sjekk om det liknar TEDS (ikkje berre eit vanleg AD-svar)
            if d[0] not in (0xAE, 0xA1, 0xAC, 0xA8):  # Ikkje standard svar-typar
                print(f"  Stort svar ({len(trimmed)} bytes): {d[:32].hex()}")


def main():
    pcap_files = [
        ("D:/Koding/dewesoft/sirius2.pcapng", "sirius2 (Sundet-oppsett, Lo-LV aktiv)"),
        ("D:/Koding/dewesoft/sirius1.pcapng", "sirius1 (original capture)"),
    ]

    for filepath, name in pcap_files:
        try:
            print(f"\nLes {filepath} ...")
            packets = parse_pcapng(filepath)
            if packets:
                analyze_for_teds(packets, name)
            else:
                print(f"  Ingen pakkar funne!")
        except Exception as e:
            print(f"  FEIL: {e}")
            import traceback
            traceback.print_exc()


if __name__ == '__main__':
    main()

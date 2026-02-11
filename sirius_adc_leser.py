#!/usr/bin/env python3
"""
SIRIUS ADC Data-leser
======================
Leser og analyserer raa ADC-data fra Dewesoft SIRIUS via USB.

Basert paa funn:
  - EP2 IN (0x82) streamer ADC-data uten kommando (alltid aktiv)
  - Data er int16 LE, trolig 8 kanaler interleaved
  - ~45 pakker/sek * 512 bytes = ~22.5 KB/s
  - 32 rammer per pakke * 45 = ~1440 Hz samplingrate

Rammestruktur (hypotese - 8 kanaler, 16 bytes per ramme):
  [ch0] [ch1] [ch2] [ch3] [ch4] [ch5] [ch6] [ch7]
  int16  int16 int16 int16 int16 int16 int16 int16

EP6 (0x86) sender synk-data: repeterende 8-byte moenster 00000000 000000E0

Bruk:
  python3 sirius_adc_leser.py                    # Les 5 sekunder, analyser
  python3 sirius_adc_leser.py --varighet 30      # Les 30 sekunder
  python3 sirius_adc_leser.py --lagre             # Lagre til CSV/NPZ
  python3 sirius_adc_leser.py --kanaler           # Finn antall kanaler
  python3 sirius_adc_leser.py --live              # Vis live-verdier
"""

import sys
import os
import json
import struct
import time
import argparse
import logging
import csv
from datetime import datetime
from collections import defaultdict

import numpy as np

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger('sirius_adc')

DEWESOFT_VID = 0x1CED
SIRIUS_PID = 0x1002

EP_CMD_OUT = 0x01
EP_CMD_IN = 0x81
EP_DATA_ADC = 0x82
EP_DATA_CTRL = 0x84
EP_DATA_SYNC = 0x86
EP_DATA_OUT = 0x08

TIMEOUT_MS = 500


def koble_til():
    """Koble til SIRIUS USB."""
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

    try:
        produsent = usb.util.get_string(dev, dev.iManufacturer)
        produkt = usb.util.get_string(dev, dev.iProduct)
        serienr = usb.util.get_string(dev, dev.iSerialNumber)
        log.info(f"Tilkoblet: {produsent} {produkt} (S/N: {serienr})")
    except Exception:
        log.info("Tilkoblet SIRIUS")

    return dev


def les_raa_data(dev, varighet_sek=5.0):
    """Les raa ADC-data fra EP2 i angitt varighet.

    Returns:
        tuple: (raa_buffer, pakketeller, elapsed)
    """
    import usb.core

    log.info(f"Leser ADC-data i {varighet_sek} sekunder...")

    buffer = bytearray()
    pakker = 0
    start = time.time()

    while time.time() - start < varighet_sek:
        try:
            data = dev.read(EP_DATA_ADC, 512, timeout=TIMEOUT_MS)
            buffer.extend(data)
            pakker += 1
        except usb.core.USBTimeoutError:
            pass
        except usb.core.USBError as e:
            log.warning(f"USB-feil: {e}")
            break

    elapsed = time.time() - start
    log.info(f"Lest {len(buffer)} bytes i {pakker} pakker ({elapsed:.1f}s)")

    return bytes(buffer), pakker, elapsed


def les_synk_data(dev, varighet_sek=2.0):
    """Les synk-data fra EP6."""
    import usb.core

    buffer = bytearray()
    pakker = 0
    start = time.time()

    while time.time() - start < varighet_sek:
        try:
            data = dev.read(EP_DATA_SYNC, 512, timeout=TIMEOUT_MS)
            buffer.extend(data)
            pakker += 1
        except (usb.core.USBTimeoutError, usb.core.USBError):
            pass

    return bytes(buffer), pakker


def finn_rammestruktur(data, maks_kanaler=16):
    """Analyser data for aa finne antall kanaler (rammebredde).

    Metode: Beregn autokorrelasjon av int16-verdier og finn
    den foerste toppen etter lag 0. Denne toppen tilsvarer
    rammestorrelsen (antall kanaler).
    """
    if len(data) < 512:
        return None

    # Konverter til int16
    samples = np.frombuffer(data[:min(len(data), 16384)], dtype=np.int16)

    if len(samples) < 64:
        return None

    # Normaliser
    samples_f = samples.astype(np.float64)
    samples_f -= samples_f.mean()
    if samples_f.std() < 1:
        return None

    # Autokorrelasjon for lag 1 til maks_kanaler*2
    maks_lag = maks_kanaler * 2
    acf = np.zeros(maks_lag)
    norm = np.sum(samples_f**2)

    for lag in range(1, maks_lag):
        if lag >= len(samples_f):
            break
        acf[lag] = np.sum(samples_f[:-lag] * samples_f[lag:]) / norm

    # Finn topper i autokorrelasjon
    topper = []
    for i in range(2, len(acf) - 1):
        if acf[i] > acf[i-1] and acf[i] > acf[i+1] and acf[i] > 0.1:
            topper.append((i, acf[i]))

    # Foerste topp er sannsynligvis rammestorrelsen
    if topper:
        beste_lag = topper[0][0]
        korrelasjon = topper[0][1]
        return {
            "kanaler": beste_lag,
            "korrelasjon": float(korrelasjon),
            "topper": [(lag, float(val)) for lag, val in topper[:5]],
            "acf": [float(v) for v in acf],
        }

    return None


def analyser_kanalstruktur(data, test_kanaler=[4, 6, 8, 10, 12, 16]):
    """Test forskjellige antall kanaler og se hvilken gir mest konsistente data.

    For hvert kandidat-antall: del data i rammer, beregn varians per kanal.
    Riktig antall kanaler gir kanaler med FORSKJELLIG men STABIL varians.
    """
    if len(data) < 1024:
        return None

    samples = np.frombuffer(data[:min(len(data), 65536)], dtype=np.int16)
    resultater = {}

    for n_ch in test_kanaler:
        # Trim til hele rammer
        n_frames = len(samples) // n_ch
        if n_frames < 10:
            continue

        trimmed = samples[:n_frames * n_ch]
        rammer = trimmed.reshape(n_frames, n_ch)

        # Per-kanal statistikk
        kanal_stats = []
        for ch in range(n_ch):
            ch_data = rammer[:, ch].astype(np.float64)
            kanal_stats.append({
                "mean": float(np.mean(ch_data)),
                "std": float(np.std(ch_data)),
                "min": int(np.min(ch_data)),
                "max": int(np.max(ch_data)),
                "range": int(np.max(ch_data) - np.min(ch_data)),
            })

        # Beregn "konsistens-score": kanaler bor ha stabil varians
        # over tid, men forskjellig fra hverandre
        stds = [s["std"] for s in kanal_stats]
        ranges = [s["range"] for s in kanal_stats]

        # Mellomkanal-variasjon (bra om kanaler er forskjellige)
        std_variasjon = np.std(stds) if len(stds) > 1 else 0

        # Sjekk tidsstabilitet: del i halvdeler
        foerste = rammer[:n_frames//2, :]
        andre = rammer[n_frames//2:, :]
        stabilitet = 0
        for ch in range(n_ch):
            s1 = np.std(foerste[:, ch])
            s2 = np.std(andre[:, ch])
            if s1 > 0:
                stabilitet += abs(s1 - s2) / s1
        stabilitet /= n_ch  # Gjennomsnittlig relativ endring

        resultater[n_ch] = {
            "kanaler": kanal_stats,
            "mellomkanal_std_variasjon": float(std_variasjon),
            "tidsstabilitet": float(stabilitet),
            "score": float(std_variasjon / (stabilitet + 0.01)),  # Hoeyere = bedre
        }

    return resultater


def vis_kanalanalyse(data, n_kanaler):
    """Vis detaljert analyse per kanal."""
    samples = np.frombuffer(data, dtype=np.int16)
    n_frames = len(samples) // n_kanaler
    trimmed = samples[:n_frames * n_kanaler]
    rammer = trimmed.reshape(n_frames, n_kanaler)

    print(f"\n{'='*70}")
    print(f"  Kanalanalyse ({n_kanaler} kanaler, {n_frames} rammer)")
    print(f"{'='*70}")

    print(f"\n  {'Kanal':>6s} {'Mean':>10s} {'Std':>10s} {'Min':>8s} {'Max':>8s} {'Range':>8s}")
    print(f"  {'-'*56}")

    for ch in range(n_kanaler):
        ch_data = rammer[:, ch].astype(np.float64)
        mean = np.mean(ch_data)
        std = np.std(ch_data)
        mn = np.min(ch_data)
        mx = np.max(ch_data)
        rng = mx - mn

        # Klassifiser kanalen
        if std < 5:
            klasse = "DC/null"
        elif std < 100:
            klasse = "lav"
        elif std < 1000:
            klasse = "middels"
        else:
            klasse = "SIGNAL"

        print(f"  Ch{ch:>3d}  {mean:>10.1f} {std:>10.1f} {mn:>8.0f} {mx:>8.0f} {rng:>8.0f}  {klasse}")

    # Vis de foerste 5 rammene
    print(f"\n  Foerste 5 rammer (int16):")
    for i in range(min(5, n_frames)):
        vals = ' '.join(f'{v:>7d}' for v in rammer[i, :])
        print(f"    [{i:3d}] {vals}")

    # Vis de foerste 5 rammene som hex
    print(f"\n  Foerste 5 rammer (hex):")
    for i in range(min(5, n_frames)):
        raw_offset = i * n_kanaler * 2
        raw_bytes = data[raw_offset:raw_offset + n_kanaler * 2]
        hex_vals = ' '.join(f'{raw_bytes[j]:02x}{raw_bytes[j+1]:02x}'
                           for j in range(0, len(raw_bytes), 2))
        print(f"    [{i:3d}] {hex_vals}")

    return rammer


def vis_live(dev, n_kanaler=8, varighet=10):
    """Vis live ADC-verdier i terminalen."""
    import usb.core

    print(f"\n{'='*70}")
    print(f"  Live ADC-verdier ({n_kanaler} kanaler)")
    print(f"  Trykk Ctrl+C for aa stoppe")
    print(f"{'='*70}\n")

    # Header
    header = '  '.join(f'Ch{i:>2d}' for i in range(n_kanaler))
    print(f"  {header}")
    print(f"  {'-' * (7 * n_kanaler)}")

    start = time.time()
    ramme_buffer = np.zeros((10, n_kanaler), dtype=np.float64)  # Glidende snitt
    buf_idx = 0

    try:
        while time.time() - start < varighet:
            try:
                raw = dev.read(EP_DATA_ADC, 512, timeout=TIMEOUT_MS)
                data = bytes(raw)
                samples = np.frombuffer(data, dtype=np.int16)
                n_frames = len(samples) // n_kanaler

                if n_frames > 0:
                    rammer = samples[:n_frames * n_kanaler].reshape(n_frames, n_kanaler)
                    # Bruk siste ramme
                    siste = rammer[-1].astype(np.float64)
                    ramme_buffer[buf_idx % 10] = siste
                    buf_idx += 1

                    # Glidende snitt
                    n_gyldige = min(buf_idx, 10)
                    snitt = np.mean(ramme_buffer[:n_gyldige], axis=0)

                    vals = '  '.join(f'{v:>6.0f}' for v in snitt)
                    print(f"\r  {vals}", end='', flush=True)

            except usb.core.USBTimeoutError:
                pass
            except usb.core.USBError:
                break

            time.sleep(0.05)

    except KeyboardInterrupt:
        pass

    print(f"\n\n  Stoppet etter {time.time() - start:.1f}s")


def lagre_data(rammer, n_kanaler, elapsed, filnavn_prefiks="sirius"):
    """Lagre data til CSV og NPZ."""
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    n_frames = rammer.shape[0]

    # Estimert samplingrate
    sample_rate = n_frames / elapsed if elapsed > 0 else 0

    # CSV
    csv_fil = f"{filnavn_prefiks}_{ts}.csv"
    with open(csv_fil, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow([f"# SIRIUS ADC data - {datetime.now().isoformat()}"])
        writer.writerow([f"# Kanaler: {n_kanaler}, Rammer: {n_frames}, "
                        f"Estimert samplingrate: {sample_rate:.1f} Hz"])

        header = ["Ramme"] + [f"Ch{i}" for i in range(n_kanaler)]
        writer.writerow(header)

        for i in range(n_frames):
            row = [i] + [int(v) for v in rammer[i]]
            writer.writerow(row)

    log.info(f"CSV lagret: {csv_fil}")

    # NPZ
    npz_fil = f"{filnavn_prefiks}_{ts}.npz"
    kanal_data = {}
    for ch in range(n_kanaler):
        kanal_data[f"ch{ch}"] = rammer[:, ch].astype(np.int16)

    tid = np.arange(n_frames) / sample_rate if sample_rate > 0 else np.arange(n_frames)
    kanal_data["tid"] = tid
    kanal_data["sample_rate"] = np.array([sample_rate])

    np.savez_compressed(npz_fil, **kanal_data)
    log.info(f"NPZ lagret: {npz_fil}")

    # Metadata JSON
    meta_fil = f"{filnavn_prefiks}_{ts}_meta.json"
    meta = {
        "tidspunkt": datetime.now().isoformat(),
        "enhet": "Dewesoft SIRIUS (1ced:1002)",
        "kanaler": n_kanaler,
        "rammer": n_frames,
        "varighet_sek": elapsed,
        "estimert_sample_rate_hz": sample_rate,
        "bytes_totalt": n_frames * n_kanaler * 2,
        "filer": {"csv": csv_fil, "npz": npz_fil},
        "per_kanal": {},
    }

    for ch in range(n_kanaler):
        ch_data = rammer[:, ch].astype(np.float64)
        meta["per_kanal"][f"ch{ch}"] = {
            "mean": float(np.mean(ch_data)),
            "std": float(np.std(ch_data)),
            "min": int(np.min(ch_data)),
            "max": int(np.max(ch_data)),
            "rms": float(np.sqrt(np.mean(ch_data**2))),
        }

    with open(meta_fil, 'w', encoding='utf-8') as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
    log.info(f"Metadata lagret: {meta_fil}")

    return csv_fil, npz_fil, meta_fil


def main():
    parser = argparse.ArgumentParser(
        description='SIRIUS ADC Data-leser - Les maaldata direkte fra SIRIUS USB'
    )
    parser.add_argument('--varighet', type=float, default=5.0,
                        help='Lesevarighet i sekunder (standard: 5)')
    parser.add_argument('--kanaler', action='store_true',
                        help='Analyser og finn antall kanaler automatisk')
    parser.add_argument('--n-kanaler', type=int, default=0,
                        help='Angi antall kanaler manuelt (0=autodetekt)')
    parser.add_argument('--lagre', action='store_true',
                        help='Lagre data til CSV/NPZ-filer')
    parser.add_argument('--live', action='store_true',
                        help='Vis live ADC-verdier')
    parser.add_argument('--live-varighet', type=float, default=30.0,
                        help='Varighet for live-visning (standard: 30)')
    parser.add_argument('--raa', action='store_true',
                        help='Vis raa hex-data fra foerste pakker')
    parser.add_argument('--debug', action='store_true')

    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    print(f"{'='*70}")
    print(f"  SIRIUS ADC Data-leser")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}")

    dev = koble_til()

    # Les raa data
    raa_data, pakker, elapsed = les_raa_data(dev, args.varighet)

    if len(raa_data) == 0:
        print("\n  Ingen ADC-data mottatt!")
        print("  SIRIUS streamer kanskje ikke. Proov:")
        print("    1. Koble fra og til SIRIUS USB")
        print("    2. Vent noen sekunder og proov igjen")
        print("    3. Sjekk at USB/IP-deling er stoppet")
        sys.exit(1)

    # Statistikk
    print(f"\n  Raaadata:")
    print(f"    Pakker:     {pakker}")
    print(f"    Bytes:      {len(raa_data)} ({len(raa_data)/1024:.1f} KB)")
    print(f"    Varighet:   {elapsed:.1f}s")
    print(f"    Hastighet:  {len(raa_data)/1024/elapsed:.1f} KB/s")
    print(f"    Pakker/sek: {pakker/elapsed:.1f}")

    # Vis raa data
    if args.raa:
        print(f"\n  Raa hex (foerste 3 pakker):")
        for i in range(min(3, pakker)):
            offset = i * 512
            pkt = raa_data[offset:offset+512]
            for j in range(0, min(64, len(pkt)), 16):
                chunk = pkt[j:j+16]
                hex_str = ' '.join(f'{b:02x}' for b in chunk)
                print(f"    Pkt{i} +{j:3d}: {hex_str}")

    # ---- Finn antall kanaler ----
    n_kanaler = args.n_kanaler

    if n_kanaler == 0:
        print(f"\n  Autodetekterer antall kanaler...")

        # Metode 1: Autokorrelasjon
        acf_result = finn_rammestruktur(raa_data)
        if acf_result:
            print(f"    Autokorrelasjon: {acf_result['kanaler']} kanaler "
                  f"(korrelasjon={acf_result['korrelasjon']:.3f})")
            print(f"    Topper: {acf_result['topper'][:5]}")
            n_kanaler = acf_result['kanaler']

        # Metode 2: Kanalstruktur-analyse
        if args.kanaler or n_kanaler == 0:
            struktur = analyser_kanalstruktur(raa_data)
            if struktur:
                print(f"\n    Kanalstruktur-analyse:")
                print(f"    {'Kanaler':>8s} {'Score':>8s} {'Std-var':>10s} {'Stabilitet':>12s}")
                print(f"    {'-'*42}")

                for n_ch, info in sorted(struktur.items(), key=lambda x: x[1]['score'],
                                         reverse=True):
                    print(f"    {n_ch:>8d} {info['score']:>8.2f} "
                          f"{info['mellomkanal_std_variasjon']:>10.1f} "
                          f"{info['tidsstabilitet']:>12.4f}")

                # Velg beste
                beste = max(struktur.items(), key=lambda x: x[1]['score'])
                if n_kanaler == 0:
                    n_kanaler = beste[0]
                    print(f"\n    Beste kandidat: {n_kanaler} kanaler (score={beste[1]['score']:.2f})")

    if n_kanaler == 0:
        n_kanaler = 8  # Standard-antakelse
        print(f"\n    Bruker standard: {n_kanaler} kanaler")

    # ---- Vis kanalanalyse ----
    rammer = vis_kanalanalyse(raa_data, n_kanaler)

    # Samplingrate
    samples = np.frombuffer(raa_data, dtype=np.int16)
    n_frames = len(samples) // n_kanaler
    sample_rate = n_frames / elapsed if elapsed > 0 else 0
    print(f"\n  Estimert samplingrate: {sample_rate:.1f} Hz per kanal")
    print(f"  Totalt: {n_frames} rammer i {elapsed:.1f}s")

    # ---- Lagre ----
    if args.lagre:
        trimmed = samples[:n_frames * n_kanaler]
        rammer_array = trimmed.reshape(n_frames, n_kanaler)
        csv_fil, npz_fil, meta_fil = lagre_data(rammer_array, n_kanaler, elapsed)
        print(f"\n  Filer lagret:")
        print(f"    CSV:      {csv_fil}")
        print(f"    NPZ:      {npz_fil}")
        print(f"    Metadata: {meta_fil}")

    # ---- Live-visning ----
    if args.live:
        vis_live(dev, n_kanaler, args.live_varighet)

    # Les synk-data
    print(f"\n  Leser synk-data (EP6)...")
    synk_data, synk_pakker = les_synk_data(dev, 1.0)
    if synk_pakker > 0:
        synk_samples = np.frombuffer(synk_data[:min(len(synk_data), 512)], dtype=np.int16)
        unike = len(np.unique(synk_samples))
        print(f"    {synk_pakker} pakker, {unike} unike int16-verdier")
        print(f"    Foerste 16B: {synk_data[:16].hex()}")
    else:
        print(f"    Ingen synk-data")

    # Oppsummering
    print(f"\n{'='*70}")
    print(f"  Oppsummering")
    print(f"{'='*70}")
    print(f"  Enhet:          Dewesoft SIRIUS (1ced:1002)")
    print(f"  Kanaler:        {n_kanaler}")
    print(f"  Samplingrate:   ~{sample_rate:.0f} Hz")
    print(f"  Opplosning:     16-bit (int16)")
    print(f"  Datarate:       {len(raa_data)/1024/elapsed:.1f} KB/s")
    print(f"  USB-pakker:     {pakker/elapsed:.0f}/s")

    import usb.util
    usb.util.dispose_resources(dev)

    print(f"\n  Ferdig!")


if __name__ == "__main__":
    main()

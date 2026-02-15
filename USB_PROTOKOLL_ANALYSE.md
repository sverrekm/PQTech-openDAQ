# SIRIUS USB-protokoll analyse

**Kjelde:** `sirius1.pcapng` (38 MB, 73 657 pakkar, 105 sek)
**Fanga:** 2026-02-14, DewesoftX (Windows) <-> SIRIUSi-HS (VID=0x1CED PID=0x1002)
**Verktoy:** Wireshark/tshark

---

## Endepunkt-oversikt

| Endepunkt | Retning | Type | Pakkar | Beskriving |
|-----------|---------|------|--------|------------|
| 0x01 (EP1) | OUT | Interrupt | 34 190 | Kommandoar fraa host |
| 0x81 (EP1) | IN | Interrupt | 34 190 | Svar fraa SIRIUS |
| 0x82 (EP2) | IN | Bulk | 2 149 | ADC-data (15 872 B/pakke) |
| 0x84 (EP4) | IN | Bulk | 973 | Status/kontroll (20 B, alltid nullar) |
| 0x86 (EP6) | IN | Bulk | 2 149 | Sync-data (15 872 B/pakke) |

**Viktig:** EP2 og EP6 har noyaktig same tal pakkar (2149) — DewesoftX les dei i lockstep.

---

## Tidslinje

```
  0.0s   USB-enumerering (control transfers)
 14.3s   Fyrste EP1-kommando (init-sekvens startar)
 14.3s   Register-lesingar (0xA8) — EEPROM, slot-info, kalibrering
 14.5s   AD-kommandoar (0xAD) — slot-konfigurasjon
 14.5s   AE-telemetri-polling startar (0xAE kvart ~105ms)
 50.5s   Per-slot AD-konfigurering (0x13-skriv + 0x14-les syklusar)
 50.9s   Pre-start (0xA4 00) + slot-enumerering (0xAC)
 51.0s   Full acquisition-konfigurering (26 register-skrivingar)
 51.08s  TRIGGER: Register 0x02 → EP2 ADC-data startar
 51.27s  Fyrste EP2/EP6-pakke (0.19s etter trigger)
 51.30s  Register 0x03 ("streaming confirmed")
 51.3s+  Steady-state streaming med B1-heartbeat
104.9s   Capture stoppa (ingen stopp-kommando sendt)
```

---

## Kommando-opkodar

### Enkle kommandoar (send + les svar)

| Opkode | Byte | Beskriving | Bruk |
|--------|------|------------|------|
| `0x00` | 1 | Firmware-versjon | Init |
| `0xA0 01` | 2 | Sett aktiv modus | Init |
| `0xA1` | 1 | Hent slot-tilstedevaerelse | Init |
| `0xA4 00` | 2 | Pre-start modus | For start-acquisition |
| `0xA8 XX XX XX` | 4 | Les EEPROM/register (addr + len) | Init, diagnostikk |
| `0xAC` | 1 | Hent slot-typer | Init |
| `0xAE 1F 0C` | 3 | AE telemetri (temperatur/helse) | Kontinuerleg ~9.5 Hz |
| `0xB0 3F 0C` | 3 | Init/reset hovudkontrollar | Init |
| `0xB1` | 1 | **Poll/heartbeat** | **Kontinuerleg ~120-200/sek** |

### AD-kommandoar (0xAD + B1-poll-syklus)

Format: `AD 3F 0C 00 00 00 <reg> <data[8]>` (15 bytes totalt)

| Operasjon | Reg-byte | Beskriving |
|-----------|----------|------------|
| Slot-query | 0x08 | Global slot-avlesing |
| Slot-enum | 0x0C | Per-slot enumerering |
| Skriv | 0x13 | Skriv til register (med 5A-commit) |
| Les | 0x14 | Les fraa register |
| Batch | 0x1C | Batch-operasjon |

---

## Heartbeat-protokollen (KRITISK)

### DewesoftX sender TO parallelle EP1-operasjonar under streaming:

**1. B1 poll (0xB1) — hovud-heartbeat**
- 1 byte: `B1`
- Rate: ~120-200 per sekund (varierer med USB-belastning)
- Dei fleste svar er 0 bytes (timeout/NAK)
- Av og til 64-byte svar med register-status
- **Held EP2 ADC-straumen aktiv**

| Sekund | B1-polls |
|--------|----------|
| 52-53 | 211 |
| 53-54 | 187 |
| 54-55 | 127 |
| 55-56 | 109 |
| 56-57 | 166 |
| 57-58 | 199 |
| 58-59 | 153 |
| 59-60 | 94 |
| **Snitt** | **~156/sek** |

**2. AE telemetri (0xAE 1F 0C) — instrument-helse**
- 3 bytes: `AE 1F 0C`
- Rate: ~9.5 Hz (kvart ~105 ms)
- Svar: 64 bytes med temperatur/helse-data
- Format: `[teller_hi][teller_lo][status][temp x9][ff-padding]`
- Koyrer heile tida, ogsaa naar B1 og AD stoppar midlertidig

### Kva vi gjorde feil (og fiksa)

| Problem | Konsekvens |
|---------|------------|
| Brukte `0xAE` som heartbeat | Feil opcode — AE er telemetri, ikkje heartbeat |
| Sendte kvart 2. sekund | ~400x for sjeldan — SIRIUS tolka det som frakobla |
| Mangla `0xB1` polling | EP2 stogga fordi SIRIUS ikkje fekk EP1-aktivitet |

---

## Start-acquisition sekvensen

### Fase 1: Pre-start
```
A4 00                               — Pre-start modus
AC                                   — Hent slot-typer
  Svar: 04040404000000000000000006060606
  (4x ADC-slot type 0x04, 4x digital type 0x06)
```

### Fase 2: Hovudkonfigurasjon (26 register-skrivingar via 0xAD)

| Steg | Register | Data (hex) | Beskriving |
|------|----------|------------|------------|
| 1 | 0x67 | `80004e20005a0306` | Sample rate: 0x4E20 = **20000 Hz** |
| 2 | 0x7B | `00000c8000000040` | Buffer: 0x0C80 = 3200 |
| 3-10 | 0x82 | `000000XX00000031` | Kanal 0-7 config (0x31 = aktiv) |
| 11 | 0xE5 | `00001800ffffffff` | DMA-oppsett |
| 12 | 0x6F | `3fff231fffffffff` | Kanal-maske |
| 13 | 0x72 | `0000000200000000` | Modus |
| 14 | 0x10 | `00000000ffffffff` | Null-stilling |
| 15 | 0x11 | `00000000ffffffff` | Null-stilling |
| 16 | 0x07 | `03000000ffffffff` | Kontroll-flagg |
| 17 | 0x9C | `00640064ffffffff` | Kalibrering |
| 18 | 0x98 | `0214320000000000` | ADC-timing |
| 19 | 0x99 | `60600000ffffffff` | ADC-modus |
| 20 | 0x9D | `0000000000000000` | Reset |
| 21 | 0x96 | `ffffffffffffffff` | Alle kanalar |
| 22 | 0xD0 | `00000001ffffffff` | DMA start |
| 23 | 0x68 | `000000ffffffffff` | Kanal-maske (0xFF = alle 8) |
| 24 | 0xCC | `000000c0ffffffff` | Sampling-oppsett |
| 25 | 0xCD | `000001ffffffffff` | Sampling-modus |
| 26+ | 0xCA-CF | diverse | Gain/offset per kanal |
| - | 0x84 | `0000000000000000` | Forsterkar-reset |
| - | 0xC8 | `ffffffffffffffff` | Commit kalibrering |
| - | 0x64 | `ffffffffffffffff` | Final prep |

### Fase 3: Trigger + stadfesting

```
AD 3F 0C 00 00 00 02 FF FF FF FF FF FF FF FF   — Reg 0x02: START ACQUISITION
  (Krev mange B1-poll-syklusar, ~137ms)

  ... EP2/EP6-data startar etter ~190ms ...

AD 3F 0C 00 00 00 03 FF FF FF FF FF FF FF FF   — Reg 0x03: STREAMING CONFIRMED
  (Sendt 220ms etter reg 0x02)
```

### Fase 4: Post-start status-lesingar

```
Reg 0x65 — Les tilbake config-status
Reg 0xC9 — ADC-status
Reg 0x97 — Kanal-status
Reg 0x07 — Kontroll (sett til 0x00, var 0x03)
Reg 0x0D — Feil-register
Reg 0x0B — Overflow-teljar
```

---

## Periodiske operasjonar under streaming

| Operasjon | Rate | Beskriving |
|-----------|------|------------|
| B1 poll | ~156/sek | Hovud-heartbeat, held EP2 aktiv |
| AE telemetri (0xAE) | ~9.5 Hz | Temperatur/helse-monitoring |
| EP4 les (0x84) | ~9 Hz | Status-register (alltid 0x00) |
| Reg 0x08 les | ~0.5 Hz | Watchdog/keep-alive |
| Reg 0x0C[0-2] les | ~0.5 Hz | Overflow/feil-teljarar |
| Reg 0x1C les | ~0.5 Hz | Batch-status |

---

## EP2 ADC-dataformat

- **Pakkestorleik:** 15 872 bytes
- **Rate:** ~20 pakkar/sek
- **Format:** 992 rammer x 8 kanalar x int16 LE (interleaved)
- **Bandbreidde:** ~317 KB/sek

```
Ramme N: [K0_lo K0_hi K1_lo K1_hi ... K7_lo K7_hi]  (16 bytes)
Ramme N+1: [K0_lo K0_hi K1_lo K1_hi ... K7_lo K7_hi]
...
(992 rammer per USB-pakke)
```

## EP6 Sync-dataformat

- **Pakkestorleik:** 15 872 bytes (same som EP2)
- **Rate:** ~20 pakkar/sek (lockstep med EP2)
- **Format:** 1984 x 8-byte records

```
Kvar record: 00 00 00 00 00 00 00 E0  (konstant i dette fangsten)
```

`0xE0` = bits 7,6,5 sett — truleg "intern klokke, ingen ekstern sync".
Alle records er identiske naar ingen ekstern sync-kjelde er tilkobla.

---

## B1-svar format under streaming

Dei fleste B1-polls gir 0 bytes (NAK/timeout). Svar kjem berre naar SIRIUS har data:

```
Byte 0: Status-flagg
  0x00 = "prosesserer" / echo
  0x01 = "ny data klar" / ACK
Byte 1-63: Register-status eller AD-svar
```

---

## Oppsummering av feil vi fann og fiksa

| # | Feil | Effekt | Fiks |
|---|------|--------|------|
| 1 | Heartbeat var 0xAE (telemetri) | Feil kommando til SIRIUS | Bytta til 0xB1 (poll) |
| 2 | Heartbeat kvart 2. sekund | EP2 stogga, SIRIUS "frakobla" | Aukte til ~100/sek |
| 3 | Mangla register 0x03 | Ingen "streaming confirmed" | La til 0.2s etter reg 0x02 |
| 4 | Ingen AE telemetri under streaming | Mangla helse-monitoring | La til ~10 Hz AE |
| 5 | ENODEV (Errno 19) talt til 50 | Blokkerte USB i ~50 sek | Stoppar umiddelbart |
| 6 | Foreldrelause traadar etter feil | EBUSY ved rekobling | Thread-scan + join(3s) |

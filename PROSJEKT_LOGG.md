# PQTech openDAQ Bridge - Prosjektlogg

## Oversikt

Raspberry Pi 5 med Dewesoft-instrument tilkobla via USB.
Docker-container med to hovudmodusar:

1. **USB/IP-modus** (generisk) — Del kva som helst Dewesoft-instrument
   (SIRIUS, KRYPTON, IOLITE, MINITAURs) til Windows via USB/IP.
   DewesoftX ser instrumentet som lokal USB-eining.
2. **Direkte-modus** (SIRIUS-spesifikk) — Pi les ADC-data direkte via
   reverse-engineered USB-protokoll og strøymer via openDAQ.

**Bonus:** MQTT-sensorar kan strøymast som ekstra kanalar via openDAQ-broen
uavhengig av modus (t.d. temperaturprobar, værstasjonar).

**Mål:** Fleksibel gateway mellom Dewesoft-instrument og DewesoftX over nettverk,
med MQTT-sensorintegrering som tilleggsverdi.

## Hardware (testoppsett)

| Komponent | Detaljar |
|-----------|----------|
| Datamaskin | Raspberry Pi 5 |
| Instrument | Dewesoft SIRIUSi-HS, 8 AI-kanalar |
| Tilkobling | USB direkte til Pi (Bus 3, VID=0x1CED, PID=0x1002) |
| Container IP | 192.168.1.161 (macvlan) |
| Windows PC | DewesoftX 2025.3 |

## Arkitektur

```
                        ┌─────────────────────────────────────────┐
                        │  Docker-container (pqtech-opendaq)      │
                        │                                         │
  Dewesoft ──USB──>     │  ┌── USB/IP-modus ──────────────────┐  │
  instrument            │  │  usbip bind + usbipd (:3240)     │  │
                        │  │  → Windows: usbip attach          │  │
                        │  │  → DewesoftX ser lokal USB        │  │
                        │  └──────────────────────────────────┘  │
                        │                                         │
                        │  ┌── Direkte-modus (SIRIUS) ────────┐  │
                        │  │  SiriusDriver (reverse-engineered)│  │
                        │  │  → EP2 ADC-streaming              │  │
                        │  │  → Autonom maaler (CSV/NPZ)       │  │
                        │  └──────────┬───────────────────────┘  │
                        │             │                           │
                        │  ┌── openDAQ Nettverksbro ──────────┐  │
  MQTT broker ──────>   │  │  ADC-kanalar (0..N frå SIRIUS)   │  │
  (temp, vêr, etc.)     │  │  + MQTT-kanalar (bonus-sensorar) │  │
                        │  │  NativeStreaming (:7420)           │  │
                        │  │  OPC-UA (:4840, intern)           │  │
                        │  │  mDNS-annonsering                 │  │
                        │  └──────────┬───────────────────────┘  │
                        └─────────────┼───────────────────────────┘
                                      │
                              DewesoftX (Windows)
                              → Finn eining via mDNS / daq.nd://
                              → ADC + MQTT kanalar i same system
```

### Modus-persistens

Sist aktive modus (`direkte` / `usbip`) vert lagra i `/data/konfig/modus.json`.
Ved container-restart vert same modus automatisk gjenoppretta:
- USB/IP: Hoppar over SIRIUS-tilkobling, startar usbipd automatisk
- Direkte: Koplar til SIRIUS-driver, startar ADC-streaming

## USB-protokoll (reverse-engineered)

### Endepunkt
| Endepunkt | Retning | Funksjon |
|-----------|---------|----------|
| EP1 OUT (0x01) | Til enhet | Kommandoar (AE, A0, B0, AD, B1) |
| EP1 IN (0x81) | Fraa enhet | Kommandosvar |
| EP2 IN (0x82) | Fraa enhet | ADC-data (8 ch x int16 LE, 32 B/pakke) |
| EP4 IN (0x84) | Fraa enhet | Kontrollstatus |
| EP6 IN (0x86) | Fraa enhet | Synkronisering/tidsstempel |
| EP8 OUT (0x08) | Til enhet | Data til enhet (uutforska) |

### Kommandoar (EP1)
| Opcode | Namn | Detaljar |
|--------|------|----------|
| 0x00 | FW-versjon | Les firmware-versjon |
| 0xA0 nn | SetMode | nn=01 aktiv, nn=00 inaktiv |
| 0xA1 | GetConfig | Slot-tilstedevaerelse (kva slottar finst) |
| 0xA8 | EEPROM | Les EEPROM-data |
| 0xAC | GetSlotTypes | Slot-type per slot (04=analog, 06=digital) |
| 0xAE | Telemetri | Heartbeat (returnerer 64 bytes) |
| 0xB0 3F 0C | Init | Initialiser/reset hovudkontrollar |
| 0xAD | AD-kommando | Register les/skriv med B1-polling |
| 0xB1 | Poll | Poll for AD-svar |

### Viktig funn om EP2 (ADC-data)
- **Factory fresh**: EP2 strøymer ADC-data kontinuerleg utan nokon kommando
- **Etter init-sekvens** (A0/B0/AD): EP2 sluttar å strøyme permanent
- **SIRIUS har 2 uavhengige kontrollerar:**
  - FX2 USB-brikke: handterer USB-kommunikasjon, RESETTAR ved USB-fråkopling
  - Hovudkontrollar: styrer ADC/EP2, HAR EIGEN STRAUMFORSYNING, overlever USB-fråkopling
- **USB-replug og sysfs authorized-toggle** resetter berre FX2, IKKJE hovudkontrollaren
- **START STREAMING FUNNE!** Register 0x02 via AD-kommando startar EP2 (sjå nedanfor)

### Start Acquisition-sekvens (FUNNE 2026-02-14, Wireshark pcapng)

**Kjelde:** `sirius1.pcapng` - Wireshark USBPcap capture fraa DewesoftX paa Windows.
105 sekund, 73 657 frames. Fangar komplett init → idle → start → streaming syklus.

**Trigger-kommando (Register 0x02):**
```
ad 3f 0c 00 00 00 02 ff ff ff ff ff ff ff ff
```
- Siste kommando før EP2/EP4/EP6 URB-ar vert submitterte
- Tek ~137ms aa fullfoere (lengste register-operasjon i heile capturen)
- EP2 ADC-data ankjem ~187ms etter trigger
- Finst nøyaktig 2 gonger i capturen, begge etterfølgt av EP2-data

**Komplett pre-start sekvens (34 register-skrivingar + trigger):**
```
Steg 1:  A4 00                                          Pre-start modus
Steg 2:  AC                                             GetSlotTypes
Steg 3:  AD reg 0x67 = 00 4E 20 00 5A 03 06             Sample rate (20000 Hz)
Steg 4:  AD reg 0x7B = 00 0C 80 00 00 00 40             Buffer-konfig
Steg 5:  AD reg 0x82 ch0 = 00 00 00 00 00 31            ADC-konfig kanal 0
Steg 6:  AD reg 0x82 ch1 = 00 00 01 00 00 31            ADC-konfig kanal 1
Steg 7:  AD reg 0x82 ch2 = 00 00 02 00 00 31            ADC-konfig kanal 2
Steg 8:  AD reg 0x82 ch3 = 00 00 03 00 00 31            ADC-konfig kanal 3
Steg 9:  AD reg 0x82 ch4 = 00 00 04 00 00 31            ADC-konfig kanal 4
Steg 10: AD reg 0x82 ch5 = 00 00 05 00 00 31            ADC-konfig kanal 5
Steg 11: AD reg 0x82 ch6 = 00 00 06 00 00 31            ADC-konfig kanal 6
Steg 12: AD reg 0x82 ch7 = 00 00 07 00 00 31            ADC-konfig kanal 7
Steg 13: AD reg 0xE5 = 00 18 00 ff ff ff ff             Timing/sync
Steg 14: AD reg 0x6F = 3f ff 23 1f ff ff ff             Kanal-enable-maske
Steg 15: AD reg 0x72 = 00 00 00 02 00 00 00             Trigger-konfig
Steg 16: AD reg 0x10 = 00 00 00 00 ff ff ff             Kontroll
Steg 17: AD reg 0x11 = 00 00 00 00 ff ff ff             Kontroll
Steg 18: AD reg 0x07 = 03 00 00 00 ff ff ff             Mode/kontroll
Steg 19: AD reg 0x9C = 00 64 00 64 ff ff ff             Filter-konfig
Steg 20: AD reg 0x98 = 02 14 32 00 00 00 00             Desimering/averaging
Steg 21: AD reg 0x99 = 60 60 00 00 ff ff ff             Sample timing
Steg 22: AD reg 0x9D = 00 00 00 00 00 00 00             ???
Steg 23: AD reg 0x96 = ff ff ff ff ff ff ff             Status-sjekk (les)
Steg 24: AD reg 0xD0 = 00 00 00 01 ff ff ff             Stream enable
Steg 25: AD reg 0x68 = 00 00 00 ff ff ff ff             DMA/transfer-konfig
Steg 26: AD reg 0xCC = 00 00 00 c0 ff ff ff             ???
Steg 27: AD reg 0xCD = 00 00 01 ff ff ff ff             ???
Steg 28: AD reg 0xCA = 00 10 00 10 00 10 00 10          Kalibrering
Steg 29: AD reg 0xCB = 00 10 00 10 00 10 00 10          Kalibrering
Steg 30: AD reg 0xCE = 10 10 00 00 00 00 00 00          ???
Steg 31: AD reg 0xCF = 00 00 00 00 ff ff ff             ???
Steg 32: AD reg 0x84 = 00 00 00 00 00 00 00             Clear status
Steg 33: AD reg 0xC8 = ff ff ff ff ff ff ff             Status readback (les)
Steg 34: AD reg 0x64 = ff ff ff ff ff ff ff             Status readback (les)
Steg 35: AD reg 0x02 = ff ff ff ff ff ff ff  <<<---     START STREAMING TRIGGER
```
Poll med B1 etter reg 0x02 til status=0x01 (~137ms). Deretter strøymer EP2.

**Post-start (valfritt):**
```
AD reg 0x03 = les (verifikasjon)
AD reg 0x65, 0xC9, 0x97, 0x07, 0x0D, 0x0B = readbacks
```

**Tidslinje fraa pcapng:**
| Fase | Tid (s) | Varighet | Beskriving |
|------|---------|----------|------------|
| Init | 14.28-14.48 | ~200ms | A1, A0 01, 00, A8 x64, B0 3F 0C |
| Idle/polling | 14.5-50.2 | ~36s | AD register-lesing per slot (1107 AD-cmds) |
| Kanal-konfig | 50.2-50.88 | ~680ms | Per-kanal AD skriving (reg 0x13/0x14) |
| Start #1 | 50.88-51.32 | ~440ms | Start-sekvens → EP2 startar (2 pakkar) |
| Start #2 | 51.47-51.73 | ~260ms | Identisk re-start |
| Streaming | 51.78-104.9 | ~53s | Kontinuerleg EP2 (15872B, 20pkt/s, 20kHz) |

**EP2 data-format (ved 20 kHz):**
- Pakkestorleik: 15 872 bytes (alltid)
- = 992 frames x 8 kanalar x 2 bytes (int16 LE)
- Rate: ~20 pakkar/sek (~50ms intervall)
- Datarate: ~317 KB/s

### Init-sekvens (DewesoftX-replika)
```
Fase 1: AE telemetri x4 (heartbeat)
Fase 2: 00 (FW-versjon), A0 01 (aktiver), A1 (slottar), AC (slot-typar),
        A8 (EEPROM), B0 3F 0C (init-reset)
Fase 3: AD 08 (slot-query), AD 0C (slot-enum x3), AD 1C (batch)
Fase 4: Per-slot (A5 kommando-dispatch, sampling-konfig, trigger-lesingar)
Fase 5: Flush EP2/EP4/EP6
```

**Problem med init**: Per-slot initialisering (Fase 4) feilar med poll-timeout paa
alle 4 slottar. Hovudkontrollaren responderer paa enkle kommandoar (AE, A0, A1, AC, B0)
men ikkje paa per-slot register-lesingar (AD op=0x14).

## Status per 2026-02-26

### Hovudfunksjonar

#### USB/IP-modus (generisk)
- [x] Alle Dewesoft-instrument (SIRIUS, KRYPTON, IOLITE, MINITAURs) kan delast via USB/IP
- [x] `usbip bind` + `usbipd` på port 3240
- [x] Windows-klient: `usbip attach` → DewesoftX ser instrument som lokal USB
- [x] Robust cleanup av stale kernel-state (kill usbipd → unbind → bind → start)
- [x] Automatisk gjenoppretting ved container-restart (modus-persistens)

#### Direkte-modus (SIRIUS-spesifikk)
- [x] Native SIRIUS USB-driver (reverse-engineered protokoll)
- [x] USB-tilkobling med auto-detach, set_configuration
- [x] EP1 kommandokanal (send/motta kommandoar)
- [x] EP2 ADC-streaming ved 20 kHz, 8 kanalar
- [x] Start-acquisition sekvens (34 register + reg 0x02 trigger)
- [x] Full init-sekvens replay frå pcapng (~1000 AD-kommandoar)
- [x] Lo-LV slot-initialisering (slot 4-7, 223 kommandoar frå pcapng)
- [x] Excitation-spenning 5V unipolar for Rogowski-integrator
- [x] ADC nullpunkt auto-kalibrering (fjernar DC-offset ~-420 counts)
- [x] Konfigurerbar to-punkt sensor-skalering per kanal

#### openDAQ Nettverksbro
- [x] openDAQ v3.31 kompilert frå kildekode (aarch64 Docker)
- [x] Dynamisk antal ADC-kanalar (0..N basert på modus og tilkobling)
- [x] MQTT-kanalar som ekstra openDAQ-signalar (bonus-sensorar)
- [x] NativeStreaming på :7420 med mDNS-annonsering
- [x] OPC-UA på :4840 (intern, utan mDNS — unngår duplikat i DewesoftX)
- [x] DewesoftX oppdagar og koplar til eininga via `daq.nd://`

#### MQTT Virtuelle Kanalar
- [x] Abonner på vilkårlege MQTT-topics (JSON eller rå payload)
- [x] JSON-sti-ekstraksjon (t.d. `temperature.value`)
- [x] Konfigurerbar range (min/max), eining, namn per kanal
- [x] MQTT-kanalar synlege i DewesoftX saman med ADC-kanalar
- [x] Fungerer i begge modusar (med eller utan SIRIUS tilkobla)
- [x] Web UI for full MQTT broker- og kanal-konfigurasjon

#### Modus-persistens
- [x] Sist aktive modus lagra i `/data/konfig/modus.json`
- [x] Automatisk gjenoppretting ved container-restart
- [x] USB/IP: Hoppar over SIRIUS-tilkobling, startar usbipd automatisk
- [x] Direkte: Koplar til SIRIUS-driver, startar ADC-streaming
- [x] `GET /api/modus` returnerer gjeldande modus

#### Generisk instrument-støtte
- [x] Instrument-modell og serienummer via env vars (`OPENDAQ_MODEL`, `OPENDAQ_SERIAL`)
- [x] C++ openDAQ-patch les modell frå env var ved runtime
- [x] docker-entrypoint.sh brukar env vars i alle konfig-filer
- [x] Generisk ±10V standard kanalkonfig (v11) for ukjende instrument
- [x] Frontend og Web UI utan SIRIUS-spesifikke referansar

#### Web UI (React + Tailwind CSS)
- [x] Dashboard med live status for alle tenester
- [x] SIRIUS direkte-status med Start/Stopp/Rekoble
- [x] USB/IP-panel med Del/Stopp/klient-instruksjonar
- [x] MQTT-konfigurasjon (broker + topics)
- [x] Kanal-konfigurasjon med sensor-skalering og excitation
- [x] Debug-konsoll (send vilkårleg hex til EP1)
- [x] Sidebar med kanal-liste og live-verdiar
- [x] Kanal-detaljside med sparkline og statistikk
- [x] Tailwind CSS (migrert frå eigendefinert CSS)

### Hardware-oppsett (SIRIUSi-HS testrig)

| Slot | Type | Forsterkar | ADC-range | Sensor | Excitation |
|------|------|-----------|-----------|--------|------------|
| 0-2 | Hi-LV | SIRIUS-HS-HVv2 | +/-1600V | Direkte spenning | N/A |
| 3 | Hi-LV | SIRIUS-HS-HVv2 | +/-1600V | Inaktiv | N/A |
| 4-6 | Lo-LV | SIRIUS-HS-LVv2 / LV-LEMO10+v2 | +/-5V | Rogowski 6kA (0-3V = 0-6000A) | 5V unipolar |
| 7 | Lo-LV | SIRIUS-HS-LV-LEMO10+v2 | +/-5V | Inaktiv | Av |

**Lo-LV LEMO 9-pin kontakt (STG module DSUB 9pin):**
```
Pin 1 (Exc+)  +5V til integrator
Pin 2 (In+)   + signal fraa integrator (0-10Vdc)
Pin 3 (Sns-)  GND (minus til integrator)
Pin 4 (GND)   GND
Pin 5 (R+)    Ikkje brukt
Pin 6 (Sns+)  +5V til integrator (sense)
Pin 7 (In-)   Bygla til Pin 4 GND (single-ended)
Pin 8 (Exc-)  GND (minus til integrator)
Pin 9 (TEDS)  DS2431+ TEDS-chip (sensor-ID)
```

### Framtidig
- [ ] TEDS-lesing gjennom SIRIUS USB-protokoll (register 0x15 eller A5 sub-cmd)
- [ ] Auto-konfigurasjon av sensor-skalering basert på TEDS-data
- [ ] Korrekt AC RMS-berekning (per-syklus i staden for per-blokk)
- [ ] Fleire excitation-alternativ (2.5V, 10V)
- [ ] Støtte for fleire USB-instrument samstundes
- [ ] WebSocket live-data til frontend (erstatte polling)

### TEDS (Transducer Electronic Data Sheet) - UNDER UTFORSKING

**Kva er TEDS:** IEEE 1451.4 standard for sensor-metadata lagra i EEPROM (DS2431+)
innebygd i proben/sensorkontakten. Inneheld produsent, modell, kalibrering, range, etc.

**DS2431+ 1-Wire EEPROM:**
- 128 bytes (4 pages x 32 bytes) + 16 bytes kontrollregister
- Family code: 0x2D, 8-byte ROM ID (family + 48-bit serial + CRC8)
- 1-Wire protokoll: Reset → Skip ROM (0xCC) → Read Memory (0xF0) → adresse → data
- Parasittisk straumforsyning (treng ikkje ekstern VCC)
- Pull-up motstand ~4.7K paa datalinja

**IEEE 1451.4 TEDS dataformat (128 bytes):**
```
Byte 0:        Sjekksum (XOR av bytes 1-31 i page 0)
Bytes 1-8:     Basic TEDS (64-bit bitstraum, LSB-fyrst):
                 Bit 0-13:  Manufacturer ID (14 bit)
                 Bit 14-28: Model Number (15 bit)
                 Bit 29-33: Version Letter (5 bit, A=1..Z=26)
                 Bit 34-39: Version Number (6 bit)
                 Bit 40-63: Serial Number (24 bit)
Bytes 9-31:    Template TEDS data (template selector + sensorspesifikke felt)
Bytes 32-127:  Kalibrering, brukardata, ekstra template-data (3 pages)
```

**Template-typar (byte 9, bit 64-71):**
- 25 = IEPE akselerometer/kraft
- 30 = Hoegnivaa spenning
- 33 = Resistiv bru (strain gauge)
- 36 = Termoelement
- 37 = RTD

**Tilgang via SIRIUS Lo-LV (Class 2 MMI):**
- Pin 9 (TEDS) paa LEMO-kontakten er dedikert 1-Wire datalinje
- Class 2: Direkte 1-Wire tilgang (ingen polaritetsreversering som Class 1/IEPE)
- SIRIUS har truleg innebygd 1-Wire master i kvar slot-forsterkar

**Pcapng-analyse (sirius1 + sirius2):**
- DewesoftX probar **register 0x15** med sub-adresser: 0x80, 0xA0, 0xA8, 0xAA, 0xEA, 0xFA, 0xFE, 0xFF
- Alle svar er `0xFF` (ingen TEDS-data returnert)
- Register 0x15 vart lese FOER Lo-LV init — 1-Wire-brua var truleg ikkje aktiv
- Ingen andre TEDS-spesifikke kommandoar funne i pcapng-data
- EEPROM (A8) inneheld berre enheitsdata (serienr, lisensnokkel, fabrikkalibrering)

**Probe Tester App (D:\\Koding\\Probe_tester_app):**
- Separat app med Arduino som les DS2431+ direkte via 1-Wire
- Arduino sender data over seriell (COM3, 115200 baud):
  - `Fabrikk-ID:` — ROM-ID fraa DS2431+
  - `Custom-ID:` — brukar-ID (skriv med `SETID`-kommando)
  - `Strøm:` / `Motstand:` — elektriske maalingar
- Stadfester at probane HAR DS2431+ TEDS-chip
- Kan lese og skrive EEPROM (128 bytes)

**Uloyst: Korleis lese TEDS gjennom SIRIUS USB-protokoll?**
- Register 0x15 er kandidat, men returnerte berre 0xFF
- Mogleg at 1-Wire-brua krev init/excitation foer den responderer
- Kan vere ukjende A5 sub-kommandoar (0x05, 0x07, 0x08?) for 1-Wire tilgang
- Alternativ: Fange ny pcapng medan DewesoftX eksplisitt les TEDS

### Framtidig
- [ ] Verifiser straumverdiar mot kjend last (meir noyaktig kalibrering)
- [ ] DewesoftX viser korrekte einingar (A for straum, V for spenning)
- [ ] Fleire excitation-alternativ (2.5V, 10V) viss noedvendig
- [ ] TEDS-lesing gjennom SIRIUS USB-protokoll (register 0x15 eller A5 sub-cmd)
- [ ] Auto-konfigurasjon av sensor-skalering basert paa TEDS-data
- [ ] Korrekt AC RMS-berekning (per-syklus i staden for per-blokk)

## Feilsoekingshistorikk

### Problem 1: openDAQ import feiler
**Symptom:** `import opendaq` feiler i Python
**Aarsak:** Modul-stiar ikkje konfigurert, .module.so-filer ikkje funne
**Loysing:** `InstanceBuilder.add_module_path("/usr/local/lib")`, OPENDAQ_MODULE_PATH env

### Problem 2: OPC-UA endpoint annonserer 127.0.0.1
**Symptom:** DewesoftX kan ikkje koble til OPC-UA
**Aarsak:** Docker mapper hostname til 127.0.1.1 i /etc/hosts, open62541 brukar gethostname()
**Loysing:** Omskriv /etc/hosts i docker-entrypoint.sh med riktig IP (commit 127e68f)

### Problem 3: sed -i feiler i Docker
**Symptom:** Container krasjar med "cannot rename: Device or resource busy"
**Aarsak:** Docker bind-monter /etc/hosts, sed -i lagar tempfil+rename som feiler
**Loysing:** `sed ... > /tmp/hosts.fixed && cat /tmp/hosts.fixed > /etc/hosts` (commit dd89869)

### Problem 4: DewesoftX "Dewesoft NET" feil protokoll
**Symptom:** DewesoftX "Connect Failed" via "Dewesoft NET"
**Aarsak:** "Dewesoft NET" brukar Telnet+TCP, ikkje OPC-UA/openDAQ
**Loysing:** Bruk native openDAQ-oppdaging i DewesoftX (ikkje "Dewesoft NET")

### Problem 5: Ingen mDNS-annonsering
**Symptom:** DewesoftX finn ikkje eininga
**Aarsak:** openDAQ .module.so-filene inkluderer ikkje mDNS-modul
**Loysing:** avahi service-fil paa Pi host (/etc/avahi/services/opendaq.service)

### Problem 6: 0x80000006 - eining som sub-device
**Symptom:** DewesoftX feilar med "not part of target structure"
**Aarsak:** `instance.add_device()` legg eininga som sub-device (0 kanalar paa root)
**Loysing:** `builder.set_root_device("daqref://device0")` (commit 0a7a00c)

### Problem 7: 0x80000014 - GetAvailableFunctionBlockTypes
**Symptom:** DewesoftX krasjar ved oppsett av eininga
**Aarsak:** Versjonsmismatch mellom openDAQ 3.31 server og DewesoftX 2025.3 klient
**Loysing:** Deaktiver libref_fb_module i docker-entrypoint.sh (commit b8c2f06)

### Problem 8: Tomme server capability connection strings
**Symptom:** DewesoftX feilar med 0x80000006 ved NewSetup
**Aarsak:** openDAQ sin mDNS-baserte interface-oppdaging feiler i Docker
**Loysing:** Manuell setting av PrimaryConnectionString og Addresses (commit efd4f15)

### Problem 9: GetDomain crash med set_root_device
**Symptom:** openDAQ krasjar med "Component not part of target structure"
**Aarsak:** `set_root_device()` gir root-device som ikkje stottar GetDomain
**Loysing:** Bruk `add_device("daqref://device0")` i staden (commit c05af35)

### Problem 10: EBUSY ved reconnect (start/stopp streaming-syklus)
**Symptom:** USB Errno 16 (EBUSY) naar streaming startar/stoppar ved reconnect
**Aarsak:** Start→stopp→start-syklus etterlatar USB endpoint i busy-tilstand
**Loysing:** Kontinuerleg streaming-modell - start EIN GONG ved boot, aldri stopp.
  Autonom maaler les fraa buffer (hent_data) i staden for start/stopp (commit 0f30105)

### Problem 11: Cascading EBUSY naar EP2 er daud
**Symptom:** EP2 timeout ved oppstart → streaming startar likevel → EBUSY-flaum
**Aarsak:** Streaming starta utan aa sjekke ep2_ok
**Loysing:** ep2_ok property, sjekk i alle streaming-start-stiar (commit 23d6425)

### Problem 12: EP2 timeout etter tidlegare init (DELVIS LOYST)
**Symptom:** EP2 (0x82) returnerer aldri data, timeout etter 5 sekunder
**Aarsak:** Tidlegare init-sekvens (A0/B0/AD-kommandoar) stoppa EP2-streaming.
  SIRIUS hovudkontrollar bevarer denne tilstanden sjolv etter USB-fråkopling,
  sysfs reset, og alle kjende EP1-kommandoar.
**Prøvd (kommando-basert, alle feila):**
- sysfs USB power-cycle (authorized 0→1): FX2 re-enumererer, EP2 framleis daud
- A0 modus-toggle (00→01): Ingen effekt
- B0 init-reset + A0 01: Ingen effekt
- Full init-sekvens: Per-slot init feilar (poll timeout), EP2 framleis daud
- Per-slot commit: Ingen effekt
- Alternative A0-verdiar (02,03,04,10,80,FF): Ingen effekt
- B0 med ulike parametrar (00/00, 3F/00, 00/0C, FF/FF, 7F/0C): Ingen effekt
- sysfs + full init + A0 toggle: Ingen effekt
**Prøvd (hardware-reset, commit ab9a419):**
- dev.reset() (USB bus reset): FX2 rebootter, men EP2 framleis daud etter reconnect
- uhubctl power-cycle: Fysisk straumkutt, device forsvinn, kjem attende men EP2 timeout
**GJENNOMBROT (docker rebuild + restart):**
- Etter `docker compose up --build -d` → EP2 fungerer ved boot!
- Fyrste reelle ADC-data: 10112 samples, 8 kanalar, RMS ~7455/5313
- EP2 streamer utan nokon init-sekvens, akkurat som factory-fresh
**Rotårsak:** Docker rebuild → Pi restart USB-stakken → full re-enumerering →
  FX2 rebootter → EP2 streamer naturleg (ingen init-kommandoar sendt)
**Status:** EP2 fungerer ved boot. Problem er at Rekoble knappen øydelegg det.

### Problem 13: Rekoble øydelegg fungerande EP2 (LOYST)
**Symptom:** EP2 fungerer ved boot, brukar klikkar Rekoble, EP2 døyr
**Årsak (flerfaldig):**
1. Gammal streaming-tråd held EP2 oppteken → ny driver får EBUSY
2. rekoble_driver() oppretta ny SiriusDriver utan å stoppe gammal streaming
3. koble_til() trigga aggressiv EP2-recovery (sysfs reset → 9 strategiar)
   som faktisk ØYDELA det fungerande EP2
4. sysfs reset under aktiv streaming → cascading I/O errors (Errno 19, 5)
5. uhubctl: for kort vent, pyusb-cache hadde stale device handle
**Loysning:**
- koble_til() er no enkel: berre _koble_til_intern(), INGEN auto-recovery
- rekoble() stoppar streaming FYRST, deretter clean disconnect/reconnect
- rekoble_driver() stoppar streaming eksplisitt før rekoble
- forsok_gjenoppliv_ep2() stoppar streaming før recovery-strategiar
- uhubctl: lenger vent (8s), pyusb cache-tømming, retry-loop for reconnect
- EP2 recovery er no MANUELT (Gjenoppliv EP2-knappen), ikkje auto-trigga

### Problem 14: Lo-LV slottar ikkje initialiserte (LOYST)
**Symptom:** Lo-LV kanalar 4-6 (Rogowski straumprobar) viste berre stoy/null i DewesoftX
**Aarsak:** Init-sekvensen (INIT_SEKVENS) inneheldt berre Hi-LV slottar 0-3.
  Lo-LV slottar 4-7 krev ein eigen init-sekvens med 223 kommandoar:
  kalibrering (A5 03 9A xx), filter, modus, excitation (A5 02 C3 01 / A5 02 BC 04),
  og slot-kalibrering (A5 04 E9 xx). Utan denne sekvensen responderer ikkje
  forsterkarane og excitation-spenning vert ikkje aktivert.
**Kjelde:** `sirius2.pcapng` - Wireshark-fangst med Sundet-oppsett (alle 8 kanalar aktive)
**Loysing:** Ekstraherte 4 slot-spesifikke init-sekvensar (LV_SLOT4_INIT..LV_SLOT7_INIT)
  og la dei til i `sirius_init_sekvens.py`. Replay i `_start_acquisition()` (commit 98683eb)

### Problem 15: Lo-LV init-rekkefolgje feil (LOYST)
**Symptom:** Lo-LV init vart lagt ETTER A4 pre-start. Kanalar viste framleis berre
  stoy med ~-430 raw count DC-offset, sjolv med 7kW 3-fase last.
**Aarsak:** DewesoftX sender Lo-LV slot-init (register 0x13 skriving) FOER A4 00 (pre-start).
  Naar init kom etter A4 var forsterkarkonfigurasjonen ikkje aktiv under streaming.
**Loysing:** Flytta Lo-LV init til "Steg 0" i `_start_acquisition()`, foer A4 pre-start
  og excitation-oppsett. Matchande rekkefolgje som DewesoftX (commit 98683eb)

### Problem 16: ADC DC-offset gir feil straumverdiar (LOYST)
**Symptom:** Straum-kanalar viste ~131A RMS med 7kW last (forventa ~10A per fase).
  Spenningskanalar viste ~23V phantom paa inaktiv kanal 3.
**Aarsak:** Alle ADC-kanalar har ein konstant DC-offset paa ~-420 raw int16 counts.
  Denne offseten dominerte dei relativt smaa straumsignala (~150 counts peak-to-peak).
  Offset-kjelda er truleg forsterkar-referansedrift eller ADC-nullpunkt-drift.
**Loysing:** Auto-kalibrering av ADC-nullpunkt i `opendaq_bro.py`:
  1. Akkumulerer raatt ADC-gjennomsnitt over fyrste 40 datablokker (~2 sekund)
  2. Lagrar per-kanal DC-offset i `_adc_nullpunkt`
  3. Subtraherer offset (i skalerte einingar) fraa alle etterfylgjande data
  4. Reset ved kanal-rekonfigurering
  **Resultat:** kanal 0-2: ~224V RMS (korrekt 230V AC), kanal 3: 0.07V,
  kanal 4-6: 14-23A RMS (korrekt for 7kW/3 fasar), kanal 7: 0.00 (commit 98683eb)

## Git-commits (kronologisk)

| Commit | Beskriving |
|--------|-----------|
| f55e67c | Oppretta opendaq_bro.py |
| d219f07 | Dockerfile COPY og sirius_server-integrasjon |
| a63aff7 | Web UI dashboard-kort for openDAQ |
| 9ea80ff | Docker entrypoint-oppdatering |
| 64580e5 | Modul-sti debugging |
| 127e68f | Fix OPC-UA endpoint 127.0.0.1 |
| dd89869 | Fix sed -i paa Docker bind-mount |
| 0a7a00c | set_root_device i staden for add_device |
| b8c2f06 | Deaktiver ubrukte openDAQ-modular |
| efd4f15 | Fiks tomme server capability connection strings |
| c05af35 | Fix DewesoftX GetDomain crash: use add_device instead of set_root_device |
| 06b5e61 | Fix EBUSY on reconnect: proper USB release + EBUSY retry logic |
| 0f30105 | Continuous streaming: eliminate start/stop cycle causing EBUSY |
| 23d6425 | Skip streaming when EP2 test fails - prevent cascading EBUSY errors |
| 31e4453 | Auto-recover EP2 via sysfs USB power-cycle when EP2 test fails |
| 3257461 | EP2 revival: 7 command strategies to restart dead ADC streaming |
| ab9a419 | EP2 hardware reset: dev.reset() + uhubctl power-cycle |
| 595a29a | Revert serial til DB19106004 (Dewesoft instrument-serial) |
| 246dc25 | Fix DewesoftX GetDisplayName crash: system.ini + korrekt XML |
| e85a276 | Fix "Invalid or no data": GetPossibleSampleRate + lisens-fiks |
| 482c14c | Fix GetPossibleSampleRate: IntProperty i staden for ListProperty |
| 2bbf915 | Try FloatProperty for GetPossibleSampleRate |
| 716277b | Fix root cause: handle custom namespace DataTypes i OPC-UA |
| d5fb650 | Optimaliser data acquisition rate fraa 2 kHz til 20 kHz |
| da595a0 | Oppdater feilsokingslogg med Fase 8: acquisition rate 2→20 kHz |
| c1f033b | Fix Dockerfile: bruk arkitektur-spesifikke kompilatorflagg |
| 34e6a4a | Verifisert: DewesoftX mottek data ved 20000 Hz |
| 8df83bb | Konfigurerbar to-punkt sensor-skalering per kanal med Web UI |
| 8d33208 | Fix validering: godta 8 eller 9 kanalar i PUT /api/kanalar |
| 4fa5be4 | Excitation voltage for Lo-LV integrator + UI-forbetringar |
| 0b54e09 | Excitation-spenning konfigurerbar per kanal i Web UI |
| 98683eb | Lo-LV slot-init, excitation og ADC nullpunkt auto-kalibrering |
| f62147c | Migrer frontend frå eigendefinert CSS til Tailwind CSS |
| 0414a0a | Fix UI-hopping: ikkje re-sett loading ved kvar polling-runde |
| cc5f0c0 | Gjer Oversikt-korta meir kompakte |
| 2a2eddd | Oppdater serienummer til D019274CF6 (USB-serial knytt til lisens) |
| fa532e1 | Les lisens frå SIRIUS EEPROM og skriv til system_ds.lic |
| 60ab534 | Flytt lisens-EEPROM-lesing til koble_til() så den alltid køyrer |
| d77e928 | Prøv zlib-komprimert lisens for DewesoftX GetUncompressedLicense |
| adb9add | Legg til MQTT virtuelle kanalar som ekstra openDAQ-kanalar |
| aad09cb | Legg til mqtt_konfig.py og mqtt_klient.py i Dockerfile COPY |
| 4e53329 | Vis MQTT-kanalar i sidebar/dashboard og fjern Tid-kanal |
| fe13407 | MQTT uavhengig av SIRIUS, konfigurerbart ADC-kanaltal, kanalnummerering |
| fbfc832 | Berre aktive kanalar i broen, fjerna simulering |
| 78d7f94 | Persist driftsmodus (USB/IP vs direkte) across container restarts |
| 1894585 | Restart bridge med antal_adc=0 ved USB/IP-modus |
| 8d2e66e | Fix USB/IP 'Device busy (already exported)' permanent |
| ebabc4e | Disable OPC-UA mDNS discovery to fix duplicate device in DewesoftX |
| d045bf1 | Generisk instrument-støtte: fjern all SIRIUS-spesifikk hardkoding |

## Filar

| Fil | Beskriving |
|-----|-----------|
| **Backend (Python)** | |
| sirius_server.py | Hovudserver: driver + openDAQ bro + web UI + autonom måling + modus-logikk |
| sirius_driver.py | SIRIUS USB-driver: tilkobling, init, Lo-LV init, excitation, streaming |
| sirius_protokoll_impl.py | Lavnivå USB-protokoll (EP1 kommandoar, AD/B1 poll) |
| sirius_init_sekvens.py | Init-sekvensar frå pcapng: INIT_SEKVENS + LV_SLOT4-7_INIT |
| opendaq_bro.py | openDAQ nettverksbro (dynamisk ADC + MQTT kanalar, NativeStreaming, OPC-UA) |
| web_ui.py | Flask web API med live status, modus-byte, kanal/MQTT-konfig |
| enhet_konfig.py | Eining-konfigurasjon + modus-persistens (les_modus/lagre_modus) |
| kanal_konfig.py | Kanal-konfigurasjon datamodell og JSON-persistens (v11 generisk) |
| mqtt_konfig.py | MQTT-konfigurasjon: broker-tilkobling og kanal-definisjonar |
| mqtt_klient.py | MQTT-klient: abonnement, JSON-parsing, live-verdiar |
| usbip_manager.py | USB/IP-styring: bind/unbind/usbipd med stale-state cleanup |
| **Docker** | |
| Dockerfile | Multi-stage: bygg openDAQ v3.31 + runtime (generisk instrument-patch) |
| docker-compose.yml | Docker Compose: pqtech-opendaq, macvlan, env vars |
| docker-entrypoint.sh | Container oppstart: hostname-fiks, modul-deaktivering, env var-konfig |
| **Frontend (React + Tailwind)** | |
| frontend/ | Vite + React + TypeScript + Tailwind CSS |
| frontend/src/App.tsx | Hovudlayout: sidebar med kanalar + hovudinnhald |
| frontend/src/components/ | Dashboard-kort: SiriusStatus, UsbIp, Mqtt, DeviceConnection, ChannelConfig, Debug |
| frontend/src/pages/ | ChannelPage (kanal-detalj med sparkline og statistikk) |
| frontend/src/api/ | API-klientar: sirius, mqtt, kanalar, opendaq |
| **Verktøy** | |
| _deploy.py | Deploy-skript: kopier filer til Pi-container via SSH/docker cp |
| _hent_live.py | Hjelpar: hent live-data frå container via SSH |
| sirius_adc_leser.py | Referanse: enkel EP2-lesar (factory-fresh device) |
| sirius_sniffer.py | USB-trafikk sniffer for protokoll-analyse |
| sirius_dekoder.py | Dekoder for sniffa USB-pakkar |

## Portar

| Port | Teneste |
|------|---------|
| 8080 | Web UI (Flask) |
| 3240 | USB/IP daemon (usbipd) — berre i USB/IP-modus |
| 4840 | OPC-UA (openDAQ, intern — ikkje annonsert via mDNS) |
| 7420 | Native Streaming (openDAQ, mDNS-annonsert) |
| 7414 | WebSocket/LT Streaming (openDAQ) |

## Andre Dewesoft-verktoy tilgjengelege

| Mappe | Beskriving | Relevant? |
|-------|-----------|-----------|
| DWDataReader_v5_0_4 | Fil-lesar for Dewesoft .dw datafiler (Python/C/C#/Matlab) | Nei - les lagra filer, ikkje live |
| DSRemoteConnect | Fjernkontroll-API for DewesoftX (DCOM/TCP) | Indirekte - viser at "start measurement" er eksplisitt steg, men abstraherer USB-protokollen |

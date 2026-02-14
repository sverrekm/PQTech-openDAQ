# openDAQ SIRIUS Bridge - Prosjektlogg

## Oversikt

Raspberry Pi 5 med Dewesoft SIRIUSi-HS (8 AI-kanalar) tilkobla via USB.
Docker-container kjoerer native SIRIUS-driver for autonome maalingar,
og openDAQ nettverksbro for DewesoftX-tilkobling over nettverk.

**Maal:** DewesoftX paa Windows skal kunne koble til SIRIUS via openDAQ-protokollen
over nettverket, samtidig som native driver held fram med autonome maalingar.

## Hardware

| Komponent | Detaljar |
|-----------|----------|
| Datamaskin | Raspberry Pi 5 |
| Instrument | Dewesoft SIRIUSi-HS, 8 AI-kanalar, S/N D019274CF6, FW 2.11.1.1 |
| Tilkobling | USB direkte til Pi (Bus 3, VID=0x1CED, PID=0x1002) |
| Pi nettverk | WiFi 192.168.1.160, Kabla 192.168.1.53 |
| Windows PC | 192.168.1.100 (DewesoftX 2025.3 DEMO) |

## Arkitektur

```
SIRIUS ──USB──> SiriusDriver (native, reverse-engineered)
                    |
                    +──> Kontinuerleg EP2-streaming (start ved boot, aldri stopp)
                    +──> Autonom maaler (les fraa buffer, CSV/NPZ kvar 60s)
                    +──> Web UI (:8080, live kanal-verdiar)
                    |
              OpenDAQBro (opendaq_bro.py)
                    |
                    +──> daqref://device0 (sub-device, 8 AI-kanalar)
                    +──> oppdater_data() injiserer ADC-data i openDAQ-signal
                    +──> OPC-UA server (:4840)
                    +──> Native Streaming (:7420)
                    +──> WebSocket/LT Streaming (:7414)
                    +──> mDNS via avahi (Pi host)
                    |
              DewesoftX (Windows)
                    +──> Oppdagar eininga via mDNS
                    +──> Koplar til via OPC-UA + Native Streaming
```

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

## Status per 2026-02-14

### Fungerer
- [x] Native SIRIUS USB-driver (reverse-engineered protokoll)
- [x] USB-tilkobling med auto-detach, set_configuration
- [x] EP1 kommandokanal (send/motta kommandoar)
- [x] USB string descriptor-lesing (produsent, produkt, serienr)
- [x] sysfs USB power-cycle (deauthorize/reauthorize via /sys/bus/usb)
- [x] Kontinuerleg streaming-modell (start ein gong, aldri stopp)
- [x] EP2 status-tracking (ep2_ok property)
- [x] 7-strategis EP2 gjenoppliving (kommando-basert)
- [x] Kanal-konfig med JSON-persistens (/data/konfig/kanalar.json)
- [x] Autonom maaling med CSV/NPZ-lagring (samlar fraa buffer)
- [x] Web UI paa port 8080 med live status, Rekoble, Gjenoppliv EP2
- [x] Debug-kommando i web UI (send vilkaarleg hex til EP1)
- [x] openDAQ kompilert fraa kildekode (v3.31, aarch64 Docker)
- [x] openDAQ nettverksbro med 8 AI-kanalar
- [x] OPC-UA server paa :4840
- [x] Native Streaming paa :7420
- [x] mDNS-annonsering via avahi
- [x] DewesoftX oppdagar og koplar til eininga

### EP2 STATUS: Fungerer ved boot!
- EP2 streamer ADC-data ved factory-default (etter docker rebuild/USB re-enumerering)
- Fyrste reelle data: 10112 samples, 8 kanalar, RMS ~7455/5313
- **Ingen "start EP2" kommando trengst** - EP2 er PÅ som standard

### USB-SNIFF FUNN (stadfesta)
- **Init-sekvensen (A0/A1/A8/B0) DREP EP2** - USB-sniff frå DewesoftX stadfester dette
- I opptak MED EP2-data: kun AD/B1-polling og AE-telemetri, INGEN A0/A1/A8/B0
- I opptak UTAN EP2-data: init-sekvens køyrt, EP2 forsvinn

### WIRESHARK PCAPNG FUNN (2026-02-14) - START STREAMING FUNNE!
- **sirius1.pcapng**: Komplett Wireshark USBPcap-capture fraa DewesoftX paa Windows
- **105 sekund**, 73 657 frames, fangar init → idle → "Start Store" → streaming
- **Register 0x02 via AD-kommando** er "Start Acquisition"-triggeren
- **34 register-skrivingar** (sample rate, ADC-konfig, DMA, kalibrering) som preamble
- **A4 00 + AC** sendes før register-sekvensen
- EP2 leverer 15 872 bytes/pakke ved 20 pkt/s (20 kHz, 8 kanalar, int16 LE)
- **EP8 OUT (0x08)** var IKKJE brukt i capturen - start skjer via EP1 AD-kommandoar

### Neste steg
- [x] **Fange DewesoftX "Start Store" USB-trafikk** - GJORT! (sirius1.pcapng)
- [x] **Identifiser start-kommando** - FUNNE! Register 0x02 via AD-kommando
- [ ] **Implementer start-sekvens i sirius_driver.py** - Send 34 register-skrivingar
      + reg 0x02 trigger for aa starte EP2 etter init-sekvensen har stoppa den
- [ ] **Test start-sekvens paa Pi** - Verifiser at EP2 kjem tilbake etter init
- [x] **EP8 OUT (0x08)** - Ikkje brukt av DewesoftX for start. Kan ignorerast.
- [x] **dev.reset()** - Implementert (commit ab9a419). FX2 rebootter men EP2
      kjem ikkje tilbake automatisk (hovudkontrollar bevarer tilstand).
- [x] **uhubctl** - Implementert (commit ab9a419). Power-cycle funkar men
      treng lang ventetid og pyusb cache-tømming.

### Framtidig (etter EP2-start er implementert)
- [ ] Injiser reelle SIRIUS-data i openDAQ-signalar (oppdater_data fungerer)
- [ ] DewesoftX ser live ADC-verdiar
- [ ] Full DewesoftX-integrasjon med 8 kanalar

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

## Filar

| Fil | Beskriving |
|-----|-----------|
| sirius_driver.py | SIRIUS USB-driver: tilkobling, init, streaming, EP2-gjenoppliving |
| sirius_protokoll_impl.py | Lavnivaa USB-protokoll (EP1 kommandoar, AD/B1 poll) |
| sirius_server.py | Server: driver + openDAQ bro + web UI + autonom maaling |
| opendaq_bro.py | openDAQ nettverksbro (OPC-UA, streaming, 8 kanalar) |
| kanal_konfig.py | Kanal-konfigurasjon datamodell og JSON-persistens |
| web_ui.py | Flask web UI med live status, debug, kanal-konfig |
| docker-entrypoint.sh | Container oppstart, hostname-fiks, modul-deaktivering |
| docker-compose.yml | Docker Compose (privileged, host network, /sys mounted) |
| Dockerfile | Multi-stage: bygg openDAQ + runtime |
| sirius_adc_leser.py | Referanse: enkel EP2-lesar (fungerte paa factory-fresh device) |
| sirius_sniffer.py | USB-trafikk sniffer for protokoll-analyse |
| sirius_dekoder.py | Dekoder for sniffa USB-pakkar |

## Portar

| Port | Teneste |
|------|---------|
| 8080 | Web UI (Flask) |
| 4840 | OPC-UA (openDAQ) |
| 7420 | Native Streaming (openDAQ) |
| 7414 | WebSocket/LT Streaming (openDAQ) |

## Andre Dewesoft-verktoy tilgjengelege

| Mappe | Beskriving | Relevant? |
|-------|-----------|-----------|
| DWDataReader_v5_0_4 | Fil-lesar for Dewesoft .dw datafiler (Python/C/C#/Matlab) | Nei - les lagra filer, ikkje live |
| DSRemoteConnect | Fjernkontroll-API for DewesoftX (DCOM/TCP) | Indirekte - viser at "start measurement" er eksplisitt steg, men abstraherer USB-protokollen |

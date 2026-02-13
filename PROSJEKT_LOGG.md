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
- **Ingen kjend kommando** for aa restarte EP2 etter at init har stoppa den

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

## Status per 2026-02-13

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

### KRITISK BLOKKERING: EP2 (ADC-data) er daud
- EP2 vart stoppa av ein tidlegare init-sekvens
- Hovudkontrollaren sin tilstand overlever USB-fråkopling (eigen straumforsyning)
- Alle 7 gjenoppliving-strategiar feilet:
  1. Modus-toggle (A0 00→01): EP2 timeout
  2. Init-reset (B0) + aktiver (A0 01): EP2 timeout
  3. Full init-sekvens: Per-slot init feilar, EP2 timeout
  4. Per-slot commit: EP2 timeout
  5. Alternative A0-modusar (02,03,04,10,80,FF): EP2 timeout
  6. B0 med ulike parametrar: EP2 timeout
  7. sysfs reset + full init + modus-toggle: EP2 timeout

### Neste steg (ikkje prøvd enno)
- [ ] **dev.reset()** - Ekte USB RESET-signal som tvingar FX2 firmware-reboot.
      Forskjellig fraa sysfs authorized som berre er kernel-nivaa drop/reacquire.
      Viss FX2 firmware sin oppstartsrutine sender "start stream" til hovudkontrollar,
      kan dette vere løysinga.
- [ ] **uhubctl** - Ekte USB port power-cycling (kutt straum til porten).
      Viss SIRIUS hovudkontrollar faar straum fraa USB, vil dette resette alt.
- [ ] **EP8 OUT (0x08)** - Uutforska data-endepunkt. DewesoftX kan sende
      "start acquisition" via dette endepunktet.
- [ ] **Fange DewesoftX "Start Store" USB-trafikk** - Wireshark+USBPcap paa Windows
      for aa finne eksakt kommando som startar EP2-streaming.

### Framtidig (etter EP2 er fiksa)
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

### Problem 12: EP2 timeout etter tidlegare init (ULOYST - KRITISK)
**Symptom:** EP2 (0x82) returnerer aldri data, timeout etter 5 sekunder
**Aarsak:** Tidlegare init-sekvens (A0/B0/AD-kommandoar) stoppa EP2-streaming.
  SIRIUS hovudkontrollar bevarer denne tilstanden sjolv etter USB-fråkopling,
  sysfs reset, og alle kjende EP1-kommandoar.
**Prøvd:**
- sysfs USB power-cycle (authorized 0→1): FX2 re-enumererer, EP2 framleis daud
- A0 modus-toggle (00→01): Ingen effekt
- B0 init-reset + A0 01: Ingen effekt
- Full init-sekvens: Per-slot init feilar (poll timeout), EP2 framleis daud
- Per-slot commit: Ingen effekt
- Alternative A0-verdiar (02,03,04,10,80,FF): Ingen effekt
- B0 med ulike parametrar (00/00, 3F/00, 00/0C, FF/FF, 7F/0C): Ingen effekt
- sysfs + full init + A0 toggle: Ingen effekt
**Observasjonar:**
- EP1 fungerer: AE returnerer `0000000000000000` (ikkje FF)
- FW-versjon les OK: `020b0101`
- Slot-typar endrar seg mellom runs (04042804→04040404) = sysfs HAR delvis effekt
- Slot-tilstedevaerelse endrar seg litt mellom runs
- EEPROM returnerer all-FF
- Per-slot register-lesing (AD op=0x14) timeout konsekvent
**Status:** ULOYST. Neste steg: dev.reset(), uhubctl, EP8 OUT, USB-capture.

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

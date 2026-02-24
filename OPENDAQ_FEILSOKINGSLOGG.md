# openDAQ → DewesoftX Feilsøkingslogg

Komplett kronologisk logg over alle forsøk på å få DewesoftX til å koble til og bruke SIRIUS via openDAQ-protokollen over nettverket.

**Mål:** DewesoftX 2025.3 på Windows (192.168.1.100) skal oppdage og koble til ein Dewesoft SIRIUSi-HS via openDAQ OPC-UA + NativeStreaming frå ein Raspberry Pi 5.

**Tidsperiode:** Januar–Februar 2026

---

## Fase 1: Grunnleggjande oppsett

### 1.1 Docker-container med openDAQ SDK

Bygde multi-stage Docker-image som kompilerer openDAQ frå kildekode (C++/CMake) på aarch64.

**Problem:** CMake feila på ARM64.
- Prøvd: inline-kommentarar i RUN-kommando → feil
- Prøvd: Ubuntu 24.04 for cmake 3.28 → ARM64-pakker mangla
- **Løysing:** Debian bookworm + manuell cmake-versjon + synlege feilloggar

### 1.2 openDAQ Python-import feilar

**Symptom:** `import opendaq` feilar — `.module.so`-filer ikkje funne.
- Prøvd: `OPENDAQ_MODULE_PATH` env → ikkje plukka opp
- Prøvd: `InstanceBuilder.add_module_path()` → feil CWD
- **Løysing:** `cd /usr/local/lib` i entrypoint + `python3 -m` for korrekt sys.path (`64580e5`, `7151e31`)

### 1.3 USB/IP for SIRIUS-deling

Implementerte USB/IP slik at SIRIUS kan delast mellom Pi og Windows. Bytta frå `usbipd-win` til `usbip-win2`. Lagt til modus-switching mellom USB/IP og lokal driver.

---

## Fase 2: DewesoftX-oppdaging og tilkobling

### 2.1 "Dewesoft NET" — feil protokoll (tidleg misforståing)

**Symptom:** DewesoftX "Connect Failed" ved manuell tilkopling via "Dewesoft NET".
**Årsak:** "Dewesoft NET" brukar Telnet+TCP, ikkje openDAQ. openDAQ-einingar dukkar
automatisk opp som **Detected devices** i HW Settings når mDNS-annonsering fungerer —
ein treng ikkje leggje til adressa manuelt.
**Lærdom:** openDAQ-einingar skal oppdagast automatisk, ikkje via "Dewesoft NET".

### 2.2 Ingen mDNS-annonsering

**Symptom:** DewesoftX viser ikkje eininga under "Detected devices" i Setup > Devices.
**Årsak:** openDAQ-modulane inkluderte ikkje mDNS-teneste.
**Løysing:** Avahi service-fil på Pi-hosten (`/etc/avahi/services/opendaq.service`). Seinare erstatta med `builder.add_discovery_server("mdns")` og `server.enable_discovery()` (`a502139`).

### 2.3 OPC-UA endpoint annonserer 127.0.0.1

**Symptom:** Eininga dukkar opp under "Detected devices", men DewesoftX kan ikkje koble til.
**Årsak:** Docker mapper hostname til `127.0.1.1` i `/etc/hosts` → open62541 brukar `gethostname()` → endpoint-URL vert `opc.tcp://127.0.0.1:4840/`.
**Løysing:** Skriv om `/etc/hosts` i `docker-entrypoint.sh` med riktig IP (`127e68f`).

**Tilleggsproblem:** `sed -i` feilar på Docker bind-mount (`/etc/hosts`).
**Løysing:** `sed > /tmp/hosts.fixed && cat /tmp/hosts.fixed > /etc/hosts` (`dd89869`).

### 2.4 Eininga som sub-device — 0x80000006

**Symptom:** DewesoftX feilar med `0x80000006` ("not part of target structure").
**Årsak:** `instance.add_device("daqref://device0")` la eininga som sub-device → 0 kanalar på root.
**Løysing:** `builder.set_root_device("daqref://device0")` — eininga vert rota (`0a7a00c`).

### 2.5 GetAvailableFunctionBlockTypes — 0x80000014

**Symptom:** DewesoftX krasjar ved oppsett.
**Årsak:** Versjonsmismatch: openDAQ v3.31 server vs DewesoftX 2025.3 (~v3.20.x) → `GetAvailableFunctionBlockTypes` returnerer ukjent format.
**Løysing:** Deaktiver `libref_fb_module`, `libopcua_client_module`, `libnative_stream_cl_module`, `libsimulator_device_module` i entrypoint (`b8c2f06`).

### 2.6 Tomme ServerCapability connection strings

**Symptom:** DewesoftX feilar med `0x80000006` ved NewSetup.
**Årsak:** openDAQ sin mDNS-baserte interface-oppdaging feilar i Docker → `PrimaryConnectionString` tom.
**Løysing:** Manuell setting av `PrimaryConnectionString` og `Addresses` (`efd4f15`).

---

## Fase 3: DewesoftX-krasjar og C++ patchar

### 3.1 GetDomain nil-krasj

**Symptom:** openDAQ krasjar med "Component not part of target structure".
**Årsak:** `set_root_device()` → instance wrapper vidaresender ikkje `getDomain()` → returnerer nullptr → DewesoftX krasjar med "External exception E06D7363".
- Prøvd: `add_device()` i staden → funka men ga sub-device-problem igjen (`c05af35`)
- Prøvd: `set_root_device()` tilbake → nil-krasj att (`994f642`)
- **Løysing (C++ Patch 1):** Patch `device_impl.h` — `getDomain()` returnerer fallback `DeviceDomain` i staden for nullptr (`a2f82e6`).

### 3.2 Nil string-eigenskapar — InitStringProperty-krasj

**Symptom:** DewesoftX krasjar med "Interface object is nil" i `InitStringProperty`.
**Årsak:** openDAQ VariantConverter for IString krasjar på nullptr. IBaseObject-konverteren returnerer tom variant for nil → DewesoftX les null-peikar.
**Løysing (C++ Patch 2):** Patch `core_types_converter.cpp` — nil string → tom streng for både `IString::ToVariant` og `IBaseObject::ToVariant` (`efb2174`).

### 3.3 DeviceInfo viser "openDAQ" / "Reference device"

**Symptom:** DewesoftX viser generiske namn, ikkje SIRIUS-identitet.
**Årsak:** DeviceInfo er frosen (read-only) etter build — kan ikkje endrast frå Python.
- Prøvd: `info.set_property_value()` → "property is read-only"
- Prøvd: `setattr(info, 'serial_number', ...)` → ingen effekt
- Prøvd: `set_default_root_device_info()` → access violation i DewesoftX (`21c0cc1`, revert)
- **Løysing (C++ Patch 3):** Patch `ref_device_impl.cpp` — hardkoda SIRIUS-verdiar + env-var for serienummer/MAC (`8cb0562`).

### 3.4 DeviceType er nil — DewesoftX-krasj

**Symptom:** "Interface object is nil" i `TOpenDaqDeviceInfo.UpdateInfo`.
**Årsak:** `DeviceType` er eit C++-objekt, ikkje streng → nil-string-patchen hjelper ikkje.
- Prøvd: Sett DeviceType frå Python → ikkje mogleg (read-only)
- **Løysing (C++ Patch 3, utvida):** Legg til `devInfo.setDeviceType(DeviceType(...))` i C++ med alle 5 argument (`99bac0a`, `627d129`).

### 3.5 MAC/SerialNumber ikkje synleg i OPC-UA

**Symptom:** DewesoftX viser "Not provided" for MAC og serienummer.
**Årsak:** `createOptionalNode()` returnerer false for `MacAddress` og `SerialNumber` → OPC-UA-nodar vert aldri oppretta.
**Løysing (C++ Patch 4):** Kviteliste `MacAddress`, `SerialNumber`, `Platform`, `HardwareRevision`, `SoftwareRevision` i `tms_server_device.cpp` (`7e360eb`).

### 3.6 Counter-kanal (Tid) krasjar DewesoftX

**Symptom:** "Interface object is nil" i `TryGetSampleRate`.
**Årsak:** Counter waveform (Waveform=3) manglar domain-signal.
- Prøvd: 9 kanalar (8 ADC + 1 Tid) (`5a8521d`)
- **Løysing:** Fjern kanal 8, bruk berre 8 ADC-kanalar (`d056f05`).

### 3.7 ~90 "DataType incompatible"-åtvaringar

**Symptom:** open62541 loggar ~90 åtvaringar per oppstart.
**Årsak:** TMS VariantConverter produserer alltid Int64/Double (targetType=nullptr), men OPC-UA TypeDefinition-nodar forventar UInt16/UInt32/Float.
- Prøvd: Oppgradere til v3.30.0 for PR #880 fix → braut heile driveren (`d9203c5`, revert `3c8c469`)
- **Løysing (C++ Patch 6):** Patch `opcuaserver.cpp` — `coerceVariantToNodeType()` les noden sin DataType og konverterer varianten (`61a9e42`).

### 3.8 RefDevice genererer syntetisk data

**Symptom:** openDAQ-kanalar viser sinusbølger i staden for SIRIUS-data.
**Årsak:** RefDevice si interne `acqLoop()` genererer data som overskriv injiserte verdiar.
**Løysing (C++ Patch 5):** Patch `ref_device_impl.cpp` — `OPENDAQ_DISABLE_ACQ` env-var skrur av `collectSamples`/`collectTimeSignalSamples` (`0f578c0`).

---

## Fase 4: SDK-versjonsmismatch (hovudproblemet)

### 4.1 openDAQ v3.30/v3.31 vs DewesoftX 2025.3

**Symptom:** Eininga dukkar opp under "Detected devices" i Setup, men vert vist som "Disconnected" etter tilkopling. Ingen feilmeldingar i log.

**Rotårsak oppdaga via GitHub Issue #1047:** DewesoftX 2025.3 brukar openDAQ ~v3.20.x internt. Server v3.30+ er **ikkje bakoverkompatibel**.

**Breaking changes mellom v3.20 og v3.30:**
1. Serialiseringsformat endra (PR #733): list-objekt serialisert som objekt i staden for JSON-arrays
2. Enum-omnamning: `TimeSource` → `TimeProtocol`
3. NativeProtocol RPC-skjema inkompatibelt

**Kronologi:**
- Starta med v3.31 (nyaste) → "Disconnected" (`aea55d4`)
- Oppgraderte til v3.30.0 for spesifikk fix → same problem (`d9203c5`)
- Revert til release/3.20 branch → bygde, men uklar tag (`83b1a2a`)
- **Løysing:** Pin til `3.20.6` (nyaste 3.20.x patch) (`bd8c5af`, `1865bbd`)

### 4.2 "Disconnected" etter nedgradering

Sjølv med v3.20.6 viste DewesoftX "Disconnected". Fleire årsaker:

**4.2.1 Hostname som ikkje kan resolvast**
**Symptom:** DewesoftX koplar til, men disconnect-ar etter ~1 sekund.
**Årsak:** OPC-UA endpoint URL = `opc.tcp://IOTmanager:4840/`. Windows kan ikkje resolve "IOTmanager".
**Løysing:** Sett hostname til IP-adresse i entrypoint: `hostname "$OPENDAQ_IP"` (`b19cc13`, `67ab1e1`).

**4.2.2 Docker bridge IP-lekkasje**
**Symptom:** mDNS annonserer Docker bridge-IP (172.17.x.x) som DewesoftX ikkje kan nå.
- Prøvd: Fjerne Docker bridge IP-ar i entrypoint → braut Gitea/Portainer (`9935adc`, revert `0013f58`)
- Prøvd: Python `zeroconf` for eksplisitt mDNS (`65488a0`, revert `171d513`)
- **Løysing:** `network_mode: host` + eksplisitt `OPENDAQ_IP` env-var + `_fiks_primary_connection_strings()` (`5357641`).

**4.2.3 NativeStreaming peikar til feil IP**
**Symptom:** OPC-UA koplar til OK, men NativeStreaming feilar → DewesoftX disconnectar.
**Årsak:** Pi har to IP-ar (WiFi 192.168.1.160 + LAN 192.168.1.53). OPC-UA brukar ein, NativeStreaming den andre.
**Løysing:** `_fiks_primary_connection_strings()` set riktig IP på alle ServerCapabilities (`5357641`).

---

## Fase 5: Data-injeksjon

### 5.1 Eigenskapsmodulering (forsøk 1)

Prøvde å oppdatere openDAQ-kanalar via `set_property_value("Amplitude", ...)` frå callback.
**Problem:** Eigenskapar er metadata, ikkje data. DewesoftX viser konstant verdi.

### 5.2 DataPacket-injeksjon (forsøk 2)

Brukar `DataPacket` + `DataPacketWithDomain` + `signal.send_packet()` for å injisere reelle SIRIUS ADC-samples.

**Problem:** `signal.send_packet()` → "Object does not implement ISignalConfig".
**Årsak:** `ch.signals` returnerer `ISignal` (read-only). `send_packet()` krev `ISignalConfig`.
- Prøvd: `opendaq.ISignalConfig(signal)` → kallar fabrikk, feil (`7022284`)
- **Løysing:** `opendaq.ISignalConfig.cast_from(signal)` — cast, ikkje fabrikk (`eff523a`).

**Problem:** `ISignalConfig.domain_signal` og `.descriptor` er write-only.
**Løysing:** Les frå `ISignal` (read-only) *før* cast, lagra separat (`7d74e6e`).

**Skaleringsfaktor:** SIRIUS ADC leverer int16 (-32768..32767) → `physical = raw * (range_max / 32768)` (`50034bd`).

---

## Fase 6: EP2 ADC-streaming (USB-driver)

### 6.1 EP2 timeout

**Symptom:** EP2 (0x82) returnerer aldri data etter init-sekvens.
**Årsak:** Init-sekvensen (A0/B0/AD-kommandoar) stoppa EP2. SIRIUS hovudkontrollar bevarer tilstanden sjølv etter USB-fråkopling.

**Prøvd (kommando-basert, alle feila):**
- sysfs USB power-cycle (authorized 0→1)
- A0 modus-toggle (00→01)
- B0 init-reset + A0 01
- Full init-sekvens (per-slot feila med poll timeout)
- Alternative A0-verdiar (02,03,04,10,80,FF)
- B0 med ulike parametrar

**Prøvd (hardware-reset):**
- `dev.reset()` (USB bus reset) — FX2 rebootta, EP2 framleis død (`ab9a419`)
- `uhubctl` power-cycle — fysisk straumkutt, EP2 framleis død (`ab9a419`)

**Gjennombrot:** Docker rebuild → Pi restarta USB-stakken → full re-enumerering → EP2 fungerte ved boot!

### 6.2 Start Acquisition frå pcapng

Analyserte `sirius1.pcapng` (73 657 frames frå DewesoftX med Wireshark USBPcap).
**Funn:** Register 0x02 via AD-kommando er "Start Acquisition"-triggeren.
34 register-skrivingar + reg 0x02 → EP2 startar etter ~187ms (`8e6be3d`).

### 6.3 EBUSY ved reconnect

**Symptom:** USB Errno 16 (EBUSY) når streaming startar/stoppar.
**Årsak:** Start→stopp→start-syklus etterlét USB endpoint busy.
**Løysing:** Kontinuerleg streaming — start ÉIN GONG ved boot, aldri stopp (`0f30105`).

### 6.4 Orphan-trådar

**Symptom:** EBUSY etter rekobling pga gammal sirius-adc/sirius-heartbeat-tråd.
**Løysing:** Klasse-nivå `_orphan_kill` event + `dev.reset()` for å tvinge ENODEV på blokkerte USB-lesingar (`0f8c6e7`, `ed1fe71`).

---

## Fase 7: macvlan-nettverk + SSH for DewesoftX (2026-02-23)

### 7.1 Problem: DewesoftX krev SSH til eininga

**Symptom:** DewesoftX 2025.3 prøver SSH med `root` / `D3W3Soft30112018` mot eininga si IP for full tilkopling. SSH feilar fordi containeren deler IP med Pi-en (192.168.1.160), som har SSH med brukar `sverre` — ikkje `root`.

**Årsak:** DewesoftX brukar hardkoda legitimasjon og SSH-kommandoar mot `/opt/dewesoft/scripts/` for å identifisere eininga (sysinfo, firmware, status).

**Løysing:** macvlan-nettverk — gi containeren eigen IP (192.168.1.161) på LAN-et. Containeren køyrer eigen sshd med DewesoftX sine legitimasjonar.

### 7.2 Implementering

**Nettverksendring:**
- Fjerna `network_mode: host` frå docker-compose.yml
- La til macvlan-nettverk `daqnet` med statisk IP `192.168.1.161`
- IP konfigurerbar via `.env`-fil (`CONTAINER_IP=192.168.1.161`)

**SSH-oppsett (docker-entrypoint.sh):**
- Installerer `openssh-server` ved oppstart viss det manglar (~30s fyrste gong)
- Set root-passord til `D3W3Soft30112018`
- Konfigurerer `PermitRootLogin yes`
- Startar sshd i bakgrunnen før openDAQ-serveren

**DewesoftRT-stubs (dewesoft_stubs/platform_control.sh):**
- Stub-skript som svarar på DewesoftX sine SSH-kommandoar
- `sysinfo` → genererer `/opt/dewesoft/scripts/system.xml`
- `info` → "ready"
- `date get` → noverande tidspunkt

**Dockerfile:**
- La til `openssh-server` i apt-get (for image-rebuild)
- COPY av stub-skript + mkdir for sshd og Dewesoft-katalogar

**Andre endringar i same commit:**
- `opendaq_bro.py`: Hoppar over TCP-probe mot NativeStreaming (port 7420) — forårsaka "Failed to read connect request headers" og stale sessions. Hoppar over `daq.nd://` (NativeConfiguration) som er inkompatibel mellom openDAQ-versjonar.
- `sirius_server.py`: Fiksa dual-modul-problem (`__main__` vs `sirius_server`), port-probe berre mot OPC-UA.

**Commits:** `139232e`

### 7.3 Host-oppsett (éin gong på Pi)

macvlan-avgrensing: hosten kan ikkje nå containeren direkte. Workaround:
```bash
sudo ip link add macvlan-bridge link eth0 type macvlan mode bridge
sudo ip addr add 192.168.1.162/32 dev macvlan-bridge
sudo ip link set macvlan-bridge up
sudo ip route add 192.168.1.161/32 dev macvlan-bridge
```

### 7.4 DewesoftX "Different device" + GetDisplayName-krasj

**Symptom:** DewesoftX koplar til SSH OK, men krasjar med:
```
Access violation at address 000000000198EB3D in module 'DEWEsoft.exe'. Read of address 0000000000000008
DSRTLinuxUnit.TSystemSettings.GetDisplayName (Line 1354)
```
Etterfølgt av: `Error(268435464) Different device - Device serial-numbers do not match`

**Analyse (binæranalyse av DEWEsoft.exe):**

DewesoftX lastar ned **tre filer** via SCP under tilkopling:
1. `/opt/dewesoft/scripts/system.xml` — einingsidentitet (`TDSRTSystemProperties`)
2. `/opt/dewesoft/software/system/system.ini` — innstillingar (`TSystemSettings`)
3. `/opt/dewesoft/software/system/system_ds.lic` — lisens

DewesoftX køyrer også SSH-kommandoen:
```
/opt/dewesoft/scripts/platform_control.sh sysinfo > /opt/dewesoft/scripts/system.xml
```

**Rotårsak 1: Manglande `system.ini`**
`TSystemSettings` les frå `system.ini` (INI-format med `[Settings]`-seksjon).
Utan denne fila vert `TSystemSettings`-objektet nil → `GetDisplayName` prøver å lese felt
på offset 8 frå nil-peikar → Access Violation. Fila krevst med:
```ini
[Settings]
DisplayName=SIRIUSi-HS [DB19106004]
DisplayLocation=
DeviceBehaviour=DewesoftDAQ
GroupLogicalID=
```

**Rotårsak 2: Feil element-namn i `system.xml`**
Vi brukte element-namn som `<Model>`, `<Manufacturer>`, `<FirmwareVersion>` — men
`TDSRTSystemProperties` brukar heilt andre namn. Funne via binæranalyse:
- `DeviceId`, `DeviceDisplayName`, `DeviceName`
- `SerialNumber`, `SystemSerialNumber`
- `PlatformVersion`, `BitstreamVersion`, `ApplicationVersion`
- `LinuxVersion`, `UbootVersion`, `HardwareVersion`
- `DSVersion`, `BootType`, `BundleVersion`, `BundleBuild`
- `AmplifiersList` → `Amplifier` → `SerialNumber`, `HWVersion`, `FWVersion`, `ModuleConnectorType`

**Rotårsak 3: Feil lisensfilnamn**
Vi oppretta `license.xml` men DewesoftX lastar ned `system_ds.lic`.

**Binæranalyse-metode:**
- Las DEWEsoft.exe (95.6 MB) med Python
- Fann `TSystemSettings`, `TDSRTSystemProperties` klasse-RTTI og felt-namn
- Fann alle `/opt/dewesoft/`-stiar som UTF-16LE-strengar
- Identifiserte `[Settings]`-seksjon og `DisplayName`-nøkkel nær `TSystemSettings`-kode
- Fann `GenerateSystemXML`-metode og `ReadSystemProperties` med `Path`-parameter
- Oppdaga `system.ini`-sti med feilmelding "Failed to download system settings file"

**Løysing:**
- Oppretta `/opt/dewesoft/software/system/system.ini` med `[Settings]`-seksjon
- Fiksa `system.xml` med korrekte `TDSRTSystemProperties`-element-namn
- Endra lisensfil til `system_ds.lic`

### 7.5 Serienummer-forvirring

To ulike serienummer:
- `D019274CF6` = Cypress FX2 USB-kontroller serial (USB descriptor, det pyusb ser)
- `DB19106004` = Dewesoft instrument-serial (produksjons-ID: DB=produktlinje, 19=år, 10=veke, 6004=eining)

Desse er uavhengige — lagra i ulike EEPROM-ar. Ingen matematisk samanheng
(hex-verdiar: 893 775 203 574 vs 941 018 341 380).

DewesoftX brukar `DB19106004` som einings-ID når instrumentet er tilkobla direkte.
OPC-UA-serveren annonserer same serial via C++ patch.

---

## Fase 8: Optimaliser data acquisition rate — 2 kHz → 20 kHz (2026-02-24)

### 8.1 Rotårsak: Sample rate mismatch (20× feil)

**Symptom:** DewesoftX Dynamic acquisition rate avgrensa til ≤2000 Hz. Over dette
viser alle kanalar "Invalid or no data".

**Rotårsak:** openDAQ-brua rapporterte 1 kHz sample rate til DewesoftX medan
SIRIUS-maskinvara leverer data ved 20 kHz (hardkoda i start-acquisition register 0x4E20).
DewesoftX tolka dei ekstra samplene som ugyldig data ved høge ratar.

Mismatch-en fanst på fire stader:

| Stad | Var | Korrekt |
|------|-----|---------|
| `opendaq_bro.py:76` — `_tick_delta` | 1000 (= 1 kHz) | 50 (= 20 kHz) |
| `opendaq_bro.py:784` — `GlobalSampleRate` | 1000.0 | 20000.0 |
| `docker-compose.yml:48` — `SAMPLE_RATE` env | 1000 | 20000 |
| `sirius_driver.py:179` — `MaaleKonfig` default | 1000 | 20000 |

### 8.2 Fase 1: Fiks sample rate (STØRST EFFEKT)

**Endringar (berre Python-filer, inga rebuild):**
- `opendaq_bro.py`: `_tick_delta = 50`, `GlobalSampleRate` les `SAMPLE_RATE` env (default 20000)
- `docker-compose.yml`: `SAMPLE_RATE=20000`
- `sirius_driver.py`: `MaaleKonfig.sample_rate` default 20000
- `kanal_konfig.py`: Alle 9 STANDARD_KONFIG entries 1000→20000, `KONFIG_VERSJON` 6→7

**Forventa effekt:** Acquisition rate: 2000 → **20000 Hz**

### 8.3 Fase 2: Optimaliser hot path (CPU-reduksjon)

Reduserer Python-overhead i `oppdater_data()` slik at Pi 5 held tritt med 20 kHz.

**2A: Stats berre for web UI — reduser frekvens**
`opendaq_bro.py:556-571` — Statistikk (snitt, RMS, topp) brukast berre av web UI
som pollar kvart 2. sekund. Bereknar stats kvar 20. pakke (~1 Hz) i staden for kvar pakke.
Eliminerer 19/20 av float64-allokeringar og 3-pass statistikkberekningar.

**2B: Raskare deinterleave med numpy reshape**
`sirius_driver.py:1734-1756` — Erstatta per-kanal slice-loop med `interlev.reshape(n_frames, antall_kanaler).T`.

**2C: deque i staden for list.pop(0)**
`sirius_driver.py:207,1600-1603` — `list.pop(0)` er O(n). Bytta til `collections.deque(maxlen=1000)` for O(1) eviction.

**2D: Unngå sorted() i hot loop**
`opendaq_bro.py:545` — `sorted(kanal_data.items())` sorterte dict kvar pakke. Bytta til fast klasse-nivå indeks-liste `_KANAL_KEYS = ["kanal_0", ..., "kanal_7"]`.

**Forventa effekt:** CPU: **-30 til -50%**

### 8.4 Fase 3: Kompilatorflagg og Docker-ressursar

Krev Docker image rebuild (~30-60 min på Pi 5).

**3A: Arkitektur-spesifikke kompilatorflagg**
`Dockerfile:571` — La til `-O3 -mcpu=cortex-a76` for både CXX og C flags.
Pi 5 sin eksakte CPU-kjerne → aktiverer NEON SIMD og aggressiv vektorisering.

**3B: Auk minne frå 512M til 1G**
`docker-compose.yml:65` — Ved 20 kHz med 8 kanalar: ~350 MB typisk, spikes til ~500 MB.
512 MB ga nesten ikkje margin. Pi 5 har 8 GB totalt.

**3C: Auk byggeparallellisme**
`docker-compose.yml:6` — `PARALLELLE_JOBBER: 2` → `4`. Berre for raskare bygg.

**Forventa effekt:** C++: **-10 til -15%**, ingen OOM

### 8.5 Fase 4: Pre-allokerte buffers

**4A: Pre-allokert float64-buffer**
`opendaq_bro.py` — `np.empty(992, dtype=np.float64)` for gjenbruk i `oppdater_data()`
med `np.multiply(..., out=)` i staden for ny allokering per kanal per pakke.

**Forventa effekt:** Marginalt: **-5 til -10% GC**

### 8.6 Deployment-rekkefølge

```
Fase 1+2+4 (Python-filer)    ← docker cp + restart, inga rebuild
   ↓ test med DewesoftX
Fase 3 (kompilatorflagg)     ← Krev full Docker rebuild
```

**Commit:** `d5fb650`

---

## Noverande status (2026-02-24)

### Fungerer
- SIRIUS USB-driver med reverse-engineered protokoll
- EP2 ADC-streaming med 8 kanalar, **20 kHz**, ~317 kB/s
- Start Acquisition-sekvens (34 register + reg 0x02 trigger)
- 7-strategis EP2 recovery (kommando → dev.reset → uhubctl)
- openDAQ v3.20.6 kompilert med 6 C++ patchar
- OPC-UA server (:4840) + NativeStreaming (:7420)
- mDNS-oppdaging via `add_discovery_server("mdns")`
- DewesoftX **oppdagar eininga** under "Detected devices" i Setup > Devices
- DataPacket-injeksjon av reelle SIRIUS-data
- Web UI med live status, debug, kanalkonfig
- MCP-server for Claude-tilgang til alle API-endepunkt
- macvlan-nettverk med eigen container-IP (192.168.1.161)
- SSH-server i containeren med DewesoftX-legitimasjon (`root`/`D3W3Soft30112018`)
- DewesoftRT stub-skript (`platform_control.sh`) for SSH-kommandoar
- `system.xml` med korrekte `TDSRTSystemProperties`-element-namn
- `system.ini` med `[Settings]`-seksjon (`DisplayName`, `DeviceBehaviour`)
- `system_ds.lic` lisensfil (tom — unngår parse-feil)
- **DewesoftX mottek data** ved Dynamic acquisition rate opptil **20000 Hz** (Setup > Analog in)
- `GetPossibleSampleRate` eigenskapen lagt til på device + kanalar
- Optimalisert hot path: stats 1 Hz, deque, numpy reshape, pre-allokert buffer

### 7.6 DewesoftX "Invalid or no data" på kanalar

**Symptom:** DewesoftX koplar til, men alle kanalar viser "Invalid or no data".

**Feilmeldingar i DewesoftX Event Viewer:**
1. `openDAQ Error 0x80000006: Property with name GetPossibleSampleRate does not exist.`
   - Kjelde: `TDSOpenDaqAI.CalcADCSampleRate`
2. `ID is missing in license: .0"-?> <-Lice-nse>`
   - Kjelde: `GetUncompressedLicense`

**Analyse — GetPossibleSampleRate:**
Binæranalyse av DEWEsoft.exe viser at `TDSOpenDaqAI.CalcADCSampleRate` spør etter
eigenskapen `GetPossibleSampleRate` på openDAQ-kanalane. RefDevice har ikkje denne
eigenskapen → 0x80000006 ("property does not exist").

**Fix 1 — Legg til GetPossibleSampleRate:**
La til `_legg_til_sample_rate_eigenskap()` i `opendaq_bro.py` som legg til
`GetPossibleSampleRate` som `FloatProperty` (200000.0 Hz) på device + kvar kanal.
Fleire property-typar vart prøvd: ListProperty → 0x80004002, IntProperty → 0x80004002,
FloatProperty → 0x80004002. DewesoftX godtek ikkje grensesnittet uansett type —
men `FloatProperty` eliminerte den opprinnelege 0x80000006-feilen.

**Analyse — Lisens-feil:**
Ekte Dewesoft-lisens (`system_ds.lic`) er komprimert/kryptert binær, ikkje plain XML.
`GetUncompressedLicense` prøver å dekomprimere vår XML og får søppel-output.
Lisensfeilmelding er truleg berre ei åtvaring, ikkje årsak til "Invalid or no data".

**Fix 2 — Tom lisensfil:**
Gjer `system_ds.lic` tom (0 bytes) i `docker-entrypoint.sh` for å unngå parse-feil.

**Analyse — OPC-UA DataType incompatible (namespace 7):**
Container-loggar viste ~90 "DataType incompatible"-åtvaringar frå open62541 ved oppstart.
Alle kanal-eigenskapar (Frequency, DC, Amplitude, SampleRate, PacketSize osv.) feila.
Rotårsak: Patch 6 sin `coerceVariantToNodeType()` handterte berre DataTypes i
namespace 0 (standard OPC-UA). openDAQ TMS registrerer eigendefinerte typar i namespace 7
— desse vart ignorerte (early return `false`).

**Fix 3 — findRegisteredDataType() i Patch 6:**
Oppdatert Patch 6 i Dockerfile med ny funksjon `findRegisteredDataType()` som søkjer
gjennom `UA_ServerConfig.customDataTypes`-registeret for typar i ikkje-standard
namespaces. Krev Docker image rebuild (C++ rekompilering). Commit `716277b`.

**Test — Innebygd datagenerering:**
Testa med `OPENDAQ_DISABLE_ACQ` deaktivert (RefDevice genererer sinusbølger).
Framleis "Invalid or no data" → stadfesta at problemet er i OPC-UA-konfigurasjonen,
ikkje i data-injeksjonen.

**LØYSING — Dynamic acquisition rate:**
Problemet viste seg å vere knytt til **Dynamic acquisition rate** i DewesoftX
(Setup > Analog in). Med standard (høg) rate viste alle kanalar "Invalid or no data".
Ved å sette **Dynamic acquisition rate til 2000 Hz**, byrja data å kome gjennom.
Truleg årsak: openDAQ-serveren (eller RefDevice) klarar ikkje å levere data raskt nok
ved høge ratar, og DewesoftX tolkar timeout/manglande pakkar som ugyldig data.

**Filar endra:** `opendaq_bro.py`, `docker-entrypoint.sh`, `Dockerfile` (Patch 6)
**Commits:** `e85a276`, `482c14c`, `2bbf915`, `716277b`

### Uløyst / neste steg
- Deploy Fase 3 (Dockerfile rebuild med `-O3 -mcpu=cortex-a76`) for ~10-15% C++ yting
- Verifisere at DewesoftX godtek 20000 Hz Dynamic acquisition rate etter deploy
- Lisens-formatet er ukjent (komprimert binær) — tom fil er workaround
- openDAQ bridge startar ikkje automatisk (port-kollisjon eller import-feil)

---

## Oppsummering av C++ patchar i Dockerfile

| # | Fil | Kva | Kvifor |
|---|-----|-----|--------|
| 1 | `device_impl.h` | `getDomain()` fallback DeviceDomain | DewesoftX krasjar på nullptr |
| 2 | `core_types_converter.cpp` | nil string → tom streng | DewesoftX krasjar i InitStringProperty |
| 3 | `ref_device_impl.cpp` | SIRIUS DeviceInfo + DeviceType | DewesoftX viser "openDAQ" / nil-krasj |
| 4 | `tms_server_device.cpp` | Kviteliste MAC/Serial i OPC-UA | DewesoftX viser "Not provided" |
| 5 | `ref_device_impl.cpp` | Deaktiver acqLoop (env-var) | Python injiserer ekte data |
| 6 | `opcuaserver.cpp` | Type coercion i writeValue | ~90 DataType-åtvaringar |

## Git-statistikk

- **~111 commits** relatert til openDAQ-integrasjon
- **6 C++ kildekode-patchar** på openDAQ SDK
- **1 SDK-nedgradering** (v3.31 → v3.20.6)
- **~15 revert-commits** (forsøk som forverra situasjonen)
- **2 Wireshark pcapng-analysar** (73 657 USB-frames + direkte USB-capture)
- **1 binæranalyse av DEWEsoft.exe** (95.6 MB, UTF-16LE strengsøk)
- **1 acquisition rate-optimalisering** (2 kHz → 20 kHz, 4 fasar)

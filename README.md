# OpenDacko - openDAQ SIRIUS Bridge

Docker-container som gjer ein Dewesoft SIRIUSi-HS tilgjengeleg over nettverket
via openDAQ-protokollen. DewesoftX (Windows) kan koble til og bruke SIRIUS
som om den var lokal, sjolv om den sit paa ein Raspberry Pi eller Linux-server.

## Arkitektur

```
SIRIUS ──USB──> SiriusDriver (reverse-engineered USB-protokoll)
                    |
                    +──> EP2 ADC-streaming (8 kanalar, int16, opp til 20 kHz)
                    +──> Autonom maaling (CSV/NPZ kvar 60s)
                    +──> Web UI (:8080)
                    |
              OpenDAQ Nettverksbro
                    |
                    +──> OPC-UA server (:4840)
                    +──> Native Streaming (:7420)
                    +──> WebSocket/LT Streaming (:7414)
                    +──> mDNS-annonsering
                    |
              DewesoftX (Windows)
                    +──> Oppdagar eininga automatisk
                    +──> Koplar til via OPC-UA + Native Streaming
```

## Krav

- Linux-maskin (Raspberry Pi 5 eller Ubuntu Server)
- Docker og Docker Compose
- Dewesoft SIRIUSi-HS tilkopla via USB

## Hurtigstart

### 1. Klon repoet

```bash
git clone https://gitea.merodningen.no/sverre/OpenDackoConteiner.git
cd OpenDackoConteiner
```

### 2. Kjoer host-oppsett (ein gong)

```bash
sudo bash setup_host.sh
```

Installerer kernel-moduler (usbip, usbmon), udev-reglar for Dewesoft USB
(VID `0x1CED`), og legg brukar til `plugdev`-gruppa.

### 3. Bygg og start

```bash
docker compose up -d --build
```

Fyrste bygg tek lang tid fordi openDAQ v3.30 kompilerast fraa kjelda
med OPC-UA, streaming og Python-bindings. Paa Raspberry Pi med lite RAM:

```bash
docker compose build --build-arg PARALLELLE_JOBBER=1
docker compose up -d
```

### 4. Opne Web UI

Gaa til `http://<ip>:8080` i nettlesaren.

## Portar

| Port | Teneste |
|------|---------|
| 8080 | Web UI (React + Flask) |
| 4840 | OPC-UA (openDAQ) |
| 7420 | Native Streaming (openDAQ) |
| 7414 | WebSocket/LT Streaming (openDAQ) |

## Konfigurasjon

Miljovariablar i `docker-compose.yml`:

| Variabel | Standard | Beskriving |
|----------|----------|-----------|
| `NATIVE_SIRIUS` | `true` | Bruk reverse-engineered USB-driver |
| `TILKOBLING` | *(tom)* | Overstyr tilkobling (t.d. `daq.opcua://ip`) |
| `BRUK_SIMULATOR` | `false` | Kjoer utan hardware (referanse-eining) |
| `WEB_PORT` | `8080` | Port for web-grensesnittet |
| `MAALE_INTERVALL` | `60` | Sekund mellom autonome maalingar |
| `MAALE_VARIGHET` | `5` | Varighet per maaling (sekund) |
| `SAMPLE_RATE` | `1000` | Samplingsrate (Hz) |
| `OPENDAQ_IP` | *(auto)* | OPC-UA annonsert IP (auto-detect om tom) |

## Filar

| Fil | Beskriving |
|-----|-----------|
| `sirius_driver.py` | SIRIUS USB-driver (tilkobling, init, streaming) |
| `sirius_protokoll_impl.py` | Lavnivaa USB-protokoll (EP1 kommandoar, AD/B1) |
| `sirius_server.py` | Hovudserver: driver + openDAQ bro + web UI |
| `opendaq_bro.py` | openDAQ nettverksbro (OPC-UA, streaming, 8 kanalar) |
| `kanal_konfig.py` | Kanalkonfigurasjon og JSON-persistens |
| `web_ui.py` | Flask web UI med live status og debug |
| `opendaq_server.py` | Alternativ server via openDAQ SDK direkte |
| `setup_host.sh` | Host-oppsett (kernel-moduler, udev, ein gong) |
| `docker-entrypoint.sh` | Container-oppstart og konfigurasjon |
| `Dockerfile` | Multi-stage: bygg openDAQ + React frontend + runtime |

## Flytte til nytt system

### Alternativ A: Bygg paa det nye systemet

```bash
git clone https://gitea.merodningen.no/sverre/OpenDackoConteiner.git
cd OpenDackoConteiner
sudo bash setup_host.sh
docker compose up -d --build
```

### Alternativ B: Eksporter image og last inn

Paa maskinen der det allereie er bygd:

```bash
docker save opendaq-sirius:latest | gzip > opendaq-sirius.tar.gz
scp opendaq-sirius.tar.gz brukar@ny-maskin:/home/brukar/
```

Paa det nye systemet:

```bash
docker load < opendaq-sirius.tar.gz
git clone https://gitea.merodningen.no/sverre/OpenDackoConteiner.git
cd OpenDackoConteiner
sudo bash setup_host.sh
docker compose up -d
```

## Hardware

| Komponent | Detaljar |
|-----------|----------|
| Instrument | Dewesoft SIRIUSi-HS, 8xAI, S/N D019274CF6, FW 2.11.1.1 |
| USB | VID=0x1CED, PID=0x1002 |
| Protokoll | Reverse-engineered fraa Wireshark USB-capture |

## Lisens

Internt prosjekt.

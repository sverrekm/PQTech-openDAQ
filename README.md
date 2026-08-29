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

### 3. Konfigurer containeren (valfritt, anbefalt)

```bash
sudo bash pqtech-config.sh
```

Terminal-meny (liknar `raspi-config`) for å setje opp IP-adresse (fast eller
auto-finn ledig), nettverksgrensesnitt, driftsmodus (node/hub), kor måledata
vert lagra (`DATA_DIR`), ingest-token og web-port. Skriv til `.env` og
`konfig/modus.json`, og kan byggje/starte containeren på nytt frå menyen.

### 4. Bygg og start

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

## Eksternt lese-API (API-nøklar)

Ein klient utanfor hub-nettet — til dømes ein desktop-widget — kan lese
måledata over HTTPS med ein API-nøkkel. Ingen VPN, ingen sesjons-cookie.

**Opprett nøkkel:** Innstillingar → «Deling og integrasjonar» → API-nøklar.
Nøkkelen vert vist éin einaste gong; berre ein SHA-256-hash vert lagra, så han
kan ikkje hentast fram att. Kvar nøkkel kan deaktiverast eller trekkjast
tilbake for seg, ha utløpsdato, og avgrensast til utvalde kanalar
(`Sundet/Spenning L1`, eller `Sundet/*` for alt frå ein node).

Alle nøklar er **read-only** — berre GET slepp gjennom, uansett endepunkt.

### Endepunkt

| Endepunkt | Gjer |
|---|---|
| `GET /api/v1/info` | Kva hub dette er, og kva nøkkelen har tilgang til |
| `GET /api/v1/kanalar` | Siste verdi for kvar kanal |
| `GET /api/v1/straum` | Server-Sent Events, ny pakke kvart `?intervall=<sek>` (0,5–60) |

Nøkkelen sendast som `X-API-Key: <nøkkel>` eller `Authorization: Bearer <nøkkel>`.
For SSE frå ein nettlesar går han som `?api_key=<nøkkel>`, sidan `EventSource`
ikkje kan setje headerar — merk at query-parametrar kan hamne i proxy-loggar.

```bash
curl -H "X-API-Key: pqt_..." https://opendac.pqtech.no/api/v1/kanalar
```

```json
{"tid": "2026-08-29T20:15:03",
 "kanalar": [{"node": "Sundet", "namn": "Spenning L1", "verdi": 232.4,
              "eining": "V", "type": "opendaq", "tilkobla": true}]}
```

### Eksempelklient

`eksempel/widget_klient.py` er ei enkelt fil (krev berre `requests`) som viser
både polling og SSE med reconnect:

```bash
pip install requests
export PQTECH_API_NOKKEL=pqt_...
python eksempel/widget_klient.py --url https://opendac.pqtech.no --straum
```

Klassen `HubKlient` i den fila kan klippast rett ut og leggjast bak kva GUI
som helst (tkinter, Qt, tray-ikon).

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

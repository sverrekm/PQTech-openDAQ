# PQTech openDAQ — Produktoversikt

*Kundevendt oversikt: hva produktet er, hvordan det virker, hva slags data det
håndterer og hvor, og hvordan det er sikret. For praktisk bruk, se
[Brukermanualen](BRUKERMANUAL.md). Engelsk versjon: [../en/PRODUCT-OVERVIEW.md](../en/PRODUCT-OVERVIEW.md).*

---

## 1. Hva er PQTech openDAQ?

PQTech openDAQ er en **måle-gateway for kraft- og energidata**. Den kobler
måleinstrumenter (Dewesoft SIRIUS, Elspec PQube 3 / BlackBox, Modbus-målere,
MQTT-kilder, solcelleomformere med SunSpec) til analyseverktøy og databaser, og
gjør målingene tilgjengelige **lokalt på anlegget** og — hvis ønskelig — samlet
på ett sted for en hel portefølje av anlegg.

Kort sagt: en liten, robust boks (Raspberry Pi) som står ute på anlegget,
samler inn måledata kontinuerlig, tar vare på dem selv om nettet faller, og
leverer dem videre til Dewesoft DewesoftX, Grafana/InfluxDB, rapporter og
kundens egne systemer.

**Problemet det løser:**
- Instrumenter fra ulike leverandører snakker ulike protokoller (openDAQ/OPC-UA,
  Modbus, MQTT, FTP/CSV). PQTech openDAQ samler dem til én strøm.
- Anlegg står ofte på 5G/CGNAT uten fast IP — vanskelig å nå utenfra. Produktet
  bruker et sikkert mesh-nett (Tailscale) så data kan hentes uten åpne porter.
- Måledata må ikke gå tapt ved nettbrudd — noden bufrer lokalt og etterfyller.
- Én kunde kan ha mange anlegg — data kan skilles per **kunde og anlegg** for
  videre behandling.

---

## 2. Slik virker det — arkitektur

Produktet er **edge-først**: innsamling og lagring skjer så nær instrumentet som
mulig, og data flyttes bare når det trengs.

```
  Instrument(er)              Node (Raspberry Pi)            Hub (valgfri)
  ─────────────              ────────────────────           ─────────────
  SIRIUS (USB)      ─┐
  PQube 3 (Modbus)  ─┤  →   Innsamling + lokal buffer   →   Aggregerer mange
  MQTT-kilder       ─┤       + openDAQ-server                noder, lagrer,
  Modbus/SunSpec    ─┘       (DewesoftX kobler hit)          deler, rapporterer
                                     │
                                     └── Push over HTTPS (token) → Hub
```

**To roller (samme programvare, valgt i oppsettet):**

- **Node** — står ute på anlegget. Samler inn fra instrumentene, bygger kanaler,
  bufrer lokalt, og eksponerer dataene for DewesoftX (OPC-UA + native streaming).
  Kan pushe utvalgte kanaler videre til en hub.
- **Hub** — samler data fra mange noder på ett sted. Lagrer tidsserier, arkiverer
  rå-filer til NAS (delt per kunde/anlegg), deler til Grafana, og gir ett
  dashbord over hele flåten. En hub kan ligge på kundens kontor eller i sky.

En node fungerer helt fint alene (uten hub). Hub-en er for den som vil samle
flere anlegg.

---

## 3. Datakilder / instrumenter

| Kilde | Tilkobling | Merknad |
|---|---|---|
| Dewesoft SIRIUS | USB | Direkte driver; høyhastighets ADC. Deles til DewesoftX over nett. |
| Elspec PQube 3 | Modbus TCP | Auto-oppdaget på nettet; strømmer over openDAQ. |
| Elspec BlackBox G4500 | FTP (CSV) / Modbus RS-485 | CSV-rapporter hentes via FTP; PQZIP-bølgeform arkiveres. Modbus krever RS-485→TCP-gateway. |
| MQTT-kilder | MQTT | Broker avlyttes; strøm-relevante topics kan gjøres til kanaler. |
| Modbus-målere (generisk) | Modbus TCP | Konfigurerbare registre. |
| Solcelleomformere | SunSpec (Modbus) | Auto-oppdaging av SunSpec-blokker. |

---

## 4. Dataflyt — fra instrument til analyse

1. **Innsamling.** Noden leser instrumentet (USB/Modbus/MQTT/FTP) og bygger
   navngitte kanaler (spenning, strøm, effekt, frekvens, …).
2. **Lokal buffer.** Verdiene skrives til en lokal ringbuffer (SQLite) på noden.
   Faller nettet, fylles hullet etter når forbindelsen er tilbake (gap-fri
   store-and-forward).
3. **DewesoftX.** Noden kjører en openDAQ-server (OPC-UA + native streaming), så
   DewesoftX kan koble til og se instrumentet som om det var lokalt.
4. **Push til hub** (valgfritt). Noden sender utvalgte kanaler til hub-en over
   HTTPS med et delt token.
5. **Lagring på hub.** Hub-en lagrer tidsseriene i en database og kan arkivere
   rå CSV-filer til NAS.
6. **Deling.** Kanalverdier kan deles til Grafana/InfluxDB, leses via et
   read-API, og brukes i rapporter.

---

## 5. Hvilke data samles inn, hvor lagres de?

PQTech openDAQ håndterer **tekniske måledata** (elektriske størrelser og
tidsstempel) — ikke personopplysninger.

| Datatype | Lagringssted | Retensjon |
|---|---|---|
| Kanalverdier (tidsserie) | Node: lokal buffer (`/data/maalinger`). Hub: SQLite-database, merket per node. | Ringbuffer på node; konfigurerbar på hub. |
| Rå-fil-arkiv (CSV) | Hub → **NAS**, delt `{NAS}/{kunde}/{anlegg}/{dato}.csv` | Så lenge NAS-en har plass (kundens kontroll). |
| PQZIP-bølgeform (Elspec) | Hub/node → NAS (`/data/nas/pqzip`) | Konfigurerbar (f.eks. 14 dager). Åpnes i Elspec PQSCADA. |
| Konfigurasjon | Lokalt på hver enhet (`/data/konfig`) | Til den endres. |

**Dataplassering (viktig for kunder):** som standard ligger dataene på
**anlegget selv** (noden) og eventuelt på en **hub og NAS kunden selv eier**.
Sky-hub er valgfritt. Kunden bestemmer hvor dataene bor. Data kan skilles per
kunde og anlegg for videre behandling i tredjepartsverktøy.

---

## 6. Sikkerhet & personvern

- **Tilgangsstyring.** Web-grensesnittet krever innlogging. Noder settes opp
  med **SSH-nøkkel** (ikke delt passord) for administrasjon.
- **Node ↔ hub.** All push går over **HTTPS** og krever et delt token; feil
  token avvises. Hub-en signerer proxy-tilgang til noder med samme token
  (enkel pålogging), og tokenet sendes aldri til nettleseren.
- **Fjerntilgang uten åpne porter.** Enheter knyttes sammen i et **Tailscale**
  mesh-nett (WireGuard-kryptert). Ingen innkommende porter må åpnes i kundens
  brannmur, og løsningen virker bak 5G/CGNAT.
- **Interne proxyer** (instrument- og FTP-proxy) godtar **kun private
  IP-adresser** — de kan ikke brukes til å nå ut på det åpne internett.
- **Data på anlegget.** Edge-først-designet gjør at måledata kan bli værende
  lokalt; ingenting forlater anlegget uten at det er satt opp.
- **Personvern.** Produktet behandler elektriske måledata og tidsstempel, ikke
  personopplysninger. (Kunder med spesifikke krav kan få et eget notat om
  databehandling.)

---

## 7. Nettverk & tilkobling

- **Måle-LAN:** noden får sin egen IP på anleggets nett (kablet), så DewesoftX
  når den direkte. IP velges automatisk (ledig adresse) ved oppstart, eller
  settes fast.
- **5G / CGNAT:** støttes via Tailscale — noden er nåbar uten fast offentlig IP.
- **Førstegangsoppsett uten PC:** ved første oppstart reiser noden et eget
  **WiFi-oppsettnett med captive portal** — operatøren kobler til med telefon
  og setter opp noden i en veiviser.
- **Instrument-isolasjon:** instrument med egen ruter (f.eks. Elspec BlackBox)
  kan nås uten å ta over nodens internett-vei.

---

## 8. Kompatibilitet & standarder

- **Dewesoft DewesoftX** — OPC-UA + native streaming (instrumentet vises som en
  Dewesoft-enhet).
- **openDAQ** — åpen SDK for datainnsamling (OPC-UA, streaming, Python).
- **Modbus TCP/RTU** — standardregistre; konfigurerbart.
- **MQTT** — broker-avlytting og topic→kanal.
- **SunSpec** — solcelleomformere.
- **InfluxDB / Grafana** — deling og visualisering.
- **Elspec** — PQube 3 (Modbus), BlackBox G4500 (FTP/CSV, PQZIP via PQSCADA).

---

## 9. Maskinvare

- **Raspberry Pi 5** eller **Compute Module 5 (CM5)** med WiFi.
- **M.2 NVMe** for OS og lokal datalagring.
- Kablet nett (eth) mot måle-LAN; WiFi til oppsett/uplink.
- Kan masseproduseres: ett golden-image klones til mange noder, hver
  selv-konfigureres via captive portal ved første oppstart.

---

## 10. Drift & vedlikehold

- **Oppdatering** styres sentralt: hele flåten kan oppdateres fra ett sted.
- **Overvåking:** hvert dashbord viser status (server, instrument, buffer,
  push, hendelser, logg).
- **Utrulling:** golden-image + captive portal gir rask, terminalfri utrulling
  av nye noder.

---

## 11. Kjente begrensninger (ærlig)

- **Elspec PQZIP** er et lukket, komprimert bølgeform-format og kan **ikke**
  gjøres om til live kanaler uten Elspec PQSCADA. PQTech openDAQ arkiverer
  filene til NAS for senere analyse.
- **Elspec G4500 har ikke Modbus-TCP** (kun RS-485). Live Modbus-måling krever
  en RS-485→Modbus-TCP-gateway. Alternativt hentes CSV-rapporter via FTP.
- **openDAQ over WAN** har en praktisk grense på antall kanaler før latens/
  båndbredde blir merkbar; svært mange kanaler bør bufres/pushes i stedet for
  å strømmes rått over WAN.

---

*Dokumentversjon følger produktet i git. Sist oppdatert: se git-historikk.*

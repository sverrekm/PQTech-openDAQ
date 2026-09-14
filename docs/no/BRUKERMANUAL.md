# PQTech openDAQ — Brukermanual

*Praktisk manual for operatører: sette opp en node, bruke grensesnittet, legge
til instrumenter, drifte og feilsøke. For hva produktet er, se
[Produktoversikt](PRODUKTOVERSIKT.md). English: [../en/USER-MANUAL.md](../en/USER-MANUAL.md).*

---

## Innhold
1. [Komme i gang (førstegangsoppsett)](#1-komme-i-gang-førstegangsoppsett)
2. [Innlogging og grensesnitt](#2-innlogging-og-grensesnitt)
3. [Dashbordet](#3-dashbordet)
4. [Innstillinger](#4-innstillinger)
5. [Legge til instrumenter](#5-legge-til-instrumenter)
6. [Node ↔ hub (push)](#6-node--hub-push)
7. [Hub-modus](#7-hub-modus)
8. [Lagring og data](#8-lagring-og-data)
9. [Oppdatering og admin](#9-oppdatering-og-admin)
10. [Feilsøking](#10-feilsøking)
11. [Ordliste](#11-ordliste)

---

## 1. Komme i gang (førstegangsoppsett)

En fabrikkny node setter du opp uten PC, via et innebygd WiFi-oppsettnett:

1. Sett M.2-disken i noden, koble **eth0** til måle-nettet (kabel), slå på strøm.
2. På telefon/PC: koble til det åpne WiFi-nettet **`PQTech-Setup-XXXX`**
   (XXXX = siste 4 av enhetens serienummer). Captive-portalen åpnes automatisk
   (ellers gå til `http://10.42.0.1/`).
3. Fyll ut veiviseren:
   - **Node-navn** — identifiserer boksen (vises i DewesoftX, hub, rapporter).
   - **Rolle** — Node (måler) eller Hub (aggregerer).
   - **Uplink** — Ethernet eller WiFi (+ nett/passord).
   - **Container-IP** — **Auto** (anbefalt; velger ledig adresse) eller Static.
   - Valgfritt: **Hub-URL + token** for å pushe data til en hub.
4. Trykk **Finish setup**. Noden kobler opp, starter, og markerer seg ferdig.
5. Finn noden på nettet (ruterens DHCP-liste, mDNS, eller hub-dashbordet) og
   åpne `http://<node-ip>:8080`.

> Masseproduksjon: se [PROVISIONING.md](../PROVISIONING.md) for golden-image +
> kloning.

---

## 2. Innlogging og grensesnitt

- Åpne `http://<node-ip>:8080` og logg inn.
- **Toppfelt:** produktnavn + node-info (Node / Adresse / Modus) og
  «Server active»-indikator. «Log out» til høyre.
- **Sidemeny (venstre):** Dashboard, Hub, **Channels** (med live-verdier),
  MQTT, Hub channels/nodes, First-time setup, Correlation explorer, Settings,
  Admin. Kanal-rader viser live-verdi til høyre og en prikk for status.

---

## 3. Dashbordet

**Node-modus:**
- **Instrument-målerutenett** (øverst): hver aktive kanal og MQTT-topic som en
  stor avlesning med kilde-tagg (Sirius/USB/Sim/MQTT) og sparkline. Kanaler fra
  en tilkoblet PQube/modbus-sub-node vises også her (farget per node).
- **Kort under:** kanaltabell (live), openDAQ-bru, buffer-status, hendelser,
  MQTT-logg, server-status, logg.

**Hub-modus:**
- **Flåte-målerutenett** over de synlige kanalene nodene pusher (farget per
  node), node-oversikt, kanal-filter (velg hvilke kanaler/noder du ser),
  kanaltabell, logg.

---

## 4. Innstillinger

Settings er delt i fem kategorier (nummerert index til venstre):

**01 — Device & measurement**
- **Name this box** — node-navn (tagger alle målinger).
- **Device** — modell, ADC-kanaltall, og *USB-kort på dashbord* (Auto/Alltid/
  Aldri — skjul USB-kort på modbus/MQTT-noder).
- **Channels** — rediger kanaler (navn, enhet, aktiv, skalering).
- **Instrument discovery** — legg til oppdagede instrumenter (PQube/G4500).
- **Node services** — SMTP-mottak, NTP-tidsserver, FTP-proxy.
- **PQZIP archive** — arkiver Elspec-bølgeform til NAS.
- **Buffer** — lokal målebuffer + hub-synk.

**02 — Sharing & integrations**
- **Hub connection** — sett hvor noden pusher (parent-URL + flåte-token) og se
  live push-status. *(Se kap. 6.)*
- **MQTT** — broker-innstillinger.
- **MQTT discovery** — avlytt broker, gjør topics til kanaler.
- **InfluxDB/Grafana** — del kanalverdier.
- **EMC** — EMC-beregninger.
- **API keys** — les-API-nøkler (`pqt_…`).

**03 — Storage**
- **NAS** + **rå-fil-arkiv** (CSV) + **hub-database**. *(Se kap. 8.)*

**04 — Network & nodes**
- **WiFi**, **instrument-nett**, **nettverksskann**, **instrument-FTP**,
  **Tailscale**, **hub/node-konfig** (legg til fjern-noder).

**05 — Diagnostics (advanced)**
- Konsoll, probe-analyse, endepunkt-berging. Håndteres med omhu.

---

## 5. Legge til instrumenter

**Dewesoft SIRIUS (USB):** koble til via USB → vises under SIRIUS-status →
strømmes til DewesoftX. Kanaler settes opp i *Channels*.

**Elspec PQube 3 (Modbus TCP):** Settings → *Network & nodes* → **Nettverksskann**
→ finn PQuben → **Add as PQube 3**. Den blir en modbus-node som strømmer over
openDAQ. På en node havner den i **Hub nodes**-lista; kanalene mates inn i
openDAQ-brua og (hvis push er satt opp) videre til hubben.

**Elspec BlackBox G4500:**
- **CSV-rapporter (DL/MR-logg):** Settings → *Network & nodes* → **Instrument-FTP**.
  Sett rot til rapportmappa (typisk `/CF_UPMB/Reports`). Rapport-generering må
  være **påslått i G4500-ens eget grensesnitt** for at nye CSV-er skal lages.
- **PQZIP-bølgeform:** arkiveres til NAS via *PQZIP archive* — åpnes i Elspec
  PQSCADA (kan ikke bli live kanaler).
- **Modbus:** G4500 har kun RS-485 → krever RS-485→Modbus-TCP-gateway.

**MQTT-kilde:** Settings → *Sharing* → **MQTT discovery** → avlytt `#` → velg et
strøm-relevant topic → **Add as channel**.

**SunSpec-omformer:** **Nettverksskann** kjenner igjen SunSpec → legg til.

**Generisk Modbus-måler:** *Network & nodes* → **hub/node-konfig** → legg til
node (type modbus_tcp) med registre.

---

## 6. Node ↔ hub (push)

For at en node skal sende data til en hub:

1. Settings → *Sharing & integrations* → **Hub connection**.
2. **Hub URL** = hubbens adresse (f.eks. `https://opendac.pqtech.no`).
3. **Fleet token** = det delte ingest-tokenet hubben krever (samme på alle
   noder som pusher til samme hub).
4. **Save**. Følg med på statusen: **Sent OK** skal stige, **Failed**/«Push
   error» skal være borte.

Tokenet må matche nøyaktig — feil token gir `401 Ugyldig token`, og hubben ser
da ingen kanaler fra noden.

---

## 7. Hub-modus

- Sett rolle til **Hub** (veiviser eller Settings → Device).
- **Legg til noder:** Settings → *Network & nodes* → hub/node-konfig → adresse,
  navn, **Customer** (for NAS-mappestruktur), evt. type/protokoll.
- **Åpne en nodes UI via hubben:** klikk noden i sidemenyen (Hub nodes) →
  node-proxy åpner nodens grensesnitt gjennom hubben (krever hub-innlogging).
- **Flåte-dashbord + kanal-filter:** velg hvilke kanaler/noder du vil se.

---

## 8. Lagring og data

- **Lokal buffer (node):** ringbuffer med RMS-verdier; etterfyller hull ved
  nettbrudd.
- **Hub-database:** tidsserier fra alle noder i én database, merket per node
  (driver dashbord, Correlation explorer, Grafana-deling).
- **Rå-fil-arkiv (NAS):** CSV per **kunde/anlegg**:
  `{NAS}/{kunde}/{node}/{node}_{dato}.csv`. Slå på i *Storage*; NAS må være
  montert. Sett **Customer** på hver node for kunde-mappa (ellers `{node}/`).
- **PQZIP-arkiv (NAS):** Elspec-bølgeform for senere PQSCADA-analyse.

---

## 9. Oppdatering og admin

- **Admin-siden:** endre passord, velge språk (engelsk/bokmål), og kjøre
  programvare-oppdatering.
- **Flåte-oppdatering:** hele flåten kan oppdateres sentralt fra hubben.
- Oppdatering laster ned ny versjon og starter tjenesten på nytt automatisk.

---

## 10. Feilsøking

| Symptom | Sannsynlig årsak / tiltak |
|---|---|
| Setup-AP `PQTech-Setup-…` dukker ikke opp | WiFi-land ikke satt / rfkill. Sett WiFi-land, `nmcli radio wifi on`. |
| Finner ikke noden på nettet | Sjekk ruterens DHCP-liste; noden velger ledig IP automatisk. Static: sjekk `CONTAINER_IP`. |
| Hub ser ikke nodens kanaler | Push ikke satt opp / feil token → *Hub connection*: sett riktig Hub URL + Fleet token (`401` = feil token). |
| Node-proxy kaster deg til login | Hub-sesjon utløpt — logg inn på hubben på nytt. |
| Ingenting lagres på NAS | Slå på rå-fil-arkivet i *Storage* på **hubben**, sett katalog, og sørg for at NAS er montert. |
| PQube-kanaler ikke på hub | Noden må pushe (Hub connection-token) — kanalene går via push, ikke direkte. |
| FTP finner bare gamle CSV-er | CSV-rapportene ligger i `Reports/`; PQZIPDATA_ er bølgeform (PQZIP). Slå på rapport-generering i G4500. |
| Instrument mister USB (SIRIUS) | Ofte kabel; restart containeren etter hotplug. |
| Container restarter i loop | Sjekk `docker logs pqtech-opendaq` på verten. |

---

## 11. Ordliste

- **Node** — boks ute på anlegget som måler.
- **Hub** — samler data fra flere noder.
- **Kanal** — én målt størrelse (spenning, strøm, effekt, …).
- **Push / ingest** — noden sender kanalverdier til hubben (HTTPS + token).
- **Flåte-token / ingest-token** — delt hemmelighet noder bruker mot hubben.
- **macvlan** — gir containeren egen IP på LAN-et (så DewesoftX når den).
- **Tailscale** — kryptert mesh-nett for fjerntilgang uten åpne porter.
- **PQZIP** — Elspec sitt komprimerte bølgeform-format (åpnes i PQSCADA).
- **Captive portal** — oppsett-nettside noden viser ved førstegangsoppsett.

---

*Dokumentversjon følger produktet i git.*

# PQTech openDAQ — Databehandling og sikkerhet

*Teknisk notat om hvilke data løsningen behandler, hvor de lagres, hvordan de
overføres og sikres, og hvem som har tilgang. Beregnet på kunder og deres
sikkerhets-/personvernvurdering. English: [../en/DATA-HANDLING.md](../en/DATA-HANDLING.md).*

> **Om dette dokumentet.** Dette er en *teknisk* beskrivelse av databehandlingen
> i produktet. Det er **ikke** en juridisk databehandleravtale (DPA) og erstatter
> ikke en slik. Bruk det som faktagrunnlag for egen etterlevelse (f.eks.
> risikovurdering / DPIA). Detaljer kan variere med hvordan løsningen er satt opp
> hos den enkelte kunde.

---

## 1. Hvilke data behandles

Produktet er en **måle-gateway** og behandler i hovedsak **tekniske måledata**:

| Kategori | Eksempel | Personopplysning? |
|---|---|---|
| Måleverdier (tidsserie) | spenning, strøm, effekt, frekvens + tidsstempel | Nei |
| Bølgeform / hendelser | Elspec PQZIP, hendelseslogg | Nei |
| Konfigurasjon/metadata | node-navn, **kunde-/anleggsnavn** (som etikett), instrument-oppsett | Kan være identifiserende |
| Nettverksdata | IP-adresser, Tailscale-node-navn | Kan være identifiserende |
| Kontodata | administrator-**brukernavn** (innlogging) | Ja (begrenset) |

Måledataene i seg selv er ikke personopplysninger. Kunde-/anleggsnavn, IP-adresser
og administrator-brukernavn *kan* være identifiserende i begrenset grad, og bør
inngå i kundens egen vurdering.

---

## 2. Hvor lagres dataene (dataplassering)

Løsningen er **edge-først** — data lagres nærmest mulig instrumentet:

| Sted | Hva | Eier |
|---|---|---|
| **Node** (på anlegget) | lokal buffer (måleverdier), konfigurasjon | Kunden (utstyr på anlegget) |
| **Hub** (valgfri) | tidsserie-database (per node), delings-oppsett | Kunden (kontor) eller sky |
| **NAS** (valgfri) | rå-fil-arkiv (CSV per kunde/anlegg), PQZIP-arkiv | Kunden |

**Kunden bestemmer hvor dataene bor.** En node fungerer alene med data kun på
anlegget. Hub og NAS er kundens eget utstyr med mindre annet er avtalt. En
sky-hub er valgfri.

---

## 3. Overføring og kryptering

- **Node → hub:** over **HTTPS/TLS**, autentisert med et delt token. Uten
  gyldig token avvises data.
- **Fjerntilgang:** via **Tailscale** (WireGuard-kryptert mesh). Trafikk er
  ende-til-ende-kryptert; ingen innkommende porter åpnes i kundens brannmur.
- **Sky-hub (hvis brukt):** nås over HTTPS/TLS (bak reverse-proxy/CDN).
- **Interne proxyer** (instrument/FTP) godtar kun **private IP-adresser** og kan
  ikke rute ut på det åpne internett.
- **I ro (at rest):** måledata lagres som SQLite/CSV/PQZIP på enhetens disk/NAS.
  Disk-/volumkryptering er **kundens valg** (f.eks. kryptert NAS eller diskkryptering
  på verten) og er ikke slått på som standard.

---

## 4. Oppbevaring (retensjon) og sletting

| Data | Oppbevaring | Sletting |
|---|---|---|
| Lokal buffer (node) | ringbuffer (eldste overskrives) | automatisk rullering |
| Hub-database | konfigurerbar | slettes/tømmes på hubben |
| Rå-fil-arkiv (NAS) | så lenge NAS har plass | kunden sletter filer |
| PQZIP-arkiv (NAS) | konfigurerbar retensjon (f.eks. 14 dager) | automatisk pruning + manuelt |
| Konfigurasjon | til den endres | `golden-reset` nullstiller all node-tilstand |

Ved avvikling/omdisponering av en node fjerner **`pqtech-golden-reset`** all
node-spesifikk tilstand (konfig, tokens, WiFi-profiler, tailscale-state, måledata,
SSH-nøkler).

---

## 5. Tilgangsstyring

- **Web-grensesnitt:** krever innlogging (brukernavn + passord).
- **Node-administrasjon (SSH):** **nøkkelbasert**, passord-innlogging avslått —
  ingen delt konto-passord i flåten.
- **Node → hub:** delt **ingest-token**; hubben signerer proxy-tilgang med samme
  token (enkel pålogging). Token sendes aldri til nettleseren.
- **Les-API:** egne nøkler (`pqt_…`) for tredjeparts lesing.
- Hver node kan ha unik identitet; golden-image nullstilles slik at kloner ikke
  deler hemmeligheter.

---

## 6. Sikkerhetstiltak (oppsummert)

- Kryptering i transitt (HTTPS/TLS, WireGuard).
- Ingen åpne innkommende porter (Tailscale mesh; fungerer bak 5G/CGNAT).
- Token-basert node↔hub-autentisering; feil token avvises.
- Private-IP-only interne proxyer.
- Nøkkelbasert SSH, ingen delt passord.
- Sentralt styrt oppdatering av programvare.
- `golden-reset` for sikker gjenbruk/avvikling.

---

## 7. Underleverandører / tredjeparter

Avhengig av oppsett kan følgende tredjeparter være involvert:

| Tredjepart | Rolle | Datainnhold |
|---|---|---|
| **Tailscale** | koordinerer mesh-nett (WireGuard) | nøkkelutveksling/koordinering; måledata går kryptert node↔node (ev. via kryptert relay), ikke lagret hos Tailscale |
| **Reverse-proxy/CDN** (sky-hub) | TLS-terminering foran sky-hub | trafikk til sky-hub (hvis brukt) |
| **Git-tjener** (oppdatering) | leverer programvare-oppdateringer | ingen måledata |
| **InfluxDB / Grafana** (hvis konfigurert) | visualisering/deling | kanalverdier kunden velger å dele |

Bruk av sky-hub, ekstern InfluxDB/Grafana o.l. er **valgfritt** og styres av
kundens oppsett.

---

## 8. Personopplysninger — vurdering

- Kjernedataene (elektriske måleverdier + tidsstempel) er **ikke**
  personopplysninger.
- Metadata som **kunde-/anleggsnavn, IP-adresser og administrator-brukernavn**
  kan være identifiserende i begrenset grad.
- Kunden bør gjøre sin egen vurdering (og evt. DPIA) ut fra hvordan løsningen
  brukes, hvilke etiketter som settes, og hvor hub/NAS er plassert.

---

## 9. Kundens ansvar

- Hvor **hub og NAS** plasseres, og fysisk/logisk sikring av disse.
- Hvem som gis **innlogging og SSH-nøkkel-tilgang**.
- **Sikkerhetskopi** av hub-database/NAS-arkiv (kundens rutiner).
- Nettverkssikkerhet på anlegget (brannmur, segmentering).
- Eventuell **kryptering i ro** (kryptert NAS/disk) der det kreves.

---

## 10. Sikkerhetskopi og hendelseshåndtering

- **Datatap ved nettbrudd** motvirkes av lokal buffer + gap-fri etterfylling.
- **Sikkerhetskopi** av hub-database og NAS-arkiv er kundens ansvar (NAS-rutiner).
- Ved mistanke om kompromittering: roter tokens/SSH-nøkler, og bruk `golden-reset`
  + ny provisjonering på berørte noder.

---

*Kontakt PQTech for en formell databehandleravtale (DPA) dersom det kreves.
Dokumentversjon følger produktet i git.*

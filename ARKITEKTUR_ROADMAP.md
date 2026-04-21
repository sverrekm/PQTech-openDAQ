# Arkitektur-roadmap — PQTech openDAQ

## Kontekst

Systemet skal kommersialiserast som ein **måleboks som stillast ut hos kunde**,
typisk kopla til PQ Tech sin sentrale hub via **5G-tilkobling**. Full 20 kHz
råstraum over 5G er ikkje praktisk (~8 TB/mnd per boks, teknisk umogleg med
CGNAT). Arkitekturen må bli **edge-first** med aggregert telemetri over VPN
og full resolusjon på forespørsel.

## Status i dag (2026-04-21)

### Kva fungerer

- **Edge-node (Sundet/IOTmanager)**: Direkte-modus med SIRIUS ADC + MQTT + Modbus TCP (PQube 3)
- **Openddaq-bru**: 8 ADC + 8 MQTT + 13 Modbus-kanalar eksponert som openDAQ NativeStreaming
- **Modbus-integrasjon**: PQube 3 preset med 13 register frå Classic Bank (V/A/Hz/W/VA). Base-adresse 7000, auto-reconnect, poll-rate konfigurerbar
- **Hub (Kontor/en.pqtech.no)**: Aggregerer kanalar frå Sundet via Tailscale, eksponerer til DewesoftX
- **Buffer-system**: Lokal SQLite-ringbuffer på edge, hub-sync via HTTP
- **Frontend**: Sidebar med kollapsible seksjonar, hub restart-knapp, kanal-range overstyring
- **Auto-recovery**: Hub-kanalar (modbus, openDAQ) kjem tilbake etter container-restart

### Kjende begrensingar

1. **Tailscale via DERP-relay "hel"** (Helsinki): ~5-15 Mbps, saturert av 20 kHz streaming
   - Sundet har netcheck OK (offentleg IP, cone NAT, UDP: true)
   - Mangler UPnP på router — prøv aktivering
   - For 5G-kundar vil det uansett vere CGNAT → DERP uansett
2. **Fryser etter ein stund**: data-relay-tråd kan blokkere på `_hub_lock` om remote-node er treg. Restart løyser. Fix-oppgåve #37.
3. **MQTT-verdiar vs modbus-verdiar**: Begge virker no via DataPacket-injeksjon. Tidleg problem med at DC-property klampar ved 10V er løyst ved å bruke `oppdater_modbus_data` path når `antal_adc=0`.

## Målarkitektur

```
Kunde-site (5G, CGNAT)        PQ Tech datasenter                Consumers
────────────────────         ─────────────────                  ─────────
SIRIUS + PQube               VPS (eigen DERP, ~€4/mnd)          DewesoftX (1-10 Hz live + burst)
   │                                    ↓                          │
   ├─ Full 20 kHz buffer    Hub (Kontor)                            │
   │  (SQLite, retensjon)            │                              ↓
   │                                  ├─ NativeStreaming            InfluxDB/Grafana (trends)
   └─ Aggregert live:        ←──     │    (1-10 Hz aggregert)
      • 1-10 Hz RMS/snitt             │
      • Event-triggers                ├─ MQTT publisher              Custom integrasjonar
      • Status ~1/min                 │    (low-rate telemetri)
      Total ~25 MB/dag                │
                                       ├─ REST /api/data/historic     ERP-pull
      På forespørsel:                  │
      • Full burst       ←──          │
        /api/burst?from&to             └─ InfluxDB writer             Langtidsanalyse
```

## Estimert datamengde per edge-node

| Komponent | Bandbreidde (live) | Månadleg |
|-----------|-------------------|----------|
| 20 Hz aggregert × 20 kanalar | ~2 kbps | ~20 MB |
| Event-triggers | <100 bps snitt | <1 MB |
| Status/heartbeat | <10 bps | <1 MB |
| **Total live over 5G** | | **~25 MB/dag ≈ 750 MB/mnd** |
| Burst-henting på forespørsel | 20 kHz × tidsvindu | Avhenger av bruk |

## Roadmap (prioritert)

### Fase 1 — Stabilisering (før kommersialisering)
- **#37** Stabilitet: heartbeat + lock-timeout i data-relay (fjerner freeze-symptom)
- **#31** VPS med eigen Tailscale DERP-server (fiksar CGNAT for kundar)

### Fase 2 — Edge-first arkitektur (kjernen i komersiell løysing)
- **#32** Edge-aggregering: 1-10 Hz live stream (reduserer 5G-forbruk 1000×)
- **#33** Burst-henting API: `/api/burst?node&from&to` (full resolusjon på forespørsel)

### Fase 3 — Eksterne integrasjonar
- **#34** MQTT publisher på hub (custom klientar)
- **#35** InfluxDB writer på hub (Grafana-dashbord)
- **#36** REST `/api/data/historic` for ERP-pull

## Demo-pakken (i morgon)

**Systemet virker no** med:
- SIRIUS ADC + PQube modbus + MQTT verdiar live i DewesoftX via hub
- Kanalar er synlege i hub-sidebar og på Hub-sida
- Full frontend (restart-knapp, kollapsible sidebar, kanal-range overstyring)

### Demo-flow forslag

1. Vis hub-UI: kva nodar er tilkobla (IOTmanager/Sundet)
2. Vis at PQube-verdiar kjem live (V_L1_N, Frekvens, P_total)
3. Vis DewesoftX med alle 20 kanalar oppdateringar live
4. Vis buffer-system (SQLite-ringbuffer med retensjon)
5. Snakk om roadmap: edge-first for 5G-kundar, eksterne integrasjonar klare for utviding

### Backup-plan ved freeze

Viss hub fryser under demo:
```bash
ssh sverre@en.pqtech.no "sudo docker restart pqtech-opendaq"
```
(5-10 sek nedtid, automatisk rekobling)

## Tekniske notat

### Pymodbus 3.7+ kompatibilitet
ModbusKlient sin `_les_med_kompat` prøver `device_id`, `slave`, `unit`-kwargs i
rekkefølge for å handtere API-endringar mellom versjonar.

### openDAQ PostScaling for modbus
Modbus-kanalar brukar DataPacket-injeksjon direkte (ikkje acqLoop + DC) når
`antal_adc=0`. Dette unngår DC-property clamping (-10 til 10) og leverer rå
fysiske verdiar.

### Hub pending_changes + restart-knapp
Når openDAQ-nodar vert lagt til/fjerna via UI, set hub_server `_pending_changes=True`.
UI viser banner + restart-knapp. `restart_hub()` gjer `os.execv` → ~10 sek nedtid,
DewesoftX rekoblar automatisk.

### Cache på hent_hub_kanalar + hent_hub_status
TTL 750ms / 1s. Timeout på `_hub_lock.acquire(2.0)` — returnerer stale cache viss
bakgrunnstråd blokkerer. Fiksa CF 524-timeouts.

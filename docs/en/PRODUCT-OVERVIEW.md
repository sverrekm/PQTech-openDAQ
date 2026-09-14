# PQTech openDAQ — Product Overview

*Customer-facing overview: what the product is, how it works, what data it
handles and where, and how it is secured. For hands-on use, see the
[User Manual](USER-MANUAL.md). Norwegian version: [../no/PRODUKTOVERSIKT.md](../no/PRODUKTOVERSIKT.md).*

---

## 1. What is PQTech openDAQ?

PQTech openDAQ is a **measurement gateway for power and energy data**. It connects
measurement instruments (Dewesoft SIRIUS, Elspec PQube 3 / BlackBox, Modbus
meters, MQTT sources, solar inverters with SunSpec) to analysis tools and
databases, and makes the measurements available **locally at the site** and —
if desired — aggregated in one place for a whole portfolio of sites.

In short: a small, rugged box (Raspberry Pi) that sits at the site, continuously
collects measurement data, keeps it even if the network drops, and delivers it
onward to Dewesoft DewesoftX, Grafana/InfluxDB, reports and the customer's own
systems.

**The problem it solves:**
- Instruments from different vendors speak different protocols (openDAQ/OPC-UA,
  Modbus, MQTT, FTP/CSV). PQTech openDAQ unifies them into one stream.
- Sites often run on 5G/CGNAT with no fixed IP — hard to reach from outside. The
  product uses a secure mesh network (Tailscale) so data can be retrieved with
  no open inbound ports.
- Measurement data must not be lost on a network outage — the node buffers
  locally and backfills.
- One customer may have many sites — data can be separated per **customer and
  site** for downstream processing.

---

## 2. How it works — architecture

The product is **edge-first**: acquisition and storage happen as close to the
instrument as possible, and data moves only when needed.

```
  Instrument(s)              Node (Raspberry Pi)            Hub (optional)
  ─────────────              ────────────────────           ──────────────
  SIRIUS (USB)      ─┐
  PQube 3 (Modbus)  ─┤  →   Acquisition + local buffer  →   Aggregates many
  MQTT sources      ─┤       + openDAQ server                nodes, stores,
  Modbus/SunSpec    ─┘       (DewesoftX connects here)       shares, reports
                                     │
                                     └── Push over HTTPS (token) → Hub
```

**Two roles (same software, chosen in setup):**

- **Node** — sits at the site. Collects from the instruments, builds channels,
  buffers locally, and exposes the data to DewesoftX (OPC-UA + native streaming).
  Can push selected channels onward to a hub.
- **Hub** — gathers data from many nodes in one place. Stores time series,
  archives raw files to NAS (split per customer/site), shares to Grafana, and
  provides one dashboard across the whole fleet. A hub can live at the
  customer's office or in the cloud.

A node works perfectly on its own (no hub required). The hub is for those who
want to consolidate multiple sites.

---

## 3. Data sources / instruments

| Source | Connection | Note |
|---|---|---|
| Dewesoft SIRIUS | USB | Direct driver; high-speed ADC. Shared to DewesoftX over the network. |
| Elspec PQube 3 | Modbus TCP | Auto-discovered on the network; streams over openDAQ. |
| Elspec BlackBox G4500 | FTP (CSV) / Modbus RS-485 | CSV reports fetched over FTP; PQZIP waveforms archived. Modbus needs an RS-485→TCP gateway. |
| MQTT sources | MQTT | Broker is sniffed; power-relevant topics can become channels. |
| Modbus meters (generic) | Modbus TCP | Configurable registers. |
| Solar inverters | SunSpec (Modbus) | Auto-discovery of SunSpec blocks. |

---

## 4. Data flow — from instrument to analysis

1. **Acquisition.** The node reads the instrument (USB/Modbus/MQTT/FTP) and
   builds named channels (voltage, current, power, frequency, …).
2. **Local buffer.** Values are written to a local ring buffer (SQLite) on the
   node. If the network drops, the gap is backfilled once the link returns
   (gap-free store-and-forward).
3. **DewesoftX.** The node runs an openDAQ server (OPC-UA + native streaming), so
   DewesoftX can connect and see the instrument as if it were local.
4. **Push to hub** (optional). The node sends selected channels to the hub over
   HTTPS with a shared token.
5. **Storage on the hub.** The hub stores the time series in a database and can
   archive raw CSV files to NAS.
6. **Sharing.** Channel values can be shared to Grafana/InfluxDB, read via a
   read API, and used in reports.

---

## 5. What data is collected, and where is it stored?

PQTech openDAQ handles **technical measurement data** (electrical quantities and
timestamps) — not personal data.

| Data type | Storage location | Retention |
|---|---|---|
| Channel values (time series) | Node: local buffer (`/data/maalinger`). Hub: SQLite database, tagged per node. | Ring buffer on the node; configurable on the hub. |
| Raw-file archive (CSV) | Hub → **NAS**, split `{NAS}/{customer}/{site}/{date}.csv` | As long as the NAS has space (customer's control). |
| PQZIP waveforms (Elspec) | Hub/node → NAS (`/data/nas/pqzip`) | Configurable (e.g. 14 days). Opened in Elspec PQSCADA. |
| Configuration | Locally on each device (`/data/konfig`) | Until changed. |

**Data location (important for customers):** by default the data lives at the
**site itself** (the node) and optionally on a **hub and NAS the customer owns**.
A cloud hub is optional. The customer decides where the data lives. Data can be
separated per customer and site for downstream processing in third-party tools.

---

## 6. Security & privacy

- **Access control.** The web interface requires login. Nodes are set up with an
  **SSH key** (not a shared password) for administration.
- **Node ↔ hub.** All push goes over **HTTPS** and requires a shared token; a
  wrong token is rejected. The hub signs proxy access to nodes with the same
  token (single sign-on), and the token is never sent to the browser.
- **Remote access with no open ports.** Devices are joined in a **Tailscale**
  mesh network (WireGuard-encrypted). No inbound ports need opening in the
  customer's firewall, and it works behind 5G/CGNAT.
- **Internal proxies** (instrument and FTP proxy) accept **private IP addresses
  only** — they cannot be used to reach the open internet.
- **Data at the site.** The edge-first design lets measurement data stay local;
  nothing leaves the site unless configured.
- **Privacy.** The product processes electrical measurement data and timestamps,
  not personal data. (Customers with specific requirements can get a dedicated
  data-processing note.)

---

## 7. Networking & connectivity

- **Measurement LAN:** the node gets its own IP on the site network (wired), so
  DewesoftX reaches it directly. The IP is chosen automatically (a free address)
  at startup, or set statically.
- **5G / CGNAT:** supported via Tailscale — the node is reachable with no fixed
  public IP.
- **First-time setup with no PC:** on first boot the node raises its own
  **Wi-Fi setup network with a captive portal** — the operator connects with a
  phone and configures the node in a wizard.
- **Instrument isolation:** an instrument with its own router (e.g. an Elspec
  BlackBox) can be reached without taking over the node's internet path.

---

## 8. Compatibility & standards

- **Dewesoft DewesoftX** — OPC-UA + native streaming (the instrument appears as
  a Dewesoft device).
- **openDAQ** — open data-acquisition SDK (OPC-UA, streaming, Python).
- **Modbus TCP/RTU** — standard registers; configurable.
- **MQTT** — broker sniffing and topic→channel.
- **SunSpec** — solar inverters.
- **InfluxDB / Grafana** — sharing and visualization.
- **Elspec** — PQube 3 (Modbus), BlackBox G4500 (FTP/CSV, PQZIP via PQSCADA).

---

## 9. Hardware

- **Raspberry Pi 5** or **Compute Module 5 (CM5)** with Wi-Fi.
- **M.2 NVMe** for the OS and local data storage.
- Wired network (eth) to the measurement LAN; Wi-Fi for setup/uplink.
- Mass-producible: one golden image is cloned to many nodes, each
  self-configuring via the captive portal on first boot.

---

## 10. Operations & maintenance

- **Updates** are managed centrally: the whole fleet can be updated from one
  place.
- **Monitoring:** each dashboard shows status (server, instrument, buffer, push,
  events, log).
- **Rollout:** golden image + captive portal give fast, terminal-free deployment
  of new nodes.

---

## 11. Known limitations (honest)

- **Elspec PQZIP** is a closed, compressed waveform format and **cannot** be
  turned into live channels without Elspec PQSCADA. PQTech openDAQ archives the
  files to NAS for later analysis.
- **The Elspec G4500 has no Modbus TCP** (RS-485 only). Live Modbus measurement
  requires an RS-485→Modbus-TCP gateway. Alternatively, CSV reports are fetched
  over FTP.
- **openDAQ over WAN** has a practical limit on channel count before latency/
  bandwidth become noticeable; very many channels should be buffered/pushed
  rather than streamed raw over the WAN.

---

*Document version follows the product in git. Last updated: see git history.*

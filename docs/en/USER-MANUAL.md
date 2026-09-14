# PQTech openDAQ — User Manual

*Hands-on manual for operators: set up a node, use the interface, add
instruments, operate and troubleshoot. For what the product is, see the
[Product Overview](PRODUCT-OVERVIEW.md). Norsk: [../no/BRUKERMANUAL.md](../no/BRUKERMANUAL.md).*

---

## Contents
1. [Getting started (first-time setup)](#1-getting-started-first-time-setup)
2. [Logging in and the interface](#2-logging-in-and-the-interface)
3. [The dashboard](#3-the-dashboard)
4. [Settings](#4-settings)
5. [Adding instruments](#5-adding-instruments)
6. [Node ↔ hub (push)](#6-node--hub-push)
7. [Hub mode](#7-hub-mode)
8. [Storage and data](#8-storage-and-data)
9. [Updates and admin](#9-updates-and-admin)
10. [Troubleshooting](#10-troubleshooting)
11. [Glossary](#11-glossary)

---

## 1. Getting started (first-time setup)

A factory-fresh node is set up without a PC, via a built-in Wi-Fi setup network:

1. Insert the M.2 disk, connect **eth0** to the measurement network (cable),
   power on.
2. On a phone/PC: connect to the open Wi-Fi network **`PQTech-Setup-XXXX`**
   (XXXX = last 4 of the device serial). The captive portal opens automatically
   (otherwise go to `http://10.42.0.1/`).
3. Fill in the wizard:
   - **Node name** — identifies the box (shown in DewesoftX, hub, reports).
   - **Role** — Node (measures) or Hub (aggregates).
   - **Uplink** — Ethernet or Wi-Fi (+ network/password).
   - **Container IP** — **Auto** (recommended; picks a free address) or Static.
   - Optional: **Hub URL + token** to push data to a hub.
4. Press **Finish setup**. The node connects, starts, and marks itself done.
5. Find the node on the network (router DHCP list, mDNS, or the hub dashboard)
   and open `http://<node-ip>:8080`.

> Mass production: see [PROVISIONING.md](../PROVISIONING.md) for golden-image +
> cloning.

---

## 2. Logging in and the interface

- Open `http://<node-ip>:8080` and log in.
- **Header:** product name + node info (Node / Address / Mode) and a
  "Server active" indicator. "Log out" on the right.
- **Sidebar (left):** Dashboard, Hub, **Channels** (with live values), MQTT,
  Hub channels/nodes, First-time setup, Correlation explorer, Settings, Admin.
  Channel rows show the live value on the right and a status dot.

---

## 3. The dashboard

**Node mode:**
- **Instrument meter grid** (top): each active channel and MQTT topic as a large
  reading with a source tag (Sirius/USB/Sim/MQTT) and a sparkline. Channels from
  a connected PQube/modbus sub-node also appear here (colored per node).
- **Cards below:** channel table (live), openDAQ bridge, buffer status, events,
  MQTT log, server status, log.

**Hub mode:**
- **Fleet meter grid** over the visible channels the nodes push (colored per
  node), node overview, channel filter (pick which channels/nodes you see),
  channel table, log.

---

## 4. Settings

Settings is split into five categories (numbered index on the left):

**01 — Device & measurement**
- **Name this box** — node name (tags all measurements).
- **Device** — model, ADC channel count, and *USB cards on dashboard*
  (Auto/Always/Never — hide USB cards on modbus/MQTT nodes).
- **Channels** — edit channels (name, unit, active, scaling).
- **Instrument discovery** — add discovered instruments (PQube/G4500).
- **Node services** — SMTP receiver, NTP time server, FTP proxy.
- **PQZIP archive** — archive Elspec waveforms to NAS.
- **Buffer** — local measurement buffer + hub sync.

**02 — Sharing & integrations**
- **Hub connection** — set where the node pushes (parent URL + fleet token) and
  see live push status. *(See §6.)*
- **MQTT** — broker settings.
- **MQTT discovery** — sniff the broker, turn topics into channels.
- **InfluxDB/Grafana** — share channel values.
- **EMC** — EMC calculations.
- **API keys** — read-API keys (`pqt_…`).

**03 — Storage**
- **NAS** + **raw-file archive** (CSV) + **hub database**. *(See §8.)*

**04 — Network & nodes**
- **Wi-Fi**, **instrument network**, **network scan**, **instrument FTP**,
  **Tailscale**, **hub/node config** (add remote nodes).

**05 — Diagnostics (advanced)**
- Console, probe analysis, endpoint recovery. Handle with care.

---

## 5. Adding instruments

**Dewesoft SIRIUS (USB):** connect via USB → shows under SIRIUS status →
streams to DewesoftX. Channels are configured in *Channels*.

**Elspec PQube 3 (Modbus TCP):** Settings → *Network & nodes* → **Network scan**
→ find the PQube → **Add as PQube 3**. It becomes a modbus node that streams over
openDAQ. On a node it lands in the **Hub nodes** list; its channels are injected
into the openDAQ bridge and (if push is configured) onward to the hub.

**Elspec BlackBox G4500:**
- **CSV reports (DL/MR log):** Settings → *Network & nodes* → **Instrument FTP**.
  Set the root to the reports folder (typically `/CF_UPMB/Reports`). Report
  generation must be **enabled in the G4500's own interface** for new CSVs to be
  produced.
- **PQZIP waveforms:** archived to NAS via *PQZIP archive* — opened in Elspec
  PQSCADA (cannot become live channels).
- **Modbus:** the G4500 has RS-485 only → requires an RS-485→Modbus-TCP gateway.

**MQTT source:** Settings → *Sharing* → **MQTT discovery** → sniff `#` → pick a
power-relevant topic → **Add as channel**.

**SunSpec inverter:** **Network scan** recognizes SunSpec → add it.

**Generic Modbus meter:** *Network & nodes* → **hub/node config** → add a node
(type modbus_tcp) with registers.

---

## 6. Node ↔ hub (push)

To make a node send data to a hub:

1. Settings → *Sharing & integrations* → **Hub connection**.
2. **Hub URL** = the hub's address (e.g. `https://opendac.pqtech.no`).
3. **Fleet token** = the shared ingest token the hub requires (the same on every
   node that pushes to the same hub).
4. **Save**. Watch the status: **Sent OK** should climb, **Failed**/"Push error"
   should clear.

The token must match exactly — a wrong token gives `401 Invalid token`, and the
hub then sees no channels from the node.

---

## 7. Hub mode

- Set the role to **Hub** (wizard or Settings → Device).
- **Add nodes:** Settings → *Network & nodes* → hub/node config → address, name,
  **Customer** (for the NAS folder layout), and optionally type/protocol.
- **Open a node's UI via the hub:** click the node in the sidebar (Hub nodes) →
  the node-proxy opens the node's interface through the hub (requires hub login).
- **Fleet dashboard + channel filter:** pick which channels/nodes you see.

---

## 8. Storage and data

- **Local buffer (node):** a ring buffer of RMS values; backfills gaps after a
  network outage.
- **Hub database:** time series from all nodes in one database, tagged per node
  (drives the dashboard, Correlation explorer, Grafana sharing).
- **Raw-file archive (NAS):** CSV per **customer/site**:
  `{NAS}/{customer}/{node}/{node}_{date}.csv`. Enable it in *Storage*; the NAS
  must be mounted. Set **Customer** on each node for the customer folder
  (otherwise `{node}/`).
- **PQZIP archive (NAS):** Elspec waveforms for later PQSCADA analysis.

---

## 9. Updates and admin

- **Admin page:** change password, choose language (English/Bokmål), and run a
  software update.
- **Fleet update:** the whole fleet can be updated centrally from the hub.
- An update downloads the new version and restarts the service automatically.

---

## 10. Troubleshooting

| Symptom | Likely cause / action |
|---|---|
| Setup AP `PQTech-Setup-…` doesn't appear | Wi-Fi country not set / rfkill. Set the Wi-Fi country, `nmcli radio wifi on`. |
| Can't find the node on the network | Check the router DHCP list; the node auto-picks a free IP. Static: check `CONTAINER_IP`. |
| Hub doesn't show the node's channels | Push not configured / wrong token → *Hub connection*: set the right Hub URL + Fleet token (`401` = wrong token). |
| Node-proxy bounces you to login | Hub session expired — log in to the hub again. |
| Nothing is saved to NAS | Enable the raw-file archive in *Storage* on the **hub**, set the folder, and make sure the NAS is mounted. |
| PQube channels not on the hub | The node must push (Hub connection token) — channels travel via push, not directly. |
| FTP only finds old CSVs | The CSV reports live in `Reports/`; PQZIPDATA_ is waveform (PQZIP). Enable report generation in the G4500. |
| Instrument loses USB (SIRIUS) | Often the cable; restart the container after a hotplug. |
| Container restarts in a loop | Check `docker logs pqtech-opendaq` on the host. |

---

## 11. Glossary

- **Node** — a box at the site that measures.
- **Hub** — aggregates data from several nodes.
- **Channel** — one measured quantity (voltage, current, power, …).
- **Push / ingest** — the node sends channel values to the hub (HTTPS + token).
- **Fleet token / ingest token** — the shared secret nodes use toward the hub.
- **macvlan** — gives the container its own IP on the LAN (so DewesoftX reaches
  it).
- **Tailscale** — encrypted mesh network for remote access with no open ports.
- **PQZIP** — Elspec's compressed waveform format (opened in PQSCADA).
- **Captive portal** — the setup web page the node shows during first-time setup.

---

*Document version follows the product in git.*

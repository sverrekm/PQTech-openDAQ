# PQTech openDAQ — Data Handling and Security

*Technical note on what data the solution processes, where it is stored, how it
is transferred and secured, and who has access. Intended for customers and their
security/privacy assessment. Norsk: [../no/DATABEHANDLING.md](../no/DATABEHANDLING.md).*

> **About this document.** This is a *technical* description of the data handling
> in the product. It is **not** a legal Data Processing Agreement (DPA) and does
> not replace one. Use it as a factual basis for your own compliance work (e.g.
> risk assessment / DPIA). Details may vary with how the solution is deployed at
> each customer.

---

## 1. What data is processed

The product is a **measurement gateway** and mainly processes **technical
measurement data**:

| Category | Example | Personal data? |
|---|---|---|
| Measurement values (time series) | voltage, current, power, frequency + timestamp | No |
| Waveform / events | Elspec PQZIP, event log | No |
| Configuration/metadata | node name, **customer/site name** (as a label), instrument setup | May be identifying |
| Network data | IP addresses, Tailscale node names | May be identifying |
| Account data | administrator **username** (login) | Yes (limited) |

The measurement data itself is not personal data. Customer/site names, IP
addresses and administrator usernames *may* be identifying to a limited degree
and should be part of the customer's own assessment.

---

## 2. Where data is stored (data location)

The solution is **edge-first** — data is stored as close to the instrument as
possible:

| Location | What | Owner |
|---|---|---|
| **Node** (at the site) | local buffer (measurement values), configuration | Customer (equipment at the site) |
| **Hub** (optional) | time-series database (per node), sharing setup | Customer (office) or cloud |
| **NAS** (optional) | raw-file archive (CSV per customer/site), PQZIP archive | Customer |

**The customer decides where the data lives.** A node works on its own with data
only at the site. The hub and NAS are the customer's own equipment unless agreed
otherwise. A cloud hub is optional.

---

## 3. Transfer and encryption

- **Node → hub:** over **HTTPS/TLS**, authenticated with a shared token. Without
  a valid token, data is rejected.
- **Remote access:** via **Tailscale** (WireGuard-encrypted mesh). Traffic is
  end-to-end encrypted; no inbound ports are opened in the customer's firewall.
- **Cloud hub (if used):** reached over HTTPS/TLS (behind a reverse proxy/CDN).
- **Internal proxies** (instrument/FTP) accept private IP addresses only and
  cannot route out to the open internet.
- **At rest:** measurement data is stored as SQLite/CSV/PQZIP on the device
  disk/NAS. Disk/volume encryption is the **customer's choice** (e.g. an
  encrypted NAS or host disk encryption) and is not enabled by default.

---

## 4. Retention and deletion

| Data | Retention | Deletion |
|---|---|---|
| Local buffer (node) | ring buffer (oldest overwritten) | automatic rotation |
| Hub database | configurable | deleted/cleared on the hub |
| Raw-file archive (NAS) | as long as the NAS has space | customer deletes files |
| PQZIP archive (NAS) | configurable retention (e.g. 14 days) | automatic pruning + manual |
| Configuration | until changed | `golden-reset` wipes all node state |

When a node is decommissioned/re-purposed, **`pqtech-golden-reset`** removes all
node-specific state (config, tokens, Wi-Fi profiles, tailscale state, measurement
data, SSH keys).

---

## 5. Access control

- **Web interface:** requires login (username + password).
- **Node administration (SSH):** **key-based**, password login disabled — no
  shared account password across the fleet.
- **Node → hub:** shared **ingest token**; the hub signs proxy access with the
  same token (single sign-on). The token is never sent to the browser.
- **Read API:** dedicated keys (`pqt_…`) for third-party reads.
- Each node can have a unique identity; the golden image is wiped so clones do
  not share secrets.

---

## 6. Security measures (summary)

- Encryption in transit (HTTPS/TLS, WireGuard).
- No open inbound ports (Tailscale mesh; works behind 5G/CGNAT).
- Token-based node↔hub authentication; a wrong token is rejected.
- Private-IP-only internal proxies.
- Key-based SSH, no shared password.
- Centrally managed software updates.
- `golden-reset` for safe reuse/decommissioning.

---

## 7. Sub-processors / third parties

Depending on the deployment, the following third parties may be involved:

| Third party | Role | Data content |
|---|---|---|
| **Tailscale** | coordinates the mesh network (WireGuard) | key exchange/coordination; measurement data travels encrypted node↔node (possibly via an encrypted relay), not stored by Tailscale |
| **Reverse proxy/CDN** (cloud hub) | TLS termination in front of the cloud hub | traffic to the cloud hub (if used) |
| **Git server** (updates) | delivers software updates | no measurement data |
| **InfluxDB / Grafana** (if configured) | visualization/sharing | channel values the customer chooses to share |

Use of a cloud hub, external InfluxDB/Grafana etc. is **optional** and controlled
by the customer's setup.

---

## 8. Personal data — assessment

- The core data (electrical measurement values + timestamps) is **not** personal
  data.
- Metadata such as **customer/site names, IP addresses and administrator
  usernames** may be identifying to a limited degree.
- The customer should perform their own assessment (and DPIA if applicable) based
  on how the solution is used, which labels are set, and where the hub/NAS is
  located.

---

## 9. Customer responsibilities

- Where the **hub and NAS** are placed, and their physical/logical protection.
- Who is granted **login and SSH-key access**.
- **Backup** of the hub database/NAS archive (the customer's routines).
- Network security at the site (firewall, segmentation).
- Any **encryption at rest** (encrypted NAS/disk) where required.

---

## 10. Backup and incident handling

- **Data loss on a network outage** is mitigated by the local buffer + gap-free
  backfill.
- **Backup** of the hub database and NAS archive is the customer's responsibility
  (NAS routines).
- On suspected compromise: rotate tokens/SSH keys, and use `golden-reset` +
  re-provisioning on affected nodes.

---

*Contact PQTech for a formal Data Processing Agreement (DPA) if required.
Document version follows the product in git.*

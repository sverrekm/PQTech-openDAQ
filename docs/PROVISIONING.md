# Node-provisjonering & masseproduksjon

Slik lagar du eit **golden-image** på M.2 som kan klonast til mange nodar. Kvar
klona node reiser eit **ope WiFi-AP med captive portal** ved fyrste boot, tek
ein **ekte DHCP-lease** (så mange nodar kan stå på same LAN utan å kollidere),
og set seg opp utan terminal.

Maskinvare: Raspberry Pi 5 eller CM5 med WiFi. Måле-LAN-et er **kabla (eth0)** —
macvlan treng ein kabla parent. `wlan0` brukast til setup-AP-et og (valfritt)
management-uplink.

---

## 1. Prep ein master (éin gong)

1. **Flash Raspberry Pi OS Bookworm Lite (64-bit)** til M.2 med Raspberry Pi
   Imager. I Imager sine innstillingar: sett **WiFi-land** (viktig — utan det
   startar ikkje AP-et), hostname, og aktiver SSH om du vil.
2. Boot masteren, og på den:
   ```bash
   sudo apt-get update
   curl -fsSL https://get.docker.com | sh          # Docker
   sudo apt-get install -y network-manager git      # NetworkManager (AP) + git
   sudo systemctl enable --now NetworkManager
   sudo raspi-config nonint do_wifi_country NO       # WiFi-land om ikkje sett
   ```
3. **Klon repoet til `/opt/pqtech-opendaq`** (stien firstboot-tenesta ventar):
   ```bash
   sudo git clone https://git.pqtech.no/sverre/pq-tech-opendaq /opt/pqtech-opendaq
   ```
   (Ligg det ein annan stad, rett `WorkingDirectory`/`ExecStart` i
   `pqtech-firstboot.service`.)
4. **Bygg imaget ÉIN gong** her, så fyrste boot på nodane slepp den treige
   bygginga:
   ```bash
   cd /opt/pqtech-opendaq
   NET_PARENT=eth0 docker compose build
   ```
   Imaget `pqtech-opendaq:latest` ligg no i Docker og vert gjenbrukt av
   `start.sh up -d` utan `--build`.
5. **Installer firstboot-tenesta** (host-oppsettet — kernel-moduler/udev — gjer
   containeren sjølv ved oppstart, sjå `docker-entrypoint.sh`):
   ```bash
   sudo cp /opt/pqtech-opendaq/pqtech-firstboot.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable pqtech-firstboot.service
   ```

> Test gjerne heile flyten på masteren først: `sudo bash pqtech-golden-reset.sh`,
> reboot, sjekk at `PQTech-Setup-XXXX` dukkar opp og at wizarden fullfører.

## 2. Nullstill til golden

Rett før kloning, fjern all node-spesifikk tilstand:
```bash
sudo bash /opt/pqtech-opendaq/pqtech-golden-reset.sh
sudo shutdown -h now
```
Dette slettar node-konfig, `.env`, tailscale-state, lagra WiFi-profilar, måledata
og host-SSH-nøklar — men **beheld** Docker, imaget, repoet og firstboot-tenesta.

## 3. Klon disken per node

Ta ut M.2-en og klon han (vel éin):
- **Raspberry Pi Imager** → "Use custom" → master-`.img` du har dumpa.
- `dd if=/dev/masterdisk of=/dev/nynode bs=4M status=progress` (Linux).
- `rpi-clone` node-til-node.

## 4. Boot ein node

1. Sett M.2 i noden, kople **eth0** til måле-LAN-et, slå på.
2. På telefon/PC: kople til det opne nettet **`PQTech-Setup-XXXX`**
   (XXXX = siste 4 av Pi-serienummeret). Captive-portalen sprett opp
   automatisk (elles gå til `http://10.42.0.1/`).
3. Fyll ut: namn, rolle (node/hub), uplink (ethernet/WiFi), container-IP
   (**Auto** tilrådd), og valfritt hub-URL + token. Trykk **Finish setup**.
4. Noden riggar ned AP-et, koplar opp, startar containeren, og set marker så
   dette ikkje skjer igjen. Finn han deretter på LAN-et (mDNS/ruter-lease).

---

## IP-modus

Docker sin macvlan-driver kan **ikkje** ta ein ekte DHCP-lease (krev eit
adresse-pool). I staden VEL noden ein ledig adresse:

- **Auto (standard):** ved fyrste `start.sh up` les noden subnett/gateway av
  verten sin `eth0` og skannar `.240 → .200` etter ein **ledig** IP, som han
  pinner (skrivast til `.env`, stabil etterpå). Ingen hardkoda adresse å
  kollidere med; DewesoftX finn noden via mDNS.
- **Static:** `IP_MODE=static` + `CONTAINER_IP` i `.env` (set av captive-
  portalen eller `pqtech-config.sh` → Nettverk).

## Re-provisjonering

For å køyre setup på nytt på ein alt provisjonert node:
```bash
sudo rm -f /opt/pqtech-opendaq/konfig/provisioned && sudo reboot
```

## Feilsøking

- **AP-et startar ikkje:** WiFi-land ikkje sett, eller rfkill.
  `nmcli radio wifi` / `sudo raspi-config nonint do_wifi_country NO`.
- **Auto-IP feilar («fann inga ledig adresse» / «klarte ikkje lese subnett»):**
  eth0 må ha kabel + eigen IP før start. Sjekk `ip addr show eth0`. Eller sett
  fast IP: `pqtech-config.sh` → Nettverk → Fast IP.
- **Fleire nodar med same IP:** kvar node auto-vel ein ledig adresse ved fyrste
  boot og pinner han i `.env`. Klonar du ein disk UTAN `golden-reset` fyrst,
  arvar alle same `.env`/IP — køyr alltid `pqtech-golden-reset.sh` før kloning.

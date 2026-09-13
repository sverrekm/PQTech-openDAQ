#!/bin/bash
# =============================================================
#  pqtech-firstboot — headless first-boot provisioning
# =============================================================
#  Køyrer på HOST-en (Raspberry Pi) via pqtech-firstboot.service.
#  - Er noden alt sett opp (marker finst) → start containeren og avslutt.
#  - Elles: reis eit ope WiFi-AP + captive portal, serv setup-wizarden
#    (provision_server.py), og når operatøren har fylt ut:
#      rig ned AP-et, kople opp uplink (om WiFi vald), start containeren,
#      og set marker so dette ikkje skjer igjen.
#
#  Ingen passord på AP-et — meint for produksjon/utrulling. WiFi-PSK og
#  token vert aldri logga.
# =============================================================
set -u

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KONFIG_DIR="$REPO_DIR/konfig"
MARKER="$KONFIG_DIR/provisioned"
REQ="/run/pqtech-provision-request.json"
AP_CON="pqtech-ap"
CAPTIVE_CONF="/etc/NetworkManager/dnsmasq-shared.d/pqtech-captive.conf"
AP_IP="10.42.0.1"

logg() { echo "[firstboot] $*"; }

start_container() {
    cd "$REPO_DIR" || exit 1
    logg "Startar container (start.sh up -d)"
    bash start.sh up -d
}

# --- Alt provisjonert? Berre start og gå. ---
if [ -f "$MARKER" ]; then
    logg "Marker finst — noden er alt sett opp."
    start_container
    exit 0
fi

# --- Treng vi eit WiFi-grensesnitt for AP-et ---
if ! command -v nmcli >/dev/null 2>&1; then
    logg "nmcli manglar — kan ikkje reise AP. Startar container utan provisjonering."
    start_container
    exit 0
fi
if ! ls /sys/class/net | grep -q '^wlan'; then
    logg "Inkje wlan-grensesnitt — hoppar over captive portal, startar container."
    start_container
    exit 0
fi

# --- SSID-suffiks frå Pi-serienummeret ---
SER="$(cat /sys/firmware/devicetree/base/serial-number 2>/dev/null | tr -d '\0')"
[ -z "$SER" ] && SER="$(awk '/^Serial/{print $3}' /proc/cpuinfo 2>/dev/null | tail -1)"
SUFF="$(printf '%s' "${SER: -4}" | tr 'a-z' 'A-Z')"
[ -z "$SUFF" ] && SUFF="$(printf '%04d' $((RANDOM % 10000)))"
SSID="PQTech-Setup-$SUFF"

# --- Reis ope AP + captive DNS ---
logg "Reiser setup-AP: $SSID"
nmcli radio wifi on >/dev/null 2>&1 || true
# Alle DNS-oppslag → portalen, so telefonen sitt captive-sjekk sprett opp.
mkdir -p "$(dirname "$CAPTIVE_CONF")"
printf 'address=/#/%s\n' "$AP_IP" > "$CAPTIVE_CONF"

nmcli connection delete "$AP_CON" >/dev/null 2>&1 || true
nmcli connection add type wifi ifname wlan0 con-name "$AP_CON" autoconnect no \
    ssid "$SSID" \
    802-11-wireless.mode ap 802-11-wireless.band bg \
    ipv4.method shared >/dev/null 2>&1
if ! nmcli connection up "$AP_CON" >/dev/null 2>&1; then
    logg "Kunne ikkje reise AP-et (WiFi-land sett? rfkill?). Prøv: raspi-config → WLAN country."
    logg "Startar container utan provisjonering so noden i det minste er oppe."
    rm -f "$CAPTIVE_CONF"
    start_container
    exit 0
fi

# --- Serv setup-wizarden ---
logg "AP oppe. Serverar setup-portal på http://$AP_IP/"
rm -f "$REQ"
PQTECH_REPO="$REPO_DIR" python3 "$REPO_DIR/provision_server.py" &
PROV_PID=$!

# --- Vent på at operatøren fullfører (provision_server skriv REQ) ---
while [ ! -f "$REQ" ]; do
    if ! kill -0 "$PROV_PID" 2>/dev/null; then
        logg "Provision-serveren stoppa uventa — startar han på nytt."
        PQTECH_REPO="$REPO_DIR" python3 "$REPO_DIR/provision_server.py" &
        PROV_PID=$!
    fi
    sleep 2
done
logg "Oppsett motteke. Riggar ned AP og bruker det."
kill "$PROV_PID" 2>/dev/null || true

# --- Les nettverks-handlingane (passord aldri logga) ---
UPLINK="$(python3 -c "import json;print(json.load(open('$REQ')).get('uplink','ethernet'))" 2>/dev/null)"
WSSID="$(python3 -c "import json;print(json.load(open('$REQ')).get('wifi_ssid',''))" 2>/dev/null)"
WPASS="$(python3 -c "import json;print(json.load(open('$REQ')).get('wifi_pass',''))" 2>/dev/null)"

# --- Rigg ned AP-et ---
nmcli connection down "$AP_CON" >/dev/null 2>&1 || true
nmcli connection delete "$AP_CON" >/dev/null 2>&1 || true
rm -f "$CAPTIVE_CONF"

# --- WiFi-uplink om vald (elles står wlan0 fri; eth0 er måле-LAN) ---
if [ "$UPLINK" = "wifi" ] && [ -n "$WSSID" ]; then
    logg "Koplar wlan0 til uplink-nettet «$WSSID»"
    nmcli radio wifi on >/dev/null 2>&1 || true
    if [ -n "$WPASS" ]; then
        nmcli device wifi connect "$WSSID" password "$WPASS" ifname wlan0 >/dev/null 2>&1 \
            || logg "WiFi-uplink feila (feil passord/rekkevidde?)"
    else
        nmcli device wifi connect "$WSSID" ifname wlan0 >/dev/null 2>&1 \
            || logg "WiFi-uplink feila"
    fi
fi
WPASS=""   # ikkje la passordet liggje i miljøet

# --- Start containeren og set marker ---
start_container
mkdir -p "$KONFIG_DIR"
date -u +"%Y-%m-%dT%H:%M:%SZ" > "$MARKER"
rm -f "$REQ"
logg "Ferdig. Noden er provisjonert."

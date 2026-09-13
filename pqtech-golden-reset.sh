#!/bin/bash
# =============================================================
#  pqtech-golden-reset — gjer ein master-node til reint golden-image
# =============================================================
#  Køyr på HOST-en RETT FØR du klonar M.2-disken til nye nodar.
#  Fjernar ALL node-spesifikk tilstand, men lèt Docker, det førehands-
#  lasta imaget, repoet og firstboot-tenesta stå. Etter klon + fyrste boot
#  reiser kvar node sitt eige setup-AP (captive portal) og set seg opp.
#
#  Bruk:  sudo bash pqtech-golden-reset.sh
# =============================================================
set -u
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR" || exit 1

if [ "$(id -u)" != 0 ]; then
    echo "Køyr som root: sudo bash pqtech-golden-reset.sh"; exit 1
fi

echo "== PQTech golden-reset i $REPO_DIR =="
echo "Dette slettar node-konfig, WiFi-profilar, tailscale-state og måledata."
read -r -p "Halde fram? [j/N] " svar
case "$svar" in j|J|y|Y) ;; *) echo "Avbrote."; exit 0;; esac

# 1. Stopp og fjern container + nettverk (imaget står).
if docker compose version >/dev/null 2>&1; then
    docker compose down 2>/dev/null
fi

# 2. Måledata + databasar (les DATA_DIR frå .env før vi slettar .env).
DATA_DIR="$(grep -E '^DATA_DIR=' .env 2>/dev/null | tail -1 | cut -d= -f2-)"
for d in "$DATA_DIR" ./maalinger ./maalingar ./nas; do
    [ -n "$d" ] || continue
    case "$d" in /*) abs="$d";; *) abs="$REPO_DIR/${d#./}";; esac
    if [ -d "$abs" ]; then
        find "$abs" -maxdepth 2 -type f \( -name '*.db' -o -name '*.db-*' \
             -o -name '*.csv' -o -name '*.parquet' \) -delete 2>/dev/null
        echo "  reinska måledata i $abs"
    fi
done

# 3. Node-konfig + hemmelegheiter + provisjonerings-marker.
# *.key = t.d. flask_secret.key (Flask session-nøkkel). Delt over klonar ville
# late alle nodane dele same session-hemmelegheit — generer på nytt per node.
rm -f konfig/*.json konfig/*.key konfig/provisioned 2>/dev/null
echo "  fjerna konfig/*.json + *.key + provisioned-marker"

# 4. .env (IP-modus, fast IP, token, NAS/DATA-stiar) → tilbake til defaults.
rm -f .env
echo "  fjerna .env (default: IP_MODE=auto — vel ledig IP ved fyrste boot)"

# 5. Tailscale-state (kvar node må re-autentisere).
rm -rf tailscale/* 2>/dev/null
echo "  tømde tailscale/-state"

# 6. Lagra WiFi-profilar på verten (ikkje send master sine nett-credentials).
if command -v nmcli >/dev/null 2>&1; then
    nmcli -t -f NAME,TYPE connection show 2>/dev/null \
        | awk -F: '$2 ~ /wireless/ {print $1}' \
        | while read -r c; do nmcli connection delete "$c" >/dev/null 2>&1; done
    nmcli connection delete pqtech-ap >/dev/null 2>&1 || true
    echo "  sletta lagra WiFi-profilar"
fi
rm -f /etc/NetworkManager/dnsmasq-shared.d/pqtech-captive.conf 2>/dev/null

# 7. SSH host-nøklar på VERTEN — regenererast per node ved boot (unngå at alle
#    klonar deler nøkkel). Container sine DewesoftX-nøklar lagast i entrypoint.
rm -f /etc/ssh/ssh_host_* 2>/dev/null
echo "  fjerna host SSH-nøklar (regenererast ved fyrste boot)"

echo ""
echo "== Ferdig. Slå av, klon M.2-disken, sett i nodane. =="
echo "Kvar node reiser 'PQTech-Setup-XXXX' ved fyrste boot."

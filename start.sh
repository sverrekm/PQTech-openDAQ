#!/bin/bash
# Start PQTech-openDAQ container med auto-detektert nett og auto-vald IP.
#
# macvlan gir containeren sin eigen IP paa LAN-et (DewesoftX treng SSH dit).
# Docker sin macvlan-driver kan IKKJE ta ein ekte DHCP-lease (krev eit
# adresse-pool), so i staden VEL vi ein ledig IP ved fyrste oppstart:
#   IP_MODE=auto (standard): les subnett/gateway av verten, skann etter ein
#     ledig adresse, skriv CONTAINER_IP til .env (stabil etterpaa).
#   IP_MODE=static: brukaren har sett CONTAINER_IP i .env.
#
# Bruk som docker compose: ./start.sh up -d   |   ./start.sh down   osv.
set -e
cd "$(dirname "$0")"
ENV_FIL=".env"

les_env() {  # KEY [default]
    local key="$1" def="${2:-}" v=""
    [ -f "$ENV_FIL" ] && v="$(grep -E "^${key}=" "$ENV_FIL" 2>/dev/null | tail -1 | cut -d= -f2-)"
    [ -z "$v" ] && v="$def"; printf '%s' "$v"
}
sett_env() {  # KEY VALUE (oppdater/legg til)
    local key="$1" val="$2"; touch "$ENV_FIL"
    if grep -qE "^${key}=" "$ENV_FIL"; then
        local tmp; tmp="$(mktemp)"; sed "s|^${key}=.*|${key}=${val}|" "$ENV_FIL" > "$tmp" && mv "$tmp" "$ENV_FIL"
    else printf '%s=%s\n' "$key" "$val" >> "$ENV_FIL"; fi
}

# --- Parent-grensesnitt (macvlan) ---
[ -z "$NET_PARENT" ] && NET_PARENT="$(les_env NET_PARENT)"
if [ -z "$NET_PARENT" ]; then
    NET_PARENT="$(ip route show default 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="dev") print $(i+1)}' | head -1)"
fi
if [ -z "$NET_PARENT" ]; then
    echo "FEIL: Fann ikkje nettverksgrensesnitt. Sett NET_PARENT=eth0 ./start.sh up -d"; exit 1
fi
export NET_PARENT
echo "Nettverksgrensesnitt: $NET_PARENT"

# --- IP-modus ---
IP_MODE="$(les_env IP_MODE)"
# Bakoverkompat: manglar IP_MODE men CONTAINER_IP finst → behandla som static.
[ -z "$IP_MODE" ] && { [ -n "$(les_env CONTAINER_IP)" ] && IP_MODE="static" || IP_MODE="auto"; }

CONTAINER_IP="$(les_env CONTAINER_IP)"
NET_SUBNET="$(les_env NET_SUBNET)"
NET_GATEWAY="$(les_env NET_GATEWAY)"

# --- Les subnett/gateway av verten om vi manglar dei ---
if [ -z "$NET_SUBNET" ]; then
    cidr="$(ip -o -f inet addr show dev "$NET_PARENT" 2>/dev/null | awk '{print $4; exit}')"
    [ -n "$cidr" ] && NET_SUBNET="$(python3 -c "import sys,ipaddress;print(ipaddress.ip_interface(sys.argv[1]).network)" "$cidr" 2>/dev/null)"
fi
[ -z "$NET_GATEWAY" ] && NET_GATEWAY="$(ip route show default 2>/dev/null | awk -v d="$NET_PARENT" '$5==d {print $3; exit}')"

# --- AUTO: vel ein ledig IP dersom vi ikkje har ein ---
if [ "$IP_MODE" != "static" ] && [ -z "$CONTAINER_IP" ]; then
    if [ -z "$NET_SUBNET" ] || [ -z "$NET_GATEWAY" ]; then
        echo "FEIL: klarte ikkje lese subnett/gateway av $NET_PARENT (har det fatt IP?)."
        echo "      Sett fast IP: pqtech-config.sh → Nettverk, eller CONTAINER_IP i .env."; exit 1
    fi
    prefiks="$(printf '%s' "$NET_GATEWAY" | cut -d. -f1-3)"
    verts_ip="$(ip -o -f inet addr show dev "$NET_PARENT" 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -1)"
    echo "Auto-IP: skannar $prefiks.240 ned til .200 etter ledig adresse..."
    for x in $(seq 240 -1 200); do
        kand="${prefiks}.${x}"
        [ "$kand" = "$verts_ip" ] && continue
        [ "$kand" = "$NET_GATEWAY" ] && continue
        if ! ping -c1 -W1 "$kand" >/dev/null 2>&1; then
            CONTAINER_IP="$kand"; break
        fi
    done
    if [ -z "$CONTAINER_IP" ]; then
        echo "FEIL: fann inga ledig adresse i ${prefiks}.200–240. Sett fast IP manuelt."; exit 1
    fi
    echo "Auto-IP: valde ledig adresse $CONTAINER_IP"
    sett_env IP_MODE "auto"
    sett_env CONTAINER_IP "$CONTAINER_IP"
    sett_env NET_SUBNET "$NET_SUBNET"
    sett_env NET_GATEWAY "$NET_GATEWAY"
    sett_env NET_PARENT "$NET_PARENT"
fi

export CONTAINER_IP NET_SUBNET NET_GATEWAY
echo "IP-modus: $IP_MODE  →  container-IP: ${CONTAINER_IP:-?}  (subnett ${NET_SUBNET:-?}, gw ${NET_GATEWAY:-?})"

exec docker compose "$@"

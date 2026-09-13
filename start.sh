#!/bin/bash
# Start PQTech-openDAQ container med auto-detektert nettverksgrensesnitt.
# Finn parent-interface for macvlan automatisk (default route), og vel
# compose-filer ut fraa IP_MODE i .env (dhcp = berre base, static = base +
# docker-compose.static.yml med fast CONTAINER_IP).
#
# Bruk som docker compose: ./start.sh up -d   |   ./start.sh down   osv.
set -e

cd "$(dirname "$0")"

# Les IP_MODE frae .env (docker compose les .env sjoelv for variabel-
# substitusjon, men vi treng verdien her for aa velje compose-filer).
# Bakoverkompatibelt: manglar IP_MODE men CONTAINER_IP finst → static (gamle
# nodar var fast IP). Heilt utan .env → dhcp (nye nodar).
IP_MODE=""
if [ -f .env ]; then
    IP_MODE="$(grep -E '^IP_MODE=' .env 2>/dev/null | tail -1 | cut -d= -f2-)"
    if [ -z "$IP_MODE" ]; then
        if grep -qE '^CONTAINER_IP=.+' .env 2>/dev/null; then IP_MODE="static"; fi
    fi
fi
[ -z "$IP_MODE" ] && IP_MODE="dhcp"

# Auto-detekter nettverksgrensesnitt frå default route
if [ -z "$NET_PARENT" ]; then
    NET_PARENT=$(ip route show default 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="dev") print $(i+1)}' | head -1)
fi

if [ -z "$NET_PARENT" ]; then
    echo "FEIL: Kunne ikkje finne nettverksgrensesnitt. Sett NET_PARENT manuelt:"
    echo "  NET_PARENT=ens33 ./start.sh up -d"
    exit 1
fi

export NET_PARENT
echo "Nettverksgrensesnitt: $NET_PARENT"

# Compose-filer: static legg paa override med fast IP; dhcp = berre base.
FILER=(-f docker-compose.yml)
if [ "$IP_MODE" = "static" ]; then
    FILER+=(-f docker-compose.static.yml)
    echo "IP-modus: static ($(grep -E '^CONTAINER_IP=' .env 2>/dev/null | cut -d= -f2-))"
else
    echo "IP-modus: dhcp (containeren hentar lease sjoelv)"
fi

exec docker compose "${FILER[@]}" "$@"

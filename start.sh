#!/bin/bash
# Start PQTech-openDAQ container med auto-detektert nettverksgrensesnitt.
# Finn parent-interface for macvlan automatisk (default route).
set -e

cd "$(dirname "$0")"

# Auto-detekter nettverksgrensesnitt frå default route
if [ -z "$NET_PARENT" ]; then
    NET_PARENT=$(ip route show default 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="dev") print $(i+1)}' | head -1)
fi

if [ -z "$NET_PARENT" ]; then
    echo "FEIL: Kunne ikkje finne nettverksgrensesnitt. Sett NET_PARENT manuelt:"
    echo "  NET_PARENT=ens33 docker compose up -d"
    exit 1
fi

export NET_PARENT
echo "Nettverksgrensesnitt: $NET_PARENT"
exec docker compose "$@"

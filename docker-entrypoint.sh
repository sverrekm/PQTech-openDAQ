#!/bin/bash
# =============================================================
# USB-over-IP Entrypoint - Dewesoft SIRIUS
# =============================================================
# Starter usbipd, finner SIRIUS og deler den over nettverket.
# Kjorer som bakgrunnstjeneste i Docker.
# =============================================================
set -e

POLL_INTERVALL="${POLL_INTERVALL:-10}"
AUTO_BIND="${AUTO_BIND:-true}"

echo "=============================================="
echo "  USB-over-IP Server - Dewesoft SIRIUS"
echo "  $(date)"
echo "=============================================="
echo ""

# Last kjernemoduler (krever --privileged)
echo "[1/3] Laster kjernemoduler..."
modprobe usbip-core 2>/dev/null || echo "  usbip-core allerede lastet"
modprobe usbip-host 2>/dev/null || echo "  usbip-host allerede lastet"
echo "      OK"

# Funksjon for aa finne SIRIUS
finn_sirius() {
    local busid=""

    # Sok etter Dewesoft/SIRIUS/Dewetron i USB-enheter
    for dev in $(usbip list -l 2>/dev/null | grep "busid" | awk '{print $3}'); do
        local info
        info=$(usbip list -l 2>/dev/null | grep -A1 "$dev")
        if echo "$info" | grep -qi "dewesoft\|sirius\|dewetron"; then
            busid="$dev"
            break
        fi
    done

    # Bruk miljovariabel som fallback
    if [ -z "$busid" ] && [ -n "$SIRIUS_BUSID" ]; then
        busid="$SIRIUS_BUSID"
    fi

    echo "$busid"
}

# Start usbipd daemon
echo ""
echo "[2/3] Starter usbipd daemon..."
usbipd -D
echo "      usbipd startet (port 3240)"

# Finn og del SIRIUS
echo ""
echo "[3/3] Soker etter SIRIUS..."

BUSID=$(finn_sirius)

if [ -n "$BUSID" ]; then
    usbip bind -b "$BUSID" 2>/dev/null && \
        echo "      SIRIUS delt: busid=$BUSID" || \
        echo "      Allerede delt eller feil ved binding av $BUSID"
else
    echo "      SIRIUS ikke funnet ved oppstart"
    echo "      Tilgjengelige USB-enheter:"
    usbip list -l 2>/dev/null || echo "        (ingen)"
    echo ""
    echo "      Sett SIRIUS_BUSID miljovariabel eller koble til SIRIUS"
fi

# Vis tilkoblingsinfo
IP=$(hostname -I 2>/dev/null | awk '{print $1}')
echo ""
echo "=============================================="
echo "  USB-over-IP Server kjorer"
echo "  IP: ${IP:-ukjent}"
echo "  Port: 3240"
if [ -n "$BUSID" ]; then
    echo "  SIRIUS: $BUSID (delt)"
    echo ""
    echo "  Pa Windows-PC, kjor:"
    echo "    usbipd attach --remote ${IP:-<PI-IP>} --busid $BUSID"
fi
echo "=============================================="
echo ""

# Start web-grensesnitt
WEB_PORT="${WEB_PORT:-8080}"
echo ""
echo "[Web] Starter web-grensesnitt pa port ${WEB_PORT}..."
python3 /app/web_ui.py &
echo "      http://${IP:-localhost}:${WEB_PORT}"

# Hold containeren kjorende og overvak
echo ""
echo "Overvakning startet (sjekker hvert ${POLL_INTERVALL}s)..."
while true; do
    # Sjekk at usbipd kjorer
    if ! pgrep -x usbipd > /dev/null; then
        echo "[$(date +%H:%M:%S)] usbipd stoppet - starter pa nytt..."
        usbipd -D
    fi

    # Sjekk om SIRIUS er tilkoblet og delt
    if [ "$AUTO_BIND" = "true" ]; then
        CURRENT_BUSID=$(finn_sirius)
        if [ -n "$CURRENT_BUSID" ]; then
            # Proov aa binde hvis ikke allerede delt
            usbip bind -b "$CURRENT_BUSID" 2>/dev/null && \
                echo "[$(date +%H:%M:%S)] SIRIUS tilkoblet og delt: $CURRENT_BUSID"
        fi
    fi

    sleep "$POLL_INTERVALL"
done

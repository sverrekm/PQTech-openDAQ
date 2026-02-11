#!/bin/bash
# =============================================================
# openDAQ Server + Web UI - Docker Entrypoint
# =============================================================
# Starter openDAQ server som eksponerer SIRIUS over nettverket,
# og Flask web-grensesnitt for oversikt og styring.
# =============================================================
set -e

WEB_PORT="${WEB_PORT:-8080}"

echo "=============================================="
echo "  openDAQ Server - Dewesoft SIRIUS"
echo "  $(date)"
echo "=============================================="
echo ""

# Vis tilkobling
if [ -n "${TILKOBLING}" ]; then
    echo "  Tilkobling: ${TILKOBLING}"
else
    echo "  Tilkobling: Auto-oppdagelse (SIRIUS USB)"
fi
echo "  Web UI:     port ${WEB_PORT}"
echo ""

# Standardverdier for autonom maaling
MAALE_INTERVALL="${MAALE_INTERVALL:-60}"
MAALE_VARIGHET="${MAALE_VARIGHET:-5}"
SAMPLE_RATE="${SAMPLE_RATE:-1000}"
MAALE_PREFIKS="${MAALE_PREFIKS:-maaling}"

echo "  Maaling:    hvert ${MAALE_INTERVALL}s, varighet ${MAALE_VARIGHET}s"
echo "  Utmappe:    /data/maalinger"
echo ""

# Last usbmon for passiv USB-trafikkfangst (krever privileged mode)
if [ ! -e /sys/kernel/debug/usb/usbmon ]; then
    echo "Laster usbmon kernel-modul..."
    modprobe usbmon 2>/dev/null && echo "  usbmon lastet OK" || echo "  usbmon ikke tilgjengelig (kjor 'sudo modprobe usbmon' paa hosten)"
    # Monter debugfs hvis noedvendig
    if [ ! -e /sys/kernel/debug/usb ]; then
        mount -t debugfs none /sys/kernel/debug 2>/dev/null || true
    fi
fi
echo ""

# Felles maaling-argumenter
FELLES_ARGS=(
    --maale-intervall "${MAALE_INTERVALL}"
    --maale-varighet "${MAALE_VARIGHET}"
    --sample-rate "${SAMPLE_RATE}"
    --prefiks "${MAALE_PREFIKS}"
    --utmappe /data/maalinger
)

# Fix hostname-oppslag for OPC-UA server
# open62541 bruker gethostname() for endpoint-URL. Docker mapper hostname
# til 127.0.x.x i /etc/hosts, saa OPC-UA annonserer localhost.
# Fiks: erstatt med faktisk IP slik at DewesoftX kan koble til.
OPENDAQ_IP="${OPENDAQ_IP:-$(hostname -I | awk '{print $1}')}"
if [ -n "$OPENDAQ_IP" ]; then
    CURRENT_HOST=$(hostname)
    if grep -q "127\.0.*${CURRENT_HOST}" /etc/hosts; then
        # sed -i feiler paa Docker bind-mount, bruk cp+cat i staden
        sed "s/127\.0[.0-9]*[[:space:]]*${CURRENT_HOST}/${OPENDAQ_IP} ${CURRENT_HOST}/" /etc/hosts > /tmp/hosts.fixed
        cat /tmp/hosts.fixed > /etc/hosts
        rm -f /tmp/hosts.fixed
        echo "  OPC-UA hostname: ${CURRENT_HOST} -> ${OPENDAQ_IP}"
    fi
fi
echo ""

export PYTHONPATH=/app

if [ "${NATIVE_SIRIUS}" = "true" ]; then
    # ========================================
    # SIRIUS Direkte-modus (uten openDAQ SDK)
    # Bruker reverse-engineered USB-protokoll
    # ========================================
    echo "Modus: SIRIUS Direkte + openDAQ Nettverksservere"
    echo ""

    SERVER_CMD=(python3 -m sirius_server "${FELLES_ARGS[@]}")

    cd /app
    echo "Starter SIRIUS server + web UI..."
    echo ""
    exec "${SERVER_CMD[@]}"
else
    # ========================================
    # openDAQ-modus (standard)
    # ========================================
    echo "Modus: openDAQ SDK"
    echo ""

    # Bruker python3 -m fordi openDAQ ModuleManager trenger '' i sys.path
    # for aa finne .module.so-filer i CWD
    SERVER_CMD=(python3 -m opendaq_server "${FELLES_ARGS[@]}")

    if [ -n "${TILKOBLING}" ]; then
        SERVER_CMD+=(--tilkobling "${TILKOBLING}")
    fi

    if [ "${BRUK_SIMULATOR}" = "true" ]; then
        SERVER_CMD+=(--simulator)
    fi

    # openDAQ laster moduler fra CWD via '' i sys.path
    cd /usr/local/lib

    echo "Starter openDAQ server + web UI..."
    echo ""
    exec "${SERVER_CMD[@]}"
fi

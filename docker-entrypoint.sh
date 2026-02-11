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

# Bygg openDAQ server-kommando
# Bruker python3 -m fordi openDAQ ModuleManager trenger '' i sys.path
# for aa finne .module.so-filer i CWD
export PYTHONPATH=/app
SERVER_CMD=(python3 -m opendaq_server
    --maale-intervall "${MAALE_INTERVALL}"
    --maale-varighet "${MAALE_VARIGHET}"
    --sample-rate "${SAMPLE_RATE}"
    --prefiks "${MAALE_PREFIKS}"
    --utmappe /data/maalinger
)

if [ -n "${TILKOBLING}" ]; then
    SERVER_CMD+=(--tilkobling "${TILKOBLING}")
fi

if [ "${BRUK_SIMULATOR}" = "true" ]; then
    SERVER_CMD+=(--simulator)
fi

# openDAQ laster moduler fra CWD via '' i sys.path
cd /usr/local/lib

# Start openDAQ server + web UI (same prosess for delt tilstand)
echo "Starter openDAQ server + web UI..."
echo ""
exec "${SERVER_CMD[@]}"

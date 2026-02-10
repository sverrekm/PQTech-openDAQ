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

# Bygg openDAQ server-kommando
SERVER_CMD=(python3 /app/opendaq_server.py)

if [ -n "${TILKOBLING}" ]; then
    SERVER_CMD+=(--tilkobling "${TILKOBLING}")
fi

if [ "${BRUK_SIMULATOR}" = "true" ]; then
    SERVER_CMD+=(--simulator)
fi

# Start web-grensesnitt i bakgrunnen
echo "[1/2] Starter web-grensesnitt paa port ${WEB_PORT}..."
python3 /app/web_ui.py &
WEB_PID=$!
echo "      OK (PID: ${WEB_PID})"

# Start openDAQ server (forgrunnen - holder containeren kjorende)
echo "[2/2] Starter openDAQ server..."
echo ""
exec "${SERVER_CMD[@]}"

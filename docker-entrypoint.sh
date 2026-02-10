#!/bin/bash
# =============================================================
# Dewesoft Målesystem - Docker Entrypoint
# =============================================================
# Starter dewesoft_maaling.py som bakgrunnstjeneste.
# Håndterer graceful shutdown via SIGTERM.
#
# Miljøvariabler:
#   KONFIG_FIL       - Sti til konfigurasjonsfil (standard: /data/konfig/konfig_strom_spenning.json)
#   MAALE_INTERVALL   - Sekunder mellom målinger (standard: 60)
#   MAALE_VARIGHET    - Varighet per måling i sekunder (standard: 5)
#   MAALE_PREFIKS     - Filnavn-prefiks (standard: maaling)
#   TILKOBLING        - Overstyr connection string (tom = bruk konfig)
#   SAMPLE_RATE       - Overstyr sample rate (tom = bruk konfig)
# =============================================================
set -e

# Standardverdier
KONFIG_FIL="${KONFIG_FIL:-/data/konfig/konfig_strom_spenning.json}"
MAALE_INTERVALL="${MAALE_INTERVALL:-60}"
MAALE_VARIGHET="${MAALE_VARIGHET:-5}"
MAALE_PREFIKS="${MAALE_PREFIKS:-maaling}"

echo "=============================================="
echo "  Dewesoft SIRIUS Målesystem - Docker"
echo "=============================================="
echo ""
echo "  Konfig:       ${KONFIG_FIL}"
echo "  Intervall:    ${MAALE_INTERVALL}s"
echo "  Varighet:     ${MAALE_VARIGHET}s"
echo "  Prefiks:      ${MAALE_PREFIKS}"
echo "  Tilkobling:   ${TILKOBLING:-fra konfig}"
echo "  Utmappe:      /data/maalinger"
echo ""

# Sjekk at konfigurasjonsfil finnes
if [ ! -f "${KONFIG_FIL}" ]; then
    echo "[FEIL] Konfigurasjonsfil ikke funnet: ${KONFIG_FIL}"
    echo "       Legg en konfig-fil i ./konfig/ mappen"
    echo "       Tilgjengelige filer i /data/konfig/:"
    ls -la /data/konfig/ 2>/dev/null || echo "       (tom mappe)"
    exit 1
fi

# Bygg kommando
CMD=(python3 /app/dewesoft_maaling.py
    --konfig "${KONFIG_FIL}"
    --varighet "${MAALE_VARIGHET}"
    --gjenta "${MAALE_INTERVALL}"
    --prefiks "${MAALE_PREFIKS}"
    --utmappe /data/maalinger
)

# Legg til tilkobling hvis satt
if [ -n "${TILKOBLING}" ]; then
    CMD+=(--tilkobling "${TILKOBLING}")
fi

# Legg til sample rate hvis satt
if [ -n "${SAMPLE_RATE}" ]; then
    CMD+=(--sample-rate "${SAMPLE_RATE}")
fi

echo "Starter: ${CMD[*]}"
echo ""

# Kjør med signal-forwarding for graceful shutdown
exec "${CMD[@]}"

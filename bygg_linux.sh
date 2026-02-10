#!/bin/bash
#
# Bygg openDAQ for Linux / Raspberry Pi
# ======================================
# Dette scriptet bygger openDAQ fra kildekode med støtte for
# OPC-UA og native streaming — nødvendig for å koble til
# Dewesoft SIRIUS på Linux.
#
# Bruk:
#   chmod +x bygg_linux.sh
#   ./bygg_linux.sh          # Standard x64 Linux
#   ./bygg_linux.sh --pi     # Raspberry Pi (aarch64)
#
set -e

PI_BYGG=false
if [ "$1" = "--pi" ]; then
    PI_BYGG=true
    echo "=== Bygger for Raspberry Pi (aarch64) ==="
else
    echo "=== Bygger for Linux x64 ==="
fi

# --- 1. Installer avhengigheter ---
echo ""
echo "[1/4] Installerer avhengigheter..."
sudo apt-get update
sudo apt-get install -y \
    git \
    build-essential \
    cmake \
    ninja-build \
    python3 \
    python3-dev \
    python3-pip \
    python3-numpy \
    lld

# --- 2. Konfigurer CMake ---
echo ""
echo "[2/4] Konfigurerer CMake..."

KILDEMAPPE="$(cd "$(dirname "$0")" && pwd)"
BYGGMAPPE="${KILDEMAPPE}/build/linux"
mkdir -p "$BYGGMAPPE"

CMAKE_ARGS=(
    -S "$KILDEMAPPE"
    -B "$BYGGMAPPE"
    -G Ninja
    -DCMAKE_BUILD_TYPE=Release

    # Aktiver protokoller for SIRIUS-tilkobling
    -DOPENDAQ_ENABLE_OPCUA=ON
    -DOPENDAQ_ENABLE_NATIVE_STREAMING=ON
    -DOPENDAQ_ENABLE_WEBSOCKET_STREAMING=ON

    # Aktiver enhetsmoduler
    -DDAQMODULES_REF_DEVICE_MODULE=ON
    -DDAQMODULES_SIMULATOR_DEVICE_MODULE=ON
    -DDAQMODULES_OPENDAQ_CLIENT_MODULE=ON
    -DDAQMODULES_OPENDAQ_SERVER_MODULE=ON

    # Aktiver dataopptak-moduler
    -DDAQMODULES_BASIC_CSV_RECORDER_MODULE=ON
    -DDAQMODULES_REF_FB_MODULE=ON

    # Python-bindings
    -DOPENDAQ_GENERATE_PYTHON_BINDINGS=ON

    # Posisjonsuavhengig kode (kreves for ARM og delte biblioteker)
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON
)

if [ "$PI_BYGG" = true ]; then
    CMAKE_ARGS+=(
        -DCMAKE_C_COMPILER=gcc
        -DCMAKE_CXX_COMPILER=g++
    )
fi

cmake "${CMAKE_ARGS[@]}"

# --- 3. Bygg ---
echo ""
echo "[3/4] Bygger openDAQ (dette kan ta en stund)..."

# Bruk antall kjerner minus 1 for å unngå å fryse systemet
KJERNER=$(($(nproc) - 1))
if [ "$KJERNER" -lt 1 ]; then
    KJERNER=1
fi

# På Raspberry Pi med lite RAM: begrens parallelle jobber
if [ "$PI_BYGG" = true ]; then
    MEM_GB=$(free -g | awk '/^Mem:/{print $2}')
    if [ "$MEM_GB" -lt 4 ]; then
        KJERNER=2
        echo "  Lite RAM ($MEM_GB GB) — bruker $KJERNER parallelle jobber"
    fi
fi

cmake --build "$BYGGMAPPE" -j "$KJERNER"

# --- 4. Installer Python-pakke ---
echo ""
echo "[4/4] Installerer Python-bindings..."

# Finn bygde Python-filer
PYTHON_MODUL=$(find "$BYGGMAPPE" -name "opendaq*.so" -o -name "opendaq*.pyd" | head -1)
if [ -n "$PYTHON_MODUL" ]; then
    MODUL_MAPPE=$(dirname "$PYTHON_MODUL")
    echo "  Python-modul funnet: $PYTHON_MODUL"
    echo ""
    echo "  For å bruke openDAQ i Python, legg til i PYTHONPATH:"
    echo "    export PYTHONPATH=\"$MODUL_MAPPE:\$PYTHONPATH\""
    echo ""
    echo "  Eller legg denne linjen i ~/.bashrc for permanent oppsett:"
    echo "    echo 'export PYTHONPATH=\"$MODUL_MAPPE:\$PYTHONPATH\"' >> ~/.bashrc"
else
    echo "  [ADVARSEL] Fant ikke Python-modul. Sjekk build-logg."
fi

# --- Ferdig ---
echo ""
echo "========================================"
echo "  openDAQ bygget for Linux!"
echo "========================================"
echo ""
echo "Neste steg:"
echo "  1. Sett PYTHONPATH (se over)"
echo "  2. Test med simulator:"
echo "     python3 dewesoft_maaling.py --simulator --konfig konfig_strom_spenning.json"
echo ""
echo "  3. Koble til SIRIUS via nettverk:"
echo "     python3 dewesoft_maaling.py --konfig konfig_sirius_opcua.json"
echo ""
echo "MERK: For USB-tilkobling til SIRIUS på Linux trengs det en"
echo "gateway-PC (Windows) som kjorer openDAQ server. Se README."
echo ""

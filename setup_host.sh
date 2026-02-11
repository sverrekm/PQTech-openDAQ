#!/bin/bash
# =============================================================
# USB/IP Host-oppsett for Dewesoft SIRIUS paa Raspberry Pi
# =============================================================
# Kjores EN GANG paa Pi-hosten (IKKE i Docker-container).
# Installerer usbip-verktoy og laster kernel-moduler som
# Docker-containeren trenger for aa dele SIRIUS over nettverket.
#
# Bruk:
#   sudo bash setup_host.sh
# =============================================================
set -e

echo "=============================================="
echo "  USB/IP Host-oppsett for Dewesoft SIRIUS"
echo "=============================================="
echo ""

# Sjekk at vi kjorer som root
if [ "$EUID" -ne 0 ]; then
    echo "[FEIL] Kjor som root: sudo bash setup_host.sh"
    exit 1
fi

# 1. Installer usbip-verktoy
echo "[1/3] Installerer usbip-verktoy..."
apt-get update -qq
apt-get install -y --no-install-recommends linux-tools-common linux-tools-generic usbip hwdata usbutils
echo "      OK"

# 2. Last kernel-moduler
echo ""
echo "[2/3] Laster kernel-moduler..."
modprobe usbip-core
modprobe usbip-host
echo "      usbip-core  OK"
echo "      usbip-host  OK"

# 3. Gjor modulene permanente (last ved oppstart)
echo ""
echo "[3/3] Konfigurerer automatisk modullasting..."
MODULES_FILE="/etc/modules"
if ! grep -q "^usbip-core" "$MODULES_FILE" 2>/dev/null; then
    echo "usbip-core" >> "$MODULES_FILE"
    echo "      Lagt til usbip-core i $MODULES_FILE"
else
    echo "      usbip-core allerede i $MODULES_FILE"
fi
if ! grep -q "^usbip-host" "$MODULES_FILE" 2>/dev/null; then
    echo "usbip-host" >> "$MODULES_FILE"
    echo "      Lagt til usbip-host i $MODULES_FILE"
else
    echo "      usbip-host allerede i $MODULES_FILE"
fi

# Verifiser
echo ""
echo "Verifiserer..."
if lsmod | grep -q usbip_core; then
    echo "  usbip_core: lastet"
else
    echo "  [ADVARSEL] usbip_core ikke lastet"
fi
if lsmod | grep -q usbip_host; then
    echo "  usbip_host: lastet"
else
    echo "  [ADVARSEL] usbip_host ikke lastet"
fi

echo ""
echo "=============================================="
echo "  Host-oppsett fullfort!"
echo "=============================================="
echo ""
echo "Neste steg:"
echo "  1. Koble SIRIUS til Pi via USB"
echo "  2. Bygg og start container: docker compose up -d --build"
echo "  3. Apne http://$(hostname -I | awk '{print $1}'):8080"
echo "  4. Klikk 'Del SIRIUS via USB/IP' i web UI"
echo ""

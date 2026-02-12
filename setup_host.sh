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

# Finn script-mappen (der 99-dewesoft.rules ligger)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# 1. Installer usbip-verktoy
echo "[1/5] Installerer usbip-verktoy..."
apt-get update -qq

# Raspberry Pi OS (Debian) og Ubuntu bruker forskjellige pakkenavn
if apt-cache show linux-tools-common &>/dev/null; then
    # Ubuntu
    apt-get install -y --no-install-recommends linux-tools-common linux-tools-generic usbip hwdata usbutils
else
    # Raspberry Pi OS / Debian
    apt-get install -y --no-install-recommends usbip usbutils hwdata
fi
echo "      OK"

# 2. Installer udev-regler for Dewesoft USB-enheter
echo ""
echo "[2/5] Installerer udev-regler for Dewesoft..."
RULES_SRC="${SCRIPT_DIR}/99-dewesoft.rules"
RULES_DST="/etc/udev/rules.d/99-dewesoft.rules"
if [ -f "$RULES_SRC" ]; then
    cp "$RULES_SRC" "$RULES_DST"
    chmod 644 "$RULES_DST"
    udevadm control --reload-rules
    udevadm trigger
    echo "      Installert: $RULES_DST"
    echo "      udev-regler lastet paa nytt"
else
    echo "      [ADVARSEL] $RULES_SRC ikke funnet, oppretter manuelt..."
    cat > "$RULES_DST" << 'RULES'
# Dewesoft USB-enheter - tilgang uten root
SUBSYSTEM=="usb", ATTR{idVendor}=="1ced", MODE="0666", GROUP="plugdev", TAG+="uaccess"
RULES
    chmod 644 "$RULES_DST"
    udevadm control --reload-rules
    udevadm trigger
    echo "      Minimal udev-regel installert: $RULES_DST"
fi

# 3. Legg bruker til plugdev-gruppen
echo ""
echo "[3/5] Konfigurerer brukertilgang..."
SUDO_BRUKER="${SUDO_USER:-$(logname 2>/dev/null || echo '')}"
if [ -n "$SUDO_BRUKER" ] && [ "$SUDO_BRUKER" != "root" ]; then
    if ! groups "$SUDO_BRUKER" | grep -q plugdev; then
        usermod -aG plugdev "$SUDO_BRUKER"
        echo "      $SUDO_BRUKER lagt til i plugdev-gruppen"
        echo "      (Logg ut og inn igjen for at endringen trer i kraft)"
    else
        echo "      $SUDO_BRUKER er allerede i plugdev-gruppen"
    fi
else
    echo "      [INFO] Kunne ikke finne bruker, legg til manuelt:"
    echo "      sudo usermod -aG plugdev <brukernavn>"
fi

# 4. Last kernel-moduler
echo ""
echo "[4/5] Laster kernel-moduler..."
modprobe usbip-core
modprobe usbip-host
modprobe usbmon
echo "      usbip-core  OK"
echo "      usbip-host  OK"
echo "      usbmon      OK"

# 5. Gjor modulene permanente (last ved oppstart)
echo ""
echo "[5/5] Konfigurerer automatisk modullasting..."
MODULES_FILE="/etc/modules"
for MODUL in usbip-core usbip-host usbmon; do
    if ! grep -q "^${MODUL}" "$MODULES_FILE" 2>/dev/null; then
        echo "$MODUL" >> "$MODULES_FILE"
        echo "      Lagt til $MODUL i $MODULES_FILE"
    else
        echo "      $MODUL allerede i $MODULES_FILE"
    fi
done

# Verifiser
echo ""
echo "Verifiserer..."

# Kernel-moduler
for MODUL in usbip_core usbip_host usbmon; do
    if lsmod | grep -q "$MODUL"; then
        echo "  ${MODUL}:$(printf '%*s' $((14 - ${#MODUL})) '')lastet"
    else
        echo "  [ADVARSEL] $MODUL ikke lastet"
    fi
done

# udev-regler
if [ -f /etc/udev/rules.d/99-dewesoft.rules ]; then
    echo "  udev-regler:   installert"
else
    echo "  [ADVARSEL] udev-regler mangler"
fi

# Sjekk om SIRIUS er tilkoblet
if lsusb 2>/dev/null | grep -qi "1ced"; then
    SIRIUS_INFO=$(lsusb | grep -i "1ced")
    echo "  Dewesoft USB:  FUNNET"
    echo "    $SIRIUS_INFO"
else
    echo "  Dewesoft USB:  Ikke tilkoblet (koble til SIRIUS og sjekk med lsusb)"
fi

echo ""
echo "=============================================="
echo "  Host-oppsett fullfort!"
echo "=============================================="
echo ""
echo "Neste steg:"
echo "  1. Koble SIRIUS til Pi via USB"
echo "  2. Verifiser tilgang: lsusb | grep 1ced"
echo "  3. Bygg og start container: docker compose up -d --build"
echo "  4. Apne http://$(hostname -I | awk '{print $1}'):8080"
echo "  5. Klikk 'Del SIRIUS via USB/IP' i web UI"
echo ""
echo "Feilsoking:"
echo "  - Hvis SIRIUS ikke vises i lsusb: proov annen USB-port/kabel"
echo "  - Hvis tilgangsfeil: logg ut og inn (plugdev-gruppe)"
echo "  - Sjekk udev-regler: udevadm test /sys/bus/usb/devices/<busid>"
echo ""

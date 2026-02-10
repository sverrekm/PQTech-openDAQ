#!/bin/bash
# =============================================================
# USB-over-IP oppsett for Dewesoft SIRIUS pa Raspberry Pi 5
# =============================================================
# Deler SIRIUS USB-enhet over nettverket slik at DewesoftX
# pa en Windows-PC kan koble til som om den var lokal.
#
# Bruk:
#   chmod +x setup_usbip.sh
#   sudo ./setup_usbip.sh
#
# Etter oppsett:
#   sudo ./usbip_del.sh          # Start deling
#   sudo ./usbip_del.sh --stopp  # Stopp deling
#   sudo ./usbip_del.sh --status # Vis status
# =============================================================
set -e

echo "=============================================="
echo "  USB-over-IP oppsett for Dewesoft SIRIUS"
echo "  Raspberry Pi 5"
echo "=============================================="
echo ""

# Sjekk at vi kjorer som root
if [ "$EUID" -ne 0 ]; then
    echo "[FEIL] Kjor som root: sudo ./setup_usbip.sh"
    exit 1
fi

# 1. Installer usbip-verktoy
echo "[1/4] Installerer usbip-verktoy..."
apt-get update
apt-get install -y linux-tools-common linux-tools-generic usbip hwdata usbutils
echo "      OK"

# 2. Last kjernemoduler
echo ""
echo "[2/4] Laster kjernemoduler..."
modprobe usbip-core
modprobe usbip-host
echo "      OK"

# 3. Gjor modulene permanente (last ved oppstart)
echo ""
echo "[3/4] Konfigurerer automatisk modullasting..."
if ! grep -q "usbip-core" /etc/modules 2>/dev/null; then
    echo "usbip-core" >> /etc/modules
    echo "usbip-host" >> /etc/modules
    echo "      Lagt til i /etc/modules"
else
    echo "      Allerede konfigurert"
fi

# 4. Opprett delingsscript
echo ""
echo "[4/4] Oppretter styringsscript..."

cat > /usr/local/bin/usbip_del.sh << 'SCRIPT'
#!/bin/bash
# Dewesoft SIRIUS USB-deling over nettverk
# Bruk: usbip_del.sh [--stopp|--status|--list]

ACTION="${1:-start}"

finn_sirius() {
    # Sok etter Dewesoft SIRIUS USB-enhet
    # Dewesoft USB vendor ID: typisk 0x21a1 eller sjekk med lsusb
    BUSID=""

    # Metode 1: Sok etter kjent vendor
    for dev in $(usbip list -l 2>/dev/null | grep "busid" | awk '{print $3}'); do
        INFO=$(usbip list -l 2>/dev/null | grep -A1 "$dev")
        if echo "$INFO" | grep -qi "dewesoft\|sirius\|dewetron"; then
            BUSID="$dev"
            break
        fi
    done

    # Metode 2: Vis alle USB-enheter og la bruker velge
    if [ -z "$BUSID" ]; then
        echo "Kunne ikke auto-oppdage SIRIUS."
        echo ""
        echo "Tilgjengelige USB-enheter:"
        usbip list -l 2>/dev/null || true
        echo ""
        echo "Alle USB-enheter (lsusb):"
        lsusb
        echo ""
        echo "Skriv inn bus-ID for SIRIUS (f.eks. 1-1.2):"
        read -r BUSID
    fi

    echo "$BUSID"
}

case "$ACTION" in
    start|--start)
        echo "Starter USB-over-IP server..."

        # Start usbipd daemon
        if ! pgrep -x usbipd > /dev/null; then
            usbipd -D
            echo "  usbipd startet"
        else
            echo "  usbipd kjorer allerede"
        fi

        # Finn og del SIRIUS
        BUSID=$(finn_sirius)
        if [ -n "$BUSID" ]; then
            usbip bind -b "$BUSID" 2>/dev/null && \
                echo "  SIRIUS delt: busid=$BUSID" || \
                echo "  Allerede delt eller feil ved binding"
        else
            echo "  [FEIL] Ingen enhet valgt"
            exit 1
        fi

        echo ""
        IP=$(hostname -I | awk '{print $1}')
        echo "================================================"
        echo "  SIRIUS er tilgjengelig over nettverket!"
        echo "  IP: $IP"
        echo "  Bus-ID: $BUSID"
        echo ""
        echo "  Pa Windows-PC (krever usbip-win2):"
        echo "    usbip.exe attach -r $IP -b $BUSID"
        echo "================================================"
        ;;

    stopp|--stopp|stop|--stop)
        echo "Stopper USB-deling..."
        # Unbind alle enheter
        for dev in $(usbip list -l 2>/dev/null | grep "busid" | awk '{print $3}'); do
            usbip unbind -b "$dev" 2>/dev/null && echo "  Unbind: $dev"
        done
        # Stopp daemon
        killall usbipd 2>/dev/null && echo "  usbipd stoppet" || echo "  usbipd kjorte ikke"
        ;;

    status|--status)
        echo "USB-over-IP status:"
        echo ""
        if pgrep -x usbipd > /dev/null; then
            echo "  usbipd: KJORER"
        else
            echo "  usbipd: STOPPET"
        fi
        echo ""
        echo "Delte enheter:"
        usbip list -l 2>/dev/null || echo "  (ingen)"
        echo ""
        echo "Tilkoblede klienter:"
        usbip port 2>/dev/null || echo "  (ingen)"
        ;;

    list|--list)
        echo "Alle USB-enheter pa Pi:"
        lsusb
        echo ""
        echo "USB-enheter tilgjengelig for deling:"
        usbip list -l 2>/dev/null || echo "  (kjor: sudo modprobe usbip-host)"
        ;;

    *)
        echo "Bruk: usbip_del.sh [start|stopp|status|list]"
        exit 1
        ;;
esac
SCRIPT

chmod +x /usr/local/bin/usbip_del.sh

echo "      OK"

# Ferdig
echo ""
echo "=============================================="
echo "  Oppsett fullfort!"
echo "=============================================="
echo ""
echo "Neste steg:"
echo ""
echo "  1. Koble SIRIUS til Pi via USB"
echo "  2. Start deling:     sudo usbip_del.sh start"
echo "  3. Sjekk status:     sudo usbip_del.sh status"
echo "  4. List USB-enheter: sudo usbip_del.sh list"
echo ""
echo "Pa Windows-PC:"
echo "  1. Aktiver test-signering: bcdedit.exe /set testsigning on (reboot)"
echo "  2. Installer usbip-win2: https://github.com/vadimgrn/usbip-win2/releases"
echo "  3. List enheter:  usbip.exe list -r <PI-IP>"
echo "  4. Koble til:     usbip.exe attach -r <PI-IP> -b <BUS-ID>"
echo "  5. Start DewesoftX - SIRIUS dukker opp som lokal enhet"
echo ""
echo "Stopp deling:   sudo usbip_del.sh stopp"
echo ""

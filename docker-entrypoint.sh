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

# =============================================================
# Host-oppsett (kernel-moduler + udev)
# Kjorer automatisk ved oppstart — erstatter setup_host.sh
# Krever: privileged=true og /sys montert
# =============================================================
# nsenter kjorer kommandoer i hostens namespace (PID 1)
# Noedvendig fordi containerens modprobe/kmod ikkje kan laste host-moduler direkte
HOST="nsenter -t 1 -m -u -i -n -p --"

echo "[Host-oppsett] Laster kernel-moduler via host namespace..."
for MODUL in usbip-core usbip-host usbmon; do
    MODUL_UNDERSCORE=$(echo "$MODUL" | tr '-' '_')
    if lsmod 2>/dev/null | grep -q "$MODUL_UNDERSCORE"; then
        echo "  $MODUL: allerede lastet"
    else
        if $HOST modprobe "$MODUL" 2>&1; then
            echo "  $MODUL: lastet OK"
        else
            echo "  $MODUL: FEILET"
        fi
    fi
done

# Gjor modulene permanente slik at de overlever reboot
$HOST sh -c '
    if [ ! -f /etc/modules-load.d/dewesoft-usbip.conf ]; then
        printf "usbip-core\nusbip-host\nusbmon\n" > /etc/modules-load.d/dewesoft-usbip.conf
        echo "  Moduler gjort permanente i /etc/modules-load.d/"
    fi
' 2>/dev/null || echo "  [ADVARSEL] Kunne ikkje skrive modules-load.d"

# Installer udev-regler paa hosten (for USB-tilgang uten root)
if [ -f /etc/udev/rules.d/99-dewesoft.rules ]; then
    $HOST sh -c '
        if [ ! -f /etc/udev/rules.d/99-dewesoft.rules ]; then
            cat > /etc/udev/rules.d/99-dewesoft.rules
            udevadm control --reload-rules
            udevadm trigger
            echo "  udev-regler installert paa hosten"
        fi
    ' < /etc/udev/rules.d/99-dewesoft.rules 2>/dev/null || true
fi

# Monter debugfs for usbmon
if [ ! -e /sys/kernel/debug/usb ]; then
    mount -t debugfs none /sys/kernel/debug 2>/dev/null || true
fi
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

# Deaktiver modular som ikkje trengst for server-modus og kan foraarsake
# feil i DewesoftX-klienten (t.d. 0x80000014 ved GetAvailableFunctionBlockTypes)
for MODUL in libref_fb_module libopcua_client_module libnative_stream_cl_module libsimulator_device_module; do
    MODULFIL=$(find /usr/local/lib -maxdepth 1 -name "${MODUL}*.module.so" 2>/dev/null | head -1)
    if [ -n "$MODULFIL" ]; then
        mv "$MODULFIL" "${MODULFIL}.disabled"
        echo "  $(basename "$MODULFIL") deaktivert"
    fi
done

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

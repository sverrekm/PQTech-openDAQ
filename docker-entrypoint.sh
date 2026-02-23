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
# SSH for DewesoftX-tilkopling
# DewesoftX krev root SSH med kjende legitimasjon for full tilgang.
# Installerer openssh-server ved oppstart viss det manglar (rask deploy
# utan full image-rebuild). ~30s ekstra oppstartstid foerste gong.
# =============================================================
if ! command -v sshd &>/dev/null; then
    echo "[SSH] Installerer openssh-server (foerste oppstart)..."
    apt-get update -qq && apt-get install -y -qq --no-install-recommends openssh-server >/dev/null 2>&1
    rm -rf /var/lib/apt/lists/*
fi

# Opprett DewesoftRT stub-skript viss dei manglar (rask deploy utan rebuild)
if [ ! -f /opt/dewesoft/scripts/platform_control.sh ]; then
    echo "[SSH] Opprettar DewesoftRT stub-skript..."
    mkdir -p /opt/dewesoft/scripts /opt/dewesoft/software/system \
             /opt/dewesoft/software/app/log /opt/dewesoft/software/temp
    cat > /opt/dewesoft/scripts/platform_control.sh << 'STUBEOF'
#!/bin/bash
# DewesoftRT stub — DewesoftX køyrer desse via SSH:
#   platform_control.sh sysinfo > /opt/dewesoft/scripts/system.xml
#   platform_control.sh info booting
#   platform_control.sh date get/set
#   platform_control.sh shutdown/reboot cleanly
SERIAL="${OPENDAQ_SERIAL:-DB19106004}"
case "$1" in
  sysinfo)
    cat <<XML
<?xml version="1.0"?>
<SystemProperties>
  <DeviceId>SIRIUSi-HS</DeviceId>
  <DeviceDisplayName>SIRIUSi-HS [${SERIAL}]</DeviceDisplayName>
  <DeviceName>SIRIUSi-HS 8xHV 8xLV</DeviceName>
  <SerialNumber>${SERIAL}</SerialNumber>
  <SystemSerialNumber>${SERIAL}</SystemSerialNumber>
  <PlatformVersion>1.0</PlatformVersion>
  <BitstreamVersion>1.0</BitstreamVersion>
  <ApplicationVersion>3.20.6</ApplicationVersion>
  <ApplicationPath>/opt/dewesoft/software/app</ApplicationPath>
  <LinuxVersion>5.15.0</LinuxVersion>
  <UbootVersion>2024.01</UbootVersion>
  <Version>3.20.6</Version>
  <HardwareVersion>1.0</HardwareVersion>
  <StructVersion>1</StructVersion>
  <DSVersion>3.20.6</DSVersion>
  <BootType>0</BootType>
  <BundleVersion>3.20.6</BundleVersion>
  <BundleBuild>0</BundleBuild>
  <AmplifiersList>
    <Amplifier>
      <SerialNumber>${SERIAL}</SerialNumber>
      <HWVersion>1.0</HWVersion>
      <FWVersion>1.0</FWVersion>
      <ModuleConnectorType>SIRIUSi-HS</ModuleConnectorType>
    </Amplifier>
  </AmplifiersList>
</SystemProperties>
XML
    ;;
  info) echo "ready" ;;
  date)
    if [ "$2" = "get" ]; then
      date "+%Y-%m-%d %H:%M:%S"
    fi
    ;;
  shutdown|reboot) echo "OK" ;;
  *) echo "OK" ;;
esac
STUBEOF
    chmod +x /opt/dewesoft/scripts/platform_control.sh
fi

echo "root:D3W3Soft30112018" | chpasswd
sed -i 's/#PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config
sed -i 's/PermitRootLogin prohibit-password/PermitRootLogin yes/' /etc/ssh/sshd_config

# Eksporter miljøvariablar til SSH-sesjonar (DewesoftX sin SSH arvar ikkje Docker env)
env | grep -E '^(OPENDAQ_|TILKOBLING|NATIVE_SIRIUS|WEB_PORT)' > /etc/environment 2>/dev/null || true

mkdir -p /run/sshd
ssh-keygen -A 2>/dev/null
/usr/sbin/sshd

# =============================================================
# DewesoftX SCP-filar
# DewesoftX lastar ned desse filane via SCP under tilkopling:
#   1. /opt/dewesoft/scripts/system.xml       — einingsidentitet (TDSRTSystemProperties)
#   2. /opt/dewesoft/software/system/system.ini — innstillingar (TSystemSettings: DisplayName, etc.)
#   3. /opt/dewesoft/software/system/system_ds.lic — lisens
# DewesoftX køyrer ogsaa SSH-kommando:
#   platform_control.sh sysinfo > /opt/dewesoft/scripts/system.xml
# Viss system.ini manglar: TSystemSettings vert nil → GetDisplayName krasjar.
# Viss system.xml har feil element-namn: TDSRTSystemProperties parsar feil.
# =============================================================
SERIAL="${OPENDAQ_SERIAL:-DB19106004}"
CONTAINER_IP="${OPENDAQ_IP:-192.168.1.161}"
mkdir -p /opt/dewesoft/scripts /opt/dewesoft/software/system

# --- system.xml: einingsidentitet ---
# Element-namn MÅ matche TDSRTSystemProperties sine property-namn i DewesoftX.
# Funne via binæranalyse av DEWEsoft.exe (DSRTSystemProperties-eininga).
cat > /opt/dewesoft/scripts/system.xml <<SYSXML
<?xml version="1.0"?>
<SystemProperties>
  <DeviceId>SIRIUSi-HS</DeviceId>
  <DeviceDisplayName>SIRIUSi-HS [${SERIAL}]</DeviceDisplayName>
  <DeviceName>SIRIUSi-HS 8xHV 8xLV</DeviceName>
  <SerialNumber>${SERIAL}</SerialNumber>
  <SystemSerialNumber>${SERIAL}</SystemSerialNumber>
  <PlatformVersion>1.0</PlatformVersion>
  <BitstreamVersion>1.0</BitstreamVersion>
  <ApplicationVersion>3.20.6</ApplicationVersion>
  <ApplicationPath>/opt/dewesoft/software/app</ApplicationPath>
  <LinuxVersion>5.15.0</LinuxVersion>
  <UbootVersion>2024.01</UbootVersion>
  <Version>3.20.6</Version>
  <HardwareVersion>1.0</HardwareVersion>
  <StructVersion>1</StructVersion>
  <DSVersion>3.20.6</DSVersion>
  <BootType>0</BootType>
  <BundleVersion>3.20.6</BundleVersion>
  <BundleBuild>0</BundleBuild>
  <UpdatePackageName></UpdatePackageName>
  <DxuBranch></DxuBranch>
  <DxuCommit></DxuCommit>
  <DxuDate></DxuDate>
  <AdjustmentDate></AdjustmentDate>
  <CalibrationDate></CalibrationDate>
  <VCXOValue>0</VCXOValue>
  <StructKey></StructKey>
  <ExtCalRef></ExtCalRef>
  <AmplifiersList>
    <Amplifier>
      <SerialNumber>${SERIAL}</SerialNumber>
      <HWVersion>1.0</HWVersion>
      <FWVersion>1.0</FWVersion>
      <ModuleConnectorType>SIRIUSi-HS</ModuleConnectorType>
    </Amplifier>
  </AmplifiersList>
</SystemProperties>
SYSXML

# --- system.ini: TSystemSettings (INI-format) ---
# Seksjon [Settings] med DisplayName, DisplayLocation, DeviceBehaviour.
# KRITISK: Utan denne fila vert TSystemSettings nil → GetDisplayName krasjar
# med "Access violation Read of address 0000000000000008".
cat > /opt/dewesoft/software/system/system.ini <<SYSINI
[Settings]
DisplayName=SIRIUSi-HS [${SERIAL}]
DisplayLocation=
DeviceBehaviour=DewesoftDAQ
GroupLogicalID=
SYSINI

# --- system_ds.lic: lisensfil ---
# DewesoftX lastar ned denne (IKKJE license.xml) frå /opt/dewesoft/software/system/.
cat > /opt/dewesoft/software/system/system_ds.lic <<LICEOF
<?xml version="1.0"?>
<License>
  <SerialNumber>${SERIAL}</SerialNumber>
  <Type>RT</Type>
</License>
LICEOF

echo "  SSH: root-tilgang for DewesoftX aktivert"
echo "  system.xml + system.ini + system_ds.lic: serienummer=${SERIAL}"
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

# =============================================================
# Nettverksopprydding: IKKJE MOGLEG paa host-nettverk
# Docker-bridges (172.x.x.x) er synlege for openDAQ mDNS, men vi
# kan ikkje fjerne IP-ane (bryt Gitea/Portainer gateway-routing).
# Loysinga er Python zeroconf i opendaq_bro.py som registrerer
# mDNS-tenester med BERRE eth0-IP (ikkje Docker-bridge IP-ar).
# =============================================================

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
#
# IP-prioritet: default route fyrst (same interface som mDNS svarar fraa),
# deretter eth0/lan0/wlan0, til slutt hostname -I.
# VIKTIG: Default route prioriterast fordi mDNS-multicast svarar fraa same
# interface — DewesoftX brukar denne IP-en for oppdaging. Viss OPC-UA
# endpoint-URL har ein annan IP, kan DewesoftX avvise tilkoplinga.
if [ -z "${OPENDAQ_IP}" ]; then
    # Metode 1: Default route — same interface som mDNS svarar fraa.
    DEFAULT_IFACE=$(ip route show default 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="dev") print $(i+1)}' | head -1)
    if [ -n "$DEFAULT_IFACE" ]; then
        DEFAULT_IP=$(ip -4 addr show "$DEFAULT_IFACE" 2>/dev/null | grep -oP 'inet \K[0-9.]+' | head -1)
        if [ -n "$DEFAULT_IP" ]; then
            export OPENDAQ_IP="$DEFAULT_IP"
            echo "  IP fraa default route ($DEFAULT_IFACE): $OPENDAQ_IP"
        fi
    fi
fi
if [ -z "${OPENDAQ_IP}" ]; then
    # Metode 2 (fallback): Proev eth0/lan0/wlan0 direkte
    for IFACE in eth0 end0 lan0 wlan0; do
        ETH_IP=$(ip -4 addr show "$IFACE" 2>/dev/null | grep -oP 'inet \K[0-9.]+' | head -1)
        if [ -n "$ETH_IP" ]; then
            export OPENDAQ_IP="$ETH_IP"
            echo "  IP fraa $IFACE: $OPENDAQ_IP"
            break
        fi
    done
fi
# Fallback til fyrste ikkje-loopback IP
if [ -z "${OPENDAQ_IP}" ]; then
    export OPENDAQ_IP="$(hostname -I | awk '{print $1}')"
    echo "  IP fraa hostname -I: $OPENDAQ_IP"
fi
if [ -n "$OPENDAQ_IP" ]; then
    CURRENT_HOST=$(hostname)
    CURRENT_RESOLVE=$(getent hosts "$CURRENT_HOST" 2>/dev/null | awk '{print $1}')
    if [ -n "$CURRENT_RESOLVE" ] && [ "$CURRENT_RESOLVE" != "$OPENDAQ_IP" ]; then
        # Hostname resolver til feil IP (127.0.x.x eller WiFi) — erstatt med LAN-IP
        # sed -i feiler paa Docker bind-mount, bruk cp+cat i staden
        sed "s/[0-9.]*[[:space:]]*${CURRENT_HOST}/${OPENDAQ_IP} ${CURRENT_HOST}/" /etc/hosts > /tmp/hosts.fixed
        cat /tmp/hosts.fixed > /etc/hosts
        rm -f /tmp/hosts.fixed
        echo "  OPC-UA hostname: ${CURRENT_HOST} ${CURRENT_RESOLVE} -> ${OPENDAQ_IP}"
    elif [ -z "$CURRENT_RESOLVE" ]; then
        # Hostname finst ikkje i /etc/hosts — legg til
        echo "${OPENDAQ_IP} ${CURRENT_HOST}" >> /etc/hosts
        echo "  OPC-UA hostname lagt til: ${CURRENT_HOST} -> ${OPENDAQ_IP}"
    else
        echo "  OPC-UA hostname OK: ${CURRENT_HOST} -> ${OPENDAQ_IP}"
    fi
fi
# Detekter MAC-adresse frå same grensesnitt som IP (for DeviceInfo)
if [ -z "${OPENDAQ_MAC}" ]; then
    for IFACE in eth0 end0 lan0 wlan0; do
        IF_MAC=$(cat /sys/class/net/"$IFACE"/address 2>/dev/null)
        if [ -n "$IF_MAC" ] && [ "$IF_MAC" != "00:00:00:00:00:00" ]; then
            export OPENDAQ_MAC="$IF_MAC"
            echo "  MAC fraa $IFACE: $OPENDAQ_MAC"
            break
        fi
    done
fi
if [ -z "${OPENDAQ_MAC}" ]; then
    echo "  MAC: ikkje detektert (brukar fallback)"
fi

# Serienummer: bruk OPENDAQ_SERIAL frå environment (docker-compose.yml)
if [ -n "${OPENDAQ_SERIAL}" ]; then
    echo "  Serienummer: $OPENDAQ_SERIAL"
fi

# OPC-UA endpoint URL:
# open62541 brukar gethostname() for å lage opc.tcp://hostname:port URL-ar.
# DewesoftX på Windows kan ikkje resolve container-hostname (t.d. "IOTmanager")
# via DNS → tilkopling feilar stille (ingen error i log, berre "Disconnected").
# Løysing: Sett hostname til IP-adressa slik at endpoint-URL vert
# opc.tcp://192.168.1.160:4840/ som DewesoftX kan nå direkte.
# mDNS-oppdaging er handtert av add_discovery_server("mdns") i opendaq_bro.py.
ORIG_HOST=$(hostname)
hostname "$OPENDAQ_IP" 2>/dev/null || true
# Oppdater /etc/hosts for det nye hostnavn-et
echo "$OPENDAQ_IP $OPENDAQ_IP" >> /etc/hosts 2>/dev/null || true
echo "  OPC-UA endpoint: hostname=$OPENDAQ_IP (var: $ORIG_HOST)"
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

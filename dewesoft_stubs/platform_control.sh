#!/bin/bash
# Stub: DewesoftRT platform_control.sh
# DewesoftX koeyrer desse kommandoane over SSH for aa identifisere eininga.
# DewesoftX køyrer: platform_control.sh sysinfo > /opt/dewesoft/scripts/system.xml
# Output vert pipe-a til system.xml — difor skriv til stdout (ikkje til fil).

SERIAL="${OPENDAQ_SERIAL:-DB19106004}"

case "$1" in
  sysinfo)
    # Output til stdout — DewesoftX redirectar til system.xml
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
  info)
    echo "ready"
    ;;
  date)
    if [ "$2" = "get" ]; then
      date "+%Y-%m-%d %H:%M:%S"
    fi
    ;;
  shutdown|reboot)
    echo "OK"
    ;;
  *)
    echo "OK"
    ;;
esac

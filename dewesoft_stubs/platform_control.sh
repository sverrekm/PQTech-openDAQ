#!/bin/bash
# Stub: DewesoftRT platform_control.sh
# DewesoftX koeyrer desse kommandoane over SSH for aa identifisere eininga.
# Denne stubben returnerer forventa svar slik at DewesoftX godtek tilkoplinga.

case "$1" in
  sysinfo)
    cat > /opt/dewesoft/scripts/system.xml <<XML
<?xml version="1.0"?>
<System>
  <SerialNumber>${OPENDAQ_SERIAL:-DB19106004}</SerialNumber>
  <Model>SIRIUSi-HS 8xHV 8xLV</Model>
  <Manufacturer>Dewesoft</Manufacturer>
  <HardwareVersion>1.0</HardwareVersion>
  <FirmwareVersion>1.0.0-opendaq</FirmwareVersion>
  <Platform>RPi5-Docker</Platform>
  <SoftwareVersion>3.20.6</SoftwareVersion>
</System>
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
  *)
    echo "OK"
    ;;
esac

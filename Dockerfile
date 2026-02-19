# =============================================================
# openDAQ Server for Dewesoft SIRIUS - Raspberry Pi 5
# =============================================================
# Multi-stage build:
#   Stage 1: Kompilerer openDAQ fra GitHub (OPC-UA, streaming, Python)
#   Stage 2: Slank runtime med Flask web-grensesnitt
#
# Bygg paa Pi:
#   docker build -t opendaq-sirius .
#
# Bygg fra Windows (kryss-kompilering):
#   docker buildx build --platform linux/arm64 -t opendaq-sirius .
#
# Med faerre parallelle jobber (Pi med lite RAM):
#   docker build --build-arg PARALLELLE_JOBBER=1 -t opendaq-sirius .
# =============================================================

# ---- Stage 1: Bygg openDAQ fra kildekode ----
FROM debian:bookworm AS builder

ARG DEBIAN_FRONTEND=noninteractive
ARG PARALLELLE_JOBBER=2
ARG OPENDAQ_BRANCH=v3.30.0

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    cmake \
    ninja-build \
    mono-complete \
    python3 \
    python3-dev \
    python3-pip \
    python3-numpy \
    lld \
    pkg-config \
    libx11-dev \
    libxi-dev \
    libxcursor-dev \
    libxrandr-dev \
    libgl1-mesa-dev \
    libudev-dev \
    libfreetype6-dev \
    libusb-1.0-0-dev \
    libssl-dev \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

SHELL ["/bin/bash", "-c"]

WORKDIR /src
RUN git clone --depth 1 --branch ${OPENDAQ_BRANCH} \
    https://github.com/openDAQ/openDAQ.git .

# ---- Patch: getDomain() skal aldri returnere nil ----
# DewesoftX krasjar med "External exception E06D7363" når
# getDomain() returnerer nullptr. Instance-wrapperen vidaresender
# ikkje getDomain() til rot-eininga, so den returnerer nil sjølv
# om referanse-eininga har ein gyldig DeviceDomain frå initClock().
# Fix: Returner ein standard DeviceDomain når ingen er sett.
RUN python3 << 'PYEOF'
import re, sys

path = "/src/core/opendaq/device/include/opendaq/device_impl.h"
with open(path, "r") as f:
    content = f.read()

# 1) Legg til naudsynte includes for DeviceDomain-fabrikken
anchor = "#include <opendaq/device_info_internal_ptr.h>"
extra_includes = """
#include <opendaq/device_domain_factory.h>
#include <coreobjects/unit_factory.h>
#include <coretypes/ratio_factory.h>"""
if anchor in content:
    content = content.replace(anchor, anchor + extra_includes, 1)
    print(f"OK: La til DeviceDomain-includes etter {anchor}")
else:
    print(f"ADVARSEL: Fann ikkje anchor-include, prøver alternativ", file=sys.stderr)
    # Fallback: legg til etter fyrste opendaq-include
    content = content.replace(
        "#include <opendaq/device_ptr.h>",
        "#include <opendaq/device_ptr.h>" + extra_includes, 1)

# 2) Patch getDomain(): returner standard-domain viss ikkje sett
old_line = "    *deviceDomain = this->deviceDomain.addRefAndReturn();"
new_block = """    if (this->deviceDomain.assigned())
    {
        *deviceDomain = this->deviceDomain.addRefAndReturn();
    }
    else
    {
        // Fallback: standard DeviceDomain for å unngaa nil-krasj i DewesoftX
        auto fallbackDomain = DeviceDomain(
            Ratio(1, 1000000),
            String(""),
            UnitBuilder().setName("second").setSymbol("s").setQuantity("time").build());
        *deviceDomain = fallbackDomain.addRefAndReturn();
    }"""

if old_line in content:
    content = content.replace(old_line, new_block, 1)
    print("OK: Patcha getDomain() med fallback DeviceDomain")
else:
    print("FEIL: Fann ikkje getDomain-linja å patche!", file=sys.stderr)
    sys.exit(1)

with open(path, "w") as f:
    f.write(content)
print("Patch 1/4 komplett: getDomain")

# ---- Patch 2: OPC-UA nil string → tom streng ----
# VariantConverter<IString>::ToVariant krasjar når input er nullptr.
# VariantConverter<IBaseObject>::ToVariant returnerer tom variant for nil,
# som DewesoftX les som null string → krasj i InitStringProperty.
# Fix: Legg til nullptr-sjekk i IString-konverteren.

path2 = "/src/shared/libraries/opcuatms/opcuatms/src/converters/core_types_converter.cpp"
with open(path2, "r") as f:
    content2 = f.read()

# Patch IString ToVariant: legg til nullptr-sjekk
old_str_conv = """VariantConverter<IString>::ToVariant(const StringPtr& object, const UA_DataType* targetType, const ContextPtr& /*context*/)
{
    auto variant = OpcUaVariant();"""

# Prøv med ulike whitespace-variantar
found = False
for ws_variant in [old_str_conv,
    old_str_conv.replace("const ContextPtr& /*context*/", "const ContextPtr&  /*context*/"),
    ]:
    if ws_variant in content2:
        new_str_conv = ws_variant.replace(
            "auto variant = OpcUaVariant();",
            """auto variant = OpcUaVariant();

    // Patch: nil string → tom streng for å unngaa DewesoftX-krasj
    if (!object.assigned())
    {
        auto emptyStr = String("");
        if (targetType == nullptr || targetType == &UA_TYPES[UA_TYPES_STRING])
            variant.setScalar(*StructConverter<IString, UA_String>::ToTmsType(emptyStr));
        else if (targetType == &UA_TYPES[UA_TYPES_LOCALIZEDTEXT])
            variant.setScalar(*StructConverter<IString, UA_LocalizedText>::ToTmsType(emptyStr));
        else if (targetType == &UA_TYPES[UA_TYPES_QUALIFIEDNAME])
            variant.setScalar(*StructConverter<IString, UA_QualifiedName>::ToTmsType(emptyStr));
        return variant;
    }""")
        content2 = content2.replace(ws_variant, new_str_conv, 1)
        found = True
        break

if not found:
    # Generisk fallback: søk etter mønsteret med regex
    import re
    pattern = r'(VariantConverter<IString>::ToVariant\(const StringPtr& object.*?\{)\s*\n(\s*auto variant = OpcUaVariant\(\);)'
    match = re.search(pattern, content2, re.DOTALL)
    if match:
        indent = "    "
        nil_check = f"""\n{indent}// Patch: nil string → tom streng
{indent}if (!object.assigned())
{indent}{{
{indent}    auto emptyStr = String("");
{indent}    if (targetType == nullptr || targetType == &UA_TYPES[UA_TYPES_STRING])
{indent}        variant.setScalar(*StructConverter<IString, UA_String>::ToTmsType(emptyStr));
{indent}    else if (targetType == &UA_TYPES[UA_TYPES_LOCALIZEDTEXT])
{indent}        variant.setScalar(*StructConverter<IString, UA_LocalizedText>::ToTmsType(emptyStr));
{indent}    else if (targetType == &UA_TYPES[UA_TYPES_QUALIFIEDNAME])
{indent}        variant.setScalar(*StructConverter<IString, UA_QualifiedName>::ToTmsType(emptyStr));
{indent}    return variant;
{indent}}}"""
        replacement = match.group(1) + "\n" + match.group(2) + nil_check
        content2 = content2[:match.start()] + replacement + content2[match.end():]
        found = True

if not found:
    print("FEIL: Fann ikkje IString ToVariant å patche!", file=sys.stderr)
    sys.exit(1)

# Patch OGSAA IBaseObject ToVariant: nil → tom streng for string-kontekst
old_base = """if (!object.assigned())
        return {};"""
new_base = """if (!object.assigned())
    {
        // Patch: returner tom streng i staden for null variant
        auto variant = OpcUaVariant();
        UA_String empty = UA_STRING_ALLOC("");
        variant.setScalar(empty);
        UA_String_clear(&empty);
        return variant;
    }"""
if old_base in content2:
    content2 = content2.replace(old_base, new_base, 1)
    print("OK: Patcha IBaseObject nil → tom streng")
else:
    print("INFO: IBaseObject nil-sjekk ikkje funnen (kanskje anna format)")

with open(path2, "w") as f:
    f.write(content2)
print("Patch 2/4 komplett: nil string")
PYEOF

# ---- Patch 3: RefDevice DeviceInfo → SIRIUS-verdiar ----
# DewesoftX viser tomme felt (Name, Model, Manufacturer, MAC, Serial)
# fordi referanse-eininga brukar standardverdiar ("openDAQ", "Reference device").
# DeviceInfo er frosen (read-only) etter build — kan ikkje endrast frå Python.
# Fix: Patch C++-kjelda til å bruke SIRIUS-verdiar som standard.
RUN python3 << 'PYEOF'
import sys

path = "/src/examples/modules/ref_device_module/src/ref_device_impl.cpp"
with open(path, "r") as f:
    content = f.read()

# Patch CreateDeviceInfo(): endre standardverdiar
replacements = [
    ('devInfo.setManufacturer("openDAQ")',
     'devInfo.setManufacturer("Dewesoft")'),
    ('devInfo.setModel("Reference device")',
     'devInfo.setModel("SIRIUSi-HS 8xHV 8xLV")'),
]

patched = 0
for old, new in replacements:
    if old in content:
        content = content.replace(old, new, 1)
        patched += 1
        print(f"OK: {old} -> {new}")
    else:
        print(f"ADVARSEL: Fann ikkje '{old}'", file=sys.stderr)

# Legg til MAC-adresse og platforminfo etter serialNumber-linja
serial_line = 'devInfo.setSerialNumber(serialNumber.assigned()'
if serial_line in content:
    # Finn slutten av serialNumber-linja (neste semikolon + newline)
    idx = content.index(serial_line)
    end_idx = content.index(';', idx) + 1
    extra = """
    // MAC-adresse: les frå OPENDAQ_MAC miljøvariabel (sett i entrypoint)
    {
        const char* envMac = std::getenv("OPENDAQ_MAC");
        devInfo.setMacAddress(envMac && envMac[0] ? envMac : "00:00:00:00:00:00");
    }
    // Serienummer: bruk OPENDAQ_SERIAL viss sett (overskriv default DevSerN)
    {
        const char* envSerial = std::getenv("OPENDAQ_SERIAL");
        if (envSerial && envSerial[0])
            devInfo.setSerialNumber(envSerial);
    }
    devInfo.setPlatform("RPi5-Docker");
    devInfo.setSoftwareRevision("1.0.0-opendaq3.30");
    devInfo.setHardwareRevision("");
    devInfo.setDeviceManual("");
    devInfo.setDeviceClass("");
    devInfo.setProductCode("");
    devInfo.setDeviceRevision("");
    devInfo.setManufacturerUri("");
    devInfo.setProductInstanceUri("");
    devInfo.setAssetId("");
    devInfo.setParentMacAddress("");
    devInfo.setSystemType("");
    devInfo.setSystemUuid("");
    devInfo.setLocation("");
    devInfo.setUserName("");

    // DeviceType: DewesoftX krasjar med 'Interface object is nil' i
    // TOpenDaqDeviceInfo.UpdateInfo viss DeviceType er nullptr.
    // DeviceType er eit objekt (ikkje streng), so nil-string-patchen hjelper ikkje.
    devInfo.setDeviceType(DeviceType("dewesoft_sirius", "SIRIUSi-HS 8xHV 8xLV", "Dewesoft SIRIUSi-HS Data Acquisition", "daq.opcua", nullptr));"""
    content = content[:end_idx] + extra + content[end_idx:]
    patched += 1
    print("OK: La til MAC, platform, softwareRevision, DeviceType + alle string-felt")
else:
    print("ADVARSEL: Fann ikkje serialNumber-linje", file=sys.stderr)

# Sikre at DeviceType-header er inkludert
if '#include' in content:
    # Legg til device_type.h viss ikkje allereie inkludert
    if 'device_type' not in content.lower():
        # Finn siste #include-linje og legg til etter den
        import re
        last_include = None
        for m in re.finditer(r'^#include\s+.*$', content, re.MULTILINE):
            last_include = m
        if last_include:
            insert_pos = last_include.end()
            content = content[:insert_pos] + '\n#include <cstdlib>\n#include <opendaq/device_type_factory.h>' + content[insert_pos:]
            patched += 1
            print("OK: La til #include <opendaq/device_type_factory.h>")

if patched < 2:
    print(f"FEIL: Berre {patched} av minst 3 patchar lukkast!", file=sys.stderr)
    sys.exit(1)

with open(path, "w") as f:
    f.write(content)
print(f"Patch 3/4 komplett: DeviceInfo ({patched} endringar)")
PYEOF

# ---- Patch 4: createOptionalNode() — tillat MacAddress og SerialNumber ----
# OPC-UA DAQ Device type definerer MacAddress og SerialNumber som valfrie nodar.
# TmsServerComponent::createOptionalNode() returnerer false som standard,
# og TmsServerDevice sin kviteliste inkluderer ikkje desse.
# Utan denne patchen vert nodane aldri oppretta → DewesoftX viser "Not provided".
RUN python3 << 'PYEOF'
import sys

path = "/src/shared/libraries/opcuatms/opcuatms_server/src/objects/tms_server_device.cpp"
with open(path, "r") as f:
    content = f.read()

# Legg til MacAddress og SerialNumber i createOptionalNode-kvitelista
anchor = '    if (name == "ProductInstanceUri" && object.getInfo().getProductInstanceUri() != "")\n        return true;'
extra = """
    if (name == "MacAddress" && object.getInfo().getMacAddress() != "")
        return true;
    if (name == "SerialNumber" && object.getInfo().getSerialNumber() != "")
        return true;
    if (name == "Platform" && object.getInfo().getPlatform() != "")
        return true;
    if (name == "HardwareRevision")
        return true;
    if (name == "SoftwareRevision")
        return true;"""

if anchor in content:
    content = content.replace(anchor, anchor + extra, 1)
    print("OK: La til MacAddress, SerialNumber, Platform, HW/SW-revision i createOptionalNode")
else:
    print("FEIL: Fann ikkje ProductInstanceUri-linja i createOptionalNode!", file=sys.stderr)
    sys.exit(1)

with open(path, "w") as f:
    f.write(content)
print("Patch 4/4 komplett: createOptionalNode")
PYEOF

RUN cmake -S /src -B /src/build -G Ninja \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
    -DCMAKE_CXX_FLAGS="-Wno-error=stringop-overflow" \
    -DCMAKE_INSTALL_PREFIX=/opt/opendaq \
    -DOPENDAQ_ENABLE_OPCUA=ON \
    -DOPENDAQ_ENABLE_NATIVE_STREAMING=ON \
    -DOPENDAQ_ENABLE_WEBSOCKET_STREAMING=ON \
    -DOPENDAQ_RELEASE_WARNINGS_AS_ERRORS=OFF \
    -DDAQMODULES_REF_DEVICE_MODULE=ON \
    -DDAQMODULES_OPENDAQ_CLIENT_MODULE=ON \
    -DDAQMODULES_OPENDAQ_SERVER_MODULE=ON \
    -DDAQMODULES_REF_FB_MODULE=ON \
    -DOPENDAQ_GENERATE_PYTHON_BINDINGS=ON \
    -DOPENDAQ_ALWAYS_FETCH_DEPENDENCIES=ON \
    -DOPENDAQ_ENABLE_TESTS=OFF \
    -DOPENDAQ_ENABLE_TEST_UTILS=OFF \
    -DDAQMODULES_AUDIO_DEVICE_MODULE=OFF \
    || { echo "=== CMAKE CONFIGURE FEILET ==="; \
         echo "=== CMakeError.log ==="; \
         cat /src/build/CMakeFiles/CMakeError.log 2>/dev/null; \
         echo "=== CMakeOutput.log (siste 50 linjer) ==="; \
         tail -50 /src/build/CMakeFiles/CMakeOutput.log 2>/dev/null; \
         exit 1; }

RUN cmake --build /src/build -j ${PARALLELLE_JOBBER} \
    || { echo "=== CMAKE BUILD FEILET ==="; exit 1; }

RUN mkdir -p /opt/opendaq/lib /opt/opendaq/python && \
    find /src/build/bin -name "*.so*" -exec cp -P {} /opt/opendaq/lib/ \; && \
    SO_COUNT=$(find /opt/opendaq/lib -name "*.so*" | wc -l) && \
    echo "Fant $SO_COUNT .so-filer" && \
    if [ "$SO_COUNT" -eq 0 ]; then \
        echo "=== FEIL: Ingen .so-filer bygget ==="; \
        ls -la /src/build/bin/ 2>/dev/null || echo "(build/bin finnes ikke)"; \
        exit 1; \
    fi && \
    find /src/build/bin -name "opendaq*.so" -exec cp {} /opt/opendaq/python/ \; && \
    cp -r /src/bindings/python/package/opendaq/* /opt/opendaq/python/ && \
    echo "Python-pakke kopiert:" && \
    ls -la /opt/opendaq/python/


# ---- Stage 2: Bygg React frontend ----
FROM node:20-slim AS frontend-builder
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build


# ---- Stage 3: Runtime ----
FROM python:3.11-slim

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    libusb-1.0-0 \
    libusb-1.0-0-dev \
    libudev1 \
    libstdc++6 \
    libxrandr2 \
    libxcursor1 \
    libxi6 \
    libfreetype6 \
    usbutils \
    usbip \
    procps \
    git \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Bygg uhubctl fraa kildekode (for USB port power-cycling)
RUN git clone --depth 1 https://github.com/mvp/uhubctl /tmp/uhubctl \
    && cd /tmp/uhubctl && make && make install \
    && rm -rf /tmp/uhubctl \
    && apt-mark manual libusb-1.0-0 \
    && apt-get purge -y --auto-remove git build-essential libusb-1.0-0-dev \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir numpy flask pyusb

COPY --from=builder /opt/opendaq/lib/ /usr/local/lib/
COPY --from=builder /opt/opendaq/python/ /usr/local/lib/python3.11/site-packages/opendaq/

RUN ldconfig

RUN mkdir -p /app

WORKDIR /app

COPY opendaq_server.py .
COPY web_ui.py .
COPY usbip_manager.py .
COPY sirius_usb_probe.py .
COPY sirius_protokoll.py .
COPY sirius_dekoder.py .
COPY sirius_adc_leser.py .
COPY sirius_sniffer.py .
COPY sirius_protokoll_impl.py .
COPY sirius_driver.py .
COPY sirius_init_sekvens.py .
COPY sirius_server.py .
COPY opendaq_bro.py .
COPY kanal_konfig.py .
COPY docker-entrypoint.sh .
RUN chmod +x docker-entrypoint.sh

# React frontend (bygga i stage 2)
COPY --from=frontend-builder /frontend/dist /app/frontend/dist

# udev-regler for Dewesoft USB-enheter (tilgang uten root)
COPY 99-dewesoft.rules /etc/udev/rules.d/

ENV OPENDAQ_MODULE_PATH=/usr/local/lib
ENV LD_LIBRARY_PATH=/usr/local/lib
ENV PYTHONUNBUFFERED=1
ENV WEB_PORT=8080
ENV TILKOBLING=""

HEALTHCHECK --interval=60s --timeout=10s --retries=3 \
    CMD pgrep -f "opendaq_server.py\|sirius_server.py" > /dev/null || exit 1

ENTRYPOINT ["./docker-entrypoint.sh"]

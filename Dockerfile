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
ARG OPENDAQ_BRANCH=main

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

RUN cmake -S /src -B /src/build -G Ninja \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
    -DCMAKE_INSTALL_PREFIX=/opt/opendaq \
    -DOPENDAQ_ENABLE_OPCUA=ON \
    -DOPENDAQ_ENABLE_NATIVE_STREAMING=ON \
    -DOPENDAQ_ENABLE_WEBSOCKET_STREAMING=ON \
    -DDAQMODULES_REF_DEVICE_MODULE=ON \
    -DDAQMODULES_SIMULATOR_DEVICE_MODULE=ON \
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


# ---- Stage 2: Runtime ----
FROM python:3.11-slim

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    libusb-1.0-0 \
    libudev1 \
    libstdc++6 \
    libxrandr2 \
    libxcursor1 \
    libxi6 \
    libfreetype6 \
    usbutils \
    usbip \
    procps \
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
COPY sirius_server.py .
COPY opendaq_bro.py .
COPY kanal_konfig.py .
COPY docker-entrypoint.sh .
RUN chmod +x docker-entrypoint.sh

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

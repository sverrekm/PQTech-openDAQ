# =============================================================
# Dewesoft SIRIUS Målesystem - Docker for Raspberry Pi
# =============================================================
# Multi-stage build:
#   Stage 1 (builder): Kloner og kompilerer openDAQ fra GitHub
#   Stage 2 (runtime): Slank Python-image med kun det nødvendige
#
# Bygg på Raspberry Pi:
#   docker build -t dewesoft-maaling .
#
# Med færre parallelle jobber (Pi med < 4GB RAM):
#   docker build --build-arg PARALLELLE_JOBBER=1 -t dewesoft-maaling .
# =============================================================

# ---- Stage 1: Bygg openDAQ fra kildekode ----
FROM ubuntu:22.04 AS builder

ARG DEBIAN_FRONTEND=noninteractive
ARG PARALLELLE_JOBBER=2
ARG OPENDAQ_BRANCH=main

# Installer build-avhengigheter
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    cmake \
    ninja-build \
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
    libgl-dev \
    libudev-dev \
    libfreetype6-dev \
    libusb-1.0-0-dev \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Klon openDAQ fra GitHub
WORKDIR /src
RUN git clone --depth 1 --branch ${OPENDAQ_BRANCH} \
    https://github.com/openDAQ/openDAQ.git .

# Konfigurer CMake:
#   - Protokoller: OPC-UA, native streaming, websocket
#   - Moduler: ref-device, simulator, client, server, csv-recorder
#   - Python-bindings aktivert
#   - Tester deaktivert (spar tid/plass)
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
    -DDAQMODULES_BASIC_CSV_RECORDER_MODULE=ON \
    -DOPENDAQ_GENERATE_PYTHON_BINDINGS=ON \
    -DOPENDAQ_ALWAYS_FETCH_DEPENDENCIES=ON \
    -DOPENDAQ_ENABLE_TESTS=OFF \
    -DOPENDAQ_ENABLE_TEST_UTILS=OFF \
    -DDAQMODULES_AUDIO_DEVICE_MODULE=OFF

# Bygg (begrenset parallellitet for Pi med lite RAM)
RUN cmake --build /src/build -j ${PARALLELLE_JOBBER}

# Organiser bygde filer for kopiering til runtime-stage
RUN mkdir -p /opt/opendaq/lib /opt/opendaq/python && \
    # Kopier delte biblioteker (.so-filer)
    find /src/build/bin -name "*.so*" -exec cp -P {} /opt/opendaq/lib/ \; && \
    # Kopier Python-binding (.so for Python)
    find /src/build/bin -name "opendaq*.so" -exec cp {} /opt/opendaq/python/ \; && \
    # Kopier Python-pakke (wrapper-kode)
    cp -r /src/bindings/python/package/opendaq/* /opt/opendaq/python/ 2>/dev/null || true


# ---- Stage 2: Runtime (slank) ----
FROM python:3.11-slim

ARG DEBIAN_FRONTEND=noninteractive

# Installer runtime-avhengigheter (kun det nødvendige)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libusb-1.0-0 \
    libudev1 \
    libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

# Installer Python-pakker
RUN pip install --no-cache-dir numpy

# Kopier bygde openDAQ-binærer fra builder
COPY --from=builder /opt/opendaq/lib/ /usr/local/lib/
COPY --from=builder /opt/opendaq/python/ /usr/local/lib/python3.11/site-packages/opendaq/

# Oppdater delt bibliotek-cache
RUN ldconfig

# Opprett mapper
RUN mkdir -p /app /data/maalinger /data/konfig

WORKDIR /app

# Kopier applikasjonsfiler
COPY dewesoft_maaling.py .
COPY konfig_strom_spenning.json /data/konfig/
COPY konfig_sirius_opcua.json /data/konfig/
COPY docker-entrypoint.sh .
RUN chmod +x docker-entrypoint.sh

# Miljøvariabler
ENV OPENDAQ_MODULE_PATH=/usr/local/lib
ENV LD_LIBRARY_PATH=/usr/local/lib
ENV PYTHONUNBUFFERED=1
ENV KONFIG_FIL=/data/konfig/konfig_strom_spenning.json
ENV MAALE_INTERVALL=60
ENV MAALE_VARIGHET=5
ENV MAALE_PREFIKS=maaling
ENV TILKOBLING=""

# Helsesjekk — verifiser at nylig måling finnes
HEALTHCHECK --interval=120s --timeout=10s --retries=3 \
    CMD find /data/maalinger -name "*.csv" -mmin -5 | grep -q . || exit 1

ENTRYPOINT ["./docker-entrypoint.sh"]

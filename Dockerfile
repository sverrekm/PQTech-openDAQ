# =============================================================
# USB-over-IP Server for Dewesoft SIRIUS - Raspberry Pi 5
# =============================================================
# Deler SIRIUS USB-enhet over nettverket slik at DewesoftX
# pa en Windows-PC kan koble til som om den var lokal.
#
# Bygg:  docker build -t usbip-sirius .
# Kjor:  docker compose up -d
# =============================================================

FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    usbip \
    hwdata \
    usbutils \
    kmod \
    iproute2 \
    procps \
    python3 \
    python3-pip \
    && pip3 install --no-cache-dir --break-system-packages flask \
    && rm -rf /var/lib/apt/lists/*

COPY docker-entrypoint.sh /entrypoint.sh
COPY web_ui.py /app/web_ui.py
RUN chmod +x /entrypoint.sh

ENV WEB_PORT=8080

ENTRYPOINT ["/entrypoint.sh"]

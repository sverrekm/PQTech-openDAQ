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
    && rm -rf /var/lib/apt/lists/*

COPY docker-entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]

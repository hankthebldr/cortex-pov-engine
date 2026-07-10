#!/usr/bin/env bash
# ===========================================================================
# CortexSim Tier-C sinkhole entrypoint
# ---------------------------------------------------------------------------
# Resolves this container's own IP on the internal bridge, rewrites the
# dnsmasq wildcard so every DNS answer points back here, starts dnsmasq, then
# launches the stdlib HTTP/HTTPS catch-all in the foreground.
# ===========================================================================
set -euo pipefail

CONF=/etc/dnsmasq.d/cortexsim-sinkhole.conf

# Discover this container's primary IPv4 on the internal bridge.
SINKHOLE_IP="$(hostname -i 2>/dev/null | awk '{print $1}')"
if [ -z "${SINKHOLE_IP:-}" ]; then
    # Fallback: parse from `ip` if hostname -i is empty.
    SINKHOLE_IP="$(python3 -c 'import socket; print(socket.gethostbyname(socket.gethostname()))' 2>/dev/null || echo 127.0.0.1)"
fi
echo "[sinkhole] resolved self IP: $SINKHOLE_IP" >&2

# Bake the IP into the wildcard address record.
sed -i "s/SINKHOLE_IP/${SINKHOLE_IP}/g" "$CONF"

# Start dnsmasq in the background (no daemonize so we can see it crash).
dnsmasq --keep-in-foreground --conf-file="$CONF" &
DNSMASQ_PID=$!
echo "[sinkhole] dnsmasq started pid=$DNSMASQ_PID (wildcard -> $SINKHOLE_IP)" >&2

# Foreground: the HTTP/HTTPS catch-all. If it exits, the container exits.
exec python3 /opt/sinkhole/catch_all_http.py

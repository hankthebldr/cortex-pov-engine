# syntax=docker/dockerfile:1
# ===========================================================================
# Tier-C network sinkhole  —  cortexsim-tier-c-sinkhole
# ---------------------------------------------------------------------------
# A tiny DNS + HTTP/HTTPS catch-all so C2/exfil-shaped steps in a push bundle
# RESOLVE and CONNECT to this sidecar instead of egressing. The runner
# container has no real outbound network (internal bridge, no gateway); its
# /etc/resolv.conf points here, and every A query answers with this sinkhole's
# address. The HTTP listener returns a deterministic 200 and logs every
# request so the network *shape* is observable.
#
# dnsmasq answers DNS (any name -> sinkhole IP). A small stdlib Python server
# catches HTTP (:80) and HTTPS (:443, self-signed). No third-party Python deps.
# ===========================================================================
FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        dnsmasq python3 openssl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY dnsmasq.conf /etc/dnsmasq.d/cortexsim-sinkhole.conf
COPY catch_all_http.py /opt/sinkhole/catch_all_http.py
COPY sinkhole-entrypoint.sh /usr/local/bin/sinkhole-entrypoint
RUN chmod 0755 /usr/local/bin/sinkhole-entrypoint /opt/sinkhole/catch_all_http.py

# Self-signed cert for the HTTPS catch-all (generated at build, inert).
RUN openssl req -x509 -newkey rsa:2048 -nodes \
        -keyout /opt/sinkhole/sinkhole.key \
        -out /opt/sinkhole/sinkhole.crt \
        -days 3650 -subj "/CN=cortexsim-sinkhole.test" >/dev/null 2>&1

EXPOSE 53/udp 53/tcp 80/tcp 443/tcp
ENTRYPOINT ["/usr/local/bin/sinkhole-entrypoint"]

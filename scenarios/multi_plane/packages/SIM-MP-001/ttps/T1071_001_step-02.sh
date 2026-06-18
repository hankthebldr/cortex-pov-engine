#!/usr/bin/env bash
# T1071.001 — Endpoint: HTTP beacon callback to known-bad test domain
# Derived verbatim from SIM-MP-001 step step-02 (../../mp-001-c2-beacon-ngfw-xdr-stitch.yml).
# LEGAL: run only in an authorized, isolated lab. See ../README.md.
set -u -o pipefail
echo "[SIM-MP-001/T1071.001] Endpoint: HTTP beacon callback to known-bad test domain"

echo '[MP-001] Network side: repeated outbound HTTP to known-bad indicator';
for i in 1 2 3 4 5; do
  curl -sSL -m 10 -A 'Mozilla/5.0 (compatible; CortexSimBeacon/1.0)' \
    "http://testmynids.org/uid/index.html?beacon=$i" -o /dev/null || true;
  sleep 12;
done
echo '[*] NGFW should flag repetitive beaconing pattern from DMZ endpoint'

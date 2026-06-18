#!/usr/bin/env bash
# T1074.001 — Stage data in compressed format
# Derived verbatim from SIM-MP-003 step step-02 (../../mp-003-data-staged-exfil-dns-tunnel.yml).
# LEGAL: run only in an authorized, isolated lab. See ../README.md.
set -u -o pipefail
echo "[SIM-MP-003/T1074.001] Stage data in compressed format"

echo '[MP-003] XDR: staging compressed tarball';
tar -czf /tmp/cortexsim_stage/bundle.tar.gz /tmp/cortexsim_stage/creds.txt 2>/dev/null;
ls -la /tmp/cortexsim_stage/;
echo '[*] XDR detects tar+gzip of staging directory (classic exfil prep)'

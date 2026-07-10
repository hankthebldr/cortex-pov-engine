#!/usr/bin/env bash
# T1110.003 — ITDR signal — stale AD service-account auth attempts (password spray)
# Derived verbatim from SIM-MP-005 step step-03 (../../mp-005-cross-plane-correlation.yml).
# LEGAL: run only in an authorized, isolated lab. See ../README.md.
set -u -o pipefail
echo "[SIM-MP-005/T1110.003] ITDR signal — stale AD service-account auth attempts (password spray)"

echo '[MP-005] ITDR signal: invalid Kerberos pre-auth from same src_host';
# Targets a known seeded service account in the ITDR module (which
# plants 5 Kerberoast-vulnerable accounts). Wrong password on purpose —
# XSIAM ITDR should record the pre-auth failure burst.
ITDR_DC=${CORTEXSIM_ITDR_DC_HOST:-dc.corp.local}
ITDR_REALM=${CORTEXSIM_ITDR_REALM:-CORP.LOCAL}
for svc in svc-sql svc-backup svc-webapp svc-jenkins svc-monitor; do
  echo "Wrong-Password!" | timeout 5 kinit "${svc}@${ITDR_REALM}" 2>&1 | head -2 || true
  sleep 1
done
echo '[*] AD Security 4768 (TGT failure) burst recorded from this host'

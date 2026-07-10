#!/usr/bin/env bash
# T1005 — Collect sensitive-looking data from endpoint
# Derived verbatim from SIM-MP-003 step step-01 (../../mp-003-data-staged-exfil-dns-tunnel.yml).
# LEGAL: run only in an authorized, isolated lab. See ../README.md.
set -u -o pipefail
echo "[SIM-MP-003/T1005] Collect sensitive-looking data from endpoint"

echo '[MP-003] XDR: local data collection simulating credential harvest';
mkdir -p /tmp/cortexsim_stage;
echo "# Simulated credential file - NOT real credentials" > /tmp/cortexsim_stage/creds.txt;
for i in 1 2 3 4 5; do
  echo "user$i:simulated-hash-$(openssl rand -hex 16)" >> /tmp/cortexsim_stage/creds.txt;
done;
cat /etc/passwd | head -20 >> /tmp/cortexsim_stage/creds.txt;
echo '[*] XDR should detect /etc/passwd read + file concatenation pattern'

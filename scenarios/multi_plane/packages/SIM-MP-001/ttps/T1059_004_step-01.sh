#!/usr/bin/env bash
# T1059.004 — Endpoint: spawn interactive shell from web service
# Derived verbatim from SIM-MP-001 step step-01 (../../mp-001-c2-beacon-ngfw-xdr-stitch.yml).
# LEGAL: run only in an authorized, isolated lab. See ../README.md.
set -u -o pipefail
echo "[SIM-MP-001/T1059.004] Endpoint: spawn interactive shell from web service"

echo '[MP-001] XDR side: simulating web-app-spawned shell';
bash -c 'id; whoami; uname -a' 2>&1;
echo '[*] This command produces a BIOC on XDR for "shell spawned from www-data"'

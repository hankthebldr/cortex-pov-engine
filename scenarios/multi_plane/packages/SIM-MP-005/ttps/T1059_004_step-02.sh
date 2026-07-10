#!/usr/bin/env bash
# T1059.004 — EDR signal — interactive bash spawned from www-data running curl
# Derived verbatim from SIM-MP-005 step step-02 (../../mp-005-cross-plane-correlation.yml).
# LEGAL: run only in an authorized, isolated lab. See ../README.md.
set -u -o pipefail
echo "[SIM-MP-005/T1059.004] EDR signal — interactive bash spawned from www-data running curl"

echo '[MP-005] EDR signal: shell-from-service-context process anomaly';
# Same src_host as step-01. Same effective user (www-data).
# XDR's BIOC should fire on "interactive shell spawned from web service identity".
bash -c 'id; whoami; uname -a; curl --version' 2>&1 | head -20
echo '[*] XDR BIOC: bash invoked under www-data with interactive shell semantics'

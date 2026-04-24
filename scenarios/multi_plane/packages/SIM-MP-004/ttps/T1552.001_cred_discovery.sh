#!/usr/bin/env bash
# T1552.001 — Unsecured Credentials: Credentials In Files
# Intent: trigger XDR BIOC for recursive credential grep from service account context
set -u -o pipefail
echo "[MP-004/T1552.001] recursive AKIA search from www-data context"
# Service-account-initiated filesystem sweep for AWS key patterns
grep -r -l -E 'AKIA[0-9A-Z]{16}|aws_secret_access_key' /home /var/www /opt 2>/dev/null | head -20 || true
find / -name 'credentials' -path '*/.aws/*' 2>/dev/null | head -10 || true
# Stage any discovered keys to tmp (benign — contents not exfiltrated here)
mkdir -p /tmp && touch /tmp/cortexsim_mp004_discovered.log
echo "[done] XDR BIOC expected: Credential Access — recursive AKIA grep by www-data"

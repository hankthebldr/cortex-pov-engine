#!/usr/bin/env bash
# T1078.004 — Valid Accounts: Cloud Accounts
# Intent: hybrid pivot — endpoint process invokes cloud API with discovered creds
# Stitches EDR (aws-cli by www-data) with CDR (CloudTrail principal event)
set -u -o pipefail
echo "[MP-004/T1078.004] cloud pivot via sts:GetCallerIdentity"
export AWS_ACCESS_KEY_ID="${AWS_ACCESS_KEY_ID:-AKIAIOSFODNN7EXAMPLE}"
export AWS_SECRET_ACCESS_KEY="${AWS_SECRET_ACCESS_KEY:-wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY}"
if command -v aws >/dev/null 2>&1; then
  aws sts get-caller-identity --region us-east-1 2>&1 | head -5 || true
else
  echo "aws CLI not installed — dry-logging intent only"
fi
echo "[done] Expected: XDR (www-data → aws cli) stitched with CDR (sts:GetCallerIdentity)"

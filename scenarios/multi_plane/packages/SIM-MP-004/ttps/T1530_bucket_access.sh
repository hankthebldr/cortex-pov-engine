#!/usr/bin/env bash
# T1530 — Data from Cloud Storage
# Intent: targeted access to sensitivity-tagged bucket; CDR BIOC target
set -u -o pipefail
BUCKET="${TARGET_BUCKET:-cortexsim-sensitive-demo}"
echo "[MP-004/T1530] access sensitive bucket: ${BUCKET}"
if ! command -v aws >/dev/null 2>&1; then
  echo "aws CLI not installed — skipping"; exit 0
fi
aws s3 ls "s3://${BUCKET}/" --recursive --region us-east-1 2>&1 | head -20 || true
aws s3api get-bucket-acl --bucket "${BUCKET}" 2>&1 | head -10 || true
echo "[done] Expected: CDR — GetBucketAcl + ListObjects on sensitivity-tagged bucket by unauthorized principal"

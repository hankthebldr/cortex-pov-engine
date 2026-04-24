#!/usr/bin/env bash
# T1580 — Cloud Infrastructure Discovery
# Intent: rapid multi-service enumeration from single principal; CDR UEBA target
set -u -o pipefail
echo "[MP-004/T1580] multi-service enumeration burst"
if ! command -v aws >/dev/null 2>&1; then
  echo "aws CLI not installed — skipping"; exit 0
fi
aws ec2 describe-instances --region us-east-1 --max-items 5 2>&1 | head -20 || true
aws iam list-users --max-items 10 2>&1 | head -20 || true
aws s3 ls 2>&1 | head -20 || true
echo "[done] Expected: CDR — multi-service burst (EC2+IAM+S3) from single principal in <30s"

#!/usr/bin/env bash
# SIM-MP-004 — APT29 Cloud Credential Theft → Lateral → Exfil
# Single-entry runner — executes the kill chain with inter-step pacing,
# logs each step with UTC timestamps, and prints a detection coverage summary.
#
# LEGAL: Run only in an authorized isolated lab with a sacrificial AWS account.
#        Read README.md prerequisites before first run.

set -u -o pipefail

SCENARIO_ID="SIM-MP-004"
PACKAGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TTP_DIR="${PACKAGE_DIR}/ttps"
LOG_DIR="${PACKAGE_DIR}/evidence"
LOG_FILE="${LOG_DIR}/scenario_execution.log"
SCORECARD="${LOG_DIR}/detection_scorecard.csv"

MODE="full"
TTP_FILTER=""
DELAY_SECONDS=90
DRY_RUN=0
CLEANUP_ONLY=0

TTPS=(
  "T1552.001:T1552.001_cred_discovery.sh:Endpoint cred discovery (grep for AKIA keys)"
  "T1078.004:T1078.004_cloud_pivot.sh:Cloud pivot (sts:GetCallerIdentity)"
  "T1580:T1580_cloud_enum.sh:Cloud discovery burst (EC2+IAM+S3)"
  "T1530:T1530_bucket_access.sh:Sensitive bucket access"
  "T1537:T1537_s3_exfil.sh:S3 cross-account copy"
)

usage() {
  cat <<'EOF'
Usage: run.sh [--mode full|phase|single_ttp] [--ttp TID] [--delay SECONDS]
              [--dry-run] [--cleanup]

  --mode full          Run all TTPs in order (default)
  --mode single_ttp    Run one TTP; requires --ttp <TID>
  --ttp <TID>          Filter a single technique ID, e.g. T1552.001
  --delay <seconds>    Inter-step pacing (default: 90)
  --dry-run            Print what would run; no side effects
  --cleanup            Run cleanup only and exit

Examples:
  ./run.sh --mode full --delay 90
  ./run.sh --mode single_ttp --ttp T1552.001
  ./run.sh --dry-run
  ./run.sh --cleanup
EOF
}

log() {
  local ts
  ts="$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
  echo "[${ts}] $*" | tee -a "${LOG_FILE}"
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --mode)      MODE="$2"; shift 2 ;;
      --ttp)       TTP_FILTER="$2"; shift 2 ;;
      --delay)     DELAY_SECONDS="$2"; shift 2 ;;
      --dry-run)   DRY_RUN=1; shift ;;
      --cleanup)   CLEANUP_ONLY=1; shift ;;
      -h|--help)   usage; exit 0 ;;
      *)           echo "Unknown arg: $1"; usage; exit 2 ;;
    esac
  done
}

preflight() {
  log "=== SIM-MP-004 preflight ==="
  mkdir -p "${LOG_DIR}"
  if ! command -v aws >/dev/null 2>&1; then
    log "WARN: aws CLI not found on PATH. Cloud steps will no-op."
  fi
  if [[ -z "${AWS_ACCESS_KEY_ID:-}" ]]; then
    log "WARN: AWS_ACCESS_KEY_ID not set. Cloud steps will use placeholder keys."
  fi
  if [[ ! -f "${SCORECARD}" ]]; then
    echo "tid,technique,plane,expected_alert,status,alert_id,timestamp_utc" > "${SCORECARD}"
  fi
  log "package_dir: ${PACKAGE_DIR}"
  log "mode: ${MODE}   delay: ${DELAY_SECONDS}s   dry_run: ${DRY_RUN}"
}

run_ttp() {
  local tid="$1" script="$2" description="$3"
  local path="${TTP_DIR}/${script}"
  log "--- TTP ${tid}: ${description} ---"
  if [[ ! -x "${path}" ]]; then
    log "ERROR: TTP script missing or not executable: ${path}"
    return 1
  fi
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    log "[dry-run] would execute: ${path}"
    return 0
  fi
  "${path}" 2>&1 | tee -a "${LOG_FILE}"
  local rc=${PIPESTATUS[0]}
  log "TTP ${tid} completed rc=${rc}"
  return "${rc}"
}

cleanup() {
  log "=== cleanup ==="
  unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN || true
  rm -f /tmp/cortexsim_mp004_*.json /tmp/cortexsim_mp004_*.log 2>/dev/null || true
  log "cleanup complete"
}

summary() {
  log "=== coverage summary ==="
  local total detected
  total=$(( ${#TTPS[@]} ))
  detected=$(awk -F, 'NR>1 && $5=="DETECTED"' "${SCORECARD}" 2>/dev/null | wc -l | tr -d ' ')
  log "TTPs executed: ${total}"
  log "Detections observed (from scorecard): ${detected}"
  log "Scorecard: ${SCORECARD}"
  log "Log file: ${LOG_FILE}"
  log "Next step: populate ${SCORECARD} from XDR/XSIAM console observations."
}

main() {
  parse_args "$@"
  preflight

  if [[ "${CLEANUP_ONLY}" -eq 1 ]]; then
    cleanup
    exit 0
  fi

  local executed=0
  for entry in "${TTPS[@]}"; do
    IFS=':' read -r tid script description <<< "${entry}"
    if [[ "${MODE}" == "single_ttp" ]]; then
      if [[ "${tid}" != "${TTP_FILTER}" ]]; then continue; fi
    fi
    run_ttp "${tid}" "${script}" "${description}" || log "TTP ${tid} reported non-zero rc (continuing)"
    executed=$(( executed + 1 ))
    if [[ "${MODE}" == "full" && "${DRY_RUN}" -eq 0 ]]; then
      log "sleeping ${DELAY_SECONDS}s for CDL/CloudTrail ingestion"
      sleep "${DELAY_SECONDS}"
    fi
  done

  if [[ "${executed}" -eq 0 ]]; then
    log "ERROR: no TTPs matched filter. Check --ttp value."
    exit 3
  fi

  summary
}

main "$@"

#!/usr/bin/env bash
# SIM-MP-005 — Cross-Plane Correlation MOAT — EDR + NDR + ITDR Stitch (TC-IR-05)
# Single-entry runner — executes the multi-plane kill chain with inter-step
# pacing, logs each step with UTC timestamps, prints a coverage summary.
# Mirrors the canonical SIM-MP-004 package runner.
#
# LEGAL: Run only in an authorized isolated lab. Read README.md prerequisites
#        before first run. This package emits real (benign-shaped) signals into
#        the lab so Cortex stitches them into one incident.

set -u -o pipefail

SCENARIO_ID="SIM-MP-005"
PACKAGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TTP_DIR="${PACKAGE_DIR}/ttps"
LOG_DIR="${PACKAGE_DIR}/evidence"
LOG_FILE="${LOG_DIR}/scenario_execution.log"
SCORECARD="${LOG_DIR}/detection_scorecard.csv"

MODE="full"
TTP_FILTER=""
DELAY_SECONDS=60
DRY_RUN=0
CLEANUP_ONLY=0

TTPS=(
  "T1071.001:T1071_001_step-01.sh:NDR signal — outbound HTTP beacon to known IOC domain"
  "T1059.004:T1059_004_step-02.sh:EDR signal — interactive bash spawned from www-data running curl"
  "T1110.003:T1110_003_step-03.sh:ITDR signal — stale AD service-account auth attempts (password spray)"
  "T1078:T1078_step-04.sh:Stitch verification — assert single incident_id spans all 3 planes"
)

usage() {
  cat <<'EOF'
Usage: run.sh [--mode full|single_ttp] [--ttp TID] [--delay SECONDS]
              [--dry-run] [--cleanup]

  --mode full          Run all steps in order (default)
  --mode single_ttp    Run one step; requires --ttp <TID>
  --ttp <TID>          Filter a single technique ID, e.g. T1071.001
  --delay <seconds>    Inter-step pacing (default: 60)
  --dry-run            Print what would run; no side effects
  --cleanup            Run cleanup only and exit
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
  log "=== ${SCENARIO_ID} preflight ==="
  mkdir -p "${LOG_DIR}"
  if [[ ! -f "${SCORECARD}" ]]; then
    echo "tid,technique,plane,expected_alert,status,alert_id,timestamp_utc" > "${SCORECARD}"
  fi
  log "package_dir: ${PACKAGE_DIR}"
  log "mode: ${MODE}   delay: ${DELAY_SECONDS}s   dry_run: ${DRY_RUN}"
}

run_ttp() {
  local tid="$1" script="$2" description="$3"
  local path="${TTP_DIR}/${script}"
  log "--- step ${tid}: ${description} ---"
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
  log "step ${tid} completed rc=${rc}"
  return "${rc}"
}

cleanup() {
  log "=== cleanup ==="
  rm -f /tmp/cortexsim_* /tmp/sim_mp_005_* 2>/dev/null || true
  log "cleanup complete (see source YAML cleanup.commands for the authoritative list)"
}

summary() {
  log "=== coverage summary ==="
  local total detected
  total=$(( ${#TTPS[@]} ))
  detected=$(awk -F, 'NR>1 && $5=="DETECTED"' "${SCORECARD}" 2>/dev/null | wc -l | tr -d ' ')
  log "steps executed: ${total}"
  log "detections observed (from scorecard): ${detected}"
  log "F2 success = all required planes present under ONE incident_id (see detections/correlation_rules.xql)"
  log "Next step: populate ${SCORECARD} from the XSIAM incident + causality view."
}

main() {
  parse_args "$@"
  preflight
  if [[ "${CLEANUP_ONLY}" -eq 1 ]]; then cleanup; exit 0; fi
  local executed=0
  for entry in "${TTPS[@]}"; do
    IFS=':' read -r tid script description <<< "${entry}"
    if [[ "${MODE}" == "single_ttp" && "${tid}" != "${TTP_FILTER}" ]]; then continue; fi
    run_ttp "${tid}" "${script}" "${description}" || log "step ${tid} non-zero rc (continuing)"
    executed=$(( executed + 1 ))
    if [[ "${MODE}" == "full" && "${DRY_RUN}" -eq 0 ]]; then
      log "sleeping ${DELAY_SECONDS}s for NGFW/XDR/IdP ingestion + correlation"
      sleep "${DELAY_SECONDS}"
    fi
  done
  if [[ "${executed}" -eq 0 ]]; then log "ERROR: no steps matched filter."; exit 3; fi
  summary
}

main "$@"

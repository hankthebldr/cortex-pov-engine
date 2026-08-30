#!/usr/bin/env bash
# ===========================================================================
# run-tier-d.sh — CortexSim Tier-D pull-mode (agent path) e2e harness
# ---------------------------------------------------------------------------
# Tier C detonates a PUSH BUNDLE in an audited container. Nothing repeatably
# exercised the PULL path: mint token -> one-liner -> sha256-verified beacon ->
# server-assigned id -> enrol -> poll -> payload staging -> execute under the
# identity harness -> POST output -> complete -> Results seeded.
#
# That path had been proven exactly once, by hand. This makes it re-runnable.
#
# WHAT THIS ASSERTS, AND WHY IT IS NOT "did every step exit 0"
#
# A step can fail for three very different reasons, and collapsing them is the
# single most damaging thing this product can do:
#
#   ENGINE     the beacon/orchestrator/identity harness broke. A real defect.
#   ENVIRONMENT the target could not support the step — a missing account, an
#              absent tool, no egress to fetch one. The TTP NEVER RAN.
#   TTP        the technique ran and legitimately did not succeed.
#
# Only ENGINE failures mean CortexSim is broken. ENVIRONMENT failures are the
# ones that, left unclassified, surface in a POV report as "Cortex missed it"
# when in fact nothing was ever executed for Cortex to miss.
#
# USAGE
#   deploy/tier-d/run-tier-d.sh --scenario SIM-EDR-001
#   deploy/tier-d/run-tier-d.sh --scenario SIM-EDR-001 --simcore http://localhost:8888
#   deploy/tier-d/run-tier-d.sh --scenario SIM-EDR-001 --keep     # leave target up
#
# EXIT CODES
#   0  lifecycle completed and no ENGINE-class failure was observed
#   1  an ENGINE-class failure was observed (a real defect)
#   2  harness could not set up (docker, SimCore unreachable, enrol failed)
# ===========================================================================
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../.." >/dev/null 2>&1 && pwd)"

SIMCORE="${CORTEXSIM_SERVER:-http://localhost:8888}"
SCENARIO=""
KEEP=0
TARGET_NAME="cortexsim-tier-d-target"
TARGET_IMAGE="cortexsim-tier-d-target:latest"
RESULTS_DIR=""

log()  { printf '\033[0;36m[tier-d]\033[0m %s\n' "$*"; }
ok()   { printf '\033[0;32m[tier-d] ✓\033[0m %s\n' "$*"; }
warn() { printf '\033[0;33m[tier-d] !\033[0m %s\n' "$*"; }
err()  { printf '\033[0;31m[tier-d] ✗\033[0m %s\n' "$*" >&2; }

while [ $# -gt 0 ]; do
  case "$1" in
    --scenario) SCENARIO="$2"; shift 2 ;;
    --simcore)  SIMCORE="$2";  shift 2 ;;
    --results)  RESULTS_DIR="$2"; shift 2 ;;
    --keep)     KEEP=1; shift ;;
    -h|--help)  sed -n '1,40p' "$0"; exit 0 ;;
    *) err "unknown argument: $1"; exit 2 ;;
  esac
done

[ -n "$SCENARIO" ] || { err "--scenario is required (e.g. --scenario SIM-EDR-001)"; exit 2; }
RESULTS_DIR="${RESULTS_DIR:-${SCRIPT_DIR}/results/${SCENARIO}}"
mkdir -p "$RESULTS_DIR"

cleanup() {
  if [ "$KEEP" -eq 1 ]; then
    warn "leaving target container '${TARGET_NAME}' up (--keep)"
  else
    docker rm -f "$TARGET_NAME" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# 0. Preconditions
# ---------------------------------------------------------------------------
command -v docker >/dev/null 2>&1 || { err "docker not on PATH"; exit 2; }
docker info >/dev/null 2>&1        || { err "docker daemon unreachable (try DOCKER_CONTEXT=default)"; exit 2; }

if ! curl -fsS --max-time 5 "${SIMCORE}/api/health" >/dev/null 2>&1; then
  err "SimCore unreachable at ${SIMCORE} — start it with scripts/dev-up.sh"
  exit 2
fi
ok "SimCore reachable at ${SIMCORE}"

# ---------------------------------------------------------------------------
# 1. Provisioned target
# ---------------------------------------------------------------------------
log "building the provisioned target image (bare ubuntu cannot run this corpus)"
docker build -q -f "${SCRIPT_DIR}/Dockerfile.target" -t "$TARGET_IMAGE" "$SCRIPT_DIR" >/dev/null
docker rm -f "$TARGET_NAME" >/dev/null 2>&1 || true
docker run -d --name "$TARGET_NAME" --network host "$TARGET_IMAGE" >/dev/null
ok "target '${TARGET_NAME}' up"

# ---------------------------------------------------------------------------
# 2. Enrol a REAL beacon via the REAL one-liner
# ---------------------------------------------------------------------------
log "minting an enrollment token"
TOKEN="$(curl -fsS -X POST -H 'Content-Type: application/json' \
          -d '{"ttl_seconds":900,"max_uses":1}' \
          "${SIMCORE}/api/agents/enroll/tokens" \
        | python3 -c 'import sys,json;d=json.load(sys.stdin);print(d.get("token") or d.get("enrollment_token") or "")')"
[ -n "$TOKEN" ] || { err "could not mint an enrollment token"; exit 2; }

# `--mode=foreground` BLOCKS by design (it babysits the beacon), so it must be
# run detached. Running it attached is the mistake that looks like a hang.
log "running the real installer one-liner on the target (detached)"
docker exec -d \
  -e CORTEXSIM_TOKEN="$TOKEN" \
  -e CORTEXSIM_SERVER="$SIMCORE" \
  -e CORTEXSIM_MODE=foreground \
  "$TARGET_NAME" \
  bash -c "curl -fsSL '${SIMCORE}/api/agents/install?os=linux' | bash"

log "waiting for the beacon to check in"
AGENT_ID=""
for _ in $(seq 1 40); do
  AGENT_ID="$(curl -fsS "${SIMCORE}/api/agents" \
    | python3 -c '
import sys,json
d=json.load(sys.stdin); a=d if isinstance(d,list) else d.get("agents",[])
live=[x for x in a if x.get("status")=="online"]
print(live[0]["agent_id"] if live else "")' 2>/dev/null || true)"
  [ -n "$AGENT_ID" ] && break
  sleep 2
done
[ -n "$AGENT_ID" ] || {
  err "no beacon came online — install telemetry:"
  curl -fsS "${SIMCORE}/api/agents/install/attempts" | tail -c 800 >&2 || true
  exit 2
}
ok "beacon online: ${AGENT_ID}"

# ---------------------------------------------------------------------------
# 3. Launch
#
# The consent gate is a real safety control: a scenario binding a dual-use
# adapter is REFUSED without explicit authorization. This harness runs against
# an ephemeral local container it created itself, so it authorizes explicitly
# rather than pretending the gate is not there.
# ---------------------------------------------------------------------------
log "launching ${SCENARIO} in pull mode against ${AGENT_ID}"
LAUNCH="$(curl -fsS -X POST -H 'Content-Type: application/json' \
  -d "{\"scenario_id\":\"${SCENARIO}\",\"mode\":\"pull\",\"target_agent_id\":\"${AGENT_ID}\",\"consent\":{\"simulation_authorized\":true,\"c2_authorized\":false}}" \
  "${SIMCORE}/api/runs" || true)"

RUN_ID="$(printf '%s' "$LAUNCH" | python3 -c 'import sys,json
try: print(json.load(sys.stdin).get("run_id",""))
except Exception: print("")' 2>/dev/null || true)"

if [ -z "$RUN_ID" ]; then
  err "launch refused:"
  printf '%s\n' "$LAUNCH" | head -c 900 >&2
  exit 2
fi
ok "run ${RUN_ID}"

# ---------------------------------------------------------------------------
# 4. Poll to terminal
# ---------------------------------------------------------------------------
log "waiting for the run to reach a terminal state"
STATUS="running"
for _ in $(seq 1 120); do
  STATUS="$(curl -fsS "${SIMCORE}/api/runs/${RUN_ID}" \
    | python3 -c 'import sys,json;print(json.load(sys.stdin).get("status",""))' 2>/dev/null || echo "")"
  case "$STATUS" in
    running|pending|queued|"") sleep 3 ;;
    *) break ;;
  esac
done

curl -fsS "${SIMCORE}/api/runs/${RUN_ID}" > "${RESULTS_DIR}/run.json"
ok "terminal status: ${STATUS}  (run.json saved)"

# ---------------------------------------------------------------------------
# 5. Classify — the part that makes this a harness rather than a smoke test
# ---------------------------------------------------------------------------
python3 "${SCRIPT_DIR}/classify.py" \
  --run "${RESULTS_DIR}/run.json" \
  --scenario "$SCENARIO" \
  --out "${RESULTS_DIR}/verdict.json"
RC=$?

log "artifacts in ${RESULTS_DIR}"
exit $RC

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
#   0  lifecycle completed and classify.py returned a genuine PASS (a clean
#      run, or an honest PASS-with-unrun-steps ENVIRONMENT outcome)
#   1  classify.py returned FAIL or INCONCLUSIVE — an ENGINE-class failure
#      (a real defect), or the run does not carry enough honest evidence to
#      call PASS (zero steps executed, some declared steps never reported)
#   2  harness could not set up (docker, SimCore unreachable, enrol failed)
#   3  the run never reached a terminal state within the poll budget (a hung
#      run, or SimCore not updating status) — classify.py was never even
#      invoked, because there is nothing terminal yet to classify
# ===========================================================================
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../.." >/dev/null 2>&1 && pwd)"

# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/poll_status.sh"

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
#
# Selecting "the first online agent" is WRONG on a shared docker host: this
# machine can (and did, in testing) already have another beacon online — e.g.
# a leftover container from a previous manual run — with the SAME hostname as
# this target (containers on `--network host` inherit the host's kernel
# hostname). /api/agents has no field that says "this is the one MY install
# just enrolled", so two agents can legitimately share agent-id-prefix
# "<hostname>-..." and differ only in the random suffix. Picking online[0] by
# recency raced and silently ran the scenario against the WRONG, unprovisioned
# container — the exact www-data/nologin defect this harness exists to catch,
# reintroduced by the harness's own agent-selection bug.
#
# Fix: correlate by TIME + the install script's own telemetry, not by guessing
# from agent state. The installer POSTs `stage=run code=OK agent_id=<id>` to
# /api/agents/install/telemetry the moment it starts the beacon (see
# core/api/agents.py InstallTelemetry) — that record IS the ground truth for
# "which agent id did THIS install produce". We only accept a telemetry row
# timestamped at or after the moment we kicked off this install.
# Naive-UTC on purpose, to lexically compare against the server's own
# `datetime.utcnow().isoformat()` timestamps in install/telemetry — a
# timezone-aware ("+00:00"-suffixed) string would compare unreliably against
# the server's un-suffixed one.
INSTALL_START_TS="$(python3 -W ignore -c 'from datetime import datetime; print(datetime.utcnow().isoformat())')"
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
  AGENT_ID="$(curl -fsS "${SIMCORE}/api/agents/install/attempts?limit=20" \
    | python3 -c "
import sys, json
start = '${INSTALL_START_TS}'
d = json.load(sys.stdin)
attempts = d.get('attempts', [])
# newest-first; take the first 'run'/OK attempt reported at-or-after our own
# install start with a non-empty agent_id — i.e. OUR install, not someone
# else's beacon that happens to still be alive on this host.
for a in attempts:
    if (a.get('stage') == 'run' and a.get('code') == 'OK'
            and a.get('agent_id') and a.get('reported_at', '') >= start):
        print(a['agent_id'])
        break
" 2>/dev/null || true)"
  [ -n "$AGENT_ID" ] && break
  sleep 2
done
[ -n "$AGENT_ID" ] || {
  err "no beacon came online — install telemetry:"
  curl -fsS "${SIMCORE}/api/agents/install/attempts" | tail -c 800 >&2 || true
  exit 2
}
# Belt-and-suspenders: confirm the correlated agent is actually online before
# trusting it to run the scenario.
LIVE_CHECK="$(curl -fsS "${SIMCORE}/api/agents" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
a = d if isinstance(d, list) else d.get('agents', [])
match = [x for x in a if x.get('agent_id') == '${AGENT_ID}']
print(match[0].get('status', '') if match else '')" 2>/dev/null || true)"
[ "$LIVE_CHECK" = "online" ] || { err "correlated agent ${AGENT_ID} is not online (status='${LIVE_CHECK}')"; exit 2; }
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
  is_non_terminal_status "$STATUS" && sleep 3 || break
done

curl -fsS "${SIMCORE}/api/runs/${RUN_ID}" > "${RESULTS_DIR}/run.json"

# A hung run is a green harness if this is not checked: the loop above just
# EXITS after its fixed budget regardless of why — it does not distinguish
# "reached a terminal state" from "gave up waiting". Without this gate, a
# STATUS still stuck at running/pending/queued/"" prints as if it were a
# legitimate terminal status and classify.py runs against a mid-flight,
# forever-incomplete run.json.
if is_non_terminal_status "$STATUS"; then
  err "run ${RUN_ID} never reached a terminal state within the poll budget (120 x 3s = 360s)."
  err "last known status: '${STATUS}'. This is NOT a pass — the run may be hung, or SimCore"
  err "may not be updating run status. Partial run.json saved to ${RESULTS_DIR}/run.json."
  exit 3
fi
ok "terminal status: ${STATUS}  (run.json saved)"

# ---------------------------------------------------------------------------
# 5. Classify — the part that makes this a harness rather than a smoke test
# ---------------------------------------------------------------------------
# `|| RC=$?` is load-bearing, not decoration: under `set -e`, classify.py
# returning 1 (an ENGINE-class verdict) would otherwise terminate the script
# on THAT statement, before the `log "artifacts in..."` line below ever runs —
# so the one message that tells the operator where verdict.json landed would
# silently vanish on exactly the run that most needs it read.
RC=0
python3 "${SCRIPT_DIR}/classify.py" \
  --run "${RESULTS_DIR}/run.json" \
  --scenario "$SCENARIO" \
  --out "${RESULTS_DIR}/verdict.json" || RC=$?

log "artifacts in ${RESULTS_DIR}"
exit $RC

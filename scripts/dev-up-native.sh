#!/usr/bin/env bash
# ==============================================================================
# dev-up-native.sh — CortexSim bring-up WITHOUT Docker (fallback path).
#
# scripts/dev-up.sh (the container path) is the canonical jumpbox entrypoint —
# the image bakes agent-dist/rust-dist/payloads so it needs no toolchain at
# all on the host. Use THIS script only when the Docker daemon is unavailable
# or the image registry is unreachable — e.g. sandboxed CI runners and cloud
# dev containers where Docker Hub blob pulls are blocked. Because this path
# runs straight on the host instead of the pre-baked image, it is honest that
# it starts from LESS than the image has: no agent-dist/ (prebuilt beacons)
# and no rust-dist/ until you build them yourself — see step 5 below, which
# reports that as a "degraded" boot rather than either hiding it or timing
# out as if the app never started.
#
# Same contract as scripts/dev-up.sh (generate .env, bring the stack up, poll
# /api/health):
#   1. Generate ./.env from .env.example (existing .env is never clobbered).
#   2. Create ./.venv (Python 3.11) and install core/requirements.txt.
#   3. Build the React UI and install it into core/static/ (what SimCore mounts
#      as StaticFiles — the Dockerfile does this via its ui-builder stage).
#   4. Launch uvicorn in the background, logging to logs/simcore.log.
#   5. Poll http://localhost:${CORTEXSIM_PORT}/api/health until it responds.
#      {"status":"ok"} prints success; {"status":"degraded"} still counts as
#      a successful bring-up and prints which component(s) are degraded and
#      why (e.g. AGENT_DIST_EMPTY — run `make agent-dist` for prebuilt
#      beacons) instead of silently spinning for 90s and reporting a bare
#      timeout for a server that came up fine. Only a genuine non-response,
#      or the uvicorn process dying, is an actual failure here.
#
# Usage:  scripts/dev-up-native.sh            # full bring-up
#         scripts/dev-up-native.sh --skip-ui  # reuse existing core/static/
#         scripts/dev-up-native.sh --stop     # stop a running instance
#
# Prerequisites: python3.11, node/npm. The Go beacon is NOT built here — see
# CLAUDE.md ("Go Beacon Agent") for `go build -o bin/cortexsim-agent .`.
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." >/dev/null 2>&1 && pwd)"
cd "${REPO_ROOT}"

ENV_FILE="${REPO_ROOT}/.env"
ENV_EXAMPLE="${REPO_ROOT}/.env.example"
VENV="${REPO_ROOT}/.venv"
LOG_FILE="${REPO_ROOT}/logs/simcore.log"
PID_FILE="${REPO_ROOT}/logs/simcore.pid"

SKIP_UI=0
for arg in "$@"; do
  case "${arg}" in
    --skip-ui) SKIP_UI=1 ;;
    --stop)    STOP=1 ;;
    *) printf 'unknown flag: %s\n' "${arg}" >&2; exit 2 ;;
  esac
done

log() { printf '\033[0;36m[dev-up-native]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[dev-up-native]\033[0m %s\n' "$*"; }
err() { printf '\033[0;31m[dev-up-native] ERROR:\033[0m %s\n' "$*" >&2; }

mkdir -p "${REPO_ROOT}/logs"

# ---------------------------------------------------------------------------
# 0. --stop: terminate a running instance and exit.
# ---------------------------------------------------------------------------
if [[ -n "${STOP:-}" ]]; then
  if [[ -f "${PID_FILE}" ]] && kill -0 "$(cat "${PID_FILE}")" 2>/dev/null; then
    kill "$(cat "${PID_FILE}")"
    rm -f "${PID_FILE}"
    log "SimCore stopped."
  else
    log "No running SimCore recorded in ${PID_FILE}."
  fi
  exit 0
fi

# ---------------------------------------------------------------------------
# 1. Ensure .env exists with a real secret (development mode).
#    Mirrors dev-up.sh so both paths produce an identical .env.
# ---------------------------------------------------------------------------
gen_secret() {
  if command -v python3 >/dev/null 2>&1; then
    python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
  elif command -v openssl >/dev/null 2>&1; then
    openssl rand -base64 48 | tr -d '\n'
  else
    err "Need python3 or openssl to generate CORTEXSIM_SECRET."
    exit 1
  fi
}

if [[ -f "${ENV_FILE}" ]]; then
  log ".env already present — leaving it untouched."
else
  [[ -f "${ENV_EXAMPLE}" ]] || { err ".env.example not found — cannot bootstrap .env."; exit 1; }
  log "No .env found — generating one from .env.example (development mode)."
  SECRET="$(gen_secret)"
  cp "${ENV_EXAMPLE}" "${ENV_FILE}"
  sed -i \
    -e 's/^CORTEXSIM_ENV=.*/CORTEXSIM_ENV=development/' \
    -e "s|^CORTEXSIM_SECRET=.*|CORTEXSIM_SECRET=${SECRET}|" \
    "${ENV_FILE}"
  chmod 600 "${ENV_FILE}" 2>/dev/null || true
  log "Wrote ${ENV_FILE} with a freshly generated CORTEXSIM_SECRET."
fi

PORT="$(sed -n 's/^CORTEXSIM_PORT=\([0-9][0-9]*\).*/\1/p' "${ENV_FILE}" | head -n1)"
PORT="${PORT:-8888}"

# ---------------------------------------------------------------------------
# 2. Python venv + backend dependencies.
# ---------------------------------------------------------------------------
PY=""
for candidate in python3.11 python3; do
  if command -v "${candidate}" >/dev/null 2>&1; then
    if "${candidate}" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3, 11) else 1)'; then
      PY="${candidate}"
      break
    fi
  fi
done
[[ -n "${PY}" ]] || { err "Python 3.11+ not found. Install python3.11 and re-run."; exit 1; }

if [[ ! -x "${VENV}/bin/python" ]]; then
  log "Creating virtualenv at ${VENV} (${PY})."
  "${PY}" -m venv "${VENV}"
fi
log "Installing backend dependencies (core/requirements.txt)."
"${VENV}/bin/pip" install -q --upgrade pip
"${VENV}/bin/pip" install -q -r "${REPO_ROOT}/core/requirements.txt"

# ---------------------------------------------------------------------------
# 3. Build the React UI into core/static/.
#    SimCore mounts core/static/ as StaticFiles; without it "/" 404s.
# ---------------------------------------------------------------------------
if (( SKIP_UI )); then
  log "--skip-ui: reusing existing core/static/."
  [[ -f "${REPO_ROOT}/core/static/index.html" ]] || \
    err "core/static/index.html missing — the UI will 404 until you re-run without --skip-ui."
else
  command -v npm >/dev/null 2>&1 || { err "npm not found — install Node 20+ or pass --skip-ui."; exit 1; }
  log "Building the React UI (npm ci && npm run build)."
  ( cd "${REPO_ROOT}/ui" && npm ci --silent && npm run build )
  rm -rf "${REPO_ROOT}/core/static"
  cp -r "${REPO_ROOT}/ui/dist" "${REPO_ROOT}/core/static"
  log "Installed UI build into core/static/."
fi

# ---------------------------------------------------------------------------
# 3b. Adapter-source preflight (non-fatal) — same rationale as dev-up.sh:
#     tier-2 sources are executed by the agent on the target, not by SimCore,
#     so a miss is a heads-up that atomic-backed scenarios won't detonate.
# ---------------------------------------------------------------------------
if [[ -x "${REPO_ROOT}/scripts/check-adapter-sources.sh" ]]; then
  if ! "${REPO_ROOT}/scripts/check-adapter-sources.sh" >/dev/null 2>&1; then
    log "Heads-up: a tier-2 adapter source tree is missing — atomic-backed"
    log "scenarios won't detonate. Details: scripts/check-adapter-sources.sh"
  fi
fi

# ---------------------------------------------------------------------------
# 4. Launch uvicorn. CWD must be core/ (main.py uses flat imports) and
#    CORTEXSIM_BASE_DIR must be the repo root so scenarios/, detection_scanner/,
#    tools/ and data/ resolve — the same wiring the Dockerfile sets via ENV.
# ---------------------------------------------------------------------------
if [[ -f "${PID_FILE}" ]] && kill -0 "$(cat "${PID_FILE}")" 2>/dev/null; then
  log "SimCore already running (pid $(cat "${PID_FILE}")) — restarting it."
  kill "$(cat "${PID_FILE}")" || true
  sleep 2
fi

set -a
# shellcheck disable=SC1090
. "${ENV_FILE}"
set +a
export CORTEXSIM_BASE_DIR="${REPO_ROOT}"

log "Starting SimCore on port ${PORT} (logs: ${LOG_FILE})."
# Detach properly: `setsid` puts uvicorn in its own session so it outlives this
# script, and every fd is redirected (stdin from /dev/null, stdout+stderr to the
# log) so a piped invocation — `dev-up-native.sh | tee` — is not held open by the
# server holding the caller's stdout. $! is then uvicorn's own pid, which is what
# --stop needs; a wrapper subshell's pid would leave --stop killing the wrong
# process (or nothing).
cd "${REPO_ROOT}/core"
setsid "${VENV}/bin/uvicorn" main:app --host 0.0.0.0 --port "${PORT}" \
    </dev/null >"${LOG_FILE}" 2>&1 &
SIMCORE_PID=$!
echo "${SIMCORE_PID}" >"${PID_FILE}"
disown "${SIMCORE_PID}" 2>/dev/null || true
cd "${REPO_ROOT}"

# ---------------------------------------------------------------------------
# 5. Poll health until it responds (timeout ~90s). "ok" and "degraded" both
#    mean the app came up — see the header comment. Only silence, or the
#    uvicorn process dying, is an actual failure.
# ---------------------------------------------------------------------------

# Print each degraded component's code + detail from a /api/health body.
# python3 (exact, key-order-independent) when available; otherwise point the
# operator at the log/URL rather than guess with fragile text parsing.
print_degraded_components() {
  local body="$1"
  if command -v python3 >/dev/null 2>&1; then
    printf '%s' "${body}" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
    for name, comp in (d.get("components") or {}).items():
        if isinstance(comp, dict) and comp.get("status") == "degraded":
            print("    - %s: %s -- %s" % (name, comp.get("code", "?"), comp.get("detail", "")))
except Exception:
    pass
' 2>/dev/null || true
  else
    log "  (install python3 to see per-component detail, or: curl -s ${HEALTH_URL})"
  fi
}

# Extract the top-level "status" field. python3 first (correct regardless of
# key order); the sed fallback is anchored to the START of the body
# (`^{"status":...`), not a greedy scan — a plain `.*"status":...` match
# grabs the LAST "status" key in the blob, which belongs to a nested
# component, not the top-level field, on any response with more than one
# component (i.e. every real response). Verified against a live degraded
# SimCore: the unanchored form reported "ok".
extract_status() {
  local body="$1"
  if command -v python3 >/dev/null 2>&1; then
    printf '%s' "${body}" | python3 -c 'import json,sys
try:
    print(json.load(sys.stdin).get("status",""))
except Exception:
    pass' 2>/dev/null || true
  else
    printf '%s' "${body}" | sed -n 's/^{[[:space:]]*"status"[[:space:]]*:[[:space:]]*"\([a-z_]*\)".*/\1/p' | head -n1
  fi
}

HEALTH_URL="http://localhost:${PORT}/api/health"
log "Waiting for ${HEALTH_URL} ..."

deadline=$(( $(date +%s) + 90 ))
while :; do
  BODY="$(curl -fsS --max-time 3 "${HEALTH_URL}" 2>/dev/null || true)"
  if [[ -n "${BODY}" ]]; then
    HSTATUS="$(extract_status "${BODY}")"
    case "${HSTATUS}" in
      ok)
        log "SimCore is healthy."
        printf '\n  \033[0;32m✓ CortexSim is up:\033[0m  http://localhost:%s\n' "${PORT}"
        printf '    logs: %s   stop: scripts/dev-up-native.sh --stop\n\n' "${LOG_FILE}"
        exit 0
        ;;
      degraded)
        warn "SimCore booted but reports DEGRADED — this is common on a native"
        warn "bring-up (e.g. no prebuilt agent-dist/ until you run 'make agent-dist')."
        print_degraded_components "${BODY}"
        printf '\n  \033[1;33m⚠ CortexSim is up (degraded):\033[0m  http://localhost:%s\n' "${PORT}"
        printf '    logs: %s   stop: scripts/dev-up-native.sh --stop\n' "${LOG_FILE}"
        printf '    Full detail: curl -s %s | python3 -m json.tool\n\n' "${HEALTH_URL}"
        exit 0
        ;;
    esac
  fi
  if ! kill -0 "${SIMCORE_PID}" 2>/dev/null; then
    err "SimCore exited during startup. Last log lines:"
    tail -n 20 "${LOG_FILE}" >&2
    exit 1
  fi
  if (( $(date +%s) >= deadline )); then
    err "Timed out waiting for ${HEALTH_URL} after 90s. See ${LOG_FILE}."
    exit 1
  fi
  sleep 2
done

#!/usr/bin/env bash
# ==============================================================================
# dev-up.sh — one-command local CortexSim bring-up.
#
# Idempotent + safe to re-run:
#   1. If ./.env is missing, generate one from .env.example with a freshly
#      generated CORTEXSIM_SECRET and CORTEXSIM_ENV=development. An existing
#      .env is never clobbered — your secret survives re-runs.
#   2. `docker compose up -d --build`.
#   3. Poll http://localhost:${CORTEXSIM_PORT}/api/health until {"status":"ok"}
#      (timeout ~90s) and print the URL.
#
# Usage:  scripts/dev-up.sh
# ==============================================================================
set -euo pipefail

# Resolve repo root from this script's location so it runs from anywhere.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." >/dev/null 2>&1 && pwd)"
cd "${REPO_ROOT}"

ENV_FILE="${REPO_ROOT}/.env"
ENV_EXAMPLE="${REPO_ROOT}/.env.example"

log() { printf '\033[0;36m[dev-up]\033[0m %s\n' "$*"; }
err() { printf '\033[0;31m[dev-up] ERROR:\033[0m %s\n' "$*" >&2; }

# ---------------------------------------------------------------------------
# 1. Ensure .env exists with a real secret (development mode).
# ---------------------------------------------------------------------------
gen_secret() {
  # Prefer python's secrets; fall back to openssl. Either yields >= 32 bytes
  # of high entropy, which satisfies the production master-key guard too.
  if command -v python3 >/dev/null 2>&1; then
    python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
  elif command -v openssl >/dev/null 2>&1; then
    openssl rand -base64 48 | tr -d '\n'
  else
    err "Need python3 or openssl to generate CORTEXSIM_SECRET. Install one and re-run."
    exit 1
  fi
}

if [[ -f "${ENV_FILE}" ]]; then
  log ".env already present — leaving it untouched."
else
  if [[ ! -f "${ENV_EXAMPLE}" ]]; then
    err ".env.example not found at ${ENV_EXAMPLE} — cannot bootstrap .env."
    exit 1
  fi
  log "No .env found — generating one from .env.example (development mode)."
  SECRET="$(gen_secret)"

  # Start from the template, then override the two values that matter for a
  # frictionless local boot. sed only rewrites the assignment lines; comments
  # and docs from .env.example are preserved.
  cp "${ENV_EXAMPLE}" "${ENV_FILE}"
  sed -i \
    -e 's/^CORTEXSIM_ENV=.*/CORTEXSIM_ENV=development/' \
    -e "s|^CORTEXSIM_SECRET=.*|CORTEXSIM_SECRET=${SECRET}|" \
    "${ENV_FILE}"
  chmod 600 "${ENV_FILE}" 2>/dev/null || true
  log "Wrote ${ENV_FILE} with a freshly generated CORTEXSIM_SECRET."
fi

# Read CORTEXSIM_PORT from .env for the health-check URL (default 8888).
PORT="$(sed -n 's/^CORTEXSIM_PORT=\([0-9][0-9]*\).*/\1/p' "${ENV_FILE}" | head -n1)"
PORT="${PORT:-8888}"

# ---------------------------------------------------------------------------
# 1b. Adapter-source preflight (non-fatal).
#     SimCore boots and serves the UI without the tier-2 adapter source trees
#     (e.g. sources/atomic-red-team) — those are executed by the agent on the
#     target, not by SimCore. So a miss here is a heads-up, not a blocker: it
#     tells you the atomic-backed EDR/MP scenarios won't detonate until you run
#     `git submodule update --init --recursive` (or, if a gitlink is missing,
#     `git submodule add --force <url> <path>`). install.sh enforces this hard.
# ---------------------------------------------------------------------------
if [[ -x "${REPO_ROOT}/scripts/check-adapter-sources.sh" ]]; then
  if ! "${REPO_ROOT}/scripts/check-adapter-sources.sh" >/dev/null 2>&1; then
    log "Heads-up: a tier-2 adapter source tree is missing — atomic-backed"
    log "scenarios won't detonate. Details: scripts/check-adapter-sources.sh"
  fi
fi

# ---------------------------------------------------------------------------
# 2. Bring the stack up.
# ---------------------------------------------------------------------------
if docker compose version >/dev/null 2>&1; then
  DC=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  DC=(docker-compose)
else
  err "Docker Compose not found. Install Docker Desktop or the compose plugin."
  exit 1
fi

log "Building and starting SimCore: ${DC[*]} up -d --build"
"${DC[@]}" up -d --build

# ---------------------------------------------------------------------------
# 3. Poll health until ok (timeout ~90s).
# ---------------------------------------------------------------------------
HEALTH_URL="http://localhost:${PORT}/api/health"
log "Waiting for ${HEALTH_URL} ..."

deadline=$(( $(date +%s) + 90 ))
while :; do
  if curl -fsS --max-time 3 "${HEALTH_URL}" 2>/dev/null | grep -q '"status"[[:space:]]*:[[:space:]]*"ok"'; then
    log "SimCore is healthy."
    printf '\n  \033[0;32m✓ CortexSim is up:\033[0m  http://localhost:%s\n\n' "${PORT}"
    exit 0
  fi
  if (( $(date +%s) >= deadline )); then
    err "Timed out waiting for ${HEALTH_URL} after 90s."
    err "Check logs with: ${DC[*]} logs -f simcore"
    exit 1
  fi
  sleep 2
done

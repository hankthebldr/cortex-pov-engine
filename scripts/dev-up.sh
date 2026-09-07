#!/usr/bin/env bash
# ==============================================================================
# dev-up.sh — THE canonical CortexSim bring-up for a jumpbox operator.
#
# This is the container path: the image bakes agent-dist/ + rust-dist/ +
# payloads/ at build time, so this script needs no Go/Rust/Node toolchain and
# no git submodules on the host — only docker. (install.sh is the OTHER path:
# a full from-source bootstrap for contributors who are building the toolchain
# itself. dev-up-native.sh is the fallback for hosts with no Docker daemon.)
#
# Idempotent + safe to re-run:
#   1. Prereq check: docker present and its daemon reachable, with an
#      actionable fix (not a raw docker error) when it isn't.
#   2. If ./.env is missing, generate one from .env.example with a freshly
#      generated CORTEXSIM_SECRET and CORTEXSIM_ENV=development. An existing
#      .env is never clobbered — your secret survives re-runs.
#   3. `docker compose up -d --build`.
#   4. Poll http://localhost:${CORTEXSIM_PORT}/api/health until it responds.
#      {"status":"ok"} prints success; {"status":"degraded"} still counts as
#      a successful bring-up (the app IS up) and prints which component(s)
#      are degraded and why, rather than silently spinning for 90s and
#      reporting a bare timeout for a server that came up fine. Only a
#      genuine non-response times out as an actual failure.
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
warn() { printf '\033[1;33m[dev-up]\033[0m %s\n' "$*"; }
err() { printf '\033[0;31m[dev-up] ERROR:\033[0m %s\n' "$*" >&2; }

# ---------------------------------------------------------------------------
# 0. Prereqs: docker present, its daemon reachable. Fail fast with the exact
#    fix rather than letting `docker compose up` surface a raw connection
#    error 90 seconds into a build. The daemon-unreachable branch specifically
#    covers the Docker-Desktop-hijacked-the-active-context trap: `docker
#    context ls` still shows a working context, just not the current one.
# ---------------------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
  err "docker not found on PATH."
  err "Install it:   curl -fsSL https://get.docker.com | sudo sh"
  err "(or use the full source bootstrap instead: ./install.sh)"
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  err "docker is installed but its daemon is not reachable from context '$(docker context show 2>/dev/null || echo unknown)'."
  alt=""
  while IFS= read -r ctx; do
    if DOCKER_CONTEXT="${ctx}" docker info >/dev/null 2>&1; then
      alt="${ctx}"
      break
    fi
  done < <(docker context ls --format '{{.Name}}' 2>/dev/null || true)
  if [[ -n "${alt}" ]]; then
    err "Context '${alt}' IS reachable. Fix:   export DOCKER_CONTEXT=${alt}"
  else
    err "Start the daemon, e.g.:   sudo systemctl start docker"
  fi
  exit 1
fi
log "docker: $(docker --version) — daemon reachable."

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

  # Render the template to a temp file and move it into place, rather than
  # `cp` then `sed -i`. Two reasons, both of which bit on macOS:
  #   - BSD sed's -i REQUIRES a backup-suffix argument, so the GNU-style
  #     `sed -i -e ...` parses `-e` AS that suffix and dies with
  #     "sed: -e: No such file or directory". Redirecting to a temp file
  #     needs no -i at all and behaves identically on BSD and GNU.
  #   - `cp` first, edit second is not atomic. Under `set -e` a failing sed
  #     aborted the script having ALREADY created .env, holding the
  #     template's literal placeholder secret. The next run then took the
  #     "already present — leaving it untouched" branch and booted with it.
  tmp_env="${ENV_FILE}.tmp.$$"
  sed \
    -e 's/^CORTEXSIM_ENV=.*/CORTEXSIM_ENV=development/' \
    -e "s|^CORTEXSIM_SECRET=.*|CORTEXSIM_SECRET=${SECRET}|" \
    "${ENV_EXAMPLE}" > "${tmp_env}"
  chmod 600 "${tmp_env}" 2>/dev/null || true
  mv "${tmp_env}" "${ENV_FILE}"
  log "Wrote ${ENV_FILE} with a freshly generated CORTEXSIM_SECRET."
fi

# ---------------------------------------------------------------------------
# 1b. Validate the secret we are about to boot with — whether this run
#     generated it or inherited an existing .env. docker-compose.yml's
#     ${CORTEXSIM_SECRET:?...} guard only asserts NON-EMPTY, so
#     .env.example's literal "replace-me-with-a-generated-32plus-byte-secret"
#     satisfies it and the stack comes up encrypting the integration
#     credential vault — which holds customer Cortex tenant API keys — with a
#     value that is committed, public, and identical for every operator.
#     Refuse, and print the exact fix.
# ---------------------------------------------------------------------------
CURRENT_SECRET="$(sed -n 's/^CORTEXSIM_SECRET=//p' "${ENV_FILE}" | head -n1)"
if [[ -z "${CURRENT_SECRET}" || "${CURRENT_SECRET}" == replace-me-* || ${#CURRENT_SECRET} -lt 32 ]]; then
  err "${ENV_FILE} carries a placeholder or too-short CORTEXSIM_SECRET."
  err "That value encrypts the integration-credential vault, so it must not be"
  err "the template default. Regenerate it with:"
  err "    rm ${ENV_FILE} && scripts/dev-up.sh"
  exit 1
fi

# Read CORTEXSIM_PORT from .env for the health-check URL (default 8888).
PORT="$(sed -n 's/^CORTEXSIM_PORT=\([0-9][0-9]*\).*/\1/p' "${ENV_FILE}" | head -n1)"
PORT="${PORT:-8888}"

# CORTEXSIM_VERSION drives the versioned image tag + container name
# (cortex-pov-engine-simcore-v<version>). Compose defaults it to 1.0.0.
VERSION="$(sed -n 's/^CORTEXSIM_VERSION=//p' "${ENV_FILE}" | head -n1)"
VERSION="${VERSION:-1.0.0}"

# ---------------------------------------------------------------------------
# 2b. Adapter-source preflight (non-fatal).
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
# 3. Bring the stack up.
# ---------------------------------------------------------------------------
if docker compose version >/dev/null 2>&1; then
  DC=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  DC=(docker-compose)
else
  err "Docker Compose not found. Install Docker Desktop or the compose plugin."
  exit 1
fi

log "Version ${VERSION}: image cortex-pov-engine-simcore:${VERSION}, container cortex-pov-engine-simcore-v${VERSION}"
log "Building and starting SimCore: ${DC[*]} up -d --build"
"${DC[@]}" up -d --build

# ---------------------------------------------------------------------------
# 4. Poll health until it responds (timeout ~90s). "ok" and "degraded" both
#    mean the app came up — see the header comment. Only silence times out.
# ---------------------------------------------------------------------------

# Print each degraded component's code + detail from a /api/health body.
# python3 (exact, key-order-independent) when available; otherwise point the
# operator at the URL rather than guess with fragile text parsing.
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
# (`^{"status":...`) rather than scanned greedily — a plain `.*"status":...`
# scan matches the LAST "status" key in the blob, which is a nested
# component's, not the top-level one, on every response that has more than
# one component (i.e. every real response). Verified: an unanchored version
# of this extractor reported "ok" against a live "degraded" response.
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
        printf '\n  \033[0;32m✓ CortexSim is up:\033[0m  http://localhost:%s\n\n' "${PORT}"
        exit 0
        ;;
      degraded)
        warn "SimCore booted but reports DEGRADED — this can be normal on first boot (e.g. no agents enrolled yet)."
        print_degraded_components "${BODY}"
        printf '\n  \033[1;33m⚠ CortexSim is up (degraded):\033[0m  http://localhost:%s\n'  "${PORT}"
        printf '    Full detail: curl -s %s | python3 -m json.tool\n\n' "${HEALTH_URL}"
        exit 0
        ;;
    esac
  fi
  if (( $(date +%s) >= deadline )); then
    err "Timed out waiting for ${HEALTH_URL} after 90s — no response at all."
    err "Check logs with: ${DC[*]} logs -f simcore"
    exit 1
  fi
  sleep 2
done

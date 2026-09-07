#!/bin/bash
# ==============================================================================
# session-start.sh — provision a Claude Code on the web session for CortexSim
#
# A fresh cloud session clones this repo WITHOUT submodules and with none of the
# Python or Node dependencies installed. Every one of the gates below then fails
# for an environmental reason that looks exactly like a code defect:
#
#   make check-adapters     FAIL  tier-2 submodule source missing on disk
#                                 (GAP-ADAPT-01 guard — 10 uninitialised
#                                  submodules under sources/)
#   make validate-detection FAIL  env: Install jsonschema>=4.18
#   make e2e-tierc          FAIL  No module named pytest
#   pytest tests/           FAIL  No module named fastapi
#   cd ui && vitest         FAIL  no node_modules
#
# Diagnosing those costs a chunk of every session, and the failure mode is worse
# than the cost: "check-adapters is red" reads as a corpus regression, not as a
# clone that never ran `git submodule update`.
#
# Synchronous on purpose. The alternative — async — starts the session sooner
# but lets the agent run a gate before its dependencies exist, which reproduces
# the exact confusion this hook removes.
#
# Idempotent: every step is a no-op once satisfied, so `resume`/`clear`/`compact`
# re-fires are cheap.
# ==============================================================================
set -uo pipefail   # NOT -e: a best-effort step must not abort provisioning

# Web sessions only. A local checkout already has its toolchain.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

ROOT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
cd "$ROOT" || exit 0

log() { printf '[session-start] %s\n' "$1"; }

# ------------------------------------------------------------------------------
# 1. Git submodules — REQUIRED by make check-adapters (tier-2 sources)
# ------------------------------------------------------------------------------
# Shallow: atomic-red-team alone is large and no gate reads submodule history.
if [ -f .gitmodules ]; then
  if [ -z "$(find sources -maxdepth 2 -name .git -print -quit 2>/dev/null)" ]; then
    log "initialising git submodules (shallow)"
    git submodule update --init --recursive --depth 1 >/dev/null 2>&1 \
      && log "submodules ready" \
      || log "WARN submodule init failed — 'make check-adapters' will report missing tier-2 sources"
  else
    log "submodules already present"
  fi
fi

# ------------------------------------------------------------------------------
# 2. Python — SimCore runtime deps + the test/validator toolchain
# ------------------------------------------------------------------------------
# --ignore-installed cryptography: the base image carries a Debian-packaged
# cryptography with no RECORD file, so a plain install aborts with
# "Cannot uninstall cryptography 41.0.7, RECORD file not found".
if ! python3 -c 'import fastapi, sqlalchemy, jsonschema, pytest' >/dev/null 2>&1; then
  log "installing Python dependencies"
  pip install --quiet --ignore-installed cryptography -r core/requirements.txt >/dev/null 2>&1
  pip install --quiet 'jsonschema>=4.18' pytest pytest-asyncio httpx anyio pyyaml >/dev/null 2>&1
  if python3 -c 'import fastapi, sqlalchemy, jsonschema, pytest' >/dev/null 2>&1; then
    log "python deps ready"
  else
    log "WARN python deps incomplete — pytest / validate-detection may fail"
  fi
else
  log "python deps already present"
fi

# ------------------------------------------------------------------------------
# 3. Node — the UI suite (vitest) and `vite build`
# ------------------------------------------------------------------------------
# `npm install` rather than `npm ci`: the container image is cached after this
# hook, and install reuses that cache across sessions where ci would not.
if [ -f ui/package.json ] && [ ! -d ui/node_modules ]; then
  log "installing ui dependencies"
  ( cd ui && npm install --no-audit --no-fund >/dev/null 2>&1 ) \
    && log "ui deps ready" \
    || log "WARN npm install failed — the ui suite will not run"
else
  log "ui deps already present"
fi

# ------------------------------------------------------------------------------
# 4. Docker — best effort, and honest about why it may not help
# ------------------------------------------------------------------------------
# The image-parity gates (make build, check-refs, check-agent-shelf,
# check-ground-truth, check-rust-shelf) all need a built image. The daemon is
# not running by default and does not inherit HTTPS_PROXY, so it egresses
# direct. Starting it here is still only half the story: pulling any base image
# requires that the egress policy allow Docker Hub's blob CDN
# (production.cloudfront.docker.com). Where that is denied, `docker pull`
# returns 403 and every image gate is BLOCKED — which is NOT the same as passing.
# `scripts/launch-preflight.sh` reports that distinction explicitly.
if command -v docker >/dev/null 2>&1 && ! docker info >/dev/null 2>&1; then
  if [ -n "${HTTPS_PROXY:-}" ]; then
    mkdir -p /root/.docker
    cat > /root/.docker/config.json <<EOF
{ "proxies": { "default": { "httpsProxy": "${HTTPS_PROXY}", "httpProxy": "${HTTPS_PROXY}", "noProxy": "localhost,127.0.0.1" } } }
EOF
  fi
  log "starting dockerd (best effort)"
  ( HTTPS_PROXY="${HTTPS_PROXY:-}" HTTP_PROXY="${HTTPS_PROXY:-}" \
    NO_PROXY="localhost,127.0.0.1" nohup dockerd >/tmp/dockerd.log 2>&1 & ) >/dev/null 2>&1
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    docker info >/dev/null 2>&1 && break
    sleep 1
  done
  docker info >/dev/null 2>&1 \
    && log "dockerd up (image pulls still require Docker Hub egress)" \
    || log "dockerd unavailable — image-parity gates will report BLOCKED"
fi

# ------------------------------------------------------------------------------
# 5. Session environment
# ------------------------------------------------------------------------------
# Every backend invocation in this repo needs both of these. Exporting them once
# means `pytest tests/` works instead of only the fully-prefixed form.
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  {
    echo "export CORTEXSIM_BASE_DIR=\"$ROOT\""
    echo "export PYTHONPATH=\"$ROOT/core\""
    echo "export CORTEXSIM_ENV=development"
  } >> "$CLAUDE_ENV_FILE"
  log "exported CORTEXSIM_BASE_DIR, PYTHONPATH, CORTEXSIM_ENV"
fi

log "provisioning complete"
exit 0

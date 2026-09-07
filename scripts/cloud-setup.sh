#!/usr/bin/env bash
# CortexSim — Claude Code CLOUD ENVIRONMENT setup script.
#
# Paste `bash scripts/cloud-setup.sh` into the Setup script field of the cloud
# environment at claude.ai/code (see docs/reference/cloud-environment.md for the
# other fields). Keeping the body here rather than in the web form means it is
# reviewed, diffed and versioned like everything else — a setup script that
# lives only in a text box drifts from the repo it provisions.
#
# CONSTRAINTS THIS SCRIPT IS WRITTEN AROUND (Anthropic-documented, 2026-09-06):
#   * Ubuntu 24.04, runs as root, ~5 MINUTE TIMEOUT, must exit 0.
#   * Result is cached per environment for ~7 days, then re-runs.
#   * 4 vCPU / 16 GB RAM / 30 GB disk.
#   * Egress goes through Anthropic's filtering proxy. Default "Trusted" covers
#     package registries + GitHub; anything else needs the Custom allowlist.
#
# The five-minute budget is the binding constraint and dictates what is NOT
# here: no `docker compose up --build`. This repo's image has four builder
# stages (python, go beacon matrix, rust musl tools, vite) and takes minutes on
# its own, so building it here would time out the environment and block every
# session. The image is built inside the session instead, on demand.
#
# Everything below is idempotent and non-fatal where it can be: a cloud session
# that comes up with three of four toolchains is far more useful than one that
# refuses to start. Anything genuinely required is verified at the end and
# reported as a single explicit summary rather than a silent partial success.
set -uo pipefail

log()  { printf '\n[cloud-setup] %s\n' "$*"; }
warn() { printf '[cloud-setup] WARN: %s\n' "$*" >&2; }

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT" || { warn "cannot cd to $REPO_ROOT"; exit 0; }
log "repo: $REPO_ROOT"

# ── Python 3.11 ───────────────────────────────────────────────────────────────
# The cloud image documents "Python 3.x" and does NOT pin a minor version, but
# this repo is not version-agnostic: sqlalchemy 2.0.30 does not import on 3.14
# (that is why the local dev loop needs a .venv311), and CI pins 3.11. Rather
# than hope the image's default matches, resolve 3.11 explicitly.
#
# `uv` is pre-installed in the cloud image and fetches a standalone 3.11 in
# seconds; apt cannot (Ubuntu 24.04 ships 3.12 and has no 3.11 without a PPA,
# which would cost minutes of the five-minute budget).
PY=""
if command -v uv >/dev/null 2>&1; then
  log "installing Python 3.11 via uv"
  uv python install 3.11 >/dev/null 2>&1 || warn "uv python install 3.11 failed"
  PY="$(uv python find 3.11 2>/dev/null || true)"
fi
if [ -z "$PY" ] && command -v python3.11 >/dev/null 2>&1; then
  PY="$(command -v python3.11)"
fi
if [ -z "$PY" ]; then
  warn "no Python 3.11 — falling back to python3 ($(python3 -V 2>&1)); sqlalchemy 2.0.30 may not import"
  PY="$(command -v python3 || true)"
fi
log "python: ${PY:-NONE} ($("${PY:-true}" -V 2>&1 || echo unknown))"

# ── Python dependencies ───────────────────────────────────────────────────────
if [ -n "$PY" ]; then
  log "installing backend + test dependencies"
  "$PY" -m pip install --upgrade pip --quiet 2>/dev/null || warn "pip upgrade failed"
  "$PY" -m pip install --quiet -r core/requirements.txt || warn "core/requirements.txt install failed"
  # Not in requirements.txt because they are test-only, but every gate needs
  # them: pyyaml in particular is what the scenario-corpus tests load through,
  # and without it every one dies on ModuleNotFoundError.
  "$PY" -m pip install --quiet pytest pytest-asyncio httpx pyyaml || warn "test deps install failed"
fi

# ── Node 20 ───────────────────────────────────────────────────────────────────
# The image ships Node 20, 21 and 22 with 22 on PATH; CI builds on 20 and the
# lockfile is resolved against it. Select 20 when a version manager exposes it,
# but do not fail the environment over it — the bundle builds on 22 too.
if command -v nvm >/dev/null 2>&1 || [ -s "${NVM_DIR:-$HOME/.nvm}/nvm.sh" ]; then
  # shellcheck disable=SC1091
  . "${NVM_DIR:-$HOME/.nvm}/nvm.sh" 2>/dev/null || true
  nvm use 20 >/dev/null 2>&1 || warn "nvm could not select Node 20; using $(node -v 2>/dev/null)"
fi
log "node: $(node -v 2>/dev/null || echo NONE) / npm $(npm -v 2>/dev/null || echo NONE)"

if [ -f ui/package-lock.json ]; then
  log "installing UI dependencies (npm ci)"
  # PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD mirrors both CI jobs: @playwright/test's
  # postinstall pulls ~200 MB from the Playwright CDN, which is not on the
  # default Trusted allowlist and is the single most likely thing to make this
  # script exceed its timeout or fail outright. Browsers are fetched on demand
  # by the layout guard instead (see the note at the end).
  ( cd ui && PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 PUPPETEER_SKIP_DOWNLOAD=1 npm ci --no-audit --no-fund ) \
    || warn "npm ci failed — the UI suite and the layout guard will not run"
fi

# ── Go ────────────────────────────────────────────────────────────────────────
# The beacon needs 1.21+. The cloud image documents only "Go 1.x", so check
# rather than assume, and report loudly instead of failing a whole environment
# over a toolchain only the agent tests need.
if command -v go >/dev/null 2>&1; then
  GO_VER="$(go version | awk '{print $3}' | sed 's/^go//')"
  log "go: $GO_VER"
  # 1.21 is the floor (agent/go.mod). Compare major.minor numerically.
  if [ "$(printf '%s\n1.21\n' "$GO_VER" | sort -V | head -1)" != "1.21" ]; then
    warn "go $GO_VER is BELOW the 1.21 floor in agent/go.mod — the agent suite will not build"
  fi
else
  warn "no Go toolchain — the agent suite and the cross-compile gate cannot run"
fi

# ── Docker ────────────────────────────────────────────────────────────────────
# Documented as available (docker + docker compose). Verified, not assumed: a
# large part of this repo's gates (make build / test-backend / check-*-shelf /
# the compose stack) are docker-only, and knowing that up front is worth more
# than discovering it mid-task.
if docker info >/dev/null 2>&1; then
  log "docker: $(docker --version 2>/dev/null); compose: $(docker compose version --short 2>/dev/null || echo NONE)"
else
  warn "docker daemon unreachable — make build / test-backend / the compose stack are unavailable"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
# One explicit block rather than a silent exit: the whole point of this repo's
# health surface is that a degraded state must never read as a healthy one, and
# the same rule applies to its own provisioning.
log "environment summary"
printf '  python : %s\n' "$("${PY:-true}" -V 2>&1 || echo NONE)"
printf '  node   : %s\n' "$(node -v 2>/dev/null || echo NONE)"
printf '  go     : %s\n' "$(go version 2>/dev/null | awk '{print $3}' || echo NONE)"
printf '  docker : %s\n' "$(docker --version 2>/dev/null || echo NONE)"
printf '  ui deps: %s\n' "$([ -d ui/node_modules ] && echo installed || echo MISSING)"
cat <<'NOTE'

  Not done here, on purpose:
    * The SimCore image is NOT built (four builder stages would exceed the
      ~5 min setup timeout). Build it in-session:  make build   /   make up
    * Playwright browsers are NOT downloaded (~200 MB, CDN not on the default
      Trusted allowlist). The layout guard fetches them when it runs:
        cd ui && npx playwright install --with-deps chromium
      If that is blocked, add the Playwright CDN to the environment's Custom
      allowed-domains list (see docs/reference/cloud-environment.md).
    * CORTEXSIM_SECRET is NOT generated here — it must be identical across
      sessions or the encrypted credential vault cannot be decrypted. Set it on
      the environment instead (same doc).
NOTE

# Always succeed: a non-zero exit blocks the environment from starting at all,
# which is a worse failure than starting with a warned-about gap that the
# summary above makes visible.
exit 0

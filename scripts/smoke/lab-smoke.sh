#!/usr/bin/env bash
# ==============================================================================
# CortexSim — lab-smoke.sh
#
# One-command smoke harness for verifying a CortexSim lab deployment.
#
# Usage:
#   scripts/smoke/lab-smoke.sh                 # local docker compose
#   scripts/smoke/lab-smoke.sh --target=local
#   scripts/smoke/lab-smoke.sh --target=jumpbox --url=https://jumpbox.lab:8888
#   scripts/smoke/lab-smoke.sh --keep          # don't tear compose down on exit
#   scripts/smoke/lab-smoke.sh --strategy=synthetic|structural|cortex_xql
#
# What it does:
#   1. Picks a target (local compose vs. existing jumpbox URL)
#   2. For local: docker compose up -d, waits for /api/health green
#   3. Runs tests/smoke/ via pytest
#   4. For local: docker compose down (unless --keep)
#   5. Reports pass/fail with the strategy that was applied
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

# -----------------------------------------------------------------------------
# Argument parsing
# -----------------------------------------------------------------------------
TARGET="local"
URL=""
KEEP=0
STRATEGY="${CORTEXSIM_OBSERVATION_STRATEGY:-synthetic}"
PYTEST_ARGS=()

for arg in "$@"; do
    case "$arg" in
        --target=*) TARGET="${arg#*=}" ;;
        --url=*)    URL="${arg#*=}" ;;
        --keep)     KEEP=1 ;;
        --strategy=*) STRATEGY="${arg#*=}" ;;
        --help|-h)
            head -30 "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) PYTEST_ARGS+=("$arg") ;;
    esac
done

# -----------------------------------------------------------------------------
# Resolve SimCore URL
# -----------------------------------------------------------------------------
case "$TARGET" in
    local)
        URL="${URL:-http://localhost:8888}"
        ;;
    jumpbox)
        if [[ -z "$URL" ]]; then
            echo "ERROR: --target=jumpbox requires --url=https://..." >&2
            exit 2
        fi
        ;;
    *)
        echo "ERROR: --target must be 'local' or 'jumpbox', got '$TARGET'" >&2
        exit 2
        ;;
esac

export CORTEXSIM_SMOKE_URL="$URL"
export CORTEXSIM_OBSERVATION_STRATEGY="$STRATEGY"

echo "[lab-smoke] target=$TARGET  url=$URL  strategy=$STRATEGY"

# -----------------------------------------------------------------------------
# Bring up local stack if needed
# -----------------------------------------------------------------------------
COMPOSE_STARTED=0
if [[ "$TARGET" == "local" ]]; then
    if ! curl -fsS "$URL/api/health" >/dev/null 2>&1; then
        echo "[lab-smoke] SimCore not running locally — starting docker compose"
        docker compose up -d --build
        COMPOSE_STARTED=1
    else
        echo "[lab-smoke] SimCore already healthy at $URL — not touching compose"
    fi
fi

# -----------------------------------------------------------------------------
# Always teardown what we started, even on test failure
# -----------------------------------------------------------------------------
cleanup() {
    local rc=$?
    if [[ $COMPOSE_STARTED -eq 1 && $KEEP -eq 0 ]]; then
        echo "[lab-smoke] tearing down docker compose"
        docker compose down || true
    elif [[ $COMPOSE_STARTED -eq 1 && $KEEP -eq 1 ]]; then
        echo "[lab-smoke] --keep set, leaving compose up"
    fi
    if [[ $rc -eq 0 ]]; then
        echo "[lab-smoke] ✓ all smoke tests passed"
    else
        echo "[lab-smoke] ✗ smoke failed with rc=$rc"
    fi
    return $rc
}
trap cleanup EXIT

# -----------------------------------------------------------------------------
# Resolve a Python interpreter with the test deps installed
# -----------------------------------------------------------------------------
PY=""
if [[ -x "$REPO_ROOT/.venv/bin/pytest" ]]; then
    PY="$REPO_ROOT/.venv/bin/python"
elif command -v pytest >/dev/null 2>&1; then
    PY="$(command -v python3)"
else
    echo "[lab-smoke] No pytest found — bootstrapping a venv"
    python3 -m venv "$REPO_ROOT/.venv"
    "$REPO_ROOT/.venv/bin/pip" install -q --upgrade pip
    "$REPO_ROOT/.venv/bin/pip" install -q -r "$REPO_ROOT/core/requirements.txt"
    "$REPO_ROOT/.venv/bin/pip" install -q pytest pytest-asyncio httpx
    PY="$REPO_ROOT/.venv/bin/python"
fi

# -----------------------------------------------------------------------------
# Run the smoke tests
# -----------------------------------------------------------------------------
"$PY" -m pytest tests/smoke -v --tb=short "${PYTEST_ARGS[@]}"

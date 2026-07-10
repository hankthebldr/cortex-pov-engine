#!/usr/bin/env bash
# ===========================================================================
# run-tier-c.sh  —  CortexSim Tier-C isolated execution operator entrypoint
# ---------------------------------------------------------------------------
# Runs a CortexSim push bundle inside an ephemeral, audited, network-sinkholed
# container and collects ground-truth telemetry — proving "the right binaries
# fire under the right identities" WITHOUT touching a customer endpoint.
#
# e2e execution methodology, Tier C, increment 1 (infra).
# See deploy/tier-c/README.md and docs/design/e2e-execution-methodology.md.
#
# USAGE
#   # From a local bundle file:
#   deploy/tier-c/run-tier-c.sh --bundle /path/to/bundle.sh
#
#   # Fetch the bundle from a running SimCore by scenario id:
#   deploy/tier-c/run-tier-c.sh --scenario SIM-EDR-001 \
#       [--simcore http://localhost:8888] [--format bash]
#
#   # Where to drop results (default: deploy/tier-c/results/<run-id>):
#   deploy/tier-c/run-tier-c.sh --bundle b.sh --results /tmp/tier-c-out
#
#   # Keep the stack up for inspection instead of tearing down:
#   deploy/tier-c/run-tier-c.sh --bundle b.sh --keep
#
# OUTPUT (under the results dir)
#   audit.log               raw auditd log (ground truth)
#   observed-signals.json   structured summary keyed by syscall class
#   bundle-stdout.log       the bundle's own stdout/stderr
#   sinkhole/http.jsonl     every HTTP(S) request the bundle made (shape)
#
# ISOLATION GUARANTEES
#   - No external egress: runner + sinkhole live on an internal Docker bridge
#     with no gateway; DNS resolves every name to the sinkhole.
#   - Ephemeral: containers are torn down on exit (unless --keep).
#   - Audited: auditd captures execve / setuid / connect / credential reads.
# ===========================================================================
set -euo pipefail

# --- locate ourselves so paths work from any CWD --------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.tier-c.yml"
PROJECT_NAME="cortexsim-tier-c"

# --- defaults -------------------------------------------------------------
BUNDLE=""
SCENARIO=""
SIMCORE="${CORTEXSIM_SIMCORE_URL:-http://localhost:8888}"
FORMAT="bash"
RESULTS_DIR=""
KEEP=0
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$"

usage() {
    sed -n '2,40p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit "${1:-0}"
}

# --- arg parse ------------------------------------------------------------
while [ $# -gt 0 ]; do
    case "$1" in
        --bundle)   BUNDLE="${2:-}"; shift 2 ;;
        --scenario) SCENARIO="${2:-}"; shift 2 ;;
        --simcore)  SIMCORE="${2:-}"; shift 2 ;;
        --format)   FORMAT="${2:-}"; shift 2 ;;
        --results)  RESULTS_DIR="${2:-}"; shift 2 ;;
        --keep)     KEEP=1; shift ;;
        -h|--help)  usage 0 ;;
        *) echo "unknown argument: $1" >&2; usage 1 ;;
    esac
done

die() { echo "[run-tier-c] ERROR: $*" >&2; exit 1; }
log() { echo "[run-tier-c] $*" >&2; }

command -v docker >/dev/null 2>&1 || die "docker not found on PATH"

# docker compose v2 (plugin) vs legacy docker-compose
if docker compose version >/dev/null 2>&1; then
    DC=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
    DC=(docker-compose)
else
    die "neither 'docker compose' nor 'docker-compose' is available"
fi

# --- resolve results dir --------------------------------------------------
if [ -z "$RESULTS_DIR" ]; then
    RESULTS_DIR="$SCRIPT_DIR/results/$RUN_ID"
fi
mkdir -p "$RESULTS_DIR"
log "results -> $RESULTS_DIR"

# --- stage the bundle into a clean dir the compose file mounts ------------
STAGE_DIR="$SCRIPT_DIR/.bundle-stage/$RUN_ID"
mkdir -p "$STAGE_DIR"

cleanup() {
    if [ "$KEEP" -eq 1 ]; then
        log "--keep set: leaving stack up. Tear down with:"
        log "  ${DC[*]} -p $PROJECT_NAME -f $COMPOSE_FILE --profile tier-c down -v"
    else
        log "tearing down isolated stack"
        "${DC[@]}" -p "$PROJECT_NAME" -f "$COMPOSE_FILE" --profile tier-c \
            down -v --remove-orphans >/dev/null 2>&1 || true
    fi
    rm -rf "$STAGE_DIR" 2>/dev/null || true
}
trap cleanup EXIT

if [ -n "$SCENARIO" ]; then
    [ -z "$BUNDLE" ] || die "pass --bundle OR --scenario, not both"
    log "fetching bundle for scenario=$SCENARIO from $SIMCORE (format=$FORMAT)"
    command -v curl >/dev/null 2>&1 || die "curl required for --scenario"
    URL="$SIMCORE/api/scenarios/$SCENARIO/download?format=$FORMAT"
    HTTP_CODE="$(curl -sS -o "$STAGE_DIR/bundle.sh" -w '%{http_code}' "$URL" || echo 000)"
    [ "$HTTP_CODE" = "200" ] || die "SimCore returned HTTP $HTTP_CODE for $URL"
    [ -s "$STAGE_DIR/bundle.sh" ] || die "fetched bundle is empty"
elif [ -n "$BUNDLE" ]; then
    [ -f "$BUNDLE" ] || die "bundle file not found: $BUNDLE"
    cp "$BUNDLE" "$STAGE_DIR/bundle.sh"
else
    die "specify --bundle <file> or --scenario <id>"
fi

# Sanity: the staged bundle must parse as bash before we waste a container.
if ! bash -n "$STAGE_DIR/bundle.sh"; then
    die "staged bundle failed 'bash -n' — refusing to run a malformed bundle"
fi
log "staged bundle: $STAGE_DIR/bundle.sh ($(wc -l < "$STAGE_DIR/bundle.sh") lines)"

# --- run the isolated stack ----------------------------------------------
# The compose file bind-mounts $CORTEXSIM_BUNDLE_PATH -> /bundle (ro) and
# ./results -> /results in each container. We point both at this run's dirs.
export CORTEXSIM_BUNDLE_PATH="$STAGE_DIR"
export CORTEXSIM_BUNDLE_FILE="/bundle/bundle.sh"

# Compose resolves the relative ./results in the file against the compose
# file's dir; symlink this run's results dir into place so output lands there.
COMPOSE_RESULTS="$SCRIPT_DIR/results"
mkdir -p "$COMPOSE_RESULTS"

log "building + starting isolated stack (this may take a moment on first run)"
RUN_RC=0
"${DC[@]}" -p "$PROJECT_NAME" -f "$COMPOSE_FILE" --profile tier-c \
    up --build --abort-on-container-exit --exit-code-from runner \
    || RUN_RC=$?

# --- collect output -------------------------------------------------------
# The runner writes to the shared ./results bind mount. Copy this run's files
# out into the per-run results dir for a clean, self-contained artifact set.
for f in observed-signals.json audit.log bundle-stdout.log; do
    if [ -f "$COMPOSE_RESULTS/$f" ]; then
        cp "$COMPOSE_RESULTS/$f" "$RESULTS_DIR/$f"
    fi
done
if [ -f "$COMPOSE_RESULTS/sinkhole/http.jsonl" ]; then
    mkdir -p "$RESULTS_DIR/sinkhole"
    cp "$COMPOSE_RESULTS/sinkhole/http.jsonl" "$RESULTS_DIR/sinkhole/http.jsonl"
fi

if [ -f "$RESULTS_DIR/observed-signals.json" ]; then
    log "observed-signals.json:"
    if command -v jq >/dev/null 2>&1; then
        jq . "$RESULTS_DIR/observed-signals.json" >&2 || cat "$RESULTS_DIR/observed-signals.json" >&2
    else
        cat "$RESULTS_DIR/observed-signals.json" >&2
    fi
else
    log "WARNING: no observed-signals.json produced — check stack logs"
fi

log "Tier-C run complete (runner exit=$RUN_RC). Artifacts in $RESULTS_DIR"
exit "$RUN_RC"

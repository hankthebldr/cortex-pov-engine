#!/usr/bin/env bash
# ==============================================================================
# CortexSim installer preflight
#
# Read-only checker that verifies a host is *ready to run* install.sh OR
# already has a healthy CortexSim deployment.  Touches nothing — safe to
# run repeatedly and against production jumpboxes.
#
# Two modes:
#   ./preflight.sh prereqs   # before install: deps + OS + disk + network egress
#   ./preflight.sh installed # after install:  artefacts + compose up + /health
#   ./preflight.sh both      # default — runs both passes and reports gaps
#
# Exit code 0 = all checks passed; non-zero = something needs attention.
# ==============================================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# -----------------------------------------------------------------------------
MODE="${1:-both}"
FAILED=0
WARNINGS=0

ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; WARNINGS=$((WARNINGS+1)); }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$*"; FAILED=$((FAILED+1)); }
note() { printf '    %s\n' "$*"; }
hdr()  { printf '\n\033[1m== %s ==\033[0m\n' "$*"; }

# -----------------------------------------------------------------------------
check_os() {
    hdr "Host"
    if [[ "$(uname -s)" == "Linux" ]]; then
        local distro=""
        if [[ -r /etc/os-release ]]; then
            distro="$(. /etc/os-release && echo "${PRETTY_NAME:-${NAME:-Linux}}")"
        fi
        ok "Linux: ${distro:-unknown}"
    elif [[ "$(uname -s)" == "Darwin" ]]; then
        warn "macOS detected — install.sh targets Ubuntu/Debian; expect manual deps"
    else
        bad "unsupported OS: $(uname -s)"
    fi

    local arch
    arch="$(uname -m)"
    case "$arch" in
        x86_64|amd64|aarch64|arm64) ok "arch: $arch" ;;
        *) warn "unusual arch '$arch' — submodule Rust tools may fail to build" ;;
    esac
}

check_prereq_commands() {
    hdr "Prerequisite commands (for ./install.sh)"
    local need=(git curl bash awk grep sed)
    for c in "${need[@]}"; do
        if command -v "$c" >/dev/null 2>&1; then
            ok "$c"
        else
            bad "$c missing"
        fi
    done

    # Optional but used by various steps
    for c in docker docker-compose go cargo node npm python3; do
        if command -v "$c" >/dev/null 2>&1; then
            ok "$c ($($c --version 2>/dev/null | head -1 || echo present))"
        else
            warn "$c missing — install.sh will attempt to install it"
        fi
    done
}

check_disk_and_memory() {
    hdr "Capacity"
    if command -v df >/dev/null 2>&1; then
        local avail_kb
        avail_kb="$(df -k "$REPO_ROOT" | awk 'NR==2 {print $4}')"
        if [[ -n "$avail_kb" && "$avail_kb" -gt 10485760 ]]; then
            ok "free disk in repo dir: $((avail_kb / 1024 / 1024)) GiB"
        else
            warn "less than 10 GiB free in $REPO_ROOT (have $((avail_kb/1024/1024)) GiB) — submodule builds + images need ~8 GiB"
        fi
    fi
    if [[ -r /proc/meminfo ]]; then
        local mem_mb
        mem_mb="$(awk '/^MemTotal:/ {print int($2/1024)}' /proc/meminfo)"
        if (( mem_mb >= 3500 )); then
            ok "RAM: ${mem_mb} MiB"
        else
            warn "RAM ${mem_mb} MiB — Rust submodules need ~4 GiB to compile"
        fi
    fi
}

check_network() {
    hdr "Network egress (passive — uses HEAD requests)"
    for host in github.com proxy.golang.org pypi.org registry.npmjs.org sh.rustup.rs ghcr.io; do
        if curl -sfI -m 5 "https://$host" -o /dev/null 2>&1; then
            ok "https://$host reachable"
        else
            warn "https://$host NOT reachable — corresponding install step will fail"
        fi
    done
}

check_repo_state() {
    hdr "Repository state"
    if [[ -d "$REPO_ROOT/.git" ]]; then
        ok "$REPO_ROOT is a git repo"
        local missing=0
        if [[ -f "$REPO_ROOT/.gitmodules" ]]; then
            while IFS= read -r path; do
                if [[ ! -e "$REPO_ROOT/$path/.git" && -z "$(ls -A "$REPO_ROOT/$path" 2>/dev/null)" ]]; then
                    warn "submodule not initialised: $path"
                    missing=$((missing+1))
                fi
            done < <(awk '/path = / { print $3 }' "$REPO_ROOT/.gitmodules")
            [[ $missing -eq 0 ]] && ok "all git submodules initialised"
        fi
    else
        bad "$REPO_ROOT is not a git repo — install.sh expects a clone"
    fi
}

check_installed_artefacts() {
    hdr "Built artefacts (after install.sh runs)"
    [[ -x "$REPO_ROOT/bin/cortexsim-agent" ]] \
        && ok "bin/cortexsim-agent compiled" \
        || warn "bin/cortexsim-agent missing — run install.sh or 'cd agent && go build -o ../bin/cortexsim-agent .'"

    [[ -d "$REPO_ROOT/ui/dist" ]] \
        && ok "ui/dist (Vite build) present" \
        || warn "ui/dist missing — run 'cd ui && npm install && npm run build'"

    [[ -d "$REPO_ROOT/core/static" ]] \
        && ok "core/static present (UI mounted to FastAPI)" \
        || warn "core/static missing — UI won't be served"

    # Spot-check Rust tools.  They're optional unless the customer scenario
    # selects them; only warn.
    for tool in signalbench ackbarx xdrtop; do
        if [[ -x "$REPO_ROOT/sources/$tool/target/release/$tool" ]]; then
            ok "$tool built"
        else
            warn "$tool not built — only matters if scenarios reference it"
        fi
    done
}

check_simcore_running() {
    hdr "SimCore service"
    local url="${CORTEXSIM_BASE_URL:-http://localhost:8888}"
    if curl -fsS -m 5 "$url/api/health" -o /tmp/_simcore_pf 2>/dev/null; then
        ok "$url/api/health responding: $(cat /tmp/_simcore_pf)"
    else
        warn "SimCore not responding at $url — start with 'docker compose up -d --build'"
        return
    fi

    # If it's up, sanity-check the catalog
    local count
    count="$(curl -fsS "$url/api/scenarios" 2>/dev/null | python3 -c 'import sys,json;d=json.load(sys.stdin);print(d.get("total", len(d.get("scenarios",[]))))' 2>/dev/null)"
    if [[ -n "$count" && "$count" -gt 0 ]]; then
        ok "$count scenarios loaded"
    else
        warn "scenarios endpoint returned no entries — YAML loader may have rejected the catalog"
    fi
}

# -----------------------------------------------------------------------------
echo "[preflight] mode=$MODE  repo=$REPO_ROOT"

case "$MODE" in
    prereqs)
        check_os; check_prereq_commands; check_disk_and_memory; check_network; check_repo_state
        ;;
    installed)
        check_installed_artefacts; check_simcore_running
        ;;
    both|"")
        check_os; check_prereq_commands; check_disk_and_memory; check_network; check_repo_state
        check_installed_artefacts; check_simcore_running
        ;;
    *)
        echo "ERROR: unknown mode '$MODE' (prereqs|installed|both)" >&2
        exit 2
        ;;
esac

# -----------------------------------------------------------------------------
echo
if (( FAILED == 0 )); then
    if (( WARNINGS == 0 )); then
        echo "[preflight] ✓ all checks passed"
    else
        echo "[preflight] ✓ pass with $WARNINGS warning(s) — review before deploying"
    fi
    exit 0
else
    echo "[preflight] ✗ $FAILED check(s) failed, $WARNINGS warning(s)"
    exit 1
fi

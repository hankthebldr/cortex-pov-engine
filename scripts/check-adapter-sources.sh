#!/usr/bin/env bash
# ==============================================================================
# check-adapter-sources.sh — Tool-Adapter source existence preflight (GAP-ADAPT-01)
#
# For every adapter pack under tools/packs/*.yml that is:
#   - tier 2 (git submodule)  → verify install.source_path exists on disk
#   - tier 4 (runtime-fetched)→ verify the runtime install target exists on disk
#                               (informational — these are fetched at deploy,
#                                so a miss is WARN, never FAIL)
#
# Exit code:
#   0  — all tier-2 sources present (tier-4 misses are WARN only)
#   1  — at least one tier-2 submodule source_path is missing on disk
#
# Self-contained: pure bash + grep/sed. No yq / python / jq dependency, so it
# runs on a clean Ubuntu 22.04 jumpbox or inside the SimCore image.
#
# Usage:
#   scripts/check-adapter-sources.sh            # auto-detect repo root
#   CORTEXSIM_BASE_DIR=/repo scripts/check-adapter-sources.sh
# ==============================================================================
set -euo pipefail

# ------------------------------------------------------------------------------
# Locate repo root (parent of this script's scripts/ dir), allow override.
# ------------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
REPO_ROOT="${CORTEXSIM_BASE_DIR:-$(cd "${SCRIPT_DIR}/.." && pwd)}"
PACKS_DIR="${REPO_ROOT}/tools/packs"

# ------------------------------------------------------------------------------
# Colors (disabled when not a terminal)
# ------------------------------------------------------------------------------
if [[ -t 1 ]]; then
    RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BOLD='\033[1m'; NC='\033[0m'
else
    RED=''; GREEN=''; YELLOW=''; BOLD=''; NC=''
fi

if [[ ! -d "$PACKS_DIR" ]]; then
    echo -e "${RED}FAIL${NC} packs dir not found: ${PACKS_DIR}" >&2
    echo "  (set CORTEXSIM_BASE_DIR to the repo root)" >&2
    exit 1
fi

# ------------------------------------------------------------------------------
# Minimal flat-YAML field reader. The adapter packs use a simple, predictable
# layout: top-level `tier:` and a nested `install:` block whose children are
# indented two spaces. We only need a handful of scalar fields.
# ------------------------------------------------------------------------------

# Read a top-level scalar (e.g. `tier`, `adapter_id`). Strips quotes + comments.
read_top_field() {
    local file="$1" key="$2"
    grep -E "^${key}:[[:space:]]" "$file" 2>/dev/null \
        | head -1 \
        | sed -E "s/^${key}:[[:space:]]*//; s/[[:space:]]*#.*$//" \
        | sed -E 's/^["'"'"']//; s/["'"'"']$//'
}

# Read a field nested one level under the `install:` block (two-space indent).
# Stops scanning at the next top-level (column-0) key so we don't bleed into
# the `invoke:` block.
read_install_field() {
    local file="$1" key="$2"
    awk -v k="$key" '
        /^install:[[:space:]]*$/ { in_install=1; next }
        in_install && /^[^[:space:]]/ { in_install=0 }
        in_install {
            # match two-space-indented "  key:"
            if ($0 ~ "^  " k ":") {
                line=$0
                sub("^  " k ":[[:space:]]*", "", line)
                sub("[[:space:]]*#.*$", "", line)        # strip trailing comment
                gsub(/^["\x27]|["\x27]$/, "", line)        # strip wrapping quotes
                print line
                exit
            }
        }
    ' "$file"
}

# ------------------------------------------------------------------------------
# Counters + accumulators
# ------------------------------------------------------------------------------
pass=0
warn=0
fail=0
fail_lines=()

echo -e "${BOLD}Tool-Adapter source preflight${NC}  (repo: ${REPO_ROOT})"
echo "--------------------------------------------------------------------------"

shopt -s nullglob
for file in "$PACKS_DIR"/*.yml; do
    base="$(basename "$file")"
    [[ "$base" == _* ]] && continue   # skip _schema.yml et al.

    tier="$(read_top_field "$file" tier)"
    aid="$(read_top_field "$file" adapter_id)"
    [[ -z "$aid" ]] && aid="$base"

    case "$tier" in
        2)
            src="$(read_install_field "$file" source_path)"
            if [[ -z "$src" ]]; then
                fail=$((fail + 1))
                line="${aid} (tier 2): no install.source_path declared"
                fail_lines+=("$line")
                echo -e "  ${RED}FAIL${NC} ${line}"
                continue
            fi
            # resolve relative to repo root
            if [[ "$src" = /* ]]; then resolved="$src"; else resolved="${REPO_ROOT}/${src}"; fi
            if [[ -d "$resolved" ]]; then
                pass=$((pass + 1))
                echo -e "  ${GREEN}PASS${NC} ${aid} (tier 2): ${src} present"
            else
                fail=$((fail + 1))
                line="${aid} (tier 2): submodule source ${src} MISSING — run 'git submodule update --init --recursive'"
                fail_lines+=("$line")
                echo -e "  ${RED}FAIL${NC} ${line}"
            fi
            ;;
        4)
            # Tier-4 tools are fetched at deploy. We can only assert presence of
            # an already-fetched target (binary path or clone dir). A miss is
            # WARN, not FAIL, since the runtime_install_command runs at dispatch.
            bin="$(read_install_field "$file" binary)"
            target=""
            # Prefer an absolute binary path or a /tmp clone dir if we can see one.
            if [[ "$bin" = /* ]]; then
                target="$bin"
            else
                # try to spot a clone dir in the runtime_install_command
                ric="$(read_install_field "$file" runtime_install_command)"
                # extract the last absolute /tmp/... token, if any
                target="$(printf '%s\n' "$ric" | grep -oE '/tmp/[A-Za-z0-9._/-]+' | head -1 || true)"
            fi

            if [[ -n "$target" && ( -e "$target" || -d "$target" ) ]]; then
                pass=$((pass + 1))
                echo -e "  ${GREEN}PASS${NC} ${aid} (tier 4): ${target} already fetched"
            elif [[ -n "$bin" ]] && command -v "$bin" >/dev/null 2>&1; then
                pass=$((pass + 1))
                echo -e "  ${GREEN}PASS${NC} ${aid} (tier 4): '${bin}' on PATH"
            else
                warn=$((warn + 1))
                echo -e "  ${YELLOW}WARN${NC} ${aid} (tier 4): not yet fetched (runtime-install at dispatch)"
            fi
            ;;
        *)
            : # tiers 1/3/5 not in scope for this check
            ;;
    esac
done
shopt -u nullglob

# ------------------------------------------------------------------------------
# Rust tier-2 tools — the three source trees NO adapter pack covers.
#
# signalbench, ackbarx and xdrtop are tier-2 submodules with no tools/packs/*.yml,
# so the loop above never looks at them and this gate was blind to all three.
# That blindness is why signalbench's registry build_cmd ("cargo build --release")
# could be broken for every DC who ever ran it without anything noticing: the
# crate embeds helpers/pacemaker's output via include_bytes! and that helper must
# be built FIRST. build-rust-dist.sh --check-recipe asserts exactly the inputs
# that rot silently, costs ~50 ms, and needs no compiler and no Docker — so it
# belongs in this always-on job rather than behind a paths: filter, where a skip
# would read exactly like a pass.
# ------------------------------------------------------------------------------
RUST_CHECK="${REPO_ROOT}/scripts/build-rust-dist.sh"
echo ""
if [[ ! -x "$RUST_CHECK" ]]; then
    warn=$((warn + 1))
    echo -e "  ${YELLOW}WARN${NC} rust tools: scripts/build-rust-dist.sh not present/executable — recipe gate skipped"
elif [[ ! -d "${REPO_ROOT}/sources" ]]; then
    # e.g. running inside the SimCore image, which does not COPY sources/.
    warn=$((warn + 1))
    echo -e "  ${YELLOW}WARN${NC} rust tools: sources/ absent in this context — recipe gate NOT run (run it from a checkout)"
else
    if "$RUST_CHECK" --check-recipe; then
        pass=$((pass + 1))
    else
        fail=$((fail + 1))
        fail_lines+=("rust tier-2 tools: build-rust-dist.sh --check-recipe failed (see output above)")
    fi
fi
echo ""

echo "--------------------------------------------------------------------------"
echo -e "Summary: ${GREEN}PASS=${pass}${NC}  ${YELLOW}WARN=${warn}${NC}  ${RED}FAIL=${fail}${NC}"

if (( fail > 0 )); then
    echo ""
    echo -e "${RED}${BOLD}FAILED:${NC} ${fail} tier-2 submodule source(s) missing on disk:" >&2
    for l in "${fail_lines[@]}"; do echo -e "  ${RED}-${NC} ${l}" >&2; done
    echo "" >&2
    echo "Tier-2 adapter sources are git submodules. Provision them with:" >&2
    echo "    git submodule update --init --recursive   # (install.sh Step 3)" >&2
    exit 1
fi

echo -e "${GREEN}OK${NC} — all tier-2 adapter sources present (tier-4 fetched at deploy)."
exit 0

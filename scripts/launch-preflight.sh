#!/usr/bin/env bash
# ==============================================================================
# launch-preflight.sh — Gate B readiness for a `dev` -> `main` release merge
#
# CONTRIBUTING.md defines two gates. Gate A (feature -> dev) is per-PR and CI
# already expresses most of it. Gate B (dev -> main) is the one that ships into
# a customer lab, and it is mostly NOT expressible as a single CI job: it spans
# branch topology, version coherence, counted ground truth, and the honesty
# rules in CLAUDE.md §"Gate A5". This script runs it as one command.
#
# It reports THREE outcomes per check, and the distinction is the whole point:
#
#   PASS     — the check ran and the invariant holds.
#   FAIL     — the check ran and the invariant is broken. Blocks the merge.
#   BLOCKED  — the check could NOT run here (missing toolchain, no registry
#              egress, no docker). This is NOT a pass. A gate that cannot run
#              reads exactly like a passing one unless it is named, so every
#              BLOCKED is listed separately and the summary refuses to call the
#              tree releasable while any remain.
#
# That last rule mirrors the repo's own doctrine: "a zero is degraded, not ok",
# and "a job that can be skipped reads exactly like a passing one".
#
# Exit codes:
#   0  — every check PASSed and none were BLOCKED: releasable.
#   1  — at least one FAIL.
#   2  — no FAILs, but at least one BLOCKED: verdict withheld, not granted.
#
# Usage:
#   scripts/launch-preflight.sh              # everything
#   scripts/launch-preflight.sh --host-only  # skip checks needing a built image
#   scripts/launch-preflight.sh --quick      # skip the long test suites
#
# Env:
#   RELEASE_BASE   base branch a release merges into      (default: main)
#   RELEASE_HEAD   branch being released                  (default: dev)
# ==============================================================================
set -uo pipefail   # NOT -e: a failing check must be recorded, not abort the run

ROOT="${CORTEXSIM_BASE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$ROOT" || exit 1

BASE="${RELEASE_BASE:-main}"
HEAD_BRANCH="${RELEASE_HEAD:-dev}"

HOST_ONLY=0
QUICK=0
for arg in "$@"; do
  case "$arg" in
    --host-only) HOST_ONLY=1 ;;
    --quick)     QUICK=1 ;;
    -h|--help)   sed -n '2,40p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown flag: $arg" >&2; exit 1 ;;
  esac
done

PASSED=(); FAILED=(); BLOCKED=()

pass()    { PASSED+=("$1");  printf '  \033[32mPASS\033[0m    %-38s %s\n' "$1" "${2:-}"; }
fail()    { FAILED+=("$1");  printf '  \033[31mFAIL\033[0m    %-38s %s\n' "$1" "${2:-}"; }
blocked() { BLOCKED+=("$1"); printf '  \033[33mBLOCKED\033[0m %-38s %s\n' "$1" "${2:-}"; }
section() { printf '\n\033[1m── %s %s\033[0m\n' "$1" "$(printf '─%.0s' $(seq 1 $((60 - ${#1}))))"; }

# Run a command quietly; PASS on exit 0, FAIL otherwise.
check() {
  local name="$1" desc="$2"; shift 2
  local out; out=$("$@" 2>&1); local rc=$?
  if [ $rc -eq 0 ]; then pass "$name" "$desc"
  else
    fail "$name" "$desc (exit $rc)"
    printf '%s\n' "$out" | tail -6 | sed 's/^/          | /'
  fi
}

have_docker_image() {
  command -v docker >/dev/null 2>&1 || return 1
  docker info >/dev/null 2>&1 || return 1
  docker image inspect "$(grep -oE '^IMAGE[[:space:]]*[:?]?=[[:space:]]*\S+' Makefile | head -1 | awk -F= '{print $2}' | tr -d ' ')" \
    >/dev/null 2>&1
}

# ==============================================================================
section "1 · Branch topology"
# ==============================================================================
# A release must be a clean fast-forward-shaped merge. If BASE has commits HEAD
# does not, someone committed to main directly — which CONTRIBUTING.md forbids —
# and the release would silently revert them.
git fetch -q origin "$BASE" "$HEAD_BRANCH" 2>/dev/null

if git rev-parse -q --verify "origin/$HEAD_BRANCH" >/dev/null; then
  AHEAD=$(git rev-list --count "origin/$BASE..origin/$HEAD_BRANCH" 2>/dev/null || echo "?")
  BEHIND=$(git rev-list --count "origin/$HEAD_BRANCH..origin/$BASE" 2>/dev/null || echo "?")
  if [ "$BEHIND" = "0" ]; then
    pass "base-not-ahead" "$BASE has nothing $HEAD_BRANCH lacks ($AHEAD to ship)"
  else
    fail "base-not-ahead" "$BASE is $BEHIND commit(s) ahead of $HEAD_BRANCH — direct commit to $BASE?"
  fi
else
  blocked "base-not-ahead" "origin/$HEAD_BRANCH not fetched"
fi

# Unmerged topic branches are abandoned work: a session's output that reaches
# neither gate. Surfacing them at release time is the only reliable moment.
#
# The branch currently checked out is excluded. It is unmerged by definition —
# it is the work in progress — and flagging it made this check fail on every
# run from a topic branch, which is how a check earns the right to be ignored.
# It is reported separately so it is still visible, just not as abandonment.
CURRENT=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
ORPHANS=$(git for-each-ref --format='%(refname:short)' refs/remotes/origin \
          | grep -vE "^origin/(HEAD|$BASE|$HEAD_BRANCH|$CURRENT)$" \
          | while read -r b; do
              git merge-base --is-ancestor "$b" "origin/$HEAD_BRANCH" 2>/dev/null || echo "$b"
            done)
if [ -z "$ORPHANS" ]; then
  pass "no-abandoned-branches" "every remote branch is merged into $HEAD_BRANCH"
else
  fail "no-abandoned-branches" "unmerged work would be stranded by this release:"
  printf '%s\n' "$ORPHANS" | while read -r b; do
    printf '          | %s (+%s commits)\n' "$b" "$(git rev-list --count "origin/$HEAD_BRANCH..$b" 2>/dev/null)"
  done
fi

# The checked-out branch, if it is not itself the release head, is in flight —
# stated so a release cut from here cannot silently omit it.
if [ "$CURRENT" != "$HEAD_BRANCH" ] && [ "$CURRENT" != "$BASE" ]; then
  AHEAD_CUR=$(git rev-list --count "origin/$HEAD_BRANCH..HEAD" 2>/dev/null || echo 0)
  if [ "$AHEAD_CUR" != "0" ]; then
    printf '          \033[36mnote\033[0m    %-38s %s\n' "in-flight branch" \
      "$CURRENT is +$AHEAD_CUR ahead of $HEAD_BRANCH — merge it before releasing"
  fi
fi

# ==============================================================================
section "2 · Release coherence"
# ==============================================================================
# tag-on-main.yml reads the FIRST `## [X.Y.Z]` heading in CHANGELOG.md and cuts
# that tag. If it does not advance past what BASE already carries, the merge is
# a silent no-op release; if it disagrees with the app's reported version, the
# tag and `GET /api/health` describe different builds.
CL_VER=$(grep -oE '^## \[[0-9]+\.[0-9]+\.[0-9]+\]' CHANGELOG.md | head -1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+')
APP_VER=$(grep -oE '^\s*version="[0-9]+\.[0-9]+\.[0-9]+"' core/main.py | head -1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+')
BASE_VER=$(git show "origin/$BASE:CHANGELOG.md" 2>/dev/null | grep -oE '^## \[[0-9]+\.[0-9]+\.[0-9]+\]' | head -1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+')

if [ -n "$CL_VER" ]; then pass "changelog-version" "top released section is $CL_VER"
else fail "changelog-version" "no '## [X.Y.Z]' heading — tag-on-main.yml would error"; fi

if [ "$CL_VER" = "$APP_VER" ]; then pass "version-coherence" "CHANGELOG == core/main.py == $CL_VER"
else fail "version-coherence" "CHANGELOG=$CL_VER but core/main.py=$APP_VER"; fi

if [ "$CL_VER" != "$BASE_VER" ]; then pass "version-advances" "$BASE is at $BASE_VER -> releasing $CL_VER"
else fail "version-advances" "$BASE already at $CL_VER — merge would tag nothing"; fi

if git ls-remote --tags origin "refs/tags/v$CL_VER" 2>/dev/null | grep -q .; then
  fail "tag-unused" "v$CL_VER already exists on origin — tag-on-main.yml will no-op"
else
  pass "tag-unused" "v$CL_VER is free"
fi

# ==============================================================================
section "3 · Honesty invariants (CLAUDE.md Gate A5)"
# ==============================================================================
# These have no CI expression at all. They are the ones whose failure mode is a
# false claim about a CUSTOMER'S detection coverage, not a red build.
if grep -q '"tenant_verified"' docs/reference/ground-truth.json 2>/dev/null; then
  TV=$(python3 -c 'import json;d=json.load(open("docs/reference/ground-truth.json"));v=d["tenant_verified"];print(v if not isinstance(v,dict) else json.dumps(v))' 2>/dev/null)
  pass "tenant-verified-declared" "ground-truth records tenant_verified=$TV"
else
  fail "tenant-verified-declared" "ground-truth.json does not state tenant_verified"
fi

# A write path to Cortex must never be on by default. Asserted against the
# EFFECTIVE runtime default by constructing Settings with the two vars cleared
# from the environment — not by grepping source, which matches the guard's own
# error strings ("set CORTEXSIM_XSIAM_ALLOW_WRITE=1 to ...") and reports the
# guard working as the guard broken.
WRITE_CHK=$(CORTEXSIM_BASE_DIR="$ROOT" PYTHONPATH="$ROOT/core" python3 - <<'PYEOF' 2>&1
import os
for k in ("CORTEXSIM_XSIAM_ALLOW_WRITE", "CORTEXSIM_XSIAM_ALLOW_DESTRUCTIVE"):
    os.environ.pop(k, None)
from config import Settings
s = Settings()
w, d = bool(s.CORTEXSIM_XSIAM_ALLOW_WRITE), bool(s.CORTEXSIM_XSIAM_ALLOW_DESTRUCTIVE)
print(f"write={w} destructive={d}")
raise SystemExit(1 if (w or d) else 0)
PYEOF
)
if [ $? -eq 0 ]; then
  pass "no-default-write-path" "XSIAM write flags default off ($WRITE_CHK)"
else
  fail "no-default-write-path" "a write flag defaults ON: $WRITE_CHK"
fi

# ==============================================================================
section "4 · Corpus + reference gates"
# ==============================================================================
check "validate-detection"  "detection corpus + export determinism" make -s validate-detection
check "check-uctc-sheet"    "UC/TC coverage sheet in sync"          make -s check-uctc-sheet
check "check-streamer"      "analytics streamer <-> card datasets"  make -s check-streamer
check "check-adapters"      "tier-2 sources + de-hand-rolling"      make -s check-adapters
check "check-lab-ready"     "lab-readiness manifest determinism"    make -s check-lab-ready
check "coverage-strict"     "MITRE/plane/type coverage floors"      make -s coverage-strict

# ground-truth regenerates inside the image via make, but the generator itself
# is plain Python — run it directly so this gate is not lost to a registry
# outage. The gate is the DIFF, not the container.
if CORTEXSIM_BASE_DIR="$ROOT" PYTHONPATH="$ROOT/core" \
     python3 scripts/generate_ground_truth.py >/dev/null 2>&1; then
  if git diff --exit-code docs/reference/ground-truth.json docs/reference/ground-truth.md >/dev/null 2>&1; then
    pass "check-ground-truth" "counted ground truth matches the tree"
  else
    fail "check-ground-truth" "ground-truth drifted — commit the regenerated files"
  fi
else
  blocked "check-ground-truth" "generator could not run (missing python deps)"
fi

# ==============================================================================
section "5 · Test suites"
# ==============================================================================
if [ "$QUICK" = "1" ]; then
  blocked "backend-suite" "--quick"
  blocked "ui-suite"      "--quick"
else
  check "backend-suite" "pytest tests/" \
    env CORTEXSIM_BASE_DIR="$ROOT" CORTEXSIM_ENV=development PYTHONPATH="$ROOT/core" \
        python3 -m pytest tests/ -q -p no:cacheprovider
  if [ -d ui/node_modules ]; then
    check "ui-suite" "vitest" bash -c "cd '$ROOT/ui' && npx vitest run --reporter=dot"
  else
    blocked "ui-suite" "ui/node_modules absent — run npm ci"
  fi
fi

check "agent-cross"  "beacon builds for linux/darwin/windows" make -s test-agent-cross
check "agent-tests"  "go test -race"  bash -c "cd '$ROOT/agent' && go test -race -count=1 ./..."
check "e2e-tierc"    "Tier-C isolated execution"              make -s e2e-tierc

# ==============================================================================
section "6 · Image parity (Gate B: the image ships what the tree has)"
# ==============================================================================
if [ "$HOST_ONLY" = "1" ]; then
  blocked "image-parity" "--host-only"
  blocked "check-refs"   "--host-only"
elif ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
  blocked "image-parity" "no docker daemon"
  blocked "check-refs"   "no docker daemon (runs inside the prod image)"
elif ! have_docker_image; then
  blocked "image-parity" "prod image not built — run 'make build'"
  blocked "check-refs"   "prod image not built — run 'make build'"
else
  check "check-agent-shelf" "built image serves every beacon target" make -s check-agent-shelf
  check "check-refs"        "strict UC/TC refs through the real loader" make -s check-refs
fi

# ==============================================================================
section "Summary"
# ==============================================================================
printf '  passed  %d\n  failed  %d\n  blocked %d\n' "${#PASSED[@]}" "${#FAILED[@]}" "${#BLOCKED[@]}"

if [ ${#FAILED[@]} -gt 0 ]; then
  printf '\n\033[31mNOT RELEASABLE\033[0m — %d gate(s) failed:\n' "${#FAILED[@]}"
  printf '  · %s\n' "${FAILED[@]}"
  exit 1
fi

if [ ${#BLOCKED[@]} -gt 0 ]; then
  printf '\n\033[33mVERDICT WITHHELD\033[0m — no failures, but %d gate(s) never ran:\n' "${#BLOCKED[@]}"
  printf '  · %s\n' "${BLOCKED[@]}"
  printf '\nA gate that could not run is not a gate that passed. Run these on a\n'
  printf 'host with the missing capability before merging to %s.\n' "$BASE"
  exit 2
fi

printf '\n\033[32mRELEASABLE\033[0m — every Gate B check ran and passed.\n'
exit 0

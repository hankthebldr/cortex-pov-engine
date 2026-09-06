#!/usr/bin/env bash
# ==============================================================================
# check-ui-shelf.sh — assert the BUILT IMAGE serves the console THIS TREE builds
#
# The sibling of check-agent-shelf / check-rust-shelf, applied to the React
# console. Same argument, same failure history: every gate that looked at the
# source tree agreed with itself while the image shipped something else.
#
# WHAT WENT WRONG (observed 2026-09-06)
#   Container cortex-pov-engine-simcore-v1.0.0 served /assets/index-DKlwogzX.css
#   carrying `.theme-console.shell{display:grid}` while the tree built
#   index-BWXDyOlT.css carrying `display:flex`. Below ~1179px the header and
#   workspace rendered wider than the viewport and were clipped with NO
#   scrollbar (.shell is overflow:hidden, so scrollWidth-clientWidth reads 0).
#   The whole newer console — safety banner, POV RUN phase bar, Composer nav —
#   was simply absent from the running image. CI was green throughout: the `ui`
#   job proves the bundle BUILDS, never that the image SHIPS it.
#
# WHY THE REFERENCE IS BUILT IN DOCKER, NOT ON THE HOST
#   Vite asset filenames are content hashes, so filename-set equality IS content
#   equality — but only across one toolchain. This host runs node v26; the image
#   builds with node:20-alpine. Comparing across that gap would turn minifier
#   differences into "drift" and the guard would be muted within a week. So the
#   reference bundle is produced by the image's OWN ui-builder stage. Any
#   difference that survives is real.
#
# Exit code:
#   0  — image assets/ set == ui-builder assets/ set, and index.html agrees
#   1  — drift, or any precondition that cannot be proven (fail closed)
#
# Usage:
#   make check-ui-shelf                             # uses the repo default image
#   make IMAGE=foo:bar check-ui-shelf
#   scripts/check-ui-shelf.sh cortex-pov-engine-simcore:1.0.0
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
REPO_ROOT="${CORTEXSIM_BASE_DIR:-$(cd "${SCRIPT_DIR}/.." && pwd)}"

# No default. The Makefile derives IMAGE from VERSION, so any literal baked in
# here would drift from it silently — and "checked the wrong image and said OK"
# is the exact failure class this guard exists to remove.
IMAGE="${1:-${IMAGE:-}}"
[[ -n "$IMAGE" ]] || {
    echo "usage: $0 <image>   (or: make check-ui-shelf, which passes the repo default)" >&2
    exit 1
}
REF_TAG="${CORTEXSIM_UI_REF_TAG:-cortexsim-ui-ref:check}"

# Where each side keeps its bundle. Both are structural facts of core/Dockerfile
# (ui-builder builds to /ui/dist; the runtime stage COPYs it to core/static),
# so if either moves this guard must be updated with it.
REF_ASSETS="/ui/dist/assets"
IMG_STATIC="/app/core/static"

if [[ -t 1 ]]; then
    RED='\033[0;31m'; GREEN='\033[0;32m'; BOLD='\033[1m'; NC='\033[0m'
else
    RED=''; GREEN=''; BOLD=''; NC=''
fi

fail() { echo -e "${RED}${BOLD}FAIL${NC} $*" >&2; exit 1; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# ------------------------------------------------------------------------------
# Preconditions. Every one of these fails closed: an unreadable image must not
# read the same as a clean image (CLAUDE.md — "a zero is degraded, not ok").
# ------------------------------------------------------------------------------
docker info >/dev/null 2>&1 \
    || fail "docker daemon unreachable — start Docker Desktop"

docker image inspect "$IMAGE" >/dev/null 2>&1 \
    || fail "no image '${IMAGE}' — run: make build   (or: make IMAGE=<tag> check-ui-shelf)"

# ------------------------------------------------------------------------------
# Reference: the image's own ui-builder stage, against the current tree.
# ------------------------------------------------------------------------------
echo "[ui-shelf] building reference bundle from ui-builder stage (node:20-alpine)"
docker build -f "${REPO_ROOT}/core/Dockerfile" --target ui-builder \
    -t "$REF_TAG" "$REPO_ROOT" >"${WORK}/build.log" 2>&1 \
    || { sed 's/^/    /' "${WORK}/build.log" >&2; fail "ui-builder stage did not build"; }

docker run --rm --entrypoint sh "$REF_TAG" -c "ls -1 '${REF_ASSETS}'" 2>/dev/null \
    | sort > "${WORK}/ref.txt" || true

ref_n=$(wc -l < "${WORK}/ref.txt" | tr -d ' ')
[[ "$ref_n" -gt 0 ]] \
    || fail "ui-builder produced NO assets — the reference itself is broken, so this run proves nothing about ${IMAGE}"

# ------------------------------------------------------------------------------
# Subject: the image, never the checkout. No -v mount, deliberately — a host
# `npm run build` must not be able to stand in for what the image ships. That
# substitution is exactly how the beacon shipped 4 targets while every
# source-tree gate reported 5.
# ------------------------------------------------------------------------------
docker run --rm --entrypoint sh "$IMAGE" -c "ls -1 '${IMG_STATIC}/assets'" 2>/dev/null \
    | sort > "${WORK}/img.txt" || true

img_n=$(wc -l < "${WORK}/img.txt" | tr -d ' ')
[[ "$img_n" -gt 0 ]] \
    || fail "${IMAGE} has no ${IMG_STATIC}/assets — the image serves no console at all"

# ------------------------------------------------------------------------------
# The comparison. Reported in two directions because they have two different
# causes and two different fixers:
#   missing    — the image predates the tree            -> rebuild the image
#   unexpected — something other than a clean build put bytes there
#                (host core/static/ leaking through COPY core/, a docker cp)
# ------------------------------------------------------------------------------
comm -23 "${WORK}/ref.txt" "${WORK}/img.txt" > "${WORK}/missing.txt"
comm -13 "${WORK}/ref.txt" "${WORK}/img.txt" > "${WORK}/unexpected.txt"

# index.html is what the browser actually follows. An asset set can match while
# the served entrypoint still points at a hash that is not there — a 404 for the
# stylesheet, which renders as an unstyled page rather than an error.
docker run --rm --entrypoint sh "$IMAGE" -c "cat '${IMG_STATIC}/index.html'" 2>/dev/null \
    > "${WORK}/index.html" || true
[[ -s "${WORK}/index.html" ]] \
    || fail "${IMAGE} has no ${IMG_STATIC}/index.html — nothing to serve the console from"

grep -o '/assets/[A-Za-z0-9._-]*' "${WORK}/index.html" \
    | sed 's|^/assets/||' | sort -u > "${WORK}/referenced.txt" || true
comm -23 "${WORK}/referenced.txt" "${WORK}/ref.txt" > "${WORK}/dangling.txt"

miss_n=$(wc -l < "${WORK}/missing.txt" | tr -d ' ')
unex_n=$(wc -l < "${WORK}/unexpected.txt" | tr -d ' ')
dang_n=$(wc -l < "${WORK}/dangling.txt" | tr -d ' ')

if [[ "$miss_n" -eq 0 && "$unex_n" -eq 0 && "$dang_n" -eq 0 ]]; then
    echo -e "${GREEN}ui shelf OK${NC}: ${IMAGE} serves all ${ref_n} assets this tree builds"
    exit 0
fi

echo -e "${RED}${BOLD}ui shelf DRIFT${NC}: ${IMAGE} does not serve what this tree builds" >&2
echo "  reference (ui-builder): ${ref_n} assets" >&2
echo "  image     (${IMAGE}): ${img_n} assets" >&2

if [[ "$miss_n" -gt 0 ]]; then
    echo >&2
    echo "  ${miss_n} asset(s) THIS TREE BUILDS but the image does not serve:" >&2
    sed 's/^/    - /' "${WORK}/missing.txt" >&2
    echo "  -> the image predates the tree. Rebuild it:  make build" >&2
fi

if [[ "$unex_n" -gt 0 ]]; then
    echo >&2
    echo "  ${unex_n} asset(s) the image serves that this tree does NOT build:" >&2
    sed 's/^/    - /' "${WORK}/unexpected.txt" >&2
    echo "  -> not a clean build. Usual cause: a host core/static/assets/ riding in" >&2
    echo "     through 'COPY core/ /app/core/' (gitignored, so easy to miss), or a" >&2
    echo "     'docker cp' / 'make ui-sync' patch that was committed into an image." >&2
fi

if [[ "$dang_n" -gt 0 ]]; then
    echo >&2
    echo "  ${dang_n} asset(s) index.html REFERENCES that this tree does not build:" >&2
    sed 's/^/    - /' "${WORK}/dangling.txt" >&2
    echo "  -> this is what a user's browser actually requests. A stale entrypoint" >&2
    echo "     is the shape of the 2026-09-06 clipped-header incident." >&2
fi

exit 1

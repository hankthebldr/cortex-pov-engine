#!/usr/bin/env bash
# ─── Cross-compile the CortexSim beacon for every target SimCore serves ──────
#
# WHY this exists: the one-line onboarding used to require a Go toolchain ON THE
# CUSTOMER ENDPOINT plus egress to proxy.golang.org. Neither survives a hardened
# enterprise network or a change-control review. SimCore now serves a prebuilt,
# checksummed binary from /api/agents/binary, and this script (or the equivalent
# `agent-builder` stage in core/Dockerfile) is what fills that shelf.
#
#   ./scripts/build-agent-dist.sh              # → ./agent-dist/
#   OUT_DIR=/tmp/dist ./scripts/build-agent-dist.sh
#
# The beacon is stdlib-only, so CGO_ENABLED=0 gives a fully static binary that
# runs on any glibc/musl host of the right arch — no runtime deps to smuggle
# past change control.
#
# windows/amd64 is INCLUDED as of the build-tag split: agent/executor now has
# _unix / _windows variants, so GOOS=windows builds and vets clean and the
# Windows beacon is a real artifact rather than an honest 501.
#
# REPRODUCIBILITY, precisely: -trimpath makes the output independent of the
# checkout path, so the SAME Go toolchain over the SAME source IN THE SAME VCS
# STATE yields byte-identical binaries (measured: two consecutive runs, all 5
# digests equal). Two more things are inputs, and both make this script's
# bytes differ from the image's for the same commit:
#
#   * The Go PATCH VERSION — go1.22.2 here and go1.22.12 in `golang:1.22-alpine`
#     produce different bytes.
#   * The VCS STAMP. Run inside a git checkout, `go build` embeds vcs.revision,
#     vcs.time and vcs.modified (read them back with `go version -m <binary>`).
#     vcs.modified flips to true for any uncommitted or untracked file, so a
#     dirty tree builds different bytes than a clean one. The `agent-builder`
#     stage in core/Dockerfile COPYs only agent/, has no .git, and stamps
#     nothing. The stamp is kept on purpose: it is the only commit identity an
#     unsigned release beacon carries, and release.yml records it in
#     cortexsim-agent-buildinfo.txt. Do not add -buildvcs=false here without
#     changing that step; tests/installer/test_agent_dist_matrix_parity.py
#     pins the pair.
#
# Both are expected and harmless HERE because nothing trusts a carried-in
# digest: the installer fetches the checksum from the same SimCore that serves
# the bytes, and /api/agents/binary/sha256 hashes the file on disk rather than
# reading SHA256SUMS. Do not build a supply-chain check that pins a digest
# across producers or toolchains — it would be unfalsifiable, and would go red
# on a Go point release rather than on tampering.
#
# THE SHELF IS REPLACED, NOT APPENDED TO. Every target is built into a staging
# directory inside OUT_DIR first. Only when all of them have built are the old
# cortexsim-agent-* files and SHA256SUMS removed and the new set moved in. So a
# binary for a target that has since left TARGETS cannot stay on the shelf with
# a digest beside it, and a build that fails part-way leaves the previous shelf
# exactly as it was instead of a mix of old and new under one stale SHA256SUMS.
# Other files in OUT_DIR are not this script's and are left alone.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${OUT_DIR:-$REPO_ROOT/agent-dist}"
AGENT_DIR="$REPO_ROOT/agent"

TARGETS=(
  "linux/amd64"
  "linux/arm64"
  "darwin/amd64"
  "darwin/arm64"
  "windows/amd64"
)

if ! command -v go >/dev/null 2>&1; then
  echo "ERROR: go toolchain not found — install Go 1.21+ or build via 'make build'" >&2
  exit 1
fi

if [ ! -d "$AGENT_DIR" ]; then
  echo "ERROR: agent source tree not found at $AGENT_DIR" >&2
  exit 1
fi

# The build below runs from inside agent/, so a relative OUT_DIR would resolve
# against the wrong directory there. Anchor it to where the caller stood. An
# absolute path (POSIX, or a drive-letter one under Git Bash on Windows) is
# used exactly as given.
case "$OUT_DIR" in
  /*|[A-Za-z]:[\\/]*) ;;
  *) OUT_DIR="$PWD/$OUT_DIR" ;;
esac

mkdir -p "$OUT_DIR"
echo "[agent-dist] go: $(go version)"
echo "[agent-dist] out: $OUT_DIR"

# Staged INSIDE OUT_DIR so every move below is a rename on one filesystem. The
# trap removes it on any exit, so a failed build leaves nothing behind either.
STAGE="$(mktemp -d "$OUT_DIR/.agent-dist-stage.XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT

BUILT=()
for target in "${TARGETS[@]}"; do
  goos="${target%%/*}"
  goarch="${target##*/}"
  # Windows refuses to execute an extensionless binary, and the installer's
  # download path keys on the served filename — so the suffix is load-bearing,
  # not cosmetic.
  ext=""; [ "$goos" = "windows" ] && ext=".exe"
  name="cortexsim-agent-${goos}-${goarch}${ext}"
  out="$STAGE/$name"
  # -trimpath keeps the build reproducible across checkout locations; -s -w drops
  # the symbol table so the artefact a DC copies onto a customer host is ~5 MB.
  ( cd "$AGENT_DIR" && CGO_ENABLED=0 GOOS="$goos" GOARCH="$goarch" \
      go build -trimpath -ldflags="-s -w" -o "$out" . )
  echo "[agent-dist] built $name ($(wc -c <"$out" | tr -d ' ') bytes)"
  BUILT+=("$name")
done

# SHA256SUMS is for humans and CI (`sha256sum -c`). The install endpoint computes
# its own digest from the file on disk, so a stale sums file can never make a
# tampered binary verify.
( cd "$STAGE" && sha256sum cortexsim-agent-* > SHA256SUMS 2>/dev/null \
    || shasum -a 256 cortexsim-agent-* > SHA256SUMS )

# Every target built and summed: replace the shelf. Each new binary is renamed
# over its predecessor, then anything left that this run did not build is a
# target that has left TARGETS and goes, and SHA256SUMS lands last. A failure
# above never reaches here (set -e), so the previous shelf is still whole.
for name in "${BUILT[@]}"; do
  mv -f "$STAGE/$name" "$OUT_DIR/$name"
done
for old in "$OUT_DIR"/cortexsim-agent-*; do
  [ -e "$old" ] || continue
  case " ${BUILT[*]} " in
    *" $(basename "$old") "*) ;;
    *) echo "[agent-dist] removing stale $(basename "$old") — not a current target"
       rm -f "$old" ;;
  esac
done
mv -f "$STAGE/SHA256SUMS" "$OUT_DIR/SHA256SUMS"

echo "[agent-dist] wrote $OUT_DIR/SHA256SUMS"
echo "[agent-dist] done — SimCore serves these from GET /api/agents/binary"

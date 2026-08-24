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
# checkout path, so the SAME Go toolchain over the SAME source yields
# byte-identical binaries (measured: two consecutive runs, all 5 digests equal).
# The Go PATCH VERSION is still an input — go1.22.2 here and go1.22.12 in
# `golang:1.22-alpine` produce different bytes for the same commit. That is
# expected and harmless HERE because nothing trusts a carried-in digest: the
# installer fetches the checksum from the same SimCore that serves the bytes,
# and /api/agents/binary/sha256 hashes the file on disk rather than reading
# SHA256SUMS. Do not build a supply-chain check that pins a digest across
# toolchains — it would be unfalsifiable, and would go red on a Go point
# release rather than on tampering.
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

mkdir -p "$OUT_DIR"
echo "[agent-dist] go: $(go version)"
echo "[agent-dist] out: $OUT_DIR"

for target in "${TARGETS[@]}"; do
  goos="${target%%/*}"
  goarch="${target##*/}"
  # Windows refuses to execute an extensionless binary, and the installer's
  # download path keys on the served filename — so the suffix is load-bearing,
  # not cosmetic.
  ext=""; [ "$goos" = "windows" ] && ext=".exe"
  out="$OUT_DIR/cortexsim-agent-${goos}-${goarch}${ext}"
  # -trimpath keeps the build reproducible across checkout locations; -s -w drops
  # the symbol table so the artefact a DC copies onto a customer host is ~5 MB.
  ( cd "$AGENT_DIR" && CGO_ENABLED=0 GOOS="$goos" GOARCH="$goarch" \
      go build -trimpath -ldflags="-s -w" -o "$out" . )
  echo "[agent-dist] built $(basename "$out") ($(wc -c <"$out" | tr -d ' ') bytes)"
done

# SHA256SUMS is for humans and CI (`sha256sum -c`). The install endpoint computes
# its own digest from the file on disk, so a stale sums file can never make a
# tampered binary verify.
( cd "$OUT_DIR" && sha256sum cortexsim-agent-* > SHA256SUMS 2>/dev/null \
    || shasum -a 256 cortexsim-agent-* > SHA256SUMS )

echo "[agent-dist] wrote $OUT_DIR/SHA256SUMS"
echo "[agent-dist] done — SimCore serves these from GET /api/agents/binary"

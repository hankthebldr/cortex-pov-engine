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
# windows/amd64 is deliberately ABSENT: agent/executor is POSIX-only (Setpgid,
# syscall.Kill) and does not compile for GOOS=windows. Serving a binary we
# cannot build would be worse than the honest 501 the install endpoint returns.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${OUT_DIR:-$REPO_ROOT/agent-dist}"
AGENT_DIR="$REPO_ROOT/agent"

TARGETS=(
  "linux/amd64"
  "linux/arm64"
  "darwin/amd64"
  "darwin/arm64"
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
  out="$OUT_DIR/cortexsim-agent-${goos}-${goarch}"
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

#!/usr/bin/env bash
# ─── Build the CortexSim Rust tool matrix (static musl) into ./rust-dist ─────
#
# WHY this exists: `sources/{signalbench,ackbarx,xdrtop}` are tier-2 git
# submodules whose only distribution story was `cargo build --release` ON THE
# CUSTOMER JUMPBOX. That needs a Rust toolchain plus crates.io egress — exactly
# what a default-deny enterprise network blocks, and exactly the problem the Go
# beacon's agent-dist matrix already solved for the beacon. This script is the
# Rust half of that same answer: a DC builds once on their own machine, SimCore
# serves the checksummed bytes, and the target host needs neither rustup nor
# the public internet.
#
#   ./scripts/build-rust-dist.sh                 # → ./rust-dist/  (all tools)
#   ./scripts/build-rust-dist.sh xdrtop          # one tool
#   ./scripts/build-rust-dist.sh --check-recipe  # ~50ms, no compile, no docker
#   OUT_DIR=/tmp/rd ./scripts/build-rust-dist.sh
#   RUST_BUILDER_IMAGE=rust:1.90-alpine ./scripts/build-rust-dist.sh
#
# ── Why Docker and not the host ──────────────────────────────────────────────
# Go cross-compiles from one host with CGO_ENABLED=0. Rust does not: every
# target needs a std for that triple plus a linker, and a *static* binary needs
# musl. A stock host (this one included) has neither rustup nor musl-gcc, so a
# host build silently produces a glibc-dynamic binary that dies on a customer
# host with `error while loading shared libraries`. We refuse to produce that.
# rust:1.90-alpine is musl-NATIVE, so this is a native build, not a cross —
# which is why it needs no cross linker and why it actually works.
#
# ── Why not a core/Dockerfile builder stage ─────────────────────────────────
# `agent-builder` can live in the Dockerfile because the beacon is stdlib-only
# and needs no network. A Rust stage would need crates.io at `docker build`
# time, which breaks the offline image build. rust-dist/ therefore follows the
# `payloads/` precedent (staged on the HOST, COPYed in), not agent-dist/'s.
#
# ── Target matrix: linux/amd64 ONLY, and the absence is legible ─────────────
# Rejected targets and their reasons are recorded in MANIFEST.json's
# `unsupported_targets[]` and quoted verbatim by the serving API's 404, so a DC
# who asks "where is arm64?" gets a sentence rather than a mystery. A smaller
# matrix that genuinely runs beats a wide one that half-fails in front of a
# customer.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${OUT_DIR:-$REPO_ROOT/rust-dist}"
SOURCES_DIR="$REPO_ROOT/sources"
# Pinned, never floating on `rust:alpine`: an alpine base bump that moves
# OpenSSL would silently revert xdrtop to a dynamic libssl link.
BUILDER_IMAGE="${RUST_BUILDER_IMAGE:-rust:1.90-alpine}"

TARGET_TRIPLE="x86_64-unknown-linux-musl"
DIST_OS="linux"
DIST_ARCH="amd64"

ALL_TOOLS=(signalbench ackbarx xdrtop)

# ─── Legible absence ────────────────────────────────────────────────────────
# Each entry is  target|tools|reason  and lands verbatim in MANIFEST.json.
UNSUPPORTED=(
  "linux/arm64|xdrtop|openssl-sys requires an aarch64 OpenSSL; an x86_64 alpine has no aarch64 openssl-libs-static, and enabling the openssl crate's 'vendored' feature would require editing the xdrtop submodule. ackbarx and signalbench are reachable on arm64 but shipping 1 of 3 unproven artifacts is worse than an honest gap — arm64 widens only behind an execution gate (qemu binfmt), never on a build-only pass."
  "darwin/amd64|signalbench,ackbarx,xdrtop|signalbench is Linux-only by design (reads /proc, links libc) and ackbarx is a Linux SNMP daemon. Cross-building Mach-O from Linux additionally needs an Apple SDK, which is not redistributable."
  "darwin/arm64|signalbench,ackbarx,xdrtop|signalbench is Linux-only by design (reads /proc, links libc) and ackbarx is a Linux SNMP daemon. Cross-building Mach-O from Linux additionally needs an Apple SDK, which is not redistributable. Each reason is written to stand alone because the serving 404 quotes exactly one of them, with no other context."
  "windows/amd64|signalbench,ackbarx,xdrtop|No upstream Windows build exists for signalbench or ackbarx; xdrtop is a terminal UI shipped upstream only as a .zip. Use the Go beacon's push mode on Windows targets."
)

# ─── Colours (off when not a TTY) ───────────────────────────────────────────
if [ -t 1 ]; then
  RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[1;33m'; NC=$'\033[0m'
else
  RED=''; GREEN=''; YELLOW=''; NC=''
fi

log()  { echo "[rust-dist] $*"; }
fail() { echo "${RED}ERROR${NC}: $*" >&2; exit 1; }

# ─────────────────────────────────────────────────────────────────────────────
# --check-recipe — the always-on, zero-cost gate (CI 'adapters' job)
#
# This asserts the things that rot SILENTLY between full builds. signalbench's
# build has been broken for every DC who ever ran it (see phase 1 below) and
# nothing noticed, because nothing cheap ever looked.
# ─────────────────────────────────────────────────────────────────────────────
check_recipe() {
  local rc=0 pass=0

  echo "Rust tool build-recipe preflight  (repo: $REPO_ROOT)"
  echo "--------------------------------------------------------------------------"

  for tool in "${ALL_TOOLS[@]}"; do
    if [ -f "$SOURCES_DIR/$tool/Cargo.toml" ]; then
      echo "  ${GREEN}PASS${NC} $tool: sources/$tool/Cargo.toml present"
      pass=$((pass + 1))
    else
      echo "  ${RED}FAIL${NC} $tool: sources/$tool/Cargo.toml MISSING — run 'git submodule update --init --recursive'"
      rc=1
    fi
  done

  # THE guard for the defect this pass found. signalbench/src/techniques/
  # software.rs does include_bytes!("../../embedded_binaries/pacemaker_helper")
  # and that file is NOT in the upstream git tree — it must be produced by
  # building helpers/pacemaker first. If a future gitlink bump moves or removes
  # that helper crate, the build fails with "couldn't read embedded_binaries/
  # pacemaker_helper", which names neither the helper nor the fix. This line is
  # what turns that into an actionable failure on every PR.
  if [ -f "$SOURCES_DIR/signalbench/helpers/pacemaker/Cargo.toml" ]; then
    echo "  ${GREEN}PASS${NC} signalbench: helpers/pacemaker crate present (feeds include_bytes! of embedded_binaries/pacemaker_helper)"
    pass=$((pass + 1))
  else
    echo "  ${RED}FAIL${NC} signalbench: helpers/pacemaker/Cargo.toml MISSING — src/techniques/software.rs embeds embedded_binaries/pacemaker_helper via include_bytes!, which ONLY this helper crate produces. Without it the build dies with 'couldn't read embedded_binaries/pacemaker_helper'."
    rc=1
  fi

  if grep -q 'include_bytes!("../../embedded_binaries/pacemaker_helper")' \
      "$SOURCES_DIR/signalbench/src/techniques/software.rs" 2>/dev/null; then
    echo "  ${GREEN}PASS${NC} signalbench: include_bytes! path unchanged (phase-1 recipe still correct)"
    pass=$((pass + 1))
  else
    echo "  ${YELLOW}WARN${NC} signalbench: include_bytes! of embedded_binaries/pacemaker_helper not found in src/techniques/software.rs — upstream may have changed the embed; re-verify phase 1 of this script."
  fi

  # Drift guard: every registry entry whose build_cmd still invokes the Rust
  # toolchain must have a recipe here, or a DC gets sent down the host-build
  # path this whole pipeline exists to eliminate.
  local reg="$REPO_ROOT/core/tools/registry.py"
  if [ -f "$reg" ]; then
    local cargo_tools
    cargo_tools="$(awk '
      /^    "[a-z0-9_-]+": \{/ { name=$1; gsub(/[",:{]/,"",name) }
      /build_cmd.*cargo/ { if (name != "") print name }
    ' "$reg" | sort -u)"
    local t
    for t in $cargo_tools; do
      if printf '%s\n' "${ALL_TOOLS[@]}" | grep -qx "$t"; then
        echo "  ${GREEN}PASS${NC} $t: registry cargo-based build_cmd has a recipe in build-rust-dist.sh"
        pass=$((pass + 1))
      else
        echo "  ${RED}FAIL${NC} $t: core/tools/registry.py declares a cargo-based build_cmd but build-rust-dist.sh has no recipe — that tool can only be built on a jumpbox with rustup + crates.io egress."
        rc=1
      fi
    done
  fi

  # Staleness: a rust-dist/ built from an older gitlink serves a DC a different
  # tool than the checkout says. Non-fatal at recipe level, but never silent.
  if [ -f "$OUT_DIR/MANIFEST.json" ]; then
    local t built live
    for t in "${ALL_TOOLS[@]}"; do
      built="$(sed -n "/\"tool\": \"$t\"/,/}/p" "$OUT_DIR/MANIFEST.json" \
                 | grep -o '"submodule_commit": "[0-9a-f]*"' | head -1 \
                 | sed 's/.*"\([0-9a-f]*\)"$/\1/')"
      [ -z "$built" ] && continue
      live="$(git -C "$SOURCES_DIR/$t" rev-parse HEAD 2>/dev/null || echo '')"
      if [ -n "$live" ] && [ "$built" != "$live" ]; then
        echo "  ${RED}FAIL${NC} $t: rust-dist/ was built from ${built:0:7} but the checkout is now ${live:0:7} — run 'make rust-dist'"
        rc=1
      fi
    done
  fi

  echo "--------------------------------------------------------------------------"
  if [ "$rc" -eq 0 ]; then
    echo "${GREEN}OK${NC} — $pass check(s) passed; recipes match the source trees."
  fi
  return "$rc"
}

# ─────────────────────────────────────────────────────────────────────────────
# In-container build driver.
#
# Written to a temp file and bind-mounted in, rather than inlined into
# `docker run sh -c`, so the quoting stays readable and the recipes stay
# greppable.
# ─────────────────────────────────────────────────────────────────────────────
write_builder_script() {
  cat >"$1" <<'BUILDER'
#!/bin/sh
# Runs INSIDE rust:1.90-alpine. /src is the repo's sources/ mounted READ-ONLY —
# that mount flag is the mechanical guarantee that no submodule file is ever
# written, enforced by the kernel rather than by convention.
set -eu

TOOLS="$1"
TRIPLE="$2"
OS="$3"
ARCH="$4"

# Guard against a silently-wrong architecture: on an arm64 docker host an
# unpinned `rust:1.90-alpine` is arm64 and would emit arm64 bytes under an
# -amd64 filename. --platform is passed by the caller; this is the assertion.
case "$TRIPLE" in
  x86_64-*) want=x86_64 ;;
  aarch64-*) want=aarch64 ;;
  *) want="" ;;
esac
got="$(uname -m)"
if [ -n "$want" ] && [ "$got" != "$want" ]; then
  echo "ERROR: builder container is $got but the target triple is $TRIPLE — refusing to emit mislabelled bytes" >&2
  exit 1
fi

echo "[builder] $(rustc --version)  target=$TRIPLE  arch=$got"

# musl-dev/gcc: the musl libc + linker for the native static build.
# perl + openssl-dev + openssl-libs-static: xdrtop only (see its recipe).
apk add --no-cache musl-dev gcc make perl pkgconf openssl-dev openssl-libs-static binutils >/dev/null 2>&1 \
  || { echo "ERROR: apk add failed — the builder image needs network at build time (this is a DC-side build, not a customer-host one)" >&2; exit 1; }

# +crt-static on the musl target yields a static-pie ELF with zero DT_NEEDED.
export RUSTFLAGS="-C target-feature=+crt-static"
rustup target add "$TRIPLE" >/dev/null 2>&1 || true

mkdir -p /out

for tool in $TOOLS; do
  echo "[builder] === $tool ==="
  # Copy the tree OUT of the read-only mount so target/ and signalbench's
  # generated embedded_binaries/ land in scratch, never in the submodule.
  # target/ is excluded: a host-side GNU build leaves gigabytes there and none
  # of it is valid for the musl triple.
  rm -rf "/work/$tool"
  mkdir -p "/work/$tool"
  ( cd "/src/$tool" && tar -c --exclude=./target --exclude=./.git . ) | tar -x -C "/work/$tool"

  cd "/work/$tool"

  case "$tool" in
    signalbench)
      # PHASE 1 — NOT OPTIONAL. src/techniques/software.rs does
      #   include_bytes!("../../embedded_binaries/pacemaker_helper")
      # and that file is untracked upstream: it is BUILT from helpers/pacemaker.
      # Upstream's own .github/workflows/build-binary.yml does exactly this.
      # Because this builder image is musl-native, a plain --release build of
      # the helper already yields the static musl helper that helpers/pacemaker/
      # build.sh cross-compiles for. Skipping phase 1 fails the lib build with
      # "couldn't read embedded_binaries/pacemaker_helper" — a message that
      # names neither the helper crate nor the fix, which is how this defect
      # survived in STATIC_TOOL_REGISTRY's build_cmd unnoticed.
      echo "[builder] $tool phase 1/2: helpers/pacemaker → embedded_binaries/pacemaker_helper"
      ( cd helpers/pacemaker && cargo build --release --target "$TRIPLE" ) \
        || { echo "ERROR: signalbench phase 1 failed — helpers/pacemaker did not build. src/techniques/software.rs cannot be compiled without embedded_binaries/pacemaker_helper." >&2; exit 1; }
      mkdir -p embedded_binaries
      cp "helpers/pacemaker/target/$TRIPLE/release/pacemaker_helper" embedded_binaries/pacemaker_helper
      echo "[builder] $tool phase 2/2: main crate"
      cargo build --release --target "$TRIPLE"
      ;;
    xdrtop)
      # xdrtop's Cargo.toml has `reqwest = { version = "0.12", features =
      # ["json"] }` with no default-features = false, so default-tls →
      # native-tls → openssl-sys. Without OPENSSL_STATIC the result dynamically
      # links libssl.so.3/libcrypto.so.3 and dies on any host without OpenSSL 3
      # — the precise portability failure this whole script exists to remove.
      # The clean fix (rustls-tls) would mean editing a SUBMODULE, which is
      # forbidden; OPENSSL_STATIC + alpine's openssl-libs-static gets the same
      # outcome from the outside.
      OPENSSL_STATIC=1 OPENSSL_NO_VENDOR=1 OPENSSL_DIR=/usr \
        cargo build --release --target "$TRIPLE"
      ;;
    *)
      cargo build --release --target "$TRIPLE"
      ;;
  esac

  # The binary name is not always the crate dir name; take the one executable
  # that is not a build script artefact.
  bin=""
  for cand in "target/$TRIPLE/release/$tool" $(ls -1 "target/$TRIPLE/release/" 2>/dev/null); do
    p="target/$TRIPLE/release/$(basename "$cand")"
    if [ -f "$p" ] && [ -x "$p" ]; then bin="$p"; break; fi
  done
  [ -n "$bin" ] || { echo "ERROR: $tool built but no executable found under target/$TRIPLE/release/" >&2; exit 1; }

  # ── VERIFY BEFORE PUBLISHING ──────────────────────────────────────────────
  # This repo's recurring failure is green-while-proving-nothing. A build
  # script that emits a binary nobody executed is that failure in this pass's
  # shape, so nothing reaches /out unless it is (a) provably static and
  # (b) provably able to start.
  if readelf -d "$bin" 2>/dev/null | grep -q 'NEEDED'; then
    echo "ERROR: $tool is DYNAMICALLY linked — it would die with 'error while loading shared libraries' on a customer host. Refusing to publish." >&2
    readelf -d "$bin" | grep NEEDED >&2
    exit 1
  fi
  if ! "./$bin" --version >/dev/null 2>&1; then
    # Not every CLI has --version; fall back to --help before giving up.
    if ! "./$bin" --help >/dev/null 2>&1; then
      echo "ERROR: $tool built but does not execute (--version and --help both failed). A binary that exists is not a binary that runs. Refusing to publish." >&2
      exit 1
    fi
  fi

  ver="$("./$bin" --version 2>/dev/null | head -1 | tr -d '\r' || true)"
  [ -n "$ver" ] || ver="unknown"

  out="/out/cortexsim-tool-${tool}-${OS}-${ARCH}"
  cp "$bin" "$out"
  chmod 0755 "$out"
  size="$(wc -c <"$out" | tr -d ' ')"
  echo "[builder] published $(basename "$out") ($size bytes, static, '$ver')"
  # One line per tool, consumed by the host side to build MANIFEST.json.
  echo "${tool}|${ver}|${size}" >>/out/.build-report
done
BUILDER
  chmod +x "$1"
}

# ─────────────────────────────────────────────────────────────────────────────
# Deterministic MANIFEST.json — the pin analogue for a locally-built artifact.
#
# The payload shelf's `pin` tells a DC WHICH tool they shipped. A built binary
# needs the same guarantee, keyed on the submodule gitlink instead of a release
# tag. Rendered byte-deterministically (fixed key order, 2-space indent, no
# timestamp / hostname / username) so the file is provably re-derivable —
# sha256 and size legitimately vary with the toolchain, which is what
# builder_image pins.
# ─────────────────────────────────────────────────────────────────────────────
write_manifest() {
  local report="$1" manifest="$2"
  {
    echo '{'
    echo '  "$generated": {'
    echo '    "by": "scripts/build-rust-dist.sh",'
    echo '    "schema": 1'
    echo '  },'
    printf '  "builder_image": "%s",\n' "$BUILDER_IMAGE"
    printf '  "target": "%s",\n' "$TARGET_TRIPLE"
    printf '  "os": "%s",\n' "$DIST_OS"
    printf '  "arch": "%s",\n' "$DIST_ARCH"
    echo '  "tools": ['
    local first=1 line tool ver size fname sha commit tag
    while IFS='|' read -r tool ver size; do
      [ -z "$tool" ] && continue
      fname="cortexsim-tool-${tool}-${DIST_OS}-${DIST_ARCH}"
      sha="$(sha256sum "$OUT_DIR/$fname" | awk '{print $1}')"
      commit="$(git -C "$SOURCES_DIR/$tool" rev-parse HEAD 2>/dev/null || echo '')"
      tag="$(git -C "$SOURCES_DIR/$tool" tag --points-at HEAD 2>/dev/null | head -1 || true)"
      [ "$first" -eq 1 ] || echo '    },'
      first=0
      echo '    {'
      printf '      "tool": "%s",\n' "$tool"
      printf '      "version": "%s",\n' "$ver"
      printf '      "submodule_commit": "%s",\n' "$commit"
      printf '      "submodule_tag": %s,\n' "$( [ -n "$tag" ] && printf '"%s"' "$tag" || printf 'null' )"
      printf '      "filename": "%s",\n' "$fname"
      printf '      "sha256": "%s",\n' "$sha"
      printf '      "size_bytes": %s,\n' "$size"
      echo '      "static": true,'
      echo '      "verified_exec": true'
    done < <(sort "$report")
    [ "$first" -eq 1 ] || echo '    }'
    echo '  ],'
    echo '  "unsupported_targets": ['
    local i n=${#UNSUPPORTED[@]} entry ut utools ureason
    for i in $(seq 0 $((n - 1))); do
      entry="${UNSUPPORTED[$i]}"
      ut="${entry%%|*}"
      ureason="${entry##*|}"
      utools="${entry#*|}"; utools="${utools%%|*}"
      echo '    {'
      printf '      "target": "%s",\n' "$ut"
      printf '      "tools": [%s],\n' "$(printf '%s' "$utools" | awk -F, '{for(j=1;j<=NF;j++){printf "%s\"%s\"", (j>1?", ":""), $j}}')"
      printf '      "reason": "%s"\n' "$ureason"
      if [ "$i" -eq $((n - 1)) ]; then echo '    }'; else echo '    },'; fi
    done
    echo '  ]'
    echo '}'
  } >"$manifest"
}

# ─────────────────────────────────────────────────────────────────────────────
main() {
  local tools=()
  for arg in "$@"; do
    case "$arg" in
      --check-recipe) check_recipe; exit $? ;;
      -h|--help) sed -n '2,40p' "${BASH_SOURCE[0]}"; exit 0 ;;
      -*) fail "unknown flag: $arg (try --help)" ;;
      *)
        printf '%s\n' "${ALL_TOOLS[@]}" | grep -qx "$arg" \
          || fail "unknown tool: $arg (known: ${ALL_TOOLS[*]})"
        tools+=("$arg")
        ;;
    esac
  done
  [ "${#tools[@]}" -eq 0 ] && tools=("${ALL_TOOLS[@]}")

  # Same refusal shape as build-agent-dist.sh's missing-Go check: name the
  # missing dependency AND the way out.
  command -v docker >/dev/null 2>&1 || fail \
"docker not found — the Rust matrix cannot be built on this host.
  Static musl builds need rustup + a musl toolchain; a plain host 'cargo build
  --release' produces a glibc-DYNAMIC binary that dies on a customer jumpbox,
  so this script refuses to fall back to it.
  Fix: install Docker, or copy a rust-dist/ directory from a machine that has
  it (the bytes are portable — that is the whole point)."

  [ -d "$SOURCES_DIR" ] || fail "sources/ not found at $SOURCES_DIR"
  local t
  for t in "${tools[@]}"; do
    [ -f "$SOURCES_DIR/$t/Cargo.toml" ] || fail \
      "sources/$t/Cargo.toml missing — tier-2 sources are git submodules. Run: git submodule update --init --recursive"
  done

  mkdir -p "$OUT_DIR"
  rm -f "$OUT_DIR"/.build-report

  log "builder: $BUILDER_IMAGE  target: $TARGET_TRIPLE"
  log "tools:   ${tools[*]}"
  log "out:     $OUT_DIR"

  local tmp
  tmp="$(mktemp -d)"
  # shellcheck disable=SC2064
  trap "rm -rf '$tmp'" EXIT
  write_builder_script "$tmp/build.sh"

  # A named volume for CARGO_HOME makes re-runs warm without polluting the
  # repo or the submodules.
  docker volume create cortexsim-rust-cargo >/dev/null

  docker run --rm \
    --platform linux/amd64 \
    -v "$SOURCES_DIR:/src:ro" \
    -v "$OUT_DIR:/out" \
    -v "$tmp/build.sh:/build.sh:ro" \
    -v cortexsim-rust-cargo:/usr/local/cargo/registry \
    -e CARGO_TERM_COLOR=never \
    "$BUILDER_IMAGE" \
    /build.sh "${tools[*]}" "$TARGET_TRIPLE" "$DIST_OS" "$DIST_ARCH"

  [ -s "$OUT_DIR/.build-report" ] || fail "builder produced no artifacts"

  write_manifest "$OUT_DIR/.build-report" "$OUT_DIR/MANIFEST.json"
  rm -f "$OUT_DIR/.build-report"

  # Same form as agent-dist's SHA256SUMS so ONE verification idiom covers both.
  # For humans and `sha256sum -c` only: the serving endpoint recomputes its own
  # digest from the bytes on disk, so a stale sums file can never make a
  # tampered binary verify.
  ( cd "$OUT_DIR" && sha256sum cortexsim-tool-* > SHA256SUMS 2>/dev/null \
      || shasum -a 256 cortexsim-tool-* > SHA256SUMS )

  echo
  log "matrix (${TARGET_TRIPLE}):"
  local f
  for f in "$OUT_DIR"/cortexsim-tool-*; do
    printf '  %-44s %10s bytes\n' "$(basename "$f")" "$(wc -c <"$f" | tr -d ' ')"
  done
  echo
  log "unsupported targets (recorded in MANIFEST.json, surfaced by the API's 404):"
  local entry
  for entry in "${UNSUPPORTED[@]}"; do
    printf '  %-14s %s\n' "${entry%%|*}" "$(printf '%s' "${entry##*|}" | cut -c1-88)…"
  done
  echo
  log "wrote $OUT_DIR/SHA256SUMS and $OUT_DIR/MANIFEST.json"
  log "done — SimCore serves these from GET /api/tools/binary/{tool}"
}

main "$@"

# ---- Python builder stage: compile wheels (psutil etc. need gcc) ----
FROM python:3.11-slim AS python-builder

RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY core/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --prefix=/install -r /tmp/requirements.txt

# ---- Agent builder stage: cross-compile the beacon SimCore hands to endpoints ----
# WHY: the one-line onboarding must not require a Go toolchain or public Go-proxy
# egress on the CUSTOMER endpoint. Baking the matrix here means a `docker compose
# up` deployment can serve a ready-to-run beacon from /api/agents/binary with the
# DC's own SimCore as the only outbound dependency.
#
# Kept in lockstep with scripts/build-agent-dist.sh (same targets, same flags) —
# and that lockstep is now ASSERTED, not just claimed. It drifted once: the
# script gained windows/amd64 and this loop did not, so every deployed image
# served 4 targets, `GET /api/agents/binary?os=windows` 404'd, and the
# PowerShell installer refused before enrolling — while CI stayed green because
# it only ever proved the beacon COMPILES for windows, never that the image
# SHIPS it. tests/installer/test_agent_dist_matrix_parity.py parses both files
# and fails on any divergence. The `ext` expansion is load-bearing: Windows will
# not execute an extensionless binary and core/api/agents.py::_binary_name()
# appends ".exe" for GOOS=windows, so dropping it yields a silent 404
# indistinguishable from "not built".
FROM golang:1.22-alpine AS agent-builder

WORKDIR /src
COPY agent/ ./agent/
RUN cd agent && \
    for t in linux/amd64 linux/arm64 darwin/amd64 darwin/arm64 windows/amd64; do \
        goos="${t%/*}"; goarch="${t#*/}"; \
        ext=""; [ "$goos" = "windows" ] && ext=".exe"; \
        CGO_ENABLED=0 GOOS="$goos" GOARCH="$goarch" \
            go build -trimpath -ldflags="-s -w" \
            -o "/out/cortexsim-agent-${goos}-${goarch}${ext}" . || exit 1; \
    done && \
    cd /out && sha256sum cortexsim-agent-* > SHA256SUMS

# ---- Rust builder stage: static-musl builds of the three tier-2 Rust tools ----
# WHY: signalbench, ackbarx and xdrtop are tier-2 SUBMODULE tools, and until now
# the only way to get one onto a jumpbox was `cargo build --release` ON THAT
# HOST — a Rust toolchain plus crates.io egress, i.e. precisely what a
# default-deny customer network blocks and precisely the problem the beacon
# matrix above and the payload shelf below already solve for their own
# artifacts. This stage bakes them the same way agent-builder bakes the beacon,
# so a container-only deploy serves a ready-to-run tool with the DC's own
# SimCore as the only outbound dependency.
#
# Alpine is the base because it is musl-NATIVE: x86_64-unknown-linux-musl is the
# host triple here, so every build below is a plain native build, not a
# cross-compile, and needs no cross linker. That is the whole reason the recipe
# is this short — Rust does not cross-compile for free the way Go does.
#
# linux/amd64 ONLY, deliberately, and the absence is recorded rather than
# silent: arm64 would need an aarch64 OpenSSL for xdrtop that an x86_64 alpine
# cannot supply, and the alternative (the openssl crate's `vendored` feature)
# means editing sources/xdrtop/Cargo.toml — a submodule, which CLAUDE.md
# forbids. MANIFEST.json carries unsupported_targets[] with that reason so the
# serving endpoint can quote it back instead of returning a mystery 404.
#
# Unlike payloads/ below, this is a builder stage rather than host staging: the
# offline-build argument that governs payloads/ is about fetching THIRD-PARTY
# bytes from arbitrary upstreams, and it does not apply here because this image
# ALREADY requires a package network to build (python-builder hits PyPI,
# ui-builder hits the npm registry). Adding crates.io changes the build time,
# not the class of build — and it is what makes `docker build` alone sufficient
# to produce a servable tool, with no host toolchain anywhere.
FROM rust:1.90-alpine AS rust-builder

# musl-dev/gcc: the linker cc invokes. openssl-dev + openssl-libs-static: xdrtop
# depends on reqwest 0.12 WITHOUT `default-features = false`, so it resolves
# default-tls -> native-tls -> openssl-sys; without the static archives here it
# links libssl.so.3/libcrypto.so.3 and dies on any host that lacks OpenSSL 3 —
# the exact portability failure this stage exists to remove. binutils supplies
# readelf, which is the static-linkage gate below (`file` is not in this image
# and busybox ldd cannot be trusted to distinguish static-pie).
RUN apk add --no-cache \
        musl-dev gcc make perl pkgconf \
        openssl-dev openssl-libs-static \
        binutils \
    && mkdir -p /out

# Only the tracked crate inputs are copied, never the whole submodule directory:
# a host `cargo build` leaves ~300 MB of target/ per tool in the working tree,
# and COPYing that would push a third of a gigabyte of stale glibc objects into
# the build for no reason. Copying (rather than mounting) is also what keeps the
# submodule rule intact — signalbench's build MUST write embedded_binaries/ into
# its own tree, and it writes it into this copy.

# --- ackbarx: SNMP trap forwarder. rustls-tls, no C dependencies. ---
# Directory-level COPY, deliberately: a missing FILE makes COPY fail the whole
# build, an EMPTY DIRECTORY does not. `git clone` without --recursive leaves
# sources/<tool>/ empty, and before this the image could not be built at all
# from such a tree — a hard regression for anyone who cloned normally, and what
# broke the e2e job, which checks out with submodules disabled on purpose.
# .dockerignore keeps host-built target/ out of the context.
COPY sources/ackbarx/ /src/ackbarx/
RUN cd /src/ackbarx \
    && if [ ! -f Cargo.toml ]; then \
         echo "[rust-dist] SKIP ackbarx: submodule not checked out"; exit 0; fi \
    && RUSTFLAGS="-C target-feature=+crt-static" \
       cargo build --release --target x86_64-unknown-linux-musl \
    && cp target/x86_64-unknown-linux-musl/release/ackbarx \
          /out/cortexsim-tool-ackbarx-linux-amd64

# --- xdrtop: live XSIAM/XDR terminal monitor. Needs the OpenSSL archives. ---
# OPENSSL_NO_VENDOR stops openssl-sys from trying to compile its own copy (it
# would need the `vendored` feature, which is not enabled in the submodule);
# OPENSSL_STATIC + OPENSSL_DIR point it at alpine's libssl.a/libcrypto.a.
COPY sources/xdrtop/ /src/xdrtop/
RUN cd /src/xdrtop \
    && if [ ! -f Cargo.toml ]; then \
         echo "[rust-dist] SKIP xdrtop: submodule not checked out"; exit 0; fi \
    && RUSTFLAGS="-C target-feature=+crt-static" \
       OPENSSL_STATIC=1 OPENSSL_NO_VENDOR=1 OPENSSL_DIR=/usr \
       cargo build --release --target x86_64-unknown-linux-musl \
    && cp target/x86_64-unknown-linux-musl/release/xdrtop \
          /out/cortexsim-tool-xdrtop-linux-amd64

# --- signalbench: MITRE-mapped endpoint telemetry generator. TWO PHASES. ---
# Phase 1 is NOT optional and is the reason `cargo build --release` (the
# build_cmd in core/tools/registry.py) has never worked for anyone:
# src/techniques/software.rs:19 does
#     include_bytes!("../../embedded_binaries/pacemaker_helper")
# and embedded_binaries/ is not in the upstream git tree. The helper is a
# separate crate under helpers/pacemaker that must be built and copied in first,
# exactly as upstream's own .github/workflows/build-binary.yml does. Skip it and
# the build dies with "couldn't read embedded_binaries/pacemaker_helper", which
# names a file nobody can find rather than the crate that produces it.
# Because this image is musl-native, the helper's own build.sh recipe
# (x86_64-unknown-linux-musl + crt-static) is what a plain release build already
# produces here.
COPY sources/signalbench/ /src/signalbench/
RUN cd /src/signalbench \
    && if [ ! -f Cargo.toml ]; then \
         echo "[rust-dist] SKIP signalbench: submodule not checked out"; exit 0; fi \
    && test -f helpers/pacemaker/Cargo.toml || { \
         echo "FATAL: sources/signalbench/helpers/pacemaker is missing."; \
         echo "  signalbench's lib embeds helpers/pacemaker's output via"; \
         echo "  include_bytes!(embedded_binaries/pacemaker_helper); without it"; \
         echo "  the crate cannot compile. A gitlink bump moved or removed it."; \
         exit 1; } \
    && ( cd helpers/pacemaker \
         && RUSTFLAGS="-C target-feature=+crt-static" \
            cargo build --release --target x86_64-unknown-linux-musl ) \
    && mkdir -p embedded_binaries \
    && cp helpers/pacemaker/target/x86_64-unknown-linux-musl/release/pacemaker_helper \
          embedded_binaries/pacemaker_helper \
    && RUSTFLAGS="-C target-feature=+crt-static" \
       cargo build --release --target x86_64-unknown-linux-musl \
    && cp target/x86_64-unknown-linux-musl/release/signalbench \
          /out/cortexsim-tool-signalbench-linux-amd64

# --- The gate: a binary that exists is not a binary that runs. ---
# Nothing reaches /app/rust-dist that has not been proven to (a) carry no
# dynamic-linker dependency and (b) actually execute. A silent revert to a
# dynamic libssl link — an alpine bump that moves OpenSSL, an openssl-sys
# release that changes the env-var contract — would otherwise reintroduce the
# exact portability failure this stage removes, without failing the build.
RUN set -eu; \
    if ! ls /out/cortexsim-tool-*-linux-amd64 >/dev/null 2>&1; then \
        echo "[rust-dist] NO tools built — the submodules are not checked out."; \
        echo "  The image will serve an EMPTY rust shelf and /api/tools/binaries"; \
        echo "  will report total: 0. That is the honest state, not a failure:"; \
        echo "  a normal \`git clone\` must still be able to build this image."; \
        echo "  To ship the Rust tools: git submodule update --init --recursive"; \
        exit 0; \
    fi; \
    for f in /out/cortexsim-tool-*-linux-amd64; do \
        if readelf -l "$f" | grep -q INTERP; then \
            echo "FATAL: $f has a PT_INTERP segment — it is dynamically linked."; \
            echo "  It will die on any host without the matching loader/libs."; \
            exit 1; \
        fi; \
        if readelf -d "$f" 2>/dev/null | grep -q NEEDED; then \
            echo "FATAL: $f declares DT_NEEDED shared libraries."; \
            exit 1; \
        fi; \
        "$f" --version >/dev/null 2>&1 || { \
            echo "FATAL: $f does not execute (--version failed)."; \
            exit 1; \
        }; \
        echo "[rust-dist] verified static+executable: $(basename "$f")"; \
    done

# Provenance. The submodule gitlinks are not readable from inside the build (a
# submodule's .git is a pointer into the superproject), so they arrive as build
# args. Left unset they are recorded as null with provenance "unrecorded" —
# never as a plausible-looking value, because a manifest that guesses which
# commit it shipped is worse than one that admits it does not know.
ARG SIGNALBENCH_COMMIT=""
ARG ACKBARX_COMMIT=""
ARG XDRTOP_COMMIT=""

# MANIFEST.json is the pin analogue for a locally-built artifact: the payload
# shelf pins an upstream release, this pins the submodule commit the bytes came
# from. Rendered deterministically — fixed key order, no timestamp, no hostname,
# no builder username — so two builds of the same inputs are diffable.
RUN set -eu; cd /out; \
    if ! ls cortexsim-tool-* >/dev/null 2>&1; then \
        : > SHA256SUMS; \
        printf '%s\n' '{' \
          '  "$generated": { "by": "core/Dockerfile rust-builder stage", "schema": 1 },' \
          '  "builder_image": "rust:1.90-alpine",' \
          '  "target": "x86_64-unknown-linux-musl",' \
          '  "platform": "linux/amd64",' \
          '  "provenance": "not_built",' \
          '  "tools": [],' \
          '  "unsupported_targets": [],' \
          '  "not_built_reason": "the tier-2 Rust submodules were not checked out in this build context; run git submodule update --init --recursive and rebuild"' \
          '}' > MANIFEST.json; \
        echo "[rust-dist] wrote an EMPTY manifest with provenance not_built"; \
        exit 0; \
    fi; \
    sha256sum cortexsim-tool-* > SHA256SUMS; \
    prov="recorded"; \
    [ -n "$SIGNALBENCH_COMMIT$ACKBARX_COMMIT$XDRTOP_COMMIT" ] || prov="unrecorded"; \
    { \
      echo '{'; \
      echo '  "$generated": {'; \
      echo '    "by": "core/Dockerfile rust-builder stage",'; \
      echo '    "schema": 1'; \
      echo '  },'; \
      echo '  "builder_image": "rust:1.90-alpine",'; \
      echo '  "target": "x86_64-unknown-linux-musl",'; \
      echo '  "platform": "linux/amd64",'; \
      echo "  \"provenance\": \"$prov\","; \
      echo '  "tools": ['; \
      sep=''; \
      for t in ackbarx signalbench xdrtop; do \
        f="cortexsim-tool-$t-linux-amd64"; \
        ver=$(./"$f" --version 2>/dev/null | head -1 | awk '{print $NF}'); \
        sz=$(wc -c < "$f" | tr -d ' '); \
        sha=$(sha256sum "$f" | cut -d' ' -f1); \
        case "$t" in \
          ackbarx)     commit="$ACKBARX_COMMIT" ;; \
          signalbench) commit="$SIGNALBENCH_COMMIT" ;; \
          xdrtop)      commit="$XDRTOP_COMMIT" ;; \
        esac; \
        if [ -n "$commit" ]; then commit="\"$commit\""; else commit='null'; fi; \
        [ -z "$sep" ] || printf ',\n'; \
        sep='y'; \
        echo '    {'; \
        echo "      \"filename\": \"$f\","; \
        echo "      \"sha256\": \"$sha\","; \
        echo "      \"size_bytes\": $sz,"; \
        echo '      "static": true,'; \
        echo "      \"submodule_commit\": $commit,"; \
        echo "      \"tool\": \"$t\","; \
        echo '      "verified_exec": true,'; \
        echo "      \"version\": \"$ver\""; \
        printf '    }'; \
      done; \
      printf '\n'; \
      echo '  ],'; \
      echo '  "unsupported_targets": ['; \
      echo '    {'; \
      echo '      "reason": "openssl-sys requires an aarch64 OpenSSL that an x86_64 alpine cannot supply; enabling the openssl crate'"'"'s vendored feature would require editing the xdrtop submodule, which is forbidden. ackbarx and signalbench are reachable on arm64 but are not shipped alone, and nothing here can execute an arm64 binary to prove them.",'; \
      echo '      "target": "linux/arm64",'; \
      echo '      "tools": ["ackbarx", "signalbench", "xdrtop"]'; \
      echo '    },'; \
      echo '    {'; \
      echo '      "reason": "signalbench is Linux-only by design (reads /proc) and ackbarx is a Linux SNMP daemon; cross-building Mach-O from Linux also needs an Apple SDK.",'; \
      echo '      "target": "darwin/amd64,darwin/arm64",'; \
      echo '      "tools": ["ackbarx", "signalbench", "xdrtop"]'; \
      echo '    },'; \
      echo '    {'; \
      echo '      "reason": "no upstream Windows build exists for signalbench or ackbarx; none of the three is exercised on Windows by any scenario.",'; \
      echo '      "target": "windows/amd64",'; \
      echo '      "tools": ["ackbarx", "signalbench", "xdrtop"]'; \
      echo '    }'; \
      echo '  ]'; \
      echo '}'; \
    } > MANIFEST.json \
    && cat MANIFEST.json

# ---- UI builder stage: produce ui/dist/ for static serving ----
FROM node:20-alpine AS ui-builder

WORKDIR /ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci
COPY ui/ ./
RUN npm run build

# ---- Runtime stage: slim image with only the installed packages ----
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Bring in the compiled site-packages from the builder.
COPY --from=python-builder /install /usr/local

WORKDIR /app

# Bundle application code + content so the image is self-contained (no host mounts required).
COPY core/      /app/core/
COPY scenarios/ /app/scenarios/
COPY infra/     /app/infra/
# TTP detection-card corpus + tool-adapter packs. The TtpCatalog and
# adapter_catalog read these from <base>/detection_scanner/ttps and
# <base>/tools/packs at boot; without them the catalogs load empty and every
# scenario with a ttp_ref/adapter_ref logs an "unresolved card" warning.
COPY detection_scanner/ /app/detection_scanner/
COPY tools/             /app/tools/
# FY27 UC/TC master-index snapshot. uctc_registry reads
# <base>/docs/uc_tc_mapping/_v2.2-source at boot; without it the registry loads
# empty, CORTEXSIM_STRICT_REFS silently degrades to advisory (S-10..S-16 stop
# rejecting), /api/uctc and /api/pov serve index_loaded:false, and the UC/TC
# console renders permanently degraded. Only this subtree ships — the rest of
# docs/ is prose and is deliberately left out of the image.
COPY docs/uc_tc_mapping/ /app/docs/uc_tc_mapping/

# Drop the built React UI into the location SimCore mounts as StaticFiles (see core/main.py).
COPY --from=ui-builder /ui/dist/ /app/core/static/

# Prebuilt beacons served by GET /api/agents/binary. Without them the installer
# falls back to a source build (which needs Go on the target) and the endpoint
# returns a structured AGENT_BINARY_UNAVAILABLE naming this directory.
COPY --from=agent-builder /out/ /app/agent-dist/

# The K8s payload shelf, served by GET /api/k8s/payload/{name}. Staged on the
# HOST by ./scripts/build-payloads.sh before `docker build`, deliberately: a
# builder stage that downloaded here would make `docker build` require network,
# which breaks the offline build this feature exists to serve. An empty shelf is
# a valid state — the manifest generator then reports every declared tool as
# unstaged and the bootstrap endpoint refuses with PAYLOAD_NOT_STAGED rather
# than let a pod run a scenario without its tooling.
COPY payloads/ /app/payloads/

# Prebuilt static Rust tools, served by GET /api/tools/binary/{tool} with the
# same idiom as the beacon shelf above (plural collection / singular artifact,
# digest computed on SimCore from the bytes on disk, carried out in an
# X-CortexSim-Tool-SHA256 header, verified by the consumer before execution).
# Without this COPY the endpoint returns a structured TOOL_BINARY_UNAVAILABLE
# naming this directory, and the only way to run signalbench/ackbarx/xdrtop on
# a target is `cargo build --release` on that target — a Rust toolchain plus
# crates.io egress on a customer host, which is the failure this closes.
# An empty rust-dist/ is a valid state; the endpoint reports it as empty rather
# than 500ing.
COPY --from=rust-builder /out/ /app/rust-dist/

ENV CORTEXSIM_BASE_DIR=/app
ENV CORTEXSIM_ENV=production
ENV CORTEXSIM_PORT=8888
ENV PYTHONPATH=/app/core

# core/main.py uses flat imports (config, database, engine.*) — CWD must be /app/core
WORKDIR /app/core

EXPOSE 8888

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8888"]

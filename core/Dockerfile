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
# DC's own SimCore as the only outbound dependency. Kept in lockstep with
# scripts/build-agent-dist.sh (same targets, same flags).
FROM golang:1.22-alpine AS agent-builder

WORKDIR /src
COPY agent/ ./agent/
RUN cd agent && \
    for t in linux/amd64 linux/arm64 darwin/amd64 darwin/arm64; do \
        goos="${t%/*}"; goarch="${t#*/}"; \
        CGO_ENABLED=0 GOOS="$goos" GOARCH="$goarch" \
            go build -trimpath -ldflags="-s -w" \
            -o "/out/cortexsim-agent-${goos}-${goarch}" . || exit 1; \
    done && \
    cd /out && sha256sum cortexsim-agent-* > SHA256SUMS

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

ENV CORTEXSIM_BASE_DIR=/app
ENV CORTEXSIM_ENV=production
ENV CORTEXSIM_PORT=8888
ENV PYTHONPATH=/app/core

# core/main.py uses flat imports (config, database, engine.*) — CWD must be /app/core
WORKDIR /app/core

EXPOSE 8888

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8888"]

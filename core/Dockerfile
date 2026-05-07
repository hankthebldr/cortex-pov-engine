# ---- Python builder stage: compile wheels (psutil etc. need gcc) ----
FROM python:3.11-slim AS python-builder

RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY core/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --prefix=/install -r /tmp/requirements.txt

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

# Drop the built React UI into the location SimCore mounts as StaticFiles (see core/main.py).
COPY --from=ui-builder /ui/dist/ /app/core/static/

ENV CORTEXSIM_BASE_DIR=/app
ENV CORTEXSIM_ENV=production
ENV CORTEXSIM_PORT=8888
ENV PYTHONPATH=/app/core

# core/main.py uses flat imports (config, database, engine.*) — CWD must be /app/core
WORKDIR /app/core

EXPOSE 8888

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8888"]

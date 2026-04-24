FROM python:3.11-slim

# Minimal runtime deps — the heavy build toolchains (cargo/rustc/golang/npm) that
# were previously bundled belong in the jumpbox image, not the shipped SimCore image.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY core/requirements.txt /app/core/requirements.txt
RUN pip install --no-cache-dir -r /app/core/requirements.txt

# Bundle application code + content so the image is self-contained (no host mounts required).
COPY core/      /app/core/
COPY scenarios/ /app/scenarios/
COPY infra/     /app/infra/

ENV CORTEXSIM_BASE_DIR=/app
ENV CORTEXSIM_ENV=production
ENV CORTEXSIM_PORT=8888
ENV PYTHONPATH=/app/core

# core/main.py uses flat imports (config, database, engine.*) — CWD must be /app/core
WORKDIR /app/core

EXPOSE 8888

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8888"]

# CortexSim — convenience targets that run the SAME gates as .github/workflows/ci.yml
# so a green `make ci` locally predicts a green CI run.
#
# Quick reference:
#   make up        — bring SimCore up locally (scripts/dev-up.sh, else docker compose)
#   make down      — stop SimCore
#   make build     — build the production simcore image
#   make test      — backend + agent + ui suites (the three code-test gates)
#   make validate  — detection corpus validity + export determinism
#   make ci        — everything CI runs, in one shot
#
# Override the image tag with `make IMAGE=foo:bar build`.

SHELL := /bin/bash
.DEFAULT_GOAL := help

IMAGE       ?= cortexsim:dev
COMPOSE     ?= docker compose
# A high-entropy secret so the production-mode boot guard (validate_master_key
# in core/config.py) doesn't refuse to start with the `changeme` default.
SECRET      ?= $(shell openssl rand -hex 32)

.PHONY: help up down build test test-backend test-agent test-ui validate \
        validate-detection check-adapters coverage coverage-strict ci clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# -----------------------------------------------------------------------------
# Lifecycle
# -----------------------------------------------------------------------------
up: ## Start SimCore locally (scripts/dev-up.sh if present, else docker compose)
	@if [ -x scripts/dev-up.sh ]; then \
		scripts/dev-up.sh; \
	else \
		echo "scripts/dev-up.sh not found — falling back to docker compose"; \
		CORTEXSIM_SECRET=$(SECRET) $(COMPOSE) up -d --build; \
		echo "SimCore on http://localhost:8888  (health: /api/health)"; \
	fi

down: ## Stop SimCore
	$(COMPOSE) down

build: ## Build the production simcore image
	docker build -f core/Dockerfile -t $(IMAGE) .

# -----------------------------------------------------------------------------
# Code-test gates (mirror ci.yml backend / agent / ui jobs)
# -----------------------------------------------------------------------------
test: test-backend test-agent test-ui ## Run backend + agent + ui suites

test-backend: build ## pytest INSIDE the built prod image (CI 'backend' job)
	docker run --rm \
		-v "$(CURDIR):/repo" \
		-w /repo \
		-e CORTEXSIM_BASE_DIR=/repo \
		-e CORTEXSIM_ENV=development \
		-e CORTEXSIM_SECRET="$(SECRET)" \
		-e PYTHONPATH=/repo/core \
		$(IMAGE) \
		sh -c "pip install --no-cache-dir pytest pytest-asyncio httpx && \
		       pytest tests/ -v --tb=short --ignore=tests/smoke"

test-agent: ## go build + vet + test -race (CI 'agent' job)
	cd agent && go build ./... && go vet ./... && go test ./... -race -count=1 -v

test-ui: ## npm ci + build + vitest (CI 'ui' job)
	cd ui && npm ci && npm run build && npx vitest run

# -----------------------------------------------------------------------------
# Detection + adapter gates (mirror ci.yml detection / adapters jobs)
# -----------------------------------------------------------------------------
validate: validate-detection check-adapters ## Detection corpus + adapter source gates

validate-detection: ## validate.py (0 fail) + export-determinism gate (CI 'detection' job)
	python3 detection_scanner/scripts/validate.py --quiet
	python3 detection_scanner/scripts/export_artifacts.py
	git diff --exit-code detection_scanner/exports/

check-adapters: ## tier-2 adapter source preflight (CI 'adapters' job)
	CORTEXSIM_BASE_DIR=$(CURDIR) scripts/check-adapter-sources.sh

coverage: ## detection coverage-quality report (WARN-only, exit 0)
	python3 detection_scanner/scripts/coverage_report.py

coverage-strict: ## coverage report as a hard gate (exit 1 on floor/target breach)
	python3 detection_scanner/scripts/coverage_report.py --strict

# -----------------------------------------------------------------------------
# Everything
# -----------------------------------------------------------------------------
ci: test validate ## Run every CI gate (backend + agent + ui + detection + adapters)
	@echo "All CI gates passed."

clean: ## Remove the built image
	-docker rmi $(IMAGE) 2>/dev/null || true

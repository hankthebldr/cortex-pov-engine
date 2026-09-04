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

.PHONY: help up down build agent-dist lab-ready check-lab-ready test test-backend test-agent test-agent-cross \
        test-ui validate validate-detection check-refs check-adapters coverage \
        coverage-strict check-agent-shelf rust-dist check-rust-recipe \
        check-rust-shelf check-rust-exec e2e-tierc ground-truth check-ground-truth \
        wiki wiki-check ci clean

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
# UI dev loop
# -----------------------------------------------------------------------------
# The console is BAKED into the image (core/Dockerfile: COPY --from=ui-builder
# /ui/dist/ /app/core/static/) and nothing bind-mounts it, so out of the box a
# one-line CSS change costs a trip through four builder stages. Measured on this
# tree, edit -> visible:
#
#     ui-dev  (vite HMR, :5273)          ~50 ms   no container involved
#     ui-sync (vite build + docker cp)   ~980 ms  container keeps running
#     compose build + force-recreate     ~3340 ms container restarts
#
# The restart is the hidden cost, not the seconds: force-recreate drops enrolled
# agents' connections and every open SSE stream, so a UI tweak mid-run destroys
# the run you were looking at. `ui-sync` never restarts anything.

ui-dev: ## UI hot-reload on :5273, API proxied to the running SimCore (fastest loop)
	@echo "vite dev on http://localhost:5273  — /api proxies to $${CORTEXSIM_DEV_API:-http://localhost:8888}"
	@echo "use this for component + CSS work; it does NOT exercise the production bundle"
	cd ui && npm run dev -- --port 5273 --strictPort

ui-sync: ## Build the real bundle and push it into the RUNNING container (no rebuild, no restart)
	@cd ui && npx vite build
	@# Clear hashed chunks first so the container MIRRORS ui/dist instead of
	@# accumulating every build's assets. Scoped to assets/ on purpose:
	@# /app/core/static/agent is a read-only bind mount (docker-compose.yml),
	@# so an rm -rf over the whole static dir would fail on it.
	@docker exec $(UI_CONTAINER) sh -c 'rm -rf /app/core/static/assets' 2>/dev/null \
	  || { echo "container '$(UI_CONTAINER)' not running - try: make up"; exit 1; }
	@docker cp ui/dist/. $(UI_CONTAINER):/app/core/static/
	@echo "pushed ui/dist -> $(UI_CONTAINER):/app/core/static  (http://localhost:8888)"
	@echo "no rebuild, no restart - enrolled agents and open SSE streams survive"

# Overridable so this works against a differently-named stack (e.g. a worktree's
# compose project, which prefixes the directory name).
UI_CONTAINER ?= cortex-pov-engine-simcore-1

agent-dist: ## Cross-compile the beacon matrix into ./agent-dist (served by /api/agents/binary)
	scripts/build-agent-dist.sh

# The Rust half of the same answer the beacon matrix gives for Go: build once on
# the DC's machine, serve checksummed bytes, and the customer jumpbox needs
# neither rustup nor crates.io egress. Docker is REQUIRED and the script refuses
# without it — a host `cargo build --release` produces a glibc-DYNAMIC binary
# that dies on a customer host, which is worse than no binary at all.
rust-dist: ## Build the static-musl Rust tool matrix into ./rust-dist (served by /api/tools/binary)
	scripts/build-rust-dist.sh

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

test-agent: test-agent-cross ## go build + vet + test -race, all target platforms (CI 'agent' job)
	cd agent && go build ./... && go vet ./... && go test ./... -race -count=1 -v

# The beacon shipped for months unable to compile for Windows (POSIX-only
# Setpgid/syscall.Kill in agent/executor) while 71 scenarios declared
# platforms: [windows] — pull mode was impossible there and no gate noticed.
# This target is that gate. CGO_ENABLED=0 matches scripts/build-agent-dist.sh.
test-agent-cross: ## Cross-compile + vet the beacon for every supported host family
	@set -e; for goos in linux darwin windows; do \
		echo "[agent-cross] GOOS=$$goos build+vet"; \
		( cd agent && CGO_ENABLED=0 GOOS=$$goos go build ./... \
		           && CGO_ENABLED=0 GOOS=$$goos go vet ./... ); \
	done
	@echo "[agent-cross] linux darwin windows OK"

test-ui: ## npm ci + build + vitest (CI 'ui' job)
	cd ui && npm ci && npm run build && npx vitest run

# -----------------------------------------------------------------------------
# Detection + adapter gates (mirror ci.yml detection / adapters jobs)
# -----------------------------------------------------------------------------
validate: validate-detection check-refs check-adapters check-streamer check-agent-shelf check-ground-truth ## Detection corpus + UC/TC ref + adapter source + streamer-fidelity + beacon-shelf + ground-truth gates
# NOTE: check-adapters now also runs `build-rust-dist.sh --check-recipe`, so the
# Rust recipe gate is inside `make validate` at ~50 ms. check-rust-shelf and
# check-rust-exec are NOT in validate: both need a `make build` / `make
# rust-dist` first and cost minutes, so they are opt-in (and paths:-filtered in
# CI). See the rust-dist target above.

# The assertion is about the IMAGE, never the checkout — hence `--entrypoint sh`
# with no `-v`. A host mount would let a `make agent-dist` on the dev box mask an
# agent-builder stage that never built the target, which is exactly how the
# windows/amd64 gap survived: the script emitted 5, the image shipped 4, and
# every gate that looked at the source tree agreed with itself.
check-agent-shelf: ## assert the BUILT IMAGE serves every beacon target (needs `make build`)
	docker run --rm --entrypoint sh $(IMAGE) -c '\
	  set -e; cd /app/agent-dist; sha256sum -c SHA256SUMS; \
	  for t in linux-amd64 linux-arm64 darwin-amd64 darwin-arm64 windows-amd64.exe; do \
	    test -s "cortexsim-agent-$$t" || { echo "MISSING cortexsim-agent-$$t"; exit 1; }; \
	  done; \
	  echo "shelf OK: $$(ls cortexsim-agent-* | wc -l) targets"'

check-rust-recipe: ## ~50ms: assert the Rust build recipes still match the submodule trees
	scripts/build-rust-dist.sh --check-recipe

# Same argument as check-agent-shelf, applied to the Rust matrix: assert the
# IMAGE, never the checkout. The beacon shipped 5 targets from the script and 4
# from the image for months because every gate looked at the source tree and
# agreed with itself. `docker run --entrypoint sh` with no `-v` is deliberate —
# a host mount would let a local `make rust-dist` mask a rust-builder stage that
# never landed.
check-rust-shelf: ## assert the BUILT IMAGE serves the Rust tool matrix (needs `make build`)
	docker run --rm --entrypoint sh $(IMAGE) -c '\
	  set -e; cd /app/rust-dist; sha256sum -c SHA256SUMS; \
	  for t in signalbench ackbarx xdrtop; do \
	    test -s "cortexsim-tool-$$t-linux-amd64" || { echo "MISSING cortexsim-tool-$$t-linux-amd64"; exit 1; }; \
	  done; \
	  echo "rust shelf OK: $$(ls cortexsim-tool-* | wc -l) tools"'

# THE gate this pass exists for. A binary that exists is not a binary that runs:
# a compiled file that dies with `error while loading shared libraries` on a
# customer jumpbox is worse than an honest "not supported", because the DC finds
# out in front of the customer. Every artifact is started on a clean glibc host
# AND a clean musl host, with --network none so nothing can be quietly fetched.
check-rust-exec: ## execute every rust-dist binary on clean ubuntu + alpine (needs `make rust-dist`)
	@set -e; for img in ubuntu:22.04 alpine:3; do \
	  for t in signalbench ackbarx xdrtop; do \
	    printf '  %-14s %-12s ' "$$img" "$$t"; \
	    docker run --rm --network none --platform linux/amd64 \
	      -v "$(CURDIR)/rust-dist:/d:ro" "$$img" \
	      "/d/cortexsim-tool-$$t-linux-amd64" --version; \
	  done; \
	done; \
	echo "[rust-exec] 6/6 started on clean glibc + musl hosts with no network"

validate-detection: ## validate.py (0 fail) + export-determinism gate (CI 'detection' job)
	python3 detection_scanner/scripts/validate.py --quiet
	python3 detection_scanner/scripts/export_artifacts.py
	git diff --exit-code detection_scanner/exports/

check-refs: ## every scenario through the REAL loader under CORTEXSIM_STRICT_REFS
	docker run --rm -v "$(CURDIR):/repo" -w /repo -e CORTEXSIM_BASE_DIR=/repo \
	  -e CORTEXSIM_ENV=development -e PYTHONPATH=/repo/core $(IMAGE) \
	  sh -c "pip install --no-cache-dir -q pytest pytest-asyncio httpx && \
	         pytest tests/engine/test_corpus_refs_strict.py -q"

# gen_wiki.py drives the REAL scenario loader, so it needs core/requirements.txt
# installed. Prefer the repo venv; fall back to whatever python3 is on PATH and
# let the ImportError name the missing dep rather than failing cryptically.
WIKI_PY := $(shell [ -x .venv/bin/python ] && echo .venv/bin/python || echo python3)

wiki: ## build the GitHub wiki tree from the live corpus -> build/wiki/
	$(WIKI_PY) scripts/gen_wiki.py --out build/wiki

wiki-check: ## build the wiki to a temp dir and report page counts only
	$(WIKI_PY) scripts/gen_wiki.py --check

unscoreable-report: ## regenerate docs/uc_tc_mapping/unscoreable-tcs.md from the index snapshot
	python3 scripts/report_unscoreable_tcs.py

crosswalk-report: ## UC/TC crosswalk reconciliation summary
	python3 scripts/uctc_crosswalk_v2.2.py --report

# scripts/generate_ground_truth.py runs uctc_crosswalk_v2.2.py --report and
# coverage_report.py --json (the two named ground-truth commands) plus direct
# filesystem/loader counts, cross-checks every number two independent ways,
# and writes docs/reference/ground-truth.{json,md} — deterministic, no
# timestamps, so a clean regeneration is byte-identical when nothing drifted.
# Runs INSIDE $(IMAGE) (never the host python) so the "boot-truth" fields
# (real Pydantic scenario loader, real AssertionCatalog, the strict-refs
# pytest gate) are always available and the committed file's shape never
# depends on which machine happened to regenerate it.
ground-truth: ## Regenerate docs/reference/ground-truth.{json,md} from the corpus (needs `make build`)
	docker run --rm -v "$(CURDIR):/repo" -w /repo -e CORTEXSIM_BASE_DIR=/repo \
	  -e CORTEXSIM_ENV=development -e PYTHONPATH=/repo/core $(IMAGE) \
	  python3 scripts/generate_ground_truth.py

check-ground-truth: ground-truth ## Ground-truth determinism gate (CI step): regenerate and diff against committed files
	git diff --exit-code docs/reference/ground-truth.json docs/reference/ground-truth.md

check-adapters: ## tier-2 source preflight + de-hand-rolling wiring gate (CI 'adapters' job)
	CORTEXSIM_BASE_DIR=$(CURDIR) scripts/check-adapter-sources.sh
	CORTEXSIM_BASE_DIR=$(CURDIR) python3 scripts/check-adapter-wiring.py --list

e2e-tierc: ## Tier-C isolated-execution assertion suite (CI 'e2e-isolated' job, no docker)
	python3 -m pytest tests/e2e_isolated/test_tier_c_assets.py \
		tests/e2e_isolated/test_tier_c_isolated_exec.py -v --tb=short

check-streamer: ## analytics-streamer emitter↔card dataset reconcile (fails on a dataset mismatch)
	python3 scripts/check-streamer-fidelity.py

coverage: ## detection coverage-quality report (WARN-only, exit 0)
	python3 detection_scanner/scripts/coverage_report.py

coverage-strict: ## coverage report as a hard gate (exit 1 on floor/target breach)
	python3 detection_scanner/scripts/coverage_report.py --strict

lab-ready: ## Regenerate docs/reference/lab-readiness.{json,md} — which scenarios emit signal in a lab
	python3 scripts/lab_readiness.py --json docs/reference/lab-readiness.json --md docs/reference/lab-readiness.md

check-lab-ready: lab-ready ## Determinism gate: regenerate the manifest and fail if it drifts from the committed files
	git diff --exit-code docs/reference/lab-readiness.json docs/reference/lab-readiness.md

# -----------------------------------------------------------------------------
# Everything
# -----------------------------------------------------------------------------
ci: test validate e2e-tierc ## Run every CI gate (backend + agent + ui + detection + adapters + e2e-tierc)
	@echo "All CI gates passed."

clean: ## Remove the built image
	-docker rmi $(IMAGE) 2>/dev/null || true

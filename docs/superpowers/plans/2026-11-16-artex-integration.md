# ARTEX → POV engine (1.3.0) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface the ARTEX AI-pentest platform as a new "Offensive → ARTEX" tab inside the POV engine console, served same-origin via a reverse proxy, with ARTEX running as an opt-in companion service.

**Architecture:** B2 (approved). ARTEX (AGPLv3) runs as its own container + Postgres under a compose `offensive` profile, reached only through SimCore. ARTEX is made serve-under-a-base-prefix (`/artex`) end-to-end; SimCore adds a transparent streaming reverse proxy at `/artex/*` → internal `artex:8787/artex/*`, a `/api/artex/status` probe, a health component, and a new immersive console destination that frames `/artex/`. No ARTEX code is imported into cortex-pov-engine — the process/HTTP boundary keeps AGPL cleanly aggregated.

**Tech Stack:** Python 3.11 / FastAPI / httpx 0.27.2 / Starlette `StreamingResponse` (cortex-pov-engine). Go 1.26 `net/http` + Next.js static export (ARTEX). React (vitest). Docker Compose v2.

**Spec:** `docs/superpowers/specs/2026-11-16-artex-integration-design.md`

## Global Constraints

- **AGPL boundary:** cortex-pov-engine Go/Python code MUST NOT import or vendor ARTEX source. Integration is HTTP-only across a process boundary. ARTEX changes live in the ARTEX repo (its own PR/worktree).
- **Opt-in:** ARTEX + Postgres live behind compose `profiles: ["offensive"]`, default off. The default POV deployment is byte-unchanged.
- **Internal-only ARTEX ports:** ARTEX `8787` and mitmproxy `8788` are NOT published to the host under B2 — reached only via SimCore's proxy on the compose network.
- **A disabled opt-in is `ok`, not degraded:** the `artex` health component reports `ok`/`ARTEX_NOT_ENABLED` when `CORTEXSIM_ARTEX_ORIGIN` is unset, mirroring `probe_mcp`'s `MCP_DISABLED`.
- **Zero outbound when disabled:** `/api/artex/status` and `probe_artex` make no network call when the origin is unset.
- **Pin a concrete `ARTEX_TAG`**, never `:latest`; do not set `ARTEX_UPDATE_GITHUB_TOKEN` (self-update off).
- **Version:** 1.3.0 is set in exactly these coupled places — `docker-compose.yml` `${CORTEXSIM_VERSION:-…}` (×2 lines), `core/api/health.py` `APP_VERSION`, `.env.example` `CORTEXSIM_VERSION=`; `tests/scripts/test_dev_up_existing_env.py` holds health.py ↔ compose together.
- **Prefix is consistent end-to-end:** browser `/artex/…` → SimCore proxy → `artex:8787/artex/…` → ARTEX `StripPrefix("/artex")`. The proxy never rewrites paths or bodies. ARTEX's frontend calls `${base}/api`, never bare `/api`.

## Review Focus

- **SSE buffering (most likely to bite):** ARTEX's activity stream, `/api/side-questions/{id}/events`, and `/api/update/stream` are `text/event-stream`. The proxy must stream chunks through without buffering the whole response, or the live activity view hangs. → pinned in Task B3.
- **Disabled → zero outbound:** with `CORTEXSIM_ARTEX_ORIGIN` unset, status and health must make no socket call (a default POV must not reach the network). → pinned in Tasks B2, B4.
- **Prefix collision:** if ARTEX's frontend still calls bare `/api/...`, it hits SimCore's API instead of ARTEX. The ARTEX build must address its API under `${base}/api`. → pinned in Task A1.
- **CI compose-config gate:** adding a `?`-required `POSTGRES_PASSWORD` behind a profile must keep `docker compose -f docker-compose.yml config -q` green with only a throwaway secret interpolated. → pinned in Task B6.
- **ARTEX back-compat:** the base-prefix change must default OFF so standalone ARTEX (`/api/...` at root) is unaffected. → pinned in Task A1.

---

## Task A1 (ARTEX repo): serve under a configurable base prefix

**Repo/worktree:** `/Users/henry/Github/Github_desktop/ARTEX/.claude/worktrees/repo-integration-1-3-0-fc0a41` (its own PR).

**Files:**
- Modify: `server/server.go` (`Handler()` ~650) — wrap the mux when a base is set.
- Create/Modify: `config` loader — read `ARTEX_BASE_PATH` env (default `""`).
- Modify: `web/next.config.mjs` — `basePath`/`assetPrefix` on the export branch.
- Modify: `web/src/lib/api.ts` (and `web/src/lib/side-questions.ts`) — API base = `${basePath}/api`.
- Create: `docs/embedded-in-povengine.md`.
- Test: `server/server_baseprefix_test.go`.

**Interfaces:**
- Produces: ARTEX served under `${ARTEX_BASE_PATH}` (e.g. `/artex`) for BOTH frontend assets/routes and `/api/*`; `ARTEX_BASE_PATH=""` = today's root behavior.

- [ ] **Step 1: Write the failing Go test** — `server/server_baseprefix_test.go`:
  - `TestHandler_NoBasePath_ServesApiAtRoot`: with base `""`, `GET /api/auth/status` → 200.
  - `TestHandler_BasePath_ServesUnderPrefix`: with base `/artex`, `GET /artex/api/auth/status` → 200 and `GET /api/auth/status` → 404.
  (Construct the server with a base-prefix option; assert via `httptest`.)
- [ ] **Step 2: Run to verify it fails** — `go test ./server -run TestHandler_.*BasePath -v` → FAIL (option/behavior absent).
- [ ] **Step 3: Implement base-prefix wrapping** in `server/server.go`: give `Handler()` (or `New`) an `ARTEX_BASE_PATH` from config; when non-empty, return `http.StripPrefix(base, mux)` wrapped so a bare `/{base}` redirects to `/{base}/`. Keep the mux definitions unchanged (still `/api/...`, `/`).
- [ ] **Step 4: Run to verify it passes** — same command → PASS.
- [ ] **Step 5: Front-end base** — in `web/next.config.mjs` set `basePath`/`assetPrefix` from `process.env.NEXT_PUBLIC_BASE_PATH` on the `isExport` branch; in `web/src/lib/api.ts` change `fetch(\`/api${path}\`)` to `fetch(\`${API_BASE}${path}\`)` where `API_BASE = (process.env.NEXT_PUBLIC_BASE_PATH ?? "") + "/api"`; fix the one `side-questions.ts` call site the same way.
- [ ] **Step 6: Build check** — `cd web && NEXT_PUBLIC_BASE_PATH=/artex NEXT_EXPORT=1 npm run build:static` succeeds and emits assets under `out/artex/_next/…`.
- [ ] **Step 7: Doc** — write `docs/embedded-in-povengine.md`: pinned tag, self-update off, internal-only `8787`/`8788`, served under `/artex` behind the POV engine proxy, `ARTEX_BASE_PATH=/artex`.
- [ ] **Step 8: Commit** (in the ARTEX worktree) — `git add server/server.go server/server_baseprefix_test.go web/next.config.mjs web/src/lib/api.ts web/src/lib/side-questions.ts docs/embedded-in-povengine.md` → `feat(embed): serve under configurable base prefix for POV-engine embedding`.

---

## Task B1 (cortex-pov-engine): `CORTEXSIM_ARTEX_ORIGIN` setting

**Files:**
- Modify: `core/config.py` (Settings) — add the field.
- Modify: `.env.example` — document it (above the compose divider, ~line 177, so compose passes it into the simcore container).
- Test: `tests/test_env_example_coverage.py` (existing — must stay green).

**Interfaces:**
- Produces: `settings.CORTEXSIM_ARTEX_ORIGIN: str` — internal upstream origin (e.g. `http://artex:8787`), default `""` = not enabled.

- [ ] **Step 1: Write the failing test** — add to the env-coverage expectations (or run the existing exact-set test) so a missing `.env.example` line fails.
- [ ] **Step 2: Run** — `PYTHONPATH=core .venv/bin/python -m pytest tests/test_env_example_coverage.py -v` → FAIL (field undocumented).
- [ ] **Step 3: Implement** — add `CORTEXSIM_ARTEX_ORIGIN: str = ""` to `core/config.py` Settings; add the documented line + value to `.env.example` above the compose divider.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** — `git add core/config.py .env.example` → `feat(artex): add CORTEXSIM_ARTEX_ORIGIN setting`.

---

## Task B2 (cortex-pov-engine): `/api/artex/status` probe

**Files:**
- Create: `core/api/artex.py`
- Modify: `core/main.py` (~line 649 area) — `app.include_router(artex_router, prefix="/api")`.
- Test: `tests/api/test_artex_status.py`

**Interfaces:**
- Consumes: `settings.CORTEXSIM_ARTEX_ORIGIN` (Task B1).
- Produces: `GET /api/artex/status` → `{"reachability": "not_enabled"|"unreachable"|"reachable", "initialized": bool|None, "origin_configured": bool, "code": str, "detail": str}`. `probe_artex(settings, client=None) -> dict` reusable by health (Task B4).

- [ ] **Step 1: Write the failing tests** — `tests/api/test_artex_status.py`, injecting an httpx transport/stub:
  - `test_status_not_enabled_makes_no_outbound`: origin `""` → `reachability=="not_enabled"`, `code=="ARTEX_NOT_ENABLED"`, and the (spy) transport was never called.
  - `test_status_reachable_initialized_true`: stub `GET /artex/api/auth/status` → `{"initialized": true}` ⇒ `reachability=="reachable"`, `initialized is True`.
  - `test_status_reachable_needs_setup`: stub → `{"initialized": false}` ⇒ `reachable`, `initialized is False`.
  - `test_status_unreachable_on_connect_error`: stub raises ConnectError ⇒ `reachability=="unreachable"`, `code=="ARTEX_UNREACHABLE"`.
- [ ] **Step 2: Run** — `PYTHONPATH=core .venv/bin/python -m pytest tests/api/test_artex_status.py -v` → FAIL.
- [ ] **Step 3: Implement** `probe_artex` + the route in `core/api/artex.py`: when origin empty, return not_enabled without constructing a client; else `GET {origin}/artex/api/auth/status` with a short timeout via the injectable `httpx.AsyncClient`, map success/parse/connect-error to the three states. Register the router in `core/main.py`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** — `git add core/api/artex.py core/main.py tests/api/test_artex_status.py` → `feat(artex): /api/artex/status reachability+initialized probe`.

---

## Task B3 (cortex-pov-engine): `/artex/*` streaming reverse proxy

**Files:**
- Modify: `core/api/artex.py` — add the proxy mount.
- Modify: `core/main.py` — mount it (NOT under `/api`; at `/artex`).
- Test: `tests/api/test_artex_proxy.py`

**Interfaces:**
- Consumes: `settings.CORTEXSIM_ARTEX_ORIGIN`.
- Produces: an ASGI/route handler for `ALL /artex/{path:path}` that forwards method, path (prefix intact), query string, and request headers to `{origin}/artex/{path}` and streams the response back with its status, headers, and body.

- [ ] **Step 1: Write the failing tests** — `tests/api/test_artex_proxy.py`, with a local stub upstream (an in-process ASGI app or httpx MockTransport):
  - `test_proxy_forwards_get_with_prefix`: `GET /artex/api/health` reaches upstream path `/artex/api/health`, returns its 200 body.
  - `test_proxy_forwards_post_body_and_query`: POST body + `?token=abc` arrive unchanged upstream.
  - `test_proxy_streams_event_stream_unbuffered`: upstream emits 3 SSE frames with delays; the proxy yields them incrementally (assert first frame arrives before the stream closes; `Content-Type: text/event-stream` preserved).
  - `test_proxy_502_when_origin_unreachable`: connect error → 502 with a structured JSON error.
  - `test_proxy_404_when_disabled`: origin `""` → 404/`ARTEX_NOT_ENABLED`, no outbound.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** the proxy using `httpx.AsyncClient.stream(method, url, headers=…, content=…)` wrapped in Starlette `StreamingResponse(upstream.aiter_raw(), status_code=…, headers=…, media_type=…)`; drop hop-by-hop headers; do not set `Content-Length` when streaming. Mount at `/artex/{path:path}` in `core/main.py`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** — `git add core/api/artex.py core/main.py tests/api/test_artex_proxy.py` → `feat(artex): SSE-safe reverse proxy at /artex/*`.

---

## Task B4 (cortex-pov-engine): `artex` health component

**Files:**
- Modify: `core/api/health.py` — `probe_artex` wrapper, `COMPONENT_KEYS`, components dict (~1046/1149).
- Test: `tests/api/test_health.py` (existing exact-set assertion must be updated).

**Interfaces:**
- Consumes: `probe_artex` (Task B2) or a thin health-local probe reusing it.
- Produces: `components["artex"]` in `/api/health`.

- [ ] **Step 1: Write the failing test** — in `tests/api/test_health.py`: `"artex"` is in the response `checked`/components; with origin unset, `components["artex"]["status"]=="ok"` and `code=="ARTEX_NOT_ENABLED"`. Update the exact-`COMPONENT_KEYS`-set assertion to include `"artex"`.
- [ ] **Step 2: Run** — `PYTHONPATH=core .venv/bin/python -m pytest tests/api/test_health.py -v` → FAIL.
- [ ] **Step 3: Implement** — add `"artex"` to `COMPONENT_KEYS`; `components["artex"] = _guard("artex", lambda: probe_artex_health(settings))`; the probe returns `ok`/`ARTEX_NOT_ENABLED` when unset (mirroring `probe_mcp`), `ok`/`ARTEX_READY` when reachable, `degraded`/`ARTEX_UNREACHABLE` when configured-but-down. Zero outbound when unset.
- [ ] **Step 4: Run** → PASS; also run the full `tests/api/test_health.py`.
- [ ] **Step 5: Commit** — `git add core/api/health.py tests/api/test_health.py` → `feat(artex): /api/health artex component (ok when disabled)`.

---

## Task B5 (cortex-pov-engine): console "Offensive → ARTEX" tab

**Files:**
- Modify: `ui/src/app/destinations.jsx` — `JOURNEYS` (+`offensive`) and `DESTINATIONS` (+`artex`).
- Create: `ui/src/components/console/ArtexSurface.jsx`
- Test: `ui/src/components/console/__tests__/ArtexSurface.test.jsx`, and the existing `DestinationNav` set test.

**Interfaces:**
- Consumes: `GET /api/artex/status` (Task B2).
- Produces: destination `{ id:'artex', label:'ARTEX', group:'offensive', icon:<chosen>, Component: ArtexSurface, immersive:true, exitTo:'runs' }`.

- [ ] **Step 1: Write the failing tests** — `ArtexSurface.test.jsx` (vitest + jsdom), mocking the status client:
  - renders a Cortex-styled **setup gate** (kit `StatusIcon`/`Button`) when status is `not_enabled` or `unreachable`;
  - renders a **frame** whose `src` ends in `/artex/` when `reachable` + `initialized`;
  - renders a "finish first-run setup" gate when `reachable` + `!initialized`.
  Plus: the destinations/`DestinationNav` test includes `artex`.
- [ ] **Step 2: Run** — `cd ui && npx vitest run src/components/console/__tests__/ArtexSurface.test.jsx` → FAIL.
- [ ] **Step 3: Implement** — add the `offensive` journey + `artex` destination (immersive, like `composer`); write `ArtexSurface.jsx` that fetches status on mount and branches gate/frame as above, using `kit` components + design tokens for the wrapper.
- [ ] **Step 4: Run** → PASS; run `npx vitest run` for the console dir to catch set-assertion fallout.
- [ ] **Step 5: Commit** — `git add ui/src/app/destinations.jsx ui/src/components/console/ArtexSurface.jsx ui/src/components/console/__tests__/ArtexSurface.test.jsx` → `feat(artex): Offensive→ARTEX console tab (status-gated frame)`.

---

## Task B6 (cortex-pov-engine): compose profile + 1.3.0 version bump

**Files:**
- Modify: `docker-compose.yml` — `artex` + `postgres` under `profiles: ["offensive"]`; version default ×2.
- Modify: `core/api/health.py` `APP_VERSION`; `.env.example` `CORTEXSIM_VERSION`; `core/config.py` version comment.
- Test: `tests/scripts/test_dev_up_existing_env.py` (health↔compose), `tests/test_compose_hardening.py` if present; CI `docker compose config -q`.

**Interfaces:**
- Consumes: Task B1 (`CORTEXSIM_ARTEX_ORIGIN` passed into simcore), Task A1 (ARTEX served under `/artex`, `ARTEX_BASE_PATH=/artex`).

- [ ] **Step 1: Write/adjust the failing test** — bump the expected version in `tests/scripts/test_dev_up_existing_env.py` to `1.3.0` (and any health-version assertion) → it now fails against the current `1.2.5`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** —
  - `docker-compose.yml`: add `artex` (image `autumn27/artex:${ARTEX_TAG:?pin a concrete tag}`, `environment: ARTEX_PG_DSN`, `ARTEX_BASE_PATH=/artex`, NO self-update token, NO host port publish), `postgres` (own `volumes: artex_pgdata`), both `profiles: ["offensive"]`, `depends_on` healthcheck; set `CORTEXSIM_ARTEX_ORIGIN=http://artex:8787` into the `simcore` env; bump `${CORTEXSIM_VERSION:-1.3.0}` on both lines.
  - `core/api/health.py`: `APP_VERSION = "1.3.0"`; `.env.example`: `CORTEXSIM_VERSION=1.3.0`; `core/config.py` comment.
- [ ] **Step 4: Run** — the version test → PASS; then **prove the CI gate**: `docker compose -f docker-compose.yml config -q` with only a throwaway `CORTEXSIM_SECRET` exits 0 (if the profiled `?`-required `POSTGRES_PASSWORD` trips it, give it a compose default or guard and re-run).
- [ ] **Step 5: Commit** — `git add docker-compose.yml core/api/health.py .env.example core/config.py tests/scripts/test_dev_up_existing_env.py` → `feat(artex): opt-in offensive compose profile; bump 1.3.0`.

---

## Task B7 (cortex-pov-engine): docs

**Files:**
- Modify: `CLAUDE.md` (new "Offensive plane / ARTEX" section), `CHANGELOG.md` (1.3.0 entry), the MVP/launch runbook (`--profile offensive`).

- [ ] **Step 1: Write** the CLAUDE.md section (what ARTEX is, AGPL boundary, opt-in profile, `/artex` proxy, separate `/setup`, internal-only ports, `tenant-verified` unaffected — ARTEX is offensive, not a tenant claim), the CHANGELOG 1.3.0 entry, and the runbook bring-up line.
- [ ] **Step 2: Commit** — `git add CLAUDE.md CHANGELOG.md docs/release/…` → `docs(artex): document the Offensive/ARTEX integration (1.3.0)`.

---

## Task B8 (cortex-pov-engine): end-to-end verification (manual/e2e)

Not a unit task — the browser-level proof the unit suites cannot give.

- [ ] Bring up `CORTEXSIM_SECRET=… POSTGRES_PASSWORD=… ARTEX_TAG=<pinned> docker compose --profile offensive up -d --build`.
- [ ] `GET /api/health` → `artex` component present and `ok`/`ARTEX_READY` (origin set); `GET /api/artex/status` → reachable.
- [ ] In the console, open **Offensive → ARTEX**: the setup gate appears first-run; completing ARTEX's `/setup` in the frame lands the ARTEX dashboard; the activity stream (SSE) updates live through the proxy (confirms Task B3 doesn't buffer).
- [ ] Confirm ARTEX `8787`/`8788` are NOT reachable from the host directly (`curl` to the host port fails) — only via `/artex/*`.
- [ ] Capture a screenshot of the embedded ARTEX tab for the PR.

---

## Self-review notes

- **Spec coverage:** tab (B5) · separate setup = ARTEX `/setup` in-frame + gate (B5, B2 initialized axis) · same functionality = transparent proxy (B3) · opt-in (B6) · AGPL boundary (A1 own repo + Global Constraints) · health ok-when-disabled (B4) · version (B6) · docs (B7). ARTEX `basePath`/prefix (A1). Covered.
- **Ordering:** A1 first (gives the proxy a real target), then B1→B2→B3→B4→B5→B6→B7, B8 last. B2/B3 can be built against a stub upstream before A1 lands, but integration (B8) needs A1.
- **Types:** `probe_artex` dict shape is shared by B2 and B4; `CORTEXSIM_ARTEX_ORIGIN` by B1/B2/B3/B6; `/artex/` frame src by A1/B3/B5. Consistent.
- **Review Focus:** SSE (B3), zero-outbound (B2/B4), prefix collision (A1), CI config-q (B6), ARTEX back-compat (A1) — each pinned to a task's tests above.

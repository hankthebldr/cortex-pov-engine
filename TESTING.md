# CortexSim — Testing Strategy

> Single source of truth for how to validate a CortexSim deployment, from
> unit tests on a laptop to a smoke pass against a lab jumpbox.

## The pyramid

```
                       ┌────────────────────────────────────┐
                       │  Tier 5 — Lab deployment           │   manual / one-shot
                       │  preflight + lab-target-verify     │
                       ├────────────────────────────────────┤
                       │  Tier 2b — Playwright E2E          │   5 spec files
                       │  ui/tests/e2e/*.spec.ts            │
                       ├────────────────────────────────────┤
                       │  Tier 1 — API smoke                │   4 test files
                       │  tests/smoke/                      │
                       ├────────────────────────────────────┤
                       │  Tier 3 — Backend coverage         │   ~30 test files
                       │  tests/api/ + tests/engine/        │
                       ├────────────────────────────────────┤
                       │  Tier 2a — UI Vitest               │   7 test files
                       │  ui/src/components/__tests__/      │
                       ├────────────────────────────────────┤
                       │  Tier 4 — Go agent                 │   3 test files
                       │  agent/**/*_test.go                │
                       └────────────────────────────────────┘
                            fastest, broadest at the bottom
```

## TL;DR — daily commands

| Want to… | Run |
|---|---|
| Confirm your laptop install is healthy | `bash scripts/installer/preflight.sh` |
| Run all Python tests (no live SimCore needed) | `pytest tests/ --ignore=tests/smoke -v` |
| Run Go agent tests | `cd agent && go test ./... -race` |
| Run UI unit tests | `cd ui && npm test` |
| Run UI E2E (needs SimCore running) | `cd ui && npm run test:e2e` |
| Run the full lab smoke suite | `scripts/smoke/lab-smoke.sh` |
| Smoke against a remote jumpbox | `scripts/smoke/lab-smoke.sh --target=jumpbox --url=https://jb.lab:8888` |
| Verify a lab target before deploying an agent | `scripts/smoke/lab-target-verify.sh --server=https://jb:8888` |

## Tier 1 — API smoke (`tests/smoke/`)

End-to-end tests that speak HTTP to a real SimCore instance. They auto-skip
if `/api/health` isn't reachable within `CORTEXSIM_SMOKE_TIMEOUT` (default 60s),
so the same files are safe to run anywhere.

Files:
- `test_health_and_catalog.py` — `/api/health`, OpenAPI shape, scenario catalogue
  populated for every active plane, infra modules listed, MITRE coverage renders
- `test_run_lifecycle_push.py` — launch → seed results → validate → coverage →
  markdown + JSON report → detection matrix CSV → Navigator layer → bundle tar.gz
- `test_agent_lifecycle.py` — register (new + idempotent), poll-unknown 404,
  poll-idle returns `{"task": null}`, full pull-mode cycle
- `test_scenario_catalog_integrity.py` — every loaded scenario is push-launchable
  and seeds Result rows

### Observation strategy

`tests/smoke/observation_strategy.py` factors out the **"is the lab healthy from
a detection-quality perspective?"** decision. Three modes, selected via
`CORTEXSIM_OBSERVATION_STRATEGY`:

| Strategy | What it asserts | Use it for |
|---|---|---|
| `structural` | Result rows seeded from `expected_detections` | Fastest sanity check; CI without tenant |
| `synthetic` *(default)* | Auto-marks Results observed via `PUT /api/results/{id}/validate`, exercises full MTTD + report pipeline | CI default; lab dry-runs |
| `cortex_xql` | Queries the Cortex tenant via XQL for the expected detections and only marks the ones that fired | Real-tenant POV dry-runs **(not implemented yet — see [observation_strategy.py](tests/smoke/observation_strategy.py))** |

## Tier 2 — UI tests

### 2a · Vitest unit (`ui/src/components/__tests__/`)

- `PlaneSelector.test.jsx` — counts derived from API, click selection, error degrade
- `ScenarioBrowser.test.jsx` — text filter, plane filter query string, row click callback
- `LaunchPanel.test.jsx` — pull mode + agent picker + identity selector, push mode + format toggle, error surfacing
- `ResultsValidationWizard.test.jsx` — render seeded results, PUT validate flow
- `InfraGenerator.test.jsx` — module list, POST /api/infra/generate payload shape
- `EalConsole.test.jsx` — plugin/campaign list rendering
- `smoke.test.jsx` — crash-resistance render for the remaining components

Mock fetch lives in [`ui/src/test/mockFetch.js`](ui/src/test/mockFetch.js) —
all tests use it; default route returns 404 so unmocked calls are loud.

```bash
cd ui
npm install
npm test                # one-shot
npm run test:watch      # TDD loop
npm run test:coverage   # with v8 coverage report
```

### 2b · Playwright E2E (`ui/tests/e2e/`)

Five golden-path specs, each documents one DC workflow end-to-end. Requires
a running SimCore at `CORTEXSIM_BASE_URL` (default `http://localhost:8888`).

```bash
cd ui
npm run test:e2e:install   # one-time: install Chromium
npm run test:e2e           # headless
npm run test:e2e:headed    # see the browser
```

| Spec | Validates |
|---|---|
| `01-health-and-shell.spec.ts` | App shell loads, header chrome, plane selector |
| `02-scenario-launch-push.spec.ts` | UI-driven push launch path |
| `03-validation-and-report.spec.ts` | Validation → 100% coverage → all 3 report artefacts |
| `04-mitre-and-infra.spec.ts` | MITRE heatmap + Infra Generator surface |
| `05-eal-campaign.spec.ts` | EAL plugin list rendered |

Helpers in `ui/tests/e2e/_fixtures.ts` give every test an `api` fixture for
pre-seeding via real HTTP (faster than UI clicks for setup).

## Tier 3 — Backend (`tests/api/` + `tests/engine/`)

Direct router tests using in-memory SQLite — no orchestrator dependency.
See `tests/api/conftest.py` for the shared `make_client` factory.

- `tests/api/test_agents_api.py` — register + idempotency, list, poll 404 / null,
  `last_seen` advances on poll
- `tests/api/test_results_api.py` — list, per-run with coverage + by-type +
  MTTD, validate (sets `observed_at`, computes `mttd_seconds`), notes update
- `tests/api/test_runs_api.py` — list/get, report JSON + markdown + matrix CSV
  + Navigator layer + bundle tarball decompresses to expected artefacts,
  output append, complete → status transition
- `tests/api/test_mitre_api.py` — empty / scenario-only / run-not-detected /
  detected, per-step technique override, by-tactic grouping
- `tests/engine/test_scenario_catalog.py` — parametrised over every YAML in
  `scenarios/**/*.yml`:
  - parses + validates against `ScenarioSchema`
  - id format and filename alignment
  - has at least one step + one expected detection
  - plane field matches directory location
  - all `scenario_id`s unique across the library

```bash
pytest tests/api -v
pytest tests/engine/test_scenario_catalog.py -v   # rejects malformed YAML at PR time
```

## Tier 4 — Go agent (`agent/**/*_test.go`)

- `agent/identity/harness_test.go` — command wrapping for every identity mode
  (direct/runuser/sudo_u/su), shell-quote escaping, unknown-mode rejection
- `agent/executor/shell_test.go` — stdout/stderr capture, non-zero exit is
  not an error, missing binary surfaces via exit code
- `agent/beacon/client_test.go` — register, poll 404 = no task, poll → decode,
  send-output and complete payload shape, **plus pinned wire-contract tests**
  (`TestPollTasks_DecodesWrappedTaskEnvelope`, `TestPollTasks_NullTaskIsIdle`)
  proving the `{"task": …}` envelope is unwrapped correctly in both branches.

```bash
cd agent
go vet ./...
go test ./... -race -count=1 -v
```

### Task-envelope contract (resolved)

`GET /api/agents/{id}/tasks` returns `{"task": null}` or `{"task": {...}}` from
FastAPI. `beacon.PollTasks()` unwraps that envelope (decodes into an anonymous
`struct{ Task *Task }`), so a `null` body maps cleanly to `(nil, nil)` / "no
task" and a populated body returns the inner `*Task`. This was previously a
known gap (the client decoded a bare `Task` and silently treated every real
response as idle); it is now fixed in `client.go` and locked by the two wire-
contract tests above.

## Tier 5 — Lab deployment verification

### 5a · `scripts/installer/preflight.sh`

Read-only host audit. Three modes:

```bash
scripts/installer/preflight.sh prereqs    # before install.sh
scripts/installer/preflight.sh installed  # after install.sh — checks artefacts + /api/health
scripts/installer/preflight.sh both       # default
```

Exits non-zero only on hard failures; warnings (e.g. submodule build
missing) print yellow but don't break the build. CI runs `prereqs` mode
on every push to keep the installer entrypoint warm.

### 5b · `scripts/smoke/lab-smoke.sh`

Top-level driver. Brings up local docker compose (if needed), runs the
entire Tier 1 suite against it, tears down on exit:

```bash
scripts/smoke/lab-smoke.sh                          # local
scripts/smoke/lab-smoke.sh --target=jumpbox --url=https://jb.lab:8888
scripts/smoke/lab-smoke.sh --strategy=structural    # skip auto-validation
scripts/smoke/lab-smoke.sh --keep                   # leave compose up after
```

### 5b · `scripts/smoke/lab-target-verify.sh`

Run on a lab attack target (NOT the jumpbox) to confirm the box is ready
to host a `cortexsim-agent` beacon: OS sanity, identity-harness
prerequisites (`runuser`/`sudo`/`su`), service-account presence,
SimCore reachability, register/poll round-trip:

```bash
# On a lab target:
bash scripts/smoke/lab-target-verify.sh --server=https://jumpbox.lab:8888
```

Exit codes:
- `0` lab-ready
- `1` missing prerequisite
- `2` cannot reach SimCore
- `3` identity harness incomplete

### 5c · GitHub Actions

`.github/workflows/test.yml` runs all tiers in parallel, with `e2e-stack`
gated on the faster jobs:

```
python-tests ─┐
go-tests     ─┼─→ e2e-stack (compose up SimCore, run smoke + Playwright)
ui-unit      ─┘
installer-preflight  (parallel, fast)
```

## What "lab-ready" means here

A CortexSim deployment is lab-ready when **all** of the following pass:

1. `scripts/installer/preflight.sh installed` reports zero failures
2. `scripts/smoke/lab-smoke.sh --target=<host>` passes
3. On at least one lab target, `scripts/smoke/lab-target-verify.sh` reports
   "target is lab-ready"
4. A pull-mode scenario, launched via the UI against that target, completes
   with `status: complete` and the DC's validation marks at least one
   detection observed end-to-end

The first three are automatable. The fourth is the irreducible manual
step: SimCore generates the signal, but it is the SOC's tenant — not
SimCore — that owns the "did Cortex actually detect this?" verdict.
`CORTEXSIM_OBSERVATION_STRATEGY=cortex_xql` is the placeholder for
automating step 4 once the XQL query shape is agreed with the SOC team.

## Adding a new test

| Adding… | Lives under | Pattern to follow |
|---|---|---|
| New scenario YAML | `scenarios/<plane>/<id>.yml` | `tests/engine/test_scenario_catalog.py` will pick it up automatically |
| New FastAPI router | `core/api/<name>.py` | Mirror `tests/api/test_results_api.py` — `make_client` + in-memory DB |
| New UI component | `ui/src/components/<Name>.jsx` | Add a `.test.jsx` next door using `installRoutes()` from `mockFetch.js` |
| New cross-cutting flow | UI + API together | Add a `.spec.ts` under `ui/tests/e2e/` and a matching smoke test under `tests/smoke/` |
| New EAL plugin | `core/eal_simulator/plugins/` | Follow the existing `tests/eal_simulator/test_plugin_*.py` template |

## Quick troubleshooting

| Symptom | Probable cause |
|---|---|
| Smoke tests skip with "SimCore not reachable" | `docker compose up` didn't finish — `docker compose logs simcore` |
| Playwright "browser not installed" | Run `npx playwright install --with-deps chromium` once |
| `pytest tests/smoke` hangs | `CORTEXSIM_OBSERVATION_STRATEGY=cortex_xql` set but no tenant — switch to `synthetic` |
| Coverage in MITRE heatmap is 0 even after observe | Result rows lacked `executed_at` — check orchestrator seeded them |
| `getScenarios` returning empty array in UI | Should be fixed (`ui/src/api/client.js` unwraps `data.scenarios`); if recurring, the API may have changed shape |

# CortexSim — HTTP API + Agent Lifecycle Reference

> **Domain:** HTTP API surface + agent (beacon) lifecycle + execution-mode mechanics.
> **Scope:** Every `@router` route across `core/api/*.py`, the ORM state machines in
> `core/models.py`, the Go beacon poll/execute/report loop in `agent/`, the
> orchestrator task queue (`core/engine/orchestrator.py`), and the push-bundle
> generator (`core/engine/push_generator.py`).
> **Audience:** Anyone building the lifecycle phase (Tier-C isolated-execution
> backend) or wiring new UI flows. Exhaustive by design — nothing sampled.
>
> Last reconciled against source: commit `b7eebc5` (branch `main`).
> **Lifecycle update (2026-06-08):** abort, SSE event streaming, agent
> heartbeat/staleness, and the `aborted` Run state have all shipped. The "MISSING"
> notes below have been updated to reflect the live surface.

---

## 0. Topology at a glance

```
┌──────────────┐  HTTP/JSON   ┌─────────────────────────┐  in-mem queue   ┌──────────────┐
│  React UI    │ ───────────► │  SimCore (FastAPI :8888) │ ──────────────► │  Go beacon   │
│  (static SPA)│ ◄─────────── │  routers under /api      │ ◄────────────── │  agent (pull)│
└──────────────┘   poll/REST  └─────────────────────────┘  poll/report    └──────────────┘
                                        │
                                        │ push mode: render self-contained
                                        ▼  bash / k8s bundle (no runtime dep)
                                 DC downloads + runs offline
```

- **No Cortex API connection.** SimCore generates signals *into* the customer
  environment; it never reads alerts out. Detection confirmation is a manual DC
  action via `PUT /api/results/{id}/validate`.
- **Transport is plain JSON over HTTP, plus Server-Sent Events for live updates.**
  SSE (`text/event-stream`) now backs `GET /api/runs/{id}/events` (scoped) and
  `GET /api/events` (global); the rest of the surface remains REST/JSON. No
  WebSocket, no gRPC (GAP-API-002 closed 2026-06-08).
- **All 11 routers** are mounted under `/api` in `core/main.py` (the 11th is the
  new `events` router). Plus one app-level route: `GET /api/health`, and a static
  file mount at `/`. **65 HTTP routes total** (64 `@router` routes + `/api/health`).

---

## 1. Full REST endpoint table (grouped by router)

All routers are included with `prefix="/api"` in `core/main.py`. The `prefix`
column below shows the router's own prefix; the full path is `/api` + prefix + route.

Standard structured error envelope across the codebase:
`{"error": "...", "code": "...", "detail": "..."}` (and `path` for TTP schema
errors). Unhandled exceptions → `500 {"error":"Internal server error","code":"INTERNAL_ERROR","detail":<str>}`
(`core/main.py` global handler). `CryptoError` → `500 {"code":"CRYPTO_ERROR"}`.

### 1.1 Health (app-level, `core/main.py`)

| Method | Full path | Purpose | Response |
|--------|-----------|---------|----------|
| GET | `/api/health` | Liveness probe | `{"status":"ok","version":"1.0.0"}` |

> **GAP-API-007:** `version` is hard-coded `"1.0.0"`; the report footer says
> `CortexSim v1.0`. No build/commit stamp. The UI (`AppConsole.jsx:115`) notes
> health "doesn't yet expose sensor status."

### 1.2 Scenarios (`core/api/scenarios.py`, prefix `/scenarios`)

| Method | Full path | Purpose | Request | Response |
|--------|-----------|---------|---------|----------|
| GET | `/api/scenarios` | List scenarios; optional filters | query: `plane`, `uc_ref`, `ttp_ref` | `{"scenarios":[<scenario.to_dict()>],"total":N}` |
| GET | `/api/scenarios/{scenario_id}` | Single scenario detail | — | `<scenario.to_dict()>` or 404 `SCENARIO_NOT_FOUND` |
| GET | `/api/scenarios/{scenario_id}/infra-hints` | Resolve `external_tools[].adapter_ref` → IaC module suggestions | — | `{scenario_id, plane, adapter_refs[], resolved_adapters[], unresolved_refs[], suggested_modules[]}` |
| GET | `/api/scenarios/{scenario_id}/download` | Self-contained execution bundle | query: `format=bash\|k8s` (default `bash`) | `PlainTextResponse` shell/yaml w/ `Content-Disposition` attachment; 400 `INVALID_FORMAT` |

- `plane` is upper-cased server-side; `uc_ref` exact-match; `ttp_ref` filtered in
  Python (full scan of `steps[].expected_detections[].ttp_ref`, justified by small
  catalog ~50-69 scenarios).
- `download` calls `push_generator.generate_bash` / `generate_k8s` directly — see §4.

### 1.3 Runs (`core/api/runs.py`, **no router prefix** — routes are absolute)

> Note: this router declares `APIRouter(tags=["runs"])` with **no prefix**, so its
> routes mount directly under `/api`. The launch route is `/api/run` (singular);
> everything else is `/api/runs/...` (plural). This singular/plural split is a
> historical wart (GAP-API-008).

| Method | Full path | Purpose | Request body | Response |
|--------|-----------|---------|--------------|----------|
| POST | `/api/run` | Launch a scenario run (pull or push) | `LaunchRequest`: `{scenario_id, mode, target_agent_id?, identity?, consent?}` | `{run_id, mode, message, download_url?}`; 400 `INVALID_MODE`; 422 `LAUNCH_FAILED` |
| GET | `/api/runs` | List all runs (newest first) | — | `{"runs":[<run.to_dict()>],"total":N}` |
| GET | `/api/runs/{run_id}` | Run detail + status | — | `<run.to_dict()>`; 404 `RUN_NOT_FOUND` |
| GET | `/api/runs/{run_id}/report` | POV report | query: `format=markdown\|json` (default markdown) | markdown `PlainTextResponse` **or** JSON `{run, scenario, results, coverage, mttd, tools_used}`; 404 |
| GET | `/api/runs/{run_id}/report/matrix` | detection_matrix.csv (Phase 8) | — | `text/csv` attachment |
| GET | `/api/runs/{run_id}/report/navigator` | ATT&CK Navigator v4.5 layer JSON | — | `application/json` attachment |
| GET | `/api/runs/{run_id}/report/bundle` | tar.gz of matrix + navigator + exec_summary | — | `application/gzip` attachment |
| POST | `/api/runs/{run_id}/output` | Agent streams output (append) | `OutputRequest`: `{output}` | `{"status":"ok","run_id"}`; 404 |
| POST | `/api/runs/{run_id}/complete` | Agent reports completion | `CompleteRequest`: `{exit_code, summary}` | `{"status":"complete"\|"failed","run_id"}`; 404 |
| POST | `/api/runs/{run_id}/abort` | Operator-initiated abort | — | `{"status":"aborted"\|<terminal>,"run_id","was_terminal"}`; 404 `RUN_NOT_FOUND` |
| GET | `/api/runs/{run_id}/control` | Agent stop-signal poll | — | `{"abort":bool,"run_id","status"}` |
| GET | `/api/runs/{run_id}/events` | SSE stream scoped to one run (+ global) | — | `text/event-stream` |

> The single-run SSE route lives in the dedicated `events` router (`core/api/events.py`)
> but its path falls under `/api/runs/{run_id}/...`; the global stream is
> `GET /api/events`. See §1.12.

- **`consent`** (`dict[str,bool]`) carries launch-time authorization for gated tool
  adapters: `c2_authorized` (for `safety_class: c2-framework`) and
  `simulation_authorized` (for `dual-use-lab-only`). The orchestrator refuses to
  create a Run and returns 422 `LAUNCH_FAILED` if a gated adapter lacks consent
  (see §3.1 / `_check_adapter_consent`).
- `/complete` sets run status to `complete` if `exit_code == 0` else `failed`,
  stamps `completed_at`, and appends a `--- COMPLETION SUMMARY ---` block to
  `run.output`.
- **`/abort`** (GAP-API-001, **closed 2026-06-08**) transitions a `pending`/`running`
  run to the `aborted` state, stamps `completed_at`, dequeues the task via
  `orchestrator.abort(run_id)`, and publishes a `run.status` SSE frame. It is
  **idempotent**: a run already in a terminal state returns 200 with
  `was_terminal: true` rather than an error.
- **`/control`** is the lightweight stop-signal the in-flight agent polls. It
  returns `abort=true` when the run is in the orchestrator's aborted set, has
  reached any terminal status, or has vanished (DB reset) — so the agent halts
  rather than spinning.

### 1.4 Results (`core/api/results.py`, prefix `/results`)

| Method | Full path | Purpose | Request body | Response |
|--------|-----------|---------|--------------|----------|
| GET | `/api/results` | All results across all runs (newest first) | — | `{"results":[...],"total":N}` |
| GET | `/api/results/{run_id}` | Results for a run + coverage + MTTD stats | — | `{run_id, results[], total, coverage:{observed,total,pct,by_type}, mttd}`; 404 if run missing |
| PUT | `/api/results/{result_id}/validate` | DC marks detection observed/not | `ValidateRequest`: `{observed:bool, notes?}` | `<result.to_dict()>`; 404 `RESULT_NOT_FOUND` |
| PUT | `/api/results/{result_id}/notes` | Update notes only | `NotesRequest`: `{notes}` | `<result.to_dict()>`; 404 |

- `validate` with `observed=true` stamps `observed_at = utcnow()` (enables MTTD =
  `observed_at - executed_at`); `observed=false` clears `observed_at`.
- Note the path-param type asymmetry: `/results/{run_id}` takes a **string** run id;
  `/results/{result_id}/validate` takes an **int** PK. Both live under the same
  prefix; FastAPI disambiguates by the trailing segment.

### 1.5 Tools (`core/api/tools.py`, prefix `/tools`)

| Method | Full path | Purpose | Request | Response |
|--------|-----------|---------|---------|----------|
| GET | `/api/tools` | List built-in tools + live+DB-merged runtime status | — | `{"tools":[{tool_name,status,pid,port,install_path,description,plane,type,last_health_check}],"total":N}` |
| GET | `/api/tools/adapters` | Tool-adapter catalog (slim cards) | query: `plane`, `tier`, `safety_class`, `category` (compose AND) | `{"adapters":[<summary>],"total":N}` |
| GET | `/api/tools/adapters/{adapter_id}` | Full adapter pack | — | `adapter.model_dump()`; 404 `ADAPTER_NOT_FOUND` |
| POST | `/api/tools/{tool_name}/install` | Build tool from submodule source | — | `{"status":"installed",...}`; 404 `TOOL_NOT_FOUND`; 500 `INSTALL_FAILED` |
| POST | `/api/tools/{tool_name}/start` | Start managed process | `StartParams`: `{params:{}}` | `{"status":"running","pid",...}`; 404; 500 `START_FAILED` |
| POST | `/api/tools/{tool_name}/stop` | Stop process | — | `{"status":"stopped",...}`; 404; 500 `STOP_FAILED` |
| GET | `/api/tools/{tool_name}/status` | Live health check + status | — | `{tool_name,status,pid,port,healthy,description,plane}`; 404 |

- Adapter routes are deliberately declared **before** `/{tool_name}/...` so
  `/tools/adapters` is never shadowed by the `{tool_name}` catch-all.
- `_sync_tool_instance` upserts the `ToolInstance` DB row on every install/start/
  stop/status so state survives restarts; live process status wins over DB on read.
- Adapter filters silently return `[]` on unknown values (defensive vs UI drift) —
  they do not 400.

### 1.6 Agents (`core/api/agents.py`, prefix `/agents`)

| Method | Full path | Purpose | Request | Response |
|--------|-----------|---------|---------|----------|
| GET | `/api/agents/install` | Generate ready-to-run installer script | query: `os=linux\|windows`, `id`, `server?`, `interval` | `PlainTextResponse` bash (`.sh`) or PowerShell (`.ps1`); 400 `BAD_OS` |
| GET | `/api/agents` | List registered agents (newest `last_seen` first) | — | `{"agents":[<agent.to_dict()>],"total":N}` |
| POST | `/api/agents/register` | Register/re-register a beacon (idempotent) | `RegisterRequest`: `{agent_id, hostname, os, capabilities[]}` | `{"status":"registered","agent_id","message"}` |
| DELETE | `/api/agents/{agent_id}` | Prune a beacon row (does not touch run history) | — | `{"status":"deleted","agent_id"}`; 404 `AGENT_NOT_FOUND` |
| GET | `/api/agents/{agent_id}/tasks` | Beacon polls for next task; bumps `last_seen` | — | `{"task":<task.to_dict()>}` or `{"task":null}`; 404 if agent unknown |

- `register` is idempotent: existing `agent_id` → metadata update + `status=online`
  + `last_seen` bump; else insert.
- `/{agent_id}/tasks` updates `last_seen=utcnow()` on *every poll*, then calls
  `orchestrator.dequeue(agent_id)`. The poll is the primary `last_seen` writer, but
  agent liveness is no longer pinned to `online`: status is **derived from
  `last_seen` age at read time** — `online` (< 30s) · `stale` (< 5m) · `offline`
  (≥ 5m) — and a **background heartbeat sweep** (`heartbeat_sweep_loop`, started from
  `main.py` lifespan, 30s interval) recomputes each agent's derived status and emits
  `agent.status` SSE frames on the global bus when one flips. `GET /api/agents` adds a
  `last_seen_age_seconds` field per row (GAP-AGENT-001, **closed 2026-06-08**).
- The installer builds the stdlib-only Go beacon **on the target** (`go install
  github.com/hankthebldr/cortexsim/agent@latest` or local `CORTEXSIM_SRC` build).
  No binary hosting. Requires Go 1.21+ on the target.

### 1.7 MITRE (`core/api/mitre.py`, prefix `/mitre`)

| Method | Full path | Purpose | Response |
|--------|-----------|---------|----------|
| GET | `/api/mitre/coverage` | Technique coverage heatmap data | `{techniques[], by_tactic[], summary:{total_techniques,detected,run_not_detected,not_run}}` |

- Per-technique `status` ∈ `detected` \| `run_not_detected` \| `not_run` \|
  `no_scenario`. Aggregates scenario-level + per-step techniques + observed Results.
- **Only counts scenarios with `status == "active"`** (`mitre.py:38`). Draft/
  deprecated scenarios are invisible to the heatmap (GAP-API-009).

### 1.8 Infra / IaC (`core/api/infra.py`, prefix `/infra`)

| Method | Full path | Purpose | Request | Response |
|--------|-----------|---------|---------|----------|
| GET | `/api/infra/modules` | List Terraform modules | query: `provider=aws` (default) | `{"modules":[<metadata>],"total":N}` |
| POST | `/api/infra/generate` | Render + bundle Terraform tar.gz | `InfraGenerateRequest` (Pydantic) | `InfraGenerateResponse` (`bundle_id`, `download_url`); 422 `GENERATION_FAILED` |
| GET | `/api/infra/bundles` | List previously generated bundles | — | `{"bundles":[<summary>],"total":N}` |
| GET | `/api/infra/bundles/{bundle_id}/download` | Download tar.gz | — | `FileResponse` gzip; 404 `BUNDLE_NOT_FOUND` |

- These routes are **sync** (`def`, not `async def`) — they do blocking filesystem
  work (`shutil.copytree`) off the event loop is *not* offloaded (GAP-API-010).

### 1.9 EAL Traffic Simulator (`core/api/eal.py`, prefix `/eal`)

| Method | Full path | Purpose | Request | Response |
|--------|-----------|---------|---------|----------|
| GET | `/api/eal/plugins` | List registered simulator plugins | — | `{"plugins":[...],"total":N}` |
| GET | `/api/eal/plugins/{name}` | Plugin metadata + JSON schema | — | `plugin.metadata()`; 404 `PLUGIN_NOT_FOUND` |
| POST | `/api/eal/campaigns` | Persist a campaign def (validates each step's plugin+params) | `Campaign` (Pydantic) | `<EalCampaign.to_dict()>` (201); 422 `PLUGIN_NOT_FOUND`/`PARAMS_INVALID`; 409 `DUPLICATE_CAMPAIGN` |
| GET | `/api/eal/campaigns` | List campaigns (newest first) | — | `{"campaigns":[...],"total":N}` |
| GET | `/api/eal/campaigns/{campaign_id}` | Single campaign | — | `<EalCampaign.to_dict()>`; 404 `CAMPAIGN_NOT_FOUND` |
| POST | `/api/eal/campaigns/{campaign_id}/launch` | Launch campaign in background (dry-run default) | `LaunchRequest`: `{dry_run?, operator?}` | `LaunchResponse`: `{run_id, campaign_id, status, dry_run}`; 404; 422 `SPEC_INVALID`/`SAFETY_VIOLATION` |
| GET | `/api/eal/runs` | List EAL runs | query: `campaign_id?` | `{"runs":[...],"total":N}` |
| GET | `/api/eal/runs/{run_id}` | Single EAL run + step results | — | `<EalCampaignRun.to_dict()>`; 404 `RUN_NOT_FOUND` |

- **This is a parallel run/lifecycle subsystem** distinct from the `Run`/agent
  system. EAL campaigns run via FastAPI `BackgroundTasks` (in-process async),
  *not* via the beacon. Polling `GET /api/eal/runs/{run_id}` tracks status
  (`pending` → terminal). Cross-link: it shares the `consent`/`simulation_authorized`
  safety-gate philosophy with the orchestrator but enforces it via `SafetyPolicy`
  pre-flight, returning 422 `SAFETY_VIOLATION`.
- **GAP-API-011:** EAL runs also have no abort endpoint — once a background task
  is launched it runs to completion or crash. EAL run statuses are a *different
  enum* (`pending`/`success`/`failed`/etc. from the executor) than core Runs.

### 1.10 Credentials (`core/api/credentials.py`, prefix `/credentials`)

| Method | Full path | Purpose | Request | Response |
|--------|-----------|---------|---------|----------|
| GET | `/api/credentials/secrets` | List secret metadata (never plaintext/ciphertext) | — | `{"secrets":[<secret.to_dict()>]}` |
| PUT | `/api/credentials/secrets` | Upsert encrypted secret | `SecretPut`: `{name, plaintext, type_hint?, description?, rotation_days?}` | `<secret.to_dict()>` |
| GET | `/api/credentials/secrets/{name}` | Secret metadata only | — | `<secret.to_dict()>`; 404 |
| DELETE | `/api/credentials/secrets/{name}` | Delete secret | — | 204; 404 |
| GET | `/api/credentials/integrations` | List integrations | query: `kind?` | `{"integrations":[...]}` |
| PUT | `/api/credentials/integrations` | Upsert integration (+ its secret) | `IntegrationPut`: `{name, kind, plaintext_secret, config, secret_type_hint?, description?}` | `<integration.to_dict()>` |
| GET | `/api/credentials/integrations/{name}` | Single integration | — | `<integration.to_dict()>`; 404 |
| DELETE | `/api/credentials/integrations/{name}` | Delete integration | — | 204; 404 |
| POST | `/api/credentials/integrations/{name}/verify` | Record a liveness-probe outcome | `VerifyMark`: `{ok, error?}` | `<integration.to_dict()>`; 404 |
| GET | `/api/credentials/_internal/probe-crypto-error` | Test hook (excluded from schema) | — | always raises `CryptoError` → 500 |

- Plaintext is **write-only over HTTP**: accepted on PUT, never returned. Decryption
  is internal-Python-only via `CredentialStore`. `to_dict()` never carries ciphertext.
- 404s here use a **bare-string** `detail` (e.g. `"secret 'x' not found"`), unlike
  the structured `{error,code,detail}` envelope used everywhere else (GAP-API-012).

### 1.11 TTPs (`core/api/ttps.py`, prefix `/ttps`)

Read surface (always available):

| Method | Full path | Purpose | Request | Response |
|--------|-----------|---------|---------|----------|
| GET | `/api/ttps` | List TTP cards (slim) | query: `status?`, `tactic?`, `platform?` | `{"ttps":[<summary>],"total":N}` |
| GET | `/api/ttps/_schema` | TTP authoring JSON Schema | — | schema doc; 500 `SCHEMA_NOT_FOUND`/`SCHEMA_DECODE` |
| GET | `/api/ttps/{ttp_id}` | Full card + `referenced_by_adapters[]` | — | raw card + reverse adapter xrefs; 404 `TTP_NOT_FOUND` |
| GET | `/api/ttps/{ttp_id}/runs` | Run history for this TTP (rolled up by run) | query: `limit=20 (1..200)` | `{ttp_id, runs[], total}`; 404 |

Authoring surface (gated on `CORTEXSIM_AUTHORING_ENABLED=true`, else 403 `AUTHORING_DISABLED`):

| Method | Full path | Purpose | Notes |
|--------|-----------|---------|-------|
| POST | `/api/ttps` | Create draft under `_drafts/` | 201; forces `status=draft`; 409 `TTP_ID_CONFLICT`; 422 `TTP_SCHEMA_INVALID` |
| PUT | `/api/ttps/{ttp_id}` | Update in place (drafts stay drafts) | 400 `ID_MISMATCH`; 404; 422 |
| POST | `/api/ttps/{ttp_id}/promote` | Move draft → active corpus | idempotent; re-validates; `moved` flag |
| POST | `/api/ttps/_reload` | Hot-reload catalog from disk | returns `{loaded, entries}` |

- `_schema` / `_reload` are declared before `/{ttp_id}` so the catch-all can't
  shadow them. `ttp_id` must match `TTP-YYYY-NNNN` (locks out path traversal).
- `/{ttp_id}/runs` is the **temporal cross-link**: it joins `Result.ttp_ref`
  (indexed) → `Run`, rolling up expected/observed counts and min MTTD per run.
  This is the only place the static TTP corpus meets live run history.

### 1.12 Events / SSE (`core/api/events.py`, **no router prefix** — routes are absolute)

| Method | Full path | Purpose | Response |
|--------|-----------|---------|----------|
| GET | `/api/runs/{run_id}/events` | SSE stream scoped to a single run (+ global events) | `text/event-stream` |
| GET | `/api/events` | SSE stream of every event across all runs + agent status | `text/event-stream` |

- Both return `text/event-stream` and emit one SSE frame per event. The browser
  consumes them with `EventSource.onmessage` + `JSON.parse`; each event carries a
  `{type, run_id, ts, data}` envelope (`run.status`, `run.output`, `agent.status`, …).
- Backed by an in-process `event_bus` (`core/events.py`). The orchestrator and the
  `/abort`, `/output`, `/complete`, and heartbeat-sweep paths publish to it; the SSE
  endpoint subscribes and relays. An initial comment frame fires so
  `EventSource.onopen` triggers promptly (GAP-API-002, **closed 2026-06-08**).

### 1.13 POV scoping (`core/api/pov.py`, prefix `/pov`)

Answers "what can this tenant license, and what would they have to buy to run
the rest of the POV". Backed by the v2.2 index registry, not by the scenario
corpus alone — every capability it names resolves to a real `PAN-*` part number.

| Method | Full path | Purpose | Request | Response |
|--------|-----------|---------|---------|----------|
| GET | `/api/pov/profiles` | Canned tenant entitlement tiers | — | `ng-siem-bare` · `enterprise` · `premium` |
| GET | `/api/pov/capabilities` | Every licensable capability + its SKU | — | capability → `PAN-*` part number |
| POST | `/api/pov/scope` | Scope the corpus to an entitlement set | `{profile}` **or** `{base_platform[], addons[]}` | in-scope scenarios + the generated upsell list |

- Entitlements always resolve through `registry.entitlements_for*`, which walks
  the use case and drops capacity SKUs. Reading `TestCase.required_addon` raw
  would disagree with this endpoint on the same test case.

### 1.14 UC / TC index (`core/api/uctc.py`, prefix `/uctc`)

The FY27 v2.2 master index as a **read-only** in-product surface, joined to the
engine's own evidence (`Scenario.tc_refs` → `Run.tc_verdict`). Backs the
console's **UC / TC Index** destination (`#/uctc`). Authoring stays in
`docs/uc_tc_mapping/` + `scripts/uctc_crosswalk_v2.2.py` — there is no write path.

| Method | Full path | Purpose | Response |
|--------|-----------|---------|----------|
| GET | `/api/uctc/summary` | Header counts + evidence rollup in one call | totals, per class/tier/priority/sheet tallies |
| GET | `/api/uctc/use-cases` | The 49 UCs with per-UC coverage arithmetic | query: `sheet?`, `subdomain?`, `evidenced=all\|yes\|no\|partial` |
| GET | `/api/uctc/use-cases/{uc_id}` | One UC + its UCS groups + child TCs | 404 `UC_NOT_FOUND` |
| GET | `/api/uctc/test-cases` | The main table, all 266 rows, unpaginated | filters: `uc_id`, `ucs_id`, `validation_class`, `tier`, `priority`, `sheet`, `pov_scenario_id`, `evidenced`, `scoreable`, `plane` |
| GET | `/api/uctc/test-cases/{tc_id}` | Full detail: measurement contract, entitlement, payload, evidencing scenarios, run verdicts | 404 `TC_NOT_FOUND` |
| GET | `/api/uctc/coverage` | Every rollup at once (by UC / plane / class / tier / priority / sheet) | worst-covered UC first |
| GET | `/api/uctc/gaps` | Unevidenced TCs — the build backlog | defaults `validation_class=DET,HNT`; P1 first |
| GET | `/api/uctc/payloads` | POV-SC payloads + engine usage + `needs_split` | query: `in_use?` |
| GET | `/api/uctc/by-scenario/{scenario_id}` | Forward view for a deep link | resolved/unresolved refs + tier delta; 404 `SCENARIO_NOT_FOUND` |

- **Every** response carries `{index_loaded, index_version}`. The registry is
  fail-soft (the prod image has historically shipped without `docs/`), so a
  stripped deploy returns **200 with `index_loaded: false`** and empty
  collections. Callers must render that as degraded, never as "0 test cases".
- Evidence is keyed on `Scenario.tc_refs` **only**. Joining through
  `pov_scenario_id` would over-claim: `POV-SC-001` binds 21 test cases.
- Active scenarios only unless `include_inactive=true`.
- `is_scoreable` is surfaced deliberately — 57 of the 107 detection-backable
  rows carry no measurable threshold, so `pass` is impossible for them by
  construction.

> **Still undocumented in this table:** `/api/connectors`, `/api/xsiam`, and the
> `/api/runs/*` storyline + causality routes. Those predate this pass and are
> covered by their own design docs.

---

## 2. Lifecycle state machines

### 2.1 Run (`models.Run.status`)

ORM default `"pending"`. Enumerates: `pending | running | complete | failed | aborted`
(the `aborted` state shipped 2026-06-08, GAP-API-001).

```
                 POST /api/run (orchestrator.launch)
                         │
                         ▼
                    ┌─────────┐
                    │ pending │  (row created, Results seeded w/ executed_at)
                    └─────────┘
                  pull │        │ push
       _handle_pull    │        │  _handle_push  (status stays "pending" forever!)
   (enqueue task,      │        ▼
    set running)       │   ┌─────────┐
                       ▼   │ pending │ ──► (no further server-side transition;
                  ┌─────────┐         │     DC runs bundle offline, optionally
                  │ running │         └──   marks Results observed by hand)
                  └─────────┘
                       │
       agent POST /api/runs/{id}/complete
                       │
        exit_code==0   │   exit_code!=0
             ▼         ▼
       ┌──────────┐ ┌────────┐
       │ complete │ │ failed │   (terminal; completed_at stamped)
       └──────────┘ └────────┘
```

- **Terminal states:** `complete`, `failed`, `aborted`.
- **`aborted` now exists** (GAP-API-001, closed 2026-06-08): `POST /api/runs/{id}/abort`
  transitions a non-terminal run to `aborted`, stamps `completed_at`, dequeues the
  task, and signals the agent via `/control`. Idempotent on already-terminal runs.
- **Status string mismatch RESOLVED:** backend produces `complete`; the UI now
  checks for `complete` too (GAP-API-003 closed 2026-06-08). The "last completed run"
  detection matches real backend-completed runs.
- **Push runs are orphaned at `pending`:** `_handle_push` never advances status.
  A push-mode run sits at `pending` permanently because the offline bundle has no
  callback to `/complete` — GAP-API-004.

### 2.2 Result (`models.Result`)

No dedicated `status` column — state is encoded in two booleans/timestamps:
`observed` (bool) + `executed_at` + `observed_at`.

```
   orchestrator._seed_results (at launch)
                │
                ▼
   ┌──────────────────────────────┐
   │ observed=False               │   executed_at = run start
   │ observed_at=None             │   (mttd_seconds == None)
   │ (optionally TTP-enriched:    │
   │  detection_logic/kind/sev)   │
   └──────────────────────────────┘
                │
   PUT /api/results/{id}/validate
        observed=true │      │ observed=false (re-toggle)
                      ▼      ▼
   ┌──────────────────────────┐   ┌──────────────────────────┐
   │ observed=True            │   │ observed=False           │
   │ observed_at=utcnow()     │◄─►│ observed_at=None         │
   │ mttd_seconds computed    │   │ mttd_seconds == None     │
   └──────────────────────────┘   └──────────────────────────┘
```

- `mttd_seconds` is a **computed property** = `observed_at - executed_at`, `None`
  unless both are set.
- Validation is freely reversible (toggle observed on/off any number of times).
- **TTP enrichment** happens once at seed time: if a detection carries `ttp_ref`/
  `detection_id`, the orchestrator copies the card's `kind`/`logic`/`severity`/
  `mitre_technique` onto the Result so the report can render deployable XQL inline.

### 2.3 Agent (`models.Agent.status`)

Default `"online"`. Enumerates: `online | stale | offline` — **derived from
`last_seen` age at read time** (online < 30s · stale < 5m · offline ≥ 5m), not a
hand-set field (GAP-AGENT-001 closed 2026-06-08).

```
   POST /api/agents/register          GET /api/agents/{id}/tasks (any poll)
            │                                   bumps last_seen=utcnow()
            ▼                                          │
       ┌────────┐ ◄───────────────────────────────────┘
       │ online │  last_seen age < 30s
       └────────┘
            │  age ≥ 30s          age ≥ 5m
            ▼                        ▼
       ┌────────┐               ┌─────────┐
       │ stale  │ ────────────► │ offline │   (derived at read time;
       └────────┘               └─────────┘    heartbeat sweep flips + emits SSE)
            │
   DELETE /api/agents/{id}
            ▼
       (row removed entirely — no tombstone)
```

- **`stale`/`offline` are now set** (GAP-AGENT-001, closed 2026-06-08). Status is
  **derived from `last_seen` age at read time** (`_derive_status`): online < 30s ·
  stale < 5m · offline ≥ 5m. A dead beacon no longer stays `online` forever.
- A **background sweep** (`heartbeat_sweep_loop`, 30s, started from `main.py`
  lifespan) recomputes derived status and emits an `agent.status` SSE frame on the
  global bus when one transitions — so the UI sees an agent go offline without a
  re-list. `last_seen` is still primarily bumped by the task poll (the poll interval,
  default 10s, doubles as the implicit heartbeat); there is no separate heartbeat
  POST endpoint — staleness is computed from the existing poll-driven `last_seen`.

### 2.4 ToolInstance (`models.ToolInstance.status`)

Default `"not_installed"`. Comment enumerates: `not_installed | installed | running | stopped`.

```
   ┌───────────────┐ POST /tools/{n}/install  ┌───────────┐
   │ not_installed │ ───────────────────────► │ installed │ (installed_at stamped)
   └───────────────┘                          └───────────┘
                                                 │      ▲
                              POST /tools/{n}/start    │ POST /tools/{n}/stop
                                                 ▼      │
                                            ┌─────────┐ │
                                            │ running │─┘
                                            └─────────┘
                                                 │
                              GET /tools/{n}/status → re-syncs to live process status
                                                 ▼
                                            ┌─────────┐
                                            │ stopped │  (process gone / explicitly stopped)
                                            └─────────┘
```

- Live process status (from `instantiator`) wins over DB; DB only fills gaps after
  a SimCore restart (`tools.py` `list_tools`, lines 110-112).
- `last_health_check` stamped on `GET /status` (and any `_sync_tool_instance(...,
  health_check_now=True)`).

### 2.5 EalCampaignRun (`models.EalCampaignRun.status`) — parallel subsystem

Default `"pending"`. Set by `_get_executor().execute(...)` result `state.status`
(executor-defined enum, e.g. `success`/`failed`). `dry_run` defaults `True`.

```
   POST /api/eal/campaigns/{id}/launch
            │ (SafetyPolicy pre-flight; 422 on violation)
            ▼
       ┌─────────┐  BackgroundTask runs executor.execute()
       │ pending │ ───────────────────────────────────────┐
       └─────────┘                                         │
            │ crash (exception)        normal completion   │
            ▼                                  ▼            │
       ┌────────┐                    ┌──────────────────┐  │
       │ failed │                    │ <executor status>│◄─┘
       └────────┘                    │ + step_results[] │
                                     └──────────────────┘
```

---

## 3. Pull vs Push execution mode walkthrough

Entry point for both: `POST /api/run` → `orchestrator.launch(...)`.

Shared prologue (both modes):
1. Validate `mode ∈ {pull, push}` (400 `INVALID_MODE` otherwise).
2. Load `Scenario` by id (422 `LAUNCH_FAILED` if not found).
3. **Adapter consent gate** (`_check_adapter_consent`): for every
   `external_tools[].adapter_ref`, if the resolved adapter is `c2-framework`
   and `consent.c2_authorized` is falsy → refuse (no Run created). Same for
   `dual-use-lab-only` + `simulation_authorized`. `destructive` adapters with no
   cleanup commands are also refused (defence in depth).
4. Create `Run` row (`status="pending"`, `started_at=now`), commit.
5. `_seed_results`: one `Result` per `step.expected_detections[]`, `executed_at=now`,
   TTP-enriched where refs resolve.

### 3.1 Pull mode (`_handle_pull`)

```
POST /api/run {mode:"pull", target_agent_id:"jumpbox-01", identity:"..."}
        │
        ├─ require target_agent_id (else 422 "target_agent_id is required for pull mode")
        │
        ├─ build Task{task_id, run_id, scenario_id,
        │            steps=_resolve_adapter_placeholders(scenario.steps),
        │            identity_context}
        │
        ├─ orchestrator._enqueue(target_agent_id, task)   # in-memory dict[agent_id]->[Task]
        │
        └─ Run.status = "running"; commit
                │
                ▼
   Beacon GET /api/agents/jumpbox-01/tasks  (every `interval` seconds)
        → orchestrator.dequeue(agent_id) pops Task → {"task": <task.to_dict()>}
                │
                ▼
   Beacon executes via identity harness (see §5), then:
        POST /api/runs/{run_id}/output    (full combined STDOUT/STDERR)
        POST /api/runs/{run_id}/complete  {exit_code, summary}
                │
                ▼
   Run.status = complete|failed; completed_at stamped
   DC later: PUT /api/results/{id}/validate to confirm detections (MTTD)
```

- **Queue is in-memory and ephemeral** (`orchestrator._queue`). Restarting SimCore
  loses all undelivered tasks; the durable `Run` row is left at `running` with no
  way to recover the task — GAP-API-005 (high).
- `_resolve_adapter_placeholders` substitutes `{adapter:TOOL-XYZ}` in step commands
  with the adapter's rendered `run_template`; unresolved placeholders are left raw
  so the agent surfaces the failure instead of silently no-op'ing.

> ⚠️ **GAP-AGENT-002 (critical) — Task wire-shape mismatch.** The orchestrator's
> `Task.to_dict()` emits `{task_id, run_id, scenario_id, steps:[...],
> identity_context, created_at}` (a list of steps + a string identity context).
> The Go beacon's `Task` struct (`agent/beacon/client.go` lines 24-38) expects
> `{run_id, scenario_id, command:string, identity:{mode,username}}` — a single
> flat command + a structured identity object. The fields **do not line up**:
> the agent has no `steps` handling, gets an empty `Command`, and `Identity`
> (mode/username) is never populated by the server. A real pull-mode run today
> would dispatch an empty command. This is the single biggest blocker for the
> pull path working end-to-end.

### 3.2 Push mode (`_handle_push`)

```
POST /api/run {mode:"push"}
        │
        └─ download_url = "/api/scenarios/{scenario_id}/download"
                │  (Run row exists at status="pending", never advances)
                ▼
   Response: {run_id, mode:"push", message:"Push bundle ready for download", download_url}
                │
                ▼
   DC: GET /api/scenarios/{id}/download?format=bash|k8s
        → push_generator.generate_bash / generate_k8s (see §4)
                │
                ▼
   DC runs the self-contained bundle OFFLINE on a clean Ubuntu 22.04 host
        (no SimCore dependency at runtime — by design)
                │
                ▼
   NO callback to /output or /complete. Run stays "pending".
   DC manually marks Results observed via the UI/validate endpoint.
```

- Push bundles are **stateless artifacts**; the offline host never phones home.
  Consequently push-mode runs cannot show progress and never reach a terminal
  status server-side (GAP-API-004).
- The bundle re-renders on each `download` call from the live scenario dict — it is
  not snapshotted at launch time.

---

## 4. Push bundle generation (`core/engine/push_generator.py`)

Two pure functions over a `scenario.to_dict()`:

### `generate_bash(scenario) -> str`
Emits a single self-contained `set -euo pipefail` script:
1. **Header** — scenario metadata + expected detections as comments.
2. **Logging** — tees to `/tmp/cortexsim-{id}-{timestamp}.log`.
3. **Identity harness** — `run_as(identity, cmd, step_id)`: `root|container-runtime|
   direct` run directly; known service accounts (`www-data postgres mysql node
   python3 nobody svc-backup`) prefer `runuser -l` → fall back `sudo -u` → `su -s
   /bin/bash`; unknown identity warns + runs direct.
4. **Dependency checks** — `check_dep` per non-adapter `type: binary` tool (adapter-
   backed tools are skipped — installed in step 5).
5. **Inline tool downloads** — `curl` for `install_inline` tools with a `source`.
6. **Tool-adapter installs** — resolves each `adapter_ref` in the catalog:
   `c2-framework` adapters are **never auto-staged** (warn only); tier-4
   runtime-fetched tools emit their `runtime_install_command`; tier 1/2/3 emit a
   "must be pre-provisioned" note.
7. **Cleanup** — `trap cleanup EXIT` running the scenario's `cleanup.commands`.
8. **TTP steps** — each `run_as '<identity>' '<escaped cmd>' '<step_id>'`.

### `generate_k8s(scenario) -> str`
Emits a `Namespace` (`cortexsim-{id}`) + one `batch/v1 Job` per step
(`ttlSecondsAfterFinished: 300`, `restartPolicy: Never`, `ubuntu:22.04`, inline
identity-harness `if/elif` block, CPU/mem limits). Begins on `kubectl apply`.

- Both escape single quotes via `'\''`. Self-contained by design.
- **GAP-PUSH-001:** the bash identity-harness service-account allowlist
  (`www-data postgres mysql node python3 nobody svc-backup`) and the Go harness
  modes (`direct/runuser/sudo_u/su` keyed off a *mode* string) are **two different
  models**. Push bundles key off the *username* and guess the mechanism; pull tasks
  are supposed to carry an explicit `{mode, username}`. A scenario authored for one
  path will not behave identically on the other.

---

## 5. Agent (beacon) lifecycle — register → poll → execute → report → delete

Source: `agent/main.go`, `agent/beacon/client.go`, `agent/identity/harness.go`,
`agent/executor/shell.go`. Go module `github.com/hankthebldr/cortexsim/agent`,
Go 1.21+, **stdlib only**.

### 5.1 Startup & registration (`main.go`)
- Flags: `--server` (default `http://localhost:8888`), `--id` (**required**),
  `--interval` (default 10s, min 1).
- Builds `beacon.New(server, id, interval)` (HTTP client timeout 30s).
- `client.Register(hostname, runtime.GOOS, ["shell","identity-harness"])` →
  `POST /api/agents/register`. **Registration failure is non-fatal** — logged as a
  warning; the agent proceeds to poll and SimCore re-registers on demand... except
  it doesn't: `/tasks` 404s an unknown agent rather than auto-registering, so a
  failed initial register means every poll 404s until `register` succeeds again
  (and the agent never re-calls Register in the loop) — GAP-AGENT-003 (medium).
- Installs SIGINT/SIGTERM handler → cancels context → clean shutdown.

### 5.2 Poll loop (`beacon.Run`)
```
for every ticker tick (interval):
    task, err := PollTasks()          # GET /api/agents/{id}/tasks
    if err: log + continue            # transient errors don't kill the loop
    if task == nil: log "no task"     # 404 OR {"task":null} both → idle
    else: executeTask(ctx, task)
ctx cancelled → return (clean exit)
```
- `PollTasks` treats HTTP 404 and `{"task":null}` identically as "idle". Note: a
  404 also means *agent unknown*, so an unregistered agent looks indistinguishable
  from "no work" — silent failure mode (GAP-AGENT-003).

### 5.3 Execute (`beacon.executeTask` → `identity.Execute` → `executor.RunCommand`)
- Runs `identity.Execute(ExecutionIdentity{Mode, Username, Command})` in a goroutine.
- Identity harness wraps the command per `Mode`:
  - `direct`/`""` → command as-is
  - `runuser` → `runuser -l <user> -c '<cmd>'`
  - `sudo_u` → `sudo -u <user> <split args...>` (no `-c`, cleaner causality)
  - `su` → `su -s /bin/bash <user> -c '<cmd>'`
  - unknown mode → error
- `executor.RunCommand` runs `sh -c <wrapped>`, captures stdout/stderr separately.
  Non-zero exit is **not** a Go error (returned as exitCode); only exec-launch
  failures (binary missing) return `err` with exitCode `-1`.
- **Output streaming is real, per-step** (GAP-AGENT-004, closed 2026-06-08). The
  beacon now iterates `task.Steps[]` sequentially and POSTs each step's output to
  `/output` as it completes (one `/output` POST per step), so the SSE stream and the
  console see incremental progress instead of a single final blob. Identity is
  resolved **agent-side** per step via `identity.ResolveIdentity(username)` (the
  server emits a username string in `step.identity` / `task.identity_context`; the
  agent maps it to a `{mode, user}`), which is the fix for the GAP-AGENT-002 wire-shape
  mismatch — the dispatched command is no longer empty.

### 5.4 Report
- `POST /api/runs/{run_id}/output` with combined `=== STDOUT === / === STDERR ===`.
- `POST /api/runs/{run_id}/complete` with `{exit_code, summary}` where summary is
  `SUCCESS|FAILED (exit N) | scenario=... mode=... duration=...`.
- On ctx-cancel mid-execution: `Complete(runID, -1, "agent shutdown — task
  interrupted")` then return.
- **Server-driven abort** (GAP-API-001, closed 2026-06-08): the beacon polls
  `GET /api/runs/{run_id}/control` (a) once before each step and (b) every
  `controlPollInterval` (2s) while a step runs. On `abort=true` it terminates the
  in-flight step's process group and reports the conventional `abortExitCode` (130).
  A vanished/terminal run also returns `abort=true` so the agent halts rather than
  spinning.

### 5.5 Delete
- `DELETE /api/agents/{agent_id}` removes the registry row (Targets UI prune).
  Does not affect run history. The running beacon process is unaffected — it will
  keep polling and 404, re-creating nothing (it never re-registers in-loop).

---

## 6. UI ↔ backend contract gaps (endpoints one side has and the other lacks)

These were the original contract gaps; **all but the low-sev health one are now
closed** (2026-06-08):

| UI call | Backend reality (2026-06-08) | Status |
|---------|------------------------------|--------|
| `POST /api/runs/{id}/abort` | **Route exists** — transitions to `aborted`, idempotent, signals the agent via `/control`. | RESOLVED (GAP-API-001) |
| `GET /api/runs/{id}/events` (SSE) | **Route exists** (`core/api/events.py`) — `text/event-stream`, plus the global `GET /api/events`. | RESOLVED (GAP-API-002) |
| UI status check vs backend tokens | Backend emits `complete` / `failed` / `aborted`; the UI matches `complete`/`aborted`. | RESOLVED (GAP-API-003) |
| `/api/health` sensor status | Health still returns only `{status, version}`. | OPEN (low, GAP-API-007) |

All *backend* routes have at least one UI or agent consumer (no obvious orphan
server routes), except the test-only `/_internal/probe-crypto-error` and the
authoring TTP routes (gated, UI has TTP authoring affordances behind the env flag).

---

## 7. Cross-domain links

- **Tool adapters** (`docs/tool-adapters.md`): consent gate (`c2_authorized`,
  `simulation_authorized`) enforced in `orchestrator._check_adapter_consent`;
  adapter catalog consumed by `/api/tools/adapters*`, `/api/scenarios/{id}/infra-hints`,
  report `tools_used` table, and push-bundle installs.
- **TTP corpus** (`detection_scanner/ttps/`): `Result.ttp_ref` is the join key;
  `/api/ttps/{id}/runs` closes the static-content ↔ live-run loop; orchestrator
  enriches Results from `engine.ttp_catalog`.
- **IaC generator** (`docs/superpowers/specs/2026-04-20-iac-topology-generator-design.md`):
  `/api/scenarios/{id}/infra-hints` → `/api/infra/generate` handoff.
- **EAL simulator**: parallel campaign/run lifecycle (`/api/eal/*`) reusing the
  safety-authorization philosophy but a separate executor + status enum.
- **Credentials** (Phase 9): `IntegrationCredential` referenced by future XSIAM/AWS
  integrations; verify-outcome recorded via `/verify`.

---

## 8. Summary of gaps — lifecycle phase status (2026-06-08)

Most of the lifecycle backlog **shipped** in the multi-wave revamp:

1. ✅ `POST /api/runs/{id}/abort` + the `aborted` Run state + `/control` agent
   stop-signal poll (GAP-API-001).
2. ✅ **Task wire shape** reconciled — the beacon iterates `steps[]` and resolves
   identity agent-side; pull mode no longer dispatches empty commands (GAP-AGENT-002).
3. ✅ SSE chosen and shipped (`/api/runs/{id}/events` + `/api/events`); the beacon
   streams per-step output (GAP-API-002 + GAP-AGENT-004).
4. ✅ Agent `online`/`stale`/`offline` derived from `last_seen` + background heartbeat
   sweep emitting `agent.status` SSE (GAP-AGENT-001).
5. ✅ `complete` vs `completed` status-token mismatch reconciled to `complete`
   (GAP-API-003).
6. ⏳ **Still open:** persist/rebuild the in-memory task queue across restarts
   (GAP-API-005). Abort/control now make an interrupted run *recoverable*, but the
   queue itself is not yet durable.
7. ⏳ **Still open:** give push-mode runs a terminal path or a documented
   "manual-complete" affordance (GAP-API-004).

> Pull mode is now **end-to-end working**: launch → enqueue → beacon polls + executes
> per-step with agent-side identity resolution → per-step `/output` → `/complete`,
> with operator abort via `/abort`+`/control` and live updates over SSE.

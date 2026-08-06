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

- **The Cortex connection is OPT-IN, READ-ONLY, and no longer hypothetical**
  *(corrected 2026-08-06 — this bullet previously read "No Cortex API
  connection … it never reads alerts out", which has been false since the
  measurement loop landed and directly contradicted `CLAUDE.md`)*. SimCore's
  primary job is still to generate signal **into** the customer environment, and
  it **never writes** to Cortex (`CORTEXSIM_XSIAM_ALLOW_WRITE` /
  `_ALLOW_DESTRUCTIVE` default off). But when a credential is configured it
  **does** read out: `core/connectors/` pulls observed alerts to auto-validate
  seeded `Result` rows (evidence-backed MTTD), and `core/integrations/xsiam/`
  runs read-only XQL for Tier-2 verification and assertion probes.
  `PUT /api/results/{id}/validate` remains the manual, offline path — it is the
  fallback, not the only mechanism. See §1.15.
- **Transport is plain JSON over HTTP, plus Server-Sent Events for live updates.**
  SSE (`text/event-stream`) now backs `GET /api/runs/{id}/events` (scoped) and
  `GET /api/events` (global); the rest of the surface remains REST/JSON. No
  WebSocket, no gRPC (GAP-API-002 closed 2026-06-08).
- **133 HTTP routes total** (counted 2026-08-06 against the built image, see
  below). Routers under `/api`, plus the app-level `GET /api/health` and a static
  file mount at `/`. Per-prefix: `runs` 17 · `agents` 14 · `eal` 13 ·
  `credentials` 10 · `uctc` 9 · `tools` 8 · `ttps` 8 · `xsiam` 8 · `shelf` 7 ·
  `k8s` 6 · `assertions` 6 · `scenarios` 5 · `infra` 4 · `results` 4 · `pov` 3 ·
  `connectors` 2 · `mitre` 2 · `run` 1 · `events` 1 · `health` 1, plus 5
  FastAPI-owned docs/openapi routes.

> **Count it, do not quote it.** The "65 HTTP routes / 11 routers" line that stood
> here was two content passes stale. The command is the ground truth:
>
> ```bash
> docker run --rm -v "$PWD:/app" -w /app -e CORTEXSIM_BASE_DIR=/app cortexsim:dev \
>   python -c "import sys;sys.path.insert(0,'core');from main import app;\
> print(len([r for r in app.routes if getattr(r,'methods',None)]))"
> ```

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
| GET | `/api/scenarios` | List scenarios (**summary projection**); optional filters | query: `plane`, `uc_ref`, `ttp_ref`, `entitlement` | `{"scenarios":[<summary>],"total":N,"projection":"summary"}` |
| GET | `/api/scenarios/{scenario_id}` | Single scenario detail (**full document**) | — | `<scenario.to_dict()>` or 404 `SCENARIO_NOT_FOUND` |
| GET | `/api/scenarios/{scenario_id}/infra-hints` | Resolve `external_tools[].adapter_ref` → IaC module suggestions | — | `{scenario_id, plane, adapter_refs[], resolved_adapters[], unresolved_refs[], suggested_modules[]}` |
| GET | `/api/scenarios/{scenario_id}/download` | Self-contained execution bundle | query: `format=bash\|k8s\|powershell\|auto` (default `auto`) | `PlainTextResponse` shell/`.ps1`/yaml w/ `Content-Disposition`; 400 `INVALID_FORMAT`; 409 `BUNDLE_TARGET_UNSATISFIABLE` |

- `plane` is upper-cased server-side; `uc_ref` exact-match; `ttp_ref` filtered in
  Python against the ORM rows (so the list projection does not affect it).
- **List projection (contract change, 2026-08-02).** The list response is a
  SUMMARY row, not a detail document — the full corpus serialised to 1293 KB and
  ~66 % of that was step `command` text and per-detection `description` prose no
  list view renders. Measured **1293.2 KB → 431.2 KB (-66.7 %)**. Each step keeps
  only `{id, name, mitre_technique, expected_detections:[{type, plane}]}` — the
  fields the console actually reads off a list row (technique/detection-type/plane
  facets in `FilterPalette`, `useScenarioFilter`, `ScenarioGrid`,
  `StackCoverageView`, and `steps.length` in `AppConsole`). Four top-level fields
  are dropped whole: `cleanup`, `external_tools`, `success_criteria`, and the
  unprojected `steps` payload. The envelope carries `projection: "summary"` so a
  consumer can tell a summary row from a detail document without guessing at
  missing keys. **`GET /api/scenarios/{id}` is unchanged** and remains the source
  for commands, cleanup, identity, causality, `platform_variants`,
  `detection_id`/`ttp_ref` and `verification_xql`.
- `download` dispatches through `push_generator.resolve_target` — see §4. `auto`
  prefers POSIX for back-compat and falls back to `windows`. **409, not 400**, is
  returned when the request is well-formed but the scenario's content cannot
  satisfy the target; the `detail` names every offending step, its reason code
  and the fix. `k8s` is gated on the POSIX resolution (its Job runs `/bin/bash -c`
  in `ubuntu:22.04`). `X-CortexSim-Bundle-Target` is always set;
  `X-CortexSim-Bundle-Alternates` appears when both targets are emittable.

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
| POST | `/api/runs/{run_id}/complete` | Agent reports completion | `CompleteRequest`: `{exit_code, summary}` | `{"status":"complete"\|"failed","run_id","tc_verdict"}`; 404 |
| POST | `/api/runs/{run_id}/verify` | **Tier-2** verification against a registered XSIAM tenant (outbound XQL) | query: `force=0\|1` | `{run_id, tc_verdict, reverified, tenant, queries_issued, reason?}`; 200 + `pending` + `reason:"no_tenant_integration"` when no credential; 404 `RUN_NOT_FOUND` |
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
| GET | `/api/agents/install` | Generate ready-to-run installer script | query: `os=linux\|macos\|darwin\|windows`, `token?`, `id`, `server?`, `interval`, `mode=service\|foreground`, `uninstall=0\|1` | `PlainTextResponse` bash (`.sh`) or PowerShell (`.ps1`); 400 `BAD_OS` |
| GET | `/api/agents/binaries` | Inventory the prebuilt beacon shelf | — | `{"binaries":[{os,arch,filename,size_bytes,sha256,modified_at}],"total":N}` |
| GET | `/api/agents/binary` | Download a prebuilt beacon | query: `os`, `arch` (`uname` spellings accepted) | `FileResponse` + `X-CortexSim-Agent-SHA256`; 400 `BAD_OS`/`BAD_ARCH`; 404 `AGENT_BINARY_UNAVAILABLE` |
| GET | `/api/agents/binary/sha256` | Expected digest for a target | query: `os`, `arch` | `PlainTextResponse` hex digest; same 400/404 |
| POST | `/api/agents/install/telemetry` | Installer reports its terminal stage/code | `InstallTelemetry` (length-capped, control chars stripped) | `{"status":"recorded"}`; also an `agent.install` SSE frame |
| GET | `/api/agents/install/attempts` | Read recent install attempts | query: `limit` (default 25) | `{"attempts":[…],"total":N}` — in-memory deque (100), lost on restart |
| POST | `/api/agents/enroll/tokens` | Mint a TTL / max-uses / revocable enrollment token | `{label?, ttl_seconds?, max_uses?}` (`ttl_seconds` default 3600, `ge=60`, `le=2_592_000`) | token (revealed once) |
| GET | `/api/agents/enroll/tokens` | List token metadata (never the secret) | — | `{"tokens":[…]}` |
| DELETE | `/api/agents/enroll/tokens/{token_id}` | Revoke a token | — | `{"status":"revoked"}` |
| POST | `/api/agents/enroll` | Redeem a token; SimCore **assigns** the agent id | `EnrollRequest`: `{token, hostname, os, capabilities[], desired_name?}` | `{"status":"enrolled","agent_id"}`; 4xx `ENROLL_DENIED` |
| GET | `/api/agents` | List registered agents (newest `last_seen` first) | — | `{"agents":[<agent.to_dict()>],"total":N}` |
| POST | `/api/agents/register` | Register/re-register a beacon (idempotent, legacy self-asserted id) | `RegisterRequest`: `{agent_id, hostname, os, capabilities[]}` | `{"status":"registered","agent_id","message"}` |
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
- **The installer needs no compiler and no public-internet egress on the target**
  (changed 2026-07-31; it previously ran `go install …@latest` against
  proxy.golang.org, which no hardened endpoint allows). SimCore *serves* the
  beacon: `{linux,darwin}×{amd64,arm64}` + `windows/amd64` are cross-compiled into
  `/app/agent-dist`
  by the `agent-builder` stage of `core/Dockerfile` (baked into the image, and
  **not** shadowed by any compose mount) or on a host-run dev SimCore by
  `make agent-dist` → `scripts/build-agent-dist.sh`. `CORTEXSIM_AGENT_DIST` points
  at an scp'd drop instead. With an empty shelf `/api/agents/binary` returns a 404
  that names what *is* available and the command that fixes it.
- Acquisition order in the script: `CORTEXSIM_BIN` (pre-staged — fully air-gapped)
  → download from this SimCore + sha256 verify → `CORTEXSIM_SRC` local source build
  (dev escape hatch, needs Go) → fail `BINARY_UNAVAILABLE`. A checksum mismatch is a
  hard stop, never a silent fallthrough to a compiler.
- `?mode=service` (**default**) installs a supervisor so the beacon survives the
  SSH session and a reboot — systemd system unit as root (`Restart=always`), a
  `--user` unit otherwise (with the `runuser`/linger caveat printed), or a launchd
  `LaunchDaemon`/`LaunchAgent` on macOS. With no supervisor present it detaches via
  `setsid`+`nohup` and reports `DEGRADED_NO_SUPERVISOR` rather than claiming a
  service exists (verified on a bare `ubuntu:22.04` target: "no service manager
  available — detaching the beacon with setsid/nohup", pid printed, exit 0).
  **Caveat — that degrade only fires when `systemctl` is ABSENT.** On a host
  where the `systemctl` *binary* exists but systemd is not PID 1 (Docker, WSL
  without systemd, LXC, chroot), `cs_install_systemd()` probes
  `command -v systemctl` alone, so it enters the systemd branch and hard-fails
  `SERVICE_START_FAILED` (`cs_fail` calls `exit 1`, not `return 1`, so the nohup
  fallback is unreachable). The DC is left with the binary installed, a unit file
  written, **no beacon running**, an enrolled agent row that reads `online` for
  ~30s before drifting stale, and a `fix:` line recommending `journalctl` on a
  host with no journal. Fix is queued as a handoff patch against
  `core/api/agents.py` (probe the live manager — `systemctl is-system-running ||
  [ -d /run/systemd/system ]` — and make the residual failure `return 1` so it
  degrades instead of dying). `?mode=foreground` keeps the old babysat behaviour;
  `?uninstall=1` returns an idempotent best-effort removal script.
  `CORTEXSIM_BIN_DIR` / `CORTEXSIM_LOG` relocate the install prefix and log.
- Every stage exits with a stable code, printed as `stage= code= / what: / fix:`
  **and** POSTed to `/api/agents/install/telemetry`: `OK` · `UNSUPPORTED_OS` ·
  `UNSUPPORTED_ARCH` · `CURL_MISSING` · `SERVER_UNREACHABLE` · `ENROLL_DENIED` ·
  `NO_AGENT_ID` · `BINARY_FETCH_FAILED` · `BINARY_UNAVAILABLE` · `CHECKSUM_MISMATCH` ·
  `CHECKSUM_SKIPPED` · `BIN_NOT_EXECUTABLE` · `BIN_INSTALL_FAILED` ·
  `SOURCE_BUILD_FAILED` · `SERVICE_START_FAILED` · `DEGRADED_NO_SUPERVISOR`.
- **Windows beacon: SERVABLE (2026-08-05).** `scripts/build-agent-dist.sh` and the
  `agent-builder` stage in `core/Dockerfile` both cross-compile `windows/amd64` to
  `cortexsim-agent-windows-amd64.exe`, so a deployed image serves it from
  `GET /api/agents/binary?os=windows` (verified: HTTP 200, `PE32+ executable
  (console) x86-64`, 5,797,888 B, `X-CortexSim-Agent-SHA256` equal to the
  in-image digest) and `?os=windows` returns a PowerShell installer with **no**
  preflight refusal. `_WINDOWS_PREFLIGHT_UNAVAILABLE` remains, correctly, as a
  statement about a deployment built without the agent-builder stage.
  <br>The earlier note here blamed `scripts/build-agent-dist.sh` for omitting
  `windows/amd64`. **That was the wrong cause with the right conclusion**: the
  script has carried the target since the build-tag split; the omission was in
  the `core/Dockerfile` agent-builder loop, so `make agent-dist` on a dev box
  emitted 5 binaries while every *deployed image* shipped 4 and 404'd — with
  remediation text advising the DC to rebuild the image, the one action that
  provably could not fix it. Nothing caught it because CI's `agent` job proves
  the beacon *compiles* for Windows and `make agent-dist` proves the *script*
  emits it; no gate ever looked inside `/app/agent-dist`. Two now do:
  `tests/installer/test_agent_dist_matrix_parity.py` (static, parses both files,
  fails closed if either parser matches nothing) and `make check-agent-shelf`
  (runs `sha256sum -c` against the **built image** with no host mount, so source
  drift cannot fake it).
  <br>**Still unproven: no Windows host has executed the beacon or the PowerShell
  installer.** Serving, digesting and installer emission are verified; `sc.exe`
  service creation and PS 5.1 execution are not. Push mode (§1.2
  `format=powershell`) remains the path with end-to-end evidence behind it.
- The Go beacon treats **SIGHUP** as a graceful shutdown; previously a hangup hit
  the default disposition and killed the beacon when the terminal went away.

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
| GET | `/api/eal/campaigns/{campaign_id}/collectors` | Resolve every step's collector destination (secret-free) + warnings | — | `{collectors:[{scheme,host,port,path,dataset,auth_header,auth_scheme,token_configured}],destinations:[…],warnings:[…]}` |
| POST | `/api/eal/campaigns/{campaign_id}/collectors/preflight` | Canary-POST one discard-safe record per collector | — | per-collector verdict in the delivery taxonomy; non-allowlisted hosts are `target_not_authorised` and **never contacted** |
| POST | `/api/eal/campaigns/{campaign_id}/bundle` | Pre-render records into a self-contained offline bundle | — | `{bundle_id, download_url, records, skipped_steps[]}` (201) |
| GET | `/api/eal/bundles/{bundle_id}/download` | Download the bundle tar.gz | — | `FileResponse` gzip; 404 |
| GET | `/api/eal/runs` | List EAL runs | query: `campaign_id?` | `{"runs":[...],"total":N}` |
| GET | `/api/eal/runs/{run_id}` | Single EAL run + step results | — | `<EalCampaignRun.to_dict()>` incl. the campaign-level `delivery` rollup; 404 `RUN_NOT_FOUND` |
| POST | `/api/eal/runs/{run_id}/abort` | Cooperative abort of a live campaign | — | `AbortResponse` |

- **This is a parallel run/lifecycle subsystem** distinct from the `Run`/agent
  system. EAL campaigns run via FastAPI `BackgroundTasks` (in-process async),
  *not* via the beacon. Polling `GET /api/eal/runs/{run_id}` tracks status
  (`pending` → terminal). Cross-link: it shares the `consent`/`simulation_authorized`
  safety-gate philosophy with the orchestrator but enforces it via `SafetyPolicy`
  pre-flight, returning 422 `SAFETY_VIOLATION`.
- **Delivery is accounted, not assumed** (2026-07-31). Collector-POST plugins
  (every analytics log-streamer + `email_emitter`) previously counted any POST that
  did not raise as a delivered record — a 401 from a Broker VM, a 404 on a mistyped
  path, or a 302 to a captive portal all produced `status="success"` and a green
  campaign that ingested nothing. Only **2xx** now delivers; `events_emitted` /
  `bytes_sent` report what the collector **accepted**, `detail.delivery` carries
  attempted-vs-delivered, and the step status derives success / partial / error
  against a 12-code taxonomy with a remediation line each. `GET /api/eal/runs/{id}`
  gains a campaign-level `delivery_verdict` (`delivered` / `partial` /
  `not_delivered` / `not_applicable`) computed at read time — no schema migration,
  and pre-ledger runs render `not_applicable`. **Not applied** to
  `oauth_grant_emulator` / `llm_provider_egress` / `agentic_egress`: those POST to
  real third-party endpoints where a 4xx is the expected outcome, so the same rule
  would manufacture a false red.
- **To emit from inside the customer network**, use the offline bundle — record
  building is a pure function, so SimCore pre-renders every record and the artifact
  only POSTs bytes via stdlib `urllib` (no pip install, no SimCore at run time,
  same invariant as a push bundle). Credentials are never written into a bundle;
  the manifest names an env var the operator exports. Steps that cannot be
  pre-rendered (C2 beacon, DNS tunnel, browser driver) are listed in
  `skipped_steps`. **There is still no way to dispatch an EAL campaign to an
  enrolled beacon** — the executor runs in SimCore's own process.
- EAL run statuses are a *different enum* (`pending`/`success`/`failed`/etc. from
  the executor) than core Runs. Abort is cooperative and cannot pre-empt an
  in-flight bundle send. Bundle artifacts accumulate under `data/eal_bundles` with
  no retention sweep (same as `infra/blueprints`).

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
  `{type, run_id, ts, data}` envelope (`run.status`, `run.output`, `result.observed`,
  `agent.status`, `agent.install`, `run.verdict`, …).
- **`run.verdict` (new, 2026-08-02)** is published whenever `Run.tc_verdict` is
  written — i.e. from `complete_run` (seeds the verdict, typically `pending` at
  t=0) and from `connectors/service.py::apply_verdicts`, the single funnel for
  manual `/observations`, credential-backed `/reconcile` and the auto sweep. It
  is the frame that tells a console the POV pass/fail readout moved. Note that
  `core/events.py`'s module docstring still enumerates only the original four
  types and has not been updated.
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

### 1.15 Payload shelf (`core/api/payloads.py`, prefixes `/shelf` **and** `/k8s`)

Staged, digest-pinned tool artifacts, so a scenario's tooling does **not** have to
be fetched from the public internet by the target host at dispatch. Contract:
[`payload-shelf.md`](payload-shelf.md).

**Two prefixes, one implementation.** `_register_shelf_routes` registers the same
handler functions on both routers. `/api/k8s/*` stays mounted **forever** — every
manifest this engine has emitted hard-codes `$CORTEXSIM_SERVER/api/k8s/payloads`
and `/api/k8s/payload/$PN` inside `k8s_manifest._SERVED_FETCH`, and those files
live in customers' GitOps repos. It is an additional mount, **not a redirect**: a
redirect would make a stale manifest silently keep working, so the drift would
never be discovered.

| Method | Full path | Auth | Purpose | Response |
|--------|-----------|------|---------|----------|
| GET | `/api/shelf/payloads` · `/api/k8s/payloads` | **always open** | Inventory + reachability probe + `declared[]` | staged names, sizes, digests |
| GET | `/api/shelf/payload/{name}` · `/api/k8s/payload/{name}` | shelf token | The bytes | `X-CortexSim-Payload-SHA256`, `-Name`, `Cache-Control: no-store`; 404 `PAYLOAD_UNAVAILABLE` |
| GET | `/api/shelf/payload/{name}/sha256` (+ `/api/k8s/…`) | shelf token | Bare hex, **humans only** | a consumer using this verifies nothing |
| GET | `/api/shelf/artifacts` | open | The DERIVED declaration | `{staged[], unstaged[], unpinned[], artifacts[]}` |
| POST | `/api/shelf/compose` | shelf token | Resolve a digest-bound plan, or refuse | 409 `PAYLOAD_NOT_STAGED` / `PAYLOAD_PIN_MISMATCH`; 400 `BAD_CONSUMER` |
| GET | `/api/shelf/resolve/{scenario_id}` | shelf token | Console preflight for one scenario | same shape, `consumer=console` |
| POST | `/api/shelf/stage` | shelf token | Pull a public tool onto **this SimCore** | 201 + `pack_snippet` + `DECLARE_IN_PACK`; 409 `SHELF_EGRESS_DISABLED`; 502 `PAYLOAD_FETCH_FAILED` |

- **`/payloads` is unauthenticated in every mode** — it is the manifest's
  reachability probe, and a probe that can fail for two reasons (no route / bad
  credential) sends a DC to argue with the customer's network team about an auth
  problem that does not exist.
- The **digest is recomputed from the shelf bytes at compose time** and baked
  into what the consumer carries. The consumer verifies against a value it
  carried **in**, never one it fetched from the server it is trusting.
- `composition_id` is deterministic (it excludes `server_url`), so a POV report
  can cite it and two DCs can compare.
- **`air_gapped` + `unstaged_adapters[]` are the anti-false-green fields.** A
  shelf covering two of a scenario's five tools must be legible, not silent.
  ⚠ **Known defect:** an `adapter_ref` that is not in the catalog is silently
  dropped rather than landing in `unstaged_adapters[]` — `payload-shelf.md` §9
  item 12.
- ⚠ **Known defect:** `GET /api/shelf/artifacts` reports
  `used_by = "(no scenario references this adapter yet)"` for every artifact,
  because the handler calls `declared_artifacts()` without `scenarios=`. The
  generated `payloads/sources.json` — same function, called with it — is
  correct. §9 item 13.

**Agent capability `artifact-fetch`.** A beacon that can stage artifacts
advertises `artifact-fetch` alongside `shell` / `identity-harness` on all three
`GOOS` (`agent/capabilities.go`). `GET /api/agents/{id}/tasks` **refuses** to
hand an artifact-carrying task to an agent that lacks it — **409
`AGENT_CANNOT_STAGE_ARTIFACTS`** naming the artifact, the agent's actual roster
and the re-install one-liner — and fails the run rather than letting it hang in
`running`. Without that gate an old beacon would silently drop the unknown
`artifacts` key and run every step **without its tooling**: a manufactured false
negative delivered by the back-compat mechanism itself. Optional
`--artifact-token` / `CORTEXSIM_ARTIFACT_TOKEN` carries a shelf bearer token;
note that `scripts/build-agent-dist.sh` and `GET /api/agents/install` do **not**
yet bake it into the systemd unit, so shelf `token` mode + a beacon is currently
unreachable in the field (it 403s with `ARTIFACT_FORBIDDEN` naming the fix).

### 1.15 Cortex connector — the measurement loop (`core/api/connectors.py`, prefix `/connectors`)

> Added to this table 2026-08-06. Every call below is **opt-in and read-only**;
> none writes to Cortex. With no credential configured they answer **200 with a
> `pending`-shaped result**, never an error and never a green.

| method | path | purpose | notable response |
|---|---|---|---|
| GET | `/api/connectors` | List connector kinds and, per kind, whether a usable integration credential is **configured** and **verified** | includes `preflight_url`; reports **both** credential kinds (`xsiam` = alert read-back, `xsiam_tenant` = XQL) |
| POST | `/api/connectors/{kind}/preflight` | **Staged tenant preflight** — "is my connection working?" answered *before* the POV | `{tenant, kind, base_url_host, overall, stages[], queries_issued, capabilities_confirmed[], capabilities_denied[], proves}` |
| POST | `/api/runs/{run_id}/observations` | Manual batch ingest of alerts a DC exported from the console — no credential, fully offline | seeded `Result` rows gain `observed_at` → MTTD |
| POST | `/api/runs/{run_id}/reconcile?connector=xsiam` | Credential-backed pull for the run's window | same funnel (`apply_verdicts`) as the manual path |
| POST | `/api/runs/{run_id}/verify` | **Tier-2** verification via read-only XQL (see §1.3) | 200 + `pending` + `reason` when no credential |

**Preflight stages**, in order, each with a stable `code` and a `remediation`
naming the consequence *in verdict terms*: `config` → `dns_tls` → `auth` →
`scope_alerts` (kind `xsiam`) / `scope_xql` (kind `xsiam_tenant`) → `datasets`
(opt-in, priced one XQL query per dataset) → `clock`. **Every stage runs even
when an earlier one degraded**, and a skipped stage is reported explicitly as
`SKIPPED` / `PF_SKIPPED_UNREACHABLE` — an absent stage would read as "fine". Only
an unreachable host short-circuits.

`queries_issued` is in every response **on purpose**: a preflight driven by an
injected transport reports `0`, and the `proves` string says so verbatim, so a
mocked green can never be quoted as "connection validated".

> **Wire caveat (open as of 2026-08-06).** The console's Readiness surface calls
> `POST /api/xsiam/tenants/{name}/preflight` for `kind: xsiam_tenant`. **That
> route does not exist** — it returns **405**, which the client's 404-only
> fallback does not catch, so the DC sees `preflight failed: Method Not Allowed`.
> The working route for both kinds is `POST /api/connectors/{kind}/preflight`.
> See `ui/src/components/console/ReadinessView.jsx::TenantRow.runPreflight`.

**Still undocumented in this table:** `/api/xsiam` (the ~116-operation read-only
Cortex operation catalog) and the `/api/runs/*` storyline + causality routes.
Both are covered by their own design docs.

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

- **The queue is durable** (GAP-API-005 closed). `orchestrator._queue` is a
  write-through cache over the `queued_tasks` table and is rehydrated by
  `orchestrator.rehydrate()` at boot; a SimCore restart restores undelivered
  tasks and fails any orphaned `running` run whose task was lost.
- `_resolve_adapter_placeholders` substitutes `{adapter:TOOL-XYZ}` in step commands
  with the adapter's rendered `run_template`; unresolved placeholders are left raw
  so the agent surfaces the failure instead of silently no-op'ing. ⚠ Note
  `generate_bash` does **not** substitute them — a push bundle would ship the
  literal string `{adapter:TOOL-LINPEAS}`. No shipped scenario uses the
  placeholder, which is why this has never been caught.

> ✅ **GAP-AGENT-002 is CLOSED.** The historical wire-shape mismatch (server
> emitting `steps[]`, beacon expecting a flat `command`) no longer exists. Verified
> 2026-08-05 by a real pull-mode launch of `SIM-EDR-022` against a compiled beacon:
> the task was received (`steps=5`), executed as a causality-chained run under an
> `apache2` CGO, and reported per-step output and completion.

> ⚠️ **The artifact-staging phase is NOT wired into launch.** `Task.artifacts`
> exists on the wire, round-trips durably, and the beacon honours it: it stages
> **all-or-nothing before any step runs**, verifying each artifact's sha256
> against the digest carried in the task, and on failure emits a per-step
> `ARTIFACT NOT STAGED` frame plus exit **78** (`EX_CONFIG`) with *"THIS STEP DID
> NOT RUN … NOT a gap in the customer's detection coverage"*. But
> **`_handle_pull` never calls `payload_shelf.compose()`**, so `Task.artifacts`
> is `[]` on every real launch and nothing is ever staged; and `compose()` emits
> `url`/`dest_path` while the beacon's `ArtifactSpec` requires `path`/`dest`, so
> even a wired call would be refused with `ARTIFACT_SPEC_INVALID`. See
> [`payload-shelf.md`](payload-shelf.md) §9 items 5 and 5a.

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

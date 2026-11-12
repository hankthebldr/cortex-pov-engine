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
- **165 HTTP routes total** (counted 2026-11-11 on the host venv with the walker
  below, on `feature/api-ingestion-doors`; not re-counted against a built image).
  Routers under `/api`, plus the app-level `GET /api/health` and a static file mount
  at `/`, plus 4 FastAPI-owned docs/openapi routes. The walker reads 162 on dev
  `8c90739` (the 161 that stood here was 1 stale); the ingestion doors read API adds 3
  (§1.16b). Per-prefix breakdown: run the walker; a quoted breakdown went stale twice.

> **Count it, do not quote it.** The "65 HTTP routes / 11 routers" line that stood
> here was two content passes stale, and the 133 after it was 20 stale. The
> command is the ground truth. The one-liner this section used to carry
> (`len([r for r in app.routes if getattr(r,'methods',None)])`) returns **4** on
> FastAPI 0.141: `include_router` now keeps each router behind a wrapper, so the
> count has to descend into it.
>
> ```bash
> cd core && CORTEXSIM_ENV=development CORTEXSIM_BASE_DIR=$PWD/.. \
>   CORTEXSIM_SECRET=count-only-not-a-real-key python - <<'PY'
> from main import app
> def leaves(routes):
>     for r in routes:
>         sub = getattr(r, "original_router", None)
>         if sub is not None and not getattr(r, "methods", None):
>             yield from leaves(sub.routes)
>         else:
>             yield r
> print(len([r for r in leaves(app.routes) if getattr(r, "methods", None)]))
> PY
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
| GET | `/api/scenarios/{scenario_id}/tenant-content` | What the scenario (or saved draft) needs to exist in the tenant. Read-only; see §1.2.1 | — | the tenant-content manifest, 404 `SCENARIO_NOT_FOUND`, 422 `TENANT_CONTENT_UNREADABLE` |
| GET | `/api/scenarios/{scenario_id}/infra-hints` | Resolve `external_tools[].adapter_ref` → IaC module suggestions | — | `{scenario_id, plane, adapter_refs[], resolved_adapters[], unresolved_refs[], suggested_modules[]}` |
| GET | `/api/scenarios/{scenario_id}/download` | Self-contained execution bundle | query: `format=bash\|k8s\|powershell\|auto` (default `auto`) | `PlainTextResponse` shell/`.ps1`/yaml w/ `Content-Disposition`; 400 `INVALID_FORMAT`; 409 `BUNDLE_TARGET_UNSATISFIABLE` |

- `plane` is upper-cased server-side; `uc_ref` exact-match; `ttp_ref` filtered in
  Python against the ORM rows (so the list projection does not affect it).
- **List projection (contract change, 2026-08-02).** The list response is a
  SUMMARY row, not a detail document — the full corpus serialised to 1293 KB and
  ~66 % of that was step `command` text and per-detection `description` prose no
  list view renders. Measured **1293.2 KB → 431.2 KB (-66.7 %)**. Each step keeps
  only `{id, name, mitre_technique, expected_detections:[{type, plane}]}` (plus
  `ttp_ref` since 2026-11-10, next bullet) — the
  fields the console actually reads off a list row (technique/detection-type/plane
  facets in `FilterPalette`, `useScenarioFilter`, `ScenarioGrid`,
  `StackCoverageView`, and `steps.length` in `AppConsole`). Four top-level fields
  are dropped whole: `cleanup`, `external_tools`, `success_criteria`, and the
  unprojected `steps` payload. The envelope carries `projection: "summary"` so a
  consumer can tell a summary row from a detail document without guessing at
  missing keys. **`GET /api/scenarios/{id}` is unchanged** and remains the source
  for commands, cleanup, identity, causality, `platform_variants`,
  `detection_id` and `verification_xql`.
- **List projection keeps `ttp_ref` (2026-11-10).** Each slim detection is now
  `{type, plane, ttp_ref}`. `expected_detections[].ttp_ref` is the only bulk
  source of the card → scenario link, and Catalog › TTP cards joins it to
  `tc_ref` / `tc_refs` to group cards by use case, test case and FY27 subdomain
  (`ui/src/components/console/catalogBindings.js`). It is a projection, not a
  default: a detection authored without `ttp_ref` gains no null key, so the
  console can tell "this SimCore does not serve card links" (key absent
  everywhere → grouping disabled, with the reason) from "no scenario cites this
  card". Cost, measured on the 193-scenario corpus: **+35.8 KB**
  (1377 detections × 26 bytes; 555.7 KB → 590.7 KB, +6.3 %), against 1770 KB
  for the same scenarios as detail documents.
- `download` dispatches through `push_generator.resolve_target` — see §4. `auto`
  prefers POSIX for back-compat and falls back to `windows`. **409, not 400**, is
  returned when the request is well-formed but the scenario's content cannot
  satisfy the target; the `detail` names every offending step, its reason code
  and the fix. `k8s` is gated on the POSIX resolution (its Job runs `/bin/bash -c`
  in `ubuntu:22.04`). `X-CortexSim-Bundle-Target` is always set;
  `X-CortexSim-Bundle-Alternates` appears when both targets are emittable.

### 1.2.1 Tenant content (read-only)

A scenario's expected detections are three different kinds of work, and until
2026-11-10 the console rendered them as one row of type tags:

| Ownership | Types | What it asks of a DC |
|---|---|---|
| `run` | XQL | a query to run after the step, to confirm the telemetry landed |
| `deploy` | BIOC · IOC · Correlation | must exist in the tenant before the run; this is what raises the alert |
| `built-in` | ABIOC · Analytics · Prevention | ships with Cortex; needs the module licensed and enabled, nothing to import |

| Method | Full path | Input | Notes |
|--------|-----------|-------|-------|
| GET | `/api/scenarios/{scenario_id}/tenant-content` | a corpus scenario or a saved draft row | `core/api/scenarios.py` |
| POST | `/api/scenarios/drafts/tenant-content` | an UNSAVED draft: the body `POST /api/scenarios/drafts` takes | `core/api/drafts.py`; validates the same way (422 `DRAFT_SCHEMA_INVALID`) and **persists nothing** |

One resolver backs both: `core/engine/tenant_content.py::build_manifest`, over
the raw cards in `engine.ttp_catalog`. The Composer derives the same facts in
the browser (`ui/src/components/console/composerContent.js`) so its canvas
follows each edit without a round trip; `spec/tenant_content_cases.json` is
asserted by both test suites so the two cannot disagree.

```json
{
  "scenario_id": "SIM-AIACC-003", "name": "...",
  "catalog_loaded": true, "tenant_verified": 0, "write_path": false,
  "summary": {"correlation": {"total": 1, "deployable": 0, "intent_only": 1,
                              "unreadable": 0, "declared": 0, "card_only": 1, "unbound": 0}},
  "rules": [{"rule_id": "CR-AIACC-0003", "ttp_ref": "TTP-2026-0009", "name": "...",
             "content_status": "intent-only", "binding": "card-only",
             "logic": "XQL(...) OR XQL(...) OR XQL(...)", "logic_kind": "composition",
             "inputs": [{"kind": "xql", "name": "...", "state": "step",
                         "resolved_kind": "xql", "steps": ["step-02"]}],
             "declared_by": [], "deploy": null}],
  "unbound": [], "queries": [], "biocs": [], "iocs": [], "built_in": [], "notes": []
}
```

- **Two axes per rule.** `content_status` is what the CARD holds: `intent-only`
  (a name, an id and a logic line), `deployable` (a complete `deploy` block) or
  `unreadable` (a shape the resolver does not know, with `content_error`).
  `binding` is what the SCENARIO asks for: `declared` (a step expects the rule,
  so a run scores it) or `card-only` (on a cited card, expected by no step).
- **No correlation rule in the corpus is deployable.** Every one is
  `intent-only`: the card schema allows only `name`, `rule_id` and `logic`, and
  `logic` is an authoring note such as `XQL(a) OR XQL(b)`, not XQL a tenant can
  run. A test pins the count at zero (`tests/engine/test_tenant_content.py`).
  The `deploy` field names the classifier recognises are candidates until they
  are pinned to a rule exported from a real tenant.
- **Nothing is invented.** A stub is `deploy: null`. There is no default
  severity and no synthesised description.
- **A zero is degraded.** No card corpus → 200 with `catalog_loaded: false`,
  null counts and a single `CATALOG_NOT_LOADED` note; render it as degraded,
  never as "nothing needed". Other `notes[].code` values: `NO_CARD_BINDING`,
  `CARD_NOT_FOUND`, `RULE_UNREADABLE`, `RULE_LOGIC_UNPARSED`,
  `UNBOUND_CORRELATION`, `UNKNOWN_DETECTION_TYPE`, `NO_CARD_REFERENCE`,
  `DETECTION_NOT_ON_CARD`.
- **No write path, no tenant contact.** `write_path` is `false` and
  `tenant_verified` is `0` in every response. Neither route makes an outbound
  call (a test refuses any socket connect), and nothing here reaches
  `OP-CORRELATIONS-INSERT`. Creating a rule is the DC's own action in the XSIAM
  console.

**See also §1.16**, which reads the same cited cards for a different question:
`GET /api/scenarios/{id}/prerequisites` (the datasets those cards read and what
must exist for each) and `GET /api/scenarios/{id}/query-library` (the cards'
queries as paste-ready text). The `queries[]` here are the queries a step
*declares*, as card objects; the query library is every query on the cited
cards, as text.

The on-disk `detection_scanner/exports/correlation/*.json` files carry the same
status (`content_status`, `deploy: null`, `importable_rules: 0`) and no longer
stamp `severity: "high"` on every rule.

### 1.3 Runs (`core/api/runs.py`, prefix `/runs`)

> Note: the router carries the `/runs` prefix (GAP-API-008), so every route lives under
> `/api/runs/...`, and the launch route is `POST /api/runs`. `POST /api/run` (singular,
> the historical path) survives as a deprecated alias on the unprefixed `compat_router`
> (`core/api/runs.py:62-63`) so older clients keep working.

| Method | Full path | Purpose | Request body | Response |
|--------|-----------|---------|--------------|----------|
| POST | `/api/runs` (alias `/api/run`, deprecated) | Launch a scenario run (pull or push) | `LaunchRequest`: `{scenario_id, mode, target_agent_id?, identity?, consent?, authorized_by?, allow_runtime_install?, agent_profile_mode?, bas_mode_attested?, soakless_attested?, attested_by?, paired_with_run_id?, canary_run_id?}` (the last six are the prevention-efficacy launch context, §1.3.1) | `{run_id, mode, message, download_url?}`; 400 `INVALID_MODE`; 409 `CONSENT_REQUIRED` / `PAIR_INVALID` / `CANARY_INVALID` (and the other launch-precondition codes); 422 `LAUNCH_FAILED` and a malformed `agent_profile_mode` |
| POST | `/api/runs/canary` | Launch the block-mode canary against an agent (§1.3.1) | `CanaryRequest`: `{target_agent_id}` | `{run_id, scenario_id}`; 409 `CANARY_NOT_AVAILABLE` when `CORTEXSIM_CANARY_SCENARIO_LINUX` is unset |
| GET | `/api/runs` | List all runs (newest first) | — | `{"runs":[<run.to_dict()>],"total":N}` |
| GET | `/api/runs/{run_id}` | Run detail + status | — | `<run.to_dict()>` plus `step_outcomes[]` (§1.3.1); 404 `RUN_NOT_FOUND` |
| GET | `/api/runs/{run_id}/pair` | This run, its report/block partner and the per-detection delta (§1.3.1) | — | `{runs:[<run>,<run>], delta:{report_run_id, block_run_id, severed_at_step, steps[]}, merge:{merge_class, basis, merge_reason}}` (`merge.code` is present only when a stored ledger is corrupt: `CASE_LEDGER_UNREADABLE`); 404 `RUN_NOT_FOUND`; 409 `RUN_NOT_PAIRED` |
| POST | `/api/runs/{run_id}/case-ledger/refresh` | Re-read the cases named in the run's stored `case_ledger` (read-only; cases only, stored alerts kept). Explicit operator action | query: `force=true\|false` (default false) | `{run_id, refreshed, queries_issued, merge_class, code, read_at, ledger, reason_code}`; on a read also `previous_merge_class`; `refreshed: false` (200, no tenant read) carries `reason_code` + `reason`: `CASE_LEDGER_NAMES_NO_CASE` (the ledger names no case; only the next reconcile advances it) or `CASE_LEDGER_READ_RECENTLY` (read inside `CORTEXSIM_CASE_LEDGER_MIN_INTERVAL_MINUTES`; also `retry_after_seconds`; `force=true` reads anyway); `reason_code` is `null` on a read. 404 `RUN_NOT_FOUND` (no such run); 409 `NO_CASE_LEDGER` (run never reconciled, no stored ledger); 409 `CASE_LEDGER_UNREADABLE` (stored ledger corrupt); 409 `CASE_LEDGER_TENANT_UNKNOWN` (legacy ledger records no integration and more than one could have read it); 409 `CASE_LEDGER_TENANT_MISMATCH` (the ledger records a `tenant` and its integration now points at another; `detail: {recorded, integration, integration_tenant}`; no read, stored ledger kept); 404 `CONNECTOR_NOT_FOUND` (no such connector kind); 404 `INTEGRATION_NOT_FOUND` (the integration the ledger recorded no longer exists); 400 `NO_INTEGRATION` (no credential may serve the connector); 500 `CREDENTIAL_ERROR` (credential could not be loaded); 502 `CONNECTOR_PULL_FAILED` (the tenant case read failed; stored ledger kept); any other code 500 |
| GET | `/api/runs/{run_id}/report` | POV report | query: `format=markdown\|json` (default markdown) | markdown `PlainTextResponse` **or** JSON `{run, scenario, results, coverage, mttd, tools_used, case_ledger}` (markdown gains a `## Case` section); 404 |
| GET | `/api/runs/{run_id}/report/matrix` | detection_matrix.csv (Phase 8) | — | `text/csv` attachment |
| GET | `/api/runs/{run_id}/report/navigator` | ATT&CK Navigator v4.5 layer JSON | — | `application/json` attachment |
| GET | `/api/runs/{run_id}/report/bundle` | tar.gz of matrix + navigator + exec_summary | — | `application/gzip` attachment |
| POST | `/api/runs/{run_id}/output` | Agent streams output (append) | `OutputRequest`: `{output}` | `{"status":"ok","run_id"}`; 404 |
| POST | `/api/runs/{run_id}/complete` | Agent reports completion | `CompleteRequest`: `{exit_code, summary, steps[]?}` (`steps[]` is per-step host evidence, §1.3.1; an old beacon omits it) | last endpoint: `{"status":"complete"\|"failed","run_id","tc_verdict"}`; fan-out still owed endpoints: `{"status","run_id","pending_endpoints"}`; run already finished: `{"status":<terminal>,"run_id","already_terminal":true}`; 404 `RUN_NOT_FOUND` |
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
- **`authorized_by`** (`str`, optional) names the operator. With
  `consent.simulation_authorized` it is the first key of the live gate for the
  run's in-process EAL steps (§1.3.2); a blank name counts as none. It gates
  nothing else and refuses no launch.
- `/complete` appends a `--- COMPLETION SUMMARY ---` block to `run.output` and
  decrements `open_tasks`. A non-zero `exit_code` makes the run `failed`, and
  failure is sticky across a fan-out. The endpoint that brings `open_tasks` to 0
  terminalises the run (`failed` if any endpoint failed, else `complete`), stamps
  `completed_at`, publishes the terminal `run.status` frame and scores the run.
  It acts only on a run still in flight — the same guard `/abort` uses, so a
  `failed` fan-out still owed endpoints keeps accepting them. On a run that
  already finished (`complete`, `failed` with `completed_at`, `staged`,
  `aborted`, or failed by an `AGENT_CANNOT_STAGE_ARTIFACTS` refusal) a late or
  duplicate `/complete` is a 200 no-op with `already_terminal: true`: status,
  `completed_at`, output and verdict are unchanged, and nothing is published or
  re-scored.
- **`/abort`** (GAP-API-001, **closed 2026-06-08**) transitions a `pending`/`running`
  run to the `aborted` state, stamps `completed_at`, dequeues the task via
  `orchestrator.abort(run_id)`, and publishes a `run.status` SSE frame. It is
  **idempotent**: a run already in a terminal state returns 200 with
  `was_terminal: true` rather than an error.
- **`/control`** is the lightweight stop-signal the in-flight agent polls. It
  returns `abort=true` when the run is in the orchestrator's aborted set, has
  reached any terminal status, or has vanished (DB reset) — so the agent halts
  rather than spinning.

#### 1.3.1 Prevention-efficacy wire contract

A `prevent` expectation is credited only when **two independent witnesses agree**:
the tenant alert says *Prevented* and the host says the step's process was killed.
Neither alone is enough. This section is the wire contract for the host witness, the
launch attestation, the pair and the canary. The vocabularies live in
`core/engine/outcome.py` and are copied here from its constants.

**Host evidence — `POST /api/runs/{run_id}/complete` `steps[]`.** One record per step
(`agent/beacon/step_outcome.go::StepOutcome`; server model `StepOutcomeIn` in
`core/api/runs.py`):

| Field | Type | Meaning |
|---|---|---|
| `step_id` | string | Scenario step id (e.g. `step-02`) |
| `exit_code` | int or null | `null` when the step never produced one (anchor death, `not_run`) — never a misleading `0` |
| `termination` | string | One of the termination vocabulary below |
| `signal` | int, optional | Signal number when `termination` is `signaled` |
| `reason` | string, optional | Required on `not_run`; otherwise omitted |

- **Termination vocabulary** (`TERMINATIONS`): `exited`, `signaled`,
  `anchor_terminated`, `beacon_lost`, `timeout`, `aborted`, `not_run`. The beacon emits
  every value except `beacon_lost`, which is synthesised server-side (below).
- **Host witness** (`HOST_WITNESS`): `signaled` and `anchor_terminated` only. Only
  these can satisfy the host half of the two-witness rule. `signaled` means *a signal
  ended the process*, not *the agent killed it*: the beacon classifies a chained
  step's rc in 129..159 as `signaled` (`step_outcome.go::classifyChainedRC`), so a voluntary
  `exit 137` or a SIGSEGV crash (139) also reads `signaled`. The two-witness rule bounds
  this — without a `Prevented` alert it can never be more than `pending`.
- **`reason` vocabulary on a step record**: `UPSTREAM_SEVERED`, `UPSTREAM_NOT_EXECUTED`,
  `UPSTREAM_FAILED`, `TOOL_ABSENT`. An out-of-vocabulary `termination` or `reason`, or a
  `not_run` without a `reason`, is rejected 422 — never tolerated.
- **Idempotent and non-downgrading.** While the run is in flight a duplicate `/complete` upserts the same rows; once the run is terminal it is ignored (`already_terminal: true`) and writes nothing. A
  `signaled` / `anchor_terminated` / `beacon_lost` row already on file is never replaced
  by a non-witness termination reported by another endpoint of a fan-out; a witness may
  replace anything. `started_at` is set by the first `/output` for a step
  (`OutputRequest.step_id`) and is not touched by `/complete`. Despite its name it is
  **not** the step's start: the beacon posts a step's output after the step ends, so
  `started_at` means "the host had finished this step by then" (kept for migration
  reasons; `pick_severed` relies on these after-end semantics).
- **Status rule.** A non-zero `exit_code` fails the run — except when the host witnessed a
  sever on a step carrying a `prevent` expectation in a `block`-mode run: then the kill is
  the expected result and the run is not `failed`. The severed step is stored on
  `Run.severed_at_step` (first writer wins).
- **Old beacons.** An absent `steps[]` writes no `step_outcomes` rows and leaves the
  status rule unchanged.

**Result outcomes** (`OUTCOMES`): `prevented`, `detected`, `missed`, `pending`,
`not_reached`, `not_executed`. `not_reached` (an upstream step was severed or failed) and
`not_executed` (the step never ran) are excluded from the efficacy denominator
(`EXCLUDED_FROM_DENOMINATOR`), as are soak-pending rows (`ANALYTICS_ACTIVATION_PERIOD`,
`DEDUP_WINDOW`). In `report` or `unknown` mode a `prevent` expectation is scored as a
detect and is never graded as prevention. Reason codes live in `outcome.py` (for example
`PREVENTION_ALERT_WITHOUT_TERMINATION`, `PREVENTION_UNCONFIRMED_NO_ALERT`,
`PREVENTION_UNCONFIRMED_BEACON_LOST`, `DETECTED_NOT_PREVENTED`, `BLOCK_MODE_UNCONFIRMED`,
`ATTESTATION_CONTRADICTED`, `UPSTREAM_SEVERED`, `TOOL_ABSENT`, `EVIDENCE_INCOMPLETE`,
`PASS_WITHHELD_UNSCOPED`, `HOST_EVIDENCE_ABSENT`, `RUN_ABORTED`).

- **`missed` needs the host's word that the step ran — for every expectation.** A row
  (`detect` or `prevent`, any profile mode) whose window closed with no alert and **no
  host record** for its step (push mode, an old beacon with no
  `steps[]`, a staging failure, a reconcile before `/complete`) reads `pending` /
  `HOST_EVIDENCE_ABSENT`, never `missed`: nothing says the step ran. With `exited` on
  record a block-mode `prevent` row is `missed` / fail; a `detect` row (or a
  report-mode `prevent` row) is `missed` with no `kpi_verdict` from reconcile — that is
  the Tier-2 verifier's. An `aborted` termination reads `pending` /
  `RUN_ABORTED` — for `detect` and `prevent` rows alike, in every profile mode (the run
  was cut short; its verdict is undefined); a `timeout` stays
  `missed` / fail (the step ran its whole budget unprevented).
- **A silent `detect` row is written `missed` — when the host says it ran.** Once its
  window closes with no matched alert, the silent-row judgement
  (`outcome.py::silent_detect_verdict`) writes `outcome = missed` only when the step's
  host record is `exited`, `timeout`, `signaled` or `anchor_terminated`, and only on
  complete evidence (a host-scoped, untruncated, lossless credential pull; else
  `pending` / `EVIDENCE_INCOMPLETE`). No record or `beacon_lost` → `pending` /
  `HOST_EVIDENCE_ABSENT`; `aborted` → `pending` / `RUN_ABORTED`. Report/unknown-mode
  `prevent` rows follow the same rule. `kpi_verdict` is left to the Tier-2 verifier. A later matched
  alert replaces `missed` with `detected`.
- **An outcome-less row is awaiting judgement, never `missed`.** One rule,
  `outcome.py::row_status`, names every row's status on every surface (report matrix
  and icons, scorecard, storyline, console): the engine's `outcome` when it decided one;
  otherwise `detected` when `observed` is true (a manual validation, incl. pre-branch
  runs), else `pending` — rendered "awaiting reconcile", with no reason token. `missed`
  exists only as an engine outcome: neither a terminal run status nor an `observed_at`
  without `observed` makes one.
- **`pending` is never a coverage hit.** Every coverage numerator (`/api/results`,
  the run report headline and by-type table, `detection_accuracy`, the reconcile
  summary, the exec summary and Navigator layer, the MITRE heatmap tally and the TTP
  runs badge) counts through `outcome.py::is_hit` over the one denominator rule:
  `detected` / `prevented` when an outcome exists, `observed` only for an outcome-less
  row. A prevent row with an alert but no host witness (`pending` /
  `PREVENTION_ALERT_WITHOUT_TERMINATION`, `observed` true) is a hit nowhere.
- **A host exclusion replaces a Tier-1 silent miss.** A later `not_run` for a step moves
  a `missed` row (any expectation) to `not_reached` / `not_executed` and clears its
  `fail`; a later `aborted` moves it to `pending` / `RUN_ABORTED` (a Tier-1 `fail`
  becomes `pending`); a decided `detected` / `prevented` row is never replaced.

**`GET /api/runs/{run_id}` carries `step_outcomes[]`** — `[{step_id, termination, signal,
rc, reason, started_at, recorded_at}]` (`started_at` = first `/output` arrival, i.e.
after the step ended — see above) in scenario step order (rows for steps the
scenario no longer lists go last), an empty list when there are none, projected
read-only from the `step_outcomes` rows (`core/api/runs.py::_step_outcomes_view`). A row
with `termination: null` means the step's output arrived but its end was never reported
(the beacon died before `/complete`); consumers must not read it as `exited`.
**The match window.** For each Result it is `[executed_at, window_end]`, where
`window_end = max(executed_at, the step's host record) + window` (host record =
`started_at`, else `recorded_at`; window = the per-type window, else the request's).
One helper, `connectors/service.py::result_window_end`, computes it; the matcher's upper
bound (passed per result as `window_end_by_result`) and the silent-row judgement both
use it, so a queued or slow step's alert is accepted until the step's own window closes
and the step is never judged silent before then. The lower bound and the MTTD start are
the seeded `executed_at` (launch): `Result.executed_at` is NOT re-stamped, because the
host record lands after the step ended and an alert raised during the step would fall
before a re-stamped bound.

**Required-binary preflight (`required_binaries`).** For each step whose command
substitutes an adapter placeholder, the orchestrator writes the pack's `install.binary`
into that step's `required_binaries` (`engine.orchestrator._resolve_adapter_placeholders_with_binaries`;
written only when non-empty). Before running step *i* the beacon checks every listed
binary with `exec.LookPath` (skipped on Windows). On a miss it:

1. POSTs an `/output` line prefixed `!! TOOL_ABSENT: <binary>`;
2. records the step `not_run` / `TOOL_ABSENT` and every later step `not_run` /
   `UPSTREAM_NOT_EXECUTED`;
3. completes the run with **exit code `127`** and a summary ending
   `| TOOL_ABSENT: <binary> at <step_id> — step not run`.

The Results for those steps read `not_executed`, not `missed`. A resolved pack with no
`install.binary` is not checked and logs `TOOL_PRESENCE_UNCHECKED` server-side. A related
marker: when the executor reports a termination the beacon does not recognise it records
the step `exited`, writes `!! TERMINATION_UNKNOWN: <termination> signal=<n>` to `/output`
and appends `| TERMINATION_UNKNOWN: <step_id>` to the `/complete` summary — an
unrecognised value never reads as a clean exit.

**Beacon-lost terminalisation** (`core/engine/beacon_lost.py`). A Cortex XDR agent that is
not in BAS mode may kill the beacon, so `/complete` never arrives. The heartbeat sweep
(`core/api/agents.py::sweep_agents_once`) calls `terminalise_lost_runs` for every agent
with `last_seen is None` or `now - last_seen >= CORTEXSIM_BEACON_LOST_AFTER_SECONDS`
(default 180). The console's online / stale / offline label (30 s / 300 s) is display only
and does not gate the sweep. `pick_severed` attributes the loss: a step the host already
terminated (`signaled` / `anchor_terminated`) if one is on record; otherwise the **first**
scenario step with no `step_outcomes` row (`/output` writes a row only after a step ends);
if every step has a row, the last step, its witnessed termination standing. The severed
step reads `beacon_lost`, later steps `not_run` / `UPSTREAM_SEVERED`, and a `prevent`
expectation on the severed step is `pending` / `PREVENTION_UNCONFIRMED_BEACON_LOST` — it
can never read `prevented`.

**Launch attestation (`LaunchRequest`).** Operator-declared and persisted verbatim on the
Run; the engine never touches the agent profile.

| Field | Meaning |
|---|---|
| `agent_profile_mode` | `report`, `block` or `unknown` (default `unknown`); anything else is a 422 |
| `bas_mode_attested` | Operator attests the Cortex agent is in BAS mode (only the simulation is terminated) |
| `soakless_attested` | Operator attests the Analytics activation period has elapsed; without it Analytics / ABIOC rows stay `pending` / `ANALYTICS_ACTIVATION_PERIOD` |
| `attested_by` | Who made the attestation. `attested_at` is stamped server-side (naive UTC) when `agent_profile_mode` is not `unknown` or any attestation field is sent |
| `paired_with_run_id` | Links this launch (the block-mode run) to its completed report-mode run |
| `canary_run_id` | The canary run offered as evidence the profile really blocks |

**The pair — `GET /api/runs/{run_id}/pair`.** A POV runs one scenario twice on one
endpoint, first in `report` mode, then in `block` mode, and the second launch names the
first via `paired_with_run_id`. `engine.pairing.validate_pair` refuses the launch with
**409 `PAIR_INVALID`** (nothing is seeded) when `paired_with_run_id` matches no run, the scenarios differ, the targets differ,
either mode is not `report` / `block`, the two modes are equal, the first run is not
terminal (`complete` or `failed`, with `completed_at`), or the first run is already paired.
`/pair` works from either run (forward via `paired_with_run_id`, else the newest run
pointing at it) and returns the two runs plus a delta joined per
`(step_id, detection_id)`; a detection present on one side only appears with the other side
`null`. 409 `RUN_NOT_PAIRED` when no partner exists.

**The run case ledger (`Run.case_ledger`).** Every reconcile, a zero-match one included,
builds it from the stored ledger's alerts plus the alerts it newly matched (each taken
from this pull when the pull returned it), so a late match into a new case is seen
against the earlier ones. When no alert carries a case id the ledger is stored with no
tenant read (`NO_CASE_YET` for Medium+ alerts); it advances on the next reconcile, not on
a refresh. Otherwise reconcile reads the cases by id, then the issue statuses by id, and,
on a `SPLIT_NATIVE` that mixes an IP-only and a host-keyed alert, one endpoint record (the
bridge diagnosis): at most three reads per reconcile, one page per 100 ids each. A failed
case read keeps the stored ledger and scores the run with `merge_class: PENDING` (summary
`case_ledger.stored_ledger_kept: true`). The ledger is stored with the `integration` that
read it and its `tenant` (the per-tenant XQL ledger's key, `tenant_ledger_key` of the
normalised base URL): a reconcile carries stored alerts only from the same tenant, a
refresh refuses a ledger whose integration now points elsewhere, and a pair read from two
tenants merges to `PENDING`; a legacy ledger without `tenant` behaves as before. Shape:
`{merge_class, merge_reason, basis, read_at, integration, tenant, code, cases[], alerts[],
issue_read, shared_with_other_runs}`; `read_at`, `resolved_at` and
`observed_at` are naive UTC ISO strings. `merge_class` is `ONE_CASE | SPLIT_SEALED |
SPLIT_NATIVE | LOW_ONLY_NO_CASE | NO_CASE_YET | PENDING`. Severity decides only Low-only,
no case yet, fewer than two Medium+ alerts and Medium+ alerts not yet in a case; the
missing-case check and the split rules read every alert that carries a case id. A case
id the tenant does not return is `PENDING`, never `ONE_CASE`. A split is classified per
boundary between consecutive cases (ordered by first alert): a **seal** (the earlier case
was sealed before the later one's first alert), **native** (the earlier case was open or
auto-resolved, or sealed after) or **unknown** (sealed, no resolve time). Any native
boundary is `SPLIT_NATIVE`; otherwise any unknown one is `PENDING`; only all-seal is
`SPLIT_SEALED`. A case `closed_by` is `open | socfw_auto_triage | auto_resolved |
other_resolved`; an issue adds `socfw_ai_close | unread`. An unrecognised case status,
or a reply row for a case the read did not request, raises and the ledger reports
`CASE_STATUS_UNRECOGNIZED` with merge `PENDING` (that case is never rendered).
`GET /api/runs/{id}/pair` returns the pair-level `merge`; a corrupt stored ledger reads
`PENDING` / `CASE_LEDGER_UNREADABLE`.

*What bounds these reads.* Every page is counted in the reconcile's (or refresh's)
`queries_issued`. They do **not** charge the per-tenant XQL ledger
(`integrations/xsiam/ledger.py`), and neither do reconcile's own alert and incident pulls;
metering connector reads there is a separate change. What does bound them:
`POST /api/runs/{id}/case-ledger/refresh` re-reads cases only (one page per 100 ids) and
refuses to read inside `CORTEXSIM_CASE_LEDGER_MIN_INTERVAL_MINUTES` (60) of the ledger's
`read_at` unless `force=true`. With `CORTEXSIM_AUTO_RECONCILE` on, the auto-reconcile loop
refreshes ledgers of runs started within `CORTEXSIM_CASE_LEDGER_WINDOW_HOURS` (24), at most
`CORTEXSIM_CASE_LEDGER_MAX_RUNS_PER_SWEEP` (20) per sweep, skipping any read newer than the
same minimum interval and terminal ledgers, and stops the sweep on the first HTTP 429; a
429 in the reconcile sweep skips the refresh phase for that cycle. A ledger is terminal
when every case in it is sealed (resolved and not auto-resolved), its merge is not
`PENDING` and every case an alert names is in it; an open or auto-resolved case, a
`PENDING` merge or a missing case can still change, so its ledger keeps being refreshed.

**Disposition is context, not detection evidence** (`Tenant disposition is shown for context. It is not detection evidence.`): it never feeds `detected`/`missed`, MTTD or a
detection verdict. The single exception: `SPLIT_SEALED` withholds a Cross-Source
Correlation Rate FAIL as `pending` (reason: `fail withheld: a case was sealed by the response layer before the chain finished — the split is not the detection engine's`), and only
when the ledger classified every cased alert the KPI counted, in the same case; otherwise
the FAIL stands and the measurement carries `sealed_split_not_applied`. A PASS is never
touched. The bridge diagnosis reads `BRIDGE_UNREAD` (never `BRIDGED`) when the endpoint
read fails, returns no record or more than one record for the host, or a record whose IPs
are all unparseable. The endpoint reply fields used by the bridge diagnosis come from
`docs/reference/xsiam-platform-apis/05-endpoints-api.md` and are unverified against a live
tenant. `tenant-verified` is 0.

**The canary — `POST /api/runs/canary`.** Launches the benign built-in trigger named by
`CORTEXSIM_CANARY_SCENARIO_LINUX` as a pull run with `agent_profile_mode: "block"`. Empty
(the default) returns 409 `CANARY_NOT_AVAILABLE` and block mode stays attested-only. A
launch that passes `canary_run_id` is validated (**409 `CANARY_INVALID`**: unknown run, not
the configured canary scenario, a different target, aborted, or not finished) and stamps
`Run.block_mode_status`: `block_mode_confirmed` only when the canary has a `prevented`
Result, else `block_mode_unconfirmed`. A host witness alone never confirms the profile, so
reconcile the canary before launching the block run. The `BLOCK_MODE_UNCONFIRMED` hold
applies only when `block_mode_status` is `block_mode_unconfirmed`, i.e. a launch passed
`canary_run_id` and that canary produced no `prevented` Result: there is then evidence the
profile does not block, so a `fail` verdict on a `prevent` expectation is held at
`pending` / `BLOCK_MODE_UNCONFIRMED` rather than charged to the product. With no canary
(`CANARY_NOT_AVAILABLE`, `block_mode_status` is `null`;
`tests/api/test_runs_canary.py:117`) there is no evidence either way: the operator's
`agent_profile_mode: "block"` attestation stands and a silent `prevent` row resolves
`missed` / `fail`. The guard protects against a profile the canary proved misconfigured,
never against an unverified attestation; configure `CORTEXSIM_CANARY_SCENARIO_LINUX` to
get it.

> **`tenant-verified` for prevention is 0.** Nothing above has been executed against a
> live tenant: the three host witnesses are proven against a branch SimCore (§5.6) and the
> rest by injected transports. Tier-D has no Cortex agent, so no run there reads
> `prevented`.

#### 1.3.2 In-process EAL steps: the live gate

A pull-mode run's `channel: eal` steps run as one campaign inside SimCore's own
process at launch (`orchestrator._dispatch_eal_in_process`); no beacon is
involved. This is the one path on which a **scenario run** makes SimCore itself
POST to a customer collector, so the campaign goes live only when **every**
condition below holds, and otherwise runs dry-run (records pre-rendered, nothing
POSTed):

1. the launch carried `consent.simulation_authorized: true` **and** a non-blank
   `authorized_by`;
2. every eal step's plugin is a collector plugin (`targets.collector_plugin_names`);
3. every eal step resolves to a configured collector target (§1.9.1) — `explicit`,
   `binding` or `default`; an inline `collector_url` does not count;
4. every such target has a key in the vault;
5. no such target's last preflight is failing (`not_configured` / `unreachable` /
   `refused`);
6. the campaign's total sleep, Σ `sleep_seconds × (iterations − 1)` over its eal
   steps (defaults 0 and 1), is at most **30 s**
   (`EAL_IN_PROCESS_MAX_SLEEP_SECONDS`) — the campaign runs synchronously inside
   the launch request.

The campaign goes live whole or not at all. In a dry run each eal step's
`Run.channel_dispatch` entry has `status: dry_run` (or the executor's own
`not_delivered` / `skipped` / `error`) and a `code` saying why:

| `code` | Meaning |
|---|---|
| `EAL_DRY_RUN_NO_CONSENT` | Every step, when condition 1 fails |
| `EAL_DRY_RUN_NOT_COLLECTOR` | The step's plugin does not POST to a collector target |
| `EAL_DRY_RUN_TARGET_UNRESOLVED` | The step's target resolution is `none`, `error` or `inline` |
| `EAL_DRY_RUN_TARGET_NO_KEY` | The step's target has no key stored |
| `EAL_DRY_RUN_TARGET_FAILING` | The step's target's last preflight failed |
| `EAL_DRY_RUN_SLEEP_CAP` | Total sleep is over 30 s; set on the steps that sleep |
| `EAL_DRY_RUN_HELD` | This step is clean, but another step blocks; `detail` names the blocking steps |

When the gate passes, the keys are read from the vault (`collector_targets.load_tokens`)
only then and only into the in-memory campaign, which runs with
`dry_run: false`, `simulation_authorized: true`, the launch's `authorized_by` and
the resolved targets' exact `host:port` pins as `target_allowlist`. The ledger
`status` is then `dispatched` (code `null`) or the executor's `not_delivered` /
`skipped` / `error`; delivery is accounted by `eal_simulator/delivery.py` (only a
2xx delivers, so a collector 401 reads `not_delivered`). Each eal step's seeded
`Result.executed_at` is that step's own recorded start (naive UTC), or the launch
time when the step has no result. ***tenant-verified is 0***: nothing on this
path has delivered to a live tenant.

### 1.4 Results (`core/api/results.py`, prefix `/results`)

| Method | Full path | Purpose | Request body | Response |
|--------|-----------|---------|--------------|----------|
| GET | `/api/results` | All results across all runs (newest first) | — | `{"results":[...],"total":N}` |
| GET | `/api/results/{run_id}` | Results for a run + coverage + MTTD stats | — | `{run_id, results[], total, coverage:{observed,total,pct,by_type}, mttd}`; 404 if run missing |
| PUT | `/api/results/{result_id}/validate` | DC marks detection observed/not | `ValidateRequest`: `{observed:bool, notes?}` | `<result.to_dict()>`; 404 `RESULT_NOT_FOUND`; 409 `RESULT_STEP_NOT_RUN` / `RESULT_PREVENTED` / `ATTESTATION_NOT_A_WITNESS` / `NOT_AN_ATTESTATION` |
| PUT | `/api/results/{result_id}/notes` | Update notes only | `NotesRequest`: `{notes}` | `<result.to_dict()>`; 404 |

- `validate` with `observed=true` stamps `observed_at = utcnow()` (enables MTTD =
  `observed_at - executed_at`); `observed=false` clears `observed_at`.
- `validate` is an operator **attestation** and writes `outcome`: `observed=true` on a
  row whose outcome is none / `pending` / `missed` sets `detected`; `observed=false`
  **retracts** the attestation only — it clears `observed` / `observed_at`, reverts a
  `detected` the attestation wrote (no matched-alert `Observation`) to none, leaves an
  engine-written outcome alone, and never writes `missed`; on an engine-matched row
  (an `Observation` exists) it is refused with **409 `NOT_AN_ATTESTATION`** and nothing
  changes. It is refused with **409
  `RESULT_STEP_NOT_RUN`** on `not_reached` / `not_executed` (no attestation for a step
  that never ran) **409 `RESULT_PREVENTED`** on `prevented` (an attestation never
  downgrades a two-witness verdict), and **409 `ATTESTATION_NOT_A_WITNESS`** for
  `observed=true` on any `expected_outcome: prevent` row, in any mode (a hand mark cannot
  stand in for the alert witness; reconcile against the tenant); re-asserting `observed=true` on an observed
  `prevented` row only updates the note. Provenance stays readable: an attested row has
  `observed_at` but no matched alert.
- Note the path-param type asymmetry: `/results/{run_id}` takes a **string** run id;
  `/results/{result_id}/validate` takes an **int** PK. Both live under the same
  prefix; FastAPI disambiguates by the trailing segment.

### 1.5 Tools (`core/api/tools.py`, prefix `/tools`)

| Method | Full path | Purpose | Request | Response |
|--------|-----------|---------|---------|----------|
| GET | `/api/tools` | List built-in tools + live+DB-merged runtime status | — | `{"tools":[{tool_name,status,pid,port,install_path,description,plane,type,last_health_check}],"total":N}` |
| GET | `/api/tools/adapters` | Tool-adapter catalog (slim cards) | query: `plane`, `tier`, `safety_class`, `category` (compose AND) | `{"adapters":[<summary>],"total":N}` |
| GET | `/api/tools/adapters/{adapter_id}` | Full adapter pack | — | `adapter.model_dump()`; 404 `ADAPTER_NOT_FOUND` |
| POST | `/api/tools/{tool_name}/install` | Build tool from submodule source | — | `{"status":"installed",...}`; 404 `TOOL_NOT_FOUND`; 409 `INSTALL_IN_PROGRESS` (installs run one at a time, off the event loop); 500 `INSTALL_FAILED` |
| POST | `/api/tools/{tool_name}/start` | Start managed process | `StartParams`: `{params:{}}` — only the `run_template` placeholders the registry leaves open (`mitre_id`, `config_path`) | `{"status":"running","pid",...}`; 400 `BAD_TOOL_PARAM` (a registry key, an unreferenced key, a missing placeholder or a malformed value; nothing is spawned); 404; 500 `START_FAILED` |
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
| GET | `/api/agents/install` | Generate ready-to-run installer script | query: `os=linux\|macos\|darwin\|windows`, `token?`, `id`, `server?`, `interval`, `mode=service\|foreground`, `uninstall=0\|1` | `PlainTextResponse` bash (`.sh`) or PowerShell (`.ps1`); the baked `server`/`id`/`token` are single-quoted literals in both; 400 `BAD_OS`/`BAD_MODE`; 400 `BAD_AGENT_ID` (not 1-120 of `A-Za-z0-9._-`, letter/digit first), `BAD_TOKEN` (not `cxs_` + `A-Za-z0-9_-`), `BAD_SERVER` (not `http(s)://host[:port][/path]`, including one derived from the Host header); 400 `SERVER_NOT_THIS_SIMCORE` (`server` is neither the origin the request arrived on nor an entry in `CORTEXSIM_PUBLIC_URLS`; a matching `server` is baked in canonical form) |
| GET | `/api/agents/binaries` | Inventory the prebuilt beacon shelf | — | `{"binaries":[{os,arch,filename,size_bytes,sha256,modified_at}],"total":N}` |
| GET | `/api/agents/binary` | Download a prebuilt beacon | query: `os`, `arch` (`uname` spellings accepted) | `FileResponse` + `X-CortexSim-Agent-SHA256`; 400 `BAD_OS`/`BAD_ARCH`; 404 `AGENT_BINARY_UNAVAILABLE` |
| GET | `/api/agents/binary/sha256` | Expected digest for a target | query: `os`, `arch` | `PlainTextResponse` hex digest; same 400/404 |
| GET | `/api/agents/manifest` | Kubernetes container-agent manifest (multi-doc YAML) | query: `profile`, `image`, `prepare`, `arch`, `ttl_seconds`, `cluster_privilege_authorized?`, `node_access_authorized?`, `authorized_by?` | `text/yaml`; 400 `BAD_PROFILE`/`BAD_PREPARE`/`BAD_ARCH`/`BAD_TTL`/`BAD_IMAGE`/`BAD_AUTHORIZED_BY`/`AUTHORIZED_BY_REQUIRED`; 403 `CLUSTER_PRIVILEGE_DISABLED`; 409 `CLUSTER_CONSENT_REQUIRED`; 404 `AGENT_BINARY_UNAVAILABLE`. Full reference: `docs/reference/k8s-container-agent.md` |
| POST | `/api/agents/install/telemetry` | Installer reports its terminal stage/code | `InstallTelemetry` (length-capped, control chars stripped) | `{"status":"recorded"}`; also an `agent.install` SSE frame |
| GET | `/api/agents/install/attempts` | Read recent install attempts | query: `limit` (default 25) | `{"attempts":[…],"total":N}` — in-memory deque (100), lost on restart |
| POST | `/api/agents/enroll/tokens` | Mint a TTL / max-uses / revocable enrollment token | `{label?, ttl_seconds?, max_uses?}` (`ttl_seconds` default 3600, `ge=60`, `le=2_592_000`) | token (revealed once) |
| GET | `/api/agents/enroll/tokens` | List token metadata (never the secret) | — | `{"tokens":[…]}` |
| DELETE | `/api/agents/enroll/tokens/{token_id}` | Revoke a token | — | `{"status":"revoked"}` |
| POST | `/api/agents/enroll` | Redeem a token; SimCore **assigns** the agent id | `EnrollRequest`: `{token, hostname, os, capabilities[], desired_name?, runtime_facts?}` | `{"status":"enrolled","agent_id"}`; 4xx `ENROLL_DENIED` |
| GET | `/api/agents` | List registered agents (newest `last_seen` first) | — | `{"agents":[<agent.to_dict()>],"total":N}` |
| POST | `/api/agents/register` | Register/re-register a beacon (idempotent, legacy self-asserted id) | `RegisterRequest`: `{agent_id, hostname, os, capabilities[], runtime_facts?}` | `{"status":"registered","agent_id","message"}` |
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

- **Kubernetes container agent (2026-11-10, authored, not tenant-verified).**
  `GET /api/agents/manifest` serves gated profiles on the GET with consent in the
  query string; the push path's `GET /api/scenarios/{id}/download?format=k8s` still
  returns `409 GATED_FORMAT_REQUIRES_POST` unchanged. `authorized_by` is required
  whenever either consent key is present. The pod enrolls natively with new beacon
  flags `--enroll-token-file`, `--state-dir`, `--posture-profile`,
  `--prepare-status-file`, `--expires-at`, `--subreaper`.
- **`runtime_facts`.** `register` and `enroll` accept an optional `runtime_facts`
  object (`container`, `pod_name`, `pod_namespace`, `node_name`, `posture_profile`,
  `prepare_status`, `tools[]`, `accounts[]`, `anchor_rename`; unknown keys ignored,
  wrong types 422), stored on `agents.runtime_facts` and returned in
  `agent.to_dict()`. Every beacon now sends it (`container: false` off a pod).
  `capabilities` and `interpreters` accept `null` as `[]`.
- **Launch gate for container agents** (`POST /api/runs`): `409
  AGENT_POSTURE_INSUFFICIENT` and `409 AGENT_TOOL_ABSENT` (override with
  `allow_missing_tools: true`, tools only), and `warnings[]` codes
  `AGENT_NO_RBAC_FOR_KUBECTL` and `AGENT_TOOL_ROSTER_UNKNOWN`. Host and VM agents
  are not gated.

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
| POST | `/api/infra/generate` | Render + bundle Terraform tar.gz | `InfraGenerateRequest` (Pydantic): `region` must be an AWS region (`us-east-1`, `us-gov-west-1`), `params.jumpbox_size` an AWS instance type (`t3.medium`, `m7i-flex.large`, ≤32 chars); string values reach the `.tf`/`.tfvars` only as escaped HCL literals | `InfraGenerateResponse` (`bundle_id`, `download_url`); 422 request validation (incl. a bad `region`/`jumpbox_size`); 422 `GENERATION_FAILED` |
| GET | `/api/infra/bundles` | List previously generated bundles | — | `{"bundles":[<summary>],"total":N}` |
| GET | `/api/infra/bundles/{bundle_id}/download` | Download tar.gz | — | `FileResponse` gzip; 404 `BUNDLE_NOT_FOUND` |

- These routes are **sync** (`def`, not `async def`) — they do blocking filesystem
  work (`shutil.copytree`) off the event loop is *not* offloaded (GAP-API-010).

### 1.9 EAL Traffic Simulator (`core/api/eal.py`, prefix `/eal`)

| Method | Full path | Purpose | Request | Response |
|--------|-----------|---------|---------|----------|
| GET | `/api/eal/plugins` | List registered simulator plugins | — | `{"plugins":[...],"total":N}`; each plugin carries `family` (`analytics_log_streamer` \| `network_eal`) and `delivery` (`collector` \| `wire`), the signal-path class the Composer lanes read (`core/engine/signal_path.py`) |
| GET | `/api/eal/plugins/{name}` | Plugin metadata + JSON schema | — | `plugin.metadata()`; 404 `PLUGIN_NOT_FOUND` |
| POST | `/api/eal/campaigns` | Persist a campaign def (validates each step's plugin+params) | `Campaign` (Pydantic) | `<EalCampaign.to_dict()>` (201); 400 `CALLER_EXECUTABLE_REFUSED` (a step sets `params.binary`; a plugin runs only the CLI on SimCore's PATH); 422 `PLUGIN_NOT_FOUND`/`PARAMS_INVALID`; 409 `DUPLICATE_CAMPAIGN` |
| GET | `/api/eal/campaigns` | List campaigns (newest first) | — | `{"campaigns":[...],"total":N}` |
| GET | `/api/eal/campaigns/{campaign_id}` | Single campaign | — | `<EalCampaign.to_dict()>`; 404 `CAMPAIGN_NOT_FOUND` |
| POST | `/api/eal/campaigns/{campaign_id}/launch` | Launch campaign in background (dry-run default); collector steps resolve against the instance's collector targets | `LaunchRequest`: `{dry_run?, operator?, c2_authorized?}` | `LaunchResponse`: `{run_id, campaign_id, status, dry_run, degraded[]}`; 404; 400 `CALLER_EXECUTABLE_REFUSED` (a stored step sets `params.binary`); 422 `SPEC_INVALID`/`SAFETY_VIOLATION`; **live only:** 422 `COLLECTOR_TARGET_NOT_CONFIGURED`/`COLLECTOR_TARGET_UNKNOWN`, 409 `COLLECTOR_TARGET_FAILING` |
| GET | `/api/eal/campaigns/{campaign_id}/collectors` | Resolve every step's collector destination (secret-free) + warnings | — | `{collectors:[{scheme,host,port,path,dataset,auth_header,auth_scheme,has_auth_token,collector_target,resolution_source}],destinations:[…],warnings:[…],resolutions:[…],blocking:[…],failing:[…],degraded:[…]}` |
| POST | `/api/eal/campaigns/{campaign_id}/collectors/preflight` | Canary-POST one discard-safe record per collector | — | per-collector verdict in the delivery taxonomy; non-allowlisted hosts are `target_not_authorised` and **never contacted** |
| POST | `/api/eal/campaigns/{campaign_id}/detector-readiness` | Can this campaign's analytics detectors fire at all? Judges route, then soakless, then onboarding date. **Zero outbound calls** | `{source_onboarded_at?: {<source_key>: <ISO-8601 with tz>}, soakless_attested?, attested_by?, xdm_modeled_sources?: [<source_key>]}` | `{routes{<plugin>: <pipe>}, detectors[] (readiness.armed_basis, readiness.route), counts{armed,not_armed,unknown,not_armable,undocumented}, ready, soakless_attested, attested_by, tenant_verified: 0, gate_basis, banner}`; codes `DETECTOR_ROUTE_NO_ANALYTICS` / `DETECTOR_ROUTE_REQUIRES_XDM` / `DETECTOR_ROUTE_UNRESOLVED` / `DETECTOR_ARMED_SOAKLESS`; 422 `NO_ANALYTICS_DETECTOR` / `ONBOARDING_TIMESTAMP_NAIVE` / `UNKNOWN_DATA_SOURCE` |
| POST | `/api/eal/campaigns/{campaign_id}/bundle` | Pre-render records into a self-contained offline bundle | — | `{bundle_id, download_url, records, skipped_steps[]}` (201) |
| GET | `/api/eal/bundles/{bundle_id}/download` | Download the bundle tar.gz | — | `FileResponse` gzip; 404 |
| GET | `/api/eal/runs` | List EAL runs | query: `campaign_id?` | `{"runs":[...],"total":N}` |
| GET | `/api/eal/runs/{run_id}` | Single EAL run + step results | — | `<EalCampaignRun.to_dict()>` incl. the campaign-level `delivery` rollup and `collector_resolution` (where each collector step went and why, never a key; NULL before collector targets); 404 `RUN_NOT_FOUND` |
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

### 1.9.1 Collector targets (`core/api/collector_targets.py`, prefix `/collector-targets`)

The tenant-side ingestion doors this instance's collector emitters POST to. One
SimCore instance serves one POV, so these are instance configuration, persisted
in its database; each key is in the encrypted vault (`Secret`
`collector-target:<name>`, `type_hint: collector_token`). **No response carries a
key or a key tail** — the operator API is unauthenticated, so that absence is the
guarantee.

| Method | Path | Purpose | Request | Response / errors |
|---|---|---|---|---|
| GET | `/api/collector-targets` | List targets | — | `{targets[], count, default_target, collector_plugins[], unbound_plugins[], status, code}`; zero targets is `status: degraded`, `code: NO_COLLECTOR_TARGETS` |
| GET | `/api/collector-targets/{name}` | One target | — | `{name, kind, url, dataset, auth_header, auth_scheme, plugins[], is_default, has_key, last_check, warnings[]}`; 404 `TARGET_NOT_FOUND` |
| PUT | `/api/collector-targets/{name}` | Create or replace | `{kind, url, dataset?, auth_header?, auth_scheme?, plugins[], is_default?, token?, clear_token?}` — `token` write-only, omitted keeps the stored key | target + `previous_default`; 422 `TARGET_NAME_INVALID` / `TARGET_INVALID` / `TARGET_PLUGIN_NOT_COLLECTOR` / `TARGET_PLUGIN_ALREADY_BOUND`. A change to url, kind, header, scheme or key discards `last_check` |
| DELETE | `/api/collector-targets/{name}` | Remove the target and its key | — | 204; 404 |
| POST | `/api/collector-targets/{name}/preflight` | configured → reachable → accepted | `{write_canary: false}` | `{verdict, code, rungs[{rung,status,code,detail,proves,does_not_prove}], write_canary, post_attempted, records_written, status_code, remediation, warnings[], tenant_verified: 0}`; stored as `last_check` |

`kind` ∈ `xsiam_http_collector` (key required) · `http_forwarder`. Broker VM
targets are not modelled yet. **The reachable rung sends no HTTP request**; only
`write_canary: true` writes, one discard-safe record. Resolution precedence and
the launch gate: `docs/reference/eal-plugin-catalog.md` § Collector targets.

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
`running`. The failure is one guarded UPDATE (the in-flight guard `/complete`
and `/abort` use) that appends its marker in SQL, so a fan-out's other endpoints
keep the `/output` and `/complete` writes they land meanwhile; a run that
already finished keeps its verdict, and only the refusal that failed the run
publishes the terminal `run.status` frame. Without that gate an old beacon would silently drop the unknown
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
| POST | `/api/runs/{run_id}/observations` | Manual batch ingest of alerts a DC exported from the console — no credential, fully offline. Scoped like a pull (`scope_to_host` on the run's host; another host's alert is excluded); the batch is `unscoped` — every pass withheld (`PASS_WITHHELD_UNSCOPED`) — when the run has no resolvable target or any alert names no host. Silence in a manual batch is never a miss. | seeded `Result` rows gain `observed_at` → MTTD; summary carries `host_scope`, and `excluded_other_host` + a `warning` naming the expected host whenever another host's alert was not counted (same shape as `dropped_unparseable_timestamps`) |
| POST | `/api/runs/{run_id}/reconcile?connector=xsiam` | Credential-backed pull for the run's window — a credential of kind `xsiam`, else an `xsiam_tenant` one (one-way: the tenant kind serves alert read-back, kind `xsiam` never serves XQL); `?integration=` names a row of either kind and an unknown name is **404 `INTEGRATION_NOT_FOUND`**, never a fallback | same funnel (`apply_verdicts`) as the manual path; summary carries `host_scope`, `truncated`/`truncation`, `warnings[]`, `measurement`, and `incidents_considered` when a correlation scenario needed the incidents read |
| POST | `/api/runs/{run_id}/verify` | **Tier-2** verification via read-only XQL (see §1.3) | 200 + `pending` + `reason` when no credential |

**Preflight stages**, in order, each with a stable `code` and a `remediation`
naming the consequence *in verdict terms*: `config` → `dns_tls` → `auth` →
`scope_alerts` → `alert_shape` (kind `xsiam`) / `scope_xql` → `datasets`
(opt-in, priced one XQL query per dataset) → `clock` (kind `xsiam_tenant`).
**Every stage runs even when an earlier one degraded**, and a skipped stage is
reported explicitly as `SKIPPED` / `PF_SKIPPED_UNREACHABLE` — an absent stage
would read as "fine". Only an unreachable host short-circuits.

**`response_layer` (2026-11-11)** — the last stage of the `xsiam_tenant` ladder
(after `clock`), enabled by the query parameter `response_layer` (bool, default
`true`; pass `false` to omit it). It answers "is the Palo-Cortex SOC Framework
(`soc-optimization-unified`, pinned to 3.31.4) running on this tenant, and what
would its Auto Triage do to a CortexSim case?" with **read-only** calls, every
one counted in `queries_issued`: installed packs
(`GET /xsoar/contentpacks/metadata/installed`), list names
(`GET /xsoar/lists/names`), the two config lists `SOCFWTenantConfig` and
`SOCFWConfigModes` (`GET /xsoar/lists/download/{name}`, only when the core pack
and both lists are present) and one XQL over the framework's execution dataset
(`timeframe` argument `{"relativeTime": 86400000}`, never XQL time arithmetic).
The stage carries the parsed layer under `extra.response_layer`:
`state` ∈ `ABSENT | PRESENT_UNREAD | PRESENT | UNKNOWN` (`PRESENT_UNREAD` = core pack installed but list content or evidence not readable; `UNKNOWN` = installed packs not read or unrecognised), `framework_version`, `leftover_lists`
(a framework uninstalled with its lists left behind reads `ABSENT`, never
`PRESENT`), `auto_triage`, per-source `sources_used` and `read_at`. All
framework strings and shapes live in `core/connectors/socfw.py`.

`auto_triage` is `{live, window_hours, score_threshold, score_off, live_source,
window_source, threshold_source}`, with provenance **per value**, because most of
these values are not read from the tenant:

| field | values | meaning |
|---|---|---|
| `live_source` | `xql_evidence` · `mode_inferred` · `unknown` | `xql_evidence`: production `auto_triage_closed` rows in the last 24 h. `mode_inferred`: the inferred mode's **shipped** jobs map in `SOCFWConfigModes`; the tenant's actual JOB schedule (which the framework's own `matching_mode` reads) is **not** read |
| `window_source` / `threshold_source` | `tenant_config` · `shipped_default` · `unknown` | `tenant_config`: `SOCFWTenantConfig.triage` (`threshold_source` also covers `score_off`). `shipped_default`: CortexSim's copy of the framework default (6 h / 40); the framework reads the live value from `SOCOptimizationConfig_V3`, which is **not** read |

A layer whose lists were not read claims no window or threshold (`null`, source
`unknown`), not even a default. The OK stage detail, the Readiness row and the
launch advisory print "(inferred from mode, not read)" / "(shipped default, not
read)" / "not read" accordingly. Reading `SOCOptimizationConfig_V3` and the JOB
schedule is deferred to Phase 2.

| code | status | meaning |
|---|---|---|
| `PF_RESPONSE_LAYER_ABSENT` | degraded | core pack not installed (`state: ABSENT`; list names alone never make it `PRESENT`). Every POV tenant is expected to run the framework, so absence degrades preflight |
| `PF_RESPONSE_LAYER_UNREAD` | degraded | installed-packs read refused/failed (`state: UNKNOWN`; neither present nor absent is claimed), or the framework is installed but part of its state (list content, XQL) could not be read (`state: PRESENT_UNREAD`). A refused list read names its HTTP status in `detail` (401/403: the key's role; 404/405: the unverified path); both read `PRESENT_UNREAD`, never `ABSENT`. An XQL failure with the lists read degrades the stage while the layer stays `PRESENT` |
| `PF_RESPONSE_LAYER_SHAPE_UNRECOGNIZED` | degraded | a reply arrived in a shape `socfw.py` does not recognise. It raises internally and surfaces here with `state: UNKNOWN`; it is never read as defaults, `open` or `ABSENT` |

A stage skipped because an earlier stage blocked the ladder is reported
`SKIPPED` and **does not** overwrite the last stored read. A stage that read
something is persisted on the integration row (`IntegrationCredential.
response_layer` and `response_layer_read_at`) **together with the stage's own
verdict** under `response_layer.stage` = `{status, code, detail}`, so a degraded
stage behind a `PRESENT` layer is never stored as healthy. The stage then carries
`stored: true`; if the store fails, preflight still returns 200 with the report,
`stored: false` and a `stored_note` (the previous stored read, if any, stays in
place). Re-registering the integration (`PUT /api/credentials/integrations`, new
key or URL) clears the stored read. It is surfaced by `GET /api/readiness`
(§1.16) and the launch advisories on `POST /api/runs` (`warnings[]`, each
`{code, level, tenant, detail, remediation}`; never a refusal, no tenant call).
`SOCFW_TRIAGE_MAY_SEAL` fires when `auto_triage.live` is true in `PRESENT` **or**
`PRESENT_UNREAD` (execution evidence proves triage live even when the lists are
unreadable), at `warn` on a paired run and `info` for an ABIOC/Analytics
scenario; `SOCFW_RESPONSE_LAYER_UNKNOWN` takes the same level. A tenant with no
stored probe reads "no SOC Framework probe is stored for this tenant — run
preflight". The console holds the launcher only on a warning that is not
`level: "info"`. Runbook: `docs/lab/2026-11-11-run-alongside-soc-framework.md`.
**Open:** the list-content path `GET /xsoar/lists/download/{name}` is
**unverified against a live tenant** (no documented source; povkit's tenant
client has `ListNames` and `DeleteList` only). The three reads are catalogued as
`OP-SOCFW-PACKS-INSTALLED`, `OP-SOCFW-LIST-NAMES` (both `povkit-observed`) and
`OP-SOCFW-LIST-GET` (`path-unverified`, and its `{name}` is restricted by
`path_param_allowlist` to `SOCFWTenantConfig` and `SOCFWConfigModes`: the executor
refuses any other list with 400 `XSIAM_CONFIG_ERROR` before a tenant call, because
XSOAR lists can hold integration config and secrets)
(`core/integrations/xsiam/operations/packs/socfw.yml`, 151 operations; the
operation schema now accepts the `/xsoar/` gateway prefix). Tenant disposition
is context, not detection evidence, and `tenant-verified` stays 0.

**`alert_shape` (2026-09-20)** answers "will matching work on *this* tenant's
alert objects?" — the question a POV used to answer with zero matches. It
reuses the scope probe's alert or issues one more 1-row call over a 7-day
lookback (so an empty tenant costs `queries_issued: 3` with the alternate-path probe below), then reports under
`keys` which of the field names the reconcile connector reads are present by
role (`timestamp · technique · name · host · source · rule_id · action`), the alert
`source` value, the tenant `total_count`, and `keys_seen`. The `action` role
(`action_pretty` / `action`, `core/connectors/preflight.py`) is what a prevention outcome
is read from. Codes:
`PF_ALERT_SHAPE_PARTIAL` (degraded: no `action` key — detection matching is unaffected but
a `prevent` expectation can never be credited as prevented — or no rule-id key, in which
case matching rests on technique/name), `PF_ALERT_SHAPE_UNMATCHABLE` (blocked: neither technique nor
name — reconcile could only ever read 0 %, and that 0 % would be wrong),
`PF_ALERT_SHAPE_UNKNOWN` (degraded: no alert in 7 days, shape unverified). A
tenant with no alert to inspect is therefore **degraded, not ready**.

**Issues API field names (2026-11-10).** The connector and this rung also read the names
the public Issues API documents (`POST /public_api/v1/issue/search`): `detection.rule_id`
(rule id), `detection.method` (source), `case_ids` (incident) and `_insert_time`
(timestamp). Each comes after the alerts-API keys of its role, so a record carrying both
keeps its old value. `detection.*` is read as the flat dotted property the schema names,
then as a nested `detection` object. `case_ids` counts only when it lists exactly one case.
`_insert_time` dates a record only when it carries no `detection_timestamp`,
`creation_time` or `local_insert_ts`; which timestamp MTTD should use is still open. Both
sides read through one function, `connectors/xsiam.py::alert_key`, and
`tests/connectors/test_issues_api_fields.py` fails if `ALERT_SHAPE_KEYS` lists a key the
connector does not read. The matcher maps the `detection.method` codes `ANALYTICS_BIOC`,
`BIOC`, `IOC`, `CORRELATION`, `MAGNIFIER` and `TRAPS` onto `abioc`, `bioc`, `ioc`,
`correlation`, `analytics` and `prevention`. TRAPS (XDR Agent) and MAGNIFIER (XDR
Analytics) are inferred from the order of the docs' two value lists. Every other code
names no family and stays unconstrained.

**Alerts-path probe (2026-11-02).** The connector reads
`/public_api/v1/alerts/get_alerts_multi_events` unless the credential's `alerts_path` says
otherwise; a field-built MCP server reads the same call on **v2** against a live tenant.
When the configured path is v1 or v2 and either (a) errors with a path-shaped status
(400 · 404 · 405 · 410) or (b) returns no alert over the whole 7-day lookback, preflight
spends **one** more 1-row query on the other version. If that one answers (a) or has alerts
(b), the stage is `BLOCKED` / `PF_ALERTS_PATH_MISMATCH` with `configured_path`,
`working_path` and a remediation naming the exact `alerts_path` value to set. Case (b) is
the quiet one: reconcile would "work", match nothing and score every seeded detection
`missed`, so `read_alerts` is withdrawn from `capabilities_confirmed`. 401 · 403 · 429 · 5xx
never trigger the probe (they are about the key, the role, the quota or the server, not
the path), nor does a custom `alerts_path`. Preflight never changes the path itself.

`queries_issued` is in every response **on purpose**: a preflight driven by an
injected transport reports `0`, and the `proves` string says so verbatim, so a
mocked green can never be quoted as "connection validated".

**What the reconcile loop measures (2026-09-21, sprint 2).** Beyond MTTD the
loop now produces two values `verifier.score_run` accepts as `measured_value`,
recorded under `tc_verdict_detail.measurement` with `family`, `basis`, `value`,
`scored` and, when the guard held it back, `withheld`:

| scenario `primary_kpi` (unit `%`) | value | basis |
|---|---|---|
| Detection Accuracy | `observed / seeded` over results that executed | `reconcile` or `manual` |
| Cross-Source Correlation Rate · Stitch Completeness · Correlation Coverage | `(alerts − incidents) / (alerts − 1)` | `alert_incident_ids` when the tenant stamps `incident_id`/`case_id` on alerts, else `host_incidents` from one `get_incidents` read scoped to the run's host |

The guard: an **unscoped** pull withholds a PASS, a **truncated** pull
withholds a FAIL, **zero matched** stays `pending`, and fewer than two alerts
cannot yield a correlation rate. A re-score with no new evidence keeps the last
measured value.

**Credential config keys the `xsiam` connector reads:** `base_url` |
`fqdn` | `tenant_url`, `api_key_id` | `auth_id`, `auth_mode`
(`standard`|`advanced`), `max_pages` (default 20 × 100 alerts), `alerts_path`
(default `/public_api/v1/alerts/get_alerts_multi_events`), `incidents_path`
(default `/public_api/v1/incidents/get_incidents`).

> **Wire caveat (open as of 2026-08-06).** The console's Readiness surface calls
> `POST /api/xsiam/tenants/{name}/preflight` for `kind: xsiam_tenant`. **That
> route does not exist** — it returns **405**, which the client's 404-only
> fallback does not catch, so the DC sees `preflight failed: Method Not Allowed`.
> The working route for both kinds is `POST /api/connectors/{kind}/preflight`.
> See `ui/src/components/console/ReadinessView.jsx::TenantRow.runPreflight`.

### 1.16 Dataset prerequisites (`core/api/prerequisites.py`, mounted before the scenarios router)

Added 2026-11-10 (POV content pack, phase 1). Read-only: local files and the
scenario table only, no outbound call; the scenario route carries
`tenant_verified: 0`. Full reference:
[`dataset-requirements.md`](dataset-requirements.md).

| Method | Full path | Purpose | Request | Response |
|--------|-----------|---------|---------|----------|
| GET | `/api/prerequisites/datasets` | The dataset requirement registry joined to the cards that read each dataset | — | `{registry_loaded, path, count, datasets[], unregistered[]}`, each row with `cards[]` and `read_by[]`; 500 `DATASET_REGISTRY_MALFORMED`, 500 `CARD_SHAPE_UNRECOGNISED` |
| GET | `/api/scenarios/{scenario_id}/prerequisites` | What one scenario needs on the Cortex and source side, derived from the cards it cites | — | `{scenario_id, registry_loaded, tenant_verified, write_path, cards[], datasets[], summary, notes[]}`, each dataset row with `read_by[]`; 404 `SCENARIO_NOT_FOUND`, 500 `DATASET_REGISTRY_MALFORMED`, 500 `CARD_SHAPE_UNRECOGNISED` |
| GET | `/api/scenarios/{scenario_id}/query-library` | The cited cards' queries as paste-ready text | query: `purpose` (comma-separated from `hunt`, `investigation`, `validation`, `metric`; default `hunt,investigation`) | `text/plain; charset=utf-8`, non-query lines as `//` comments; 422 `PURPOSE_UNRECOGNISED` (checked first), 404 `SCENARIO_NOT_FOUND`, 500 `CARD_SHAPE_UNRECOGNISED` |

- **Error shape differs from the older scenario routes.** These three return the
  error object at the top level, `{"error", "code", "detail"}`. The scenario
  routes in section 1.2 nest it under `"detail"`.
- An absent registry answers 200 with `registry_loaded: false` (and a
  `REGISTRY_ABSENT` note on the scenario route). A registry that exists but is
  malformed answers a structured 500.
- A card reads a dataset through `xql_queries[].dataset` or a `dataset =` /
  `preset =` anchor in `biocs[]` / `abiocs[]` / `correlation_rules[]` logic;
  `read_by` lists which of those four kinds did.
- Derivation note codes: `NO_CARD_BINDING`, `DETECTION_UNBOUND`, `CARD_NOT_FOUND`,
  `CARD_DECLARES_NO_QUERY`, `DATASET_UNDECLARED`, `UNREGISTERED_DATASET`,
  `REGISTRY_ABSENT`. Row states are `confirmed in a tenant` and `from
  documentation`; the response field for emulation is `can_be_emulated_by`.
- `/api/health` gains a `dataset_requirements` component (degraded codes
  `DATASET_REGISTRY_ABSENT`, `DATASET_REGISTRY_MALFORMED`, `DATASETS_UNREGISTERED`,
  `CARD_SHAPE_UNRECOGNISED`). Boot refuses an absent-or-malformed registry or an
  unregistered card dataset only when `CORTEXSIM_STRICT_DATASETS=true`
  (default `false`).
- Route count: this branch adds 3 routes.

### 1.16b Ingestion doors (`core/api/doors.py`, mounted before the scenarios router)

Added 2026-11-11. Read-only: local files and the database only, **zero outbound
calls**; every response carries `tenant_verified: 0` and `outbound_calls: 0`.
Errors use the top-level `{"error", "code", "detail"}` shape.

| Method | Full path | Purpose | Request | Response |
|--------|-----------|---------|---------|----------|
| GET | `/api/doors` | Every ingestion door (one row per agent or target instance for scoped doors) with its state and the count of corpus detections that name it | — | `{doors[], registry_loaded, tenant_verified, outbound_calls}`; each row `{key, door_id, label, scope, state, basis, rung, last_conclusive_at, age_seconds, wizard, detections_needing}`; 500 `DATASET_REGISTRY_MALFORMED`, 500 `DOOR_DERIVATION_FAILED` |
| GET | `/api/doors/readiness` | Door readiness of every loaded scenario in one fetch (the library list itself is unchanged) | query: `agent_id` (keys agent-scoped doors; without it `agent_scoped_evaluated` is false) | `{scenarios{scenario_id: readiness}, agent_scoped_evaluated, registry_loaded, tenant_verified, outbound_calls}`; 500 as above |
| GET | `/api/scenarios/{scenario_id}/doors` | One scenario's readiness plus each expected detection's requirement | query: `agent_id` | `{scenario_id, registry_loaded, readiness, detections[{step_id, detection_id, requirement}], tenant_verified, outbound_calls}`; 404 `SCENARIO_NOT_FOUND`, 500 as above |

- A readiness object is `{scenario_id, state, scorable, total, underived, route_blocked,
  unevaluated, not_open[{door_id, detections}], agent_scoped_evaluated}`. `state` is
  `ready | partial | needs_doors | not_evaluated`. `route_blocked` counts detections whose
  collector route cannot feed them (the library resolves no collector target, so every
  collector-route detection counts here); `unevaluated` counts agent-scoped OR-sets with no
  `agent_id` to key them. Both are passed through unchanged from the resolver.
- `not_open` lists each door that is **not in the open set** and how many detections it
  holds back, most first. It does **not** distinguish a door checked and found closed from
  one nobody has checked; join each `door_id` against `GET /api/doors` for the door's real
  `state`. A detection held back by two doors counts once under each, so the per-door counts
  can sum to more than the detections held back.
- A door row `state` is the last conclusive event (`open | closed`), else `unknown`; the
  `rung` axis separately carries `configured` when a zero-call fact says so (else `authored`).
  Phase 1 writes no events, so nothing reads `open` and no scenario reads `ready`.
- `detections_needing` counts a detection once for each door in its OR-sets, so the per-door
  counts can sum to more than the number of detections. Composer drafts (`status: draft`) are
  excluded. Every collector-route detection counts under `http_collector`, including the
  route-blocked ones its route cannot feed.
- An agent-scoped door (`xdr_agent`, `xdr_agent_xth`, `ngfw_stitch`) reads rung `configured`
  for a beacon only when the beacon is online **and** an `xsiam_tenant` credential with XQL
  granted exists (`ngfw_stitch` also needs a registered firewall); an online beacon alone is
  the CortexSim beacon, not the Cortex XDR agent.
- An absent dataset requirement registry answers 200 with `registry_loaded: false`:
  `detections_needing` is `null` and every scenario reads `not_evaluated` with
  `reason: REGISTRY_ABSENT`; the per-scenario route returns `registry_loaded: false`,
  `code`/`reason: REGISTRY_ABSENT`, `detections: []` and a `not_evaluated` readiness. A malformed one is a structured 500.

### 1.17 NGFW (PAN-OS) connection (`core/api/ngfw.py`, prefix `/ngfw`; preflight through `/connectors`)

Added 2026-11-10 (branch `feature/api-ngfw-connection`). Spec:
[`2026-11-10-ngfw-connection-wizard-design.md`](../superpowers/specs/2026-11-10-ngfw-connection-wizard-design.md)
(§12 lists where the build departs from it). Console: **Firewall** under *Manage*
(`#/firewall`, `ui/src/components/console/FirewallView.jsx` + `firewall/*`).

**Opt-in and read-only toward the firewall.** Nothing here is polled; every call to a
firewall is an operator action. `core/integrations/panos/client.py::PanosClient` builds
three request types only: `op` (each command parsed and checked against a per-command
argument schema in `OP_ALLOWLIST`, then sent as the client's own canonical
serialisation, never the caller's string), `config` with `action=show` (running config,
xpath must fully match one of the anchored regexes in `CONFIG_XPATH_ALLOWLIST`), and
`keygen`. A refused request raises `PanosWriteRefused` before anything is sent and
consumes no call budget. `CORTEXSIM_PANOS_ALLOW_WRITE` exists in `core/config.py`
(default `false`) and **nothing consumes it**: v1 has no write path, and
`GET /api/ngfw` reports `write_enabled: false` as a constant. The API key travels in the
`X-PAN-KEY` header, never the URL, and is scrubbed from every string in a report.

| Method | Full path | Purpose | Request | Response |
|--------|-----------|---------|---------|----------|
| GET | `/api/ngfw` | Registered firewalls, metadata only (never the key) | — | `{firewalls: [{name, host, verify_tls, vsys, forwarding_path, last_verified_ok, last_verified_error, last_verified_at}], write_enabled: false, tenant_verified: 0}` |
| POST | `/api/ngfw/keygen` | Exchange a firewall admin login for an API key and register the firewall (kind `panos_ngfw`) | JSON `{name, host, username, password, verify_tls?=true, vsys?="vsys1"}`, extra keys rejected, body ≤ 16 KiB | `<integration.to_dict()>` + `overrides_cleared` + `warnings[]` (`"password sent over unverified TLS"` when `verify_tls=false`); errors below |
| PUT | `/api/ngfw/{name}/sources/{agent_id}` | Set or clear one agent's firewall-observed source / agent-side addresses on one firewall | JSON `{observed_source?: ip \| null, agent_addresses?: [ip] \| null}`; an omitted field is unchanged, `null` / `[]` removes the override | `{firewall, agent_id, observed_source, agent_addresses}`; 404 `INTEGRATION_NOT_FOUND`, 400 `INTEGRATION_CONFIG_INVALID`, 422 `REQUEST_INVALID`, 413 `REQUEST_TOO_LARGE` |
| PUT | `/api/credentials/integrations` | Existing route; with `kind: panos_ngfw` it registers a pasted API key | `IntegrationPut` with `config` per the table below | `<integration.to_dict()>` + `overrides_cleared`; 400 `INTEGRATION_CONFIG_INVALID`; 409 `INTEGRATION_KIND_CONFLICT` (the name belongs to another kind; nothing written) |
| POST | `/api/connectors/panos_ngfw/preflight` | Staged read-only firewall preflight (dispatched at the top of the connectors route, before its `PF_` resolution) | query `integration?` (default: first by name), `agents?` (comma-separated agent ids; default every enrolled agent) | `FirewallPreflightReport` (below); `PN_NO_INTEGRATION` / `PN_CREDENTIAL_UNDECRYPTABLE` arrive as a 200 report with a blocked `config` stage; 404 `INTEGRATION_NOT_FOUND` for an unknown `integration` |

**`panos_ngfw` config** (validated at PUT and keygen time by
`core/integrations/panos/host.py::validate_panos_config`, so a key is never sent to a
malformed host): `host` (IP literal or DNS name, optional `:port`, no scheme, path,
query or userinfo; required) · `verify_tls` (bool, `true`) · `vsys` (`vsys<digits>`,
`vsys1`) · `device_name` (`localhost.localdomain`) · `routing_instance` (`default`) ·
`forwarding_path` (`auto` \| `sls` \| `syslog` \| `both`, `auto`) · `probe_destinations`
(`[{ip, port, protocol: tcp|udp, application}]`, default `1.1.1.1 udp/53 dns-base`,
`198.51.100.10 tcp/80 web-browsing`, `198.51.100.10 tcp/443 ssl`; policy lookups only,
no packet is sent) · `inside_zone` / `outside_zone` (null) · `observed_sources`
(`{agent_id: ip}`) · `agent_addresses` (`{agent_id: [ip]}`). The secret type hint is
forced to `panos_api_key_credential`.

**Re-registration rule.** A keygen or `PUT /api/credentials/integrations` for an
existing `panos_ngfw` name keeps `probe_destinations`, the zones, `device_name` and
`routing_instance` (and, on the PUT, `vsys` / `forwarding_path`), each only when the
caller did not send it, since a sent key always wins; it keeps
`observed_sources` / `agent_addresses` only when the normalised `host` is unchanged and
otherwise clears them and returns `overrides_cleared: true`. Per-agent overrides describe
the path through one firewall; carried to another they give confident, wrong verdicts.

**Keygen errors.** The body is parsed in the handler, not by FastAPI binding, because
FastAPI's default 422 echoed the password in `input` even with `hide_input_in_errors`. A
422 here is `{error, code: "REQUEST_INVALID", detail, fields: [{loc, type}]}` and a `loc`
part that is not a model field name reads `"(other)"`. Failures map to fixed text:
401 `PN_KEYGEN_REJECTED`; 502 `PN_UNREACHABLE`, `PN_TLS_FAILED`, `PN_UPSTREAM_ERROR`,
`PN_SHAPE_UNRECOGNISED`; 409 `INTEGRATION_KIND_CONFLICT` (the name belongs to another
kind; checked before anything is sent); 400 `INTEGRATION_CONFIG_INVALID`; 413
`REQUEST_TOO_LARGE`. The password is never logged, stored or returned. Generating a key
does not invalidate the admin's earlier keys (PAN-OS 9.0+).

**Threat note.** Keygen and preflight are unauthenticated, like the rest of
`/api/credentials`. Anyone who reaches SimCore can use keygen as a reachability and port
oracle toward internal hosts (the four distinct 502 codes, or a 401). No stored secret
leaks through it: keygen holds no stored key, and preflight sends a stored key only to
that integration's own validated host.

**`FirewallPreflightReport`.** `{firewall, kind: "panos_ngfw", mgmt_host, overall,
stages[], sources[], queries_issued, panos_version, config_source: "running", proves,
tenant_verified: 0, credential: {name, kind}}`. Each stage is `{id, status, code,
detail, remediation?, ms?, …extra}` with status `ok | degraded | blocked | skipped`;
`overall` is the worst. `queries_issued` is the client's own count of requests sent, so a
report from an injected transport says `0` and `proves` reads *"Firewall configuration
as read by N live call(s). It does not show that the tenant ingested or stitched
anything. A report with queries_issued=0 was not answered by a firewall."* The verdict is
stored in the integration's `last_verified_ok` (`overall != blocked`) and
`last_verified_error` (first blocked code).

Firewall-wide stages, every id exactly once and in order: `config` · `dns_tls` · `auth` ·
`system` · `clock` · `forwarding_sls` · `forwarding_syslog` · `eal_device` ·
`log_forwarding_profiles` · `decryption` · `registered_ip`. Only `PN_UNREACHABLE`,
`PN_TLS_FAILED` and `PN_AUTH_REJECTED` short-circuit the rest (to `skipped /
PN_SKIPPED_UNREACHABLE | PN_SKIPPED_AUTH`); any other failure blocks its own stage.

`sources[]` has one entry per agent: `{agent_id, hostname, fw_observed_src,
fw_src_provenance (entered | inferred:last_ip | unknown), agent_addresses,
agent_addr_provenance, nat_verdict (tuple_intact | nat_between | unknown),
collision_with[], status, stages[], probes[]}`, with stages `address` · `source_seen` ·
`policy_match` · `rule_profiles` · `nat` · `user_id` · `registered_tags`. With no agent
selected it is a single degraded `PN_NO_AGENTS` entry, never an empty list. Each
`?agents=` id that matches no enrolled agent is its own degraded `PN_AGENT_UNKNOWN`
entry `{code, status, requested_agent_id, detail, remediation}` (no `agent_id`: it is
not an agent row); the known ids are checked as usual, and when none is known these
entries replace `PN_NO_AGENTS`, so a typo never reads as "nothing selected". A green
firewall preflight proves firewall configuration, **not** tenant ingestion or stitching:
***tenant-verified is 0***.

Call budget: at most `CORTEXSIM_PANOS_MAX_CALLS_PER_PREFLIGHT` (default 120) requests;
past it, the remaining stages report `PN_CALL_BUDGET` and work done before the cap is
kept. Timeout 10 s per call.

**Every `PN_` code** (`core/integrations/panos/codes.py`, one vocabulary, value equals
name). Status is what the code carries where it is emitted.

| code | status | meaning |
|---|---|---|
| `PN_OK` | ok | the stage's check passed |
| `PN_CONFIG_INVALID` | blocked (`config`) | the stored registration does not validate; nothing was read |
| `PN_NO_INTEGRATION` | blocked (`config`) | no `panos_ngfw` integration is registered |
| `PN_CREDENTIAL_UNDECRYPTABLE` | blocked (`config`) | the stored API key cannot be decrypted (rotated `CORTEXSIM_SECRET`) |
| `PN_SKIPPED_CONFIG` | skipped | not checked because the `config` stage is blocked |
| `PN_UNREACHABLE` | blocked (`dns_tls`); keygen 502 | no answer from the management host (DNS, routing, port, HTTPS off) |
| `PN_TLS_FAILED` | blocked (`dns_tls`); keygen 502 | the management certificate did not verify |
| `PN_TLS_UNVERIFIED` | degraded (`dns_tls`) | answered with `verify_tls=false`; the peer is not proven |
| `PN_AUTH_REJECTED` | blocked (`auth`) | the firewall rejected the API key (HTTP 403 or `status="error"` code 403) |
| `PN_KEYGEN_REJECTED` | keygen 401 | the firewall issued no key for the submitted login |
| `PN_SKIPPED_UNREACHABLE` | skipped | not checked: the firewall stopped answering (unreachable or TLS failure) |
| `PN_SKIPPED_AUTH` | skipped | not checked: the firewall rejected the key |
| `PN_UPSTREAM_ERROR` | blocked (that stage only); keygen 502 | the management API answered with a non-2xx HTTP status |
| `PN_SHAPE_UNRECOGNISED` | blocked (that stage only); keygen 502 | a reply this version does not recognise (the command or xpath is named), or a well-formed PAN-OS `status="error"` reply other than an auth rejection (code 403 is `PN_AUTH_REJECTED`; at keygen every `status="error"` is `PN_KEYGEN_REJECTED`); never read as empty |
| `PN_INTERNAL_ERROR` | blocked (that stage only) | a CortexSim defect while evaluating the stage; exception type named, message withheld |
| `PN_WRITE_REFUSED` | blocked | the read-only client refused a request the stage built; nothing was sent (a CortexSim defect) |
| `PN_CALL_BUDGET` | blocked (firewall-wide) / skipped (per source) | `CORTEXSIM_PANOS_MAX_CALLS_PER_PREFLIGHT` reached before this check |
| `PN_MULTI_VSYS` | degraded (`system`) | multi-vsys is on; only the configured vsys was checked |
| `PN_NOT_NORMAL_MODE` | degraded (`system`) | operational mode is not normal (e.g. FIPS-CC) |
| `PN_CLOCK_SKEW` | degraded (`clock`) | firewall clock differs from SimCore UTC by more than 30 s |
| `PN_CLOCK_TZ_UNKNOWN` | degraded (`clock`) | the firewall clock could not be converted to UTC (no or unknown timezone) |
| `PN_LSF_DISABLED` | blocked (`forwarding_sls`) | Logging Service forwarding is off but the selected path needs it |
| `PN_COLLECTOR_INACTIVE` | blocked (`forwarding_sls`) | LSF is on but no log collector connection is Active |
| `PN_LOG_TYPE_STALE` | degraded (`forwarding_sls`) | traffic or threat last forwarded more than 15 min ago |
| `PN_LOG_TYPE_NEVER` | degraded (`forwarding_sls`) | traffic or threat has never been forwarded |
| `PN_LOG_TYPE_AGE_UNKNOWN` | degraded (`forwarding_sls`) | the last-forwarded age cannot be computed (clock unread, or a time in the future) |
| `PN_SYSLOG_NO_SERVERS` | blocked (`forwarding_syslog`) | no syslog server profile has a server, and syslog is required |
| `PN_SYSLOG_DELIVERY_UNVERIFIED` | degraded (`forwarding_syslog`) | syslog is configured; PAN-OS keeps no delivery counters, so confirm on the tenant side |
| `PN_SYSLOG_UDP` | degraded (`forwarding_syslog`) | a syslog server uses UDP (loss is silent) |
| `PN_NO_FORWARDING_PATH` | blocked (`forwarding_sls`, `forwarding_syslog`) / degraded (`log_forwarding_profiles`) | neither LSF nor syslog is configured, so no profile can be shown to deliver |
| `PN_PATH_NOT_SELECTED` | ok (unconfigured, another path proven; or configured but not the selected `forwarding_path`, with `extra.would_report {status, code}` saying what it would have read) | this forwarding path is not the one in use; information only, so it never keeps the overall from `ready` |
| `PN_EAL_DEVICE_OFF` | degraded (`eal_device`) | Enhanced Application Logging is off on the device |
| `PN_NO_LFP` | blocked (`log_forwarding_profiles`) | no log forwarding profile exists |
| `PN_LFP_MISSING_TYPE` | degraded (`log_forwarding_profiles`, as are the next four) | no profile forwards all of traffic, threat, url, wildfire and data |
| `PN_LFP_EAL_OFF` | degraded | a profile has Enhanced Application Logging off |
| `PN_LFP_TAG_ACTION` | degraded | a profile has a tagging action that can move a test host mid-run |
| `PN_LFP_WRONG_TARGET` | degraded | a log type is forwarded only on a path that is not in use |
| `PN_LFP_FILTERED` | degraded | a log type is forwarded only through a filter narrower than "All Logs" |
| `PN_DECRYPTION_OFF` | degraded (`decryption`) | no enabled decryption rule |
| `PN_REGISTERED_TAGS_PRESENT` | degraded (`registered_ip`) | registered IP tags exist on the firewall |
| `PN_NO_AGENTS` | degraded (`sources[]`) | no enrolled agent selected; no source path was checked |
| `PN_AGENT_UNKNOWN` | degraded (`sources[]`) | a requested `?agents=` id matches no enrolled agent; nothing was checked for it (`requested_agent_id` names it) |
| `PN_SOURCE_UNKNOWN` | degraded (`address`); skipped (that source's network stages) | the firewall-observed source is unknown; nothing was sent for it |
| `PN_SOURCE_COLLISION` | blocked (`address`) | two or more agents resolve to the same firewall-observed source |
| `PN_NAT_BETWEEN_AGENT_AND_FW` | blocked (`address`) | agent-side and firewall-observed addresses are disjoint: NAT breaks the 5-tuple stitch |
| `PN_AGENT_ADDRESS_UNKNOWN` | degraded (`address`) | the agent-side address is unknown, so the NAT verdict is unknown |
| `PN_SOURCE_NOT_SEEN` | degraded (`source_seen`) | the firewall has no current session from this source |
| `PN_ZONE_UNRESOLVED` | degraded (`policy_match`, `user_id`) / skipped (`nat`) | source or destination zone could not be resolved |
| `PN_NO_RULE_MATCH` | degraded (`policy_match`) | no configured security rule matches the probe (default rule, no log) |
| `PN_PROBE_FAMILY_MISMATCH` | degraded / skipped | the source and every probe are of different address families |
| `PN_RULE_DENIES` | blocked (`rule_profiles`) | the matching rule does not allow the traffic |
| `PN_RULE_CONFIG_MISMATCH` | degraded (`rule_profiles`) | the policy lookup's rule disagrees with running config (disabled, or another action) |
| `PN_NO_PROFILE` | degraded (`rule_profiles`) | an allow with no inspection or logging for some threat types or severities |
| `PN_PROFILE_BLOCKS` | degraded (`rule_profiles`) | a profile blocks some threats: threat log written, artefact never reaches the endpoint |
| `PN_PROFILE_SIGNATURE_DEFAULT` | degraded (`rule_profiles`) | a profile action is `default` (defers to the signature; likely blocks critical/high) |
| `PN_DNS_SEC_SINKHOLE` | degraded (`rule_profiles`) | DNS Security sinkholes some categories on this path |
| `PN_DNS_SEC_UNCONFIGURED` | degraded (`rule_profiles`) | the spyware profile configures no DNS Security category |
| `PN_RULE_NO_LOG_FORWARDING` | degraded (`rule_profiles`) | the matching rule has no log forwarding profile |
| `PN_RULE_LOG_PROFILE_INCOMPLETE` | degraded (`rule_profiles`) | the rule's log forwarding profile was not complete in `log_forwarding_profiles` |
| `PN_RULE_NO_LOG_END` | degraded (`rule_profiles`) | the rule does not log at session end: no traffic record to stitch |
| `PN_OBJECT_NOT_FOUND` | degraded | a referenced rule, profile, group or zone is in no scope read and is not predefined |
| `PN_NO_USER_MAPPING` | degraded (`user_id`) | no user mapping for this source |
| `PN_ZONE_USERID_OFF` | degraded (`user_id`) | User-ID is off on the source's zone |
| `PN_SOURCE_TAGGED` | degraded (`registered_tags`) | this source carries registered IP tags |

URL-filtering and WildFire-analysis profile actions are **not evaluated** in v1 (reported
as such, no code raised).

- Route count: this branch adds 3 routes (`/api/ngfw` ×3; the preflight reuses
  `/api/connectors/{kind}/preflight`).

**Still undocumented in this table:** `/api/xsiam` (the ~116-operation read-only
Cortex operation catalog) and the `/api/runs/*` storyline + causality routes.
Both are covered by their own design docs.

### 1.16 Readiness (`core/api/readiness.py`, prefix `/readiness`)

| method | path | purpose | notable response |
|---|---|---|---|
| GET | `/api/readiness` | The connector ladder, stated server-side: the same four rungs (AUTHORED · CONFIGURED · REACHABLE · VERIFIED) and `tenant-verified: N` the console's Readiness page renders, for callers that are not the console | `{server_time, tenant_verified, tenant_verified_names[], integrations[], proves, outbound_calls, statement}` |

**Zero outbound calls**, stated in the body as `outbound_calls: 0`: every field
is read from the local DB, the vault's metadata or the in-process quota ledger.
It never returns credential config. Per integration it carries `name`, `kind`,
`capabilities` (`null` when the credential declares none), `xql_capability_granted`,
`last_verified_ok` / `last_verified_at` / `last_verified_error`, a `ledger`
snapshot (kind `xsiam_tenant` with a `base_url` only, otherwise `null`), a
`response_layer` block (`null` until a preflight has stored one; otherwise
`{layer, state, stage, read_at, stale, proves, does_not_prove}`, where `stage` is
the stored stage verdict `{status, code, detail}` (`null` when none was stored,
which the console reads as unknown, never healthy), `stale` is true past 24 h or
when the timestamp is unreadable, and `does_not_prove` says the framework handled
no CortexSim case) and the four `rungs` booleans.
`GET /api/health` lists `socfw_response_layer` in `not_checked[]`, naming the
preflight stage that does check it. `tenant_verified` is counted in Python by
`core/connectors/provenance.py::tenant_verified` — runs and assertion runs whose
verdict detail carries tenant provenance; `pending` and `not_applicable` do not
count. **Authored is not proven:** today that count is 0 because nothing has run
against a live tenant, and the `statement` field says so
(`No tenant call is made by this route. tenant-verified: N`).

**Changes to existing routes on the MCP branch** (all additive unless noted):

| route | change |
|---|---|
| `GET /api/runs` | accepts `status` and `scenario_id` (equality filters) and `limit` (1..500, out of range is 422); `total` counts the filtered rows before `limit`; no parameters is unchanged |
| `POST /api/xsiam/tenants/{name}/operations/{op_id}`, `POST …/xql`, `GET …/xql/{query_id}`, `GET …/metrics` | reply carries `queries_issued` and a `ledger` snapshot (`null` on a preview). The executor reports 1 for a live op (XQL or not) **except** the result polls `OP-XQL-GET-QUERY-RESULTS` / `-STREAM`, which report 0, and 0 for a preview; `POST …/xql` and `GET …/metrics` report 1; `GET …/xql/{query_id}` reports 0. Refusals come in a fixed order and the tenant is not called when any refuses, but the order differs by path: the executor's `OP-XQL-START-XQL-QUERY` — capability (403), request shape (`XQL_REQUEST_SHAPE_INVALID`, 400), write stage (`XQL_WRITE_STAGE_REFUSED`, 400), per-tenant budget (`XSIAM_QUOTA_ERROR`, 429); `POST …/xql` — a malformed body is a pydantic 422 before the handler runs (not `XQL_REQUEST_SHAPE_INVALID`), then capability, write stage, budget; `GET …/metrics` — capability, budget; the polls and `OP-XQL-GET-QUOTA` — capability only, never budgeted. `GET …/xql/{query_id}` gains `limit` (1..1000) |
| same XQL-class routes | **behaviour change:** a credential whose `config.capabilities` does not list `"xql"` is refused with 403 `XSIAM_CAPABILITY_MISSING`. Preflight and the verify/reconcile runner now agree: an **absent** `capabilities` field means *not granted* (preflight used to authorise XQL when it was absent) |
| `POST /api/runs/{run_id}/reconcile` | summary reports `queries_issued` in ledger units (pull pages, plus the incidents pages when the correlation fallback read them) |
| `POST /api/assertions/{id}/run` | reports `queries_issued` (runner calls; 0 for a dry run or no tenant). `GET /api/assertions/runs/{run_id}` does **not** carry it — the persisted row has no such column |
| global `XsiamError` body | gains an additive `upstream_status` (the tenant's HTTP status); `{error, code, detail}` is unchanged |
| `GET /api/health` | gains an `mcp` component: `MCP_DISABLED` (ok, opt-in) when the flag is off; with `CORTEXSIM_MCP_HTTP=1` it reads the actual mounted server and reports `MCP_SDK_IMPORT_FAILED`, `MCP_NOT_MOUNTED` or `MCP_NO_TOOLS` (degraded) rather than inferring the mount from the flag |

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
- A push / `staged` run has no beacon and so no host record: reconcile never writes
  `missed` on it — a silent row reads `pending` / `HOST_EVIDENCE_ABSENT` — and its hits
  come from matched alerts or attestation (a bundle-completion callback is Phase 2).
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
  Non-zero exit is **not** a Go error (returned as exitCode). A missing binary inside
  the command is the shell's `127`, not `-1`: `-1` is returned (with `err`) only when
  `sh` itself cannot be started or the wait fails with a non-exit error. A step that declares `required_binaries` is
  preflighted before it runs — a missing binary yields the `!! TOOL_ABSENT:` marker, a
  `not_run` / `TOOL_ABSENT` step record and a run completed with `127` (§1.3.1).
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
- `POST /api/runs/{run_id}/complete` with `{exit_code, summary, steps[]}` where summary is
  `SUCCESS|FAILED (exit N) | scenario=... mode=... duration=...` and `steps[]` is the
  per-step host evidence (§1.3.1).
- On ctx-cancel mid-execution: `Complete(runID, abortExitCode, "agent shutdown — task
  interrupted", steps)` (exit 130) then return.
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

### 5.6 Tier-D host-termination witnesses

`deploy/tier-d/run-tier-d.sh` drives the pull path end to end against a container target.
With `--inject-kill subshell|anchor|beacon --at-step <id>` it plays the Cortex agent and
kills one process of the beacon's tree during that step, so each host witness in §1.3.1 is
exercised for real. The launch declares `agent_profile_mode=block`.

| Mode | What is killed | Expected record |
|---|---|---|
| `subshell` | the step's subshell | step `signaled` |
| `anchor` | the anchor shell (scenario needs `cgo_anchor` or step `causality`) | step `anchor_terminated` |
| `beacon` | the beacon itself | no `/complete`; the heartbeat sweep terminalises the run `beacon_lost` once the agent has been silent for `CORTEXSIM_BEACON_LOST_AFTER_SECONDS` plus up to one 30 s sweep |

- **Anchor exit is noticed directly** (`agent/executor/chained.go`): the chain session
  waits on the anchor pid and, on exit, reaps the orphaned step's process group, so
  `anchor_terminated` is reported within seconds rather than on pipe EOF. The beacon's own
  cleanup is never recorded as `signaled`. Otherwise the latency ceiling is the 180 s step
  timeout.
- **Injector** (`deploy/tier-d/inject-kill.sh`): waits for the target step to fork, refuses
  an ambiguous victim (exit 3), and times out at 120 s. Status words: `KILLED`, `TIMEOUT`,
  `MISSED` (the step finished before it could be hit).
- **Harness exits:** `0` genuine PASS, `1` FAIL or INCONCLUSIVE, `2` could not set up,
  `3` the run did not reach a terminal state inside the poll budget, `4` an injection was
  requested but not delivered (TIMEOUT or MISSED). `verdict.json` is still written on 4.
- **Environment:** `TIER_D_BEACON_LOST_AFTER_SECONDS` is read by
  `tests/e2e_isolated/test_tier_d_prevention_witnesses.py` (not by `run-tier-d.sh`); it
  must equal the value SimCore was started with for `CORTEXSIM_BEACON_LOST_AFTER_SECONDS`
  (the test skips the beacon witness unless it is set and no greater than 120).
- **Classification** (`deploy/tier-d/classify.py`) fails closed when `step_outcomes` is
  absent from the run. A `beacon_lost` step is classed `UNWITNESSED` and never feeds the
  TTP count.
- **Fixture:** `tests/fixtures/scenarios/sim-test-prev-001.yml` (card `TTP-2026-9901`),
  outside the corpus loader.

Measured live against a branch SimCore on the authoring host: subshell 12.5 s, anchor
12.2 s, beacon 54.6 s (the beacon figure was taken with
`CORTEXSIM_BEACON_LOST_AFTER_SECONDS=30`; the heartbeat sweep runs every 30 s,
`heartbeat_sweep_loop(interval_seconds=30)` in `core/api/agents.py`, so the loss is
called between 30 s and about 60 s after the last contact). This proves the **host witness only**. Tier-D has no Cortex agent
and no tenant alert, so **no run reads `prevented`**, and `tenant-verified` for prevention
stays 0.

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

---

## 9. MCP server (`core/mcp_server/`)

> **Authored is not proven. `tenant-verified: 0`.** The MCP layer is a
> projection of routes SimCore already serves. No MCP tool has been run against
> a live Cortex tenant, and no result it returns is tenant evidence until a run
> or assertion verdict carries tenant provenance. Spec:
> `docs/superpowers/specs/2026-11-02-cortexsim-mcp-server-design.md`.

An agent reaches SimCore through a Model Context Protocol server instead of
hand-built REST calls. The package is `core/mcp_server/` (not `core/mcp/`, which
would shadow the `mcp` SDK under `PYTHONPATH=core`); the SDK is pinned
`mcp==1.30.0`. There is one implementation of every tool and two transports; the
only difference between them is the `httpx` transport handed to the client.

The tool count is read from the registry, never typed: `cd core && PYTHONPATH=. python -m mcp_server --list-tools`
prints one row per tool and ends with `41 tools` (25 `cortexsim.*`, 16 `xsiam.*`
— 37 backed, 4 registered-but-unbacked). `make check-mcp-boot` runs that command
inside the built image and fails below 41.

### 9.1 Transports

| | stdio | Streamable HTTP |
|---|---|---|
| who launches | the DC's client (`claude mcp add cortexsim -- …`) | SimCore, when `CORTEXSIM_MCP_HTTP=1` |
| where it runs | the DC's host venv, or `docker exec -i <container> python -m mcp_server` | inside the SimCore process, mounted at `/mcp` |
| reaches SimCore via | real HTTP to `CORTEXSIM_URL` (default `http://127.0.0.1:8888`) | `httpx.ASGITransport(app)` — in-process, zero sockets |
| default | — | **off**. A port that answers JSON-RPC is a new surface; it is enabled per lab, not per image |
| session mode | per process | `stateless_http=True` — SimCore restarts are routine mid-POV, and a stateful session manager would 404 every client after one |
| DNS-rebinding guard | n/a | the SDK's transport-security allowlist, seeded with `localhost`, `127.0.0.1` and `CORTEXSIM_MCP_ALLOWED_HOSTS` (comma list, default empty) |

Authentication posture is inherited, not widened: SimCore has no API
authentication layer (`CORTEXSIM_SECRET` protects the vault at rest, not the
routes), and the MCP surface sits inside that boundary. What the surface adds is
the removal of the one thing an agent could newly do — writes to a tenant always
preview (§9.5).

Settings (all in `.env.example`): `CORTEXSIM_MCP_HTTP` (default off),
`CORTEXSIM_MCP_ALLOWED_HOSTS`, `CORTEXSIM_MCP_MAX_TENANT_CALLS_PER_MINUTE`
(default 30, a process-local token bucket over tenant-class tools) and
`CORTEXSIM_URL`. Never pass a tenant URL or key to the server; it does not read
them. Other CLI flags: `--server URL`, `--list-tools`, `--manifest` (the P-2
manifest the consumer vendors) and `--max-tenant-calls-per-minute N` (overrides
`CORTEXSIM_MCP_MAX_TENANT_CALLS_PER_MINUTE` for this process).

### 9.2 Client install

Streamable HTTP, when the mount is on (zero install on the laptop):

```bash
claude mcp add --transport http cortexsim http://localhost:8888/mcp
```

stdio, from the running container (no Python on the laptop needed; substitute
the container name of the running image):

```bash
claude mcp add cortexsim -- docker exec -i cortex-pov-engine-simcore-v1.1.0 python -m mcp_server --server http://127.0.0.1:8888
```

stdio, from a host venv that has `core/requirements.txt` installed:

```bash
claude mcp add cortexsim -e CORTEXSIM_URL=http://127.0.0.1:8888 -e PYTHONPATH=<checkout>/core -- python -m mcp_server
```

`<checkout>` is the absolute path to your clone. `claude mcp add` sets no working
directory, so without `PYTHONPATH` the server exits with `No module named mcp_server`;
`python` must be the venv's interpreter (give its absolute path if the venv is not active).

### 9.3 Tools

Canonical id (dotted) is what the manifest, the docs and the tests use; the wire
name is derived by `core/mcp_server/naming.py` (`.` → `_`, must match
`^[a-z0-9_]{1,48}$`).

| canonical id | wire name | group | class | network | backing |
|---|---|---|---|---|---|
| `cortexsim.health.get` | `cortexsim_health_get` | cortexsim | read | simcore | `GET /api/health` |
| `cortexsim.readiness.get` | `cortexsim_readiness_get` | cortexsim | read | simcore | composed |
| `cortexsim.scenario.list` | `cortexsim_scenario_list` | cortexsim | read | simcore | `GET /api/scenarios` |
| `cortexsim.scenario.get` | `cortexsim_scenario_get` | cortexsim | read | simcore | `GET /api/scenarios/{id}` |
| `cortexsim.pov.profiles` | `cortexsim_pov_profiles` | cortexsim | read | simcore | `GET /api/pov/profiles` |
| `cortexsim.pov.scope` | `cortexsim_pov_scope` | cortexsim | read | simcore | `POST /api/pov/scope` |
| `cortexsim.uctc.summary` | `cortexsim_uctc_summary` | cortexsim | read | simcore | `GET /api/uctc/summary` |
| `cortexsim.uctc.use_case` | `cortexsim_uctc_use_case` | cortexsim | read | simcore | `GET /api/uctc/use-cases/{id}` |
| `cortexsim.uctc.test_case` | `cortexsim_uctc_test_case` | cortexsim | read | simcore | `GET /api/uctc/test-cases/{id}` |
| `cortexsim.uctc.coverage` | `cortexsim_uctc_coverage` | cortexsim | read | simcore | `GET /api/uctc/coverage` |
| `cortexsim.uctc.gaps` | `cortexsim_uctc_gaps` | cortexsim | read | simcore | `GET /api/uctc/gaps` |
| `cortexsim.uctc.by_scenario` | `cortexsim_uctc_by_scenario` | cortexsim | read | simcore | `GET /api/uctc/by-scenario/{id}` |
| `cortexsim.run.launch` | `cortexsim_run_launch` | cortexsim | write | simcore → host | `POST /api/runs` |
| `cortexsim.run.get` | `cortexsim_run_get` | cortexsim | read | simcore | `GET /api/runs/{id}` |
| `cortexsim.run.list` | `cortexsim_run_list` | cortexsim | read | simcore | `GET /api/runs` |
| `cortexsim.run.results` | `cortexsim_run_results` | cortexsim | read | simcore | `GET /api/results/{run_id}` |
| `cortexsim.run.report` | `cortexsim_run_report` | cortexsim | read | simcore | `GET /api/runs/{id}/report` |
| `cortexsim.run.pair` | `cortexsim_run_pair` | cortexsim | read | simcore | `GET /api/runs/{id}/pair` |
| `cortexsim.run.abort` | `cortexsim_run_abort` | cortexsim | destructive | simcore → host | `POST /api/runs/{id}/abort` |
| `cortexsim.run.reconcile` | `cortexsim_run_reconcile` | cortexsim | write | tenant (`xsiam`) | `POST /api/runs/{id}/reconcile` |
| `cortexsim.run.verify` | `cortexsim_run_verify` | cortexsim | write | tenant (`xsiam_tenant`) | `POST /api/runs/{id}/verify` |
| `cortexsim.connector.preflight` | `cortexsim_connector_preflight` | cortexsim | read | tenant | `POST /api/connectors/{kind}/preflight` |
| `cortexsim.assertion.list` | `cortexsim_assertion_list` | cortexsim | read | simcore | `GET /api/assertions` |
| `cortexsim.assertion.get` | `cortexsim_assertion_get` | cortexsim | read | simcore | `GET /api/assertions/{id}` |
| `cortexsim.assertion.run` | `cortexsim_assertion_run` | cortexsim | write | tenant when live | `POST /api/assertions/{id}/run` (`dry_run` default true) |
| `xsiam.operation.list` | `xsiam_operation_list` | xsiam | read | none | `GET /api/xsiam/operations` |
| `xsiam.operation.get` | `xsiam_operation_get` | xsiam | read | none | `GET /api/xsiam/operations/{op_id}` |
| `xsiam.operation.execute` | `xsiam_operation_execute` | xsiam | by op | tenant (read ops) | `POST /api/xsiam/tenants/{name}/operations/{op_id}` |
| `xsiam.session.index` | `xsiam_session_index` | xsiam | read | none | credentials list + ledger + provenance |
| `xsiam.dataset.list` | `xsiam_dataset_list` | xsiam | read | tenant | `OP-XQL-GET-DATASETS` |
| `xsiam.dataset.schema` | `xsiam_dataset_schema` | xsiam | read | tenant | one XQL, derived |
| `xsiam.alert.list` | `xsiam_alert_list` | xsiam | read | tenant | `OP-ALERTS-GET-ALERTS-MULTI-EVENTS` |
| `xsiam.correlation_rule.list` | `xsiam_correlation_rule_list` | xsiam | read | tenant | `OP-CORRELATIONS-GET` |
| `xsiam.xql.query` | `xsiam_xql_query` | xsiam | read | tenant | start + poll, ledger-charged |
| `xsiam.incident.list` | `xsiam_incident_list` | xsiam | read | tenant | `OP-INCIDENTS-GET-INCIDENTS` |
| `xsiam.playbook.list` | `xsiam_playbook_list` | xsiam | read | tenant | `OP-PLAYBOOKS-GET` |
| `xsiam.healthcheck` | `xsiam_healthcheck` | xsiam | read | tenant | `OP-HEALTHCHECK` |
| `xsiam.modeling_rule.list` | `xsiam_modeling_rule_list` | xsiam | — | none | `TOOL_NOT_BACKED` |
| `xsiam.parser.list` | `xsiam_parser_list` | xsiam | — | none | `TOOL_NOT_BACKED` |
| `xsiam.content.list` | `xsiam_content_list` | xsiam | — | none | `TOOL_NOT_BACKED` |
| `xsiam.ingest.status` | `xsiam_ingest_status` | xsiam | — | none | `TOOL_NOT_BACKED` (v1) |

Four tools are registered so a consumer that expects them gets an honest
refusal (`TOOL_NOT_BACKED`) instead of a missing tool: the XSIAM public API
exposes no listing for them, and nothing is queried.

### 9.4 The result envelope

Every tool returns `{data, evidence}` as `structuredContent`, and the same
evidence as the first line of `content[0].text` for clients without
structured-output support. The MCP layer computes none of the numbers: it reads
them off SimCore responses (`queries_issued` from the routes in §1.16,
`ledger` and `tenant_verified` from `GET /api/readiness`).

| `evidence` field | meaning |
|---|---|
| `basis` | `authored` · `configured` · `reachable` · `verified` · `preview` · `simcore` · `derived`. The first four are the readiness ladder; `simcore` is SimCore's own state, `preview` a dry run, `derived` a tenant reply with the transformation stated in `data` |
| `queries_issued` | metered tenant operations this call caused, in the ledger's unit (one live catalog read is 1, but `OP-XQL-GET-QUERY-RESULTS` run on its own is 0; one XQL query is 1 including its polls; one reconcile pull page is 1). It is a cost, not a contact signal. **`null` = SimCore accepted the request and did not report a count; whether the tenant was called is not known either — this is not zero** |
| `tenant_answered` | `true` only when the tool got the tenant's reply back through SimCore (a live executor read, an XQL start or poll, the healthcheck's license-gated 403). Independent of `queries_issued`: a results poll answers at a cost of 0 and is `basis: reachable` |
| `http_round_trips` | HTTP requests to the tenant when the route reports them; `null` otherwise, never a guess |
| `tenant` | vault name; never the URL or key id |
| `ledger` | `{key, used_today, budget, remaining, breaker_open, breaker_until}` or `null`; `null` for SimCore-only tools, not `{used: 0}` |
| `tenant_verified` | counted by SimCore, today 0. **`null` = readiness could not be read — it is never rendered as 0** |
| `statement` | one sentence in the console's words, below |
| `as_of` | SimCore's clock, ISO-8601 UTC |
| `truncated` | a capped list sets this flag; it is not an error |

Statement strings (from `core/mcp_server/envelope.py::statement`, checked in this order; `N` is the count, or `not read` when `tenant_verified` is `null`):

| condition | statement |
|---|---|
| SimCore unreachable | `No tenant answered this call. SimCore itself was unreachable. tenant-verified: N` |
| not backed | `<id> has no backing operation in the XSIAM public API; nothing was queried. tenant-verified: N` |
| preview | `Preview only. Nothing was sent to the tenant. To execute, a human runs the request in execute_as_human. tenant-verified: N` |
| tenant answered (`tenant_answered`, or `queries_issued >= 1`) | `Tenant <name> answered; this call cost <n> metered operation(s). This is reachable evidence; a detection is verified only when a run verdict carries tenant provenance. tenant-verified: N` — `<n>` may be 0 (a results poll); with `tenant_answered` and a `null` count it reads `… answered; how many metered operations this call cost is not known — this is not zero. …` |
| `queries_issued` is `null` | `SimCore accepted the request; whether the tenant was called and how many operations it issued is not known — this is not zero. tenant-verified: N` |
| `queries_issued == 0` | `No tenant answered this call. This is <basis>, not tenant evidence. tenant-verified: N` |

**A zero is degraded, not ok, and tolerance hides bugs.** A response without the
fields a tool reads raises `SIMCORE_SHAPE_UNKNOWN`; it is never read as empty. A
named list whose shape declares a total (alerts, incidents, correlations,
playbooks) raises `XSIAM_PARSE_ERROR` when the tenant omits it or returns a
non-integer; only datasets falls back to the row count.

### 9.5 Writes always preview

No tool executes a write or destructive operation against a tenant. A request
with `dry_run: false` on such an operation returns `MCP_WRITE_NOT_EXECUTABLE`
with the preview attached; the human runs the previewed request in the console.
There is no write path to Cortex through this surface, and the two REST flags
`CORTEXSIM_XSIAM_ALLOW_WRITE` / `CORTEXSIM_XSIAM_ALLOW_DESTRUCTIVE` stay
default-off. `xsiam.xql.query` refuses a write-stage query
(`XQL_WRITE_STAGE_REFUSED`, refused rather than rewritten) and a credential
without `"xql"` in `config.capabilities` (`XSIAM_CAPABILITY_MISSING`, 403).
Every tenant-class tool is rate-limited per process
(`CORTEXSIM_MCP_MAX_TENANT_CALLS_PER_MINUTE`). Only some of them charge the
per-tenant daily ledger, one unit per query: an XQL start (`xsiam.xql.query`,
`xsiam.dataset.schema`, the executor's `OP-XQL-START-XQL-QUERY`), `GET …/metrics`,
and the verifier's runners behind `cortexsim.run.verify` and the live
`cortexsim.assertion.run`. **Not charged:** result polls, `OP-XQL-GET-QUOTA`,
every other catalog read (`OP-XQL-GET-DATASETS`, alerts, incidents,
correlations, playbooks, healthcheck), reconcile pulls (`cortexsim.run.reconcile`)
and preflight's scope and dataset XQL (`cortexsim.connector.preflight`).
Preflight is the one remaining XQL path the ledger does not budget; those reads
are bounded by the per-process rate limit only.

### 9.6 Error codes

The result carries `isError: true`, `content[0].text = "<code>: <error> — <detail>"`
and `structuredContent = {error, code, detail, remediation, evidence}`. SimCore's
own `{error, code, detail}` codes pass through untouched (for example
`XSIAM_CAPABILITY_MISSING`, `XQL_WRITE_STAGE_REFUSED`, `XSIAM_PARSE_ERROR`,
`XSIAM_QUOTA_ERROR`, documented where the routes are). The codes this layer
adds (`core/mcp_server/errors.py` unless noted):

| code | when |
|---|---|
| `MCP_WRITE_NOT_EXECUTABLE` | `dry_run: false` on a write or destructive op through MCP (preview attached) |
| `TOOL_NOT_BACKED` | one of the four registered-but-unbacked tools |
| `NO_TENANT_CONFIGURED` | no `xsiam_tenant` credential in the vault, or the named tenant is not one |
| `TENANT_AMBIGUOUS` | more than one tenant is configured and none was named |
| `MCP_RATE_LIMITED` | the per-process tenant-call bucket is exhausted (`retry_after_seconds`); SimCore-only tools keep working |
| `SIMCORE_UNREACHABLE` | stdio mode, `CORTEXSIM_URL` did not answer — distinct from any tenant error, so "SimCore is down" never reads as "the tenant is down" |
| `SIMCORE_SHAPE_UNKNOWN` | a SimCore response without the fields the tool reads |
| `SIMCORE_TRANSPORT_ERROR` | the request reached SimCore but no complete answer came back |
| `MCP_INVALID_ARGUMENTS` | arguments rejected against the tool's input schema |
| `UCTC_INDEX_NOT_LOADED` | `core/mcp_server/tools/cortexsim.py`: a UC/TC tool called on a snapshot-less deploy, rendered as degraded rather than "0 test cases" |
| `MCP_INTERNAL_ERROR` | `core/mcp_server/registry.py`: any unexpected exception, so the SDK's generic wrapper never drops the code |

### 9.7 Health

`GET /api/health` carries an `mcp` component (§1.16): `MCP_DISABLED` while the
opt-in is off, and with the flag on the actual mounted server is read — never
inferred from the flag. `cortexsim.health.get` and `cortexsim.readiness.get`
expose both over MCP.

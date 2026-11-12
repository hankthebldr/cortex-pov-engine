# CortexSim MCP server — design

Status: **design** (awaiting review) · 2026-11-02
Author: Henry + Claude
Branch: `feature/api-mcp-server` (cut from `dev` @ `b98ccac`)
Origin: option B of `docs/brainstorm/2026-11-02-cortex-bot-review-and-pov-integration.md`
(§2.6, "MCP server, do next, own feature branch").

Scope: give an agent — Claude Code on a DC's laptop today, `cortex-pov-agent`
next — a Model Context Protocol surface over CortexSim's own API (scenarios,
scope, runs, readiness, preflight, UC/TC, reconcile, verify) and a projection
of the 116-operation XSIAM catalog through the **same** gated executor the
REST route uses. Reads run live against a configured tenant; writes and
destructives return the dry-run preview plus the exact consent a human would
have to give, and nothing the agent can pass makes them execute. Every result
carries `queries_issued`, the per-tenant quota ledger, and the honesty line
(authored vs proven, `tenant-verified: N`) so a preview can never be quoted as
tenant proof. Phase 1 is this document. Phase 2 (implementation, tests, the
`docs/reference` section) starts only when this document is accepted.

---

## 0 · The one principle

**An agent can only ever be told what SimCore would have told a human, with the
same honesty line attached.** The MCP server is a projection of the HTTP
surface, never a second path to the tenant. It computes no evidence of its
own: `queries_issued`, the ledger snapshot and the verdict provenance all
originate in a SimCore response, and the MCP layer only reads them off and sums
them when one tool makes several calls. A result whose `queries_issued` is `0`
was not answered by a tenant, and says so in its first line.

This is the same rule the console's Readiness surface and `/api/health` already
follow (*a zero is degraded, not ok · authored is not proven · tenant-verified
is 0*). It is restated here because an LLM client is the first consumer that
will **paraphrase** what it is told, and a paraphrase drops the caveat first.

---

## 1 · Intent and why

The cortex-bot review (§2.4–§2.9) settled three things. CortexSim already owns
the pieces an MCP server needs — a declarative operation catalog with an
access class per operation, a gated executor with a traversal guard, one error
vocabulary, one per-tenant quota ledger, and a readiness ladder that states
what each rung does and does not prove. cortex-bot, by contrast, documents a
kill switch it does not implement (F1) and lets the LLM set its own write gate
(F2). And `cortex-pov-agent` names an `xsiam.*` MCP server as prerequisite P-2
of its playbook agent, with ten read tools and a manifest shape, but has no
transport code yet.

So the moment this exists for is concrete: a DC sitting in a customer lab with
Claude Code open asks *"which scenarios evidence TC-EDR-05, is my tenant
actually reachable, launch SIM-EDR-008 on pov-target-01 and tell me when the
alert lands"* — and gets answers that are true in the sense CortexSim already
enforces, from a surface that cannot be talked into a write.

What would make this a failure even if it works: a green tool result the DC
pastes into a POV report as tenant evidence when no tenant answered.

---

## 2 · Goals, non-goals, anti-goals

### Goals

- **G1 — One tool implementation, two transports.** The same tool functions
  serve stdio (launched by the DC's client) and Streamable HTTP (mounted on
  SimCore, opt-in).
- **G2 — Zero new paths to the tenant.** Every XSIAM tool goes through
  `POST /api/xsiam/tenants/{name}/operations/{op_id}` or the two XQL routes,
  inheriting `_resolve_path`, `_effective_dry_run` and credential loading
  unchanged.
- **G3 — The honesty envelope on every result**, in both the text content and
  the structured content, so a client that reads either sees it.
- **G4 — Vocabulary compatibility** with `cortex-pov-agent`'s `xsiam.*` tool
  ids where that vocabulary exists, and a generated P-2 manifest.
- **G5 — Boots inside the prod image** (`python:3.11-slim`), proven by a
  Makefile gate that runs the built image, not the host tree.
- **G6 — Writes are previews by construction.** No argument an agent can pass
  turns a write or destructive operation into a live call in v1.

### Non-goals

- **N1 — Not an authentication layer for SimCore.** SimCore has none today;
  the MCP surface inherits that trust boundary and does not widen it (§4.4).
  Adding auth to SimCore is its own spec.
- **N2 — Not the cortex-bot pack harvest.** The 24 cortex-bot-only paths
  (option A) are catalog work; when those packs land, the projection picks
  them up with no MCP change.
- **N3 — Not EAL campaign dispatch, not composer drafts, not credential CRUD.**
  An agent must never be able to add, edit or delete a tenant credential.
- **N4 — Tools only in v1.** MCP resources and prompts are considered in §13.
- **N5 — Not a fixtures provider.** `cortex-pov-agent`'s `.invalid` fixture
  set stays in that repo; this server talks to a real SimCore.

### Anti-goals

- **A1 — Tenant proof from nowhere.** A result with `queries_issued: 0` must
  read as "no tenant answered", never as "nothing found".
- **A2 — Agent-settable consent.** cortex-bot's F2. The per-request `consent`
  dict that unlocks a write on the REST route is, through MCP, set by the LLM.
  That is the one input this design refuses to honour.
- **A3 — A tool that takes a tenant URL or credential** as an argument.
  Tenants are addressed by their vault name only (§5.4).
- **A4 — A tolerant reader.** An unrecognised SimCore or tenant shape raises
  `SIMCORE_SHAPE_UNKNOWN` / `XSIAM_PARSE_ERROR`; it never reads as empty.
- **A5 — Counting tools as coverage.** "N tools" is not "N capabilities
  proven". `tenant-verified` stays 0 until a run executes against a live
  tenant and its verdict carries tenant provenance.

---

## 3 · What exists today (counted 2026-11-02)

| thing | where | state |
|---|---|---|
| XSIAM operation catalog | `core/integrations/xsiam/operations/packs/*.yml` | **116 ops** — read 57 · write 43 · destructive 16; POST 110 · GET 6; 5 with path params; no `consent` overrides |
| Gated executor | `core/api/xsiam.py::execute_operation` | `_resolve_path` (one percent-encoded segment per param; refuses `.`, `..`, `/`, `\`), `_effective_dry_run` (read → live unless `dry_run: true`; write/destructive → preview unless `dry_run: false` AND global flag AND consent key). **Reports no `queries_issued` and does not charge the ledger** |
| XQL routes | `POST /tenants/{name}/xql`, `GET /tenants/{name}/xql/{query_id}` | start + poll; no write-stage guard |
| Quota ledger | `core/integrations/xsiam/ledger.py::ledger` | process-scoped singleton, rolling-day counter + breaker; keyed by `engine/verifier.py::tenant_ledger_key(base_url)`; **only charger today is `verifier._tenant_budget`** |
| Error vocabulary | `core/integrations/xsiam/codes.py` | `XSIAM_{AUTH,QUOTA,API,QUERY,CONFIG,TRANSPORT,PARSE}_ERROR` + remediation text; exceptions carry `code` and `http_status` |
| Credential kinds | `integrations/xsiam/loader.py::XSIAM_KIND = "xsiam_tenant"` · `connectors/xsiam.py::kind = "xsiam"` | two kinds on purpose; configuring reconcile does not authorise XQL |
| Preflight | `POST /api/connectors/{kind}/preflight` | staged config → dns_tls → auth → scope → datasets (opt-in) → clock; every response carries `queries_issued` |
| Health | `GET /api/health` | zero outbound calls; `degraded_components[]`, `not_checked[]`; a zero is degraded |
| Readiness ladder | `ui/src/components/console/readiness/connectorState.js` | AUTHORED · CONFIGURED · REACHABLE · VERIFIED; **`tenant_verified` is computed in the console** from `verdictProvenance` (`queries_issued > 0` or tenant/integration in `tc_verdict_detail`); backend constants are `0` |
| Write flags | `core/config.py:120-121` | `CORTEXSIM_XSIAM_ALLOW_WRITE = False`, `CORTEXSIM_XSIAM_ALLOW_DESTRUCTIVE = False` |
| Consumer | `cortex-pov-agent/mcp/xsiam/playbook-tool-surface.md`, `mcp/README.md`, `mcp/xsiam/fixtures/manifest.yaml` | ten read tools (`xsiam.session.index`, `.dataset.list`, `.dataset.schema`, `.modeling_rule.list`, `.parser.list`, `.ingest.status`, `.content.list`, `.correlation_rule.list`, `.alert.list`, `.xql.query`); scopes `dataset.read`, `xql.execute`, `content.read`, `alert.read`; `@modelcontextprotocol/sdk` **1.30.0** (TypeScript); no transport code in `src/` yet; P-2 manifest shape `manifest_version: 2, kind: mcp-server` |
| Python SDK | PyPI `mcp` | latest **2.3.0** (requires ≥3.10, renames `FastMCP` → `MCPServer`, adds `httpx2`/`mcp-types`/`opentelemetry-api`); **1.30.0** (2026-09-07) is the last 1.x line and matches the consumer's TS pin |
| CortexSim pins | `core/requirements.txt` | `fastapi==0.141.1 starlette==1.6.0 uvicorn[standard]==0.29.0 pydantic==2.13.4 pydantic-settings==2.15.0 httpx==0.27.0 jsonschema==4.23.0`. Either SDK line forces `uvicorn ≥ 0.31.1`; 1.x also forces `httpx ≥ 0.27.1` |
| Client naming | Anthropic Messages API | tool names must match `^[a-zA-Z0-9_-]{1,64}$`; Claude Code prefixes `mcp__<server>__`, so `mcp__cortexsim__` leaves **48 characters** and **no dots** |
| tenant-verified | everywhere | **0** |

---

## 4 · Transport and process topology

### 4.1 The tool layer is a REST client of SimCore

The orchestrator's task queue, the SSE bus and the credential vault all live
inside the SimCore process. An MCP server that imported `core` and ran as its
own process would need the master key, would write `Run` rows the running
queue never sees, and would be a second implementation of every gate. So the
tool layer never imports `core.api`; it holds one `httpx.AsyncClient` and
calls the routes.

```
   DC laptop                                   SimCore (:8888)
 ┌───────────────┐   stdio (JSON-RPC)   ┌─────────────────────────────┐
 │ Claude Code   │◄────────────────────►│ python -m mcp_server        │
 │ / pov-agent   │                      │   httpx ──► http://127.0.0.1:8888/api/…
 └───────────────┘                      └─────────────────────────────┘
        │                                             │
        │ Streamable HTTP  (opt-in, same port)        ▼
        └───────────────────────────────►  /mcp  ──► httpx.ASGITransport(app) ──► /api/…
                                                     (in-process loopback, no socket)
                                                              │
                                              ┌───────────────┴───────────────┐
                                              │ core/api/xsiam.py executor    │
                                              │ _resolve_path · _effective_dry_run
                                              │ load_xsiam_client · ledger    │
                                              └───────────────┬───────────────┘
                                                              ▼
                                                      XSIAM tenant (opt-in)
```

One implementation of every tool; the only thing that differs between the two
transports is the `httpx` transport object handed to the client.

### 4.2 The two transports

| | stdio | Streamable HTTP |
|---|---|---|
| who launches | the DC's client (`claude mcp add cortexsim -- …`) | SimCore, when `CORTEXSIM_MCP_HTTP=1` |
| where it runs | the DC's host venv, or `docker exec -i <container> python -m mcp_server` | inside the SimCore process, mounted at `/mcp` |
| reaches SimCore via | real HTTP to `CORTEXSIM_URL` (default `http://127.0.0.1:8888`) | `httpx.ASGITransport(app)` — in-process, zero sockets |
| default | — | **off**. A port that answers JSON-RPC is a new surface; it is enabled per lab, not per image |
| session mode | per process | `stateless_http=True` — SimCore restarts are routine mid-POV (image rebuilds), and a stateful session manager would 404 every client after one |
| DNS-rebinding guard | n/a | the SDK's transport-security allowlist, seeded with `localhost`, `127.0.0.1`, and `CORTEXSIM_MCP_ALLOWED_HOSTS` (comma list, default empty) |

`/api/health` gains an `mcp` component (§4.5). A client install is one line
either way (Appendix C).

### 4.3 Package location: `core/mcp_server/`, not `core/mcp/`

Tests and the host-run dev server execute with `PYTHONPATH=core`. A package at
`core/mcp/` would shadow the `mcp` SDK on `import mcp` for every test in the
tree. The name `mcp_server` is unambiguous and cannot collide.

```
core/mcp_server/
  __init__.py
  __main__.py      # python -m mcp_server [--server URL] [--list-tools] [--manifest]
  server.py        # build() -> FastMCP; mount(app) for the HTTP transport
  client.py        # SimCoreClient: thin httpx wrapper, raises SimCoreError with the REST code
  envelope.py      # Evidence model + statement rules (§7)
  naming.py        # dotted id ⇄ wire name, the single mapping (§5.1)
  schemas.py       # the tool input models — copies of the REST request models, drift-tested (D-13)
  tools/
    cortexsim.py   # §5.2
    xsiam.py       # §5.3
  manifest.py      # P-2 manifest export (§11)
```

### 4.4 Authentication posture — inherited, not widened

SimCore has no API authentication layer; `CORTEXSIM_SECRET` protects the vault
at rest, not the routes. Whoever can reach `:8888` can do what the console
does. The MCP surface sits inside that same boundary:

- the HTTP mount is the **same port and process**, default-off, with the
  rebinding allowlist above — it adds no reachability SimCore did not have;
- stdio mode runs as the DC's own user on the DC's own host, talking to a
  SimCore the DC already reaches.

This design explicitly does not invent an auth scheme for one route (non-goal
N1). What it does instead is make the one thing an agent could newly do —
mutate a tenant — impossible through this surface (§6), so the inherited
boundary gets no new consequence.

### 4.5 Health

`GET /api/health` gains a component `mcp`:

```json
{"id": "mcp", "status": "ok|degraded",
 "mounted": false, "path": "/mcp", "transport": "streamable-http",
 "tools": 0, "sdk": "mcp==1.30.0", "code": "MCP_DISABLED",
 "detail": "opt-in; set CORTEXSIM_MCP_HTTP=1 to mount"}
```

The component status vocabulary is pinned to `{ok, degraded, error}` by
`tests/api/test_health.py`, the aggregate goes `degraded` for any non-`ok`
component, and the console's `healthModel.js` maps anything else to UNKNOWN —
so a fourth status would make every stock deployment report `degraded` for a
feature that is intentionally off. The rule "a zero is degraded" is about a
count that should not be zero (a corpus with no scenarios); an opt-in feature
left off is not that. So: **flag off → `ok` with `mounted: false` and
`code: MCP_DISABLED`**; flag on with the SDK failing to import or zero tools
registered → `degraded` with a code and remediation. "Off by configuration"
and "on but empty" therefore never render the same, which is the invariant the
rule exists for. The health endpoint still makes zero outbound calls — this
component counts registrations, never reachability.

---

## 5 · Tool surface

### 5.1 Naming

Every tool has one **canonical id**, dotted, in the `cortex-pov-agent`
style — `xsiam.dataset.list`, `cortexsim.run.launch`. The canonical id is what
the manifest, the docs, the `title` field and every test use.

The **wire name** is derived, never authored: `.` → `_`, and the result must
match `^[a-z0-9_]{1,48}$` (48 = 64 minus the `mcp__cortexsim__` prefix Claude
Code adds). `naming.py` holds the one mapping; a test asserts every registered
id round-trips and fits. Nothing else in the tree spells a wire name.

| canonical | wire | why |
|---|---|---|
| `xsiam.dataset.list` | `xsiam_dataset_list` | matches the consumer's id; dots are illegal on the Anthropic tool-name pattern |
| `cortexsim.connector.preflight` | `cortexsim_connector_preflight` | 30 chars, well inside 48 |

`cortexsim.*` is the prefix for CortexSim's own surface; `xsiam.*` is the
projection. No third prefix in v1.

### 5.2 The CortexSim group (`cortexsim.*`)

Where a REST route has a Pydantic request model, the tool's input schema **is
that model** (re-exported, not copied), so a REST field cannot drift from the
tool. Annotations are MCP `ToolAnnotations`: `readOnlyHint` /
`destructiveHint` / `idempotentHint` / `openWorldHint`. "Network" says whose
network a call touches: `none` (catalog or local), `simcore`, or `tenant`
(the call can leave the lab).

| canonical id | REST | annotations | network | notes |
|---|---|---|---|---|
| `cortexsim.health.get` | `GET /api/health` | read, idempotent | simcore | pass-through; `not_checked[]` is kept verbatim |
| `cortexsim.readiness.get` | `GET /api/readiness` (new, Appendix D item 6) | read | simcore | renders the four rungs **never collapsed**, each with *proves* / *does not prove*; the `tenant-verified: N` line verbatim; zero outbound calls, like `/api/health` |
| `cortexsim.scenario.list` | `GET /api/scenarios?plane&uc_ref&ttp_ref&entitlement&status` | read | simcore | capped (default 50, max 200) with `truncated` + `total` |
| `cortexsim.scenario.get` | `GET /api/scenarios/{id}` | read | simcore | |
| `cortexsim.pov.profiles` | `GET /api/pov/profiles` | read | simcore | |
| `cortexsim.pov.scope` | `POST /api/pov/scope` | read (POST that mutates nothing) | simcore | input = `ScopeRequest` |
| `cortexsim.uctc.summary` · `.use_case` · `.test_case` · `.coverage` · `.gaps` · `.by_scenario` | `GET /api/uctc/…` | read | simcore | `index_loaded: false` is surfaced as **degraded**, never as "0 test cases" |
| `cortexsim.run.launch` | `POST /api/runs` | **write**, open-world | simcore → **customer host** | input = `McpLaunchRequest`, a **projection** of `LaunchRequest` (the launch rule below); the one tool whose effect lands on a customer machine |
| `cortexsim.run.get` | `GET /api/runs/{id}` | read | simcore | |
| `cortexsim.run.list` | `GET /api/runs` | read | simcore | the route has no filters today; the tool filters client-side on `status` / `scenario_id` and caps (§13 asks the builder whether to add the params to the route) |
| `cortexsim.run.results` | `GET /api/results/{run_id}` | read | simcore | each `Result` keeps its outcome axis verbatim (`prevented` needs two witnesses; a lost beacon reads `pending`) |
| `cortexsim.run.report` | `GET /api/runs/{id}/report?format=markdown` | read | simcore | capped at 256 KiB with `truncated` |
| `cortexsim.run.pair` | `GET /api/runs/{id}/pair` | read | simcore | paired report/block runs |
| `cortexsim.run.abort` | `POST /api/runs/{id}/abort` | **destructive**, idempotent | simcore → customer host | kills a process group on a host; allowed because the console allows it with one click and because an abort can only ever reduce what runs |
| `cortexsim.run.reconcile` | `POST /api/runs/{id}/reconcile?connector=xsiam&integration=<tenant>` | write (SimCore rows), **tenant read** | tenant (kind `xsiam`) | alert read-back; `queries_issued` from the route (Appendix D) |
| `cortexsim.run.verify` | `POST /api/runs/{id}/verify[?force]` | write (SimCore rows), **tenant read** | tenant (kind `xsiam_tenant`) | Tier-2 XQL; ledger-charged by the verifier; `pending` + `reason: no_tenant_integration` passes through unchanged |
| `cortexsim.connector.preflight` | `POST /api/connectors/{kind}/preflight` | read, **tenant** | tenant | `queries_issued` pass-through; `datasets` stage stays opt-in because it is priced per query |
| `cortexsim.assertion.list` · `.get` | `GET /api/assertions`, `/{id}` | read | simcore | `rejected[]` is surfaced — a guard nobody can see is not a guard |
| `cortexsim.assertion.run` | `POST /api/assertions/{id}/run` | write, tenant when `dry_run: false` | tenant | **`dry_run` defaults `true`**, as the route does; a live run needs `dry_run: false` explicitly and the route's own checks |

**Launch rule — a projection, not the whole model.** `LaunchRequest`
(`core/api/runs.py:372`) carries more than a scenario and a target. Its
`consent` dict has **four** keys checked by the one launch gate
(`simulation_authorized`, `c2_authorized`, `cluster_privilege_authorized`,
`node_access_authorized`); `allow_runtime_install` lets the beacon run a
package manager on the target (two-key posture with
`CORTEXSIM_AGENT_ALLOW_RUNTIME_INSTALL`); and the prevention-efficacy fields
(`agent_profile_mode`, `bas_mode_attested`, `soakless_attested`,
`attested_by`) record what *the operator* says the agent profile is. Every one
of those is a human witness statement or a privilege grant, and an LLM
supplying it is cortex-bot's F2 in a different field.

So `cortexsim.run.launch` takes `McpLaunchRequest`:

| projected (agent may set) | excluded (never settable through MCP) |
|---|---|
| `scenario_id`, `mode`, `target_agent_id`, `identity`, `paired_with_run_id`, `canary_run_id`, `consent.simulation_authorized` | `consent.c2_authorized`, `consent.cluster_privilege_authorized`, `consent.node_access_authorized`, `allow_runtime_install`, `agent_profile_mode`, `bas_mode_attested`, `soakless_attested`, `attested_by` |

`simulation_authorized` is projected because it is the same consent a DC gives
by clicking *Launch*, and the scenario's own gates still apply. A scenario
whose adapter or manifest needs one of the excluded consents returns the REST
409 unchanged, naming the consent a human would send from the console. A
block-mode (prevention) run launched through MCP therefore always records
`agent_profile_mode: "unknown"` and no attestation — the paired-run evidence
the console can carry is deliberately not reachable from here.

The projection is enforced by a test, not by convention: every field of
`LaunchRequest` must appear either in `McpLaunchRequest` or in an explicit
`MCP_LAUNCH_EXCLUDED` mapping of field → reason, so a field added to the REST
model later fails the suite until someone decides which side it belongs on.

**Push mode.** `mode: push` is allowed: the result is a bundle id and the
download URL, not bytes — the tool never returns a script body.

### 5.3 The XSIAM projection group (`xsiam.*`)

Three layers, thinnest first.

**(a) The generic projection** — all 116 operations, by `op_id`:

| canonical id | backs onto | behaviour |
|---|---|---|
| `xsiam.operation.list` | `GET /api/xsiam/operations[?category&access_class]` | catalog only, no tenant call |
| `xsiam.operation.get` | `GET /api/xsiam/operations/{op_id}` | catalog only |
| `xsiam.operation.execute` | `POST /api/xsiam/tenants/{name}/operations/{op_id}` | **read** → live, `queries_issued: 1`; **write / destructive** → preview, `queries_issued: 0`, always (§6) |

`xsiam.operation.execute` takes `op_id`, `tenant`, `path_params`, `query`,
`body`. It takes **no `consent` and no `dry_run` for write-class ops** — the
REST request it builds for those always has `dry_run: true` and an empty
consent. For read ops `dry_run: true` is accepted (a preview of a read is a
legitimate way to show a DC what would be sent). The traversal guard is
inherited: a `path_params` value with a separator comes back as the executor's
own `XSIAM_CONFIG_ERROR` text, unchanged.

The generic tool returns the upstream `reply` as-is, capped at 256 KiB with
`truncated: true` and the byte count when cut. This is the one place a raw
tenant body reaches the agent, because the alternative is a projection that
can only speak the ten shapes someone pre-authored.

**(b) Named tools in the consumer's vocabulary** — each is a shaper over one
or two catalog operations, returning the consumer's fixture shape with
allowlisted fields only:

| consumer id | backing | basis | status |
|---|---|---|---|
| `xsiam.session.index` | `GET /api/readiness` (Appendix D item 6: integration names + kinds + `capabilities` + `last_verified_*`, never values; ledger snapshot per tenant; `tenant_verified`; `server_time`) | `configured` | **backed, no tenant call**. `tenant_bound` derives from which credential kinds exist; **`scopes_held` derives from the credential's `capabilities`** (`xql.execute` only when `"xql"` is listed — §5.6), never from kind alone; `degradations[]` names what is not configured or not granted |
| `xsiam.dataset.list` | `OP-XQL-GET-DATASETS` | `reachable` | backed |
| `xsiam.dataset.schema` | one XQL `dataset = <name> \| limit 1` via `xsiam.xql.query`; fields = returned column names | `derived` | backed with the derivation stated; an empty dataset returns `fields: []` **and** `reason: POPULATION_EMPTY` — never "this dataset has no fields" |
| `xsiam.alert.list` | `OP-ALERTS-GET-ALERTS-MULTI-EVENTS` | `reachable` | backed |
| `xsiam.correlation_rule.list` | `OP-CORRELATIONS-GET` | `reachable` | backed |
| `xsiam.xql.query` | `OP-XQL-START-XQL-QUERY` + `OP-XQL-GET-QUERY-RESULTS`, polled | `reachable` | backed; `queries_issued: 1` (one ledger unit; the polls ride on it, §7); **ledger-charged** (Appendix D); the XQL guard below; refused with `XSIAM_CAPABILITY_MISSING` when the credential's `capabilities` lacks `xql` (§5.6) |
| `xsiam.incident.list` | `OP-INCIDENTS-GET-INCIDENTS` | `reachable` | backed (CortexSim addition to the consumer's ten) |
| `xsiam.playbook.list` | `OP-PLAYBOOKS-GET` | `reachable` | backed (addition) |
| `xsiam.healthcheck` | `OP-HEALTHCHECK` | `reachable` | backed (addition); 403 is reported as *license-gated*, as `POST /tenants/{name}/test` does, not as unreachable |
| `xsiam.modeling_rule.list` | — | — | **not backed**: the public API has no modeling-rule listing. Registered, returns `TOOL_NOT_BACKED` with that sentence, never an empty list |
| `xsiam.parser.list` | — | — | **not backed**, same |
| `xsiam.content.list` | — | — | **not backed**, same |
| `xsiam.ingest.status` | — | — | **not backed in v1**: the only candidate query (`INGESTION_HEALTH_XQL` behind `GET /tenants/{name}/metrics`) is a placeholder that has never been run against a tenant. Backing it is tenant-verified work and is listed under Henry's decisions |

The four not-backed tools are registered on purpose: a consumer that calls
`xsiam.parser.list` gets a typed refusal naming the gap, instead of discovering
the server has a different tool list than its manifest.

**(c) The escape hatch the consumer defines, `xsiam.api.get`,** is **not**
offered. The catalog *is* the allowlist, and `xsiam.operation.execute` already
reaches every catalogued path. A path outside the catalog is a pack to author,
not a tool to call.

**XQL guard.** `xsiam.xql.query` refuses any query text containing a
`target type = dataset` or `target type = lookup` stage with
`XQL_WRITE_STAGE_REFUSED` — those stages write inside an operation the catalog
classes as `read`. Row caps are enforced through the results `limit` field
(≤ 1000), never by appending `| limit N` to the query text. A `timeframe` is
required with a 24 h relative default. Appendix D asks the REST XQL route to
adopt the same guard.

### 5.4 Tenant selection — by vault name, never by URL

No tool takes a host, URL, API key id or credential value (anti-goal A3).
Tenant-touching tools take `tenant: str`, the integration **name** in
SimCore's vault, kind `xsiam_tenant` (or `xsiam` for reconcile). Resolution:

- exactly one credential of the needed kind exists → it is the default;
- none → `NO_TENANT_CONFIGURED` (basis `authored`, `queries_issued: 0`,
  remediation names the `PUT /api/credentials/integrations` route);
- more than one and no `tenant` given → `TENANT_AMBIGUOUS` listing the names.

### 5.5 Response discipline (all tools)

- Lists cap at 50 by default, 200 max, and set `truncated: true` + `total`.
- Named `xsiam.*` tools return allowlisted fields only; upstream bodies are
  never echoed except by `xsiam.operation.execute` (above).
- Every timestamp is UTC ISO-8601 with offset; `coerce_utc` is the one coercer.
- `as_of` is set from SimCore's clock, not the MCP process's: in the mounted
  transport the two are the same process; in stdio mode it is read from the
  `Date` header uvicorn puts on every response, falling back to the local
  clock only when the header is absent.

### 5.6 Credential `capabilities` gate the XQL tools

An `xsiam_tenant` credential carries `config.capabilities` (default
`["read_alerts"]`); preflight authorises XQL only when `"xql"` is listed
(`core/api/connectors.py:320`), but `load_xsiam_client` and the executor
route ignore the field, so today a credential whose operator never granted
XQL can still run it through `POST /tenants/{name}/operations/OP-XQL-START-XQL-QUERY`
while `cortexsim.connector.preflight` on the same credential reports XQL not
authorised. That is the "configuring reconcile does not authorise XQL"
doctrine with a hole in it, and an MCP layer that papered over it client-side
would be the only place the gate lived — exactly what D-1 forbids.

So the gate goes into the REST executor (Appendix D item 1): XQL-class
operations (`OP-XQL-START-XQL-QUERY`, `OP-XQL-GET-QUERY-RESULTS`, and the two
XQL routes) are refused with a new `codes.py` entry `XSIAM_CAPABILITY_MISSING`
(403, remediation naming `config.capabilities`) when the credential does not
list `"xql"`. The MCP tools inherit it. This changes REST behaviour for an
existing caller — the console's XQL panel on a credential without the
capability — which is why it is listed among the calls Henry can veto
(§14 note).

---

## 6 · The write gate through MCP

The REST gate is three keys: `dry_run=false` **and** the global flag **and**
the matching consent key. On the REST route the consent key is set by the
caller, which is fine when the caller is a human in the console or a script a
human wrote. Through MCP the caller is the agent, and an agent that can set
`consent.write_authorized: true` has cortex-bot's F2.

**v1: a write or destructive operation through MCP always returns the
preview.** The preview is complete enough that a human can execute it without
re-deriving anything:

```json
{"data": {"dry_run": true, "op_id": "OP-ALERTS-INSERT-PARSED-ALERTS", "access_class": "write",
          "request": {"method": "POST", "path": "/public_api/v1/alerts/insert_parsed_alerts",
                      "body": {...}, "query": null},
          "consent_required": {"flag": "CORTEXSIM_XSIAM_ALLOW_WRITE=1",
                               "consent": {"write_authorized": true},
                               "dry_run": false},
          "execute_as_human": "curl -X POST http://127.0.0.1:8888/api/xsiam/tenants/<name>/operations/OP-ALERTS-INSERT-PARSED-ALERTS -d '{...,\"dry_run\":false,\"consent\":{\"write_authorized\":true}}'"},
 "evidence": {"basis": "preview", "queries_issued": 0, "http_round_trips": 0,
              "statement": "Preview only. Nothing was sent to the tenant. tenant-verified: 0"}}
```

If the agent passes `dry_run: false` on a write-class op, the tool returns the
error `MCP_WRITE_NOT_EXECUTABLE` with the preview attached — not a silent
preview, because silently downgrading an explicit request is the tolerant fix
that hides the bug. The REST route's own `XsiamConfigError` is never the thing
that stops it: the MCP layer refuses **before** building the request, and a
test asserts the stub transport saw zero calls even with both global flags set
to `True` and consent in the arguments.

**v1.1, Henry's call (§14):** a human-held `CORTEXSIM_MCP_WRITE_TOKEN`. The
agent would have to pass `write_token` per call; the server compares it in
constant time; the global flag must also be on; the ledger is charged. The
agent cannot know the token unless the human pastes it into that one call.
Destructive stays preview-only even then. This is deferred, not rejected: the
first consumer is Claude Code on a laptop, where the human is in the loop and
the console is one tab away.

---

## 7 · The result envelope

Every tool returns `Envelope[T] = {data: T, evidence: Evidence}` as
`structuredContent`, and the same evidence as the first line of
`content[0].text` so a client without structured-output support still sees it.

```
Evidence
  basis           authored | configured | reachable | verified | preview | simcore | derived
  queries_issued  int|null   # tenant OPERATIONS this call caused — the ledger's unit, summed over REST calls; null = not reported, never 0
  tenant_answered bool       # the tool got the tenant's reply back; independent of queries_issued (a poll answers at 0)
  http_round_trips int|null  # HTTP requests to the tenant, when the route reports them; null otherwise
  tenant          str|null   # vault name; never URL, never key id
  ledger          {key, used_today, budget, remaining, breaker_open, breaker_until}|null
  tenant_verified int        # computed, today 0
  statement       str        # one sentence in the console's words
  as_of           iso8601    # SimCore's clock, UTC
  truncated       bool
```

`basis` is the readiness ladder with three additions. The four rungs mean what
the console says they mean: *authored* (SimCore knows about it), *configured*
(a credential exists; no call was made), *reachable* (a tenant answered this
call), *verified* (a run or assertion verdict carries tenant provenance).
`simcore` is SimCore's own state (a run row, a health report); `preview` is a
dry run; `derived` is computed from a tenant reply with the transformation
stated in `data`.

**Statement rules** — copied from the console's Readiness wording, not
paraphrased:

| condition | statement |
|---|---|
| `queries_issued == 0`, basis not preview | "No tenant answered this call. This is `<basis>`, not tenant evidence. tenant-verified: N" |
| preview | "Preview only. Nothing was sent to the tenant. To execute, a human runs the request in `execute_as_human`. tenant-verified: N" |
| tenant answered (`tenant_answered`, or `queries_issued >= 1`) | "Tenant `<name>` answered; this call cost `<queries_issued>` metered operation(s). This is *reachable* evidence; a detection is *verified* only when a run verdict carries tenant provenance. tenant-verified: N" — `<queries_issued>` may be 0 (a results poll), stated as such |
| `queries_issued` is null | "SimCore accepted the request; whether the tenant was called and how many operations it issued is not known — this is not zero. tenant-verified: N" |
| not backed | "`<id>` has no backing operation in the XSIAM public API; nothing was queried. tenant-verified: N" |

**Where the numbers come from.** The MCP layer computes none of them:

- **The unit of `queries_issued` is the ledger's unit: one tenant
  operation.** One catalog op execution is 1; one XQL query is 1 including
  every `get_query_results` poll it needs (the polls ride on the query's
  budget — `HTTP_CALLS_PER_QUERY_WORST_CASE = 22` is the documented
  multiplier, and `OP-XQL-GET-QUERY-RESULTS` executed on its own is 0); one
  reconcile pull page is 1 and the optional incidents read is 1. This is what
  `verify` already reports (`budget["issued"]`) and what the ledger charges,
  so the number on the honesty line is the number on the budget. Preflight
  counts HTTP round trips; it keeps its own field and the envelope carries it
  as `http_round_trips`, which is `null` on every route that does not report
  one — never a guess. The MCP layer reads these off each REST response and
  sums; Appendix D adds `queries_issued` to the executor route (1 live, 0
  preview, 0 for a results poll), the XQL routes and the reconcile route.
- `ledger`, `tenant_verified` and the integration inventory come from one new
  zero-outbound route, `GET /api/readiness` (Appendix D item 6). Nothing
  reachable over HTTP carries them today: `GET /api/connectors` has no ledger,
  `connector_health_component` is built but not mounted, and nothing counts
  tenant-verified server-side. Putting them on a route keeps D-1 and G1 intact
  for the stdio process and gives the console a backend source for the ladder
  it currently derives in JavaScript. For SimCore-only tools `ledger` is
  `null`, not `{used: 0}`.
- `tenant_verified` is computed by one Python function,
  `core/connectors/provenance.py::count_tenant_verified(session)`, lifted from
  the console's `verdictProvenance` logic (runs and assertion runs whose
  verdict detail carries tenant provenance; `pending` / `not_applicable` do not
  count), and served by `GET /api/readiness`. The console keeps its
  JavaScript; a fixture test asserts the two agree on the same fixture set,
  and an AST guard asserts the Python function has exactly one definition, as
  the substrate guards do.

**Per-process rate limit.** The per-tenant daily ledger already bounds every
path. The MCP layer adds only `CORTEXSIM_MCP_MAX_TENANT_CALLS_PER_MINUTE`
(default 30) as a process-local token bucket over tenant-class tools; on
exhaustion it returns `MCP_RATE_LIMITED` with `retry_after_seconds`, and
SimCore-only tools keep working. There is no per-session counter because the
HTTP transport is stateless by decision (§4.2).

---

## 8 · Error contract

SimCore errors are already `{"error", "code", "detail"}`. The MCP layer maps
them to a tool result with `isError: true`, `content[0].text = "<code>: <error> — <detail>"`
and `structuredContent = {error, code, detail, remediation, evidence}`. It never
lets a bare exception escape, because the SDK's generic wrapper would drop the
code.

Codes this layer adds (all others are SimCore's or `codes.py`'s, passed
through):

| code | when |
|---|---|
| `MCP_WRITE_NOT_EXECUTABLE` | `dry_run: false` on a write/destructive op through MCP (preview attached) |
| `TOOL_NOT_BACKED` | one of the four registered-but-unbacked consumer tools |
| `NO_TENANT_CONFIGURED` / `TENANT_AMBIGUOUS` | §5.4 |
| `XQL_WRITE_STAGE_REFUSED` | §5.3 |
| `MCP_RATE_LIMITED` | §7 |
| `SIMCORE_UNREACHABLE` | stdio mode, `CORTEXSIM_URL` did not answer — distinct from any tenant error, so "SimCore is down" never reads as "the tenant is down" |
| `SIMCORE_SHAPE_UNKNOWN` | a REST response without the fields the tool reads — raised, never read as empty |

A `truncated` list is not an error; it is a flag in the envelope.

---

## 9 · SDK pin and dependency impact

| | `mcp==1.30.0` | `mcp==2.3.0` |
|---|---|---|
| API | `from mcp.server.fastmcp import FastMCP`; `mcp.streamable_http_app()`; lifespan enters `session_manager.run()` | `from mcp.server import MCPServer`; transport config moved to `run()`; mounting disables the built-in lifespan |
| forces on CortexSim | `uvicorn` 0.29.0 → ≥ 0.31.1 · `httpx` 0.27.0 → ≥ 0.27.1 | `uvicorn` → ≥ 0.31.1 |
| adds | `httpx-sse`, `sse-starlette ≥ 1.6.1`, `pyjwt[crypto]`, `python-multipart`, `anyio ≥ 4.5` | `httpx2` (separate package, coexists with `httpx`), `httpcore2`, `mcp-types`, `opentelemetry-api`, `truststore`, `sse-starlette ≥ 3`, `pyjwt[crypto]`, `python-multipart` |
| matches the consumer | **yes** — `@modelcontextprotocol/sdk` 1.30.0 | protocol-compatible, SDK-line different |
| maintenance | last 1.x line | current |

**Recommendation: `mcp==1.30.0`** for v1. Same line as the consumer, the
smaller dependency delta, and the migration to 2.x is an import-path rename
plus moving transport settings — mechanical when the consumer moves. The pin
is Henry's to confirm (§14) because it carries the `uvicorn` bump, which
touches the image's `CMD`.

What the builder must prove, not assume: `pip install` of the new
`core/requirements.txt` resolves inside `python:3.11-slim` against the
untouched pins (`starlette==1.6.0`, `pydantic==2.13.4`,
`pydantic-settings==2.15.0`, `jsonschema==4.23.0`), SimCore boots on the bumped
`uvicorn`, and `make build` is green. `sse-starlette ≥ 3` requires Python
≥ 3.11, which the image satisfies.

---

## 10 · Phase 2 acceptance — the tests

Reuses `tests/integration/xsiam/test_operations_api.py`'s pattern
(`_build_app` + `_StubClient` recording `request()` calls +
`monkeypatch.setattr(xsiam_api, "load_xsiam_client", …)`), with the MCP server
driven over `httpx.ASGITransport` or an in-memory stdio pair.

1. **A write tool with every key set still previews.** Global flags
   monkeypatched `True`, `consent.write_authorized: true` and `dry_run: false`
   in the tool arguments → `MCP_WRITE_NOT_EXECUTABLE`, preview attached, and
   **the stub recorded zero `request()` calls**. Gate A evidence: the test is
   written first against a pass-through implementation and observed red.
2. **A read tool routes through the catalog executor and the traversal
   guard.** `xsiam.operation.execute` with `path_params={"group_id":
   "../delete/123"}` → the executor's own "must be a single path segment"
   text; an ordinary id reaches the stub at the expected path (mirrors
   `test_operations_path_params.py`).
3. **The server boots inside the prod image.** `make check-mcp-boot`:
   `docker run --rm --entrypoint sh $(IMAGE) -c 'python -m mcp_server --list-tools'`
   prints ≥ N tools, plus an ASGI test that, with `CORTEXSIM_MCP_HTTP=1`,
   `initialize` → `tools/list` returns the registered set. No host mount, as
   `check-agent-shelf` does it.
4. **Envelope on everything.** Every registered tool's result carries
   `evidence`; `queries_issued` equals the number of ledger-charged
   operations the stub recorded (an XQL start with three polls is 1); a
   preview is 0; a SimCore-only tool has `ledger: null` and
   `http_round_trips: null`.
5. **Naming.** Every canonical id round-trips through `naming.py`, matches the
   wire pattern, fits 48 characters.
6. **Not backed means an error.** The four unbacked tools return
   `TOOL_NOT_BACKED`, never `[]`.
7. **Tenant resolution.** Zero / one / many credentials → the three outcomes
   of §5.4.
8. **XQL guard.** `target type = dataset` is refused; `| limit 2000` in the
   text is not rewritten, and the results call carries `limit ≤ 1000`.
9. **Provenance parity.** `count_tenant_verified` agrees with the console's
   `verdictProvenance` on one fixture set; the AST guard sees one definition.
10. **Health states.** Flag off → `ok` with `mounted: false` and
    `code: MCP_DISABLED`; flag on with the SDK import broken or zero tools →
    `degraded` with a code; flag on → `ok` with `mounted: true` and the
    count. The existing vocabulary test keeps passing unchanged.
11. **Non-UTC.** The suite also passes under `TZ=America/Los_Angeles`
    (`make test-backend-non-utc`); `as_of` carries an offset.
12. **Manifest.** `python -m mcp_server --manifest` emits a document that
    validates against the consumer's P-2 shape (fields copied into a local
    schema, since the consumer repo is not a dependency).
13. **Launch projection is total.** Every field of `LaunchRequest` is in
    `McpLaunchRequest` or in `MCP_LAUNCH_EXCLUDED`; a launch through MCP with
    `allow_runtime_install: true` or any excluded consent key is rejected
    before the REST call, and the stub SimCore saw no `POST /api/runs`.
14. **Schema drift.** Each model in `mcp_server/schemas.py` has the same
    field set as its REST original (D-13); the test imports both sides.
15. **Capabilities gate.** A credential with `capabilities: ["read_alerts"]`
    gets `XSIAM_CAPABILITY_MISSING` from `xsiam.xql.query` and from the REST
    XQL route alike, with zero `request()` calls on the stub; adding `"xql"`
    lets both through. Observed red before the executor change lands.
16. **Readiness route.** `GET /api/readiness` makes no outbound call (the
    stub transport records nothing), carries `server_time` with an offset,
    and `ledger` matches the `ledger.snapshot()` for the same key.

---

## 11 · Documentation and console touchpoints

- `docs/reference/api-and-agent-surface.md` gains a section "MCP server"
  (transport, the tool table of Appendix A, the envelope, the codes).
- `CHANGELOG.md` `[Unreleased]`.
- `.env.example`: `CORTEXSIM_MCP_HTTP`, `CORTEXSIM_MCP_ALLOWED_HOSTS`,
  `CORTEXSIM_MCP_MAX_TENANT_CALLS_PER_MINUTE`, `CORTEXSIM_URL` (stdio only).
- `README.md`: the two `claude mcp add` lines (Appendix C).
- `python -m mcp_server --manifest` emits the P-2 `manifest.yaml`
  (`manifest_version: 2, kind: mcp-server, id: core/xsiam`, scopes
  `dataset.read` / `xql.execute` / `content.read` / `alert.read` mapped per
  tool, `class`/`effect` per tool, `target: xsiam`). The consumer vendors it;
  this repo does not write into that repo.
- Console: optional `MCP: mounted | disabled` chip on Readiness, reading the
  new health component. Not required for acceptance; listed so it is not
  forgotten.

---

## 12 · Decisions made here

- **D-1** The tool layer is a REST client of SimCore; it never imports
  `core.api`. One implementation, two transports.
- **D-2** Package is `core/mcp_server/`, because `core/mcp/` shadows the SDK
  under `PYTHONPATH=core`.
- **D-3** Streamable HTTP mount is opt-in (`CORTEXSIM_MCP_HTTP`, default off),
  stateless, with the SDK's host allowlist seeded from
  `CORTEXSIM_MCP_ALLOWED_HOSTS`.
- **D-4** Canonical ids are dotted in the consumer's style; wire names are
  derived by one mapping and must fit 48 characters.
- **D-5** Writes and destructives through MCP always preview; `dry_run: false`
  on them is an error with the preview attached, not a silent downgrade.
- **D-6** No tool takes a URL or credential; tenants resolve by vault name
  with the zero / one / many rule.
- **D-7** `xsiam.api.get` is not offered; the catalog is the allowlist.
- **D-8** The four consumer tools without a backing operation are registered
  and return `TOOL_NOT_BACKED`.
- **D-9** `cortexsim.run.launch` takes a projection of `LaunchRequest`: of
  the four consent keys only `simulation_authorized` is settable; runtime
  install and the prevention attestation fields are excluded, and a test
  forces every new REST field onto one side or the other.
  `cortexsim.run.abort` is allowed as a destructive tool.
- **D-10** The envelope's numbers originate in SimCore responses (Appendix D
  adds `queries_issued` + ledger to the routes that lack them); the MCP layer
  only sums.
- **D-11** `tenant_verified` gets one Python definition with a parity test
  against the console's JavaScript.
- **D-12** Recommended pin `mcp==1.30.0` (Henry confirms, §14).
- **D-13** Tool input models live in `core/mcp_server/schemas.py` as
  **copies** of the REST request models (`ScopeRequest`,
  `OperationExecuteRequest` minus `consent`/`dry_run` for write ops,
  `XqlRequest`, and the `LaunchRequest` projection of D-9), with a drift test
  that imports both sides and asserts field-set equality (or projection
  totality). Re-exporting would import `core.api.*`, which drags in
  `Settings()`, the database and the orchestrator — forbidden by D-1 and
  unavailable to the stdio process. The drift test is what D-13 existed to
  buy; the copy is the price of G1.
- **D-14** Tools only in v1; no resources or prompts.
- **D-15** `queries_issued` is in ledger units (tenant operations), matching
  what `verify` reports and what the ledger charges; `http_round_trips` is a
  separate nullable field carried only where a route reports it.
- **D-16** One new zero-outbound route, `GET /api/readiness`, serves the
  integration inventory, per-tenant ledger snapshots, `tenant_verified` and
  `server_time`, so the stdio process never computes evidence in-process.
- **D-17** The `mcp` health component reports `ok` + `mounted: false` +
  `MCP_DISABLED` when the mount is off, and `degraded` when it is on but
  empty or broken; the status vocabulary is not extended.
- **D-18** The REST executor and XQL routes refuse XQL-class operations when
  the credential's `capabilities` lacks `"xql"` (`XSIAM_CAPABILITY_MISSING`
  in `codes.py`); `xsiam.session.index` derives `scopes_held` from
  `capabilities`, not from credential kind.

---

## 13 · Left to the builder

- The exact SDK class for the host allowlist (`TransportSecuritySettings` in
  1.x) and where the lifespan enters `session_manager.run()` in
  `core/main.py` — follow the pinned SDK's docs, not memory.
- Whether to add `status` / `scenario_id` / `limit` params to `GET /api/runs`
  or keep the client-side filter in `cortexsim.run.list`. Adding them is the
  better fix; it is a small REST change and belongs in this branch if done.
- The polling cadence and ceiling for `xsiam.xql.query` (the client's
  `run_xql` defaults, `max_wait=30`, `interval=1.5`, are the starting point;
  `HTTP_CALLS_PER_QUERY_WORST_CASE = 22` is the number to keep honest).
- The upper bound on `timeframe` for `xsiam.xql.query` (7 d is a reasonable
  default cap; it is a cost knob, not a safety one).
- Whether `_meta` on the tool result should also carry `evidence` for clients
  that read meta. Harmless if yes.
- MCP resources for v1.1: `cortexsim://scenario/{id}` and
  `cortexsim://run/{id}/report` are natural, read-only, and need no new
  honesty rule.
- The `check-mcp-boot` target's place in `make ci` — it belongs in the job
  that builds the image, not a job that can be skipped.
- Log line format for tenant-class tool calls (tool id, tenant name,
  `queries_issued`, duration); never the body.
- The `/mcp` mount must be registered before the catch-all
  `app.mount("/", …)` that serves the console in `core/main.py`, or the
  static mount answers first.
- `CORTEXSIM_URL` and the three `CORTEXSIM_MCP_*` variables must be `Settings`
  fields as well as `.env.example` lines, or `tests/test_config.py` fails on
  the unknown key.
- The ledger's timestamps are naive `utcnow()`; the readiness route and the
  envelope pass them through `coerce_utc` before serialising.
- The shape of `GET /api/readiness` beyond the fields §7 requires (per-rung
  `proves` / `does_not_prove` text can be lifted verbatim from
  `connectorState.js`).

---

## 14 · Decisions Henry owes

Most important first.

1. **SDK line.** `mcp==1.30.0` (recommended: matches the consumer, smaller
   delta) or `mcp==2.3.0` (current line). Either way `uvicorn` moves from
   0.29.0 to ≥ 0.31.1; 1.x also moves `httpx` to ≥ 0.27.1.
2. **Writes through MCP.** v1 preview-only as designed, or include the v1.1
   human-held `CORTEXSIM_MCP_WRITE_TOKEN` path now. The design argues for
   preview-only first; the token is the only acceptable shape for a later
   write path.
3. **HTTP mount posture.** Default-off as designed, or default-on in the
   compose stack with the allowlist at `localhost` only. Default-off is the
   conservative call because the inherited trust boundary has no auth.
4. **`xsiam.ingest.status`.** Leave not-backed (honest) or spend tenant time
   to validate an XQL over the source-status datasets so it can ship as
   `derived`. That validation is the first tenant-verified work this repo
   would ever record, and the spec does not want to pretend it happened.
5. **Vocabulary ownership.** This spec treats `cortex-pov-agent`'s
   `xsiam.*` ids as canonical and adds `xsiam.incident.list`,
   `xsiam.playbook.list`, `xsiam.healthcheck`. If that repo should instead
   adopt CortexSim's names, the mapping in `naming.py` is the only place that
   changes, but the manifest the consumer vendors would need re-cutting.

**Calls made here that Henry may veto** (settled so the builder is not
blocked; each is one decision line in §12):

- **D-18, the `capabilities` gate**, changes REST behaviour for an existing
  caller: a credential saved without `"xql"` in `config.capabilities` will
  stop answering the console's XQL panel and `GET /tenants/{name}/metrics`
  until the capability is added. That is the doctrine the preflight already
  states; the executor just did not enforce it.
- **D-17, `mcp` off reads `ok`**, because the status vocabulary is pinned in
  three places and an opt-in feature left off is not a degraded zero.
- **D-15, ledger units** for `queries_issued`, which makes the honesty line
  say "1 operation" for an XQL query that cost up to 22 HTTP calls; the
  multiplier is documented, not hidden, and `http_round_trips` carries the
  other number where a route has it.

---

## Appendix A · Full tool table

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

41 registered, 37 backed, 4 honest refusals. The longest wire name is 29
characters.

## Appendix B · Envelope examples

> **Erratum (corrected during implementation).** The "stdio mode, SimCore down"
> example below originally showed `tenant_verified: 0`. When SimCore did not
> answer, readiness was never read, so a stated 0 was a claim nobody measured.
> `Evidence.tenant_verified` is `null` when readiness could not be read, and the
> statement ends `tenant-verified: not read`; `queries_issued` is likewise `null`
> when the request reached SimCore and the count was not reported (not zero).
> *(Final fix wave, 2026-11-10.)* "Tenant answered" is its own field,
> `tenant_answered`, set by the tool when the tenant's reply came back; it no
> longer rides on `queries_issued >= 1`, because a results poll answers at a
> metered cost of 0. The null-count sentence no longer claims the tenant was
> called: "SimCore accepted the request; whether the tenant was called and how
> many operations it issued is not known — this is not zero".
> Source of truth: `core/mcp_server/envelope.py`.

**A read that a tenant answered**

```json
{"data": {"datasets": [{"name": "xdr_data", "kind": "system"}], "truncated": false, "total": 1},
 "evidence": {"basis": "reachable", "queries_issued": 1, "tenant_answered": true, "http_round_trips": null, "tenant": "deathstar",
              "ledger": {"key": "api-deathstar.xdr.us.paloaltonetworks.com", "used_today": 7,
                         "budget": 500, "remaining": 493, "breaker_open": false, "breaker_until": null},
              "tenant_verified": 0,
              "statement": "Tenant deathstar answered; this call cost 1 metered operation. This is reachable evidence; a detection is verified only when a run verdict carries tenant provenance. tenant-verified: 0",
              "as_of": "2026-11-02T18:04:11+00:00", "truncated": false}}
```

**A SimCore-only read**

```json
{"data": {"scenarios": [...], "truncated": true, "total": 193},
 "evidence": {"basis": "authored", "queries_issued": 0, "http_round_trips": null, "tenant": null, "ledger": null,
              "tenant_verified": 0,
              "statement": "No tenant answered this call. This is authored, not tenant evidence. tenant-verified: 0",
              "as_of": "2026-11-02T18:04:12+00:00", "truncated": true}}
```

**A not-backed consumer tool** (`isError: true`)

```json
{"error": "xsiam.parser.list has no backing operation", "code": "TOOL_NOT_BACKED",
 "detail": "the XSIAM public API exposes no parser listing; nothing was queried",
 "remediation": "author a pack under core/integrations/xsiam/operations/packs/ if an endpoint exists",
 "evidence": {"basis": "authored", "queries_issued": 0, "http_round_trips": null, "tenant": null, "ledger": null,
              "tenant_verified": 0, "statement": "xsiam.parser.list has no backing operation in the XSIAM public API; nothing was queried. tenant-verified: 0",
              "as_of": "2026-11-02T18:04:13+00:00", "truncated": false}}
```

**stdio mode, SimCore down** (`isError: true`)

```json
{"error": "SimCore did not answer", "code": "SIMCORE_UNREACHABLE",
 "detail": "GET http://127.0.0.1:8888/api/health: connection refused",
 "remediation": "start SimCore (./scripts/dev-up.sh) or set CORTEXSIM_URL; this is not a tenant error",
 "evidence": {"basis": "simcore", "queries_issued": 0, "http_round_trips": null, "tenant": null, "ledger": null,
              "tenant_verified": null, "statement": "No tenant answered this call. SimCore itself was unreachable. tenant-verified: not read",
              "as_of": null, "truncated": false}}
```

## Appendix C · Client install

Streamable HTTP, when the mount is on (zero install on the laptop):

```bash
claude mcp add --transport http cortexsim http://localhost:8888/mcp
```

stdio, from the running container (no Python on the laptop needed):

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

Never pass a tenant URL or key in `-e`; the server does not read them.

## Appendix D · REST changes this spec requires

All small, all in the executor's spirit, all in this branch:

1. `POST /api/xsiam/tenants/{name}/operations/{op_id}` returns
   `queries_issued` (1 live, 0 preview, 0 for `OP-XQL-GET-QUERY-RESULTS`
   because a poll rides on its query's unit) and `ledger` (snapshot by
   `tenant_ledger_key`); for `OP-XQL-START-XQL-QUERY` it calls
   `ledger.check` before and `ledger.charge` after, mapping
   `QuotaBreakerOpen` / `DailyBudgetExhausted` to `XSIAM_QUOTA_ERROR` (429).
   Today this route charges nothing, so an agent could run XQL past the daily
   budget that the verifier respects. It also refuses XQL-class operations
   with `XSIAM_CAPABILITY_MISSING` (403, new in `codes.py`) when the
   credential's `config.capabilities` does not list `"xql"` (§5.6).
2. `POST /api/xsiam/tenants/{name}/xql` and `GET …/xql/{query_id}` adopt
   the same three things — the `target type = dataset|lookup` refusal, the
   ledger check/charge on start, and the `capabilities` gate — so each guard
   is one definition used by both the REST routes and the MCP tools, and the
   REST XQL path stops being the one unbudgeted way to query a tenant.
3. `POST /api/runs/{id}/reconcile` reports `queries_issued` (one per tenant
   pull it made). `/verify` already does (`connectors/service.py:812`); the
   MCP tool reads it off unchanged.
4. `GET /api/health` gains the `mcp` component (§4.5).
5. Optional: `GET /api/runs?status&scenario_id&limit` (§13).
6. **New `GET /api/readiness`** — zero outbound calls, like `/api/health`.
   Returns `{server_time, tenant_verified, integrations: [{name, kind,
   capabilities, last_verified_ok, last_verified_at, last_verified_error,
   ledger: {…}|null, rungs: {authored, configured, reachable, verified}}],
   proves: {...}}`, where `ledger` mounts the already-written
   `connector_health_component` snapshot keyed by `tenant_ledger_key`, and
   `tenant_verified` is `provenance.count_tenant_verified(session)`. Never
   credential values. This is the one route `cortexsim.readiness.get` and
   `xsiam.session.index` read, and the backend source the console's Readiness
   view can adopt later.

Each is a Gate A change in its own right: the guard is observed failing
before the fix, in the commit message.

# cortex-bot — code review and POV integration determination

> **Audience:** Henry (Cortex DC leadership) and whoever picks up the tenant-interaction
> surface in CortexSim or `cortex-pov-agent`.
>
> **Status (updated 2026-11-02, evening):** items 1 and 2 of §2.7 are built on two topic
> branches cut from `dev`, unpushed, no PR. See §2.10 for what changed from this review.
>
> **Original status:** review complete, nothing built. Every claim below is from reading the two
> trees side by side on 2026-11-02 (cortex-bot `v1.2.0`, commit as cloned that day;
> CortexSim `main` @ `1ad8a88`, the v1.1.0 release). No tenant was touched.
> ***tenant-verified is still 0*** and nothing in this note moves it.
>
> **Origin:** "optimize the tenant interaction from the API and the MCP; a Cortex DC has
> built a cortex-bot with a bunch of tools for the MCP; review it and determine how we
> can integrate it into the POV." Repo: `github.com/alexpekarovsky/cortex-bot`
> (Apache 2.0, DC-authored, public).

---

## TL;DR

cortex-bot is **114 MCP tools** (42 modules) layered as `custom_components/` on top of the
**official PANW Cortex MCP Server** (the console-downloaded zip, Python 3.13+). It cannot
run without that server on disk and it imports five of its internals. Of the 63 distinct
tenant API paths it calls, **39 are already in CortexSim's 116-operation catalog** with an
`access_class` and a gated executor; **24 are not**, and those 24 are where the value is
(alert read-back on the v2 path, contributing events, tenant licence info, cases/issues,
ITDR risky entities, MDR reports, vulnerability management, triage presets).

Its safety model is **advisory**: the documented `ENABLE_DESTRUCTIVE_TOOLS` switch is not
implemented anywhere in the code; the real gate is a boolean parameter the LLM fills in;
the install guide recommends an Instance Administrator key; the test harness creates real
issues and correlation rules on whatever tenant it is pointed at.

**Determination.** Do not take cortex-bot as a dependency and do not run it inside
SimCore. **Harvest** its field-observed API facts into CortexSim's catalog, connector and
preflight (days of work, low risk). Then **expose CortexSim's own already-gated tenant
surface as an MCP server** for the DC's agent, read-only by default, so the agent gets
the convenience without a second, ungoverned write path to a prospect's tenant. The
natural consumer is `cortex-pov-agent`, whose `mcp/xsiam/manifest.yaml` is an open
prerequisite (P-2) with the tool vocabulary already specified.

---

## 1 · Executive summary

A Cortex Domain Consultant has published an open-source assistant layer that lets an AI
agent operate a customer's Cortex tenant through roughly a hundred operations. About a
third of those operations change tenant state: they can isolate hosts, run scripts on
endpoints, author or delete detection rules, open cases, and post commands into the
case war room. It was built fast against live tenants, which gives it something the POV
engine lacks today: observed knowledge of how the tenant API actually behaves, as
opposed to how the documentation says it behaves. That knowledge is worth taking.

The layer's protections against misuse are not. The documented master switch that is
supposed to disable destructive operations does not exist in the code. The real check is
a yes/no flag that the AI itself sets, which means a poisoned input to the AI is one step
from an endpoint isolation on a prospect's production estate, with no approval record.
The installation guide asks for an administrator-level key, and the self-test writes
real records into the customer's console.

The recommendation is to keep the two systems apart and move value in one direction only.
First, within days, fold the field-observed facts into the POV engine's existing
catalog and pre-flight checks, so a POV finds out before the customer is watching
whether its alert read-back path, licence entitlements and query shapes match this
tenant. Second, give the DC's AI agent the same tenant operations through the POV
engine's own door, which already defaults to read-only, previews every write, and
records consent, rather than through a separate ungoverned path. The risk of doing
nothing is concrete: a DC runs both side by side, an AI-driven write lands on a
customer tenant with no approval trail, or an AI-fetched number gets quoted as POV
evidence without passing through the engine's scoring, and the coverage claim in the
readout is unbacked.

---

## 2 · Technical detail

### 2.1 What cortex-bot is

| Fact | Value | Where |
|---|---|---|
| Shape | `custom_components/*.py`, 48 files, 42 `*Module(BaseModule)` classes | tree |
| Registered tools | **114** (`grep -c "self._add_tool("`); README says 116, `test_all_tools.py` says 111, INSTALL.md says 106/112/90 | counts drift |
| Runtime dependency | the **official PANW Cortex MCP Server** must be on disk; discovered via `CORTEX_MCP_SERVER_PATH`, importable `src.main`, or `~/.cortex-mcp/src`, `~/cortex-mcp/src`, `/opt/cortex-mcp/src`, `./src` | `__main__.py` |
| Official-server internals imported | `usecase.base_module.BaseModule` (43 files), `pkg.util.create_response` (41), `usecase.fetcher.get_fetcher` (33), `entities.llm_config.LLM_FORMATTING_BASE_INSTRUCTIONS` (5), `pkg.client.PAPIClient` (1), `entities.exceptions.PAPI*Error` | grep |
| Python floor | 3.12+ for cortex-bot, **3.13+** for the official server it needs | `pyproject.toml`, PANW docs |
| Auth | Standard auth only (`CORTEX_MCP_PAPI_AUTH_HEADER` raw key + `..._AUTH_ID`); no advanced/signed mode | `.env.example` |
| Transport | stdio default; Streamable HTTP via the official server | `.env.example` |
| Tests / CI | no test suite; CI = `ruff` on `E9,F63,F7,F82` + `compileall` | `.github/` |
| Licence | Apache 2.0 | `LICENSE` |

The official server itself is distributed as a zip from the tenant console (Settings →
Configurations → Integrations → Cortex MCP Server), ships `builtin_components` that an
`update` overwrites, and documents that the API key is read+write and must follow least
privilege (Viewer is pre-populated). cortex-bot's own INSTALL.md asks for **Instance
Administrator**.

### 2.2 Review findings

Severity is in the POV context: the tool is pointed at a **prospect's production tenant**
by a DC during a sales motion.

| # | Sev | Finding | Evidence |
|---|---|---|---|
| F1 | **HIGH** | The documented kill switch does not exist. `ENABLE_DESTRUCTIVE_TOOLS` appears only in `.env.example`, `SECURITY.md`, `README.md`; in **no** `.py` file. Setting it to `false` changes nothing. | `grep -rn ENABLE_DESTRUCTIVE_TOOLS` |
| F2 | **HIGH** | The real gate is a per-tool parameter `confirm_destructive_action: bool = False` that **the LLM fills in**. No server-side policy, no allow-list, no audit record, no second channel. A poisoned tool response (exactly the trust gap `SIM-KOI-006` in this repo simulates) is one argument away from `endpoints/isolate`. Present in `allowlist_files`, `blocklist_files`, `correlation_rules`, `endpoint_tools`, `file_tools`, `playbook_api`, `script_tools`, `terminate_process`. | each module's early-return `{"error": "Destructive action not confirmed", "risk_level": "HIGH"}` |
| F3 | **HIGH** | `test_all_tools.py` (1 359 lines, the documented "validate your install" step) **creates a real issue** ("Tool Testing Workspace", tags `ai-workspace,tool-testing,automated`) plus test correlation rules and BIOCs on the tenant it is pointed at. `create_issue.py` separately opens a MEDIUM "AI Workspace" issue as the agent's scratch-pad, then sleeps 5 s to resolve the numeric id. On a prospect's tenant that is visible noise in their case queue. | `test_all_tools.py`, `create_issue.py` |
| F4 | **HIGH** | Credential blast radius. INSTALL.md recommends an Instance Administrator role (official guidance says Viewer, least privilege); the raw key lives in `.env` and in the MCP client's JSON; only Standard auth is supported, so there is no signed-request option. | INSTALL.md, `.env.example` |
| F5 | MEDIUM | `sdk_base.py` sets `env["DEMISTO_VERIFY_SSL"] = "false"` for **every** `uvx demisto-sdk` subprocess (content upload, pack generation). TLS verification is off for all SDK traffic to the tenant. Bare `except:` at line 59. | `sdk_base.py` |
| F6 | MEDIUM | `client_patch.py` monkey-patches `PAPIClient.request` globally, wrapping dict replies in a `DictResponse(dict)` with a fake `status_code = 200` and a no-op `raise_for_status()`. Every HTTP-200 application-error envelope (`err_code`/`err_msg`) now looks like success to anything that checks status. | `client_patch.py` |
| F7 | MEDIUM | *Tolerance hides bugs.* `discover_dataset_schema` polls 12×5 s; on timeout it falls through to `reply.get("results", {}).get("data", [])` and reports "exists but has no data". A slow tenant reads as an empty dataset. (CortexSim's `result_rows` exists precisely to refuse this; it raises on an unrecognised envelope.) | `xql_query.py` |
| F8 | MEDIUM | `install.sh` does `find ~ -path "*/cortex*/src/main.py"`, `cp -r` into the official server's tree (which the server's own `update` then overwrites) and `pkill -f "cortex.*main.py"`. Expected file counts in the script (28 py / 25 yaml) are stale. | `install.sh` |
| F9 | LOW | No tests of any kind; CI proves the files compile. | `.github/workflows` |
| F10 | LOW | Tool count is stated five different ways across README / INSTALL / test harness / code. | docs vs `grep` |
| F11 | LOW | `run_xsoar_automation` posts **any** `!command` into the War Room and polls for artifact entries. Useful; also an arbitrary-automation primitive behind the same F2 gate. | `run_xsoar_automation.py`, `war_room.py` |

**What is genuinely good, and why it matters to us**

| # | Strength | Why it matters |
|---|---|---|
| S1 | **Field-observed API facts** (next section). Every one was learned against a live tenant, which is more than any CortexSim path can say. | *tenant-verified is 0.* cortex-bot's quirks list is the cheapest tenant-truth we can get before our first live run. |
| S2 | LLM-facing tool descriptions carry workflow guidance ("call X first, then Y", "this 500s on external alerts"). | The pattern to copy when CortexSim projects its catalog as MCP tools. |
| S3 | Licence detection via `/public_api/v1/system/get_tenant_info` (`identity_threat_expiration == 0` ⇒ ITDR not licensed). | `/api/pov/scope` today scopes against **canned profiles**; this makes entitlement *observed* instead of *declared*. |
| S4 | War Room command pattern (`entries/insert` → poll `entries/get` for `category == "artifact"`). | The only documented way to run XSOAR automations from the API. Keep it in the catalog as `write`, gated. |

### 2.3 Field-observed API facts worth harvesting

These are the specific things cortex-bot knows that the CortexSim tree does not.

| Fact | cortex-bot source | CortexSim today | Why it is load-bearing |
|---|---|---|---|
| Alert read-back works on **`/public_api/v2/alerts/get_alerts_multi_events`** | `misc_tools.py` | `XsiamConnector._ALERTS_PATH` defaults to **v1**; `config["alerts_path"]` override exists | The reconcile sweep is the moment MTTD becomes real. If the tenant honours v2 and not v1, the sweep reads **0 alerts** and the matcher scores every seeded result `missed`: a manufactured false negative on the customer's stack. |
| `get_contributing_event` returns the events that fired an **XSIAM-native correlation** alert; **500 on external** alerts | `contributing_events.py` | not called anywhere | Lets the matcher confirm the seeded step's marker is *in* the contributing events, which is a far stronger witness than name/technique within a time window. The 500 must map to "no witness available", never to a `fail`. |
| `get_incident_extra_data` with `alerts_limit` (max 1 000) | `incident_details.py` | `OP-INCIDENTS-GET-INCIDENT-EXTRA-DATA` exists; connector does not use it | Incident → alerts → events is the stitching proof the Analytics plane scenarios claim. |
| `get_tenant_info` licence fields (`identity_threat_expiration`, etc.) | `risky_entities.py` | not in catalog | Observed entitlement for `/api/pov/scope`. |
| Issue search quirk: filtering by `id` returns **sparse records** (null `name`/`severity`); refetch by `issue_domain` derived from `DOM:` tags, ≤20 pages × 100; `FIELD_ALIASES` (`status`→`status.progress`, `detection_method`→`detection.method`, `domain`→`issue_domain`) | `get_issues_fix.py` | not in catalog | Any CortexSim path that joins on issue id will hit this. |
| Reply envelopes differ per family: Cases/Issues `reply.DATA`, Assets `reply.data`, Widgets `reply.widgets` or `reply.DATA`, Scripts `reply.scripts`; MDR `mth/child` has **three** envelopes (`{DATA,COUNT}` / `{status,data}` / bare array) with two key cases | `test_all_tools.py`, `mdr_reports.py` | one XQL unwrapper (`result_rows`) that raises on unknown shapes; nothing for the other families | Each new family in CortexSim needs its own raise-on-unknown unwrapper with these fixtures, or we repeat the `reply["results"]` bug that scored a dead detection as PASS. |
| Vuln-mgmt v2 quirks: BYOS ingest takes **no `request_data` wrapper**; `confidence` is a string; `cve_id` is a list; `last_seen` ms at asset and vuln level; status is a GET | `RELEASE_NOTES.md` v1.2.0 | not in catalog | The catalog's request-shaping assumes `{"request_data": {...}}` on POST. This family needs a per-op override. |
| XQL poll cadence: start → poll every 2 s, fail after 5 consecutive poll errors, `time_frame` regex → `{"relativeTime": ms}`, `MAX_QUERY_LENGTH = 10 000` | `xql_query.py` | `XsiamClient.run_xql` with monotonic deadline, no consecutive-error cap | Minor; the cap is a reasonable quota-ledger input. |
| Integration listing has two candidate paths (`/xsoar/public/v1/settings/integration/search`, `.../integration-search`) and falls back to War Room `!GetInstances` | `integration_discovery.py` | not needed | Note only. |

### 2.4 Overlap with CortexSim's tenant surface

Counted with the two path sets (cortex-bot: every `"/public_api/..."` literal outside the
test harness; CortexSim: every `path:` in `core/integrations/xsiam/operations/packs/*.yml`).

| Set | Count |
|---|---:|
| cortex-bot distinct tenant paths | 63 |
| CortexSim catalog paths | 116 |
| **in both** | **39** (catalog classes them 21 read · 16 write · 2 destructive) |
| cortex-bot only | 24 |
| catalog only | 77 (assets 15, xql 7, legacy_exceptions 5, distributions 5, authentication-settings 5, …) |

The 39 shared paths are already governed: `POST /api/xsiam/tenants/{name}/operations/{op_id}`
runs reads live and **previews** writes and destructives unless both the global flag
(`CORTEXSIM_XSIAM_ALLOW_WRITE` / `..._ALLOW_DESTRUCTIVE`, default off) and the per-request
consent (`write_authorized` / `destructive_authorized`) are set. Nothing to port there.

**The 24 paths cortex-bot calls that the catalog lacks**, with the class they would carry:

| Path | Class | Proposed pack | Note |
|---|---|---|---|
| `/public_api/v2/alerts/get_alerts_multi_events` | read | `alerts` | v2 of `OP-ALERTS-GET-ALERTS-MULTI-EVENTS`; see §2.3 row 1 |
| `/public_api/v1/alerts/get_contributing_event` | read | `alerts` | native correlation alerts only |
| `/public_api/v1/system/get_tenant_info` | read | `system` (new) | licence fields |
| `/public_api/v1/case/search` | read | `cases-issues` (new) | `reply.DATA` envelope |
| `/public_api/v1/issue/search` | read | `cases-issues` | sparse-record quirk |
| `/public_api/v1/issue` | **write** | `cases-issues` | creates an issue |
| `/public_api/v1/get_risky_hosts` | read | `itdr` (new) | needs ITDR licence |
| `/public_api/v1/get_risky_users` | read | `itdr` | needs ITDR licence |
| `/public_api/v1/get_triage_presets` | read | `triage` | |
| `/public_api/v1/assets/get_assets` | read | `asset-inventory` | `reply.data` envelope |
| `/public_api/v1/compliance/get_assessment_results` | read | `asset-inventory` | |
| `/public_api/v1/actions/get_action_status` | read | `response-action` | status of isolate/scan/script |
| `/public_api/v1/endpoints/terminate_causality` | **write** | `response-action` | kills a causality chain on the host |
| `/public_api/v1/entries/get` | read | `war-room` (new) | |
| `/public_api/v1/entries/insert` | **write** | `war-room` | runs any `!automation` |
| `/public_api/v1/mth/child` (get reports, get comments) | read ×2 | `mdr` (new) | three envelopes |
| `/public_api/v1/mth/child` (add comment, set status, assign) | **write** ×3 | `mdr` | same path, different bodies |
| `/public_api/v1/mth/child/get_all_reports` | read | `mdr` | |
| `/public_api/v1/widgets/get` | read | `dashboards` | |
| `/public_api/v1/widgets/insert` | **write** | `dashboards` | |
| `/public_api/v1/widgets/delete` | **destructive** | `dashboards` | |
| `/public_api/uvem/v1/get_vulnerabilities` | read | `vulnerability-management` (new) | |
| `/public_api/vulnerability-management/v1/scan` | **write** | `vulnerability-management` | triggers a scan; Cortex Cloud licence |
| `/public_api/vulnerability-management/v1/external-scans/assets` | **write** | `vulnerability-management` | BYOS ingest, no `request_data` wrapper |
| `/public_api/vulnerability-management/v1/external-scans/assets/jobs/{job_id}` | read | `vulnerability-management` | GET |

Roughly **28 operations over 24 paths: 18 read · 9 write · 1 destructive.** All satisfy the
schema's `path` rule (starts with `/public_api/`); the one with a `{job_id}` token gets a
derived `path_params` entry and the executor's existing traversal guard.

### 2.5 Safety model, side by side

| Control | cortex-bot | CortexSim |
|---|---|---|
| Read vs write classification | none in code; prose in docs | `access_class` on every op, validated at load |
| Global write kill switch | documented, **not implemented** (F1) | `CORTEXSIM_XSIAM_ALLOW_WRITE` / `_DESTRUCTIVE`, default **off** |
| Per-action consent | LLM-settable boolean (F2) | `consent.write_authorized` / `destructive_authorized` on the request, AND the global flag |
| Default for a write | executes if the boolean is true | **dry-run preview** |
| Credential kinds | one key, Instance Admin recommended | two kinds (`xsiam` read-back vs `xsiam_tenant` XQL/ops); configuring one does not authorise the other |
| URL validation | none | `tenant_url.py`, enforced at PUT |
| Quota | none | per-tenant ledger, circuit breaker on `XsiamQuotaError` |
| Unknown reply envelope | tolerated → reads as empty (F7) | raises → `pending` |
| TLS | SDK path verify=false (F5) | httpx default verify |
| Evidence path | tool output goes to the LLM | reconcile → `apply_verdicts` → `tc_verdict` + SSE; nothing else counts |
| Audit | none | `queries_issued` on every preflight; run record |

The right-hand column is the reason the integration direction is one-way.

### 2.6 Integration options, ranked

| Opt | What | Verdict | Why |
|---|---|---|---|
| **A** | **Harvest.** Add the 24 paths as catalog ops; encode the envelope and request-shape quirks as fixtures and per-family unwrappers; teach preflight to probe the alert path (v2 → v1) and `get_tenant_info`; feed observed entitlement into `/api/pov/scope`. | **Do now.** | Days of work, zero new attack surface, directly reduces the "manufactured false negative" risk in the measurement loop. Every item has a Gate-A-shaped proof (a fixture that fails without the change). |
| **B** | **CortexSim MCP server.** Project (i) CortexSim's own surface (scenarios, runs, results, readiness/preflight, UC/TC, reconcile, verify) and (ii) the catalog ops, as MCP tools, through the **same** executor, so every gate in §2.5 is inherited. Read-only by default; writes dry-run unless flags + consent. Tool ids follow `cortex-pov-agent`'s `xsiam.*` vocabulary where it has one. | **Do next**, own feature branch. | Gives the DC's agent everything cortex-bot gives, through the governed door. Makes CortexSim the `mcp/xsiam/manifest.yaml` that `cortex-pov-agent` lists as prerequisite P-2. The reference MCP Python SDK targets 3.10+, so it fits the 3.11 image; confirm the pin at build time. The **official PANW server's 3.13 floor is what rules out co-hosting *that***, not MCP itself. |
| C | Consume cortex-bot / the official server **from** SimCore as an MCP client. | **Reject.** | An extra hop, a 3.13 dependency we cannot bake, a console-downloaded zip we cannot pin, and it re-introduces F1/F2/F6 behind our API. Buys nothing the catalog + httpx client do not already do. |
| D | Side-by-side: DC runs cortex-bot in their own Claude Code next to CortexSim. | **Tolerate with conditions.** | Not ours to forbid. Conditions: Viewer-role key, never Instance Admin; `test_all_tools.py` never pointed at a prospect; and **nothing read through cortex-bot is quoted as POV evidence**. Evidence enters only via `/observations`, `/reconcile`, `/verify` and lands as `tc_verdict`. A number the agent fetched outside that funnel is a screenshot, not proof. |
| E | Port cortex-bot's write tools (isolate, run script, author rules) into CortexSim as live actions. | **Reject.** | `docs/reference/xsiam-platform-apis/99-harness-design-notes.md` §1 and Gate A5: no write path to Cortex. The catalog may *document* writes; the executor previews them. `cortex-pov-agent` AGR-03 says the same from the other side: write tools are `require_approval`, always, and a write-capable manifest cannot ship green. |

### 2.7 Recommendation and sequencing

**Option A, then B. Never C or E. D under conditions.** Concretely, in Gate A units:

1. **Catalog harvest** (`core/integrations/xsiam/operations/packs/`). The 28 ops in §2.4
   across 5 new packs (`system`, `cases-issues`, `itdr`, `war-room`, `mdr`,
   `vulnerability-management`) and 5 existing ones. Per-op request-shape override for the
   BYOS ingest (no `request_data` wrapper). Proof: `catalog.count()` 116 → ~144; the
   loader rejects a pack that declares a `/public_api/` path without an `access_class`;
   `docs/reference/xsiam-platform-apis/14-operation-catalog.md` regenerated.
2. **Alert read-back path probe** (`core/connectors/preflight.py`, `core/connectors/xsiam.py`).
   Preflight's `datasets` stage tries v2 then v1 for `get_alerts_multi_events` and records
   `alerts_api_version` on the report; the connector consumes it unless `alerts_path` is
   set. Proof: a fixture where v1 answers an error envelope and v2 answers rows; without
   the change the reconcile pull yields 0 alerts and every seeded result scores `missed`;
   with it, the matcher sees the rows. That failing-first run is the Gate A evidence.
3. **Observed entitlement** (`core/api/pov.py`, preflight `scope` stage). `get_tenant_info`
   fields → `entitlement_basis: observed | declared` on the scope response; canned profiles
   stay as the `declared` fallback. Proof: `identity_threat_expiration: 0` moves the ITDR
   scenarios into the upsell list. Honesty line in the response: observed entitlement is
   not tenant-verified coverage.
4. **Contributing-event witness** (`core/connectors/matcher.py`, `ObservedAlert`). For a
   native correlation alert, pull contributing events and match the seeded step's
   `causality_id` / marker inside them; record `witness: contributing_event`. A 500 on an
   external alert records `witness: unavailable`, never a `fail`. Proof: a fixture pair
   (marker present / absent) plus the 500 case.
5. **Envelope fixtures** (`core/integrations/xsiam/queries.py` siblings). One raise-on-unknown
   unwrapper per family (`DATA`, `data`, `widgets`, `scripts`, the three `mth/child` shapes)
   with cortex-bot's observed variants as fixtures, and the existing AST drift guard
   pattern asserting one definition each.
6. **MCP server** (`feature/api-mcp-server`). Design spec first under
   `docs/superpowers/specs/`. Transport: stdio launched by the DC's client
   (`claude mcp add cortexsim …`) and, optionally, Streamable HTTP mounted on SimCore.
   Tool set = CortexSim's own surface + catalog projection; writes return the dry-run
   preview and the exact consent the caller would need. Proof: a test that a write tool
   invoked without flags + consent returns a preview and issues **zero** tenant calls
   (`queries_issued == 0`).

Items 1–5 are one topic each (`feature/adapters-…`, `fix/api-…`), cut from `dev`, Gate A
evidence in each PR body. Item 6 is a feature branch of its own.

### 2.8 What this does not change

- **tenant-verified stays 0.** cortex-bot's observations are evidence about the *API*,
  not evidence that CortexSim has executed against a tenant. They go into fixtures and
  preflight, never into a coverage number.
- **No write path to Cortex.** The catalog gains write and destructive *entries*; the
  executor's defaults and the two flags are untouched.
- **Authored is not proven.** Adding 28 ops is authored breadth. The Readiness ladder
  (AUTHORED · CONFIGURED · REACHABLE · VERIFIED) is the right place for every one of
  them to be stated as such.

### 2.9 Open decisions (Henry)

1. **Upstream the findings.** F1 (unimplemented kill switch), F2 (LLM-settable gate),
   F4 (Instance Admin), F5 (`DEMISTO_VERIFY_SSL=false`) are a colleague's public repo.
   An issue or PR to `alexpekarovsky/cortex-bot` is the collegial move and costs little.
   Your call on tone and channel.
2. **Consumer first.** `cortex-pov-agent` (TypeScript, MCP client 1.30 already wired,
   XSIAM connector unbuilt at S04) versus Claude Code on a DC laptop. The MCP server's
   tool vocabulary should match whichever goes first; the agent repo already names its ten
   read tools (`xsiam.dataset.list`, `xsiam.xql.query`, `xsiam.alert.list`, …).
3. **Policy.** Is there a WW DC position on DC-published tooling that holds customer
   tenant keys? If not, this review is a reasonable seed for one: Viewer role, no
   self-tests against prospects, evidence only through the engine.

### 2.10 Build log and corrections (2026-11-02, evening)

| Item | Branch | Commits | Result |
|---|---|---|---|
| 1 · catalog harvest | `feature/api-xsiam-catalog-harvest` | `70321fa` generator · `403c942` harvest · `b327bf9` doc counts | 116 → **148** ops, 23 → 30 categories (79 read · 51 write · 18 destructive). Guard red first: 35 failed. Host suite 5325 passed / 30 skipped. |
| 2 · alerts-path probe | `fix/api-preflight-alerts-path` | `bf319b5` | New `PF_ALERTS_PATH_MISMATCH`. Guard red on `dev`'s logic: 5 failed. Host suite 5293 passed / 30 skipped. |

Both branches are unpushed and have no PR. Neither changes `tenant-verified`.

**Corrections to this review, found while building:**

- **The harvest is 32 ops, not ~28.** The MDR family is eight distinct `mth/child/*`
  subpaths (5 read, 3 write), not one path. Two vulnerability-finding reads were invisible
  to the original count because cortex-bot sends them as bare paths that the official
  fetcher prefixes with `/public_api/v1`. Final split: 22 read · 8 write · 2 destructive.
- **`terminate_causality` is `destructive`, not `write`.** It kills a process tree and cannot
  be undone. That is stricter than the catalog's `isolate`, which is `write`, on purpose.
- **§2.3 row 1 overstated the loud case.** If v1 is gone and returns 404, reconcile already
  refuses to score, so it fails loudly and never reads 0. The manufactured false negative
  needs v1 to answer 200 with nothing while v2 holds the alerts. The probe now covers both
  cases. The connector default stays v1. Preflight tells the DC which value to set and
  never switches the path itself.
- **The doc mirror had no generator** and every count quote said "read-only", which was
  never true. `scripts/gen_xsiam_operation_catalog.py` regenerates it. It is dry-run by
  default and was proven byte-identical on the old packs. The quotes now say "gated".
- **The executor wrapped every bodyless POST in `request_data`.** Bring-your-own-scan
  ingest rejects that wrapper, so its preview showed a request that would fail. A
  `no-request-data-wrapper` tag now sends `{}` instead.

**Still open from §2.7:** item 3, observed entitlement from `get_tenant_info`. Item 4, the
contributing-event witness. Item 5, per-family raise-on-unknown unwrappers. Item 6, the MCP
server, which runs in its own session on `feature/api-mcp-server`. CHANGELOG entries for
items 1 and 2 are owed at release prep.

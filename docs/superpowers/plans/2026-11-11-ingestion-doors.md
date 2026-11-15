# Ingestion Doors Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Track each tenant-side way in ("door") with a tenant-checked state timeline, show every scenario's door readiness in the library and at launch, and exclude silent detections whose door was closed when their step ran.

**Architecture:** A frozen door registry (`core/engine/ingestion_doors.py`) plus a `doors:` / `doors_unknown:` key on every dataset row. A pure resolver joins each expected detection to its door requirement. A `door_events` table holds each door's state timeline. Read-only checks and an opt-in watcher append to it, and scoring reads it at each result's `executed_at`.

**Tech Stack:** Python 3.11, FastAPI, async SQLAlchemy on SQLite, pytest; React 18 + vitest; Playwright via Chrome for the browser check.

**Spec:** `docs/superpowers/specs/2026-11-11-ingestion-doors-design.md` (read it with this plan).

## Global Constraints

- Read-only to the tenant. Checks use the `xsiam_tenant` credential, XQL and `POST /public_api/v1/endpoints/get_endpoints` only. No write path. `CORTEXSIM_XSIAM_ALLOW_WRITE` is untouched.
- Nothing calls the tenant without an operator action or `CORTEXSIM_DOOR_WATCH=true` (default `false`). The launch path never calls the tenant.
- At most two tenant calls per door per check, charged to the existing per-tenant quota ledger. Quota exhaustion records `unknown`.
- A check that could not run is `unknown`: it never opens or closes a door. Before any conclusive event a door is `unknown`.
- An attested door is shown as `attested`, never as `verified`.
- "Recent rows" means at least one row in the last 3,600 seconds.
- No expiry timer on door state. The watcher's default cadence is 1,800 seconds.
- Exclusion applies to silent rows only. A row with an observed alert is credited as today.
- A run with any door exclusion can FAIL and never PASS (`PASS_WITHHELD_DOOR_CLOSED`). A fully excluded run reads `pending`, never `not_applicable`.
- An unrecognised shape raises. Never read it as empty.
- `tenant_verified` stays `0` in every new response body.
- Branches: tasks 1–6 on `feature/api-ingestion-doors` (phase 1, one PR); tasks 7–12 on `feature/api-door-checks`, cut from `dev` after phase 1 merges; tasks 13–14 on `feature/ui-get-started-doors`. Stage files by name. Run `make validate` (with `.venv/bin` first on `PATH`) before each PR.

## Review Focus

1. **A tenant key without the `xql` capability.** Every door check must return `unknown` with remediation `xql_not_authorised` (reusing `resolve_tenant_runner`), never `closed`. Test in Task 7.
2. **A beacon that is a k8s pod.** `xdr_agent` must match `runtime_facts.node_name`, not the pod hostname. With no `node_name` it is `unknown`. Test in Task 7.
3. **A month-long stream with a mid-stream outage.** Only rows whose `executed_at` falls inside the closed interval are excluded. Test in Task 9.
4. **A collector step whose target resolved to the `.invalid` stand-in URL**, or an agent-run step reading `$COLLECTOR_URL`. Its route is unknown and its rows are excluded, never judged as an HTTP collector. Test in Task 8.
5. **A scenario citing a card id that is not in the catalog.** The resolver marks those detections `underived` with code `CARD_NOT_FOUND` and keeps going. It never raises for the whole scenario. Test in Task 3.

---

## Phase 1 — registry, derivation, visibility (no exclusion)

### Task 1: Door registry

**Files:**
- Create: `core/engine/ingestion_doors.py`
- Test: `tests/engine/test_ingestion_doors.py`

**Interfaces:**
- Produces:
  - `Door` (frozen dataclass): `id: str`, `label: str`, `scope: str` (`"tenant" | "agent" | "target"`), `verify_datasets: tuple[str, ...]`, `provider_filter: str | None`, `needs_attestation_of: str | None`, `evidence: str` (an https URL or a repo path to the 2026-11-10 tenant-setup research appendix), `wizard: str | None` (console route id).
  - `DOORS: tuple[Door, ...]`, `get_door(door_id: str) -> Door` (raises `UnknownDoorError(ValueError)`), `door_key(door_id: str, scope_value: str | None) -> str` (returns `door_id` for tenant scope, `f"{door_id}:{scope_value}"` otherwise; raises when a non-tenant door gets `None`).
  - `CLOUD_PROVIDER_DOORS: frozenset[str] = {"cloud_aws", "cloud_gcp", "cloud_azure"}`.

Door ids, with their `verify_datasets` (copy from spec §4):

| id | scope | verify_datasets | provider_filter / attestation |
|---|---|---|---|
| `xdr_agent` | agent | `xdr_data` | — |
| `xdr_agent_xth` | agent | `xdr_data` | `needs_attestation_of="xth"` |
| `ngfw_traffic` / `ngfw_threat` / `ngfw_url` / `ngfw_globalprotect` | tenant | `panw_ngfw_traffic_raw` / `_threat_raw` / `_url_raw` / `_globalprotect_raw` | wizard `firewall` |
| `ngfw_stitch` | agent | `network_story` | wizard `firewall` |
| `cloud_aws` / `cloud_gcp` / `cloud_azure` | tenant | `cloud_audit_logs` | `provider_filter` `"AWS"` / `"GCP"` / `"AZURE"` |
| `saas_okta` | tenant | `okta_sso_raw` | — |
| `saas_entra` | tenant | `msft_azure_ad_raw` | — |
| `saas_o365` | tenant | `msft_o365_exchange_online_raw`, `msft_o365_sharepoint_online_raw` | — |
| `saas_google_workspace` | tenant | `google_workspace_login_raw`, `google_workspace_admin_console_raw` | — |
| `windows_events` | tenant | `microsoft_windows_raw` | — |
| `syslog_cisco_asa`, `syslog_vmware_esxi`, `syslog_ivanti_connect_secure`, `syslog_kubernetes_kubernetes` | tenant | `cisco_asa_raw`, `vmware_esxi_raw`, `ivanti_connect_secure_raw`, `kubernetes_kubernetes_raw` | — |
| `http_collector` | target | `()` (the target's own `dataset`) | wizard `tenant` |
| `proofpoint_tap` | tenant | `proofpoint_tap_raw` | — |
| `prisma_browser` | tenant | `panw_prisma_access_browser_raw` | — |
| `xpanse` | tenant | `()` | — |
| `tim_feed` | tenant | `indicators` | — |
| `prisma_airs` | tenant | `panw_prisma_airs_raw` | — |
| `koi_aes` | tenant | `koi_koi_raw` | — |
| `tenant_incidents` | tenant | `xsiam_incidents` | — |

- [ ] **Step 1: Write the failing tests** — `test_ids_unique`, `test_every_door_has_https_or_repo_evidence`, `test_cloud_doors_carry_a_provider_filter` (exactly the three in `CLOUD_PROVIDER_DOORS`), `test_get_door_unknown_raises`, `test_door_key_tenant_vs_scoped` (`door_key("saas_okta", None) == "saas_okta"`, `door_key("xdr_agent", "a1") == "xdr_agent:a1"`, `door_key("xdr_agent", None)` raises), `test_registry_is_frozen`.
- [ ] **Step 2: Run** `PYTHONPATH=core .venv/bin/python -m pytest tests/engine/test_ingestion_doors.py -q`. Expected: collection error (module missing).
- [ ] **Step 3: Implement** the module. Validate at import (unique ids, scope vocabulary, provider filter only on cloud doors), as `ingestion_routes._validate()` does.
- [ ] **Step 4: Run** the tests. Expected: all pass.
- [ ] **Step 5: Commit** `feat(api): ingestion door registry`.

### Task 2: `doors:` / `doors_unknown:` on every dataset row

**Files:**
- Modify: `core/engine/dataset_requirements.py` (`DatasetRequirement`, `parse_registry`, `to_dict`)
- Modify: `spec/dataset_requirements.yml` (all 32 rows)
- Modify: `docs/reference/dataset-requirements.md`
- Test: `tests/engine/test_dataset_requirements.py`

**Interfaces:**
- Consumes: `get_door` (Task 1).
- Produces: `DatasetRequirement.doors: tuple[str, ...]` and `DatasetRequirement.doors_unknown: str | None` (exactly one is set). Both appear in `to_dict()`.

Row assignments (spec §5; `doors_unknown` means no real tenant counterpart is known):

| doors | datasets |
|---|---|
| `[xdr_agent]` | `xdr_data` |
| `[ngfw_traffic]` | `panw_ngfw_traffic_raw`, `pan_ngfw_traffic` |
| `[ngfw_threat]` / `[ngfw_globalprotect]` | `panw_ngfw_threat_raw` / `panw_ngfw_globalprotect_raw` |
| `[ngfw_stitch]` | `network_story` |
| `[cloud_aws, cloud_gcp, cloud_azure]` | `cloud_audit_logs`, `cortex_cloud_posture`, `ai_spm_findings` |
| `[cloud_azure]` | `msft_azure_audit` |
| `[saas_okta]` | `okta_sso`, `saas_okta_raw` |
| `[saas_entra]` | `msft_azure_ad_signin` |
| `[saas_o365]` | `msft_o365_audit`, `msft_o365` |
| `[saas_google_workspace]` | `google_workspace_audit` |
| `[windows_events, xdr_agent_xth]` | `msft_windows_security` |
| `[syslog_cisco_asa]` / `[syslog_ivanti_connect_secure]` | `cisco_asa_raw` / `ivanti_ics_raw` |
| `[syslog_kubernetes_kubernetes, cloud_aws]` | `kubernetes_audit_logs` |
| `[proofpoint_tap]`, `[prisma_browser]`, `[xpanse]`, `[koi_aes]`, `[tenant_incidents]` | `proofpoint_tap_raw`, `prisma_browser_events`, `asm_findings`, `koi_code_scan_events`, `xsiam_incidents` |
| `doors_unknown` | `ad_audit`, `cloud_audit`, `cortexsim_airs_raw`, `esxi_syslog`, `pan_dns`, `saas_audit_logs`, `threat_intel` (each reason names what is unconfirmed, from the row's own `source_side.detail`) |

- [ ] **Step 1: Write the failing tests** — `test_every_shipped_row_has_exactly_one_door_key`, `test_unknown_door_id_rejected`, `test_both_keys_rejected`, `test_neither_key_rejected`, `test_placeholder_doors_unknown_rejected` (reject `"tbd"`, `"unknown"`, `"n/a"`, `""`, case-insensitive), `test_doors_must_be_nonempty_list`, `test_to_dict_carries_doors`.
- [ ] **Step 2: Run** them. Expected: fail (no such key).
- [ ] **Step 3: Implement** in `parse_registry`: allow the two new keys in `_keys(...)` as optional-but-exactly-one, then fill all 32 rows.
- [ ] **Step 4: Run** the tests and `make check-ground-truth`. Expected: pass; ground truth unchanged.
- [ ] **Step 5: Commit** `feat(api): every dataset row names its door`.

### Task 3: Detection → door resolution and scenario readiness

**Files:**
- Create: `core/engine/door_resolution.py`
- Test: `tests/engine/test_door_resolution.py`, `tests/engine/test_door_corpus.py`

**Interfaces:**
- Consumes: Task 1, Task 2, `engine.ttp_catalog.catalog.find/raw`, `engine.dataset_requirements.walk_card`, `engine.signal_path.signal_of`.
- Produces:
  - `DoorRequirement` (frozen) has these fields:
    - `all_of: tuple[tuple[str, ...], ...]`: an AND of OR-sets of door ids.
    - `route: str | None`: `None`, or `"collector"` for spec §5 rule 1.
    - `collector_target: str | None`: the resolved target name on the collector route.
    - `route_blocked: str | None`: `None` or `"DOOR_ROUTE_CANNOT_FEED"`.
    - `needs_analytics: bool`: `True` for Analytics and ABIOC.
    - `underived: str | None`: a code, one of `CARD_NOT_FOUND`, `NO_DATASET`, `DOORS_UNKNOWN`, `NO_PROVIDER`.
    - `to_json() -> dict` and `from_json(d) -> DoorRequirement`.
  - `detection_requirement(step: dict, detection: dict, *, catalog, registry, collector_target: str | None = None, collector_dataset: str | None = None, xdm_attested: bool = False) -> DoorRequirement`.
  - `scenario_readiness(scenario: dict, open_keys: set[str], *, agent_id: str | None, catalog, registry) -> dict` returning `{"scenario_id", "state": "ready"|"partial"|"needs_doors"|"not_evaluated", "scorable": int, "total": int, "underived": int, "closed": [{"door_id", "detections": int}], "agent_scoped_evaluated": bool}`.

Rules (spec §5, in order). Collector route: `signal_of(step)["path"] == "COLLECTOR"`. Then `all_of=(("http_collector",),)` and `route="collector"`. `route_blocked="DOOR_ROUTE_CANNOT_FEED"` when the type is BIOC or IOC; when it is Analytics or ABIOC without `xdm_attested`; or when the card's datasets do not include `collector_dataset` (this includes `collector_dataset is None`). Otherwise datasets come from `walk_card` filtered to the `DetectionCard`'s kind and `name`, AND across datasets. A `CLOUD_PROVIDER_DOORS` OR-set narrows to the one provider found from a `cloud_provider = "<X>"` filter in the logic, else the card's `metadata.tags` (`aws`/`gcp`/`azure`). With none found the detection is `underived="NO_PROVIDER"`.

- [ ] **Step 1: Write the failing tests** (unit, with tiny in-memory cards and registry):
  - `test_xdr_data_bioc_needs_xdr_agent`
  - `test_two_dataset_join_is_and`
  - `test_windows_or_set_preserved`
  - `test_collector_bioc_route_blocked`
  - `test_collector_xql_dataset_mismatch_blocked`
  - `test_collector_xql_matching_dataset_scorable`
  - `test_collector_analytics_needs_xdm`
  - `test_cloud_provider_from_filter`
  - `test_cloud_provider_from_tag`
  - `test_cloud_without_provider_underived`
  - `test_doors_unknown_row_underived`
  - `test_missing_card_underived_not_raised`
  - `test_readiness_ready_partial_needs`
  - `test_agent_doors_not_evaluated_without_agent` (tenant doors still judged, `agent_scoped_evaluated is False`)
- [ ] **Step 2: Write the corpus pin** `test_door_corpus.py::test_corpus_door_counts` over the loaded corpus. It asserts 1,411 expected detections in total and 199 judged by the collector route. `xdr_agent` must be the sole door for exactly 502 (507 `xdr_data` detections minus 5 on collector steps). The other per-door counts are measured on the implementation HEAD, written into the test with the date, and copied into spec §1.
- [ ] **Step 3: Run** both. Expected: fail (module missing).
- [ ] **Step 4: Implement**; run until green.
- [ ] **Step 5: Commit** `feat(api): derive each detection's door from its dataset or route`.

### Task 4: Door state store and timeline

**Files:**
- Modify: `core/models.py` (new `DoorEvent`), `core/database.py` (`create_all` covers the table; no column additions in this task)
- Create: `core/engine/door_state.py`
- Test: `tests/engine/test_door_state.py`

**Interfaces:**
- Produces:
  - `DoorEvent` ORM: `id`, `door_key: str` (indexed), `door_id: str`, `state: str` (`open|closed|unknown`), `basis: str` (`verified|attested|check|configured`), `rung: str` (`authored|configured|reachable|verified`), `at: datetime` (naive UTC), `evidence: JSON`, `attested_by: str | None`, `queries_issued: int`.
  - `effective_state(events: list[DoorEvent], when: datetime) -> tuple[str, DoorEvent | None]`: the last event with `state in {"open","closed"}` at or before `when`. `unknown` events never change it. Before any conclusive event it is `("unknown", None)`.
  - `async def record(db, *, door_key, door_id, state, basis, rung, evidence, attested_by=None, queries_issued=0, at=None) -> DoorEvent`.
  - `async def current(db) -> dict[str, dict]`: per door key, the effective state now, its basis, the rung, `last_conclusive_at`, `last_event_at` and `age_seconds`.
  - `async def configured_facts(db) -> dict[str, str]`: rungs derived with zero outbound calls. `configured` for cloud, SaaS and syslog doors when an `xsiam_tenant` credential with `xql` exists; for `ngfw_*` when a firewall is registered; for `http_collector:<name>` per collector target; for `xdr_agent:<agent_id>` per online beacon. Read the existing `CredentialStore`, `engine.collector_targets.load_specs` and `/api/agents` rows.
- [ ] **Step 1: Write the failing tests** — `test_effective_state_ignores_unknown`, `test_effective_state_before_any_event_unknown`, `test_attested_then_closed_check_closes`, `test_closed_then_unknown_stays_closed`, `test_no_expiry_open_stays_open_after_30_days`, `test_naive_utc_only` (a tz-aware `when` raises), `test_configured_facts_zero_outbound` (inject a transport that raises if called).
- [ ] **Step 2: Run.** Expected: fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the tests, plus `make test-backend-non-utc` scoped to this file. Expected: pass in both.
- [ ] **Step 5: Commit** `feat(api): door state timeline`.

### Task 5: Read API

**Files:**
- Create: `core/api/doors.py`; Modify: `core/main.py` (include router)
- Modify: `docs/reference/api-and-agent-surface.md`; regenerate `docs/reference/ground-truth.{json,md}` (`make ground-truth`)
- Test: `tests/api/test_doors_api.py`

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces:
  - `GET /api/doors` → `{"doors": [{key, door_id, label, scope, state, basis, rung, last_conclusive_at, age_seconds, wizard, detections_needing}], "tenant_verified": 0, "outbound_calls": 0}`
  - `GET /api/doors/readiness?agent_id=` → `{"scenarios": {scenario_id: <scenario_readiness>}, "agent_scoped_evaluated": bool}` (bulk; served here so the hot `/api/scenarios` list is unchanged — the spec §7 "library list carries `door_readiness`" is met by this one fetch)
  - `GET /api/scenarios/{id}/doors?agent_id=` → one `scenario_readiness`, plus per-detection `{step_id, detection_id, requirement}`. 404 `SCENARIO_NOT_FOUND`.
- [ ] **Step 1: Write the failing tests** — `test_list_doors_zero_outbound`, `test_readiness_bulk_keys_every_loaded_scenario`, `test_scenario_doors_404`, `test_phase1_nothing_open` (with no events every door is `unknown` or `configured`, and every scenario reads `needs_doors` or `not_evaluated`), `test_tenant_verified_zero`.
- [ ] **Step 2: Run.** Expected: fail (404 routes).
- [ ] **Step 3: Implement**; error bodies use `{"error","code","detail"}`.
- [ ] **Step 4: Run** the tests, then `make check-ground-truth` after `make ground-truth`. Expected: green, with the route count bumped by 3.
- [ ] **Step 5: Commit** the API, the doc rows and the regenerated ground truth: `feat(api): door state and scenario readiness read API`.

### Task 6: Library chip and the advisory launch check

**Files:**
- Modify: `ui/src/api/client.js` (`getDoors()`, `getDoorReadiness(agentId)`, `getScenarioDoors(id, agentId)`)
- Create: `ui/src/components/console/doors/doorModel.js` (pure: `chipFor(readiness) -> {label, tone, title}`)
- Modify: `ui/src/components/console/ScenarioTable.jsx`, `ScenarioGrid.jsx` (chip + "Runnable here" filter in `useScenarioFilter.js`)
- Modify: `ui/src/components/console/readiness/scenarioPreflight.js` (sixth check `dataPathCheck(scenario, doorReadiness)`, id `data-path`, label `Data path`)
- Test: `ui/src/components/console/doors/__tests__/doorModel.test.js`, `ui/src/components/console/readiness/__tests__/dataPathCheck.test.js`

Copy, exact:
- chip labels: `READY`, `PARTIAL k/n`, `NEEDS DOORS`, `NOT CHECKED`
- check details: pass `every door this scenario needs is open`; warn `{n} of {total} detections will not be scored: {doors}`; warn when all are excluded `this run scores nothing`; unknown when readiness is absent `door readiness not reported`.

- [ ] **Step 1: Write the failing tests** — `chip_needs_doors_names_closed`, `chip_not_checked_when_all_unknown`, `filter_runnable_keeps_ready_and_partial`, `nothing_hidden_by_default`, `data_path_warn_counts`, `data_path_scores_nothing`, `data_path_unknown_without_input` (never `pass`). The preflight-severity test asserts `dataPathCheck` never returns `block`.
- [ ] **Step 2: Run** `cd ui && npx vitest run src/components/console/doors src/components/console/readiness`. Expected: fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** vitest, `npx vite build`, and the browser check (`ui/tests/e2e/14-library-doors.spec.ts`: the chip is visible on SIM-EDR-001 and the filter keeps a ready row). Use Chrome via `channel: 'chrome'` from a scratchpad config. Expected: green.
- [ ] **Step 5: Commit** `feat(ui): door readiness in the library and at launch`, then the CHANGELOG `[Unreleased]` entry and the spec status line (`phase 1 built`) as a separate `docs:` commit. Open the phase 1 PR only when Henry asks.

## Phase 2 — checks, exclusion, verdict, watcher

### Task 7: Door checks and attestation

**Files:**
- Create: `core/engine/door_checks.py`
- Modify: `core/integrations/xsiam/client.py` (`find_endpoints(self, *, hostnames: list[str]) -> list[dict]`)
- Modify: `core/api/doors.py` (`POST /api/doors/{key}/check`, `POST /api/doors/{key}/attest`)
- Test: `tests/engine/test_door_checks.py`, `tests/api/test_doors_api.py`

**Interfaces:**
- Consumes: Task 1, Task 4, `connectors.service.resolve_tenant_runner`, `integrations.xsiam.loader.load_xsiam_client`, `engine.collector_targets.load_specs`, the NGFW preflight's last stored report.
- Produces:
  - `async def check_door(db, door_key: str, *, runner=None, client=None, now=None) -> DoorEvent` (records and returns the event).
  - `ROWS_QUERY = "dataset = {dataset} | filter _time >= to_timestamp({since_ms}, \"MILLIS\") | limit 1"` (provider doors append `| filter cloud_provider = "{provider}"`). Count rows with `integrations.xsiam.queries.row_count`. Confirm the `_time` / `cloud_provider` field names in a tenant query before the PR; until then the check's evidence says `field names from documentation`.
  - Attest body `{attested_by: str, producer?: str, host?: str, xdm_modeled?: bool}`. `xdm_modeled` is valid only on `http_collector:<name>`. 422 `ATTESTED_BY_REQUIRED` when blank. `producer` is required for `windows_events` (`broker_wec|xdr_agent_xth|xdr_collector`), and `host` for an attestation used by a push run.

Per door: rows found → `open`/`verified`; zero rows → `closed`/`check`; no tenant, no XQL, quota, 401 or 5xx → `unknown`, with the remediation from `TENANT_REMEDIATION`. `xdr_agent` first calls `find_endpoints` with the beacon's `hostname`, or `runtime_facts.node_name` for a container beacon. Not found → `closed`. Found but not connected → `closed`, with the status in evidence. A container beacon with no `node_name` → `unknown`. Found and connected → rung `reachable`, then the rows query filtered by `agent_hostname`. `ngfw_*` requires the stored NGFW preflight's forwarding stages ok for its log type before the rows query, else `closed`. `http_collector:<name>` requires the target's `last_check.verdict == "accepted"`, then queries the target's `dataset`; with no dataset, `unknown`. `xpanse` always reads `unknown` (no dataset). `xdr_agent_xth:<agent>` opens only when `xdr_agent:<agent>` is open and an attestation with `producer="xth"` exists for it. A check never opens it alone.

- [ ] **Step 1: Write the failing tests** — `test_rows_open`, `test_zero_rows_closed`, `test_no_xql_unknown`, `test_quota_unknown`, `test_unrecognised_envelope_raises`, `test_xdr_agent_not_in_tenant_closed`, `test_xdr_agent_pod_uses_node_name`, `test_xdr_agent_pod_without_node_unknown`, `test_ngfw_forwarding_off_closed`, `test_collector_not_accepted_closed`, `test_two_call_cap`, `test_attest_requires_name`, `test_attest_shown_attested`, `test_windows_attest_requires_producer`, `test_xth_needs_agent_open_and_attestation`, and API `test_check_records_queries_issued`.
- [ ] **Step 2: Run.** Expected: fail.
- [ ] **Step 3: Implement**, with all transports injectable.
- [ ] **Step 4: Run** the tests. Expected: green.
- [ ] **Step 5: Commit** `feat(api): read-only door checks and attestation`.

### Task 8: Door requirement on every seeded result

**Files:**
- Modify: `core/models.py` (`Result.required_doors: JSON`, `Result.door_scope: String`), `core/database.py` (`additions["results"]`: `("required_doors", "JSON")`, `("door_scope", "VARCHAR")`)
- Modify: `core/engine/orchestrator.py` (`_seed_results` and the EAL seeding loop at the `for s in eal_steps` block)
- Modify: `core/api/runs.py` (`LaunchRequest.door_attestations: list[{door_id, host, attested_by}] = []`, push runs only)
- Test: `tests/engine/test_seed_door_requirement.py`

**Interfaces:**
- Consumes: `detection_requirement` (Task 3), passing `collector_target=resolutions[step_id].target` and that target spec's `dataset` from the collector resolutions the EAL path already computes. `xdm_attested` comes from the latest `xdm_modeled` attestation on the collector door.
- Produces: `Result.required_doors = DoorRequirement.to_json()`. `Result.door_scope` is the effective agent id (step `target` else `Run.target`), `f"host:{host}"` for push runs, or `None`. For a push run, `door_attestations` are recorded as attested events on `door_key(door_id, "host:"+host)`.

- [ ] **Step 1: Write the failing tests** — `test_pull_row_carries_requirement_and_agent`, `test_fanout_step_uses_its_own_target`, `test_eal_row_uses_resolved_collector_dataset`, `test_unresolved_stand_in_is_route_unknown` (target resolves to `UNRESOLVED_COLLECTOR_URL` → `route_blocked="DOOR_ROUTE_CANNOT_FEED"`), `test_agent_run_collector_url_env_route_unknown`, `test_push_attestation_recorded`, `test_migration_adds_columns` (an old SQLite file gains both columns).
- [ ] **Step 2: Run.** Expected: fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the tests plus `tests/engine/test_orchestrator*.py`. Expected: green.
- [ ] **Step 5: Commit** `feat(api): seed each result with the doors it needs`.

### Task 9: Exclude silent rows behind a closed door

**Files:**
- Modify: `core/engine/outcome.py` (`DOOR_NOT_VERIFIED`, `DOOR_ROUTE_CANNOT_FEED`, `DOOR_REASONS = frozenset({...})`, `is_in_denominator` excludes `pending` with a reason in `DOOR_REASONS`)
- Modify: `core/connectors/measures.py` (`denominator_exclusion` returns `"door_not_verified"` for those reasons; `empty_exclusions()` gains the key)
- Modify: `core/connectors/service.py::_judge_silent_results` (door rule before the soak rule)
- Test: `tests/connectors/test_door_exclusion.py`

**Interfaces:**
- Consumes: `effective_state` (Task 4), `DoorRequirement.from_json` (Task 3), `Result.door_scope` (Task 8).
- Produces: `door_verdict(requirement: DoorRequirement, events_by_key: dict[str, list[DoorEvent]], *, scope: str | None, when: datetime) -> str | None` in `core/engine/door_resolution.py`. It returns a reason or `None`:
  - `route_blocked` first.
  - Then, for each AND element, `DOOR_NOT_VERIFIED` unless some door in its OR-set is `open` at `when`.
  - Keys come from `door_key(id, scope)` for agent doors, `door_key("http_collector", requirement.collector_target)` for the collector, and the bare id for tenant doors.
  - When `needs_analytics`, an open `windows_events` counts only if its opening event's `evidence["producer"]` is `broker_wec` or `xdr_agent_xth` (spec §4).
  - An `underived` requirement returns `None`.

- [ ] **Step 1: Write the failing tests:**
  - `test_closed_door_silent_row_excluded`
  - `test_observed_row_credited_despite_closed_door`
  - `test_door_opened_after_step_does_not_reinclude`
  - `test_outage_excludes_only_rows_inside_it`: three rows at day 1, 15 and 29; the door is closed between day 14 and 16; only the day-15 row is excluded.
  - `test_route_blocked_excluded`
  - `test_windows_analytics_needs_wec_or_xth_producer`: an XDR Collector producer excludes an Analytics row and admits an XQL row
  - `test_collector_row_keys_on_its_target`
  - `test_underived_scored_as_today`
  - `test_already_not_reached_untouched`
  - `test_measures_counts_door_not_verified`
- [ ] **Step 2: Run.** Expected: fail.
- [ ] **Step 3: Implement.** Load the events for the needed keys once per sweep.
- [ ] **Step 4: Run** the tests plus `tests/connectors/`. Expected: green, and `make test-backend-non-utc` is green for these files.
- [ ] **Step 5: Commit** `fix(api): a closed door excludes its silent rows instead of scoring them missed`.

### Task 10: PASS withheld on door exclusion

**Files:**
- Modify: `core/engine/outcome.py` (`PASS_WITHHELD_DOOR_CLOSED`), `core/engine/verifier.py::score_run` and `RunScore` (`door_excluded: dict[str, int]`, keyed by door id, and `to_dict` carries it)
- Test: `tests/engine/test_verifier_door.py`

Rules: count rows with `outcome_reason in DOOR_REASONS` into `door_excluded`. Then:
- If the verdict would be `pass` (by rows or by primary KPI) and `door_excluded` is non-empty, it becomes `pending`, with detail `PASS_WITHHELD_DOOR_CLOSED: {n} detection(s) not tested behind {doors}`.
- If no row was scoreable and `door_excluded` is non-empty, it becomes `pending` (not `not_applicable`).
- `fail` is unchanged. `_clamp` runs after these rules.

- [ ] **Step 1: Write the failing tests** — `test_miss_still_fails_with_exclusions`, `test_pass_becomes_pending_with_reason`, `test_kpi_pass_withheld`, `test_all_excluded_pending_not_na`, `test_no_exclusion_unchanged` (the existing verifier tests stay green untouched).
- [ ] **Step 2: Run.** Expected: fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `tests/engine/test_verifier*.py`. Expected: green.
- [ ] **Step 5: Commit** `fix(api): a run never passes while detections went untested`.

### Task 11: Opt-in door watcher

**Files:**
- Modify: `core/config.py` (`CORTEXSIM_DOOR_WATCH: bool = False`, `CORTEXSIM_DOOR_WATCH_INTERVAL: int = 1800`, `CORTEXSIM_DOOR_WATCH_MAX_QUERIES_PER_SWEEP: int = 50`)
- Create: `core/engine/door_watch.py` (`async def door_watch_once(db, *, max_queries: int) -> dict`, `async def door_watch_loop(interval_seconds: int, max_queries: int) -> None`)
- Modify: `core/main.py` (lifespan task, as `auto_reconcile_loop`), `core/api/health.py` (`not_checked[]` entry naming `/api/doors` when the watcher is off), `.env.example`
- Test: `tests/engine/test_door_watch.py`

The sweep re-checks every door whose effective state is `open`, plus each door with an `unknown` latest event, least recently checked first. It stops at `max_queries`, and an `XsiamQuotaError` ends the sweep (circuit breaker) without recording `closed`.

- [ ] **Step 1: Write the failing tests** — `test_flag_off_no_task`, `test_sweep_rechecks_open_doors`, `test_sweep_records_close`, `test_quota_breaker_records_nothing_closed`, `test_query_cap`.
- [ ] **Step 2: Run.** Expected: fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the tests. Expected: green.
- [ ] **Step 5: Commit** `feat(api): opt-in door watcher for long-running streams`.

### Task 12: Report and run readout

**Files:**
- Modify: `core/api/runs.py` (`/api/runs/{id}` carries `door_exclusions: [{door_id, detections, remediation, wizard}]`; the markdown report gains a "Not tested — door closed" section)
- Modify: `docs/reference/api-and-agent-surface.md`, `docs/reference/eal-plugin-catalog.md` (door section), `CHANGELOG.md`, `CLAUDE.md` (one paragraph under Measurement loop)
- Test: `tests/api/test_run_door_readout.py`

- [ ] **Step 1: Write the failing tests** — `test_run_lists_exclusions_by_door`, `test_report_section_present_only_when_excluded`, `test_report_never_counts_excluded_as_missed`.
- [ ] **Step 2: Run.** Expected: fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the full host suite with `.venv/bin` on `PATH`, plus `make validate` and `make test-backend-non-utc`. Expected: all green.
- [ ] **Step 5: Commit** the code and docs as separate `feat` and `docs` commits.

## Phase 3 — Get Started around doors

### Task 13: Get Started rebuilt from `/api/doors`

**Files:**
- Modify: `ui/src/components/console/setupProgress.js` (input gains `doors`; drop `readConfirmed` / `writeConfirmed` and the `collector` / `bvm` steps)
- Modify: `ui/src/components/console/GetStartedView.jsx`, `useSetupProgress.js`
- Test: `ui/src/components/console/__tests__/setupProgress.test.js`

Steps, in order:
1. `beacon`: the CortexSim beacon; required; verified from `/api/agents`.
2. `xdr_agent`: required; from `xdr_agent:<agent>`.
3. `tenant`: required.
4. Optional door steps: `ngfw` (links the Firewall wizard), `http_collector` (links Tenant › Collector targets), `cloud`, `identity`, `windows_events`, `syslog`.

Each optional step shows its door state and its `Check` button. Remove the Broker VM "514" copy. The `xdr_agent` step's copy: `The Cortex XDR agent must run on {host}. Most endpoint detections read its telemetry.`

- [ ] **Step 1: Write the failing tests** — `test_xdr_agent_step_required_and_unverified_without_door`, `test_no_localstorage_confirmations`, `test_optional_door_steps_show_state`, `test_attested_reads_attested_not_done_verified`.
- [ ] **Step 2: Run** vitest. Expected: fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** vitest and `vite build`. Expected: green.
- [ ] **Step 5: Commit** `feat(ui): Get Started checks each door, including the XDR agent`.

### Task 14: Wizard links and the re-check button

**Files:**
- Modify: `ui/src/components/console/FirewallView.jsx`, `CollectorTargetsPanel.jsx` (after a successful check, call `POST /api/doors/{key}/check` and show the door state)
- Modify: `ui/src/components/console/ScenarioPreflightCard.jsx` (`Re-check doors` runs the checks for the scenario's closed or `unknown` doors, then refreshes)
- Test: `ui/tests/e2e/15-doors-recheck.spec.ts` (browser), vitest for the button state

- [ ] **Step 1: Write the failing tests** — `recheck_button_only_when_door_not_open`, and e2e `firewall_check_updates_library_chip` (intercepted API).
- [ ] **Step 2: Run.** Expected: fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** vitest, `vite build` and the two e2e specs in Chrome. Expected: green. Verify on a parallel compose project (`:8889`) built from this branch, never by replacing `:8888`.
- [ ] **Step 5: Commit** `feat(ui): setup wizards and launch re-check update door state`.

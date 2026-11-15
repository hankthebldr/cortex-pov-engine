# CortexSim alongside the SOC Framework — Implementation Plan (Phases 0 and 1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** CortexSim knows when the SOC Framework is on a tenant and how its Auto Triage is set. Each run reports which XSIAM cases its alerts landed in and what happened to them. A corpus gate reports whether a scenario's detections share a native grouping key.

**Architecture:** Three pieces, three topic branches.
- **Part A, the probe.** A read-only `response_layer` preflight stage reads the framework's packs, lists and execution dataset. Its result is stored on the tenant integration and feeds Readiness and a launch advisory.
- **Part B, the case ledger.** A pure classifier turns the run's matched alerts and their cases into a `merge_class` and a per-case disposition. Reconcile builds it before scoring, and it is stored on `Run.case_ledger`. Its only effect on a verdict is that `SPLIT_SEALED` withholds a correlation FAIL.
- **Part C, the grouping gate.** A corpus gate reuses `TtpCatalog` to work out each detection's grouping keys.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy (SQLite), httpx (`XsiamClient`), the stdlib connector fetcher (`HttpFetcher`), React and vitest.

**Spec:** `docs/superpowers/specs/2026-11-11-socfw-coexistence-design.md` (approved 2026-11-11). Read §1, §2, §4, §5 and §10 before starting.

**Not in this plan:** spec §6 (Phase 2 observations) and §7's registry entries. They get their own plan once Part A has verified, against deathstar, the list-content path, the framework's AI-close marker and the dataset row shape. The §7 runbook *is* in this plan (Task B8), because §10 decision 2 makes its step 3 required now.

## Global Constraints

- **Read-only toward Cortex.** Add no write-class operation. `CORTEXSIM_XSIAM_ALLOW_WRITE` / `CORTEXSIM_XSIAM_ALLOW_DESTRUCTIVE` stay default-off.
- **Disposition and `merge_class` never feed detection verdicts:** not `detected`/`missed`, not MTTD, not a detection-family `tc_verdict`. **The one exception:** `merge_class == "SPLIT_SEALED"` withholds a Cross-Source Correlation Rate **FAIL** as `pending`. A PASS is never touched.
- **Closed vocabularies, copied exactly:**
  - case `closed_by` ∈ {`open`, `socfw_auto_triage`, `auto_resolved`, `other_resolved`};
  - issue `closed_by` adds `socfw_ai_close` and `unread`;
  - `merge_class` ∈ {`ONE_CASE`, `SPLIT_SEALED`, `SPLIT_NATIVE`, `LOW_ONLY_NO_CASE`, `NO_CASE_YET`, `PENDING`}.
- **An unrecognised tenant or framework shape raises** and surfaces as degraded with a code. It is never read as defaults, `open` or `ABSENT`.
- **Every framework string and shape CortexSim reads lives in `core/connectors/socfw.py`**, pinned to `soc-optimization-unified` **3.31.4** (secops-framework commit `d222ba87`).
- **`ABSENT` degrades preflight** (§10: every POV tenant is expected to run the framework).
- **Timestamps** are compared with the platform's own fields and coerced with `connectors/base.py::coerce_utc`. Every new test must pass under `make test-backend-non-utc`.
- **Every new tenant read** is counted in `queries_issued` and goes through the existing ledger, per-sweep caps and quota breaker.
- **XQL text** never uses `to_timestamp(current_time() - N)`, and never a future `to`; both return HTTP 500 on deathstar. Use the `timeframe` argument (`{"relativeTime": ms}`).
- **Fixed copy** (render it verbatim): `Tenant disposition is shown for context. It is not detection evidence.`
- **Withheld-FAIL reason** (verbatim): `fail withheld: a case was sealed by the response layer before the chain finished — the split is not the detection engine's`
- **tenant-verified stays 0.** Docs say "authored is not proven".
- **Branches:** each Part is its own topic branch cut from `dev`. Stage files by name (never `git add -A`/`.`). Gate A evidence includes proof that each new guard fails without its fix. Open a PR only when Henry asks.
- **Commands:**
  - `PY=/Users/henry/Github/Github_desktop/cortex-pov-engine/.venv/bin/python`
  - backend tests: `PYTHONPATH=core $PY -m pytest <path> -v`
  - UI tests: `cd ui && npx vitest run <path>`
  - In a fresh worktree, symlink `ui/node_modules` and put `.venv/bin` first on `PATH` for `make` gates.

## Deviations from the spec (decided here; flagged for review)

1. **`response_layer.state` gains `UNKNOWN`.** It is used when the installed-packs read fails or is unrecognised: without reading packs, neither present nor absent can be claimed. The spec's three states remain for every case where packs were read.
2. **The grouping check is a corpus gate with codes `G-01..G-05`, not a loader `S-29`.** It needs the card corpus (severity, `alert_fields`), which the scenario loader does not read.
3. **Issue status uses `POST /public_api/v1/issue/search/` filtered by `id`.** That filter field and the `status.progress` / `status.resolution_reason` / `status.resolution_comment` fields were observed on deathstar on 2026-11-10. If an alert's `external_id` doesn't come back as an issue id, the issue is `unread`, never `open`.
4. **The IP-to-host bridge check (spec §5.4) runs inside the case ledger, after the run, not in preflight.** Preflight is per tenant and has no run target. The check runs where the split is seen: on `SPLIT_NATIVE` with both IP-only and host-keyed alerts (Task B7).

## Review Focus

1. **A case id on an alert that `get_incidents` doesn't return** must make `merge_class` `PENDING`, with a reason naming the case, never `ONE_CASE`. Pinned by `test_missing_case_is_pending_not_one_case` (Task B1).
2. **An unrecognised case `status` value** must raise `CaseShapeError`, and the ledger must report code `CASE_STATUS_UNRECOGNIZED` with merge `PENDING`; it is never treated as open. Pinned by `test_unknown_status_raises` (B1) and `test_unrecognized_status_degrades_ledger` (B3).
3. **An epoch-ms `resolved_timestamp` compared with timezone-aware alert times on a non-UTC host** must classify the same way it does in UTC. Pinned by `test_split_sealed_timing_is_tz_independent` (B1).
4. **A framework uninstalled with its lists left behind** (povkit's uninstall leaves lists) must read `ABSENT` with `leftover_lists = true`, not `PRESENT`. Pinned by `test_leftover_lists_without_core_pack_is_absent` (A1).
5. **A triage score switched off in `SOCFWTenantConfig`** (threshold `None`) must set `score_off = true`, and the advisory must say every aged New unstarred case is eligible. Pinned by `test_triage_score_off_parsed` (A1) and `test_score_off_advisory_says_every_aged_case` (A5).

---

## Part A — response-layer probe

**Branch:** `feature/api-socfw-response-layer`, from `dev`.

### Task A1: Pinned framework facts and parsers

**Files:**
- Create: `core/connectors/socfw.py`
- Create: `tests/connectors/test_socfw_parsers.py`
- Create: `tests/fixtures/socfw/` holding:
  - `installed_packs.json`: a recorded `/xsoar/contentpacks/metadata/installed` reply. Use povkit's `internal/tenant` test fixtures as the shape source.
  - `list_names.json`.
  - `tenant_config.json`: a `SOCFWTenantConfig` document as `Packs/soc-optimization-unified/Scripts/SOCFWConfig/SOCFWConfig.py` writes it at 3.31.4. Copy it from that script's tests (`SOCFWConfig_test*.py`), not by hand.
  - `config_modes.json`: `SOCFWConfigModes_data.json` at 3.31.4.
  - `triage_resolve_comment.txt`: the `RESOLVE_COMMENT` value from `SOCAutoTriageScoreFilter.py:18-20`, verbatim, with the version on line 1.

**Interfaces:**
- Produces (module `connectors.socfw`):
  - Constants:
    - `FRAMEWORK_PINNED_VERSION = "3.31.4"`
    - `CORE_PACK_ID = "soc-optimization-unified"`
    - `LIFECYCLE_PACK_IDS = ("soc-framework-nist-ir", "soc-framework-nist-ir-ai")`
    - `CONFIG_LISTS = ("SOCFWTenantConfig", "SOCFWConfigModes")`
    - `EXECUTION_DATASET = "xsiam_socfw_ir_execution_raw"`
    - `CASE_LEDGER_DATASET = "xsiam_socfw_case_ledger_raw"`
    - `TRIAGE_RESOLVE_COMMENT_PREFIX = "SOC Framework Auto Triage:"`
    - `TRIAGE_DEFAULT_WINDOW_HOURS = 6.0`
    - `TRIAGE_DEFAULT_SCORE_THRESHOLD = 40`
    - `TRIAGE_JOB_NAME = "Auto Triage V3"`
  - `class ResponseLayerShapeError(ValueError)`
  - `@dataclass(frozen=True) class TenantConfigView: mode: Optional[str]; triage_window_hours: Optional[float]; triage_score_threshold: Optional[int]; triage_score_off: bool; ai_close_live: Optional[bool]`
  - `@dataclass(frozen=True) class ExecutionEvidence: triage_closed_production: int; ai_close_production: int`
  - `@dataclass(frozen=True) class AutoTriage: live: Optional[bool]; window_hours: Optional[float]; score_threshold: Optional[int]; score_off: bool; source: str`, where `source` ∈ {`tenant_config`, `job_default`, `xql_evidence`, `unknown`}
  - `@dataclass(frozen=True) class ResponseLayer`, with fields:
    - `state: str`: `ABSENT` | `PRESENT_UNREAD` | `PRESENT` | `UNKNOWN`
    - `code: str`: `RESPONSE_LAYER_OK` | `RESPONSE_LAYER_ABSENT` | `RESPONSE_LAYER_UNREAD` | `RESPONSE_LAYER_SHAPE_UNRECOGNIZED`
    - `framework_version: Optional[str]`
    - `packs: dict[str, str]`
    - `mode: Optional[str]`
    - `auto_triage: AutoTriage`
    - `ai_close: str`: `live` | `shadow` | `unknown`
    - `leftover_lists: bool`
    - `sources_used: tuple[str, ...]`
    - `read_at: datetime`

    plus `to_dict() -> dict` and `@classmethod from_dict(d: dict) -> ResponseLayer`.
  - Parsers and assembly:
    - `parse_installed_packs(reply: Any) -> dict[str, str]`, mapping pack id → version.
    - `parse_list_names(reply: Any) -> frozenset[str]`
    - `parse_tenant_config(doc: Any, modes_doc: Any) -> TenantConfigView`
    - `summarize_execution_rows(rows: list[dict]) -> ExecutionEvidence`
    - `assemble_response_layer(*, packs: Optional[dict[str, str]], list_names: Optional[frozenset[str]], tenant_config: Optional[TenantConfigView], evidence: Optional[ExecutionEvidence], sources_used: list[str], read_at: datetime, shape_error: Optional[str] = None) -> ResponseLayer`

- [ ] **Step 1: Write the failing tests.** Every fixture value is asserted exactly.

```python
def test_triage_prefix_matches_pinned_framework_comment():
    text = (FIX / "triage_resolve_comment.txt").read_text().splitlines()[1]
    assert text.startswith(socfw.TRIAGE_RESOLVE_COMMENT_PREFIX)

def test_installed_packs_parsed():
    packs = socfw.parse_installed_packs(load("installed_packs.json"))
    assert packs[socfw.CORE_PACK_ID] == "3.31.4"

def test_unrecognized_packs_shape_raises():
    with pytest.raises(socfw.ResponseLayerShapeError):
        socfw.parse_installed_packs({"unexpected": 1})

def test_leftover_lists_without_core_pack_is_absent():
    rl = socfw.assemble_response_layer(packs={"soc-crowdstrike-falcon": "2.2.7"},
        list_names=frozenset({"SOCFWTenantConfig"}), tenant_config=None, evidence=None,
        sources_used=["packs", "lists"], read_at=NOW)
    assert (rl.state, rl.code, rl.leftover_lists) == ("ABSENT", "RESPONSE_LAYER_ABSENT", True)

def test_packs_unreadable_is_unknown_not_absent():
    rl = socfw.assemble_response_layer(packs=None, list_names=None, tenant_config=None,
        evidence=None, sources_used=[], read_at=NOW, shape_error="unrecognized")
    assert (rl.state, rl.code) == ("UNKNOWN", "RESPONSE_LAYER_SHAPE_UNRECOGNIZED")

def test_pov_mode_parsed_with_ai_close_live():
    v = socfw.parse_tenant_config(load("tenant_config.json"), load("config_modes.json"))
    assert v.mode == "pov" and v.ai_close_live is True

def test_triage_score_off_parsed():
    doc = load("tenant_config.json"); doc["triage"] = {"score_threshold": "off"}
    v = socfw.parse_tenant_config(doc, load("config_modes.json"))
    assert v.triage_score_off is True and v.triage_score_threshold is None

def test_execution_evidence_proves_triage_live():
    ev = socfw.summarize_execution_rows([{"action_taken": "auto_triage_closed",
                                          "execution_mode": "production", "n": 3}])
    rl = socfw.assemble_response_layer(packs={socfw.CORE_PACK_ID: "3.31.4"},
        list_names=frozenset(), tenant_config=None, evidence=ev,
        sources_used=["packs", "lists", "xql"], read_at=NOW)
    assert rl.state == "PRESENT_UNREAD" and rl.auto_triage.live is True
    assert rl.auto_triage.source == "xql_evidence"

def test_round_trip_dict():
    rl = socfw.assemble_response_layer(packs={socfw.CORE_PACK_ID: "3.31.4"},
        list_names=frozenset(socfw.CONFIG_LISTS),
        tenant_config=socfw.parse_tenant_config(load("tenant_config.json"), load("config_modes.json")),
        evidence=None, sources_used=["packs", "lists"], read_at=NOW)
    assert socfw.ResponseLayer.from_dict(rl.to_dict()) == rl
```

  For `tenant_config.json`, adjust the edited key in `test_triage_score_off_parsed` to the real section name `SOCFWConfig.py` writes for `command=triage`. Verify it in that script before writing the test.

- [ ] **Step 2: Run** `PYTHONPATH=core $PY -m pytest tests/connectors/test_socfw_parsers.py -v`. Expected: FAIL, `ModuleNotFoundError: connectors.socfw`.
- [ ] **Step 3: Implement `core/connectors/socfw.py`.**
  - **Mode.** `parse_tenant_config` reads the mode from wherever `SOCFWConfig.py` records it. Live AI close is `shadow["soc-close-issue"]["ai"] is False`, after applying the mode's `shadow` map from `SOCFWConfigModes` and then tenant overrides, in that order, the same precedence as `SOCCommandWrapper.py:658-687`.
  - **Triage and the job switch.** The triage window and threshold come from the `triage` section, with string values coerced; `"off"`/empty means score off. Auto Triage `live` comes from evidence first (`triage_closed_production > 0` → True), then the mode's `jobs[TRIAGE_JOB_NAME]` (`"on"`/`"off"`), else `None`.
  - **Unrecognised input.** Any key-type mismatch raises `ResponseLayerShapeError` with the path in the message.
- [ ] **Step 4: Run** the same command. Expected: PASS (9 tests).
- [ ] **Step 5: Commit** `git add core/connectors/socfw.py tests/connectors/test_socfw_parsers.py tests/fixtures/socfw/*` and then `git commit -m "feat(api): pin SOC Framework facts and parse its tenant state"`.

### Task A2: The `response_layer` preflight stage

**Files:**
- Modify: `core/connectors/preflight.py`. Add codes near `:50-68`; add `_probe_response_layer`; call it from `preflight_tenant` (`:371-490`) after the clock stage; add `"response_layer"` to each `_skip_rest([...])` list in `preflight_tenant`.
- Create: `core/integrations/xsiam/operations/packs/socfw.yml`. This is spec §4.1's catalog entry for the new reads. It holds three `access_class: read` operations, each tagged `povkit-observed`:
  - `OP-SOCFW-PACKS-INSTALLED`: `GET /xsoar/contentpacks/metadata/installed`;
  - `OP-SOCFW-LIST-NAMES`: `GET /xsoar/lists/names`;
  - `OP-SOCFW-LIST-GET`: the list-content path you verify in Step 3.

  Follow the header and `provenance` comment style of `cases-issues.yml`. The operations loader must accept the file; run its existing tests.
- Test: `tests/connectors/test_preflight_response_layer.py`. Use an `XsiamClient` over `httpx.MockTransport`, following the existing tenant-preflight tests.

**Interfaces:**
- Consumes: Task A1 (`connectors.socfw`).
- Produces:
  - Constants `PF_RESPONSE_LAYER_ABSENT`, `PF_RESPONSE_LAYER_UNREAD`, `PF_RESPONSE_LAYER_SHAPE_UNRECOGNIZED`.
  - `async def _probe_response_layer(client: Any, report: PreflightReport, now: datetime) -> Stage`
  - `preflight_tenant(..., response_layer: bool = True)`. The `Stage` with id `"response_layer"` carries `extra={"response_layer": ResponseLayer.to_dict()}`.

- [ ] **Step 1: Write the failing tests.**

```python
async def test_present_framework_stage_ok():  # packs + names + list content + xql all 200
    report = await preflight_tenant(client(...), now=NOW)
    st = stage(report, "response_layer")
    assert st.status == "ok" and st.extra["response_layer"]["state"] == "PRESENT"

async def test_absent_framework_degrades_overall():
    st = stage(report, "response_layer")
    assert st.status == "degraded" and st.code == "PF_RESPONSE_LAYER_ABSENT"
    assert report.overall == "degraded"

async def test_list_content_404_is_present_unread():
    assert stage(report, "response_layer").code == "PF_RESPONSE_LAYER_UNREAD"

async def test_unrecognized_packs_reply_is_shape_code_not_absent():
    st = stage(report, "response_layer")
    assert st.code == "PF_RESPONSE_LAYER_SHAPE_UNRECOGNIZED"
    assert st.extra["response_layer"]["state"] == "UNKNOWN"

async def test_queries_issued_counts_each_call():   # 4 calls when all succeed
    assert report.queries_issued == baseline + 4

async def test_stage_skipped_when_xql_blocked():
    assert stage(report, "response_layer").status == "skipped"

async def test_opt_out():
    report = await preflight_tenant(c, now=NOW, response_layer=False)
    assert all(s.id != "response_layer" for s in report.stages)

def test_xql_text_has_no_current_time_arithmetic():
    assert "current_time()" not in RESPONSE_LAYER_XQL
```

- [ ] **Step 2: Run** `PYTHONPATH=core $PY -m pytest tests/connectors/test_preflight_response_layer.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** The four calls, in order:
  1. `client.request("GET", "/xsoar/contentpacks/metadata/installed")`
  2. `client.request("GET", "/xsoar/lists/names")`
  3. **List content.** One read per `CONFIG_LISTS` entry, through a single helper `_read_list(client, name)`. Before coding, confirm the path against povkit's `internal/tenant` and the XSOAR lists API; record the source in a comment. A 404/405 there means `tenant_config=None`.
  4. **XQL.** `client.run_xql(RESPONSE_LAYER_XQL, {"relativeTime": 86_400_000})`, where `RESPONSE_LAYER_XQL = f"dataset = {EXECUTION_DATASET} | comp count() as n by action_taken, execution_mode"`, with rows read through `queries.result_rows`.

  Each call adds 1 to `report.queries_issued`, whatever its outcome. An auth or forbidden reply on (1) means `UNKNOWN` with remediation naming the credential role. **Status mapping:** `PRESENT` → `OK`; `ABSENT`, `PRESENT_UNREAD` and `UNKNOWN` → `DEGRADED`.
- [ ] **Step 4: Run** the tests, plus `PYTHONPATH=core $PY -m pytest tests/connectors -q`. Expected: PASS, with nothing else red.
- [ ] **Step 5: Commit** (`preflight.py`, `operations/packs/socfw.yml` and the test) with the message `feat(api): add the response_layer preflight stage`.

### Task A3: Store the probe and surface it on Readiness and health

**Files:**
- Modify: `core/models.py`. On `IntegrationCredential` (`:1063-1104`), add `response_layer: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)` and `response_layer_read_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)`, and include both in `to_dict` (ISO string).
- Modify: `core/database.py`. Add `_migrate_integration_response_layer_columns(connection)` following `_migrate_runtime_dependency_columns` (`:329-353`), adding both columns to the integration table, and register it in the `init_db` list (`:172-179`).
- Modify: `core/api/connectors.py`:
  - `preflight_connector` (`:246`) gains `response_layer: bool = Query(True)`.
  - `_preflight_tenant` (`:332`) passes it through.
  - After the report, if a `response_layer` stage carries `extra`, it writes `row.response_layer = extra["response_layer"]` and `row.response_layer_read_at = utcnow()`, then commits.
- Modify: `core/api/readiness.py`. In `get_readiness` (`:59-100`), each integration gains a `"response_layer"` block, `None` when nothing is stored, holding:
  - the stored dict;
  - `read_at`;
  - `stale` (older than 24 h);
  - `proves: "the SOC Framework is configured on this tenant"`;
  - `does_not_prove: "that the framework handled any CortexSim case"`.

  It makes no outbound call.
- Modify: `core/api/health.py`. In `NOT_CHECKED` (`:61`), add `{"what": "socfw_response_layer", "why": "presence and Auto Triage settings need tenant reads, which health never makes", "checked_by": "POST /api/connectors/xsiam_tenant/preflight (stage response_layer)"}`.
- Test: `tests/api/test_response_layer_persist.py`, `tests/api/test_readiness_response_layer.py`, `tests/api/test_health_not_checked_socfw.py`.

**Interfaces:**
- Consumes: A2's `extra["response_layer"]`.
- Produces: `IntegrationCredential.response_layer` and `.response_layer_read_at`, which Task A5 reads.

- [ ] **Step 1: Write the failing tests.**
  - `test_preflight_persists_response_layer`: after a mocked preflight, the row's `response_layer["state"] == "PRESENT"` and `read_at` is set.
  - `test_readiness_marks_stale_after_24h`: a stored `read_at` 25 h ago gives `stale is True`.
  - `test_readiness_makes_no_outbound_call`: `outbound_calls == 0`, unchanged.
  - `test_not_checked_lists_socfw`: `"socfw_response_layer" in [i["what"] for i in payload["not_checked"]]`.
  - `test_migration_adds_columns_to_existing_db`: start from a pre-migration schema and assert both columns exist after `init_db`.
- [ ] **Step 2: Run** `PYTHONPATH=core $PY -m pytest tests/api/test_response_layer_persist.py tests/api/test_readiness_response_layer.py tests/api/test_health_not_checked_socfw.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the four modifications.
- [ ] **Step 4: Run** the three files, plus `tests/api -q`. Expected: PASS.
- [ ] **Step 5: Commit** with the message `feat(api): store the response layer and show it on Readiness`.

### Task A4: The Readiness row in the console

**Files:**
- Modify: `ui/src/components/console/ReadinessView.jsx`. Add a "SOC Framework" row under each `xsiam_tenant` integration's ladder; it is not a fifth rung.
- Test: `ui/src/components/console/__tests__/ReadinessResponseLayer.test.jsx`

**Interfaces:**
- Consumes: A3's `integrations[].response_layer`.

- [ ] **Step 1: Write the failing tests.**
  - `renders state, version and mode`: PRESENT, 3.31.4, pov.
  - `renders Auto Triage as "live · 6 h · score ≤ 40"`, and as `"live · 6 h · every aged case"` when `score_off`.
  - `renders the proves / does-not-prove lines verbatim`.
  - `ABSENT renders as degraded with the runbook link`.
  - `stale renders "unknown — run preflight"`.
  - `null response_layer renders "not probed"`, never ABSENT.
- [ ] **Step 2: Run** `cd ui && npx vitest run src/components/console/__tests__/ReadinessResponseLayer.test.jsx`. Expected: FAIL.
- [ ] **Step 3: Implement** with the existing Cortex tokens (`cortex-theme.css`) and no new colours. The runbook link points to `docs/lab/2026-11-11-run-alongside-soc-framework.md` (written in Task B8).
- [ ] **Step 4: Run** the same command, plus `cd ui && npx vitest run src/components/console`. Expected: PASS.
- [ ] **Step 5: Commit** with the message `feat(ui): show the SOC Framework row on Readiness`.

### Task A5: The `SOCFW_TRIAGE_MAY_SEAL` launch advisory

**Files:**
- Create: `core/engine/socfw_advisory.py`
- Modify: `core/engine/orchestrator.py`. Where `LaunchResult.warnings` is assembled (`:953-981`), append `triage_advisories(...)` for each `xsiam_tenant` integration's stored layer. It never returns a refusal.
- Test: `tests/engine/test_socfw_advisory.py`, plus one launch-path test in `tests/api/test_launch_socfw_advisory.py`.

**Interfaces:**
- Consumes: A3's stored `response_layer` and `response_layer_read_at`.
- Produces:
  - Constants: `SOCFW_TRIAGE_MAY_SEAL = "SOCFW_TRIAGE_MAY_SEAL"`, `SOCFW_RESPONSE_LAYER_UNKNOWN = "SOCFW_RESPONSE_LAYER_UNKNOWN"`, `STALE_AFTER = timedelta(hours=24)`.
  - `triage_advisories(*, tenant: str, detection_types: set[str], paired: bool, response_layer: Optional[dict], read_at: Optional[datetime], now: datetime) -> list[dict]`. Each dict is `{"code", "level", "tenant", "detail", "remediation"}`, and `level` ∈ {`warn`, `info`}.

- [ ] **Step 1: Write the failing tests.**

```python
def test_paired_run_with_live_triage_warns():
    out = triage_advisories(tenant="t", detection_types={"BIOC"}, paired=True,
                            response_layer=LIVE, read_at=NOW, now=NOW)
    assert [(a["code"], a["level"]) for a in out] == [("SOCFW_TRIAGE_MAY_SEAL", "warn")]

def test_abioc_or_analytics_scenario_is_info():
    assert ... == [("SOCFW_TRIAGE_MAY_SEAL", "info")]

def test_no_late_detection_and_not_paired_is_silent():
    assert triage_advisories(..., detection_types={"BIOC"}, paired=False, ...) == []

def test_stale_layer_says_unknown_run_preflight():
    assert out[0]["code"] == "SOCFW_RESPONSE_LAYER_UNKNOWN" and "preflight" in out[0]["detail"]

def test_remediation_lists_three_tenant_side_options():
    r = out[0]["remediation"]
    assert "star" in r and "!SOCFWConfig command=triage" in r and "dry_run" in r
    assert "docs/lab/2026-11-11-run-alongside-soc-framework.md" in r

def test_score_off_advisory_says_every_aged_case():
    assert "every New unstarred case older than" in out[0]["detail"]

async def test_launch_returns_advisory_and_still_launches():   # tests/api/...
    resp = await client.post("/api/runs", json=paired_launch)
    assert resp.status_code in (200, 201)
    assert any(w["code"] == "SOCFW_TRIAGE_MAY_SEAL" for w in resp.json()["warnings"])
```

- [ ] **Step 2: Run** `PYTHONPATH=core $PY -m pytest tests/engine/test_socfw_advisory.py tests/api/test_launch_socfw_advisory.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** `detection_types` is the set of `detection_type` across the scenario's `steps[].expected_detections`. `paired` means the launch request asks for a pair, or `paired_with_run_id` is set. The detail names the stored `window_hours` and `score_threshold`.
- [ ] **Step 4: Run** the same command, plus `tests/engine -q`. Expected: PASS.
- [ ] **Step 5: Commit** with the message `feat(api): advise at launch when Auto Triage may seal the case`.

### Task A6: Part A docs, ground truth and Gate A

**Files:**
- Modify: `docs/reference/api-and-agent-surface.md`: the preflight `response_layer` stage, the `response_layer` query parameter, and the new codes.
- Modify: `CHANGELOG.md`: an `[Unreleased]` entry.
- Modify: `docs/reference/ground-truth.{json,md}`, via `make ground-truth` (needs `make build`).

- [ ] **Step 1:** Run `make ground-truth`, then `make check-ground-truth`. Expected: exit 0.
- [ ] **Step 2:** Run the five host suites and `make validate`. All must be green except the known `check-adapters` submodule caveat in a worktree, which you state if it appears. Also run `make test-backend-non-utc`. Expected: exit 0.
- [ ] **Step 3: Gate A evidence.** Revert each guard locally and record the red test:
  - in `assemble_response_layer`, the "core pack required for PRESENT" check;
  - in `ResponseLayerShapeError`, the raise.
- [ ] **Step 4: Commit** the docs and ground truth with the message `docs(api): document the response_layer stage`.

---

## Part B — run case ledger

**Branch:** `feature/api-run-case-ledger`, from `dev`. It can start before Part A merges. Its only tie to Part A is the triage prefix constant (see Task B1). In this plan `ai_close_marker` is always `None`, so AI-closed issues read `other_resolved`; the Phase 2 plan verifies the marker.

### Task B1: The case-ledger classifier (pure)

**Files:**
- Create: `core/connectors/case_ledger.py`
- Test: `tests/connectors/test_case_ledger.py`

**Interfaces:**
- Consumes: `connectors.base.ObservedAlert`, `ObservedIncident` and `coerce_utc`. Takes `TRIAGE_RESOLVE_COMMENT_PREFIX` from `connectors.socfw` once Part A has merged; until then, a local constant with a `TODO(merge A)` that Task B8 removes.
- Produces (module `connectors.case_ledger`):
  - Constants:
    - `MEDIUM_PLUS = frozenset({"medium", "high", "critical"})`
    - `CASE_CLOSED_BY`, `ISSUE_CLOSED_BY`, `MERGE_CLASSES`: the tuples from Global Constraints.
    - `CASE_DISCLAIMER = "Tenant disposition is shown for context. It is not detection evidence."`
    - `CASE_STATUS_MAP: dict[str, str]`, covering:
      - `new` and `under_investigation` → `open`;
      - `resolved_auto_resolve` → `auto_resolved`;
      - `resolved_threat_handled`, `resolved_known_issue`, `resolved_duplicate`, `resolved_false_positive`, `resolved_true_positive`, `resolved_security_testing`, `resolved_other` → `other_resolved`.

      The values come from secops-framework `Packs/soc-framework-nist-ir/Scripts/SOCFWCaseSelect/SOCFWCaseSelect.py`; cite that file in a comment.
  - `class CaseShapeError(ValueError)`
  - `@dataclass(frozen=True) class LedgerAlert: external_id: str; observed_at: datetime; severity: Optional[str]; case_id: Optional[str]; issue_status: Optional[str]; issue_reason: Optional[str]; issue_comment: Optional[str]`
  - `@dataclass(frozen=True) class LedgerCase: case_id: str; status: str; resolve_comment: Optional[str]; resolved_at: Optional[datetime]; starred: bool; assigned: bool; xdr_url: Optional[str]; alert_count: int`
  - Functions:
    - `ledger_alert(a: ObservedAlert, issue_status: Optional[dict[str, Optional[str]]]) -> LedgerAlert`. `issue_status` is one entry of B2's `pull_issue_status` map; `None` gives unset issue fields.
    - `ledger_case_from_incident(i: ObservedIncident) -> LedgerCase`. Raises `CaseShapeError` when `status ∉ CASE_STATUS_MAP`.
    - `case_closed_by(c: LedgerCase) -> str`
    - `case_sealed(c: LedgerCase) -> bool`
    - `issue_closed_by(a: LedgerAlert, *, ai_close_marker: Optional[str] = None) -> str`
    - `classify_merge(alerts: Sequence[LedgerAlert], cases: Mapping[str, LedgerCase], *, basis: str) -> tuple[str, str]`, returning `(merge_class, reason)`.
    - `build_case_ledger(alerts: Sequence[LedgerAlert], cases: Mapping[str, LedgerCase], *, basis: str, read_at: datetime, ai_close_marker: Optional[str] = None, code: str = "CASE_LEDGER_OK") -> dict`
    - `ledger_from_dict(d: dict) -> tuple[list[LedgerAlert], dict[str, LedgerCase]]`
    - `pair_merge(a: Optional[dict], b: Optional[dict]) -> dict`

**`classify_merge` order.** The tests pin this; apply the first rule that matches:
1. `basis != "alert_incident_ids"` → `PENDING`.
2. Take the Medium+ alerts. If there are none: all alerts `low` → `LOW_ONLY_NO_CASE`, otherwise `PENDING`.
3. No Medium+ alert has a case id → `NO_CASE_YET`.
4. Fewer than two Medium+ alerts → `PENDING`.
5. A referenced case id is missing from `cases` → `PENDING`, with a reason naming it.
6. Some Medium+ alerts still lack a case id → `PENDING`.
7. One distinct case → `ONE_CASE`.
8. Otherwise, order cases by their earliest alert. If any earlier case is `case_sealed`, with `resolved_at` before a later case's earliest alert → `SPLIT_SEALED`, naming both cases. Otherwise → `SPLIT_NATIVE`.

**`build_case_ledger` output shape:**
- top level: `{read_at, basis, merge_class, merge_reason, code, shared_with_other_runs, cases: [...], alerts: [...]}`;
- each case: `{case_id, status, closed_by, sealed, starred, assigned, xdr_url, resolved_at, resolve_comment, issues_from_run, issues_other, issues_closed_first}`, where:
  - `issues_other = max(0, alert_count - issues_from_run)`;
  - `issues_closed_first` is True when `closed_by == "auto_resolved"` and every one of the run's issues in the case has an `issue_closed_by` other than `open` and `unread`. This is spec §4.2's "the record says so": it flags that the framework's AI close, not the platform, may have caused the auto-resolve.
- each alert: `{external_id, observed_at, severity, case_id, issue_closed_by}`.

- [ ] **Step 1: Write the failing tests.** These are table tests; every name below is one test.
  - `test_one_case`: 3 high alerts in case A → `ONE_CASE`.
  - `test_low_only_no_case`: 2 low alerts, no case → `LOW_ONLY_NO_CASE`.
  - `test_no_case_yet`: 2 high alerts, `case_id=None` → `NO_CASE_YET`.
  - `test_single_medium_is_pending`.
  - `test_host_incidents_basis_is_pending`: `basis="host_incidents"` → `PENDING`.
  - `test_missing_case_is_pending_not_one_case`: alerts reference A, but `cases` holds only B → `PENDING`, and `"A"` is in the reason.
  - `test_split_native`: cases A and B, neither sealed → `SPLIT_NATIVE`.
  - `test_split_sealed`: A is `resolved_other`, resolved at 10:00; B's first alert is at 10:05 → `SPLIT_SEALED`, and both ids are in the reason.
  - `test_auto_resolved_is_not_sealed`: the same as above, but A is `resolved_auto_resolve` → `SPLIT_NATIVE`.
  - `test_sealed_after_later_case_is_native`: A resolved at 10:10, B's first alert at 10:05 → `SPLIT_NATIVE`.
  - `test_split_sealed_timing_is_tz_independent`: epoch-ms `resolved_timestamp` against aware alert times, with `time.tzset()` under `TZ=America/Los_Angeles` → `SPLIT_SEALED`.
  - `test_triage_comment_is_socfw_auto_triage`: `resolved_other` plus a comment starting with the prefix → `socfw_auto_triage`, `sealed=True`.
  - `test_unknown_status_raises`: `status="resolved_mystery"` raises `CaseShapeError`.
  - `test_issue_unread_when_status_missing`: `issue_status=None` → `"unread"`, never `"open"`.
  - `test_shared_with_other_runs`: `alert_count=5`, 2 from the run → `issues_other == 3` and `shared_with_other_runs is True`.
  - `test_auto_resolved_after_issue_close_is_flagged`: the case is `resolved_auto_resolve`, and both run issues are `Resolved - False Positive` → `issues_closed_first is True`.
  - `test_pair_merge_spans_both_runs`: run 1's case A is sealed before run 2's case B → `pair_merge(...)["merge_class"] == "SPLIT_SEALED"`.
  - `test_round_trip`: `ledger_from_dict(build_case_ledger(...))` reproduces the inputs.
- [ ] **Step 2: Run** `PYTHONPATH=core $PY -m pytest tests/connectors/test_case_ledger.py -v`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement** in `core/connectors/case_ledger.py`, applying `classify_merge` in exactly the order above.
- [ ] **Step 4: Run** the same command. Expected: PASS (18 tests).
- [ ] **Step 5: Commit** with the message `feat(api): classify a run's case merge and disposition`.

### Task B2: Connector reads for cases and issue status

**Files:**
- Modify: `core/connectors/xsiam.py`. Add two methods after `pull_incidents` (`:98-118`), and the class constant `_ISSUES_SEARCH_PATH = "/public_api/v1/issue/search/"`.
- Test: `tests/connectors/test_xsiam_pull_cases.py`, using an injected `HttpFetcher` like the existing connector tests.

**Interfaces:**
- Produces:
  - `XsiamConnector.pull_cases(self, cfg: ConnectorConfig, case_ids: Sequence[str]) -> PullResult`:
    - posts to the incidents path with `{"request_data": {"filters": [{"field": "incident_id_list", "operator": "in", "value": chunk}], "search_from": 0, "search_to": len(chunk)}}`, in chunks of 100;
    - puts the rows on `.incidents` through `_normalize_incident`, keeping `raw`;
    - sets `detail["pages"]` to the chunk count.
  - `XsiamConnector.pull_issue_status(self, cfg: ConnectorConfig, issue_ids: Sequence[str]) -> dict[str, dict[str, Optional[str]]]`:
    - maps id → `{"status", "reason", "comment"}`, read from `status.progress`, `status.resolution_reason` and `status.resolution_comment`;
    - filter `{"field": "id", "operator": "in", "value": ids}`, ids sent as ints when numeric, chunks of 100;
    - leaves an id the tenant doesn't return out of the map.
  - The error vocabulary is the same as `_harvest` (codes from `integrations/xsiam/codes.py`).

- [ ] **Step 1: Write the failing tests.**
  - `test_pull_cases_body_shape`: the posted JSON equals the body above.
  - `test_pull_cases_chunks_at_100`: 230 ids → 3 posts, `detail["pages"] == 3`.
  - `test_pull_cases_keeps_raw_status_and_comment`.
  - `test_pull_cases_http_401_is_auth_code`: `ok False` with the auth code from `codes.py`.
  - `test_unrecognised_envelope_raises_not_empty`: a reply lacking `reply.incidents` / `reply.DATA` is `ok False` with the shape code, never `ok True` with an empty list.
  - `test_pull_issue_status_reads_dotted_fields`.
  - `test_pull_issue_status_omits_unreturned_ids`.
- [ ] **Step 2: Run** `PYTHONPATH=core $PY -m pytest tests/connectors/test_xsiam_pull_cases.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** both methods, reusing `_fetch`, `_parse_page` and the error mapping. Don't write a second envelope unwrapper (see the CLAUDE.md "one substrate" rule).
- [ ] **Step 4: Run** the same command, plus `tests/connectors -q`. Expected: PASS.
- [ ] **Step 5: Commit** with the message `feat(api): read cases and issue status for a run's alerts`.

### Task B3: Build the ledger inside reconcile and store it on the run

**Files:**
- Modify: `core/models.py`. `Run` (`:187`) gains `case_ledger: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)`, included in `to_dict`.
- Modify: `core/database.py`. Add `("case_ledger", "JSON")` to a new `_migrate_case_ledger_columns`, registered in `init_db`.
- Modify: `core/connectors/service.py`:
  - add `build_and_store_case_ledger`;
  - in `reconcile_run`, after `ctx` is built (`:212-217`) and **before** `apply_verdicts` (`:236`), call it when `any(ctx["alert_incidents"].values())`;
  - set `ctx["merge_class"]`;
  - add its queries to `summary["queries_issued"]`;
  - set `summary["case_ledger"] = {"merge_class", "code"}`.
- Test: `tests/connectors/test_reconcile_case_ledger.py`

**Interfaces:**
- Consumes: B1 and B2.
- Produces: `async def build_and_store_case_ledger(db: AsyncSession, run: Run, *, conn: XsiamConnector, cfg: ConnectorConfig, alerts: list[ObservedAlert], basis: str, now: Optional[datetime] = None) -> tuple[dict, int]`, returning `(ledger, queries_issued)`. It never raises on a tenant failure. Instead:
  - a failed read gives a ledger with `code="CASE_READ_FAILED"` and `merge_class="PENDING"`;
  - a `CaseShapeError` gives `code="CASE_STATUS_UNRECOGNIZED"` and `PENDING`.

- [ ] **Step 1: Write the failing tests.**
  - `test_reconcile_stores_case_ledger`: a mocked pull with 2 high alerts in case A → `run.case_ledger["merge_class"] == "ONE_CASE"`.
  - `test_reconcile_counts_case_queries`: `queries_issued` rises by exactly the case and issue pages.
  - `test_unrecognized_status_degrades_ledger`: `code == "CASE_STATUS_UNRECOGNIZED"` and `merge_class == "PENDING"`, and reconcile still succeeds.
  - `test_case_read_failure_does_not_fail_reconcile`.
  - `test_detection_verdicts_identical_with_and_without_ledger`: run the same fixture twice, with `pull_cases` stubbed to return the case and then to fail. Result `observed_at`, outcome, MTTD and `tc_verdict` must be byte-identical for an Accuracy-KPI scenario.
- [ ] **Step 2: Run** `PYTHONPATH=core $PY -m pytest tests/connectors/test_reconcile_case_ledger.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** The `LedgerAlert` list is `[ledger_alert(by_ext[e], status_map.get(e)) for e in matched_ext]`, where `status_map` comes from `pull_issue_status`. `Run.to_dict` carries `case_ledger`, so the MCP run-read tool returns it with no further change (spec §4.4).
- [ ] **Step 4: Run** the same command, plus `tests/connectors -q` and `tests/api -q`. Expected: PASS.
- [ ] **Step 5: Commit** with the message `feat(api): build the case ledger during reconcile`.

### Task B4: `SPLIT_SEALED` withholds a correlation FAIL

**Files:**
- Modify: `core/connectors/measures.py`:
  - `guard_measurement` (`:157`) gains `sealed_split: bool = False`, plus a module constant `SEALED_SPLIT_REASON` holding the verbatim reason from Global Constraints;
  - `build_measurement`'s correlation branch passes `sealed_split=(ctx.get("merge_class") == "SPLIT_SEALED")`.
- Test: `tests/connectors/test_measures_sealed_split.py`

**Interfaces:**
- Consumes: `ctx["merge_class"]` from B3.

- [ ] **Step 1: Write the failing tests.**

```python
def test_sealed_split_withholds_correlation_fail():
    value, rec = build_measurement("Cross-Source Correlation Rate", THRESH_80,
        results, {"basis": "reconcile", "alert_incidents": {"a1": "A", "a2": "B"},
                  "merge_class": "SPLIT_SEALED"})
    assert value is None and rec["withheld"] == SEALED_SPLIT_REASON

def test_sealed_split_never_touches_a_pass():
    # alerts all in one case: the value passes; merge_class forced to SPLIT_SEALED
    assert value == 100.0 and "withheld" not in rec

def test_native_split_fail_stands():
    assert value == 0.0 and rec["scored"] == 0.0     # merge_class SPLIT_NATIVE

def test_accuracy_family_ignores_merge_class():
    a = build_measurement("Detection Accuracy", T, results, ctx_without)
    b = build_measurement("Detection Accuracy", T, results, {**ctx_without, "merge_class": "SPLIT_SEALED"})
    assert a == b
```

- [ ] **Step 2: Run** `PYTHONPATH=core $PY -m pytest tests/connectors/test_measures_sealed_split.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** Add one branch in `guard_measurement`, after the `truncated` check: `provisional == FAIL and sealed_split` → `(None, SEALED_SPLIT_REASON)`.
- [ ] **Step 4: Run** the same command, plus `tests/connectors -q`. Expected: PASS.
- [ ] **Step 5: Commit** with the message `fix(api): a case sealed by the response layer withholds the correlation FAIL`.

### Task B5: Refresh the ledger, sweep it, and merge across a pair

**Files:**
- Modify: `core/config.py`. Add `CORTEXSIM_CASE_LEDGER_WINDOW_HOURS: int = 24` and `CORTEXSIM_CASE_LEDGER_MAX_RUNS_PER_SWEEP: int = 20`.
- Modify: `core/connectors/service.py`. Add `refresh_case_ledger` and `refresh_case_ledgers_once`. In `auto_reconcile_loop` (`:1564`), call `refresh_case_ledgers_once` after `auto_reconcile_once`, under the same flag and the same quota breaker.
- Modify: `core/api/runs.py`:
  - add `POST /{run_id}/case-ledger/refresh`, mapping a `ReconcileError` the same way the reconcile route does;
  - `get_run_pair` (`:886-907`) adds `"merge": pair_merge(run.case_ledger, partner.case_ledger)`.
- Test: `tests/api/test_case_ledger_refresh.py`, `tests/connectors/test_case_ledger_sweep.py`

**Interfaces:**
- Produces:
  - `async def refresh_case_ledger(db: AsyncSession, run: Run, *, fetcher: Optional[HttpFetcher] = None) -> dict`. It re-reads the cases named in the stored ledger and keeps the stored alerts. It raises `ReconcileError("NO_CASE_LEDGER", …)` when the run has none.
  - `async def refresh_case_ledgers_once(db: AsyncSession, *, window_hours: int, max_runs: int, fetcher: Optional[HttpFetcher] = None) -> dict[str, int]`, returning `{"refreshed", "skipped", "queries_issued"}`.

- [ ] **Step 1: Write the failing tests.**
  - `test_refresh_rereads_cases_keeps_alerts`.
  - `test_refresh_without_ledger_is_409_no_case_ledger`.
  - `test_sweep_skips_runs_older_than_window`.
  - `test_sweep_respects_max_runs`.
  - `test_sweep_stops_on_quota_error`.
  - `test_pair_endpoint_carries_merge`.
- [ ] **Step 2: Run** `PYTHONPATH=core $PY -m pytest tests/api/test_case_ledger_refresh.py tests/connectors/test_case_ledger_sweep.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the code above.
- [ ] **Step 4: Run** the same command, plus `tests/api -q`. Expected: PASS.
- [ ] **Step 5: Commit** with the message `feat(api): refresh case ledgers and merge across a pair`.

### Task B6: The Case section in the report and the run view

**Files:**
- Modify: `core/api/runs.py`. In the report route (`:909-1180`), the markdown gains a `## Case` section built from `run.case_ledger`, and the `json` format includes `case_ledger`. With no ledger, the section says `Not read yet — reconcile the run.`
- Create: `ui/src/components/console/RunCasePanel.jsx`
- Modify: `ui/src/components/console/RunDetailView.jsx`. Render `<RunCasePanel ledger={run.case_ledger} />`.
- Test: `tests/api/test_report_case_section.py`, `ui/src/components/console/__tests__/RunCasePanel.test.jsx`

**Interfaces:**
- Consumes: `CASE_DISCLAIMER` (B1) and `run.case_ledger`.

- [ ] **Step 1: Write the failing tests.**
  - Backend:
    - `test_markdown_case_section_has_disclaimer_verbatim`;
    - `test_markdown_lists_each_case_with_closed_by_and_link`;
    - `test_markdown_without_ledger_says_not_read`.
  - UI:
    - `renders merge class and reason`;
    - `renders one badge per case with closed_by`;
    - `renders the disclaimer verbatim`;
    - `renders read_at`;
    - `renders shared-with-other-runs count`;
    - `null ledger renders "not read yet"`.
- [ ] **Step 2: Run** `PYTHONPATH=core $PY -m pytest tests/api/test_report_case_section.py -v` and `cd ui && npx vitest run src/components/console/__tests__/RunCasePanel.test.jsx`. Expected: FAIL.
- [ ] **Step 3: Implement.** Case links use `xdr_url`; colours and styling use Cortex tokens.
- [ ] **Step 4: Run** both suites. Expected: PASS.
- [ ] **Step 5: Commit** with the message `feat(ui): show the run's case and disposition`.

### Task B7: The IP-to-host bridge diagnosis

**Files:**
- Modify: `core/connectors/xsiam.py`. Add `pull_endpoint_ips`, using the get-endpoint path already in `core/integrations/xsiam/operations/packs/endpoint-management.yml`, filtered by hostname.
- Modify: `core/connectors/case_ledger.py`. Add `bridge_diagnosis`.
- Modify: `core/connectors/service.py`. In `build_and_store_case_ledger`, call the diagnosis only when `merge_class == "SPLIT_NATIVE"` and the run's Medium+ alerts include both an alert with `host is None` and one with `host` set. Store the result as `ledger["bridge"]`, append its code to `merge_reason`, and count the read in `queries_issued`.
- Test: in `tests/connectors/test_case_ledger.py` and `tests/connectors/test_reconcile_case_ledger.py`.

**Interfaces:**
- Produces:
  - `XsiamConnector.pull_endpoint_ips(self, cfg: ConnectorConfig, hostname: str) -> Optional[list[str]]`. `None` means the endpoint wasn't returned or was unreadable.
  - `bridge_diagnosis(*, host: str, agent_ip: Optional[str], endpoint_ips: Optional[list[str]]) -> dict`, returning `{"host", "agent_ip", "endpoint_ips", "code"}`. `code` ∈ {`BRIDGED`, `UNBRIDGED_IP_HOST`, `BRIDGE_UNREAD`}. `agent_ip` is `Agent.last_ip` for the run's target agent.

- [ ] **Step 1: Write the failing tests.**
  - `test_bridge_bridged_when_agent_ip_on_endpoint`.
  - `test_bridge_unbridged_when_ip_absent`.
  - `test_bridge_unread_when_endpoint_not_returned`. It must never be `BRIDGED`.
  - `test_bridge_only_runs_on_split_native_with_mixed_keys`. On `ONE_CASE`, `pull_endpoint_ips` is not called.
- [ ] **Step 2: Run** `PYTHONPATH=core $PY -m pytest tests/connectors/test_case_ledger.py tests/connectors/test_reconcile_case_ledger.py -v`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement.** The endpoint record's IP field names are unverified. Read them from the operation's provenance notes and one recorded reply; an unrecognised record gives `BRIDGE_UNREAD`.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** with the message `feat(api): diagnose an unbridged firewall-to-endpoint split`.

### Task B8: The runbook, Part B docs and Gate A

**Files:**
- Create: `docs/lab/2026-11-11-run-alongside-soc-framework.md`. Spec §7's five steps, with step 3 required; the `pov`-mode AI-close sentence verbatim from spec §7; and the `tenant-verified: 0` line.
- Modify: `docs/reference/api-and-agent-surface.md` (the refresh route, `case_ledger`, the pair `merge`), `CHANGELOG.md`, and the ground truth (route count).
- Modify: `core/connectors/case_ledger.py`. Once Part A has merged into dev and this branch is rebased, remove the B1 `TODO(merge A)` local constant and import `TRIAGE_RESOLVE_COMMENT_PREFIX` from `connectors.socfw`.

- [ ] **Step 1:** Run `make ground-truth`, then `make check-ground-truth`. Expected: exit 0.
- [ ] **Step 2:** Run the five host suites, `make validate`, `make test-backend-non-utc` and `cd ui && npx vitest run`. Expected: green, with any worktree `check-adapters` caveat stated.
- [ ] **Step 3: Gate A evidence.** Revert each guard locally and record the red test:
  - rule 5 of `classify_merge` (missing case → `PENDING`);
  - the `sealed_split` branch;
  - the `CaseShapeError` raise.
- [ ] **Step 4: Commit** with the message `docs(docs): runbook for running CortexSim alongside the SOC Framework`.
- [ ] **Step 5 (lab, recorded, not a gate):** one SIM-EDR-023 run on `pov-target-01`, reading the ledger at T+0 and T+7 h, per spec §11. Expected:
  - `ONE_CASE` with the case `open` at T+0;
  - `socfw_auto_triage` and `sealed` at T+7 h.

  Record the outcome in the PR body, and keep tenant-verified at 0 unless Henry rules the run counts.

---

## Part C — grouping-key corpus gate

**Branch:** `feature/scenarios-grouping-key-check`, from `dev`. It is independent of Parts A and B.

### Task C1: Grouping-key analysis

**Files:**
- Create: `core/engine/grouping_keys.py`. Its header carries the MIT notice from `Palo-Cortex/secops-framework` `LICENSE.txt` and cites `tools/correlation_rule_grouping_check.py` `ENTITY_ALERT_FIELDS`.
- Test: `tests/engine/test_grouping_keys.py`

**Interfaces:**
- Consumes: `engine.ttp_catalog.TtpCatalog.find(ttp_ref, detection_id) -> Optional[DetectionCard]` (`.kind`, `.severity`) and `TtpCatalog.raw(ttp_ref)`, for correlation `alert_fields`.
- Produces (module `engine.grouping_keys`):
  - `NATIVE_GROUPING_FIELDS: dict[str, str]`, mapping each field in `ENTITY_ALERT_FIELDS` to one of `"host"`, `"user"`, `"file"`, `"ip"`.
  - Code constants: `G01_NO_SHARED_KEY = "G-01"` (WARN), `G02_STITCH_LOW_ONLY = "G-02"` (WARN), `G03_STITCH_KEY_CUSTOM = "G-03"` (ERROR), `G04_KEY_UNKNOWN = "G-04"` (INFO, outcome `GROUPING_KEY_UNKNOWN`), `G05_BRIDGEABLE = "G-05"` (INFO, outcome `BRIDGEABLE_IP_HOST`).
  - `@dataclass(frozen=True) class DetectionKeys: step_id: str; detection_id: str; detection_type: str; severity: Optional[str]; key_classes: frozenset[str]; keys_known: bool`
  - `@dataclass(frozen=True) class Finding: code: str; level: str; scenario_id: str; step_id: Optional[str]; detail: str`
  - `detection_keys(step_id: str, det: dict, catalog: TtpCatalog) -> DetectionKeys`
  - `analyze_scenario(scenario: dict, catalog: TtpCatalog) -> list[Finding]`

**Derivation rules.** Decided here; the tests pin them.

| `detection_type` | key classes | severity |
|---|---|---|
| BIOC | {host, user} | `DetectionCard.severity`, lowercased |
| ABIOC, Analytics | {host, user} | unknown |
| Prevention | {host, user, file} | unknown |
| XQL, IOC, Correlation | unknown, unless the card's correlation rule carries `alert_fields` (classes then from `NATIVE_GROUPING_FIELDS`) | unknown |

  A detection with unknown keys **or** unknown severity yields **G-04** and is left out of G-01. Only detections with known keys and a Medium+ severity are compared. If the known Medium+ detections split only between `ip` and host/user classes, the result is **G-05**, not G-01.

- [ ] **Step 1: Write the failing tests.** Build each scenario as a small inline dict over a fixture catalog.
  - `test_bioc_medium_pair_shares_host_no_finding`.
  - `test_xql_detection_yields_g04_not_silence`.
  - `test_unknown_severity_yields_g04_and_is_excluded_from_g01`.
  - `test_two_medium_no_shared_key_is_g01`.
  - `test_ip_vs_host_is_g05_bridgeable_not_g01`.
  - `test_stitch_scenario_all_low_is_g02`: declares `required_planes_in_incident`; known severities all `low`.
  - `test_correlation_alert_fields_custom_destination_is_g03`: the card rule maps the declared `stitching_key` to `Suspicious_User`.
  - `test_g03_dormant_without_alert_fields`: no `alert_fields` gives no G-03.
- [ ] **Step 2: Run** `PYTHONPATH=core $PY -m pytest tests/engine/test_grouping_keys.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the same command. Expected: PASS (8 tests).
- [ ] **Step 5: Commit** with the message `feat(scenarios): derive grouping keys for a scenario's detections`.

### Task C2: The `make check-grouping` gate, CI and ground truth

**Files:**
- Create: `scripts/check_grouping_keys.py`. It loads `scenarios/**/*.yml` the way `scripts/generate_ground_truth.py` does, and the catalog through `TtpCatalog().load(default_corpus_dir(base))`. It prints a per-code table, and `--json` emits `{counts_by_code, findings}`. It exits **1 only if a G-03 is found**.
- Modify: `Makefile`. Add a `check-grouping` target, appended to `validate:` (`:178`).
- Modify: `.github/workflows/ci.yml`. Add a step in the existing `refs` job (`:273`) running `make check-grouping`. It is a step, not a new job.
- Modify: `scripts/generate_ground_truth.py`. Add a `grouping` block (counts by code), and regenerate `docs/reference/ground-truth.{json,md}`.
- Modify: `CHANGELOG.md`, and `docs/reference/README.md` (the gate list).
- Test: `tests/scripts/test_check_grouping_keys.py`

- [ ] **Step 1: Write the failing tests.**
  - `test_exit_0_on_corpus_without_g03`: run the script over the real corpus, exit 0.
  - `test_exit_1_when_g03_present`: a tmp corpus with one G-03.
  - `test_json_counts_match_findings`.
  - `test_ground_truth_has_grouping_block`.
- [ ] **Step 2: Run** `PYTHONPATH=core $PY -m pytest tests/scripts/test_check_grouping_keys.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the script, the Makefile target, the CI step and the ground-truth block.
- [ ] **Step 4: Run** `make check-grouping`, then `make ground-truth`, `make check-ground-truth`, and the test file. Expected: exit 0. Record the G-04 count: it is the measured size of the later authoring program (§10 decision 1).
- [ ] **Step 5: Gate A evidence.** Remove the G-03 exit path and show `test_exit_1_when_g03_present` going red.
- [ ] **Step 6: Commit** with the message `ci(scenarios): gate the corpus on grouping keys`.

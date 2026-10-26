"""Tier-D classifier: the pure half of the three-witness prevention harness.

``test_tier_d_prevention_witnesses.py`` needs docker + a fixture SimCore and so
skips on most hosts. A test never observed running is an assumption, so the
parts that need no docker live here and always run:

  * ``classify_step`` reads the host ``termination`` of a step.
  * ``build_verdict`` carries ``terminations`` / ``severed_at_step`` and does
    not call a severed run INCONCLUSIVE for steps the severance accounts for.
  * ``require_step_outcomes`` refuses a run record that does not publish
    ``step_outcomes`` (``GET /api/runs/{id}``); there is no reconstruction.
  * the harness vocabulary cannot drift from ``core/engine/outcome.py``.

The /complete-shaped records below are hand-built for each of the three
witnesses the engine accepts: the step subshell killed (``signaled``), the
anchor shell killed (``anchor_terminated``) and the beacon killed
(``beacon_lost``).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CLASSIFY_PATH = REPO_ROOT / "deploy" / "tier-d" / "classify.py"


def _load():
    spec = importlib.util.spec_from_file_location("tier_d_classify_w", CLASSIFY_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tier_d_classify_w"] = mod
    spec.loader.exec_module(mod)
    return mod


classify = _load()


def _block(n, total, sid, body, code):
    return (f"=== STEP {n}/{total} · {sid} · T1059.004 · identity=www-data ===\n"
            f"{body}\n--- exit_code={code} duration=7ms ---")


def _run(output, *, status="complete", severed=None):
    return {"run_id": "r1", "status": status, "tc_verdict": "pending",
            "severed_at_step": severed, "output": output}


STEP1_OK = _block(1, 3, "step-01", "uid=33(www-data)", 0)

# /complete-shaped records (agent/beacon/step_outcome.go wire format).
SUBSHELL = [
    {"step_id": "step-01", "exit_code": 0, "termination": "exited"},
    {"step_id": "step-02", "exit_code": 137, "termination": "signaled", "signal": 9},
    {"step_id": "step-03", "exit_code": None, "termination": "not_run",
     "reason": "UPSTREAM_SEVERED"},
]
ANCHOR = [
    {"step_id": "step-01", "exit_code": 0, "termination": "exited"},
    {"step_id": "step-02", "exit_code": None, "termination": "anchor_terminated"},
    {"step_id": "step-03", "exit_code": None, "termination": "not_run",
     "reason": "UPSTREAM_SEVERED"},
]
BEACON = [  # what terminalise_lost_runs leaves behind
    {"step_id": "step-01", "exit_code": 0, "termination": "exited"},
    {"step_id": "step-02", "termination": "beacon_lost"},
    {"step_id": "step-03", "termination": "not_run", "reason": "UPSTREAM_SEVERED"},
]


# --- classify_step reads termination ---------------------------------------

@pytest.mark.parametrize("term", ["signaled", "anchor_terminated"])
def test_host_witness_termination_is_a_ttp_outcome_not_engine(term):
    klass, reason = classify.classify_step("", 137, termination=term)
    assert klass == "TTP"
    assert term in reason


def test_beacon_lost_is_named_unwitnessed_not_environment():
    klass, reason = classify.classify_step("", None, termination="beacon_lost")
    assert klass == "UNWITNESSED"
    assert "unwitnessed" in reason


def test_not_run_step_is_environment_never_a_miss():
    klass, reason = classify.classify_step("", None, termination="not_run")
    assert klass == "ENVIRONMENT"
    assert "never ran" in reason


def test_termination_does_not_mask_an_engine_signature():
    klass, _ = classify.classify_step("!! IDENTITY NOT HONOURED: x", 137, termination="signaled")
    assert klass == "ENGINE"


def test_unrecognised_termination_raises():
    with pytest.raises(ValueError, match="termination"):
        classify.classify_step("", 0, termination="vaporised")


def test_no_termination_is_the_old_behaviour():
    assert classify.classify_step("ok", 0) == ("OK", "step exited 0")


# --- build_verdict carries terminations ------------------------------------

def test_subshell_witness_verdict():
    out = STEP1_OK + _block(2, 3, "step-02", "Killed", 137)
    v = classify.build_verdict(_run(out, severed="step-02"), "SIM-TEST-PREV-001",
                               step_outcomes=SUBSHELL)
    assert v["terminations"]["step-02"] == {"termination": "signaled", "reason": None}
    assert v["terminations"]["step-03"] == {"termination": "not_run", "reason": "UPSTREAM_SEVERED"}
    assert v["severed_at_step"] == "step-02"
    assert v["run_status"] == "complete"
    # step-03 is accounted for by the severance, not "never reported".
    assert v["steps_unreported"] == 0
    assert v["harness_verdict"] == "PASS"


def test_anchor_witness_verdict_has_no_step_block_for_the_severed_step():
    v = classify.build_verdict(_run(STEP1_OK, severed="step-02"), "SIM-TEST-PREV-001",
                               step_outcomes=ANCHOR)
    assert v["terminations"]["step-02"]["termination"] == "anchor_terminated"
    assert v["severed_at_step"] == "step-02"
    assert v["steps_unreported"] == 0
    assert v["harness_verdict"] == "PASS"


def test_beacon_witness_verdict():
    out = STEP1_OK + "\n--- BEACON LOST — agent went offline mid-run; enable BAS mode ---\n"
    v = classify.build_verdict(_run(out, severed="step-02"), "SIM-TEST-PREV-001",
                               step_outcomes=BEACON)
    assert v["terminations"]["step-02"]["termination"] == "beacon_lost"
    assert v["steps_unreported"] == 0
    assert v["harness_verdict"] == "PASS"


def test_unaccounted_missing_step_is_still_inconclusive():
    # step-03 neither reported nor in any outcome record: a hole stays a hole.
    recs = [r for r in SUBSHELL if r["step_id"] != "step-03"]
    out = STEP1_OK + _block(2, 3, "step-02", "Killed", 137)
    v = classify.build_verdict(_run(out, severed="step-02"), "S", step_outcomes=recs)
    assert v["steps_unreported"] == 1
    assert v["harness_verdict"] == "INCONCLUSIVE"


def test_fail_fast_is_not_a_severance_and_stays_unreported():
    """C1 stays: downstream steps skipped because an upstream step FAILED are
    not accounted for by a host severance, so the run is still INCONCLUSIVE."""
    recs = [{"step_id": "step-01", "exit_code": 1, "termination": "exited"},
            {"step_id": "step-02", "termination": "not_run", "reason": "UPSTREAM_FAILED"},
            {"step_id": "step-03", "termination": "not_run", "reason": "UPSTREAM_FAILED"}]
    out = _block(1, 3, "step-01", "--- STDOUT ---\nbash: x: command not found", 127)
    v = classify.build_verdict(_run(out, status="failed"), "S", step_outcomes=recs)
    assert v["steps_unreported"] == 2
    assert v["harness_verdict"] == "INCONCLUSIVE"


def test_verdict_without_outcomes_keeps_the_old_shape():
    v = classify.build_verdict(_run(STEP1_OK), "S")
    assert v["terminations"] == {}
    assert v["severed_at_step"] is None
    assert v["steps_unreported"] == 2


# --- the run record must publish step_outcomes -----------------------------

def test_missing_step_outcomes_key_fails_with_a_named_reason():
    with pytest.raises(classify.MissingStepOutcomes, match="step_outcomes"):
        classify.require_step_outcomes({"status": "complete", "output": ""})


def test_cli_fails_loudly_on_a_run_without_step_outcomes(tmp_path):
    import json, subprocess
    run = tmp_path / "run.json"
    run.write_text(json.dumps({"status": "complete", "output": STEP1_OK}))
    proc = subprocess.run(
        ["python3", str(CLASSIFY_PATH), "--run", str(run), "--scenario", "S",
         "--out", str(tmp_path / "v.json")], capture_output=True, text=True)
    assert proc.returncode == 1
    assert "step_outcomes" in proc.stderr
    assert not (tmp_path / "v.json").exists()


def test_build_verdict_reads_the_run_records_own_step_outcomes():
    recs = [{"step_id": "step-01", "rc": 0, "termination": "exited", "reason": None},
            {"step_id": "step-02", "rc": 137, "termination": "signaled", "signal": 9},
            {"step_id": "step-03", "termination": "not_run", "reason": "UPSTREAM_SEVERED"}]
    run = _run(STEP1_OK + _block(2, 3, "step-02", "Killed", 137), severed="step-02")
    run["step_outcomes"] = recs
    v = classify.build_verdict(run, "S")
    assert v["terminations"]["step-02"]["termination"] == "signaled"
    assert v["harness_verdict"] == "PASS"


def test_row_with_no_termination_is_absence_of_evidence_not_an_error():
    recs = [{"step_id": "step-01", "rc": None, "termination": None, "reason": None},
            {"step_id": "step-02", "termination": "beacon_lost"},
            {"step_id": "step-03", "termination": "not_run", "reason": "UPSTREAM_SEVERED"}]
    out = STEP1_OK
    v = classify.build_verdict(_run(out, severed="step-02"), "S", step_outcomes=recs)
    assert "step-01" not in v["terminations"]
    assert v["terminations"]["step-02"]["termination"] == "beacon_lost"
    assert v["steps_unreported"] == 0


def test_beacon_lost_step_is_unwitnessed_and_never_counts_as_ttp():
    out = STEP1_OK + _block(2, 3, "step-02", "x", None)
    v = classify.build_verdict(_run(out, severed="step-02"), "S", step_outcomes=BEACON)
    assert v["steps"][1]["class"] == "UNWITNESSED"
    assert v["counts"]["TTP"] == 0 and v["unwitnessed"] == 1


# --- vocabulary cannot drift from the engine --------------------------------

def test_harness_vocabulary_matches_engine():
    from engine import outcome as eo

    assert set(classify.TERMINATIONS) == set(eo.TERMINATIONS)
    assert set(classify.HOST_WITNESS) == set(eo.HOST_WITNESS)
    assert classify.UPSTREAM_SEVERED == eo.UPSTREAM_SEVERED
    assert set(classify.NOT_RUN_REASONS) >= {
        eo.UPSTREAM_SEVERED, eo.UPSTREAM_NOT_EXECUTED, eo.UPSTREAM_FAILED, eo.TOOL_ABSENT}

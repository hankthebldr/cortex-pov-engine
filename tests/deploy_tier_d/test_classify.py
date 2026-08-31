"""Tests for deploy/tier-d/classify.py — the Tier-D run classifier.

This is the harness's own correctness surface, not the product's. The three
defects this file proves fixed are all "the harness itself would have said
PASS for a run that proves nothing" — the exact failure mode the harness
exists to keep out of a POV report:

  C1 — steps == [] (nothing executed, or an engine crash outside any parsed
       step body) must never classify as PASS.
  I1 — an ENGINE-class marker (e.g. "!! IDENTITY NOT HONOURED") inside a step
       that exited 0 must still classify ENGINE, not OK.
  I2 — a bare "Permission denied" from the step's OWN command (not the
       identity harness or install/staging tooling) is real TTP signal, not
       an ENVIRONMENT non-event.

Each test below was run against the pre-fix classify.py and observed RED
before the fix landed (see docs/design/tier-d-classifier-fixes.md for the
transcript); it is now GREEN against the fixed module.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CLASSIFY_PATH = REPO_ROOT / "deploy" / "tier-d" / "classify.py"


def _load_classify():
    spec = importlib.util.spec_from_file_location("tier_d_classify", CLASSIFY_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["tier_d_classify"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


classify = _load_classify()


def step_block(n, total, sid, tech, identity, exit_code, body_extra=""):
    return (
        f"=== STEP {n}/{total} · {sid} · {tech} · identity={identity} ===\n"
        f"{body_extra}"
        f"--- exit_code={exit_code} duration=10ms ---\n"
    )


def run_classify_cli(tmp_path, run_record, scenario="SIM-EDR-001"):
    """Invoke classify.py exactly as run-tier-d.sh does: as a subprocess over
    a real run.json on disk. This exercises the ONE interface guaranteed
    stable across the fix (the CLI contract), so it is valid evidence against
    both the pre-fix and post-fix module — unlike the build_verdict() tests
    above, which only exist post-fix."""
    run_path = tmp_path / "run.json"
    out_path = tmp_path / "verdict.json"
    run_path.write_text(json.dumps(run_record), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(CLASSIFY_PATH), "--run", str(run_path),
         "--scenario", scenario, "--out", str(out_path)],
        capture_output=True, text=True,
    )
    verdict = json.loads(out_path.read_text(encoding="utf-8"))
    return proc.returncode, verdict, proc.stdout


class TestCLIRegression:
    """The exact repro from the review, run through the real CLI entrypoint.
    Pre-fix: returncode 0, harness_verdict PASS. Post-fix: non-zero,
    harness_verdict != PASS."""

    def test_c1_dead_run_never_passes_via_cli(self, tmp_path):
        run = {
            "status": "failed",
            "output": "Traceback (most recent call last):\n RuntimeError: boom\n",
        }
        rc, verdict, stdout = run_classify_cli(tmp_path, run)

        assert verdict["harness_verdict"] != "PASS", (
            "a run in which nothing executed must never classify as PASS "
            f"(got {verdict['harness_verdict']!r}); stdout=\n{stdout}"
        )
        assert rc != 0, f"exit code must be non-zero for an unproven run, got {rc}"

    def test_i1_engine_marker_on_exit_zero_via_cli(self, tmp_path):
        output = step_block(
            1, 1, "step-01", "T1003", "root", 0,
            body_extra="!! IDENTITY NOT HONOURED: could not impersonate www-data\n",
        )
        run = {"status": "complete", "output": output}
        rc, verdict, stdout = run_classify_cli(tmp_path, run)

        assert verdict["steps"][0]["class"] == "ENGINE", (
            f"an ENGINE marker inside an exit-0 step must not classify OK: {verdict['steps']}"
        )
        assert rc != 0

    def test_i2_bare_permission_denied_is_ttp_via_cli(self, tmp_path):
        output = step_block(
            2, 5, "step-02", "T1003.008", "www-data", 1,
            body_extra="cat: /etc/shadow: Permission denied\n",
        )
        run = {"status": "failed", "output": output}
        rc, verdict, stdout = run_classify_cli(tmp_path, run)

        assert verdict["steps"][0]["class"] == "TTP", (
            f"a bare permission denial from the step's own command is real signal, "
            f"not an environment non-event: {verdict['steps']}"
        )


# ---------------------------------------------------------------------------
# C1 — a run in which nothing executed must never PASS.
# ---------------------------------------------------------------------------

class TestC1NoStepsExecuted:
    def test_engine_crash_before_any_step_is_not_a_pass(self):
        """The exact reproduction from the review: a run that died before
        dispatch, with a Traceback that never lands inside a === STEP ===
        block because no step block was ever emitted."""
        run = {
            "status": "failed",
            "output": "Traceback (most recent call last):\n RuntimeError: boom\n",
        }
        verdict = classify.build_verdict(run, "SIM-EDR-001")

        assert verdict["steps_declared"] == 0
        assert verdict["counts"]["ENGINE"] == 0  # no step body to attribute it to
        # The whole-output scan must still catch it (C1 fix #2).
        assert verdict["global_engine_signatures"], "Traceback in raw output must be found by the whole-output scan"
        assert verdict["harness_verdict"] == "FAIL"
        assert classify.exit_code_for(verdict) != 0

    def test_zero_steps_with_no_engine_signature_is_inconclusive_not_pass(self):
        """No steps, no recognizable engine marker either (e.g. the beacon
        never even connected) — still must not be PASS."""
        run = {"status": "failed", "output": "connection refused\n"}
        verdict = classify.build_verdict(run, "SIM-EDR-001")

        assert verdict["steps_declared"] == 0
        assert verdict["harness_verdict"] != "PASS"
        assert classify.exit_code_for(verdict) != 0

    def test_healthy_run_with_real_steps_still_passes(self):
        """Sanity check the fix does not break the honest case."""
        output = step_block(1, 1, "step-01", "T1003", "root", 0)
        run = {"status": "complete", "output": output}
        verdict = classify.build_verdict(run, "SIM-EDR-001")

        assert verdict["harness_verdict"] == "PASS"
        assert classify.exit_code_for(verdict) == 0


class TestC1Unreported:
    def test_unreported_steps_gate_the_verdict(self):
        """1 of 5 declared steps reported — 4 never ran. This must not be a
        clean PASS even though the one reported step was OK."""
        output = step_block(1, 5, "step-01", "T1003", "root", 0)
        run = {"status": "failed", "output": output}
        verdict = classify.build_verdict(run, "SIM-EDR-001")

        assert verdict["steps_unreported"] == 4
        assert verdict["harness_verdict"] != "PASS"
        assert classify.exit_code_for(verdict) != 0


class TestC1RunStatus:
    @pytest.mark.parametrize("status", ["running", "pending", "queued", "", None])
    def test_non_terminal_status_is_never_a_pass(self, status):
        """The timeout case: the poll loop gave up while STATUS was still
        running/pending/queued. Classifying partial output as a pass is
        exactly the bug this closes."""
        output = step_block(1, 1, "step-01", "T1003", "root", 0)
        run = {"status": status, "output": output}
        verdict = classify.build_verdict(run, "SIM-EDR-001")

        assert verdict["harness_verdict"] != "PASS"
        assert classify.exit_code_for(verdict) != 0

    def test_aborted_status_is_never_a_pass(self):
        output = step_block(1, 1, "step-01", "T1003", "root", 0)
        run = {"status": "aborted", "output": output}
        verdict = classify.build_verdict(run, "SIM-EDR-001")

        assert verdict["harness_verdict"] != "PASS"

    def test_environment_only_run_still_passes_honestly(self):
        """The real, expected SIM-EDR-001 shape: steps 1-4 OK, step 5 blocked
        on a missing runtime dependency. status ends up 'failed' (the
        beacon's aggregate exit code is non-zero) but this is the documented
        honest PASS-with-unrun-steps outcome and must stay PASS/exit 0."""
        output = "".join([
            step_block(1, 5, "step-01", "T1003", "root", 0),
            step_block(2, 5, "step-02", "T1003", "root", 0),
            step_block(3, 5, "step-03", "T1003", "root", 0),
            step_block(4, 5, "step-04", "T1003", "root", 0),
            step_block(5, 5, "step-05", "T1003", "root", 127,
                       body_extra="!! RUNTIME_DEPENDENCY_MISSING: python\n"),
        ])
        run = {"status": "failed", "output": output}
        verdict = classify.build_verdict(run, "SIM-EDR-001")

        assert verdict["counts"]["ENGINE"] == 0
        assert verdict["counts"]["ENVIRONMENT"] == 1
        assert verdict["steps_unreported"] == 0
        assert verdict["harness_verdict"] == "PASS"
        assert classify.exit_code_for(verdict) == 0


# ---------------------------------------------------------------------------
# I1 — ENGINE markers must be visible even on a zero-exit step.
# ---------------------------------------------------------------------------

class TestI1EngineMarkerOnZeroExit:
    def test_identity_not_honoured_with_exit_zero_is_engine_not_ok(self):
        body = "!! IDENTITY NOT HONOURED: could not impersonate www-data\nsome output\n"
        output = step_block(1, 1, "step-01", "T1003", "root", 0, body_extra=body)
        run = {"status": "complete", "output": output}
        verdict = classify.build_verdict(run, "SIM-EDR-001")

        step = verdict["steps"][0]
        assert step["exit_code"] == 0
        assert step["class"] == "ENGINE", f"exit-0 shortcut swallowed an ENGINE marker: {step}"
        assert verdict["harness_verdict"] == "FAIL"

    def test_payload_pin_mismatch_with_exit_zero_is_engine_not_ok(self):
        body = "PAYLOAD_PIN_MISMATCH: expected abc123 got def456\n"
        output = step_block(1, 1, "step-01", "T1003", "root", 0, body_extra=body)
        run = {"status": "complete", "output": output}
        verdict = classify.build_verdict(run, "SIM-EDR-001")

        assert verdict["steps"][0]["class"] == "ENGINE"
        assert verdict["harness_verdict"] == "FAIL"

    def test_clean_zero_exit_step_is_still_ok(self):
        output = step_block(1, 1, "step-01", "T1003", "root", 0, body_extra="all good\n")
        run = {"status": "complete", "output": output}
        verdict = classify.build_verdict(run, "SIM-EDR-001")

        assert verdict["steps"][0]["class"] == "OK"


# ---------------------------------------------------------------------------
# I2 — a bare "Permission denied" from the step's own command is TTP signal.
# ---------------------------------------------------------------------------

class TestI2PermissionDenied:
    def test_cat_shadow_as_www_data_is_ttp_not_environment(self):
        """SIM-EDR-001 step-02: `cat /etc/shadow` as www-data. The privilege
        boundary held — that IS the detection signal (T1003.008)."""
        body = "cat: /etc/shadow: Permission denied\n"
        output = step_block(2, 5, "step-02", "T1003.008", "www-data", 1, body_extra=body)
        run = {"status": "failed", "output": output}
        verdict = classify.build_verdict(run, "SIM-EDR-001")

        step = verdict["steps"][0]
        assert step["class"] == "TTP", f"a bare permission denial from the step's own command must read as real signal, got {step}"

    def test_runuser_permission_denied_is_environment(self):
        """The identity harness itself being denied IS an environment gap —
        the step's command never got a chance to run."""
        body = "runuser: Permission denied\n"
        output = step_block(1, 1, "step-01", "T1003", "www-data", 1, body_extra=body)
        run = {"status": "failed", "output": output}
        verdict = classify.build_verdict(run, "SIM-EDR-001")

        assert verdict["steps"][0]["class"] == "ENVIRONMENT"

    def test_install_permission_denied_is_environment(self):
        body = "apt-get: Permission denied\n"
        output = step_block(1, 1, "step-01", "T1003", "root", 1, body_extra=body)
        run = {"status": "failed", "output": output}
        verdict = classify.build_verdict(run, "SIM-EDR-001")

        assert verdict["steps"][0]["class"] == "ENVIRONMENT"

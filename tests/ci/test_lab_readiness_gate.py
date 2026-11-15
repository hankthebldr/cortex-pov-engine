"""The lab-readiness manifest is a determinism gate in CI (RG-PUSH-02).

``docs/reference/lab-readiness.{json,md}`` is generated from the scenario corpus
(``scripts/lab_readiness.py``) and is what a DC reads to pick scenarios that
emit real signal in a lab. When Windows steps and detections were cut from three
scenarios the manifest was not regenerated: it kept saying SIM-EDR-021 fires
ABIOC and Correlation across 7 real-signal steps after the scenario stopped
declaring them. ``make check-lab-ready`` would have caught it, and the release
runbook lists it as a PASS gate, but no workflow and neither make aggregate ran
it. So the first place it could go red was Gate B, on a branch whose CI was
green.

These tests pin the wiring only. Whether the committed manifest is current is
what the gate itself decides, in the refs job, on every push.
"""
from __future__ import annotations

import pytest

from tests.ci._wiring import ci_jobs, condition, make_n, step_index


def test_refs_job_gates_the_lab_readiness_manifest():
    refs = ci_jobs()["refs"]
    step = refs["steps"][step_index(refs, "make check-lab-ready")]
    assert step["run"].strip() == "make check-lab-ready"
    assert "continue-on-error" not in step


def test_the_lab_readiness_gate_is_not_masked_and_not_vacuous():
    """Same rule as every other gate in the job: it runs when an unrelated gate
    in front of it failed, and it does not run after a failed dependency
    install (where `python3 scripts/lab_readiness.py` cannot import yaml and the
    step would fail for a reason that is not drift)."""
    refs = ci_jobs()["refs"]
    step = refs["steps"][step_index(refs, "make check-lab-ready")]
    deps = refs["steps"][step_index(refs, "pip install -r core/requirements.txt")]
    assert "!cancelled()" in condition(step)
    assert f"steps.{deps['id']}.outcome == 'success'" in condition(step)


def test_check_lab_ready_regenerates_then_diffs_the_committed_manifest():
    out = make_n("check-lab-ready")
    regen = out.find("scripts/lab_readiness.py")
    diff = out.find("git diff --exit-code docs/reference/lab-readiness.json docs/reference/lab-readiness.md")
    assert regen >= 0 and diff > regen, out


@pytest.mark.parametrize("aggregate", ["validate", "ci"])
def test_make_aggregate_runs_the_lab_readiness_gate_ci_runs(aggregate):
    """The Makefile's promise is that a green `make ci` predicts a green CI
    run. A gate the refs job has and the aggregates lack breaks that promise in
    the direction that hurts: green locally, red on push."""
    out = make_n(aggregate)
    assert "scripts/lab_readiness.py" in out
    assert "git diff --exit-code docs/reference/lab-readiness.json docs/reference/lab-readiness.md" in out

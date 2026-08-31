"""Pure unit tests for engine.runtime_preflight — no DB, no filesystem.

docs/design/agent-runtime-dependencies.md has the full rationale. This module
is ADVISORY (the beacon's own live check is the enforcement); these tests
cover its comparison logic in isolation.
"""
from __future__ import annotations

from engine.runtime_preflight import (
    StepGap,
    evaluate_runtime_readiness,
    interpreter_satisfied,
)


def test_interpreter_satisfied_direct_match():
    assert interpreter_satisfied("python", ["python", "perl"])


def test_interpreter_satisfied_via_alias():
    # An agent's roster carries LOGICAL names (python), never concrete binary
    # names — but the alias table is consulted too so a fixture/older record
    # that happens to carry a concrete name still resolves.
    assert interpreter_satisfied("python", ["python3"])


def test_interpreter_not_satisfied_when_absent():
    assert not interpreter_satisfied("python", ["perl", "ruby"])


def test_interpreter_not_satisfied_on_empty_roster():
    assert not interpreter_satisfied("python", [])
    assert not interpreter_satisfied("python", None)


def test_evaluate_readiness_no_requirements_is_ready():
    steps = [{"id": "step-01", "command": "echo hi"}]
    readiness = evaluate_runtime_readiness(steps, [])
    assert readiness.ready is True
    assert readiness.gaps == []


def test_evaluate_readiness_reports_gap_for_missing_requirement():
    steps = [{"id": "step-05", "requires_interpreters": ["python"]}]
    readiness = evaluate_runtime_readiness(steps, [])
    assert readiness.ready is False
    assert readiness.gaps == [StepGap(step_id="step-05", missing=["python"])]


def test_evaluate_readiness_satisfied_requirement_is_ready():
    steps = [{"id": "step-05", "requires_interpreters": ["python"]}]
    readiness = evaluate_runtime_readiness(steps, ["python"])
    assert readiness.ready is True
    assert readiness.gaps == []


def test_evaluate_readiness_multiple_steps_only_gapped_ones_reported():
    steps = [
        {"id": "step-01", "requires_interpreters": ["python"]},
        {"id": "step-02", "requires_interpreters": []},
        {"id": "step-03", "requires_interpreters": ["perl"]},
    ]
    readiness = evaluate_runtime_readiness(steps, ["python"])
    assert readiness.ready is False
    assert len(readiness.gaps) == 1
    assert readiness.gaps[0].step_id == "step-03"
    assert readiness.gaps[0].missing == ["perl"]


def test_evaluate_readiness_accepts_scenario_like_object_with_steps_attr():
    class _FakeScenario:
        steps = [{"id": "step-05", "requires_interpreters": ["python"]}]

    readiness = evaluate_runtime_readiness(_FakeScenario(), [])
    assert readiness.ready is False


def test_readiness_to_dict_shape():
    steps = [{"id": "step-05", "requires_interpreters": ["python"]}]
    readiness = evaluate_runtime_readiness(steps, [])
    d = readiness.to_dict()
    assert d == {
        "ready": False,
        "gaps": [{"step_id": "step-05", "missing": ["python"]}],
        "agent_interpreters": [],
    }

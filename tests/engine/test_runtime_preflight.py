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


# ---------------------------------------------------------------------------
# Names that are not interpreters are judged by the beacon's tool facts (#327)
# ---------------------------------------------------------------------------


def test_tool_in_runtime_facts_is_not_a_gap():
    """sudo is not an interpreter, so it is never on the interpreter roster. The
    beacon looks for it and lists it in runtime_facts.tools."""
    steps = [{"id": "step-01", "requires_interpreters": ["sudo"]}]
    readiness = evaluate_runtime_readiness(steps, ["python"], {"tools": ["curl", "sudo"]})
    assert readiness.ready is True
    assert readiness.gaps == []


def test_unprobed_tool_is_unverified_not_missing():
    """The beacon does not look for ssh, scp or sshpass, so their absence from
    its tool list says nothing about the host."""
    steps = [
        {"id": "step-03", "requires_interpreters": ["scp", "ssh"]},
        {"id": "step-05", "requires_interpreters": ["sshpass", "ssh"]},
    ]
    readiness = evaluate_runtime_readiness(steps, [], {"tools": ["sudo"]})
    assert readiness.ready is True
    assert readiness.to_dict()["gaps"] == [
        {"step_id": "step-03", "missing": [], "unverified": ["scp", "ssh"]},
        {"step_id": "step-05", "missing": [], "unverified": ["sshpass", "ssh"]},
    ]
    # An agent that sent no tool facts at all was never asked about any tool.
    for facts in (None, {}, {"container": False, "tools": None}):
        blind = evaluate_runtime_readiness(steps[:1], [], facts)
        assert blind.ready is True
        assert blind.gaps == [StepGap(step_id="step-03", unverified=["scp", "ssh"])]


def test_probed_absent_tool_is_a_gap():
    """jq is on the beacon's probe list. The beacon looked and did not find it."""
    steps = [{"id": "step-02", "requires_interpreters": ["sudo", "jq"]}]
    readiness = evaluate_runtime_readiness(steps, [], {"tools": ["sudo"]})
    assert readiness.ready is False
    assert readiness.to_dict()["gaps"] == [{"step_id": "step-02", "missing": ["jq"]}]

    # A beacon that says what it looked for is read on its own list: it looked
    # for ssh and did not find it, and it never looked for jq.
    own_list = evaluate_runtime_readiness(
        [{"id": "step-04", "requires_interpreters": ["ssh", "jq", "sudo"]}], [],
        {"tools": ["sudo"], "probed": ["ssh", "sudo"]},
    )
    assert own_list.ready is False
    assert own_list.gaps == [StepGap(step_id="step-04", missing=["ssh"], unverified=["jq"])]


def test_interpreter_gap_unchanged():
    """node is an interpreter: the interpreter roster decides, with or without
    tool facts, and the gap keeps its shape."""
    steps = [{"id": "step-02", "requires_interpreters": ["node"]}]
    for facts in (None, {"tools": ["sudo"]}, {"tools": ["node", "sudo"]}):
        readiness = evaluate_runtime_readiness(steps, ["python"], facts)
        assert readiness.ready is False
        assert readiness.to_dict()["gaps"] == [{"step_id": "step-02", "missing": ["node"]}]
    assert evaluate_runtime_readiness(steps, ["node"], {"tools": []}).ready is True

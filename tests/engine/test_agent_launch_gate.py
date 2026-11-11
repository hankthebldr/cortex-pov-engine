"""Launch gate for container agents: refuse before dispatch, never after.

A step that fails for lack of privilege or tooling would be scored as a
detection the customer's Cortex missed.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from engine.agent_launch_gate import GateResult, evaluate, step_required_binaries

BASE = {"container": True, "posture_profile": "baseline",
        "tools": ["curl", "python3"], "accounts": []}
ROOT = Path(__file__).resolve().parents[2] / "scenarios"


@pytest.fixture
def load_scenario():
    def _load(sid: str) -> dict:
        for p in ROOT.rglob("*.yml"):
            if f"scenario_id: {sid}" in p.read_text(encoding="utf-8"):
                return yaml.safe_load(p.read_text(encoding="utf-8"))
        raise AssertionError(sid)
    return _load


def test_host_beacon_is_untouched():
    sc = {"steps": [{"id": "s1", "command": "kubectl get po"}]}
    assert evaluate(sc, None) == GateResult(None, [])


@pytest.mark.parametrize("facts", [{}, {"container": False, "tools": []},
                                   {"container": None, "tools": []}])
def test_non_container_facts_are_untouched(facts):
    sc = {"steps": [{"id": "s1", "command": "jq ."}]}
    assert evaluate(sc, facts) == GateResult(None, [])


def test_posture_gap_names_the_satisfying_profile(load_scenario):
    r = evaluate(load_scenario("SIM-CDR-004"), BASE)
    assert r.refusal.code == "AGENT_POSTURE_INSUFFICIENT"
    assert r.refusal.detail["satisfying_profile"] in ("cluster-privilege", "lab-privileged")
    assert "clusterrole-wildcard-verbs" in r.refusal.detail["missing_findings"]
    assert r.refusal.detail["agent_profile"] == "baseline"


def test_baseline_tier_scenario_needing_a_role_is_still_refused_on_baseline(load_scenario):
    # SIM-CDR-001 plants no finding baseline lacks; it needs a namespace Role
    # (secrets get/list), which only the RBAC-rule comparison can see.
    r = evaluate(load_scenario("SIM-CDR-001"), BASE)
    assert r.refusal and r.refusal.code == "AGENT_POSTURE_INSUFFICIENT"
    assert r.refusal.detail["missing_findings"] == []
    assert r.refusal.detail["missing_rbac_rules"]


def test_cluster_privilege_profile_covers_a_namespace_role(load_scenario):
    facts = dict(BASE, posture_profile="cluster-privilege")
    sc = load_scenario("SIM-CDR-001")
    r = evaluate(sc, facts, allow_missing_tools=True)
    assert r.refusal is None


def test_unknown_profile_is_refused_and_echoed():
    r = evaluate({"steps": []}, dict(BASE, posture_profile="mystery"))
    assert r.refusal.code == "AGENT_POSTURE_INSUFFICIENT"
    assert r.refusal.detail["agent_profile"] == "mystery"


@pytest.mark.parametrize("facts", [{"container": True, "tools": []},
                                   {"container": True, "posture_profile": None, "tools": []}])
def test_missing_profile_is_baseline_when_nothing_beyond_baseline_is_needed(facts):
    assert evaluate({"steps": [{"id": "s1", "command": "ls"}]}, facts).refusal is None


def test_missing_profile_is_refused_when_scenario_needs_more(load_scenario):
    r = evaluate(load_scenario("SIM-CDR-004"), {"container": True, "tools": None})
    assert r.refusal.code == "AGENT_POSTURE_INSUFFICIENT"
    assert r.refusal.detail["agent_profile"] is None


def test_missing_tool_refuses_and_names_step():
    sc = {"steps": [{"id": "s1", "command": "jq . /etc/os-release"}]}
    r = evaluate(sc, dict(BASE, prepare_status="PREPARE_DEGRADED"))
    assert r.refusal.code == "AGENT_TOOL_ABSENT"
    assert r.refusal.detail["missing"] == [{"step": "s1", "tools": ["jq"]}]
    assert r.refusal.detail["prepare_status"] == "PREPARE_DEGRADED"
    assert r.refusal.detail["override"] == "allow_missing_tools"


def test_override_turns_tool_refusal_off_but_not_posture(load_scenario):
    sc = {"steps": [{"id": "s1", "command": "jq ."}]}
    assert evaluate(sc, BASE, allow_missing_tools=True).refusal is None
    r = evaluate(load_scenario("SIM-CDR-004"), BASE, allow_missing_tools=True)
    assert r.refusal.code == "AGENT_POSTURE_INSUFFICIENT"


def test_unknown_roster_skips_the_tool_check_and_warns():
    sc = {"steps": [{"id": "s1", "command": "jq ."}]}
    for facts in ({"container": True, "posture_profile": "baseline"},
                  {"container": True, "posture_profile": "baseline", "tools": None}):
        r = evaluate(sc, facts)
        assert r.refusal is None
        assert [w["code"] for w in r.warnings] == ["AGENT_TOOL_ROSTER_UNKNOWN"]


def test_empty_roster_is_empty_not_unknown():
    sc = {"steps": [{"id": "s1", "command": "jq ."}]}
    r = evaluate(sc, dict(BASE, tools=[]))
    assert r.refusal.code == "AGENT_TOOL_ABSENT"


def test_kubectl_on_baseline_warns_never_refuses():
    facts = dict(BASE, tools=["kubectl"])
    r = evaluate({"steps": [{"id": "s1", "command": "kubectl get secrets"}]}, facts)
    assert r.refusal is None
    assert r.warnings[0] == {"code": "AGENT_NO_RBAC_FOR_KUBECTL", "steps": ["s1"]}


def test_kubectl_on_privileged_profile_does_not_warn():
    facts = dict(BASE, tools=["kubectl"], posture_profile="cluster-privilege")
    r = evaluate({"steps": [{"id": "s1", "command": "kubectl get secrets"}]}, facts)
    assert r.refusal is None and r.warnings == []


def test_quoted_tool_names_do_not_trip_the_gate():
    r = evaluate({"steps": [{"id": "s1", "command": "echo 'run jq later'"}]}, BASE)
    assert r.refusal is None


def test_step_required_binaries_is_the_manifest_scan():
    assert step_required_binaries("jq . x | curl -s y") == ["jq", "curl"]
    assert step_required_binaries("") == []


# ── cluster-scoped RBAC needs are compared as rules, not only as findings ────

_NODES_LIST = {"api_groups": [""], "resources": ["nodes"], "verbs": ["list"]}


def _needs_cluster_rule():
    return {"steps": [{"id": "s1", "command": "ls"}],
            "cluster_posture": {"service_account": {"cluster_role_rules": [_NODES_LIST]}}}


def test_cluster_rule_need_on_baseline_is_refused_and_named():
    r = evaluate(_needs_cluster_rule(), BASE)
    assert r.refusal and r.refusal.code == "AGENT_POSTURE_INSUFFICIENT"
    assert r.refusal.detail["satisfying_profile"] == "cluster-privilege"
    assert {"scope": "cluster", **_NODES_LIST} in r.refusal.detail["missing_rbac_rules"]


def test_cluster_rule_need_on_cluster_privilege_passes():
    r = evaluate(_needs_cluster_rule(), dict(BASE, posture_profile="cluster-privilege"))
    assert r.refusal is None


def test_a_namespaced_rule_never_covers_a_cluster_scoped_need():
    from engine import k8s_manifest as km
    from engine.agent_launch_gate import uncovered_rules
    need = km.parse_posture(_needs_cluster_rule())
    ns_only = [km.RbacRule(api_groups=["*"], resources=["*"], verbs=["*"])]
    missing = uncovered_rules(need, cluster_have=[], namespace_have=ns_only)
    assert missing == [{"scope": "cluster", **_NODES_LIST}]
    # The same rule held cluster-wide does cover it.
    assert uncovered_rules(need, cluster_have=ns_only, namespace_have=[]) == []


def test_a_cluster_rule_still_covers_a_namespaced_need(load_scenario):
    from engine import k8s_manifest as km
    from engine.agent_launch_gate import uncovered_rules
    need = km.parse_posture(load_scenario("SIM-CDR-001"))
    wild = [km.RbacRule(api_groups=["*"], resources=["*"], verbs=["*"])]
    assert need.service_account.namespace_role_rules
    assert uncovered_rules(need, cluster_have=wild, namespace_have=[]) == []
    assert all(m["scope"] == "namespace"
               for m in uncovered_rules(need, cluster_have=[], namespace_have=[]))

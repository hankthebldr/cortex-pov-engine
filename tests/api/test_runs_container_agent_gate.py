"""POST /api/runs against a container agent: refuse before dispatch.

A step that fails for lack of privilege or tooling would be scored as a
detection the customer's Cortex missed, so the launch is refused (409) before
any Run row is written.
"""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

FACTS = {"container": True, "posture_profile": "baseline",
         "tools": ["curl", "python3"], "accounts": [], "prepare_status": "PREPARE_OK"}


@pytest.fixture
def client(make_client):
    from api.runs import router, compat_router
    return make_client(router, compat_router)


def _seed(session_factory, *, command, facts, scenario_id="SIM-EDR-995", agent_id="pod-1",
          required_binaries=None, extra_steps=(), extra_agents=()):
    from models import Agent, Scenario

    async def _do():
        async with session_factory() as db:
            db.add(Scenario(
                scenario_id=scenario_id, name="gate", version="1.0",
                status="active", plane="EDR",
                uc_ref="UCS-EDR-01", uc_name="x", tc_ref="TC-EDR-01", tc_name="y",
                tc_refs=["TC-EDR-01"],
                mitre_tactic="TA0006", mitre_tactic_name="Credential Access",
                mitre_technique="T1003", mitre_technique_name="OS Credential Dumping",
                execution_identity={"default": "direct", "options": ["direct"]},
                push_supported=True, pull_supported=True,
                steps=[{
                    "id": "step-01", "name": "s", "identity": "direct",
                    "command": command, "mitre_technique": "T1003",
                    "expected_detections": [
                        {"plane": "EDR", "type": "BIOC", "description": "d"}],
                }, *extra_steps],
            ))
            db.add(Agent(agent_id=agent_id, hostname="h", os="linux",
                         capabilities=[], interpreters=[], runtime_facts=facts))
            for aid, afacts in extra_agents:
                db.add(Agent(agent_id=aid, hostname=aid, os="linux",
                             capabilities=[], interpreters=[], runtime_facts=afacts))
            await db.commit()

    asyncio.run(_do())
    return scenario_id


def _runs(session_factory):
    from models import Run

    async def _read():
        async with session_factory() as db:
            return (await db.execute(select(Run))).scalars().all()

    return asyncio.run(_read())


def _queued_steps(agent_id):
    from engine.orchestrator import orchestrator
    return [s for t in orchestrator._queue.get(agent_id, []) for s in t.steps]


def _launch(client, sid, agent_id="pod-1", **extra):
    return client.post("/api/runs", json={
        "scenario_id": sid, "mode": "pull", "target_agent_id": agent_id, **extra})


def test_absent_tool_is_refused_409_and_no_run_is_written(client, session_factory):
    sid = _seed(session_factory, command="jq . /etc/os-release", facts=FACTS,
                agent_id="pod-refuse")
    r = _launch(client, sid, "pod-refuse")
    assert r.status_code == 409, r.text
    d = r.json()["detail"]
    assert d["code"] == "AGENT_TOOL_ABSENT"
    assert d["detail"]["missing"] == [{"step": "step-01", "tools": ["jq"]}]
    # Refused before the Run row is written (slice 1 of the one-model gate).
    assert _runs(session_factory) == []
    assert _queued_steps("pod-refuse") == []


def test_override_accepts_and_stamps_required_binaries(client, session_factory):
    sid = _seed(session_factory, command="jq . /etc/os-release", facts=FACTS,
                agent_id="pod-override")
    r = _launch(client, sid, "pod-override", allow_missing_tools=True)
    assert r.status_code == 200, r.text
    steps = _queued_steps("pod-override")
    assert steps and "jq" in steps[0]["required_binaries"]


def test_present_tool_launches_and_is_stamped_for_the_beacon_preflight(client, session_factory):
    sid = _seed(session_factory, command="curl -s http://x", facts=FACTS,
                agent_id="pod-ok")
    r = _launch(client, sid, "pod-ok")
    assert r.status_code == 200, r.text
    assert "warnings" not in r.json()
    assert _queued_steps("pod-ok")[0]["required_binaries"] == ["curl"]


def test_kubectl_on_baseline_launches_with_a_warning(client, session_factory):
    sid = _seed(session_factory, command="kubectl get secrets",
                facts=dict(FACTS, tools=["kubectl"]), agent_id="pod-kc")
    r = _launch(client, sid, "pod-kc")
    assert r.status_code == 200, r.text
    assert r.json()["warnings"] == [
        {"code": "AGENT_NO_RBAC_FOR_KUBECTL", "steps": ["step-01"], "agent_id": "pod-kc"}]


def test_posture_gap_is_refused_and_not_overridable(client, session_factory, monkeypatch):
    import api.scenarios as sc_api
    monkeypatch.setattr(sc_api, "_disk_posture", lambda sid: {
        "service_account": {"cluster_role_rules": [
            {"api_groups": ["*"], "resources": ["*"], "verbs": ["*"]}]}})
    sid = _seed(session_factory, command="curl -s http://x", facts=FACTS,
                agent_id="pod-posture")
    r = _launch(client, sid, "pod-posture", allow_missing_tools=True)
    assert r.status_code == 409, r.text
    d = r.json()["detail"]
    assert d["code"] == "AGENT_POSTURE_INSUFFICIENT"
    assert d["detail"]["satisfying_profile"] == "cluster-privilege"
    assert _runs(session_factory) == []


@pytest.mark.parametrize("facts", [None, {"container": False, "tools": []}])
def test_host_agent_launch_is_unchanged(client, session_factory, facts):
    aid = f"host-{facts is None}"
    sid = _seed(session_factory, command="jq . /etc/os-release", facts=facts, agent_id=aid)
    r = _launch(client, sid, aid)
    assert r.status_code == 200, r.text
    assert "warnings" not in r.json()
    # No gate and no stamp: `required_binaries` is whatever dev's path produced.
    assert "required_binaries" not in _queued_steps(aid)[0]


# ── I1: a container beacon with no posture profile is not ours to gate ───────


def _task_shape(agent_id):
    """A queued task minus its per-launch ids, for byte-for-byte comparison."""
    import dataclasses
    import json
    from engine.orchestrator import orchestrator
    tasks = orchestrator._queue.get(agent_id, [])
    assert len(tasks) == 1, tasks
    d = dataclasses.asdict(tasks[0])
    d.pop("task_id")
    d.pop("run_id")
    d.pop("created_at", None)
    return json.dumps(d, sort_keys=True, default=str)


@pytest.mark.parametrize("tag,profile_facts", [
    (91, {"container": True, "posture_profile": "", "tools": ["curl"]}),
    (92, {"container": True, "posture_profile": "  ", "tools": []}),
    (93, {"container": True, "posture_profile": None}),
    (94, {"container": True}),
])
def test_unprofiled_container_agent_launches_exactly_like_a_host_agent(
    client, session_factory, tag, profile_facts, monkeypatch,
):
    import api.scenarios as sc_api
    # Even a scenario that needs wildcard RBAC must not be refused: the gate
    # does not apply to a beacon the manifest did not deploy.
    monkeypatch.setattr(sc_api, "_disk_posture", lambda sid: {
        "service_account": {"cluster_role_rules": [
            {"api_groups": ["*"], "resources": ["*"], "verbs": ["*"]}]}})
    host, pod = f"i1-host-{tag}", f"i1-pod-{tag}"
    sid = _seed(session_factory, command="jq . /etc/os-release && kubectl get po",
                facts=None, agent_id=host, scenario_id=f"SIM-EDR-9{tag}",
                extra_agents=[(pod, profile_facts)])
    r_host = _launch(client, sid, host)
    r_pod = _launch(client, sid, pod)
    assert r_host.status_code == 200, r_host.text
    assert r_pod.status_code == 200, r_pod.text
    assert "warnings" not in r_pod.json()
    assert _task_shape(pod) == _task_shape(host)
    assert "required_binaries" not in _queued_steps(pod)[0]


# ── S4: an unknown roster on a profiled pod is surfaced, not silent ──────────


def test_container_agent_without_a_tool_roster_warns_roster_unknown(client, session_factory):
    facts = {"container": True, "posture_profile": "baseline"}
    sid = _seed(session_factory, command="jq . /etc/os-release", facts=facts,
                agent_id="pod-noroster", scenario_id="SIM-EDR-994")
    r = _launch(client, sid, "pod-noroster")
    assert r.status_code == 200, r.text
    codes = [w["code"] for w in r.json()["warnings"]]
    assert codes == ["AGENT_TOOL_ROSTER_UNKNOWN"]
    assert r.json()["warnings"][0]["agent_id"] == "pod-noroster"


# ── I4: a composed step routed to a second container agent is gated ─────────


def test_composed_step_on_a_second_container_agent_is_gated_and_names_it(
    client, session_factory,
):
    second = {
        "id": "step-02", "name": "s2", "identity": "direct", "target": "pod-second",
        "command": "jq . /etc/os-release", "mitre_technique": "T1003",
        "expected_detections": [{"plane": "EDR", "type": "BIOC", "description": "d"}],
    }
    sid = _seed(session_factory, command="echo hi", facts=None, agent_id="host-primary",
                scenario_id="SIM-EDR-993", extra_steps=[second],
                extra_agents=[("pod-second", FACTS)])
    r = _launch(client, sid, "host-primary")
    assert r.status_code == 409, r.text
    d = r.json()["detail"]
    assert d["code"] == "AGENT_TOOL_ABSENT"
    assert d["detail"]["agent_id"] == "pod-second"
    assert d["detail"]["missing"] == [{"step": "step-02", "tools": ["jq"]}]
    assert "pod-second" in d["error"]
    assert _queued_steps("host-primary") == [] and _queued_steps("pod-second") == []

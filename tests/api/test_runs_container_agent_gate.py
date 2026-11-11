"""POST /api/runs against a container agent: refuse before dispatch.

A step that fails for lack of privilege or tooling would be scored as a
detection the customer's Cortex missed, so the launch is refused (409) and the
run row ends ``failed`` rather than ``pending``.
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
          required_binaries=None):
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
                }],
            ))
            db.add(Agent(agent_id=agent_id, hostname="h", os="linux",
                         capabilities=[], interpreters=[], runtime_facts=facts))
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


def test_absent_tool_is_refused_409_and_run_is_failed(client, session_factory):
    sid = _seed(session_factory, command="jq . /etc/os-release", facts=FACTS,
                agent_id="pod-refuse")
    r = _launch(client, sid, "pod-refuse")
    assert r.status_code == 409, r.text
    d = r.json()["detail"]
    assert d["code"] == "AGENT_TOOL_ABSENT"
    assert d["detail"]["missing"] == [{"step": "step-01", "tools": ["jq"]}]
    runs = _runs(session_factory)
    assert len(runs) == 1 and runs[0].status == "failed"
    assert "AGENT_TOOL_ABSENT" in (runs[0].output or "")
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
        {"code": "AGENT_NO_RBAC_FOR_KUBECTL", "steps": ["step-01"]}]


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
    assert _runs(session_factory)[0].status == "failed"


@pytest.mark.parametrize("facts", [None, {"container": False, "tools": []}])
def test_host_agent_launch_is_unchanged(client, session_factory, facts):
    aid = f"host-{facts is None}"
    sid = _seed(session_factory, command="jq . /etc/os-release", facts=facts, agent_id=aid)
    r = _launch(client, sid, aid)
    assert r.status_code == 200, r.text
    assert "warnings" not in r.json()
    # No gate and no stamp: `required_binaries` is whatever dev's path produced.
    assert "required_binaries" not in _queued_steps(aid)[0]

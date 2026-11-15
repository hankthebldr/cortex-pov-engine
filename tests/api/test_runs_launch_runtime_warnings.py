"""POST /api/runs and /api/runs/preflight: runtime-dependency warnings.

Two things a DC used to learn only after the run (#328):

* a step needs something the target agent does not have, so the beacon will
  refuse to run it;
* the launch asked for a runtime install on a SimCore that has not enabled it,
  so nothing will be installed.

Both are warnings on the launch response, on the dry run and on ``Run.output``.
Neither refuses the launch: the beacon's own check at execution time is the
enforcement.
"""
from __future__ import annotations

import asyncio
from datetime import datetime

import pytest
from sqlalchemy import select

DETECTION = {"detection_id": "d-1", "detection_type": "BIOC", "name": "n"}

# What a host beacon reports: it looked for its whole probe list and found these.
HOST_FACTS = {"container": False, "tools": ["curl", "sudo"], "accounts": []}

# step-01 needs sudo (in the agent's tool facts), step-02 needs node (not on its
# interpreter roster).
GAP_STEPS = [
    {"id": "step-01", "name": "one", "command": "sudo -V", "platforms": ["linux"],
     "requires_interpreters": ["sudo"], "expected_detections": [DETECTION]},
    {"id": "step-02", "name": "two", "command": "node -v", "platforms": ["linux"],
     "requires_interpreters": ["node"], "expected_detections": [DETECTION]},
]
CLEAN_STEPS = [GAP_STEPS[0]]


@pytest.fixture
def client(make_client):
    from api.runs import compat_router, router
    return make_client(router, compat_router)


@pytest.fixture
def install_flag(monkeypatch):
    """Set CORTEXSIM_AGENT_ALLOW_RUNTIME_INSTALL for one test."""
    from config import settings

    def _set(value: bool) -> None:
        monkeypatch.setattr(settings, "CORTEXSIM_AGENT_ALLOW_RUNTIME_INSTALL", value)

    return _set


def _seed(session_factory, *, agent_id, steps, scenario_id="SIM-EDR-991",
          interpreters=("python",), facts=HOST_FACTS):
    from models import Agent, Scenario

    async def _do():
        async with session_factory() as db:
            db.add(Scenario(
                scenario_id=scenario_id, name="runtime warnings", version="1.0",
                status="active", plane="EDR",
                uc_ref="UCS-EDR-01", uc_name="x", tc_ref="TC-EDR-01", tc_name="y",
                mitre_tactic="TA0006", mitre_tactic_name="Credential Access",
                mitre_technique="T1003", mitre_technique_name="OS Credential Dumping",
                execution_identity={"default": "root"},
                push_supported=True, pull_supported=True,
                steps=steps,
            ))
            db.add(Agent(agent_id=agent_id, hostname=agent_id, os="linux",
                         capabilities=["shell"], interpreters=list(interpreters),
                         runtime_facts=facts,
                         registered_at=datetime.utcnow(),
                         last_seen=datetime.utcnow(), status="online"))
            await db.commit()

    asyncio.run(_do())
    return {"scenario_id": scenario_id, "mode": "pull", "target_agent_id": agent_id}


def _runs(session_factory):
    from models import Run

    async def _read():
        async with session_factory() as db:
            return (await db.execute(select(Run))).scalars().all()

    return asyncio.run(_read())


def _queued(agent_id):
    from engine.orchestrator import orchestrator
    return orchestrator.peek_queue(agent_id)


def _by_code(warnings, code):
    return [w for w in warnings if isinstance(w, dict) and w.get("code") == code]


def test_gap_is_on_launch_response_and_run_output(client, session_factory):
    body = _seed(session_factory, agent_id="a-gap", steps=GAP_STEPS)

    r = client.post("/api/runs", json=body)
    assert r.status_code == 200, r.text
    gap = _by_code(r.json()["warnings"], "RUNTIME_DEPENDENCY_GAP")
    assert len(gap) == 1, r.json()["warnings"]
    # Only the step that lacks something is named: sudo is in the tool facts.
    assert {k: gap[0][k] for k in ("agent_id", "steps", "missing")} == {
        "agent_id": "a-gap", "steps": ["step-02"], "missing": ["node"]}
    assert "node" in gap[0]["detail"]

    (run,) = _runs(session_factory)
    line = [ln for ln in (run.output or "").splitlines() if "RUNTIME_DEPENDENCY_GAP" in ln]
    assert line == [
        f"!! RUNTIME_DEPENDENCY_GAP: agent 'a-gap'; steps step-02; {gap[0]['detail']}"]


def test_install_request_with_flag_off_warns(client, session_factory, install_flag):
    install_flag(False)
    body = _seed(session_factory, agent_id="a-off", steps=CLEAN_STEPS)

    r = client.post("/api/runs", json={**body, "allow_runtime_install": True})
    assert r.status_code == 200, r.text
    warned = _by_code(r.json()["warnings"], "RUNTIME_INSTALL_NOT_ENABLED")
    assert len(warned) == 1, r.json()["warnings"]
    assert warned[0]["agent_id"] == "a-off"
    assert "CORTEXSIM_AGENT_ALLOW_RUNTIME_INSTALL" in warned[0]["detail"]

    (run,) = _runs(session_factory)
    assert run.runtime_install_authorized is False
    assert f"!! RUNTIME_INSTALL_NOT_ENABLED: agent 'a-off'; {warned[0]['detail']}" in (
        run.output or "").splitlines()

    # A launch that did not ask for an install is not told about the flag.
    body2 = _seed(session_factory, agent_id="a-quiet", steps=CLEAN_STEPS,
                  scenario_id="SIM-EDR-992")
    assert "warnings" not in client.post("/api/runs", json=body2).json()


def test_install_request_with_flag_on_is_authorized_without_warning(
    client, session_factory, install_flag,
):
    install_flag(True)
    body = _seed(session_factory, agent_id="a-on", steps=CLEAN_STEPS)

    r = client.post("/api/runs", json={**body, "allow_runtime_install": True})
    assert r.status_code == 200, r.text
    assert "warnings" not in r.json()

    (run,) = _runs(session_factory)
    assert run.runtime_install_authorized is True
    assert "RUNTIME_INSTALL_NOT_ENABLED" not in (run.output or "")
    (task,) = _queued("a-on")
    assert task.runtime_install_authorized is True


def test_preflight_reports_same_warnings(client, session_factory, install_flag):
    install_flag(False)
    body = {**_seed(session_factory, agent_id="a-dry", steps=GAP_STEPS),
            "allow_runtime_install": True}

    pre = client.post("/api/runs/preflight", json=body)
    assert pre.status_code == 200, pre.text
    verdict = pre.json()
    assert verdict["launchable"] is True
    assert [w["code"] for w in verdict["warnings"]] == [
        "RUNTIME_DEPENDENCY_GAP", "RUNTIME_INSTALL_NOT_ENABLED"]
    assert _runs(session_factory) == [], "the dry run wrote a run"
    assert _queued("a-dry") == []

    launched = client.post("/api/runs", json=body)
    assert launched.status_code == 200, launched.text
    assert launched.json()["warnings"] == verdict["warnings"]


def test_launch_with_gap_is_still_accepted(client, session_factory):
    body = _seed(session_factory, agent_id="a-run", steps=GAP_STEPS)

    r = client.post("/api/runs", json=body)
    assert r.status_code == 200, r.text
    (run,) = _runs(session_factory)
    assert run.run_id == r.json()["run_id"]
    assert run.status == "running"
    assert run.runtime_dependency_gaps == [{"step_id": "step-02", "missing": ["node"]}]
    # Both steps reach the beacon: its own check decides at execution time.
    (task,) = _queued("a-run")
    assert [s["id"] for s in task.steps] == ["step-01", "step-02"]

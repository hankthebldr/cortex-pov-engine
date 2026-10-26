"""POST /api/runs/canary and canary_run_id validation at launch."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select


@pytest.fixture
def client(make_client):
    from api.runs import router
    return make_client(router)


@pytest.fixture
def canary_cfg(monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "CORTEXSIM_CANARY_SCENARIO_LINUX", "SIM-CAN-001")


def _scenario(sid):
    from models import Scenario
    return Scenario(
        scenario_id=sid, name="t", plane="EDR", version="1.0",
        status="active", uc_ref="UCS-EDR-01", uc_name="u", tc_ref="TC-EDR-01",
        tc_name="t", mitre_tactic="TA0006", mitre_tactic_name="c",
        mitre_technique="T1003", mitre_technique_name="d",
        steps=[{"id": "step-01", "name": "s", "expected_detections": []}],
    )


def _seed(session_factory, *, canary_target="agent-1", canary_scenario="SIM-CAN-001",
          completed=True, outcome=None, status=None):
    from models import Result, Run

    async def go():
        async with session_factory() as db:
            db.add(_scenario("SIM-T-001"))
            db.add(_scenario("SIM-CAN-001"))
            db.add(Run(
                run_id="can", scenario_id=canary_scenario, mode="pull",
                target=canary_target,
                status=status or ("complete" if completed else "running"),
                agent_profile_mode="block",
                started_at=datetime.utcnow() - timedelta(minutes=5),
                completed_at=datetime.utcnow() if completed else None,
            ))
            if outcome:
                db.add(Result(
                    run_id="can", step_id="step-01", plane="EDR",
                    signal_type="BIOC", expected_detection="d1",
                    detection_id="d1", executed_at=datetime.utcnow(),
                    outcome=outcome,
                ))
            await db.commit()
    asyncio.run(go())


def _launch(client, **kw):
    body = {"scenario_id": "SIM-T-001", "mode": "push", "target_agent_id": "agent-1",
            "agent_profile_mode": "block"}
    body.update(kw)
    return client.post("/api/runs", json=body)


def _run_row(session_factory, run_id):
    from models import Run

    async def go():
        async with session_factory() as db:
            return (await db.execute(select(Run).where(Run.run_id == run_id))).scalar_one()
    return asyncio.run(go())


def test_canary_endpoint_409_when_unconfigured(client, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "CORTEXSIM_CANARY_SCENARIO_LINUX", "")
    r = client.post("/api/runs/canary", json={"target_agent_id": "agent-1"})
    assert r.status_code == 409, r.text
    d = r.json()["detail"]
    assert d["code"] == "CANARY_NOT_AVAILABLE"
    assert d["error"] == "Canary not available"
    assert "CORTEXSIM_CANARY_SCENARIO_LINUX" in d["detail"]


def test_canary_endpoint_launches_configured_scenario(client, canary_cfg, monkeypatch):
    from engine.orchestrator import LaunchResult, orchestrator
    seen = {}

    async def fake_launch(**kw):
        seen.update(kw)
        return LaunchResult(success=True, run_id="r-1", mode="pull", message="ok")

    monkeypatch.setattr(orchestrator, "launch", fake_launch)
    r = client.post("/api/runs/canary", json={"target_agent_id": "agent-1"})
    assert r.status_code == 200, r.text
    assert r.json() == {"run_id": "r-1", "scenario_id": "SIM-CAN-001"}
    assert seen["scenario_id"] == "SIM-CAN-001"
    assert seen["mode"] == "pull"
    assert seen["target_agent_id"] == "agent-1"
    assert seen["agent_profile_mode"] == "block"


def test_launch_with_canary_derives_status_and_rejects_bad_link(
    client, session_factory, canary_cfg
):
    _seed(session_factory, outcome="prevented")
    r = _launch(client, canary_run_id="can")
    assert r.status_code == 200, r.text
    assert _run_row(session_factory, r.json()["run_id"]).block_mode_status == "block_mode_confirmed"

    # no canary link: attested-only, status stays None
    r = _launch(client)
    assert r.status_code == 200, r.text
    assert _run_row(session_factory, r.json()["run_id"]).block_mode_status is None

    # wrong target
    r = _launch(client, canary_run_id="can", target_agent_id="agent-2")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "CANARY_INVALID"

    # unknown canary
    r = _launch(client, canary_run_id="nope")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "CANARY_INVALID"


def test_launch_with_unconfirmed_canary_and_invalid_variants(
    client, session_factory, canary_cfg
):
    _seed(session_factory, outcome=None)
    r = _launch(client, canary_run_id="can")
    assert r.status_code == 200, r.text
    assert _run_row(session_factory, r.json()["run_id"]).block_mode_status == "block_mode_unconfirmed"


def _refusal(client, **kw):
    r = _launch(client, canary_run_id="can", **kw)
    assert r.status_code == 409, r.text
    d = r.json()["detail"]
    assert d["code"] == "CANARY_INVALID"
    return d["detail"]


def test_launch_rejects_wrong_scenario_canary(client, session_factory, canary_cfg):
    # terminal, right target, but a different scenario than the configured canary
    _seed(session_factory, canary_scenario="SIM-T-001", completed=True)
    assert "not the configured canary scenario" in _refusal(client)


def test_launch_rejects_canary_when_config_changed_after_it_ran(
    client, session_factory, canary_cfg, monkeypatch
):
    from config import settings
    _seed(session_factory)
    monkeypatch.setattr(settings, "CORTEXSIM_CANARY_SCENARIO_LINUX", "SIM-OTHER")
    assert "not the configured canary scenario" in _refusal(client)


def test_launch_rejects_nonterminal_canary(client, session_factory, canary_cfg):
    _seed(session_factory, completed=False)
    assert "has not finished" in _refusal(client)


def test_launch_rejects_aborted_canary(client, session_factory, canary_cfg):
    _seed(session_factory, completed=False, status="aborted")
    d = _refusal(client)
    assert "was aborted and proves nothing" in d
    assert "has not finished" not in d

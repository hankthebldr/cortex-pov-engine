"""GET /api/runs/{run_id}/pair."""
from __future__ import annotations

import asyncio
from datetime import datetime

import pytest


@pytest.fixture
def client(make_client):
    from api.runs import router
    return make_client(router)


def _seed(session_factory, paired=True):
    from models import Result, Run, Scenario

    async def go():
        async with session_factory() as db:
            db.add(Scenario(
                scenario_id="SIM-T-001", name="t", plane="EDR", version="1.0",
                status="active", uc_ref="UCS-EDR-01", uc_name="u", tc_ref="TC-EDR-01",
                tc_name="t", mitre_tactic="TA0006", mitre_tactic_name="c",
                mitre_technique="T1003", mitre_technique_name="d", steps=[]))
            now = datetime.utcnow()
            db.add(Run(run_id="rep", scenario_id="SIM-T-001", mode="pull", target="a1",
                       status="complete", agent_profile_mode="report",
                       started_at=now, completed_at=now))
            db.add(Run(run_id="blk", scenario_id="SIM-T-001", mode="pull", target="a1",
                       status="complete", agent_profile_mode="block",
                       paired_with_run_id="rep" if paired else None,
                       severed_at_step="step-01", started_at=now, completed_at=now))
            for rid, outcome in (("rep", "detected"), ("blk", "prevented")):
                db.add(Result(run_id=rid, step_id="step-01", step_name="s", plane="EDR",
                              signal_type="BIOC", expected_detection="d", detection_id="d1",
                              observed=True, executed_at=now, outcome=outcome))
            await db.commit()
    asyncio.run(go())


@pytest.mark.parametrize("rid", ["rep", "blk"])
def test_pair_endpoint_returns_both_runs_and_delta(client, session_factory, rid):
    _seed(session_factory)
    r = client.get(f"/api/runs/{rid}/pair")
    assert r.status_code == 200, r.text
    body = r.json()
    assert {x["run_id"] for x in body["runs"]} == {"rep", "blk"}
    assert body["delta"]["report_run_id"] == "rep" and body["delta"]["block_run_id"] == "blk"
    d = body["delta"]["steps"][0]["detections"][0]
    assert d["report"]["outcome"] == "detected" and d["block"]["outcome"] == "prevented"
    assert body["delta"]["severed_at_step"] == "step-01"


def test_pair_endpoint_409_when_unpaired(client, session_factory):
    _seed(session_factory, paired=False)
    r = client.get("/api/runs/rep/pair")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "RUN_NOT_PAIRED"


def test_pair_endpoint_404_unknown_run(client, session_factory):
    r = client.get("/api/runs/nope/pair")
    assert r.status_code == 404 and r.json()["detail"]["code"] == "RUN_NOT_FOUND"

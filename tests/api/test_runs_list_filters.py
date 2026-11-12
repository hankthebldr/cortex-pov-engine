"""GET /api/runs filters: status, scenario_id and limit.

The no-parameter call is the console's contract and must stay exactly
``{"runs": [...], "total": N}`` ordered by started_at desc. ``total`` counts
the rows matching the filters before ``limit`` is applied.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def client(make_client):
    from api.runs import router
    return make_client(router)


@pytest.fixture
def seeded_runs(session_factory):
    """Three runs: two on SIM-A-001 (one complete), one on SIM-B-001.

    Returns the run ids in started_at-desc order (newest first).
    """
    from models import Run, Scenario

    now = datetime.utcnow()

    async def _seed():
        async with session_factory() as db:
            for sid, name in (("SIM-A-001", "Scenario A"), ("SIM-B-001", "Scenario B")):
                db.add(Scenario(
                    scenario_id=sid,
                    name=name,
                    plane="EDR",
                    version="1.0",
                    status="active",
                    uc_ref="UCS-EDR-01",
                    uc_name="Endpoint Credential Theft",
                    tc_ref="TC-EDR-01",
                    tc_name="Linux Credential Harvest",
                    mitre_tactic="TA0006",
                    mitre_tactic_name="Credential Access",
                    mitre_technique="T1003.008",
                    mitre_technique_name="OS Credential Dumping",
                    steps=[{"id": "step-01", "name": "Read passwd", "mitre_technique": "T1087.001"}],
                ))
            db.add(Run(run_id="r-a-old", scenario_id="SIM-A-001", mode="push",
                       status="complete", started_at=now - timedelta(minutes=30)))
            db.add(Run(run_id="r-a-new", scenario_id="SIM-A-001", mode="push",
                       status="failed", started_at=now - timedelta(minutes=10)))
            db.add(Run(run_id="r-b", scenario_id="SIM-B-001", mode="push",
                       status="running", started_at=now - timedelta(minutes=20)))
            await db.commit()

    asyncio.run(_seed())
    return ["r-a-new", "r-b", "r-a-old"]


def test_no_params_is_unchanged(client, seeded_runs):
    r = client.get("/api/runs")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"runs", "total"}
    assert body["total"] == 3
    assert [row["run_id"] for row in body["runs"]] == seeded_runs


def test_status_filter(client, seeded_runs):
    r = client.get("/api/runs", params={"status": "complete"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert [row["run_id"] for row in body["runs"]] == ["r-a-old"]


def test_scenario_filter_and_limit(client, seeded_runs):
    r = client.get("/api/runs", params={"scenario_id": "SIM-A-001", "limit": 1})
    assert r.status_code == 200
    body = r.json()
    assert len(body["runs"]) == 1
    assert body["total"] == 2
    assert body["runs"][0]["run_id"] == "r-a-new"


def test_limit_above_max_is_422(client, seeded_runs):
    # The bound is LIST_RUNS_MAX_LIMIT (5000), not 500: the console's Runs
    # poll sends limit=5000 (ui/src/api/client.js RUNS_PAGE_LIMIT).
    from api.runs import LIST_RUNS_MAX_LIMIT

    r = client.get("/api/runs", params={"limit": LIST_RUNS_MAX_LIMIT + 1})
    assert r.status_code == 422
    assert client.get("/api/runs", params={"limit": LIST_RUNS_MAX_LIMIT}).status_code == 200

"""Direct router tests for /api/mitre/coverage.

The endpoint joins Scenario + Run + Result into a per-technique status map
with a tactic grouping suitable for the heatmap UI.  Tests cover:

  - empty repo → empty arrays + zero summary
  - one scenario, no run → status "not_run"
  - one scenario + one run, no observations → "run_not_detected"
  - one scenario + observed result → "detected"
  - per-step technique override picks up extra techniques
"""
from __future__ import annotations

import asyncio
from datetime import datetime

import pytest


@pytest.fixture
def client(make_client):
    from api.mitre import router
    return make_client(router)


def _seed(session_factory, *, observed: bool = False, with_run: bool = False):
    from models import Scenario, Run, Result

    async def _do():
        async with session_factory() as db:
            s = Scenario(
                scenario_id="SIM-EDR-001",
                name="Credential Dumping",
                plane="EDR",
                version="1.0",
                status="active",
                uc_ref="UCS-EDR-01",
                uc_name="X",
                tc_ref="TC-EDR-01",
                tc_name="Y",
                mitre_tactic="TA0006",
                mitre_tactic_name="Credential Access",
                mitre_technique="T1003.008",
                mitre_technique_name="Shadow Dump",
                steps=[
                    {"id": "step-01", "mitre_technique": "T1087.001"},  # per-step override
                    {"id": "step-02", "mitre_technique": "T1003.008"},
                ],
            )
            db.add(s)
            if with_run:
                db.add(
                    Run(
                        run_id="r-1",
                        scenario_id="SIM-EDR-001",
                        mode="push",
                        status="complete",
                        started_at=datetime.utcnow(),
                    )
                )
                db.add(
                    Result(
                        run_id="r-1",
                        step_id="step-02",
                        plane="EDR",
                        signal_type="BIOC",
                        expected_detection="d",
                        observed=observed,
                        observed_at=datetime.utcnow() if observed else None,
                        executed_at=datetime.utcnow(),
                    )
                )
            await db.commit()

    asyncio.get_event_loop().run_until_complete(_do())


def test_coverage_empty(client):
    r = client.get("/api/mitre/coverage")
    assert r.status_code == 200
    body = r.json()
    assert body["techniques"] == []
    assert body["summary"]["total_techniques"] == 0


def test_coverage_scenario_only_is_not_run(client, session_factory):
    _seed(session_factory, with_run=False)
    body = client.get("/api/mitre/coverage").json()
    statuses = {t["technique_id"]: t["status"] for t in body["techniques"]}
    assert statuses["T1003.008"] == "not_run"
    # per-step technique surfaced too
    assert "T1087.001" in statuses


def test_coverage_run_no_detection(client, session_factory):
    _seed(session_factory, with_run=True, observed=False)
    body = client.get("/api/mitre/coverage").json()
    s = next(t for t in body["techniques"] if t["technique_id"] == "T1003.008")
    assert s["status"] == "run_not_detected"
    assert s["total_detections"] == 1
    assert s["observed_detections"] == 0


def test_coverage_detected(client, session_factory):
    _seed(session_factory, with_run=True, observed=True)
    body = client.get("/api/mitre/coverage").json()
    s = next(t for t in body["techniques"] if t["technique_id"] == "T1003.008")
    assert s["status"] == "detected"
    assert s["observed_detections"] == 1
    assert s["coverage_pct"] == 100.0
    assert body["summary"]["detected"] >= 1


def test_coverage_groups_by_tactic(client, session_factory):
    _seed(session_factory, with_run=True, observed=True)
    body = client.get("/api/mitre/coverage").json()
    tactic_ids = [t["tactic_id"] for t in body["by_tactic"]]
    assert "TA0006" in tactic_ids

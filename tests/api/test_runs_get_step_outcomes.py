"""R3: GET /api/runs/{id} exposes the host evidence (StepOutcome rows)."""
from __future__ import annotations

import asyncio
from datetime import datetime

import pytest


@pytest.fixture
def client(make_client):
    from api.runs import router
    return make_client(router)


def _seed(session_factory, rows, steps=("step-01", "step-02", "step-03")):
    from models import Run, Scenario, StepOutcome

    async def go():
        async with session_factory() as db:
            db.add(Scenario(
                scenario_id="SIM-T-001", name="t", plane="EDR", version="1.0",
                status="active", uc_ref="UCS-EDR-01", uc_name="u", tc_ref="TC-EDR-01",
                tc_name="t", mitre_tactic="TA0006", mitre_tactic_name="c",
                mitre_technique="T1003", mitre_technique_name="d",
                steps=[{"id": s} for s in steps]))
            db.add(Run(run_id="r1", scenario_id="SIM-T-001", mode="pull", target="a",
                       status="complete"))
            for r in rows:
                db.add(StepOutcome(run_id="r1", recorded_at=datetime(2026, 1, 1), **r))
            await db.commit()
    asyncio.run(go())


def test_empty_list_when_no_rows(client, session_factory):
    _seed(session_factory, [])
    assert client.get("/api/runs/r1").json()["step_outcomes"] == []


def test_rows_are_projected_in_scenario_order(client, session_factory):
    # inserted out of scenario order on purpose
    _seed(session_factory, [
        dict(step_id="step-03", termination="not_run", reason="UPSTREAM_SEVERED"),
        dict(step_id="step-02", termination="signaled", exit_code=137, signal=9),
        dict(step_id="step-01", termination="exited", exit_code=0,
             started_at=datetime(2026, 1, 1, 0, 0, 1)),
    ])
    so = client.get("/api/runs/r1").json()["step_outcomes"]
    assert [s["step_id"] for s in so] == ["step-01", "step-02", "step-03"]
    assert set(so[1]) == {"step_id", "termination", "signal", "rc", "reason",
                          "started_at", "recorded_at"}
    assert so[1]["rc"] == 137 and so[1]["signal"] == 9 and so[1]["termination"] == "signaled"
    assert so[0]["started_at"] == "2026-01-01T00:00:01"
    assert so[2]["reason"] == "UPSTREAM_SEVERED" and so[2]["rc"] is None

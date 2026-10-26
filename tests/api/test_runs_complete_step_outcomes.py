"""/output and /complete persist per-step host evidence and sever the run."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select


@pytest.fixture
def client(make_client):
    from api.agents import router as agents_router
    from api.runs import router as runs_router
    return make_client(runs_router, agents_router)


@pytest.fixture(autouse=True)
def _clean_orchestrator():
    from engine.orchestrator import orchestrator

    orchestrator._aborted.clear()
    orchestrator._queue.clear()
    yield
    orchestrator._aborted.clear()
    orchestrator._queue.clear()


def _seed(session_factory, run_id="r1", *, mode="block", prevent_steps=("step-02",),
          steps=("step-01", "step-02", "step-03")):
    from models import Result, Run

    async def _go():
        async with session_factory() as db:
            db.add(Run(run_id=run_id, scenario_id="SIM-EDR-001", mode="pull",
                       status="running", open_tasks=1, agent_profile_mode=mode,
                       started_at=datetime.utcnow() - timedelta(seconds=30)))
            for sid in steps:
                db.add(Result(
                    run_id=run_id, step_id=sid, plane="EDR", signal_type="BIOC",
                    expected_detection=f"det {sid}",
                    expected_outcome="prevent" if sid in prevent_steps else "detect",
                ))
            await db.commit()

    asyncio.run(_go())


def _run(session_factory, run_id="r1"):
    from models import Run

    async def _go():
        async with session_factory() as db:
            r = (await db.execute(select(Run).where(Run.run_id == run_id))).scalar_one()
            return r.status, r.severed_at_step

    return asyncio.run(_go())


def _rows(session_factory, model, run_id="r1"):
    async def _go():
        async with session_factory() as db:
            return (await db.execute(
                select(model).where(model.run_id == run_id).order_by(model.id))).scalars().all()

    return asyncio.run(_go())


_SEVER_BODY = {
    "exit_code": 137,
    "summary": "severed",
    "steps": [
        {"step_id": "step-01", "exit_code": 0, "termination": "exited"},
        {"step_id": "step-02", "exit_code": 137, "termination": "anchor_terminated", "signal": 9},
        {"step_id": "step-03", "exit_code": None, "termination": "not_run",
         "reason": "UPSTREAM_SEVERED"},
    ],
}


def test_complete_without_steps_is_byte_identical(client, session_factory):
    from models import StepOutcome

    _seed(session_factory, mode="block")
    r = client.post("/api/runs/r1/complete", json={"exit_code": 1, "summary": "old beacon"})
    assert r.status_code == 200 and r.json()["status"] == "failed"
    assert _run(session_factory) == ("failed", None)
    assert _rows(session_factory, StepOutcome) == []


def test_complete_persists_step_outcomes_and_severs(client, session_factory):
    from models import Result, StepOutcome

    _seed(session_factory, mode="block")
    r = client.post("/api/runs/r1/complete", json=_SEVER_BODY)
    assert r.status_code == 200, r.text
    assert _run(session_factory) == ("complete", "step-02")

    so = {s.step_id: s for s in _rows(session_factory, StepOutcome)}
    assert set(so) == {"step-01", "step-02", "step-03"}
    assert (so["step-02"].termination, so["step-02"].signal, so["step-02"].exit_code) == (
        "anchor_terminated", 9, 137)
    assert so["step-03"].reason == "UPSTREAM_SEVERED" and so["step-03"].recorded_at is not None

    res = {x.step_id: x for x in _rows(session_factory, Result)}
    assert res["step-03"].outcome == "not_reached"
    assert res["step-03"].outcome_reason == "UPSTREAM_SEVERED"
    assert (res["step-02"].outcome, res["step-02"].outcome_reason, res["step-02"].kpi_verdict) == (
        "pending", "PREVENTION_UNCONFIRMED_NO_ALERT", "pending")
    assert res["step-01"].outcome is None


def test_complete_non_prevent_signaled_step_still_fails_run(client, session_factory):
    _seed(session_factory, mode="report", prevent_steps=())
    body = {"exit_code": 137, "summary": "x", "steps": [
        {"step_id": "step-01", "exit_code": 137, "termination": "signaled", "signal": 9}]}
    assert client.post("/api/runs/r1/complete", json=body).json()["status"] == "failed"
    assert _run(session_factory) == ("failed", "step-01")


def test_severed_without_prevent_expectation_in_block_mode_still_fails(client, session_factory):
    _seed(session_factory, mode="block", prevent_steps=())
    assert client.post("/api/runs/r1/complete", json=_SEVER_BODY).json()["status"] == "failed"


def test_duplicate_complete_does_not_duplicate_step_outcomes(client, session_factory):
    from models import StepOutcome

    _seed(session_factory, mode="block")
    assert client.post("/api/runs/r1/complete", json=_SEVER_BODY).status_code == 200
    again = client.post("/api/runs/r1/complete", json=_SEVER_BODY)
    assert again.status_code == 200
    assert len(_rows(session_factory, StepOutcome)) == 3
    assert _run(session_factory) == ("complete", "step-02")


def test_complete_rejects_unknown_termination(client, session_factory):
    from models import StepOutcome

    _seed(session_factory)
    body = {"exit_code": 0, "summary": "x",
            "steps": [{"step_id": "step-01", "termination": "exploded"}]}
    assert client.post("/api/runs/r1/complete", json=body).status_code == 422
    assert _rows(session_factory, StepOutcome) == []


def test_output_with_step_id_records_started_at(client, session_factory):
    from models import StepOutcome

    _seed(session_factory)
    for chunk in ("a", "b"):
        r = client.post("/api/runs/r1/output", json={"output": chunk, "step_id": "step-01"})
        assert r.status_code == 200
    rows = _rows(session_factory, StepOutcome)
    assert len(rows) == 1 and rows[0].step_id == "step-01"
    assert rows[0].started_at is not None and rows[0].termination is None
    client.post("/api/runs/r1/output", json={"output": "no step"})
    assert len(_rows(session_factory, StepOutcome)) == 1


def test_complete_keeps_started_at_from_output(client, session_factory):
    from models import StepOutcome

    _seed(session_factory, mode="block")
    client.post("/api/runs/r1/output", json={"output": "x", "step_id": "step-01"})
    started = _rows(session_factory, StepOutcome)[0].started_at
    client.post("/api/runs/r1/complete", json=_SEVER_BODY)
    s1 = [s for s in _rows(session_factory, StepOutcome) if s.step_id == "step-01"][0]
    assert s1.started_at == started and s1.termination == "exited"


def _fanout(session_factory, run_id="fo"):
    _seed(session_factory, run_id, mode="block")

    async def _go():
        from models import Run
        async with session_factory() as db:
            r = (await db.execute(select(Run).where(Run.run_id == run_id))).scalar_one()
            r.open_tasks = 2
            await db.commit()

    asyncio.run(_go())


def _step(client, run_id, termination, exit_code, **kw):
    body = {"exit_code": exit_code, "summary": "e", "steps": [
        {"step_id": "step-02", "exit_code": exit_code, "termination": termination, **kw}]}
    assert client.post(f"/api/runs/{run_id}/complete", json=body).status_code == 200


def _term(session_factory, run_id):
    from models import StepOutcome
    return [(s.termination, s.signal) for s in _rows(session_factory, StepOutcome, run_id)]


def test_witness_is_not_downgraded_by_a_later_exited(client, session_factory):
    _fanout(session_factory)
    _step(client, "fo", "anchor_terminated", 137, signal=9)
    _step(client, "fo", "exited", 0)
    assert _term(session_factory, "fo") == [("anchor_terminated", 9)]
    assert _run(session_factory, "fo")[1] == "step-02"


def test_witness_upgrades_an_earlier_exited(client, session_factory):
    _fanout(session_factory)
    _step(client, "fo", "exited", 0)
    _step(client, "fo", "anchor_terminated", 137, signal=9)
    assert _term(session_factory, "fo") == [("anchor_terminated", 9)]
    assert _run(session_factory, "fo")[1] == "step-02"


def test_results_are_projected_by_the_time_complete_returns(client, session_factory):
    from models import Result
    _seed(session_factory, mode="block")
    assert client.post("/api/runs/r1/complete", json=_SEVER_BODY).status_code == 200
    res = {x.step_id: x.outcome for x in _rows(session_factory, Result)}
    assert res["step-03"] == "not_reached" and res["step-02"] == "pending"


def test_unknown_reason_and_reasonless_not_run_are_422(client, session_factory):
    _seed(session_factory)
    for st in ({"step_id": "step-01", "termination": "exited", "reason": "BOGUS"},
               {"step_id": "step-01", "termination": "not_run"}):
        r = client.post("/api/runs/r1/complete", json={"exit_code": 0, "summary": "x", "steps": [st]})
        assert r.status_code == 422


def test_reason_on_non_not_run_termination_is_stored(client, session_factory):
    from models import StepOutcome
    _seed(session_factory)
    body = {"exit_code": 0, "summary": "x", "steps": [
        {"step_id": "step-01", "termination": "timeout", "reason": "UPSTREAM_FAILED"}]}
    assert client.post("/api/runs/r1/complete", json=body).status_code == 200
    assert _rows(session_factory, StepOutcome)[0].reason == "UPSTREAM_FAILED"


def test_second_endpoint_not_run_cannot_erase_witnessed_pending(client, session_factory):
    from models import Result
    _fanout(session_factory)
    _step(client, "fo", "anchor_terminated", 137, signal=9)
    _step(client, "fo", "not_run", 0, reason="UPSTREAM_SEVERED")
    assert _term(session_factory, "fo") == [("anchor_terminated", 9)]
    r = [x for x in _rows(session_factory, Result, "fo") if x.step_id == "step-02"][0]
    assert (r.outcome, r.outcome_reason) == ("pending", "PREVENTION_UNCONFIRMED_NO_ALERT")


def test_partial_branch_projects_within_the_request(client, session_factory):
    from models import Result
    _fanout(session_factory)
    body = {"exit_code": 0, "summary": "e", "steps": [
        {"step_id": "step-01", "exit_code": 0, "termination": "exited"},
        {"step_id": "step-02", "termination": "not_run", "reason": "TOOL_ABSENT"},
        {"step_id": "step-03", "termination": "not_run", "reason": "UPSTREAM_SEVERED"}]}
    r = client.post("/api/runs/fo/complete", json=body)
    assert r.json().get("pending_endpoints") == 1
    res = {x.step_id: x.outcome for x in _rows(session_factory, Result, "fo")}
    assert res == {"step-01": None, "step-02": "not_executed", "step-03": "not_reached"}

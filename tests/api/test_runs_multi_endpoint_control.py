"""A multi-endpoint run whose FIRST endpoint fails must not kill the others.

``complete_run`` keeps failure sticky: once any endpoint of a fan-out reports a
non-zero exit, ``Run.status`` becomes ``failed`` while the run still waits on
the remaining endpoints (``open_tasks > 0``, ``completed_at`` unset). The
beacon's control poll (``GET /api/runs/{id}/control`` every 2 s) treats any
status in the terminal set as a stop signal, so before this guard the SECOND
endpoint's in-flight step was killed within two seconds and the beacon reported
``aborted by operator`` — an abort nobody issued — and every detection that
endpoint would have produced was lost from the POV.

A partially-failed run is still in flight: the control channel must not stop
the surviving endpoints, and an operator abort must still be able to stop them.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def client(make_client):
    from api.runs import router
    return make_client(router)


@pytest.fixture(autouse=True)
def _clean_orchestrator():
    from engine.orchestrator import orchestrator

    orchestrator._aborted.clear()
    orchestrator._queue.clear()
    yield
    orchestrator._aborted.clear()
    orchestrator._queue.clear()


def _seed_fanout_run(session_factory, run_id: str, open_tasks: int = 2) -> str:
    from models import Run

    async def _seed():
        async with session_factory() as db:
            db.add(Run(
                run_id=run_id, scenario_id="SIM-EDR-001", mode="pull",
                status="running", open_tasks=open_tasks,
                started_at=datetime.utcnow() - timedelta(seconds=30),
            ))
            await db.commit()

    asyncio.run(_seed())
    return run_id


def test_first_endpoint_failure_does_not_stop_the_other_endpoint(client, session_factory):
    run_id = _seed_fanout_run(session_factory, "fanout-partial-fail")

    r = client.post(f"/api/runs/{run_id}/complete", json={"exit_code": 1, "summary": "boom"})
    assert r.status_code == 200
    assert r.json()["pending_endpoints"] == 1

    # The second endpoint is still executing. Its 2 s control poll must NOT
    # read a stop signal from the first endpoint's failure.
    ctl = client.get(f"/api/runs/{run_id}/control").json()
    assert ctl["abort"] is False, ctl


def test_operator_can_still_abort_a_partially_failed_run(client, session_factory):
    run_id = _seed_fanout_run(session_factory, "fanout-partial-abort")
    client.post(f"/api/runs/{run_id}/complete", json={"exit_code": 1, "summary": "boom"})

    r = client.post(f"/api/runs/{run_id}/abort")
    assert r.status_code == 200
    body = r.json()
    assert body["was_terminal"] is False, body
    assert body["status"] == "aborted"
    assert client.get(f"/api/runs/{run_id}/control").json()["abort"] is True


def test_last_endpoint_in_still_terminalises_failed_and_stops(client, session_factory):
    """The sticky verdict is unchanged: once the LAST endpoint reports, the run
    is terminal ``failed`` and the control channel says stop."""
    run_id = _seed_fanout_run(session_factory, "fanout-final")
    client.post(f"/api/runs/{run_id}/complete", json={"exit_code": 1, "summary": "boom"})
    out = client.post(f"/api/runs/{run_id}/complete", json={"exit_code": 0, "summary": "ok"}).json()
    assert out["status"] == "failed"
    ctl = client.get(f"/api/runs/{run_id}/control").json()
    assert ctl["abort"] is True


def test_single_endpoint_failed_run_still_stops(client, session_factory):
    """A legacy/single-endpoint failed run (open_tasks NULL or 0) is terminal."""
    from models import Run

    async def _seed():
        async with session_factory() as db:
            db.add(Run(run_id="single-failed", scenario_id="SIM-EDR-001", mode="pull",
                       status="failed", completed_at=datetime.utcnow()))
            await db.commit()

    asyncio.run(_seed())
    assert client.get("/api/runs/single-failed/control").json()["abort"] is True
    r = client.post("/api/runs/single-failed/abort").json()
    assert r["was_terminal"] is True


def test_artifact_gate_refusal_on_a_partially_failed_run_terminalises_it(
    make_client, session_factory,
):
    """Endpoint A failed first (run reads ``failed``, one endpoint still owed).
    Endpoint B's task is then refused by the artifact-staging gate, so B will
    never POST /complete. The gate must terminalise the run and write its
    marker; skipping a ``failed`` run left it waiting on B forever
    (``completed_at`` NULL, ``open_tasks`` 1) with no record of why."""
    from api.agents import router as agents_router
    from api.runs import router as runs_router
    from engine.orchestrator import Task, orchestrator
    from models import Run
    from sqlalchemy import select

    client = make_client(agents_router, runs_router)
    run_id = _seed_fanout_run(session_factory, "fanout-gate")
    client.post("/api/agents/register", json={
        "agent_id": "b-old", "hostname": "h", "os": "linux",
        "capabilities": ["shell", "identity-harness"],
    }).raise_for_status()
    client.post(f"/api/runs/{run_id}/complete", json={"exit_code": 1, "summary": "boom"})

    orchestrator._enqueue("b-old", Task(
        task_id="t-gate", run_id=run_id, scenario_id="SIM-EDR-999",
        steps=[{"id": "step-02", "command": "bash /tmp/x.sh"}],
        identity_context=None,
        artifacts=[{"name": "linpeas.sh", "sha256": "0" * 64,
                    "path": "/api/shelf/payload/linpeas.sh", "dest": "/tmp/x.sh",
                    "mode": "0755", "steps": ["step-02"]}],
    ))
    assert client.get("/api/agents/b-old/tasks").status_code == 409

    async def _read():
        async with session_factory() as db:
            return (await db.execute(select(Run).where(Run.run_id == run_id))).scalar_one()

    run = asyncio.run(_read())
    assert run.status == "failed"
    assert run.completed_at is not None, "run left waiting on an endpoint that was refused"
    assert "AGENT_CANNOT_STAGE_ARTIFACTS" in (run.output or "")

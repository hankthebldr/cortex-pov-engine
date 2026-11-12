"""A retried ``/complete`` counts once (RG-AGENT-03).

The beacon now retries ``/complete`` on a transport error or a 5xx
(``agent/beacon/client.go::postRetry``). ``complete_run`` decrements
``open_tasks`` on every accepted in-flight POST and nothing told it that two
POSTs were the same completion. Observed on c852eac, run seeded
``open_tasks=2``, the same body sent twice (endpoint A's reply lost, A retries):

    first : 200 {'status': 'running',  'pending_endpoints': 1}
    retry : 200 {'status': 'complete', 'tc_verdict': 'not_applicable'}
    control seen by endpoint B: {'abort': True, 'status': 'complete'}
    endpoint B /complete: 409 RUN_ALREADY_TERMINAL

One endpoint's retry terminalised the fan-out, the control channel stopped the
other endpoint mid-step, and that endpoint's evidence was refused — while the
run read ``complete``.

The server half of the fix: ``/complete`` accepts the task's identity
(``task_id``, else ``agent_id``) and records it per run in the same write
transaction as the decrement. A repeat is answered 200 with ``duplicate: true``
and the run's current state, and changes nothing.

A beacon that sends no identity (every beacon before this change) keeps the old
behaviour, which the last test pins so the boundary is explicit: without an
identity the server cannot tell a retry from the second endpoint.
"""
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


def _seed(session_factory, run_id: str, open_tasks: int) -> None:
    from models import Run

    async def _go():
        async with session_factory() as db:
            db.add(Run(run_id=run_id, scenario_id="SIM-EDR-001", mode="pull",
                       status="running", open_tasks=open_tasks,
                       started_at=datetime.utcnow() - timedelta(seconds=30)))
            await db.commit()

    asyncio.run(_go())


def _run(session_factory, run_id: str):
    from models import Run

    async def _go():
        async with session_factory() as db:
            return (await db.execute(select(Run).where(Run.run_id == run_id))).scalar_one()

    return asyncio.run(_go())


def _body(endpoint: str, **identity) -> dict:
    return {"exit_code": 0, "summary": f"SUCCESS | endpoint {endpoint}",
            "steps": [{"step_id": f"step-{endpoint}", "exit_code": 0,
                       "termination": "exited"}],
            **identity}


@pytest.mark.parametrize("identity", [
    {"task_id": "task-a", "agent_id": "agent-a"},
    {"task_id": "task-a"},
    {"agent_id": "agent-a"},
], ids=["task+agent", "task-only", "agent-only"])
def test_a_retried_complete_does_not_finish_the_fan_out(client, session_factory, identity):
    _seed(session_factory, "fan-retry", 2)
    body = _body("a", **identity)

    first = client.post("/api/runs/fan-retry/complete", json=body)
    retry = client.post("/api/runs/fan-retry/complete", json=body)

    assert first.status_code == 200 and first.json()["pending_endpoints"] == 1, first.text
    assert retry.status_code == 200, retry.text
    assert retry.json() == {"status": "running", "run_id": "fan-retry",
                            "pending_endpoints": 1, "duplicate": True}

    # Endpoint B is still executing: the control channel must not stop it.
    ctl = client.get("/api/runs/fan-retry/control").json()
    assert ctl["abort"] is False and ctl["status"] == "running", ctl

    run = _run(session_factory, "fan-retry")
    assert run.open_tasks == 1 and run.completed_at is None
    assert (run.output or "").count("endpoint a") == 1, run.output

    # ...and B's own completion is what finishes the run.
    last = client.post("/api/runs/fan-retry/complete",
                       json=_body("b", task_id="task-b", agent_id="agent-b"))
    assert last.status_code == 200 and last.json()["status"] == "complete", last.text
    assert "pending_endpoints" not in last.json()


def test_a_duplicate_does_not_republish_or_rescore(client, session_factory, monkeypatch):
    import connectors.service as service
    from events import event_bus

    _seed(session_factory, "fan-quiet", 2)
    published, scored = [], []

    async def _publish(run_id, event):
        published.append(event)

    real = service.score_run_safely

    async def _score(db, run, **kw):
        scored.append(run.run_id)
        return await real(db, run, **kw)

    monkeypatch.setattr(event_bus, "publish", _publish)
    monkeypatch.setattr(service, "score_run_safely", _score)

    body = _body("a", task_id="task-a")
    client.post("/api/runs/fan-quiet/complete", json=body)
    client.post("/api/runs/fan-quiet/complete", json=body)
    client.post("/api/runs/fan-quiet/complete", json=body)

    assert published == [] and scored == []
    assert _run(session_factory, "fan-quiet").open_tasks == 1


def test_a_duplicate_after_the_run_finished_is_still_409(client, session_factory):
    """One rule for a finished run, whoever asks: 409, the signal to stop."""
    _seed(session_factory, "single", 1)
    body = _body("a", task_id="task-a")

    first = client.post("/api/runs/single/complete", json=body)
    again = client.post("/api/runs/single/complete", json=body)

    assert first.status_code == 200 and first.json()["status"] == "complete"
    assert again.status_code == 409
    assert again.json()["detail"]["code"] == "RUN_ALREADY_TERMINAL"


def test_the_same_task_id_on_another_run_is_not_a_duplicate(client, session_factory):
    _seed(session_factory, "run-1", 2)
    _seed(session_factory, "run-2", 2)
    body = _body("a", task_id="task-a")

    one = client.post("/api/runs/run-1/complete", json=body)
    two = client.post("/api/runs/run-2/complete", json=body)

    assert "duplicate" not in one.json() and "duplicate" not in two.json()
    assert _run(session_factory, "run-2").open_tasks == 1


def test_a_refused_complete_records_no_identity(client, session_factory):
    """The identity is written in the SAME transaction as the decrement: a
    /complete refused because the run is terminal leaves nothing behind."""
    from models import RunTaskCompletion

    _seed(session_factory, "done", 1)
    client.post("/api/runs/done/complete", json=_body("a", task_id="task-a"))
    refused = client.post("/api/runs/done/complete", json=_body("b", task_id="task-b"))
    assert refused.status_code == 409

    async def _keys():
        async with session_factory() as db:
            return sorted((await db.execute(
                select(RunTaskCompletion.task_key)
                .where(RunTaskCompletion.run_id == "done"))).scalars().all())

    assert asyncio.run(_keys()) == ["task:task-a"]


def test_a_beacon_that_sends_no_identity_keeps_the_old_behaviour(client, session_factory):
    """The boundary, pinned. With no ``task_id`` / ``agent_id`` in the body a
    repeat is indistinguishable from the fan-out's second endpoint, so it is
    counted — exactly as before. Closing this needs the beacon to send its
    identity (agent/beacon/client.go, another lane)."""
    _seed(session_factory, "legacy", 2)
    body = _body("a")

    first = client.post("/api/runs/legacy/complete", json=body)
    retry = client.post("/api/runs/legacy/complete", json=body)

    assert first.json()["pending_endpoints"] == 1
    assert retry.json()["status"] == "complete", retry.text

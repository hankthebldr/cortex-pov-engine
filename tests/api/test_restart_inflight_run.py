"""A SimCore restart must not fail — or kill — a run that is executing on a beacon.

``GET /api/agents/{id}/tasks`` deletes the durable ``queued_tasks`` row the
moment it hands the task to the beacon (so a restart never re-delivers it). The
run then stays ``running`` while the beacon executes its steps. On the next boot
``orchestrator.rehydrate()`` failed EVERY pending/running run with no surviving
row, appending ``RUN FAILED ON RESTART — queued task was lost (SimCore restarted
before the agent picked it up)`` — which is false for a delivered task. Three
consequences, each reproduced below:

1. The run flips to ``failed``, so ``GET /api/runs/{id}/control`` (polled by the
   beacon every 2 s) returns ``abort: true`` and the in-flight step is killed.
   A SimCore restart therefore aborted every executing run.
2. The POV report's execution-integrity section maps that marker to "the run
   never reached the target" — a false statement about a run whose steps ran.
3. ``complete_run`` keeps ``failed`` sticky, so the beacon's eventual successful
   completion cannot correct it.

A run whose task was never delivered and whose row was lost is still failed —
that half of the sweep is correct and stays (see test_orchestrator_queue.py).
"""
from __future__ import annotations

import asyncio
from datetime import datetime

import pytest
from sqlalchemy import select


@pytest.fixture(autouse=True)
def _clean_orchestrator():
    from engine.orchestrator import orchestrator

    orchestrator._aborted.clear()
    orchestrator._queue.clear()
    yield
    orchestrator._aborted.clear()
    orchestrator._queue.clear()


async def _seed(db) -> None:
    from models import Agent, Scenario

    db.add(Scenario(
        scenario_id="SIM-EDR-001", name="Test", version="1.0", status="active",
        plane="EDR", uc_ref="UCS-EDR-01", uc_name="x", tc_ref="TC-EDR-01", tc_name="y",
        mitre_tactic="TA0006", mitre_tactic_name="Credential Access",
        mitre_technique="T1003", mitre_technique_name="OS Credential Dumping",
        execution_identity={"default": "www-data"}, push_supported=True,
        pull_supported=True,
        steps=[{"id": "step-01", "name": "s", "identity": "www-data", "command": "id",
                "expected_detections": [{"type": "BIOC", "description": "d"}]}],
    ))
    db.add(Agent(agent_id="agent-1", hostname="h1", os="linux",
                 capabilities=["shell", "identity-harness", "artifact-fetch"],
                 interpreters=[], registered_at=datetime.utcnow(),
                 last_seen=datetime.utcnow(), status="online"))
    await db.commit()


def _launch(session_factory) -> str:
    from engine.orchestrator import orchestrator

    async def _go():
        async with session_factory() as db:
            await _seed(db)
            res = await orchestrator.launch(
                scenario_id="SIM-EDR-001", mode="pull", db=db, target_agent_id="agent-1",
            )
            assert res.success, res.error
            return res.run_id

    return asyncio.run(_go())


def _restart(session_factory) -> dict:
    """A brand-new orchestrator over the same DB — what the lifespan does."""
    from engine.orchestrator import Orchestrator

    async def _go():
        async with session_factory() as db:
            return await Orchestrator().rehydrate(db)

    return asyncio.run(_go())


def _run(session_factory, run_id):
    from models import Run

    async def _go():
        async with session_factory() as db:
            return (await db.execute(select(Run).where(Run.run_id == run_id))).scalar_one()

    return asyncio.run(_go())


def test_restart_does_not_fail_a_run_whose_task_was_delivered(make_client, session_factory):
    from api.agents import router as agents_router
    from api.runs import router as runs_router

    client = make_client(agents_router, runs_router)
    run_id = _launch(session_factory)

    # The beacon collects its task — the durable row is deleted on delivery.
    task = client.get("/api/agents/agent-1/tasks").json()["task"]
    assert task is not None and task["run_id"] == run_id

    stats = _restart(session_factory)

    run = _run(session_factory, run_id)
    assert stats["failed_orphans"] == 0, stats
    assert run.status == "running"
    assert "RUN FAILED ON RESTART" not in (run.output or "")
    # The beacon's control poll must not be told to kill the executing step.
    assert client.get(f"/api/runs/{run_id}/control").json()["abort"] is False
    # And the beacon's completion still lands as a normal completion.
    out = client.post(f"/api/runs/{run_id}/complete",
                      json={"exit_code": 0, "summary": "ok"}).json()
    assert out["status"] == "complete"


def test_restart_still_fails_an_undelivered_run_whose_task_was_lost(session_factory):
    """The honest half of the sweep is unchanged: never delivered + no row ⇒ lost."""
    from models import QueuedTask

    run_id = _launch(session_factory)

    async def _drop_row():
        async with session_factory() as db:
            for row in (await db.execute(select(QueuedTask))).scalars().all():
                await db.delete(row)
            await db.commit()

    asyncio.run(_drop_row())
    stats = _restart(session_factory)
    run = _run(session_factory, run_id)
    assert stats["failed_orphans"] == 1
    assert run.status == "failed"
    assert "RUN FAILED ON RESTART" in (run.output or "")


def test_restart_still_redelivers_an_undelivered_task(make_client, session_factory):
    run_id = _launch(session_factory)
    from engine.orchestrator import Orchestrator

    async def _go():
        async with session_factory() as db:
            orch = Orchestrator()
            stats = await orch.rehydrate(db)
            task = await orch.dequeue_for_agent("agent-1", db)
            return stats, task

    stats, task = asyncio.run(_go())
    assert stats["rehydrated"] == 1 and stats["failed_orphans"] == 0
    assert task is not None and task.run_id == run_id


def test_delivered_at_migration_upgrades_a_legacy_db_idempotently(tmp_path):
    """An existing cortexsim.db has no runs.delivered_at; create_all never adds
    a column, so without the migration every Run SELECT fails after upgrade."""
    from sqlalchemy import create_engine, inspect, text

    from database import _migrate_run_delivery_columns

    eng = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE runs (run_id VARCHAR PRIMARY KEY, status VARCHAR)"))
        conn.execute(text("INSERT INTO runs (run_id, status) VALUES ('old', 'running')"))
    with eng.begin() as conn:
        _migrate_run_delivery_columns(conn)
    with eng.begin() as conn:
        _migrate_run_delivery_columns(conn)  # idempotent
        assert "delivered_at" in {c["name"] for c in inspect(conn).get_columns("runs")}
        # A legacy row reads as never-delivered — the pre-fix assumption.
        assert conn.execute(text("SELECT delivered_at FROM runs")).scalar() is None

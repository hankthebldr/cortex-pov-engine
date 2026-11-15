"""A busy database must not fail the beacon's polls or the heartbeat sweep (#326).

SQLite allows one writer. When another one holds the lock past the busy
timeout, a write fails ``database is locked``. Three writes sit on the beacon's
own paths and are bookkeeping, not evidence:

* ``GET /api/agents/{id}/tasks`` stamps the agent's ``last_seen``. A 500 here
  is a poll the beacon has to retry, and 44 of them in a minute is how a live
  beacon came to look gone.
* ``GET /api/runs/{id}/control`` stamps the same row every 2 s while a step
  runs. A 500 here is worse: the beacon reads it as a lost control channel and
  stops the step.
* the heartbeat sweep writes status transitions.

Each now waits out the busy timeout, then skips its write for this tick.
Delivering a task is different: that write must land, so a delivery that
cannot be recorded hands nothing out and keeps the task queued.

Against a real SQLite FILE, with the lock held by a second connection.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

#: How long these connections wait on the write lock before SQLite gives up.
BUSY_SECONDS = 0.5
LONG_AGO = datetime.utcnow() - timedelta(hours=1)


@pytest.fixture
def file_db(tmp_path):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    path = tmp_path / "cortexsim.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{path}",
        connect_args={"check_same_thread": False, "timeout": BUSY_SECONDS})
    SessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def _init() -> None:
        from database import Base
        import models  # noqa: F401

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    yield path, SessionLocal
    asyncio.run(engine.dispose())


@pytest.fixture
def client(file_db):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.agents import router as agents_router
    from api.runs import router as runs_router
    from database import get_db

    _, SessionLocal = file_db

    async def _get_db():
        async with SessionLocal() as session:
            yield session

    app = FastAPI()
    app.include_router(runs_router, prefix="/api")
    app.include_router(agents_router, prefix="/api")
    app.dependency_overrides[get_db] = _get_db
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clean_orchestrator():
    from engine.orchestrator import orchestrator

    def _reset():
        orchestrator._aborted.clear()
        orchestrator._queue.clear()
        orchestrator.reset_liveness()

    _reset()
    yield
    _reset()


@contextmanager
def write_lock_held(path):
    """Another writer in the middle of its transaction, for as long as the
    block lasts: what a tenant read with an open transaction used to be."""
    holder = sqlite3.connect(str(path), isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    try:
        yield
    finally:
        holder.execute("ROLLBACK")
        holder.close()


def _seed(SessionLocal, *, launch: bool = False, last_seen=None, status="online"):
    """An agent, a one-step scenario and (``launch``) a queued pull run.
    Returns the run id, or None."""
    from engine.orchestrator import orchestrator
    from models import Agent, Scenario

    async def _go():
        async with SessionLocal() as db:
            db.add(Scenario(
                scenario_id="SIM-EDR-970", name="T", version="1.0", status="active",
                plane="EDR", uc_ref="UCS-EDR-01", uc_name="x", tc_ref="TC-EDR-01",
                tc_name="y", mitre_tactic="TA0006", mitre_tactic_name="CA",
                mitre_technique="T1003.001", mitre_technique_name="LSASS",
                execution_identity={"default": "root"}, push_supported=True,
                pull_supported=True,
                steps=[{"id": "step-01", "name": "s", "identity": "root", "command": "id",
                        "mitre_technique": "T1003.001",
                        "expected_detections": [{"plane": "EDR", "type": "BIOC",
                                                 "description": "det", "outcome": "detect"}]}]))
            seen = last_seen or datetime.utcnow()
            db.add(Agent(agent_id="agent-1", hostname="h1", os="linux",
                         capabilities=["shell", "identity-harness"], interpreters=[],
                         registered_at=seen, last_seen=seen, status=status))
            await db.commit()
            if not launch:
                return None
            res = await orchestrator.launch(
                scenario_id="SIM-EDR-970", mode="pull", db=db, target_agent_id="agent-1")
            assert res.success, res.error
            return res.run_id

    return asyncio.run(_go())


def _agent(SessionLocal):
    from models import Agent

    async def _go():
        async with SessionLocal() as db:
            return (await db.execute(select(Agent))).scalar_one()

    return asyncio.run(_go())


def _run(SessionLocal, run_id):
    from models import Run

    async def _go():
        async with SessionLocal() as db:
            return (await db.execute(select(Run).where(Run.run_id == run_id))).scalar_one()

    return asyncio.run(_go())


def _queued_rows(SessionLocal) -> int:
    from models import QueuedTask

    async def _go():
        async with SessionLocal() as db:
            return len((await db.execute(select(QueuedTask))).scalars().all())

    return asyncio.run(_go())


# ---------------------------------------------------------------------------
# GET /api/agents/{id}/tasks
# ---------------------------------------------------------------------------


def test_a_poll_under_a_held_write_lock_answers_instead_of_failing(client, file_db, caplog):
    path, SessionLocal = file_db
    _seed(SessionLocal, last_seen=LONG_AGO)

    with caplog.at_level(logging.WARNING), write_lock_held(path):
        r = client.get("/api/agents/agent-1/tasks")

    assert r.status_code == 200 and r.json() == {"task": None}
    # It said so, in one line, and did not pretend the stamp was written.
    assert any("database busy" in rec.getMessage() for rec in caplog.records)
    assert _agent(SessionLocal).last_seen == LONG_AGO

    # The lock is gone: the next poll stamps as usual.
    assert client.get("/api/agents/agent-1/tasks").status_code == 200
    assert _agent(SessionLocal).last_seen > LONG_AGO + timedelta(minutes=30)


def test_a_delivery_that_cannot_be_recorded_keeps_the_task_queued(file_db):
    """Delivery pops the task from memory and then deletes its durable row. If
    that write fails and the task is not put back, it exists nowhere a beacon
    can reach until SimCore restarts, and its run waits forever."""
    from engine.orchestrator import orchestrator

    path, SessionLocal = file_db
    run_id = _seed(SessionLocal, launch=True)
    assert _queued_rows(SessionLocal) == 1

    async def _dequeue():
        async with SessionLocal() as db:
            return await orchestrator.dequeue_for_agent("agent-1", db)

    with write_lock_held(path):
        assert asyncio.run(_dequeue()) is None

    # Nothing was handed out, and nothing was lost.
    assert [t.run_id for t in orchestrator.peek_queue("agent-1")] == [run_id]
    assert _queued_rows(SessionLocal) == 1
    assert _run(SessionLocal, run_id).delivered_at is None

    task = asyncio.run(_dequeue())
    assert task is not None and task.run_id == run_id
    assert _queued_rows(SessionLocal) == 0
    assert _run(SessionLocal, run_id).delivered_at is not None


def test_the_next_poll_delivers_a_task_a_locked_poll_could_not(client, file_db):
    """The same thing through the route a beacon hits."""
    path, SessionLocal = file_db
    run_id = _seed(SessionLocal, launch=True)

    with write_lock_held(path):
        r = client.get("/api/agents/agent-1/tasks")
    assert r.status_code == 200 and r.json() == {"task": None}
    assert _run(SessionLocal, run_id).delivered_at is None

    r = client.get("/api/agents/agent-1/tasks")
    assert r.status_code == 200 and r.json()["task"]["run_id"] == run_id
    assert _run(SessionLocal, run_id).delivered_at is not None


# ---------------------------------------------------------------------------
# GET /api/runs/{id}/control
# ---------------------------------------------------------------------------


def test_a_control_poll_under_a_held_write_lock_does_not_read_as_a_lost_channel(
        client, file_db):
    """The beacon stops its step on anything but a 200 here."""
    from engine.orchestrator import orchestrator

    path, SessionLocal = file_db
    run_id = _seed(SessionLocal, launch=True)
    assert client.get("/api/agents/agent-1/tasks").json()["task"]["run_id"] == run_id

    with write_lock_held(path):
        r = client.get(f"/api/runs/{run_id}/control")

    assert r.status_code == 200
    assert r.json() == {"abort": False, "run_id": run_id, "status": "running"}
    # The poll still counts as the beacon working on this run.
    assert orchestrator.last_control_poll(run_id) is not None


# ---------------------------------------------------------------------------
# The heartbeat sweep
# ---------------------------------------------------------------------------


def test_the_heartbeat_sweep_skips_a_pass_under_a_held_write_lock(
        file_db, monkeypatch, caplog):
    import database
    from api.agents import sweep_agents_once

    path, SessionLocal = file_db
    # Reads `online`, last seen an hour ago: this pass owes it `offline`.
    _seed(SessionLocal, last_seen=LONG_AGO, status="online")
    monkeypatch.setattr(database, "AsyncSessionLocal", SessionLocal)

    with caplog.at_level(logging.WARNING), write_lock_held(path):
        changed = asyncio.run(sweep_agents_once())

    assert changed == 0
    assert any("database busy" in rec.getMessage() for rec in caplog.records)
    assert not any(rec.exc_info for rec in caplog.records), "a skipped pass is not a traceback"
    assert _agent(SessionLocal).status == "online"

    # The next pass does the work the skipped one owed.
    assert asyncio.run(sweep_agents_once()) == 1
    assert _agent(SessionLocal).status == "offline"

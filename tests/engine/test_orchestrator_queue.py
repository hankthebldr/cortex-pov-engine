"""GAP-API-005 (durable task queue + rehydrate) and GAP-API-004 (push staged).

Exercises the orchestrator's DB-backed pull-mode queue and the push-mode
terminal-state transition directly against an in-memory SQLite DB.
"""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


@pytest.fixture
def session_factory() -> async_sessionmaker:
    """Fresh in-memory DB with all tables created."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def _init() -> None:
        from database import Base
        import models  # noqa: F401

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    return SessionLocal


def _fresh_orchestrator():
    from engine.orchestrator import Orchestrator

    return Orchestrator()


async def _seed_scenario(db, scenario_id="SIM-EDR-001", *, push=True, pull=True):
    from models import Scenario

    s = Scenario(
        scenario_id=scenario_id,
        name="Test",
        version="1.0",
        status="active",
        plane="EDR",
        uc_ref="UCS-EDR-01", uc_name="x",
        tc_ref="TC-EDR-01", tc_name="y",
        mitre_tactic="TA0006", mitre_tactic_name="Credential Access",
        mitre_technique="T1003", mitre_technique_name="OS Credential Dumping",
        execution_identity={"default": "www-data"},
        push_supported=push, pull_supported=pull,
        steps=[{"id": "step-01", "name": "s", "identity": "www-data",
                "command": "id", "expected_detections": []}],
    )
    db.add(s)
    await db.commit()
    return s


def test_pull_launch_persists_queued_task(session_factory):
    """A pull launch writes a durable queued_tasks row mirroring the in-memory
    task."""
    from models import QueuedTask, Run

    async def _run():
        orch = _fresh_orchestrator()
        async with session_factory() as db:
            await _seed_scenario(db)
            result = await orch.launch(
                scenario_id="SIM-EDR-001", mode="pull", db=db,
                target_agent_id="agent-1",
            )
            assert result.success
            # In-memory queue has it
            assert len(orch.peek_queue("agent-1")) == 1
            # Durable row exists
            rows = (await db.execute(select(QueuedTask))).scalars().all()
            assert len(rows) == 1
            assert rows[0].agent_id == "agent-1"
            assert rows[0].run_id == result.run_id
            assert rows[0].payload["scenario_id"] == "SIM-EDR-001"
            # Run is running
            run = (await db.execute(select(Run).where(Run.run_id == result.run_id))).scalar_one()
            assert run.status == "running"

    asyncio.run(_run())


def test_dequeue_for_agent_removes_durable_row(session_factory):
    from models import QueuedTask

    async def _run():
        orch = _fresh_orchestrator()
        async with session_factory() as db:
            await _seed_scenario(db)
            res = await orch.launch(
                scenario_id="SIM-EDR-001", mode="pull", db=db,
                target_agent_id="agent-1",
            )
            task = await orch.dequeue_for_agent("agent-1", db)
            assert task is not None and task.run_id == res.run_id
            rows = (await db.execute(select(QueuedTask))).scalars().all()
            assert rows == []  # durable row deleted on delivery

    asyncio.run(_run())


def test_rehydrate_restores_queue_and_keeps_running_run(session_factory):
    """A NEW orchestrator (simulating a restart) rebuilds its in-memory queue
    from the durable rows; the run with a surviving task stays running."""
    from models import Run

    async def _run():
        # First process: launch (persists task) but never delivers it.
        orch1 = _fresh_orchestrator()
        async with session_factory() as db:
            await _seed_scenario(db)
            res = await orch1.launch(
                scenario_id="SIM-EDR-001", mode="pull", db=db,
                target_agent_id="agent-1",
            )
            run_id = res.run_id

        # Second process: fresh orchestrator, in-memory queue empty.
        orch2 = _fresh_orchestrator()
        assert orch2.peek_queue("agent-1") == []
        async with session_factory() as db:
            stats = await orch2.rehydrate(db)
            assert stats["rehydrated"] == 1
            assert stats["failed_orphans"] == 0
            # Queue restored — a poll now delivers the task.
            task = await orch2.dequeue_for_agent("agent-1", db)
            assert task is not None and task.run_id == run_id
            run = (await db.execute(select(Run).where(Run.run_id == run_id))).scalar_one()
            assert run.status == "running"  # not failed — task survived

    asyncio.run(_run())


def test_rehydrate_fails_orphaned_running_run(session_factory):
    """A running run whose durable task was lost is marked failed on rehydrate."""
    from models import Run

    async def _run():
        async with session_factory() as db:
            # A running pull run with NO queued_tasks row (lost task).
            db.add(Run(run_id="orphan-1", scenario_id="SIM-X", mode="pull",
                       status="running"))
            await db.commit()

        orch = _fresh_orchestrator()
        async with session_factory() as db:
            stats = await orch.rehydrate(db)
            assert stats["failed_orphans"] == 1
            run = (await db.execute(select(Run).where(Run.run_id == "orphan-1"))).scalar_one()
            assert run.status == "failed"
            assert "RUN FAILED ON RESTART" in (run.output or "")

    asyncio.run(_run())


def test_abort_persisted_deletes_durable_rows(session_factory):
    from models import QueuedTask

    async def _run():
        orch = _fresh_orchestrator()
        async with session_factory() as db:
            await _seed_scenario(db)
            res = await orch.launch(
                scenario_id="SIM-EDR-001", mode="pull", db=db,
                target_agent_id="agent-1",
            )
            await orch.abort_persisted(res.run_id, db)
            assert orch.is_aborted(res.run_id)
            rows = (await db.execute(select(QueuedTask))).scalars().all()
            assert rows == []
            assert orch.peek_queue("agent-1") == []

    asyncio.run(_run())


def test_push_launch_advances_to_staged(session_factory):
    """GAP-API-004 — a push launch ends in the terminal 'staged' state, not
    'pending'."""
    from models import Run, QueuedTask

    async def _run():
        orch = _fresh_orchestrator()
        async with session_factory() as db:
            await _seed_scenario(db)
            res = await orch.launch(
                scenario_id="SIM-EDR-001", mode="push", db=db,
            )
            assert res.success
            assert res.mode == "push"
            assert res.download_url
            run = (await db.execute(select(Run).where(Run.run_id == res.run_id))).scalar_one()
            assert run.status == "staged"
            assert run.completed_at is not None
            assert "PUSH BUNDLE STAGED" in (run.output or "")
            # Push mode does not enqueue anything.
            assert (await db.execute(select(QueuedTask))).scalars().all() == []

    asyncio.run(_run())

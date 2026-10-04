"""A launch must not overwrite a completion that beat it.

``_handle_pull`` used to put the beacon task in the in-memory queue (waking any
long-polling beacon) BEFORE it committed ``status='running'`` / ``open_tasks``.
A multichannel run then dispatched its EAL steps in-process before that commit,
so a fast agent step could be collected, executed and ``/complete``d inside the
window. ``complete_run`` read ``open_tasks`` NULL (⇒ 1), terminalised the run
and scored it — and the launch's later write then set ``status='running'``,
``open_tasks=1`` over the finished run. Result: a run that reads ``running``
forever with a ``completed_at`` stamp, never re-terminalised, its terminal SSE
frame contradicted.

The slow EAL dispatch is simulated by a patched ``_dispatch_eal_in_process``
that plays a fast beacon: it polls for the task and completes the run while the
launch is still in flight.
"""
from __future__ import annotations

import asyncio
from datetime import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.fixture
def file_db(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'launch.db'}")
    SessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def _init():
        from database import Base
        import models  # noqa: F401

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    yield SessionLocal
    asyncio.run(engine.dispose())


async def _seed(db) -> None:
    from models import Agent, Scenario

    db.add(Scenario(
        scenario_id="SIM-MP-900", name="T", version="1.0", status="active", plane="EDR",
        uc_ref="UCS-EDR-01", uc_name="x", tc_ref="TC-EDR-01", tc_name="y",
        mitre_tactic="TA0006", mitre_tactic_name="c", mitre_technique="T1003",
        mitre_technique_name="o", execution_identity={"default": "root"},
        push_supported=True, pull_supported=True,
        steps=[
            {"id": "step-01", "name": "fast", "identity": "root", "command": "id",
             "expected_detections": [{"type": "BIOC", "description": "d1"}]},
            {"id": "step-02", "name": "eal", "identity": "root", "channel": "eal",
             "eal": {"plugin": "ngfw_eal_emitter",
                     "params": {"collector_url": "https://collector.lab.invalid/eal"}},
             "expected_detections": [{"type": "Analytics", "description": "d2"}]},
        ],
    ))
    db.add(Agent(agent_id="agent-1", hostname="h1", os="linux",
                 capabilities=["shell", "identity-harness", "artifact-fetch"],
                 interpreters=[], registered_at=datetime.utcnow(),
                 last_seen=datetime.utcnow(), status="online"))
    await db.commit()


def test_a_completion_inside_the_launch_window_is_not_overwritten(file_db, monkeypatch):
    from api.runs import CompleteRequest, complete_run
    from engine.orchestrator import Orchestrator
    from models import Run

    orch = Orchestrator()
    collected: list = []

    async def _fast_beacon_during_eal(self, run_id, scenario, eal_steps, binding, executed_at, db):
        # A beacon long-polling agent-1 collects whatever is deliverable NOW,
        # runs its (fast) step and reports — all before the launch commits.
        async with file_db() as beacon_db:
            task = await orch.dequeue_for_agent("agent-1", beacon_db)
            if task is not None:
                collected.append(task)
                await complete_run(run_id, CompleteRequest(exit_code=0, summary="fast"), beacon_db)
        return {s["id"]: {"status": "dry_run"} for s in eal_steps}

    monkeypatch.setattr(Orchestrator, "_dispatch_eal_in_process", _fast_beacon_during_eal)

    async def _go():
        async with file_db() as db:
            await _seed(db)
            res = await orch.launch(
                scenario_id="SIM-MP-900", mode="pull", db=db, target_agent_id="agent-1")
            assert res.success, res.error
        # The beacon (if it did not already) collects and completes after launch.
        if not collected:
            async with file_db() as beacon_db:
                task = await orch.dequeue_for_agent("agent-1", beacon_db)
                assert task is not None
                await complete_run(res.run_id, CompleteRequest(exit_code=0, summary="ok"),
                                   beacon_db)
        async with file_db() as db:
            return (await db.execute(select(Run).where(Run.run_id == res.run_id))).scalar_one()

    run = asyncio.run(_go())
    assert run.status == "complete", (run.status, run.open_tasks, run.completed_at)
    assert run.open_tasks == 0
    assert run.completed_at is not None

"""A staging refusal must not overwrite what other callers wrote to the run meanwhile.

``_refuse_unstageable_task`` fails a run when its task needs staged artifacts
and the polling beacon cannot stage them. It read the Run through the ORM,
checked ``_is_terminal`` in Python, then assigned ``status``, ``completed_at``
and ``output = (output or "") + marker`` and committed. The flush writes those
values back as literals, so anything another request committed between the read
and the commit was overwritten. The other endpoints of a fan-out keep calling
back while one endpoint is refused:

* the surviving endpoint's ``/output`` chunk was dropped;
* its ``/complete`` summary was dropped (the decrement survived only because
  the refusal never writes ``open_tasks``);
* an operator abort was overwritten: ``aborted`` became ``failed``, the abort
  marker vanished, ``completed_at`` was re-stamped and a second terminal frame
  went out;
* two beacons refused at once both published a terminal frame and the second
  re-stamped ``completed_at``.

This is the class 5956a47 fixed for runs.py. As in test_runs_concurrency.py,
these tests use a FILE-backed SQLite with the production pool. They do not rely
on gather() timing: a cursor hook runs the competing request to completion just
before the refusal's own write to the runs row reaches SQLite, which is as wide
as the read-to-write window gets. The competing write is always committed first,
and the refusal must keep it.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.util import await_only


ARTIFACT = {
    "name": "linpeas.sh",
    "sha256": "0ea7e9ce5fcca464b5998cb73930e36647ebf9b38590fe250bbd5664fe8670a5",
    "path": "/api/shelf/payload/linpeas.sh",
    "dest": "/tmp/.cache/sysinfo.sh",
    "mode": "0755",
    "steps": ["step-01"],
}
REFUSAL_MARKER = "--- RUN FAILED: AGENT_CANNOT_STAGE_ARTIFACTS ---"


@pytest.fixture
def race_db(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'race.db'}")
    SessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def _init():
        from database import Base
        import models  # noqa: F401

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    yield engine, SessionLocal
    asyncio.run(engine.dispose())


@pytest.fixture(autouse=True)
def _clean_orchestrator():
    from engine.orchestrator import orchestrator

    orchestrator._aborted.clear()
    orchestrator._queue.clear()
    yield
    orchestrator._aborted.clear()
    orchestrator._queue.clear()


@pytest.fixture
def frames(monkeypatch) -> list[tuple[str, str]]:
    """Every terminal ``run.status`` frame published from here on."""
    from events import event_bus

    seen: list[tuple[str, str]] = []

    async def _publish(run_id, evt):
        if evt.get("type") == "run.status":
            seen.append((run_id, evt["data"]["status"]))

    monkeypatch.setattr(event_bus, "publish", _publish)
    return seen


@contextmanager
def _lands_before_the_next_run_write(engine, competitor):
    """Run ``competitor()`` to completion just before the next ``UPDATE runs``
    reaches SQLite, then let that statement proceed.

    The hook fires inside SQLAlchemy's async greenlet, so ``await_only`` can
    drive a whole request (its own session, its own pooled connection) before
    the intercepted statement executes. Yields the intercepted statements, so a
    test can prove the interleaving happened.
    """
    fired: list[str] = []

    def _hook(conn, cursor, statement, parameters, context, executemany):
        if fired or not statement.lstrip().upper().startswith("UPDATE RUNS"):
            return
        fired.append(statement)
        await_only(competitor())

    event.listen(engine.sync_engine, "before_cursor_execute", _hook)
    try:
        yield fired
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _hook)


async def _seed(SessionLocal, run_id, *, open_tasks):
    from models import Run

    async with SessionLocal() as db:
        db.add(Run(run_id=run_id, scenario_id="SIM-EDR-001", mode="pull",
                   status="running", open_tasks=open_tasks))
        await db.commit()


async def _read(SessionLocal, run_id):
    from models import Run

    async with SessionLocal() as db:
        return (await db.execute(select(Run).where(Run.run_id == run_id))).scalar_one()


async def _refuse(SessionLocal, run_id, agent_id="a-old"):
    """Drive the refusal for a legacy beacon that cannot stage artifacts."""
    from api.agents import _refuse_unstageable_task
    from engine.orchestrator import Task
    from models import Agent

    agent = Agent(agent_id=agent_id, hostname="h", os="linux", capabilities=["shell"])
    task = Task(task_id=f"t-{agent_id}", run_id=run_id, scenario_id="SIM-EDR-001",
                steps=[{"id": "step-01", "command": "bash /tmp/.cache/sysinfo.sh"}],
                identity_context=None, artifacts=[ARTIFACT])
    async with SessionLocal() as db:
        return await _refuse_unstageable_task(agent, task, db)


def _assert_refused(gate):
    assert gate is not None and gate.status_code == 409
    assert gate.detail["code"] == "AGENT_CANNOT_STAGE_ARTIFACTS"


def test_output_landing_mid_refusal_is_kept(race_db, frames):
    engine, SessionLocal = race_db
    from api.runs import OutputRequest, append_output

    async def _go():
        await _seed(SessionLocal, "fan-out", open_tasks=2)

        async def _survivor_streams():
            async with SessionLocal() as db:
                await append_output("fan-out", OutputRequest(output="<endpoint-b chunk>"), db)

        with _lands_before_the_next_run_write(engine, _survivor_streams) as fired:
            gate = await _refuse(SessionLocal, "fan-out")
        return gate, fired, await _read(SessionLocal, "fan-out")

    gate, fired, run = asyncio.run(_go())
    assert fired, "the competing /output never landed inside the refusal"
    _assert_refused(gate)
    assert "<endpoint-b chunk>" in run.output, run.output
    assert REFUSAL_MARKER in run.output
    assert run.status == "failed" and run.completed_at is not None
    assert frames == [("fan-out", "failed")]


def test_complete_landing_mid_refusal_keeps_its_summary(race_db, frames):
    engine, SessionLocal = race_db
    from api.runs import CompleteRequest, complete_run

    survivor: dict = {}

    async def _go():
        await _seed(SessionLocal, "fan-out", open_tasks=2)

        async def _survivor_completes():
            async with SessionLocal() as db:
                survivor.update(await complete_run(
                    "fan-out", CompleteRequest(exit_code=0, summary="endpoint-b done"), db))

        with _lands_before_the_next_run_write(engine, _survivor_completes) as fired:
            gate = await _refuse(SessionLocal, "fan-out")
        return gate, fired, await _read(SessionLocal, "fan-out")

    gate, fired, run = asyncio.run(_go())
    assert fired, "the competing /complete never landed inside the refusal"
    assert survivor == {"status": "running", "run_id": "fan-out", "pending_endpoints": 1}
    _assert_refused(gate)
    assert "endpoint-b done" in run.output, run.output
    assert REFUSAL_MARKER in run.output
    assert run.open_tasks == 1  # the survivor's decrement; the refusal does not decrement
    assert run.status == "failed" and run.completed_at is not None
    assert frames == [("fan-out", "failed")]


def test_abort_landing_mid_refusal_is_not_overwritten(race_db, frames):
    """The operator's abort finished the run first. The refusal must find it
    finished and change nothing: no flip to ``failed``, no re-stamp, no second
    terminal frame. The legacy beacon is still refused."""
    engine, SessionLocal = race_db
    from api.runs import abort_run

    aborted: dict = {}

    async def _go():
        await _seed(SessionLocal, "fan-out", open_tasks=2)

        async def _operator_aborts():
            async with SessionLocal() as db:
                aborted.update(await abort_run("fan-out", db))
            aborted["completed_at"] = (await _read(SessionLocal, "fan-out")).completed_at

        with _lands_before_the_next_run_write(engine, _operator_aborts) as fired:
            gate = await _refuse(SessionLocal, "fan-out")
        return gate, fired, await _read(SessionLocal, "fan-out")

    gate, fired, run = asyncio.run(_go())
    assert fired, "the competing abort never landed inside the refusal"
    assert aborted["status"] == "aborted" and aborted["was_terminal"] is False
    _assert_refused(gate)
    assert run.status == "aborted"
    assert "RUN ABORTED BY OPERATOR" in run.output, run.output
    assert REFUSAL_MARKER not in run.output
    assert run.completed_at == aborted["completed_at"]
    assert frames == [("fan-out", "aborted")]


def test_two_refusals_racing_publish_one_terminal_frame(race_db, frames):
    """Two legacy beacons in one fan-out are refused together. The first to
    write fails the run; the second must neither re-stamp it nor publish a
    second terminal frame, and both beacons still get the 409."""
    engine, SessionLocal = race_db

    first: dict = {}

    async def _go():
        await _seed(SessionLocal, "fan-out", open_tasks=2)

        async def _other_beacon_refused():
            first["gate"] = await _refuse(SessionLocal, "fan-out", agent_id="a-old-2")
            first["completed_at"] = (await _read(SessionLocal, "fan-out")).completed_at

        with _lands_before_the_next_run_write(engine, _other_beacon_refused) as fired:
            gate = await _refuse(SessionLocal, "fan-out", agent_id="a-old-1")
        return gate, fired, await _read(SessionLocal, "fan-out")

    gate, fired, run = asyncio.run(_go())
    assert fired, "the competing refusal never landed inside the refusal"
    _assert_refused(first["gate"])
    _assert_refused(gate)
    assert frames == [("fan-out", "failed")]
    assert run.status == "failed"
    assert run.completed_at == first["completed_at"]
    assert run.output.count(REFUSAL_MARKER) == 1
    assert "a-old-2" in run.output  # the first verdict stands

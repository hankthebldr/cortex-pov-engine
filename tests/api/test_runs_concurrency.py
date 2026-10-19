"""Concurrent agent callbacks must not lose writes to a Run row.

``complete_run``, ``append_output`` and ``abort_run`` each read the Run, compute
the new value in Python and write it back. pysqlite does not open a transaction
for a SELECT, so two requests on two pooled connections both read the same
``open_tasks`` / ``output`` and the second write silently discards the first.
That is not hypothetical:

* a multi-endpoint fan-out POSTs ``/complete`` once per endpoint, and two
  endpoints finishing together left the run ``running`` with ``open_tasks=1``
  forever — never terminalised, never scored, no terminal SSE frame;
* the beacon's stdout and stderr stream writers are separate goroutines, each
  POSTing ``/output`` — concurrent chunks were dropped, including the refusal
  markers ``_execution_integrity`` reads to warn a POV report that a step never
  ran;
* an operator abort racing a completion lost one side's output and could land
  ``complete`` over ``aborted``.

These tests use a FILE-backed SQLite with the production pool (the shared
in-memory StaticPool used elsewhere serialises everything onto one connection
and cannot show the race) and drive the real route functions concurrently.
"""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.fixture
def file_db(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'race.db'}")
    SessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def _init():
        from database import Base
        import models  # noqa: F401

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    yield SessionLocal
    asyncio.run(engine.dispose())


@pytest.fixture(autouse=True)
def _clean_orchestrator():
    from engine.orchestrator import orchestrator

    orchestrator._aborted.clear()
    orchestrator._queue.clear()
    yield
    orchestrator._aborted.clear()
    orchestrator._queue.clear()


async def _seed(SessionLocal, run_id, *, open_tasks=None, status="running"):
    from models import Run

    async with SessionLocal() as db:
        db.add(Run(run_id=run_id, scenario_id="SIM-EDR-001", mode="pull",
                   status=status, open_tasks=open_tasks))
        await db.commit()


async def _read(SessionLocal, run_id):
    from models import Run

    async with SessionLocal() as db:
        return (await db.execute(select(Run).where(Run.run_id == run_id))).scalar_one()


def test_two_endpoints_completing_together_terminalise_the_run(file_db):
    from api.runs import CompleteRequest, complete_run

    async def _go():
        await _seed(file_db, "fan-2", open_tasks=2)

        async def _complete(summary):
            async with file_db() as db:
                return await complete_run(
                    "fan-2", CompleteRequest(exit_code=0, summary=summary), db)

        outs = await asyncio.gather(_complete("endpoint-a"), _complete("endpoint-b"))
        return outs, await _read(file_db, "fan-2")

    outs, run = asyncio.run(_go())
    assert run.open_tasks == 0, f"lost decrement: {outs}"
    assert run.status == "complete"
    assert run.completed_at is not None
    # Exactly one caller saw the last endpoint land.
    assert sorted(o["status"] for o in outs) == ["complete", "running"], outs
    assert "endpoint-a" in run.output and "endpoint-b" in run.output


def test_concurrent_output_chunks_are_all_kept(file_db):
    from api.runs import OutputRequest, append_output

    async def _go():
        await _seed(file_db, "stream-1", open_tasks=1)

        async def _post(i):
            async with file_db() as db:
                return await append_output("stream-1", OutputRequest(output=f"<chunk-{i}>"), db)

        await asyncio.gather(*(_post(i) for i in range(12)))
        return await _read(file_db, "stream-1")

    run = asyncio.run(_go())
    missing = [i for i in range(12) if f"<chunk-{i}>" not in (run.output or "")]
    assert missing == [], f"dropped chunks {missing}"


def test_output_racing_completion_keeps_both(file_db):
    from api.runs import CompleteRequest, OutputRequest, append_output, complete_run

    async def _go():
        await _seed(file_db, "race-oc", open_tasks=1)

        async def _out():
            async with file_db() as db:
                return await append_output(
                    "race-oc", OutputRequest(output="PAYLOAD_NOT_STAGED_ON_TARGET"), db)

        async def _done():
            async with file_db() as db:
                return await complete_run(
                    "race-oc", CompleteRequest(exit_code=0, summary="done"), db)

        await asyncio.gather(_out(), _done())
        return await _read(file_db, "race-oc")

    run = asyncio.run(_go())
    assert "PAYLOAD_NOT_STAGED_ON_TARGET" in (run.output or "")
    assert "COMPLETION SUMMARY" in (run.output or "")


def test_abort_racing_completion_never_loses_a_side(file_db):
    from api.runs import CompleteRequest, abort_run, complete_run

    async def _go():
        await _seed(file_db, "race-ab", open_tasks=1)

        async def _abort():
            async with file_db() as db:
                return await abort_run("race-ab", db)

        async def _done():
            async with file_db() as db:
                return await complete_run(
                    "race-ab", CompleteRequest(exit_code=0, summary="done"), db)

        abort_out, done_out = await asyncio.gather(_abort(), _done())
        return abort_out, done_out, await _read(file_db, "race-ab")

    abort_out, done_out, run = asyncio.run(_go())
    if run.status == "aborted":
        # The abort won: the completion must have been told so, not "complete".
        assert done_out["status"] == "aborted", (abort_out, done_out)
        assert "RUN ABORTED BY OPERATOR" in run.output
    else:
        # The completion won: the abort must have been a no-op on a terminal run.
        assert run.status == "complete"
        assert abort_out["was_terminal"] is True, (abort_out, done_out)
        assert "COMPLETION SUMMARY" in run.output

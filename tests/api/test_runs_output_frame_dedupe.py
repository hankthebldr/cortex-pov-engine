"""A retried /output frame is stored once (RG-AGENT-06, server half).

The beacon retries a step's ``/output`` on a transport error or a 5xx (1, 2,
4, 8, 16 s), and SimCore's append is unconditional
(``output = coalesce(output,'') || chunk``). A frame whose first attempt was
stored but whose reply was lost was therefore stored twice: the step's block,
its ``=== STEP`` header and every integrity marker appeared two times in the
run record and in the POV report.

Since 661047c the beacon keys every non-streaming frame with ``frame_id``
(``<task_id>:<n>``, assigned once before the first attempt). SimCore now
records ``(run_id, frame_id)`` in ``run_output_frames`` in the same
transaction as the append and skips a frame it has already stored. A body
without ``frame_id`` (an older beacon, a live streaming chunk) appends exactly
as before.
"""
from __future__ import annotations

import asyncio
from datetime import datetime

import pytest
from sqlalchemy import select


@pytest.fixture
def client(make_client):
    from api.runs import router as runs_router

    return make_client(runs_router)


def _seed(session_factory, run_id="fr-1"):
    from models import Run

    async def _go():
        async with session_factory() as db:
            db.add(Run(run_id=run_id, scenario_id="SIM-EDR-001", mode="pull",
                       target="agent-1", status="running", open_tasks=1,
                       started_at=datetime.utcnow(), delivered_at=datetime.utcnow()))
            await db.commit()

    asyncio.run(_go())
    return run_id


def _output(session_factory, run_id="fr-1") -> str:
    from models import Run

    async def _go():
        async with session_factory() as db:
            return (await db.execute(
                select(Run.output).where(Run.run_id == run_id))).scalar_one() or ""

    return asyncio.run(_go())


FRAME = "=== STEP 1/2 · step-01 · T1059 · identity=root ===\nok\n--- exit_code=0 duration=1ms ---\n"


def test_a_retried_frame_is_stored_once(client, session_factory):
    _seed(session_factory)
    body = {"output": FRAME, "step_id": "step-01", "frame_id": "task-A:1"}
    first = client.post("/api/runs/fr-1/output", json=body)
    retry = client.post("/api/runs/fr-1/output", json=body)
    assert first.status_code == 200 and retry.status_code == 200, (first.text, retry.text)
    assert retry.json().get("duplicate") is True, retry.json()
    assert first.json().get("duplicate") is not True, first.json()
    assert _output(session_factory).count("=== STEP 1/2") == 1


def test_frames_with_different_ids_both_append(client, session_factory):
    _seed(session_factory)
    client.post("/api/runs/fr-1/output", json={"output": "a\n", "frame_id": "task-A:1"})
    client.post("/api/runs/fr-1/output", json={"output": "b\n", "frame_id": "task-A:2"})
    # Same counter, another task of the same run (a fan-out): not a duplicate.
    client.post("/api/runs/fr-1/output", json={"output": "c\n", "frame_id": "task-B:1"})
    assert _output(session_factory) == "a\nb\nc\n"


def test_a_body_without_frame_id_appends_every_time(client, session_factory):
    _seed(session_factory)
    for _ in range(2):
        r = client.post("/api/runs/fr-1/output", json={"output": "live\n", "step_id": "step-01"})
        assert r.status_code == 200 and r.json().get("duplicate") is not True, r.text
    assert _output(session_factory) == "live\nlive\n"


def test_the_same_frame_id_on_another_run_is_not_a_duplicate(client, session_factory):
    _seed(session_factory, "fr-1")
    _seed(session_factory, "fr-2")
    client.post("/api/runs/fr-1/output", json={"output": "x\n", "frame_id": "t:1"})
    client.post("/api/runs/fr-2/output", json={"output": "y\n", "frame_id": "t:1"})
    assert _output(session_factory, "fr-2") == "y\n"


def test_an_oversized_frame_id_is_refused(client, session_factory):
    _seed(session_factory)
    r = client.post("/api/runs/fr-1/output", json={"output": "x", "frame_id": "f" * 129})
    assert r.status_code == 422, r.text

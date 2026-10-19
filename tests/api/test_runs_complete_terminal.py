"""A finished run is finished: a late or duplicate /complete must not reopen it.

``complete_run``'s only guard used to be ``Run.status != "aborted"``. A run that
completed with exit 0 read ``complete``; a second /complete with exit 1 then
matched the UPDATE, ``open_tasks`` clamped to 0, and the request took the
last-endpoint branch. The finished run flipped ``complete`` -> ``failed``, its
``completed_at`` was re-stamped, a second COMPLETION SUMMARY was appended, the
terminal ``run.status`` SSE frame went out again and the run was re-scored. A
push-mode ``staged`` run flipped to ``complete`` the same way.

The beacon does not retry /complete, so the trigger is a replay, a proxy or
another caller. It does not need to be frequent to matter: the verdict on a
finished run is what a POV report quotes, and it changed after the fact.

The guard has a boundary. Failure is sticky across a fan-out, so a run reads
``failed`` while it still waits on other endpoints (``completed_at`` unset,
``open_tasks`` > 0). That run is still in flight and must keep accepting its
surviving endpoints' /complete.
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


def side_effects(monkeypatch) -> dict[str, list]:
    """Record what reaches the event bus and the scorer from this call on."""
    import connectors.service as service
    from events import event_bus

    seen: dict[str, list] = {"published": [], "scored": []}

    async def _publish(run_id, event):
        seen["published"].append((run_id, event))

    real_score = service.score_run_safely

    async def _score(db, run, **kwargs):
        seen["scored"].append((run.run_id, kwargs.get("source")))
        return await real_score(db, run, **kwargs)

    monkeypatch.setattr(event_bus, "publish", _publish)
    monkeypatch.setattr(service, "score_run_safely", _score)
    return seen


def _seed(session_factory, run_id: str, *, status: str = "running",
          open_tasks=1, completed_at=None, output=None) -> None:
    from models import Run

    async def _go():
        async with session_factory() as db:
            db.add(Run(
                run_id=run_id, scenario_id="SIM-EDR-001", mode="pull",
                status=status, open_tasks=open_tasks, completed_at=completed_at,
                output=output, started_at=datetime.utcnow() - timedelta(seconds=30),
            ))
            await db.commit()

    asyncio.run(_go())


def _row(session_factory, run_id: str) -> dict:
    from models import Run

    async def _go():
        async with session_factory() as db:
            run = (await db.execute(select(Run).where(Run.run_id == run_id))).scalar_one()
            return {
                "status": run.status,
                "completed_at": run.completed_at,
                "output": run.output,
                "open_tasks": run.open_tasks,
                "tc_verdict": run.tc_verdict,
            }

    return asyncio.run(_go())


def _complete(client, run_id: str, exit_code: int, summary: str):
    return client.post(
        f"/api/runs/{run_id}/complete", json={"exit_code": exit_code, "summary": summary})


def test_late_complete_cannot_reopen_a_completed_run(client, session_factory, monkeypatch):
    """The defect as reproduced in the prod image: exit 0, then a late exit 1."""
    _seed(session_factory, "done-1")
    first = _complete(client, "done-1", 0, "first")
    assert first.status_code == 200 and first.json()["status"] == "complete", first.json()
    before = _row(session_factory, "done-1")
    assert before["completed_at"] is not None

    seen = side_effects(monkeypatch)
    late = _complete(client, "done-1", 1, "late replay")

    assert late.status_code == 200
    assert late.json() == {"status": "complete", "run_id": "done-1", "already_terminal": True}
    assert _row(session_factory, "done-1") == before
    assert seen == {"published": [], "scored": []}


_STAMP = datetime(2026, 10, 19, 12, 0, 0)


@pytest.mark.parametrize(
    "status, open_tasks",
    [
        ("complete", 0),
        ("failed", 0),     # a fan-out whose last endpoint landed after a failure
        ("staged", None),  # push mode: SimCore's role ended at bundle generation
        ("aborted", 1),    # an operator abort wins over the agent's exit-130 report
    ],
)
@pytest.mark.parametrize("exit_code", [0, 1])
def test_every_terminal_state_absorbs_a_late_complete(
    client, session_factory, monkeypatch, status, open_tasks, exit_code,
):
    run_id = f"term-{status}-{exit_code}"
    _seed(session_factory, run_id, status=status, open_tasks=open_tasks,
          completed_at=_STAMP, output="recorded\n")
    before = _row(session_factory, run_id)

    seen = side_effects(monkeypatch)
    r = _complete(client, run_id, exit_code, "late")

    assert r.status_code == 200
    assert r.json() == {"status": status, "run_id": run_id, "already_terminal": True}
    assert _row(session_factory, run_id) == before
    assert seen == {"published": [], "scored": []}


def test_sticky_failure_still_accepts_the_surviving_endpoint(client, session_factory, monkeypatch):
    """The guard's boundary: ``failed`` with endpoints still owed is in flight."""
    _seed(session_factory, "fan-sticky", open_tasks=2)
    first = _complete(client, "fan-sticky", 1, "endpoint-a boom")
    assert first.json() == {"status": "failed", "run_id": "fan-sticky", "pending_endpoints": 1}
    assert _row(session_factory, "fan-sticky")["completed_at"] is None

    seen = side_effects(monkeypatch)
    last = _complete(client, "fan-sticky", 0, "endpoint-b ok")

    assert last.status_code == 200
    body = last.json()
    assert body["status"] == "failed" and "already_terminal" not in body, body
    after = _row(session_factory, "fan-sticky")
    assert after["completed_at"] is not None and after["open_tasks"] == 0
    assert "endpoint-a boom" in after["output"] and "endpoint-b ok" in after["output"]
    status_frames = [e["data"]["status"] for _, e in seen["published"] if e["type"] == "run.status"]
    assert status_frames == ["failed"]
    assert seen["scored"] == [("fan-sticky", "complete")]


def test_staging_refusal_ends_the_run_for_every_endpoint(client, session_factory, monkeypatch):
    """AGENT_CANNOT_STAGE_ARTIFACTS fails the whole fan-out without decrementing
    ``open_tasks``: it stamps ``completed_at`` and publishes the terminal frame.
    That makes the refusal a run-level verdict, like an operator abort. The
    control poll stops the surviving endpoint, and the survivor's /complete must
    not reopen, re-stamp or hang the run."""
    from engine.orchestrator import Task, orchestrator

    client.post("/api/agents/register", json={
        "agent_id": "a-old", "hostname": "h", "os": "linux", "capabilities": ["shell"],
    }).raise_for_status()
    _seed(session_factory, "fan-stage", open_tasks=2)
    orchestrator._enqueue("a-old", Task(
        task_id="t-old", run_id="fan-stage", scenario_id="SIM-EDR-001",
        steps=[{"id": "step-01", "command": "bash /tmp/.cache/sysinfo.sh"}],
        identity_context=None,
        artifacts=[{"name": "linpeas.sh", "sha256": "0" * 64,
                    "path": "/api/shelf/payload/linpeas.sh",
                    "dest": "/tmp/.cache/sysinfo.sh", "mode": "0755",
                    "steps": ["step-01"]}],
    ))

    refused = client.get("/api/agents/a-old/tasks")
    assert refused.status_code == 409
    before = _row(session_factory, "fan-stage")
    assert before["status"] == "failed" and before["completed_at"] is not None
    assert before["open_tasks"] == 2  # the refusal does not decrement
    # The surviving endpoint is told to stop on its next control poll.
    assert client.get("/api/runs/fan-stage/control").json()["abort"] is True

    seen = side_effects(monkeypatch)
    survivor = _complete(client, "fan-stage", 130, "stopped by control poll")

    assert survivor.status_code == 200
    assert survivor.json() == {"status": "failed", "run_id": "fan-stage", "already_terminal": True}
    assert _row(session_factory, "fan-stage") == before
    assert seen == {"published": [], "scored": []}

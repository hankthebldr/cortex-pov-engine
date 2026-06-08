"""Direct router tests for the Phase 2 abort + control channel on /api/runs.

Covers:
  - POST /api/runs/{id}/abort
      * happy path: pending -> aborted, running -> aborted (completed_at + marker)
      * idempotency: terminal run (complete/failed/aborted) -> 200 was_terminal=true
      * 404 for unknown run (structured envelope)
  - GET /api/runs/{id}/control
      * not-aborted running run  -> {abort: false}
      * after POST /abort         -> {abort: true}
      * terminal status in DB     -> {abort: true} (covers lost in-memory set)
      * unknown run               -> {abort: true, status: "unknown"}
  - orchestrator interplay: abort drops a queued task; complete clears the set;
    a late /complete after /abort keeps the run aborted.

The orchestrator is a process-wide singleton, so every test here uses a
unique run_id and clears the aborted set afterwards to avoid cross-test bleed.
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
    """Reset the shared orchestrator aborted-set before and after each test so
    the module-level singleton doesn't leak state between tests."""
    from engine.orchestrator import orchestrator

    orchestrator._aborted.clear()
    orchestrator._queue.clear()
    yield
    orchestrator._aborted.clear()
    orchestrator._queue.clear()


def _seed_run(session_factory, run_id: str, status: str = "running"):
    """Insert a single Run row with the given status; returns run_id."""
    from models import Run

    async def _seed():
        async with session_factory() as db:
            db.add(
                Run(
                    run_id=run_id,
                    scenario_id="SIM-EDR-001",
                    mode="pull",
                    status=status,
                    started_at=datetime.utcnow() - timedelta(seconds=30),
                )
            )
            await db.commit()

    asyncio.run(_seed())
    return run_id


# ---------------------------------------------------------------------------
# POST /api/runs/{id}/abort
# ---------------------------------------------------------------------------


def test_abort_running_run_transitions_to_aborted(client, session_factory):
    run_id = _seed_run(session_factory, "ab-running", status="running")
    r = client.post(f"/api/runs/{run_id}/abort")
    assert r.status_code == 200
    body = r.json()
    assert body == {"status": "aborted", "run_id": run_id, "was_terminal": False}

    # The DB row is now aborted with completed_at stamped + operator marker.
    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["status"] == "aborted"
    assert detail["completed_at"] is not None
    assert "RUN ABORTED BY OPERATOR" in (detail["output"] or "")


def test_abort_pending_run_transitions_to_aborted(client, session_factory):
    run_id = _seed_run(session_factory, "ab-pending", status="pending")
    r = client.post(f"/api/runs/{run_id}/abort")
    assert r.status_code == 200
    assert r.json()["status"] == "aborted"
    assert r.json()["was_terminal"] is False


def test_abort_records_run_in_orchestrator_aborted_set(client, session_factory):
    from engine.orchestrator import orchestrator

    run_id = _seed_run(session_factory, "ab-orch", status="running")
    assert orchestrator.is_aborted(run_id) is False
    client.post(f"/api/runs/{run_id}/abort").raise_for_status()
    assert orchestrator.is_aborted(run_id) is True


@pytest.mark.parametrize("terminal_status", ["complete", "failed", "aborted"])
def test_abort_is_idempotent_on_terminal_run(client, session_factory, terminal_status):
    run_id = _seed_run(session_factory, f"ab-term-{terminal_status}", status=terminal_status)
    r = client.post(f"/api/runs/{run_id}/abort")
    assert r.status_code == 200
    body = r.json()
    assert body == {"status": terminal_status, "run_id": run_id, "was_terminal": True}

    # A terminal run is NOT added to the aborted set (no-op path).
    from engine.orchestrator import orchestrator
    if terminal_status != "aborted":
        assert orchestrator.is_aborted(run_id) is False


def test_abort_unknown_run_returns_404_envelope(client):
    r = client.post("/api/runs/does-not-exist/abort")
    assert r.status_code == 404
    detail = r.json()["detail"]
    assert detail["code"] == "RUN_NOT_FOUND"
    assert "does-not-exist" in detail["detail"]


def test_abort_drops_queued_task_from_orchestrator(client, session_factory):
    """abort() must remove a queued-but-undelivered Task for the run so an
    agent that polls after the abort never receives it."""
    from engine.orchestrator import orchestrator, Task

    run_id = _seed_run(session_factory, "ab-queued", status="running")
    task = Task(
        task_id="t-1",
        run_id=run_id,
        scenario_id="SIM-EDR-001",
        steps=[{"id": "step-01", "command": "id", "identity": "www-data"}],
        identity_context=None,
    )
    orchestrator._enqueue("agent-x", task)
    assert orchestrator.peek_queue("agent-x")  # task is queued

    client.post(f"/api/runs/{run_id}/abort").raise_for_status()

    # Queue for that agent no longer holds the aborted run's task.
    assert all(t.run_id != run_id for t in orchestrator.peek_queue("agent-x"))
    # And dequeue returns nothing deliverable.
    assert orchestrator.dequeue("agent-x") is None


# ---------------------------------------------------------------------------
# GET /api/runs/{id}/control
# ---------------------------------------------------------------------------


def test_control_running_run_not_aborted(client, session_factory):
    run_id = _seed_run(session_factory, "ctl-running", status="running")
    r = client.get(f"/api/runs/{run_id}/control")
    assert r.status_code == 200
    assert r.json() == {"abort": False, "run_id": run_id, "status": "running"}


def test_control_reflects_abort_after_post(client, session_factory):
    run_id = _seed_run(session_factory, "ctl-aborted", status="running")
    # Before abort: keep going.
    assert client.get(f"/api/runs/{run_id}/control").json()["abort"] is False

    client.post(f"/api/runs/{run_id}/abort").raise_for_status()

    body = client.get(f"/api/runs/{run_id}/control").json()
    assert body["abort"] is True
    assert body["status"] == "aborted"


@pytest.mark.parametrize("terminal_status", ["complete", "failed", "aborted"])
def test_control_terminal_status_signals_abort(client, session_factory, terminal_status):
    """A run that reached a terminal status in the DB returns abort=true even
    if the in-memory aborted set was lost (e.g. SimCore restart)."""
    run_id = _seed_run(session_factory, f"ctl-term-{terminal_status}", status=terminal_status)
    body = client.get(f"/api/runs/{run_id}/control").json()
    assert body["abort"] is True
    assert body["status"] == terminal_status


def test_control_unknown_run_signals_abort(client):
    """A vanished run (DB reset) must halt the agent, not spin it."""
    body = client.get("/api/runs/ghost-run/control").json()
    assert body == {"abort": True, "run_id": "ghost-run", "status": "unknown"}


# ---------------------------------------------------------------------------
# /complete interplay with abort
# ---------------------------------------------------------------------------


def test_complete_after_abort_keeps_run_aborted(client, session_factory):
    """A late agent /complete callback must NOT overwrite an operator abort."""
    run_id = _seed_run(session_factory, "ab-then-complete", status="running")
    client.post(f"/api/runs/{run_id}/abort").raise_for_status()

    # Agent reports the aborted exit (130) afterwards.
    r = client.post(
        f"/api/runs/{run_id}/complete",
        json={"exit_code": 130, "summary": "aborted by operator"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "aborted"

    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["status"] == "aborted"


def test_complete_clears_run_from_aborted_set(client, session_factory):
    """When a run reaches a (non-aborted) terminal state via /complete, it is
    pruned from the orchestrator's aborted set to keep it bounded."""
    from engine.orchestrator import orchestrator

    run_id = _seed_run(session_factory, "complete-clears", status="running")
    # Manually seed the aborted set then complete with exit 0; complete should
    # discard it (run was not actually marked aborted in DB).
    orchestrator._aborted.add(run_id)
    client.post(
        f"/api/runs/{run_id}/complete",
        json={"exit_code": 0, "summary": "ok"},
    ).raise_for_status()
    assert orchestrator.is_aborted(run_id) is False

"""Deleting an agent must never orphan a run (the delete half of OPS-08).

``DELETE /api/agents/{id}`` removed the registry row and nothing else. Whatever
was still owed to that agent stayed exactly where it was:

* a **queued, never-collected** task kept its durable ``queued_tasks`` row and
  its run stayed ``running``. Nothing ends such a run: the beacon-lost sweep
  walks ``Agent`` rows, and this one is gone. Worse, the task was still keyed
  on the agent id, so the next beacon to register under that id (the legacy
  installer's default is ``jumpbox-01``) was handed a scenario launched at a
  different host, at a different time. Executed on c852eac: DELETE 200, then
  register + poll under the same id -> the old task is delivered.
* a **collected, still-running** run lost the only row the sweep uses to
  decide its beacon is gone, so a dead beacon's run read ``running`` forever.

The route now refuses (409 ``AGENT_HAS_OPEN_RUNS``) while any run is owed to
the agent or in flight on it, names the runs, and names the existing way to end
them (``POST /api/runs/{id}/abort``, which drops the queued task and stamps the
results). It cancels nothing by itself: ending a run is the operator's call,
and whether an undelivered task should ever expire on its own is an open
decision this does not take.
"""
from __future__ import annotations

import asyncio
from datetime import datetime

import pytest
from sqlalchemy import select


@pytest.fixture
def client(session_factory):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.agents import router as agents_router
    from api.runs import router as runs_router
    from database import get_db

    async def _get_db():
        async with session_factory() as s:
            yield s

    app = FastAPI()
    for r in (agents_router, runs_router):
        app.include_router(r, prefix="/api")
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


def _seed(session_factory, *agent_ids: str) -> None:
    from models import Agent, Scenario

    async def _go():
        async with session_factory() as db:
            db.add(Scenario(
                scenario_id="SIM-EDR-952", name="T", version="1.0", status="active", plane="EDR",
                uc_ref="UCS-EDR-01", uc_name="x", tc_ref="TC-EDR-01", tc_name="y",
                mitre_tactic="TA0007", mitre_tactic_name="Discovery",
                mitre_technique="T1082", mitre_technique_name="System Information Discovery",
                execution_identity={"default": "root"}, push_supported=True, pull_supported=True,
                steps=[{"id": "step-01", "name": "id", "identity": "root", "command": "id",
                        "mitre_technique": "T1082",
                        "expected_detections": [{
                            "plane": "EDR", "type": "BIOC", "description": "sysinfo",
                            "detection_id": "bioc-sysinfo"}]}],
            ))
            for agent_id in agent_ids:
                db.add(Agent(agent_id=agent_id, hostname="lab", os="linux",
                             capabilities=["shell", "identity-harness", "artifact-fetch"],
                             interpreters=[], registered_at=datetime.utcnow(),
                             last_seen=datetime.utcnow(), status="online"))
            await db.commit()

    asyncio.run(_go())


def _launch(session_factory, agent_id: str) -> str:
    from engine.orchestrator import orchestrator

    async def _go():
        async with session_factory() as db:
            res = await orchestrator.launch(scenario_id="SIM-EDR-952", mode="pull",
                                            db=db, target_agent_id=agent_id)
            assert res.success, res.message
            return res.run_id

    return asyncio.run(_go())


def _state(session_factory, run_id: str) -> dict:
    from models import QueuedTask, Result, Run

    async def _go():
        async with session_factory() as db:
            run = (await db.execute(select(Run).where(Run.run_id == run_id))).scalar_one()
            queued = (await db.execute(
                select(QueuedTask).where(QueuedTask.run_id == run_id))).scalars().all()
            rows = (await db.execute(
                select(Result).where(Result.run_id == run_id))).scalars().all()
            return {"status": run.status, "queued": len(queued),
                    "outcomes": sorted({r.outcome for r in rows}, key=str)}

    return asyncio.run(_go())


def _agent_ids(client) -> list[str]:
    return [a["agent_id"] for a in client.get("/api/agents").json()["agents"]]


def _register(client, agent_id: str) -> None:
    client.post("/api/agents/register",
                json={"agent_id": agent_id, "hostname": "another-host", "os": "linux",
                      "capabilities": ["shell", "identity-harness", "artifact-fetch"]}).raise_for_status()


# ---------------------------------------------------------------------------


def test_an_agent_with_a_queued_run_is_not_deleted_out_from_under_it(client, session_factory):
    _seed(session_factory, "jumpbox-01")
    run_id = _launch(session_factory, "jumpbox-01")

    r = client.delete("/api/agents/jumpbox-01")
    # c852eac: 200 deleted, and the run below stays `running` with its task
    # still queued for an agent that no longer exists.
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "AGENT_HAS_OPEN_RUNS"
    assert detail["run_ids"] == [run_id]
    assert run_id in detail["detail"] and f"/api/runs/{run_id}/abort" in detail["detail"]

    assert _agent_ids(client) == ["jumpbox-01"], "a refused delete must change nothing"
    assert _state(session_factory, run_id) == {"status": "running", "queued": 1, "outcomes": [None]}


def test_a_stale_task_is_never_handed_to_a_later_agent_with_the_same_id(client, session_factory):
    """The hazard, end to end. On c852eac the last poll below delivers the task
    launched at the DELETED agent to whichever host registered under its id."""
    _seed(session_factory, "jumpbox-01")
    run_id = _launch(session_factory, "jumpbox-01")

    assert client.delete("/api/agents/jumpbox-01").status_code == 409
    # The way out the refusal names: end the run, then delete.
    assert client.post(f"/api/runs/{run_id}/abort").json()["status"] == "aborted"
    assert client.delete("/api/agents/jumpbox-01").status_code == 200
    assert _agent_ids(client) == []
    assert _state(session_factory, run_id) == {
        "status": "aborted", "queued": 0, "outcomes": ["not_executed"]}

    _register(client, "jumpbox-01")          # a different host, same legacy id
    poll = client.get("/api/agents/jumpbox-01/tasks")
    assert poll.status_code == 200 and poll.json() == {"task": None}, poll.text


def test_an_agent_with_a_collected_run_still_in_flight_is_not_deleted(client, session_factory):
    """Its row is what the beacon-lost sweep reads to end the run if the beacon
    never reports again."""
    _seed(session_factory, "agent-a")
    run_id = _launch(session_factory, "agent-a")
    assert client.get("/api/agents/agent-a/tasks").json()["task"]["run_id"] == run_id   # collected

    r = client.delete("/api/agents/agent-a")
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["run_ids"] == [run_id]

    done = client.post(f"/api/runs/{run_id}/complete", json={"exit_code": 0, "summary": "ok"})
    assert done.status_code == 200, done.text
    assert client.delete("/api/agents/agent-a").status_code == 200


def test_a_fan_out_task_queued_for_a_second_endpoint_blocks_that_endpoints_delete(
    client, session_factory
):
    """The run's own target is agent-a; its second partition is queued for
    agent-b. Deleting agent-b would strand that partition just the same."""
    from engine.orchestrator import Task, orchestrator

    _seed(session_factory, "agent-a", "agent-b")
    run_id = _launch(session_factory, "agent-a")

    async def _queue_for_b():
        async with session_factory() as db:
            task = Task(task_id="t-b", run_id=run_id, scenario_id="SIM-EDR-952",
                        steps=[{"id": "step-02", "command": "id"}], identity_context=None)
            orchestrator._enqueue("agent-b", task)
            await orchestrator._persist_task(db, "agent-b", task)

    asyncio.run(_queue_for_b())
    r = client.delete("/api/agents/agent-b")
    assert r.status_code == 409 and r.json()["detail"]["run_ids"] == [run_id], r.text


def test_an_idle_agent_is_deleted_and_an_unknown_one_is_404(client, session_factory):
    _seed(session_factory, "agent-a", "agent-b")
    run_id = _launch(session_factory, "agent-b")     # someone else's open run

    r = client.delete("/api/agents/agent-a")
    assert r.status_code == 200 and r.json() == {"status": "deleted", "agent_id": "agent-a"}
    assert _agent_ids(client) == ["agent-b"]
    assert _state(session_factory, run_id)["status"] == "running"

    r = client.delete("/api/agents/never-was")
    assert r.status_code == 404 and r.json()["detail"]["code"] == "AGENT_NOT_FOUND"


def test_an_agent_whose_runs_all_finished_is_deleted(client, session_factory):
    _seed(session_factory, "agent-a")
    run_id = _launch(session_factory, "agent-a")
    client.get("/api/agents/agent-a/tasks").raise_for_status()
    client.post(f"/api/runs/{run_id}/complete",
                json={"exit_code": 0, "summary": "ok"}).raise_for_status()
    assert client.delete("/api/agents/agent-a").status_code == 200

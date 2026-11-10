"""A delivered run whose beacon walked away is terminalised, not left running (OPS-03).

The failure, after a SimCore restart:

1. The beacon collected run R's task (``delivered_at`` set; the durable row is
   deleted on delivery, so ``rehydrate()`` correctly leaves R ``running``).
2. The beacon finished R and POSTed ``/complete`` into the restart window. The
   POST failed; the beacon moved on.
3. The beacon keeps long-polling ``/tasks`` for new work, so its agent's
   ``last_seen`` stays fresh and the offline-agent beacon-lost sweep never
   fires for it.

R then reads ``running`` forever: never scored, never in a report, holding the
console's "in flight" count. ``rehydrate()``'s docstring said "its own
/complete terminalises it", which is exactly the message that was lost.

The signal that a beacon is executing a run is the run's own control channel:
the beacon polls ``GET /api/runs/{id}/control`` before every step and every 2 s
while one runs. A delivered ``running`` run with no control poll for
``CORTEXSIM_BEACON_LOST_AFTER_SECONDS`` (measured from ``max(boot,
delivered_at)``) whose agent is demonstrably alive has been abandoned; it is
terminalised through the beacon-lost machinery (prevention caps at
``pending``), with an explicit ``BEACON_ABANDONED_TASK`` marker and a
``run.status`` frame. And because the beacon now RETRIES ``/complete``, a
``/complete`` on a run that is already terminal is a 409 that re-applies
nothing.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

AFTER = 180


@pytest.fixture
def session_factory():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

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


@pytest.fixture
def frames(monkeypatch):
    from events import event_bus

    seen: list[dict] = []

    async def _publish(run_id, event):
        seen.append(event)

    monkeypatch.setattr(event_bus, "publish", _publish)
    return seen


def _seed(session_factory, *, prevent=False):
    """Scenario + agent + a launched, DELIVERED pull run. Returns run_id."""
    from engine.orchestrator import orchestrator
    from models import Agent, Scenario

    async def _go():
        async with session_factory() as db:
            db.add(Scenario(
                scenario_id="SIM-EDR-960", name="T", version="1.0", status="active",
                plane="EDR", uc_ref="UCS-EDR-01", uc_name="x", tc_ref="TC-EDR-01",
                tc_name="y", mitre_tactic="TA0006", mitre_tactic_name="CA",
                mitre_technique="T1003.001", mitre_technique_name="LSASS",
                execution_identity={"default": "root"}, push_supported=True,
                pull_supported=True,
                steps=[{"id": sid, "name": sid, "identity": "root", "command": "id",
                        "mitre_technique": "T1003.001",
                        "expected_detections": [{
                            "plane": "EDR",
                            "type": "Prevention" if prevent else "BIOC",
                            "description": f"det {sid}",
                            "outcome": "prevent" if prevent else "detect"}]}
                       for sid in ("step-01", "step-02")],
            ))
            db.add(Agent(agent_id="agent-1", hostname="h1", os="linux",
                         capabilities=["shell", "identity-harness"], interpreters=[],
                         registered_at=datetime.utcnow(), last_seen=datetime.utcnow(),
                         status="online"))
            await db.commit()
            res = await orchestrator.launch(
                scenario_id="SIM-EDR-960", mode="pull", db=db, target_agent_id="agent-1",
                agent_profile_mode="block" if prevent else "unknown")
            assert res.success, res.error
            task = await orchestrator.dequeue_for_agent("agent-1", db)
            assert task is not None and task.run_id == res.run_id
            return res.run_id

    return asyncio.run(_go())


def _restart(session_factory):
    """What the lifespan does: rehydrate the process singleton."""
    from engine.orchestrator import orchestrator

    async def _go():
        async with session_factory() as db:
            return await orchestrator.rehydrate(db)

    return asyncio.run(_go())


def _age(session_factory, run_id, seconds):
    """Move the run's delivery and the process boot ``seconds`` into the past,
    keeping the agent's last_seen fresh (it is idle-polling /tasks — which is
    also the idle evidence the sweep needs, recorded here for the tests that
    call the sweep directly rather than through GET /tasks)."""
    from engine.orchestrator import orchestrator
    from models import Agent, Run

    async def _go():
        async with session_factory() as db:
            run = (await db.execute(select(Run).where(Run.run_id == run_id))).scalar_one()
            run.delivered_at = datetime.utcnow() - timedelta(seconds=seconds)
            run.started_at = run.delivered_at
            agent = (await db.execute(select(Agent))).scalar_one()
            agent.last_seen = datetime.utcnow()
            await db.commit()

    asyncio.run(_go())
    orchestrator.started_at = datetime.utcnow() - timedelta(seconds=seconds)
    orchestrator.note_task_poll("agent-1")


def _sweep(session_factory, now=None):
    from engine.beacon_lost import terminalise_abandoned_runs

    async def _go():
        async with session_factory() as db:
            return await terminalise_abandoned_runs(
                db, now or datetime.utcnow(), after_seconds=AFTER)

    return asyncio.run(_go())


def _run(session_factory, run_id):
    from models import Run

    async def _go():
        async with session_factory() as db:
            return (await db.execute(select(Run).where(Run.run_id == run_id))).scalar_one()

    return asyncio.run(_go())


def _results(session_factory, run_id):
    from models import Result

    async def _go():
        async with session_factory() as db:
            return (await db.execute(
                select(Result).where(Result.run_id == run_id).order_by(Result.id)
            )).scalars().all()

    return asyncio.run(_go())


# ---------------------------------------------------------------------------
# The defect
# ---------------------------------------------------------------------------


def test_restart_then_silent_control_channel_terminalises_the_run(session_factory, frames):
    run_id = _seed(session_factory)
    stats = _restart(session_factory)
    assert stats["in_flight"] == 1 and stats["failed_orphans"] == 0
    _age(session_factory, run_id, AFTER + 60)

    assert _sweep(session_factory) == [run_id]

    run = _run(session_factory, run_id)
    assert run.status == "complete" and run.completed_at is not None
    assert run.severed_at_step == "step-01"
    assert "BEACON ABANDONED TASK" in run.output
    status_frames = [f for f in frames if f["type"] == "run.status"]
    assert status_frames and status_frames[-1]["data"]["status"] == "complete"
    assert status_frames[-1]["data"]["termination"] == "BEACON_ABANDONED_TASK"
    # Host evidence: the severed step witnessed nothing; later steps never ran.
    rows = {r.step_id: (r.outcome, r.outcome_reason) for r in _results(session_factory, run_id)}
    assert rows["step-02"] == ("not_reached", "UPSTREAM_SEVERED")


def test_the_idle_poll_itself_reaps_an_abandoned_run(client, session_factory, frames):
    """End to end through the route a live beacon actually hits: the idle
    beacon's next GET /tasks is the moment its run is known to be abandoned.
    On base the run stays `running` however long the beacon idles."""
    run_id = _seed(session_factory)
    _restart(session_factory)
    _age(session_factory, run_id, AFTER + 60)

    r = client.get("/api/agents/agent-1/tasks")
    assert r.status_code == 200 and r.json() == {"task": None}

    run = _run(session_factory, run_id)
    assert run.status == "complete", run.status
    assert "BEACON ABANDONED TASK" in run.output


def test_a_block_mode_prevent_row_caps_at_pending(session_factory, frames):
    run_id = _seed(session_factory, prevent=True)
    _restart(session_factory)
    _age(session_factory, run_id, AFTER + 60)
    assert _sweep(session_factory) == [run_id]
    first = _results(session_factory, run_id)[0]
    assert (first.outcome, first.outcome_reason) == ("pending", "PREVENTION_UNCONFIRMED_BEACON_LOST")
    assert first.kpi_verdict == "pending"


# ---------------------------------------------------------------------------
# What must NOT be reaped
# ---------------------------------------------------------------------------


def test_a_run_whose_control_channel_is_being_polled_is_left_running(client, session_factory):
    run_id = _seed(session_factory)
    _restart(session_factory)
    _age(session_factory, run_id, AFTER + 60)
    # The beacon is mid-step and polling /control every 2 s.
    assert client.get(f"/api/runs/{run_id}/control").json()["abort"] is False

    assert _sweep(session_factory) == []
    assert _run(session_factory, run_id).status == "running"


def test_silence_is_measured_from_boot_not_from_before_the_process_lived(session_factory):
    """Delivered long before the restart, but the process has only just booted:
    no control poll could have reached it yet, so it is not abandoned."""
    from engine.orchestrator import orchestrator

    run_id = _seed(session_factory)
    _age(session_factory, run_id, 3600)
    orchestrator.started_at = datetime.utcnow()  # just booted

    assert _sweep(session_factory) == []
    assert _run(session_factory, run_id).status == "running"


def test_a_stale_agent_is_left_to_the_offline_beacon_lost_path(session_factory):
    from models import Agent

    run_id = _seed(session_factory)
    _restart(session_factory)
    _age(session_factory, run_id, AFTER + 60)

    async def _stale():
        async with session_factory() as db:
            agent = (await db.execute(select(Agent))).scalar_one()
            agent.last_seen = datetime.utcnow() - timedelta(seconds=AFTER + 30)
            await db.commit()

    asyncio.run(_stale())
    assert _sweep(session_factory) == []


def test_an_undelivered_run_is_not_this_sweeps_business(session_factory):
    """Queued, never collected: rehydrate owns it (orphan) — not abandonment."""
    from engine.orchestrator import orchestrator
    from models import Agent, Run, Scenario

    async def _go():
        async with session_factory() as db:
            db.add(Scenario(
                scenario_id="SIM-EDR-961", name="T", version="1.0", status="active",
                plane="EDR", uc_ref="UCS-EDR-01", uc_name="x", tc_ref="TC-EDR-01",
                tc_name="y", mitre_tactic="TA0006", mitre_tactic_name="CA",
                mitre_technique="T1003.001", mitre_technique_name="LSASS",
                execution_identity={"default": "root"}, push_supported=True,
                pull_supported=True,
                steps=[{"id": "step-01", "name": "s", "identity": "root", "command": "id",
                        "expected_detections": []}]))
            db.add(Agent(agent_id="agent-1", hostname="h1", os="linux",
                         capabilities=["shell"], interpreters=[],
                         registered_at=datetime.utcnow(), last_seen=datetime.utcnow(),
                         status="online"))
            await db.commit()
            res = await orchestrator.launch(scenario_id="SIM-EDR-961", mode="pull", db=db,
                                            target_agent_id="agent-1")
            run = (await db.execute(select(Run))).scalar_one()
            run.started_at = datetime.utcnow() - timedelta(hours=1)
            await db.commit()
            return res.run_id

    run_id = asyncio.run(_go())
    orchestrator.started_at = datetime.utcnow() - timedelta(hours=1)
    assert _sweep(session_factory) == []
    assert _run(session_factory, run_id).status == "running"


def test_a_reaping_fault_never_breaks_task_delivery(client, session_factory, monkeypatch):
    """Reaping rides on the /tasks poll; if it raises, the poll must still hand
    the beacon its next task (the run stays running and the next poll retries)."""
    import engine.beacon_lost as bl
    from engine.orchestrator import orchestrator
    from models import Agent, Scenario

    async def _boom(*_a, **_kw):
        raise RuntimeError("reaper exploded")

    monkeypatch.setattr(bl, "terminalise_abandoned_runs", _boom)

    async def _go():
        async with session_factory() as db:
            db.add(Scenario(
                scenario_id="SIM-EDR-962", name="T", version="1.0", status="active",
                plane="EDR", uc_ref="UCS-EDR-01", uc_name="x", tc_ref="TC-EDR-01",
                tc_name="y", mitre_tactic="TA0006", mitre_tactic_name="CA",
                mitre_technique="T1003.001", mitre_technique_name="LSASS",
                execution_identity={"default": "root"}, push_supported=True,
                pull_supported=True,
                steps=[{"id": "step-01", "name": "s", "identity": "root", "command": "id",
                        "expected_detections": []}]))
            db.add(Agent(agent_id="agent-1", hostname="h1", os="linux",
                         capabilities=["shell"], interpreters=[],
                         registered_at=datetime.utcnow(), last_seen=datetime.utcnow(),
                         status="online"))
            await db.commit()
            res = await orchestrator.launch(scenario_id="SIM-EDR-962", mode="pull", db=db,
                                            target_agent_id="agent-1")
            return res.run_id

    run_id = asyncio.run(_go())
    r = client.get("/api/agents/agent-1/tasks")
    assert r.status_code == 200
    assert r.json()["task"]["run_id"] == run_id


# ---------------------------------------------------------------------------
# /complete on a terminal run: 409, nothing re-applied
# ---------------------------------------------------------------------------


def test_complete_after_abandonment_is_409_and_reapplies_nothing(client, session_factory, frames):
    run_id = _seed(session_factory)
    _restart(session_factory)
    _age(session_factory, run_id, AFTER + 60)
    assert _sweep(session_factory) == [run_id]
    before = _run(session_factory, run_id)
    frames.clear()

    # The beacon's retried /complete finally lands.
    r = client.post(f"/api/runs/{run_id}/complete",
                    json={"exit_code": 0, "summary": "late retry",
                          "steps": [{"step_id": "step-01", "termination": "exited",
                                     "exit_code": 0}]})
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "RUN_ALREADY_TERMINAL"
    assert detail["detail"]["status"] == "complete"

    after = _run(session_factory, run_id)
    assert (after.status, after.completed_at, after.output) == (
        before.status, before.completed_at, before.output)
    assert "late retry" not in after.output
    assert frames == []


def test_a_duplicate_complete_is_409(client, session_factory):
    run_id = _seed(session_factory)
    first = client.post(f"/api/runs/{run_id}/complete", json={"exit_code": 0, "summary": "ok"})
    assert first.status_code == 200 and first.json()["status"] == "complete"
    second = client.post(f"/api/runs/{run_id}/complete", json={"exit_code": 1, "summary": "dup"})
    assert second.status_code == 409
    assert _run(session_factory, run_id).status == "complete"


# ---------------------------------------------------------------------------
# A multi-endpoint fan-out is abandoned only when EVERY endpoint has let go
# ---------------------------------------------------------------------------
#
# `delivered_at` is stamped by the FIRST partition's delivery and `Run.target`
# is only the launch target. Reaping on "the launch target is idle and the
# control channel is silent" therefore ended a fan-out whose first endpoint had
# finished while its second was still queued (busy with another run, or
# offline), or had collected its task and was staging a large artifact — and
# wrote "/complete most likely lost across a SimCore restart" into the report
# of a run that was still working.


def _seed_fanout(session_factory):
    """Two linux agents; step-01 runs on agent-1 (launch target), step-02 on agent-2."""
    from engine.orchestrator import orchestrator
    from models import Agent, Scenario

    async def _go():
        async with session_factory() as db:
            db.add(Scenario(
                scenario_id="SIM-EDR-963", name="T", version="1.0", status="active",
                plane="EDR", uc_ref="UCS-EDR-01", uc_name="x", tc_ref="TC-EDR-01",
                tc_name="y", mitre_tactic="TA0006", mitre_tactic_name="CA",
                mitre_technique="T1003.001", mitre_technique_name="LSASS",
                execution_identity={"default": "root"}, push_supported=True,
                pull_supported=True,
                steps=[
                    {"id": "step-01", "name": "s1", "identity": "root", "command": "id",
                     "mitre_technique": "T1003.001",
                     "expected_detections": [{"plane": "EDR", "type": "BIOC",
                                              "description": "det 1", "outcome": "detect"}]},
                    {"id": "step-02", "name": "s2", "identity": "root", "command": "id",
                     "target": "agent-2", "mitre_technique": "T1003.001",
                     "expected_detections": [{"plane": "EDR", "type": "BIOC",
                                              "description": "det 2", "outcome": "detect"}]},
                ],
            ))
            for aid in ("agent-1", "agent-2"):
                db.add(Agent(agent_id=aid, hostname=f"h-{aid}", os="linux",
                             capabilities=["shell", "identity-harness"], interpreters=[],
                             registered_at=datetime.utcnow(), last_seen=datetime.utcnow(),
                             status="online"))
            await db.commit()
            res = await orchestrator.launch(
                scenario_id="SIM-EDR-963", mode="pull", db=db, target_agent_id="agent-1")
            assert res.success, res.error
            return res.run_id

    return asyncio.run(_go())


def _collect(client, agent_id, run_id):
    r = client.get(f"/api/agents/{agent_id}/tasks")
    assert r.status_code == 200, r.text
    task = r.json()["task"]
    assert task is not None and task["run_id"] == run_id, r.json()
    return task


def _age_fanout(session_factory, run_id, seconds):
    """Delivery, process boot and every control poll ``seconds`` in the past;
    both agents' last_seen fresh."""
    from engine.orchestrator import orchestrator
    from models import Agent, Run

    async def _go():
        async with session_factory() as db:
            run = (await db.execute(select(Run).where(Run.run_id == run_id))).scalar_one()
            run.delivered_at = datetime.utcnow() - timedelta(seconds=seconds)
            run.started_at = run.delivered_at
            for agent in (await db.execute(select(Agent))).scalars().all():
                agent.last_seen = datetime.utcnow()
            await db.commit()

    asyncio.run(_go())
    orchestrator.started_at = datetime.utcnow() - timedelta(seconds=seconds)
    orchestrator._control_polls.clear()


def _complete_partition(client, run_id, step_id):
    r = client.post(f"/api/runs/{run_id}/complete",
                    json={"exit_code": 0, "summary": f"{step_id} ok",
                          "steps": [{"step_id": step_id, "termination": "exited",
                                     "exit_code": 0}]})
    assert r.status_code == 200, r.text
    return r.json()


def test_a_fanout_whose_second_task_is_still_queued_is_not_reaped(client, session_factory):
    """The reviewed defect, reproduced at 0452666: agent-1 finishes its
    partition; agent-2's task is still QUEUED (agent-2 busy elsewhere). 180 s
    later agent-1's idle poll reaped the run, and agent-2 was then told abort."""
    run_id = _seed_fanout(session_factory)
    _collect(client, "agent-1", run_id)
    assert _complete_partition(client, run_id, "step-01")["pending_endpoints"] == 1
    _age_fanout(session_factory, run_id, AFTER + 60)

    r = client.get("/api/agents/agent-1/tasks")  # agent-1 idle-polls for new work
    assert r.status_code == 200 and r.json() == {"task": None}

    run = _run(session_factory, run_id)
    assert run.status == "running", run.output
    assert "BEACON ABANDONED TASK" not in (run.output or "")
    # agent-2 finally collects its partition and is NOT told to stop.
    _collect(client, "agent-2", run_id)
    assert client.get(f"/api/runs/{run_id}/control").json()["abort"] is False
    assert _complete_partition(client, run_id, "step-02")["status"] == "complete"


def test_a_fanout_whose_second_endpoint_is_staging_is_not_reaped(client, session_factory):
    """agent-2 collected its task and is staging a large artifact: it polls
    neither /control nor /tasks. agent-1 finished and idles. Not abandoned."""
    run_id = _seed_fanout(session_factory)
    _collect(client, "agent-1", run_id)
    _collect(client, "agent-2", run_id)
    _complete_partition(client, run_id, "step-01")
    _age_fanout(session_factory, run_id, AFTER + 60)

    assert client.get("/api/agents/agent-1/tasks").json() == {"task": None}
    assert _sweep(session_factory) == []  # the unscoped sweep agrees

    run = _run(session_factory, run_id)
    assert run.status == "running", run.output
    assert client.get(f"/api/runs/{run_id}/control").json()["abort"] is False


def test_a_fanout_both_endpoints_let_go_is_reaped_once_both_are_seen_idle(
        client, session_factory, frames):
    """The positive control: both partitions' /complete lost across a restart.
    Reaped only once BOTH endpoints have polled for new work since boot — and
    the second endpoint's own idle poll can be the one that reaps it."""
    run_id = _seed_fanout(session_factory)
    _collect(client, "agent-1", run_id)
    _collect(client, "agent-2", run_id)
    _restart(session_factory)
    _age_fanout(session_factory, run_id, AFTER + 60)

    # Only agent-1 has been seen idle since boot: agent-2 may still be working.
    assert client.get("/api/agents/agent-1/tasks").json() == {"task": None}
    assert _run(session_factory, run_id).status == "running"

    # agent-2 polls for new work too: nothing holds the run any more.
    assert client.get("/api/agents/agent-2/tasks").json() == {"task": None}
    run = _run(session_factory, run_id)
    assert run.status == "complete", run.output
    assert "BEACON ABANDONED TASK" in run.output
    status_frames = [f for f in frames if f["type"] == "run.status"]
    assert status_frames[-1]["data"]["termination"] == "BEACON_ABANDONED_TASK"


def test_the_abandoned_marker_does_not_name_a_cause_it_cannot_know():
    """The reaper fires on a SimCore restart, a beacon restart, or a /complete
    lost on the wire alike; the report must not assert which."""
    from engine.beacon_lost import _abandoned_marker

    text = _abandoned_marker(240)
    assert "most likely" not in text
    assert "SimCore or beacon restart" in text and "not known" in text

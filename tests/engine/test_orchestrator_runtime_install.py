"""Runtime-dependency posture on the launch path (docs/design/agent-runtime-dependencies.md).

Two things this proves against a real (in-memory) DB, not just by reading the
code:

1. The two-key gate. `allow_runtime_install` on the launch request does
   NOTHING by itself — CORTEXSIM_AGENT_ALLOW_RUNTIME_INSTALL must also be set
   on the deployment. Exactly CORTEXSIM_XSIAM_ALLOW_WRITE's posture, and for
   the same reason: a single mis-set request body must never be able to
   authorize a target mutation on its own.
2. It is RECORDED — not just threaded through in memory. `Run.runtime_install_authorized`
   is queryable after the fact, and the durable `queued_tasks` payload (what
   actually reaches the beacon, and what survives a SimCore restart) carries
   it too.

Also covers the advisory preflight: `Run.runtime_dependency_gaps` is populated
from `engine.runtime_preflight.evaluate_runtime_readiness` against the target
agent's advertised interpreter roster.
"""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


@pytest.fixture
def session_factory() -> async_sessionmaker:
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


async def _seed_scenario(db, scenario_id="SIM-EDR-001", *, requires_interpreters=None):
    from models import Scenario

    step = {"id": "step-05", "name": "s", "identity": "root", "command": "cmd",
            "expected_detections": []}
    if requires_interpreters:
        step["requires_interpreters"] = requires_interpreters

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
        execution_identity={"default": "root"},
        push_supported=True, pull_supported=True,
        steps=[step],
    )
    db.add(s)
    await db.commit()
    return s


async def _seed_agent(db, agent_id="agent-1", interpreters=None):
    from models import Agent
    from datetime import datetime

    a = Agent(
        agent_id=agent_id, hostname="host-1", os="linux",
        capabilities=["shell", "identity-harness"],
        interpreters=interpreters or [],
        registered_at=datetime.utcnow(), last_seen=datetime.utcnow(),
        status="online",
    )
    db.add(a)
    await db.commit()
    return a


# ---------------------------------------------------------------------------
# The two-key gate
# ---------------------------------------------------------------------------


def test_default_launch_is_not_runtime_install_authorized(session_factory, monkeypatch):
    from config import settings
    from models import Run

    monkeypatch.setattr(settings, "CORTEXSIM_AGENT_ALLOW_RUNTIME_INSTALL", False)

    async def _run():
        orch = _fresh_orchestrator()
        async with session_factory() as db:
            await _seed_scenario(db)
            result = await orch.launch(
                scenario_id="SIM-EDR-001", mode="pull", db=db,
                target_agent_id="agent-1",
            )
            assert result.success
            run = (await db.execute(select(Run).where(Run.run_id == result.run_id))).scalar_one()
            assert run.runtime_install_authorized is False

    asyncio.run(_run())


def test_request_flag_alone_does_not_authorize_without_deployment_flag(session_factory, monkeypatch):
    """The exact scenario the two-key design exists to prevent: an operator
    (or a compromised/buggy caller) sets the per-run flag on a deployment that
    has not opted in. It must still record False."""
    from config import settings
    from models import Run

    monkeypatch.setattr(settings, "CORTEXSIM_AGENT_ALLOW_RUNTIME_INSTALL", False)

    async def _run():
        orch = _fresh_orchestrator()
        async with session_factory() as db:
            await _seed_scenario(db)
            result = await orch.launch(
                scenario_id="SIM-EDR-001", mode="pull", db=db,
                target_agent_id="agent-1",
                allow_runtime_install=True,
            )
            assert result.success
            run = (await db.execute(select(Run).where(Run.run_id == result.run_id))).scalar_one()
            assert run.runtime_install_authorized is False, (
                "the per-run flag must NOT authorize anything without "
                "CORTEXSIM_AGENT_ALLOW_RUNTIME_INSTALL also being set"
            )

    asyncio.run(_run())


def test_both_keys_set_authorizes_and_is_recorded_and_reaches_the_wire(session_factory, monkeypatch):
    from config import settings
    from models import QueuedTask, Run

    monkeypatch.setattr(settings, "CORTEXSIM_AGENT_ALLOW_RUNTIME_INSTALL", True)

    async def _run():
        orch = _fresh_orchestrator()
        async with session_factory() as db:
            await _seed_scenario(db)
            result = await orch.launch(
                scenario_id="SIM-EDR-001", mode="pull", db=db,
                target_agent_id="agent-1",
                allow_runtime_install=True,
            )
            assert result.success

            run = (await db.execute(select(Run).where(Run.run_id == result.run_id))).scalar_one()
            assert run.runtime_install_authorized is True

            # It is not just recorded — it reaches the durable payload the
            # beacon actually receives (queued_tasks.payload is the exact
            # dict PollTasks decodes), and survives a restart via
            # _task_from_payload -> rehydrate().
            rows = (await db.execute(select(QueuedTask))).scalars().all()
            assert len(rows) == 1
            assert rows[0].payload.get("runtime_install_authorized") is True

    asyncio.run(_run())


def test_runtime_install_authorized_omitted_from_wire_when_false(session_factory, monkeypatch):
    """Byte-identical-when-unused guarantee, same idiom as `artifacts`."""
    from config import settings
    from models import QueuedTask

    monkeypatch.setattr(settings, "CORTEXSIM_AGENT_ALLOW_RUNTIME_INSTALL", False)

    async def _run():
        orch = _fresh_orchestrator()
        async with session_factory() as db:
            await _seed_scenario(db)
            result = await orch.launch(
                scenario_id="SIM-EDR-001", mode="pull", db=db,
                target_agent_id="agent-1",
            )
            assert result.success
            rows = (await db.execute(select(QueuedTask))).scalars().all()
            assert "runtime_install_authorized" not in rows[0].payload

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Advisory preflight
# ---------------------------------------------------------------------------


def test_preflight_gap_recorded_when_agent_lacks_the_interpreter(session_factory):
    from models import Run

    async def _run():
        orch = _fresh_orchestrator()
        async with session_factory() as db:
            await _seed_scenario(db, requires_interpreters=["python"])
            await _seed_agent(db, interpreters=[])  # no python
            result = await orch.launch(
                scenario_id="SIM-EDR-001", mode="pull", db=db,
                target_agent_id="agent-1",
            )
            assert result.success
            run = (await db.execute(select(Run).where(Run.run_id == result.run_id))).scalar_one()
            assert run.runtime_dependency_gaps == [{"step_id": "step-05", "missing": ["python"]}]

    asyncio.run(_run())


def test_preflight_reports_clean_not_none_when_agent_has_the_interpreter(session_factory):
    from models import Run

    async def _run():
        orch = _fresh_orchestrator()
        async with session_factory() as db:
            await _seed_scenario(db, requires_interpreters=["python"])
            await _seed_agent(db, interpreters=["python"])
            result = await orch.launch(
                scenario_id="SIM-EDR-001", mode="pull", db=db,
                target_agent_id="agent-1",
            )
            assert result.success
            run = (await db.execute(select(Run).where(Run.run_id == result.run_id))).scalar_one()
            # [] ("checked, clean") is distinct from None ("never checked") —
            # see runtime_preflight's own docstring on this distinction.
            assert run.runtime_dependency_gaps == []

    asyncio.run(_run())


def test_preflight_is_none_when_target_agent_was_never_seen(session_factory):
    """No Agent row exists for the target — the same shape
    test_orchestrator_queue.py's tests launch against. Must not crash, and
    must record None (not checked) rather than fabricating a clean report."""
    from models import Run

    async def _run():
        orch = _fresh_orchestrator()
        async with session_factory() as db:
            await _seed_scenario(db, requires_interpreters=["python"])
            result = await orch.launch(
                scenario_id="SIM-EDR-001", mode="pull", db=db,
                target_agent_id="never-registered-agent",
            )
            assert result.success
            run = (await db.execute(select(Run).where(Run.run_id == result.run_id))).scalar_one()
            assert run.runtime_dependency_gaps is None

    asyncio.run(_run())

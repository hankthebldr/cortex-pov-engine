"""Orchestrator channel-dispatch skeleton — Phase 3a (§8.1(3)).

The composer lets a step declare a CHANNEL. In Phase 3a the orchestrator learns
to route them, but only as a skeleton:

  * A run whose every step is a plain agent-channel step with no per-step target
    — the whole shipped corpus and every Phase-1/2 draft — is dispatched by the
    ORIGINAL single-Task path, and ``Run.channel_dispatch`` stays NULL. This is
    the load-bearing byte-identity guarantee, proven here against a real DB.
  * An agent step may carry a ``target`` naming a SECOND enrolled beacon; that
    step's task is enqueued to that agent. A target that is not enrolled refuses
    the whole launch (TARGET_AGENT_NOT_ENROLLED), all-or-nothing — never a
    silent no-op or a task nothing will collect.
  * An ``eal`` step is dispatched IN-PROCESS at launch (Phase 3b): the
    orchestrator builds a Campaign and runs it through ``CampaignExecutor`` in
    dry_run (records pre-rendered, nothing POSTed), injecting the shared identity
    principal via the binding->params adapter, seeding the step's detections with
    a real executed_at, and recording the real dispatch outcome (dry_run /
    dispatched / not_delivered / error) on ``Run.channel_dispatch`` — never a
    fabricated ingest. An all-EAL run terminalises at launch (no beacon task).

Every channel shares the ONE stitch binding the orchestrator resolves once
(seed=run_id), exactly as Phase 2 does for the single-channel run.
"""
from __future__ import annotations

import asyncio
from datetime import datetime

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


async def _seed_scenario(db, steps, scenario_id="SIM-EDR-001"):
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
        execution_identity={"default": "root"},
        push_supported=True, pull_supported=True,
        steps=steps,
    )
    db.add(s)
    await db.commit()
    return s


async def _seed_agent(db, agent_id="agent-1"):
    from models import Agent

    a = Agent(
        agent_id=agent_id, hostname=f"host-{agent_id}", os="linux",
        capabilities=["shell", "identity-harness"], interpreters=[],
        registered_at=datetime.utcnow(), last_seen=datetime.utcnow(),
        status="online",
    )
    db.add(a)
    await db.commit()
    return a


def _agent_step(step_id, *, target=None):
    step = {"id": step_id, "name": step_id, "identity": "root",
            "command": f"echo {step_id}", "mitre_technique": "T1003",
            "expected_detections": [{"type": "BIOC", "description": step_id}]}
    if target is not None:
        step["target"] = target
    return step


def _eal_step(step_id, *, plugin="ngfw_eal_emitter", params=None):
    # The analytics emitters require a collector_url even for a dry-run dispatch
    # (params validate regardless of dry_run), so give one by default.
    return {"id": step_id, "name": step_id, "identity": "root",
            "channel": "eal",
            "eal": {"plugin": plugin,
                    "params": params if params is not None
                    else {"collector_url": "https://collector.lab.invalid/eal"}},
            "expected_detections": [{"type": "Analytics", "description": step_id}]}


# ---------------------------------------------------------------------------
# 1. A no-channel run is byte-identical to today
# ---------------------------------------------------------------------------


def test_no_channel_run_is_single_task_and_channel_dispatch_is_null(session_factory):
    """The whole shipped corpus takes this path: one Task to the launch target,
    Run.channel_dispatch NULL, and the enqueued step dict grows NO channel keys."""
    from models import QueuedTask, Run

    async def _run():
        orch = _fresh_orchestrator()
        async with session_factory() as db:
            await _seed_scenario(db, steps=[_agent_step("step-01"), _agent_step("step-02")])
            result = await orch.launch(
                scenario_id="SIM-EDR-001", mode="pull", db=db,
                target_agent_id="agent-1",
            )
            assert result.success
            assert result.message == "Task queued for agent 'agent-1'"

            run = (await db.execute(select(Run).where(Run.run_id == result.run_id))).scalar_one()
            assert run.channel_dispatch is None
            assert run.status == "running"

            # Exactly one durable task, to the launch target, carrying both steps
            # with none of the three channel keys added.
            rows = (await db.execute(select(QueuedTask))).scalars().all()
            assert len(rows) == 1
            assert rows[0].agent_id == "agent-1"
            wire_steps = rows[0].payload["steps"]
            assert [s["id"] for s in wire_steps] == ["step-01", "step-02"]
            for s in wire_steps:
                assert "channel" not in s and "target" not in s and "eal" not in s

            # In-memory queue matches: one task for agent-1, none elsewhere.
            assert len(orch.peek_queue("agent-1")) == 1

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 2. A per-step target enqueues to the second endpoint
# ---------------------------------------------------------------------------


def test_per_step_target_enqueues_to_that_agent(session_factory):
    from models import QueuedTask, Run

    async def _run():
        orch = _fresh_orchestrator()
        async with session_factory() as db:
            await _seed_agent(db, "agent-1")
            await _seed_agent(db, "agent-2")
            await _seed_scenario(db, steps=[
                _agent_step("step-01"),                       # -> launch target
                _agent_step("step-02", target="agent-2"),     # -> second endpoint
            ])
            result = await orch.launch(
                scenario_id="SIM-EDR-001", mode="pull", db=db,
                target_agent_id="agent-1",
            )
            assert result.success

            # Two tasks: step-01 to agent-1, step-02 to agent-2.
            rows = {r.agent_id: r for r in
                    (await db.execute(select(QueuedTask))).scalars().all()}
            assert set(rows) == {"agent-1", "agent-2"}
            assert [s["id"] for s in rows["agent-1"].payload["steps"]] == ["step-01"]
            assert [s["id"] for s in rows["agent-2"].payload["steps"]] == ["step-02"]

            assert len(orch.peek_queue("agent-1")) == 1
            assert len(orch.peek_queue("agent-2")) == 1

            run = (await db.execute(select(Run).where(Run.run_id == result.run_id))).scalar_one()
            assert run.status == "running"
            assert run.channel_dispatch == [
                {"step_id": "step-01", "channel": "agent", "target": "agent-1",
                 "status": "enqueued", "code": None},
                {"step_id": "step-02", "channel": "agent", "target": "agent-2",
                 "status": "enqueued", "code": None},
            ]

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 3. A missing target agent refuses the whole launch, all-or-nothing
# ---------------------------------------------------------------------------


def test_missing_target_agent_refuses_all_or_nothing(session_factory):
    from models import QueuedTask, Run

    async def _run():
        orch = _fresh_orchestrator()
        async with session_factory() as db:
            await _seed_agent(db, "agent-1")   # launch target enrolled
            # agent-2 deliberately NOT enrolled
            await _seed_scenario(db, steps=[
                _agent_step("step-01"),
                _agent_step("step-02", target="agent-2"),
            ])
            result = await orch.launch(
                scenario_id="SIM-EDR-001", mode="pull", db=db,
                target_agent_id="agent-1",
            )
            assert result.success is False
            assert result.error_code == "TARGET_AGENT_NOT_ENROLLED"
            assert result.error_detail["missing_agents"] == ["agent-2"]
            assert result.error_detail["referencing_steps"] == {"agent-2": ["step-02"]}

            # All-or-nothing: NOTHING enqueued, not even the valid step-01 partition.
            assert (await db.execute(select(QueuedTask))).scalars().all() == []
            assert orch.peek_queue("agent-1") == []
            assert orch.peek_queue("agent-2") == []

            # The seeded run is terminalised (not left orphaned at 'pending'):
            # a refused launch can never advance, so it reaches a terminal state
            # with the refusal as its reason.
            run = (await db.execute(select(Run).where(Run.run_id == result.run_id))).scalar_one()
            assert run.status == "failed"
            assert run.completed_at is not None
            assert run.channel_dispatch is None

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 4. An eal step is EAL_DISPATCH_PENDING and runs no campaign
# ---------------------------------------------------------------------------


def test_eal_step_dispatches_in_process_dry_run(session_factory):
    """Phase 3b: an eal step is dispatched IN-PROCESS (dry_run) at launch — not
    parked at EAL_DISPATCH_PENDING. Only the agent step is on a beacon task; the
    eal step's detections ARE seeded (a real dispatch) and its ledger entry
    carries the real dry_run outcome. No EalCampaignRun row (in-process, not the
    campaign API)."""
    from models import QueuedTask, Result, Run

    async def _run():
        orch = _fresh_orchestrator()
        async with session_factory() as db:
            await _seed_agent(db, "agent-1")
            await _seed_scenario(db, steps=[
                _agent_step("step-01"),
                _eal_step("step-02", plugin="ngfw_eal_emitter"),
            ])
            result = await orch.launch(
                scenario_id="SIM-EDR-001", mode="pull", db=db,
                target_agent_id="agent-1",
            )
            assert result.success
            assert "dispatched in-process" in result.message

            # The eal step is NOT on any beacon task — only the agent step is.
            rows = (await db.execute(select(QueuedTask))).scalars().all()
            assert len(rows) == 1
            assert rows[0].agent_id == "agent-1"
            assert [s["id"] for s in rows[0].payload["steps"]] == ["step-01"]

            run = (await db.execute(select(Run).where(Run.run_id == result.run_id))).scalar_one()
            ledger = run.channel_dispatch
            assert ledger[0] == {"step_id": "step-01", "channel": "agent",
                                 "target": "agent-1", "status": "enqueued", "code": None}
            eal_entry = ledger[1]
            assert eal_entry["step_id"] == "step-02" and eal_entry["channel"] == "eal"
            assert eal_entry["plugin"] == "ngfw_eal_emitter"
            assert eal_entry["status"] == "dry_run"        # real dispatch outcome, not pending
            assert eal_entry["code"] != "EAL_DISPATCH_PENDING"

            # The eal step's detection IS seeded now — it actually ran (dry_run).
            eal_results = (await db.execute(
                select(Result).where(Result.step_id == "step-02")
            )).scalars().all()
            assert len(eal_results) == 1
            assert eal_results[0].observed is False
            assert eal_results[0].executed_at is not None

            # In-process dispatch does NOT persist an EalCampaignRun (that is the
            # campaign API path, not this one).
            from models import EalCampaignRun
            assert (await db.execute(select(EalCampaignRun))).scalars().all() == []

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 5. channel_dispatch is exposed on the run record (report / Run lens read it)
# ---------------------------------------------------------------------------


def test_channel_dispatch_is_in_run_to_dict(session_factory):
    from models import Run

    async def _run():
        orch = _fresh_orchestrator()
        async with session_factory() as db:
            await _seed_agent(db, "agent-1")
            await _seed_scenario(db, steps=[
                _agent_step("step-01"),
                _eal_step("step-02"),
            ])
            result = await orch.launch(
                scenario_id="SIM-EDR-001", mode="pull", db=db,
                target_agent_id="agent-1",
            )
            run = (await db.execute(select(Run).where(Run.run_id == result.run_id))).scalar_one()
            d = run.to_dict()
            assert "channel_dispatch" in d
            assert d["channel_dispatch"][1]["channel"] == "eal"
            assert d["channel_dispatch"][1]["status"] == "dry_run"

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Pure helpers — partition + ledger (no DB)
# ---------------------------------------------------------------------------


def test_partition_channels_groups_agents_and_sets_aside_eal():
    from engine.orchestrator import _partition_channels

    steps = [
        _agent_step("a"),                       # -> launch
        _agent_step("b", target="host-b"),      # -> host-b
        _eal_step("c"),                         # set aside
        _agent_step("d", target="host-b"),      # -> host-b (same partition)
    ]
    partitions, eal = _partition_channels(steps, "launch")
    assert list(partitions) == ["launch", "host-b"]
    assert [s["id"] for s in partitions["launch"]] == ["a"]
    assert [s["id"] for s in partitions["host-b"]] == ["b", "d"]
    assert [s["id"] for s in eal] == ["c"]


def test_build_channel_dispatch_preserves_step_order_and_markers():
    from engine.orchestrator import _build_channel_dispatch

    steps = [_agent_step("a"), _eal_step("b", plugin="okta_sso"),
             _agent_step("c", target="host-c")]
    ledger = _build_channel_dispatch(steps, "launch")
    assert [e["step_id"] for e in ledger] == ["a", "b", "c"]
    assert ledger[0] == {"step_id": "a", "channel": "agent", "target": "launch",
                         "status": "enqueued", "code": None}
    assert ledger[1]["channel"] == "eal" and ledger[1]["plugin"] == "okta_sso"
    assert ledger[1]["code"] == "EAL_DISPATCH_PENDING" and ledger[1]["target"] is None
    assert ledger[2]["target"] == "host-c"


# ---------------------------------------------------------------------------
# Review must-fixes: all-EAL refusal + multi-endpoint completion
# ---------------------------------------------------------------------------


def test_all_eal_run_dispatches_in_process_and_terminalises(session_factory):
    """Phase 3b: an all-EAL run IS dispatchable — its EAL steps run in-process
    (dry_run) at launch and the run terminalises 'complete' immediately (no
    beacon task to wait on, open_tasks=0). Detections are seeded; nothing hangs
    at 'running'."""
    from models import Run, QueuedTask, Result

    async def _run():
        orch = _fresh_orchestrator()
        async with session_factory() as db:
            await _seed_agent(db, "agent-1")
            await _seed_scenario(db, steps=[
                _eal_step("step-01", plugin="ngfw_eal_emitter"),
                _eal_step("step-02", plugin="ngfw_eal_emitter"),
            ])
            result = await orch.launch(
                scenario_id="SIM-EDR-001", mode="pull", db=db, target_agent_id="agent-1",
            )
            assert result.success is True
            # No beacon task — EAL ran in-process.
            assert (await db.execute(select(QueuedTask))).scalars().all() == []
            run = (await db.execute(
                select(Run).where(Run.run_id == result.run_id)
            )).scalar_one()
            assert run.status == "complete"
            assert run.completed_at is not None
            assert run.open_tasks == 0
            # Both eal steps' detections seeded.
            assert len((await db.execute(select(Result))).scalars().all()) == 2

    asyncio.run(_run())


def test_multi_endpoint_run_completes_only_after_the_last_endpoint(session_factory):
    """Two target endpoints => two beacon tasks under one run_id. The run stays
    'running' when the first endpoint reports and terminalises only when the
    last does — the old complete_run flipped the whole run on the first."""
    from models import Run
    from api.runs import complete_run, CompleteRequest

    async def _run():
        orch = _fresh_orchestrator()
        async with session_factory() as db:
            await _seed_agent(db, "agent-1")
            await _seed_agent(db, "agent-2")
            await _seed_scenario(db, steps=[
                _agent_step("step-01"),                     # -> primary agent-1
                _agent_step("step-02", target="agent-2"),   # -> second endpoint
            ])
            result = await orch.launch(
                scenario_id="SIM-EDR-001", mode="pull", db=db, target_agent_id="agent-1",
            )
            assert result.success
            run = (await db.execute(
                select(Run).where(Run.run_id == result.run_id)
            )).scalar_one()
            assert run.open_tasks == 2
            assert run.status == "running"

            # First endpoint reports — the run must NOT terminalise.
            out1 = await complete_run(result.run_id, CompleteRequest(exit_code=0, summary="a"), db)
            assert out1["status"] == "running"
            run = (await db.execute(
                select(Run).where(Run.run_id == result.run_id)
            )).scalar_one()
            assert run.open_tasks == 1
            assert run.completed_at is None

            # Second (last) endpoint reports — NOW it completes.
            out2 = await complete_run(result.run_id, CompleteRequest(exit_code=0, summary="b"), db)
            assert out2["status"] == "complete"
            run = (await db.execute(
                select(Run).where(Run.run_id == result.run_id)
            )).scalar_one()
            assert run.open_tasks == 0
            assert run.completed_at is not None


    asyncio.run(_run())


def test_multi_endpoint_run_fails_if_any_endpoint_fails(session_factory):
    """Sticky failure: one endpoint's non-zero exit makes the whole run fail,
    even if the other endpoint succeeds, and only after the last reports."""
    from models import Run
    from api.runs import complete_run, CompleteRequest

    async def _run():
        orch = _fresh_orchestrator()
        async with session_factory() as db:
            await _seed_agent(db, "agent-1")
            await _seed_agent(db, "agent-2")
            await _seed_scenario(db, steps=[
                _agent_step("step-01"),
                _agent_step("step-02", target="agent-2"),
            ])
            result = await orch.launch(
                scenario_id="SIM-EDR-001", mode="pull", db=db, target_agent_id="agent-1",
            )
            # first endpoint fails
            await complete_run(result.run_id, CompleteRequest(exit_code=1, summary="boom"), db)
            run = (await db.execute(
                select(Run).where(Run.run_id == result.run_id)
            )).scalar_one()
            assert run.status == "failed"
            assert run.completed_at is None       # not terminal yet — one endpoint left
            # second endpoint succeeds — run is still failed, now terminal
            out = await complete_run(result.run_id, CompleteRequest(exit_code=0, summary="ok"), db)
            assert out["status"] == "failed"
            run = (await db.execute(
                select(Run).where(Run.run_id == result.run_id)
            )).scalar_one()
            assert run.completed_at is not None

    asyncio.run(_run())


def test_eal_step_with_unknown_plugin_reports_error_not_fabrication(session_factory):
    """A misconfigured eal plugin must surface an honest error in the ledger,
    never a fabricated dispatch. The agent step still runs; the run proceeds."""
    from models import Run, QueuedTask

    async def _run():
        orch = _fresh_orchestrator()
        async with session_factory() as db:
            await _seed_agent(db, "agent-1")
            await _seed_scenario(db, steps=[
                _agent_step("step-01"),
                _eal_step("step-02", plugin="no_such_plugin"),
            ])
            result = await orch.launch(
                scenario_id="SIM-EDR-001", mode="pull", db=db, target_agent_id="agent-1",
            )
            assert result.success
            # Agent step still enqueued to its beacon.
            assert len((await db.execute(select(QueuedTask))).scalars().all()) == 1
            run = (await db.execute(
                select(Run).where(Run.run_id == result.run_id)
            )).scalar_one()
            eal_entry = run.channel_dispatch[1]
            assert eal_entry["status"] == "error"
            assert eal_entry["code"] == "PLUGIN_MISCONFIGURED"

    asyncio.run(_run())

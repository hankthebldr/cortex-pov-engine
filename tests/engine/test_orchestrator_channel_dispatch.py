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
    dry_run (nothing delivered), injecting the shared identity
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
            await _seed_agent(db, "agent-1")
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


# ---------------------------------------------------------------------------
# Collector targets: the in-process eal dispatch takes its destination from
# the instance's targets, like every other in-process consumer.
# ---------------------------------------------------------------------------


def test_eal_step_without_a_collector_url_uses_the_instance_target(session_factory):
    """A Composer eal step may leave collector_url to the instance's collector
    targets. Without the resolver its params fail validation and the step is
    reported as an error instead of dispatching."""
    from models import CollectorTargetConfig, Run

    async def _run():
        orch = _fresh_orchestrator()
        async with session_factory() as db:
            db.add(CollectorTargetConfig(
                name="ngfw-hec", kind="xsiam_http_collector",
                url="https://api-acme.example.test/logs/v1/event",
                auth_header="Authorization", auth_scheme="",
                plugins=["ngfw_eal_emitter"], is_default=False,
            ))
            await db.commit()
            await _seed_agent(db, "agent-1")
            await _seed_scenario(db, steps=[
                _eal_step("step-01", plugin="ngfw_eal_emitter", params={}),
            ])
            result = await orch.launch(scenario_id="SIM-EDR-001", mode="pull", db=db,
                                       target_agent_id="agent-1")
            assert result.success, result.message
            run = (await db.execute(select(Run).where(Run.run_id == result.run_id))).scalar_one()
            entry = run.channel_dispatch[0]
            assert entry["status"] == "dry_run", entry
            assert entry["collector_target"] == "ngfw-hec"

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Live delivery for in-process eal steps. A scenario run's eal steps POST to a
# customer collector only behind launch consent, a named operator, and a keyed,
# healthy collector target for every eal step. Every dry-run says why.
# ---------------------------------------------------------------------------

FAKE_KEY = "fake-collector-key-for-tests-0000"
TARGET_URL = "https://api-acme.example.test/logs/v1/event"
LIVE_CONSENT = {"simulation_authorized": True}


class _RecordingClient:
    """Stub ``httpx.AsyncClient`` capturing every POST the plugin makes
    (copied from tests/eal_simulator/test_plugin_m365_activity_emitter.py)."""

    def __init__(self, status_code: int = 202, raise_exc: Exception | None = None):
        self.requests: list[dict] = []
        self.status_code = status_code
        self.raise_exc = raise_exc
        self.closed = False

    async def post(self, url: str, *, headers=None, content=None):
        self.requests.append({
            "url": url,
            "headers": dict(headers or {}),
            "content": content,
        })
        if self.raise_exc is not None:
            raise self.raise_exc

        class _R:
            def __init__(self, status_code: int) -> None:
                self.status_code = status_code

        return _R(self.status_code)

    async def aclose(self) -> None:
        self.closed = True


class _Recorder:
    """Every client the analytics emitters build, plus the auth header the
    real ``_build_client`` would have put on it."""

    def __init__(self) -> None:
        self.status_code = 202
        self.clients: list[_RecordingClient] = []
        self.authorization: list = []

    @property
    def posts(self) -> list[dict]:
        return [r for c in self.clients for r in c.requests]


@pytest.fixture
def recorder(monkeypatch) -> _Recorder:
    from eal_simulator.analytics_emitter import AnalyticsLogEmitter

    rec = _Recorder()
    original = AnalyticsLogEmitter._build_client

    def _build(self, params):
        real = original(self, params)
        rec.authorization.append(real.headers.get("authorization"))
        stub = _RecordingClient(status_code=rec.status_code)
        rec.clients.append(stub)
        return stub

    monkeypatch.setattr(AnalyticsLogEmitter, "_build_client", _build)
    return rec


async def _seed_target(db, name="m365-hec", *, plugins=("m365_activity_emitter",),
                       key=FAKE_KEY):
    from engine.collector_targets import upsert

    await upsert(
        db, name, kind="xsiam_http_collector", url=TARGET_URL, dataset=None,
        auth_header="Authorization", auth_scheme="", plugins=list(plugins),
        is_default=False, token=key, clear_token=False,
    )


def _m365_step(step_id, **params):
    return _eal_step(step_id, plugin="m365_activity_emitter", params=params)


async def _launch_and_ledger(orch, db, **launch_kw):
    from models import Run

    result = await orch.launch(scenario_id="SIM-EDR-001", mode="pull", db=db,
                               target_agent_id="agent-1", **launch_kw)
    assert result.success, result.error
    run = (await db.execute(select(Run).where(Run.run_id == result.run_id))).scalar_one()
    return result, {e["step_id"]: e for e in run.channel_dispatch}


def test_eal_dry_run_without_consent_says_why(session_factory, recorder):
    async def _run():
        async with session_factory() as db:
            await _seed_target(db)
            await _seed_agent(db, "agent-1")
            await _seed_scenario(db, steps=[_m365_step("step-01")])
            _, ledger = await _launch_and_ledger(_fresh_orchestrator(), db)
            assert ledger["step-01"]["status"] == "dry_run"
            assert ledger["step-01"]["code"] == "EAL_DRY_RUN_NO_CONSENT"
            # A dry run builds no records; the ledger must not claim it did.
            assert ledger["step-01"]["detail"].startswith("dry-run: nothing delivered")
            assert recorder.posts == []

    asyncio.run(_run())


def test_eal_consent_without_authorized_by_stays_dry_run(session_factory, recorder):
    async def _run():
        async with session_factory() as db:
            await _seed_target(db)
            await _seed_agent(db, "agent-1")
            await _seed_scenario(db, steps=[_m365_step("step-01")])
            _, ledger = await _launch_and_ledger(
                _fresh_orchestrator(), db, consent=LIVE_CONSENT, authorized_by="   ")
            assert ledger["step-01"]["status"] == "dry_run"
            assert ledger["step-01"]["code"] == "EAL_DRY_RUN_NO_CONSENT"
            assert recorder.posts == []

    asyncio.run(_run())


def test_eal_consent_with_unresolved_target_stays_dry_run_with_code(session_factory, recorder):
    async def _run():
        async with session_factory() as db:
            await _seed_agent(db, "agent-1")
            await _seed_scenario(db, steps=[_m365_step("step-01")])
            _, ledger = await _launch_and_ledger(
                _fresh_orchestrator(), db, consent=LIVE_CONSENT, authorized_by="tester")
            assert ledger["step-01"]["status"] == "dry_run"
            assert ledger["step-01"]["code"] == "EAL_DRY_RUN_TARGET_UNRESOLVED"
            assert recorder.posts == []

    asyncio.run(_run())


def test_eal_one_keyless_step_holds_the_whole_campaign(session_factory, recorder):
    async def _run():
        async with session_factory() as db:
            await _seed_target(db, "m365-hec")
            await _seed_target(db, "keyless-hec", plugins=(), key=None)
            await _seed_agent(db, "agent-1")
            await _seed_scenario(db, steps=[
                _m365_step("step-01"),
                _m365_step("step-02", collector_target="keyless-hec"),
            ])
            _, ledger = await _launch_and_ledger(
                _fresh_orchestrator(), db, consent=LIVE_CONSENT, authorized_by="tester")
            assert ledger["step-02"]["code"] == "EAL_DRY_RUN_TARGET_NO_KEY"
            assert ledger["step-01"]["code"] == "EAL_DRY_RUN_HELD"
            assert "step-02" in ledger["step-01"]["detail"]
            assert {e["status"] for e in ledger.values()} == {"dry_run"}
            assert recorder.posts == []

    asyncio.run(_run())


def test_eal_sleep_over_cap_stays_dry_run(session_factory, recorder):
    async def _run():
        async with session_factory() as db:
            await _seed_target(db)
            await _seed_agent(db, "agent-1")
            await _seed_scenario(db, steps=[
                _m365_step("step-01", iterations=3, sleep_seconds=20),
            ])
            _, ledger = await _launch_and_ledger(
                _fresh_orchestrator(), db, consent=LIVE_CONSENT, authorized_by="tester")
            assert ledger["step-01"]["status"] == "dry_run"
            assert ledger["step-01"]["code"] == "EAL_DRY_RUN_SLEEP_CAP"
            assert recorder.posts == []

    asyncio.run(_run())


def test_eal_live_with_consent_and_keyed_target_posts(session_factory, recorder, monkeypatch):
    from datetime import timezone

    import eal_simulator.executor as executor_mod
    from eal_simulator.base import BaseSimulation
    from models import Result

    step_start = datetime(2030, 1, 1, tzinfo=timezone.utc)
    # The executor stamps a failed step; the plugin stamps its own result.
    monkeypatch.setattr(executor_mod, "_utcnow", lambda: step_start)
    monkeypatch.setattr(BaseSimulation, "utcnow", staticmethod(lambda: step_start))

    async def _run():
        async with session_factory() as db:
            await _seed_target(db)
            await _seed_agent(db, "agent-1")
            await _seed_scenario(db, steps=[_m365_step("step-01")])
            _, ledger = await _launch_and_ledger(
                _fresh_orchestrator(), db, consent=LIVE_CONSENT, authorized_by="tester")
            entry = ledger["step-01"]
            assert entry["status"] == "dispatched", entry
            assert entry["code"] is None
            assert entry["collector_target"] == "m365-hec"

            assert recorder.posts, "a live step must POST"
            assert {p["url"] for p in recorder.posts} == {TARGET_URL}
            assert recorder.authorization == [FAKE_KEY]

            results = (await db.execute(
                select(Result).where(Result.step_id == "step-01")
            )).scalars().all()
            assert len(results) == 1
            assert results[0].executed_at == datetime(2030, 1, 1)

    asyncio.run(_run())


def test_eal_live_collector_401_reads_not_delivered(session_factory, recorder):
    recorder.status_code = 401

    async def _run():
        async with session_factory() as db:
            await _seed_target(db)
            await _seed_agent(db, "agent-1")
            await _seed_scenario(db, steps=[_m365_step("step-01")])
            _, ledger = await _launch_and_ledger(
                _fresh_orchestrator(), db, consent=LIVE_CONSENT, authorized_by="tester")
            assert recorder.posts, "the live step must have tried to POST"
            assert ledger["step-01"]["status"] == "not_delivered", ledger["step-01"]

    asyncio.run(_run())


def test_eal_step_rejected_before_the_campaign_holds_the_rest(session_factory, recorder):
    """A step dropped before the campaign is built (unregistered plugin) must
    hold the clean steps at dry-run: a scenario run is never half-live."""
    async def _run():
        async with session_factory() as db:
            await _seed_target(db)
            await _seed_agent(db, "agent-1")
            await _seed_scenario(db, steps=[
                _m365_step("step-01"),
                _eal_step("step-02", plugin="no_such_plugin"),
            ])
            _, ledger = await _launch_and_ledger(
                _fresh_orchestrator(), db, consent=LIVE_CONSENT, authorized_by="tester")
            assert ledger["step-02"]["status"] == "error"
            assert ledger["step-02"]["code"] == "PLUGIN_MISCONFIGURED"
            assert ledger["step-01"]["status"] == "dry_run"
            assert ledger["step-01"]["code"] == "EAL_DRY_RUN_HELD"
            assert "step-02" in ledger["step-01"]["detail"]
            assert recorder.posts == []

    asyncio.run(_run())


def test_eal_step_with_invalid_params_holds_the_rest(session_factory, recorder):
    """Params are validated by the executor only once the campaign runs, after
    the go-live decision. A step whose params cannot validate must be caught
    before it, or the clean steps beside it go live and the run is half-live."""
    import json

    async def _run():
        async with session_factory() as db:
            await _seed_target(db, "m365-hec")
            await _seed_target(db, "idp-hec", plugins=("idp_signin_emulator",))
            await _seed_agent(db, "agent-1")
            await _seed_scenario(db, steps=[
                _m365_step("step-01"),
                # The invalid value is key-shaped: the ledger must never echo it.
                _eal_step("step-02", plugin="idp_signin_emulator",
                          params={"event_pattern": FAKE_KEY}),
                _m365_step("step-03"),
            ])
            _, ledger = await _launch_and_ledger(
                _fresh_orchestrator(), db, consent=LIVE_CONSENT, authorized_by="tester")
            assert ledger["step-02"]["status"] == "error", ledger["step-02"]
            assert ledger["step-02"]["code"] == "EAL_STEP_INVALID"
            assert "event_pattern" in ledger["step-02"]["detail"]
            for sid in ("step-01", "step-03"):
                assert ledger[sid]["status"] == "dry_run", ledger[sid]
                assert ledger[sid]["code"] == "EAL_DRY_RUN_HELD"
                assert "step-02" in ledger[sid]["detail"]
            assert recorder.posts == []
            assert FAKE_KEY not in json.dumps(ledger, default=str)

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# An eal step that delivered nothing is not executed, not missed: its Results
# leave the efficacy denominator, so a later Tier-2 verify cannot read `fail`
# for records that were never sent.
# ---------------------------------------------------------------------------

VERIFY_XQL = "dataset = msft_o365_audit | limit 1"


def _m365_verified_step(step_id, **params):
    step = _m365_step(step_id, **params)
    step["expected_detections"] = [{"type": "Analytics", "description": step_id,
                                    "verification_xql": VERIFY_XQL}]
    return step


def test_dry_run_eal_results_are_not_executed_and_never_verify_fail(session_factory, recorder):
    from connectors.service import verify_run_now
    from engine.outcome import is_in_denominator
    from models import Result, Run

    queries: list[str] = []

    async def _zero_rows(xql):
        queries.append(xql)
        return 0

    async def _run():
        async with session_factory() as db:
            await _seed_target(db)
            await _seed_agent(db, "agent-1")
            await _seed_scenario(db, steps=[_m365_verified_step("step-01"),
                                            _m365_verified_step("step-02")])
            result, ledger = await _launch_and_ledger(_fresh_orchestrator(), db)
            assert {e["status"] for e in ledger.values()} == {"dry_run"}

            rows = (await db.execute(
                select(Result).where(Result.run_id == result.run_id)
            )).scalars().all()
            assert len(rows) == 2
            for r in rows:
                assert r.outcome == "not_executed"
                assert r.outcome_reason == "EAL_DRY_RUN_NO_CONSENT"
                assert not is_in_denominator(r.outcome, r.outcome_reason)

            run = (await db.execute(
                select(Run).where(Run.run_id == result.run_id))).scalar_one()
            run.status = "complete"
            out = await verify_run_now(db, run, runner=_zero_rows, tenant="lab")
            await db.commit()
            assert out.tc_verdict != "fail", out
            assert queries == []
            for r in rows:
                assert r.kpi_verdict != "fail"

    asyncio.run(_run())


def test_live_dispatched_eal_results_stay_in_the_denominator(session_factory, recorder):
    from engine.outcome import is_in_denominator
    from models import Result

    async def _run():
        async with session_factory() as db:
            await _seed_target(db)
            await _seed_agent(db, "agent-1")
            await _seed_scenario(db, steps=[_m365_verified_step("step-01")])
            result, ledger = await _launch_and_ledger(
                _fresh_orchestrator(), db, consent=LIVE_CONSENT, authorized_by="tester")
            assert ledger["step-01"]["status"] == "dispatched", ledger["step-01"]

            rows = (await db.execute(
                select(Result).where(Result.run_id == result.run_id)
            )).scalars().all()
            assert len(rows) == 1
            assert rows[0].outcome is None
            assert is_in_denominator(rows[0].outcome, rows[0].outcome_reason)
            assert rows[0].kpi_verdict == "pending"

    asyncio.run(_run())

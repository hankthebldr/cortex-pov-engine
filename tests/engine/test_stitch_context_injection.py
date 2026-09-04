"""Stitch-Context injection on the pull launch path (spec §7.1, Phase 2).

The resolver (`engine.stitch_context`) and its schema/migration are proven in
their own modules. This one proves the ORCHESTRATOR half — the sibling of the
adapter-placeholder path — end to end against a real (in-memory) DB:

1. A `{stitch:KEY}` in a step command is replaced by the run's RESOLVED concrete
   value before the task is enqueued (so every step, and in Phase 3 every
   channel, emits the SAME shared entity).
2. An unresolved `{stitch:*}` — an unknown key, or a key the context never
   declared — is left VERBATIM, the identical honesty rule the adapter path uses,
   so the agent's own output surfaces the miss instead of a silently-empty
   command reading as success.
3. A scenario with NO `stitch_context` runs BYTE-IDENTICALLY to today: the binding
   is `None`, `{stitch:*}` is untouched, and `runs.stitch_binding` persists NULL.
4. The value persisted on `runs.stitch_binding` is the REAL binding used
   (Gate A5: the resolved values actually injected, nothing invented) and equals
   an independent `resolve_stitch_context(..., seed=run_id)` — deterministic.

The pure `_resolve_stitch_placeholders` / `_render_stitch` are also exercised
directly against a hand-built `StitchBinding` so the substitution and passthrough
rules are pinned without a DB round trip.
"""
from __future__ import annotations

import asyncio
from datetime import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


# ---------------------------------------------------------------------------
# fixtures / seeders (mirrors tests/engine/test_orchestrator_runtime_install.py)
# ---------------------------------------------------------------------------


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


async def _seed_scenario(db, scenario_id="SIM-EDR-001", *, command, stitch_context=None):
    from models import Scenario

    step = {
        "id": "step-01", "name": "s", "identity": "root",
        "command": command, "expected_detections": [],
    }
    s = Scenario(
        scenario_id=scenario_id,
        name="Test", version="1.0", status="active", plane="EDR",
        uc_ref="UCS-EDR-01", uc_name="x",
        tc_ref="TC-EDR-01", tc_name="y",
        mitre_tactic="TA0006", mitre_tactic_name="Credential Access",
        mitre_technique="T1003", mitre_technique_name="OS Credential Dumping",
        execution_identity={"default": "root"},
        push_supported=True, pull_supported=True,
        steps=[step],
        stitch_context=stitch_context,
    )
    db.add(s)
    await db.commit()
    return s


async def _seed_agent(db, agent_id="agent-1", hostname="host-1"):
    from models import Agent

    a = Agent(
        agent_id=agent_id, hostname=hostname, os="linux",
        capabilities=["shell", "identity-harness"], interpreters=[],
        registered_at=datetime.utcnow(), last_seen=datetime.utcnow(),
        status="online",
    )
    db.add(a)
    await db.commit()
    return a


def _enqueued_command(orch, agent_id="agent-1"):
    """The command of the single step on the task the launch enqueued."""
    task = orch._queue[agent_id][0]
    return task.steps[0]["command"]


# ---------------------------------------------------------------------------
# pure function — substitution + honesty (no DB)
# ---------------------------------------------------------------------------


def test_resolve_stitch_placeholders_substitutes_resolved_keys():
    from engine.orchestrator import _resolve_stitch_placeholders
    from engine.stitch_context import StitchBinding

    binding = StitchBinding(src_ip="10.4.9.12", dst_ip="203.0.113.10", dst_port=443)
    steps = [{"id": "s1", "command": "curl --local {stitch:src_ip} https://{stitch:dst_ip}:{stitch:dst_port}/beacon"}]

    out = _resolve_stitch_placeholders(steps, binding)

    assert out[0]["command"] == "curl --local 10.4.9.12 https://203.0.113.10:443/beacon"
    # input never mutated
    assert steps[0]["command"] == "curl --local {stitch:src_ip} https://{stitch:dst_ip}:{stitch:dst_port}/beacon"
    assert out is not steps and out[0] is not steps[0]


def test_unknown_and_undeclared_keys_left_verbatim():
    from engine.orchestrator import _resolve_stitch_placeholders
    from engine.stitch_context import StitchBinding

    # src_ip resolved; dst_port declared-but-None (undeclared leg); bogus unknown key
    binding = StitchBinding(src_ip="10.4.9.12")
    steps = [{"id": "s1", "command": "x {stitch:src_ip} {stitch:dst_port} {stitch:bogus}"}]

    out = _resolve_stitch_placeholders(steps, binding)

    # only the resolved leg is substituted; the miss surfaces raw for the agent
    assert out[0]["command"] == "x 10.4.9.12 {stitch:dst_port} {stitch:bogus}"


def test_none_binding_leaves_everything_verbatim():
    from engine.orchestrator import _resolve_stitch_placeholders

    steps = [{"id": "s1", "command": "echo {stitch:src_ip} hello"}]
    out = _resolve_stitch_placeholders(steps, None)
    assert out[0]["command"] == "echo {stitch:src_ip} hello"
    # still a NEW list / new step dict — never mutated in place
    assert out is not steps and out[0] is not steps[0]


def test_never_substitutes_empty_string():
    """The whole point of leaving a miss verbatim: a `{stitch:*}` must never
    become '' (which would read as a working, if pointless, command)."""
    from engine.orchestrator import _resolve_stitch_placeholders
    from engine.stitch_context import StitchBinding

    out = _resolve_stitch_placeholders(
        [{"id": "s1", "command": "run {stitch:cloud_resource}"}], StitchBinding()
    )
    assert out[0]["command"] == "run {stitch:cloud_resource}"


# ---------------------------------------------------------------------------
# end to end through orchestrator.launch (real DB)
# ---------------------------------------------------------------------------


def test_launch_substitutes_and_persists_binding(session_factory):
    from models import Run
    from engine.stitch_context import resolve_stitch_context

    async def _run():
        orch = _fresh_orchestrator()
        async with session_factory() as db:
            await _seed_agent(db)
            await _seed_scenario(
                db,
                command="curl --local-port {stitch:src_port} https://{stitch:dst_ip}:{stitch:dst_port}/b",
                stitch_context={
                    "src_ip": {"resolve": "auto_ip"},
                    "src_port": {"resolve": "auto_port"},
                    "dst_ip": {"literal": "203.0.113.10"},
                    "dst_port": {"literal": 443},
                },
            )
            result = await orch.launch(
                scenario_id="SIM-EDR-001", mode="pull", db=db, target_agent_id="agent-1",
            )
            assert result.success

            run = (await db.execute(
                select(Run).where(Run.run_id == result.run_id)
            )).scalar_one()

            # persisted binding is the REAL resolved values, nothing invented
            assert run.stitch_binding is not None
            assert run.stitch_binding["dst_ip"] == "203.0.113.10"
            assert run.stitch_binding["dst_port"] == 443
            assert run.stitch_binding["src_ip"] is not None
            assert run.stitch_binding["src_port"] is not None

            # deterministic: an independent resolve on the same seed matches byte-for-byte
            expect = resolve_stitch_context(
                {
                    "src_ip": {"resolve": "auto_ip"},
                    "src_port": {"resolve": "auto_port"},
                    "dst_ip": {"literal": "203.0.113.10"},
                    "dst_port": {"literal": 443},
                },
                seed=result.run_id,
                target=None,
            )
            assert run.stitch_binding == expect.values

            # the enqueued command carries the concrete values, no {stitch:*} left
            cmd = _enqueued_command(orch)
            assert "{stitch:" not in cmd
            assert str(run.stitch_binding["src_port"]) in cmd
            assert "203.0.113.10:443" in cmd

    asyncio.run(_run())


def test_launch_leaves_unknown_stitch_key_verbatim(session_factory):
    async def _run():
        orch = _fresh_orchestrator()
        async with session_factory() as db:
            await _seed_agent(db)
            await _seed_scenario(
                db,
                command="probe {stitch:src_ip} then {stitch:bogus}",
                stitch_context={"src_ip": {"resolve": "auto_ip"}},
            )
            result = await orch.launch(
                scenario_id="SIM-EDR-001", mode="pull", db=db, target_agent_id="agent-1",
            )
            assert result.success
            cmd = _enqueued_command(orch)
            assert "{stitch:bogus}" in cmd          # miss surfaced, not silently dropped
            assert "{stitch:src_ip}" not in cmd     # the declared leg WAS substituted

    asyncio.run(_run())


def test_from_agent_resolves_host_to_launch_target_hostname(session_factory):
    from models import Run

    async def _run():
        orch = _fresh_orchestrator()
        async with session_factory() as db:
            await _seed_agent(db, hostname="jumpbox-42")
            await _seed_scenario(
                db,
                command="hostname is {stitch:host}",
                stitch_context={"host": {"resolve": "from_agent"}},
            )
            result = await orch.launch(
                scenario_id="SIM-EDR-001", mode="pull", db=db, target_agent_id="agent-1",
            )
            assert result.success
            run = (await db.execute(
                select(Run).where(Run.run_id == result.run_id)
            )).scalar_one()
            assert run.stitch_binding["host"] == "jumpbox-42"
            assert _enqueued_command(orch) == "hostname is jumpbox-42"

    asyncio.run(_run())


def test_scenario_without_stitch_context_is_unchanged(session_factory):
    """The corpus / Phase-1 draft path: no context ⇒ command verbatim,
    binding NULL — byte-identical to before Phase 2."""
    from models import Run

    async def _run():
        orch = _fresh_orchestrator()
        async with session_factory() as db:
            await _seed_agent(db)
            await _seed_scenario(
                db,
                command="echo {stitch:src_ip} still-here",
                stitch_context=None,
            )
            result = await orch.launch(
                scenario_id="SIM-EDR-001", mode="pull", db=db, target_agent_id="agent-1",
            )
            assert result.success
            run = (await db.execute(
                select(Run).where(Run.run_id == result.run_id)
            )).scalar_one()
            assert run.stitch_binding is None
            # placeholder left verbatim because there is no context to resolve it
            assert _enqueued_command(orch) == "echo {stitch:src_ip} still-here"

    asyncio.run(_run())


def test_invalid_persisted_spec_refuses_at_launch(session_factory):
    """A stitch_context that no longer validates (an unknown directive) must
    fail CLOSED at launch, not inject a half-resolved binding (Gate A5)."""
    from models import Run

    async def _run():
        orch = _fresh_orchestrator()
        async with session_factory() as db:
            await _seed_agent(db)
            await _seed_scenario(
                db,
                command="x {stitch:src_ip}",
                stitch_context={"src_ip": {"resolve": "not_a_real_directive"}},
            )
            result = await orch.launch(
                scenario_id="SIM-EDR-001", mode="pull", db=db, target_agent_id="agent-1",
            )
            assert result.success is False
            assert "STITCH_CONTEXT_INVALID" in (result.error or "")
            # run row exists (seeded before pull) but was never marked running with a binding
            run = (await db.execute(
                select(Run).where(Run.run_id == result.run_id)
            )).scalar_one()
            assert run.stitch_binding is None
            # nothing enqueued for the agent
            assert not orch._queue.get("agent-1")

    asyncio.run(_run())

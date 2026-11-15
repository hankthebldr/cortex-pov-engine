"""A refused launch leaves nothing behind: no Run, no Result, no task, no event.

History. ``launch()`` used to commit the Run row and seed its Result rows
BEFORE several checks that could still refuse it (the payload shelf, a missing
target, the stitch context, a composed run's second endpoint, the
container-agent gate). Left ``pending``, such a row read as work still owed and
was later reported as an orphan whose "queued task was lost". The first fix
made those rows terminal ``failed`` runs. This one moves the checks: every
refusal is now decided in ``Orchestrator._evaluate_launch`` before anything is
written, which is the rule the consent gate always followed. An unauthorized or
unrunnable launch did not happen, and the run list must not say "we tried".

The same evaluation backs ``POST /api/runs/preflight``
(tests/api/test_runs_launch_preflight.py), so the console can show every one of
these refusals before the operator presses Run.
"""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import func, select
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


def _agent(agent_id="a1"):
    from datetime import datetime  # noqa: PLC0415

    from models import Agent  # noqa: PLC0415

    return Agent(agent_id=agent_id, hostname=f"h-{agent_id}", os="linux",
                 capabilities=["shell", "identity-harness"], interpreters=[],
                 registered_at=datetime.utcnow(), last_seen=datetime.utcnow(),
                 status="online")


def _scenario(scenario_id, *, steps=None, stitch_context=None):
    from models import Scenario  # noqa: PLC0415

    return Scenario(
        scenario_id=scenario_id, name="T", version="1.0", status="active", plane="EDR",
        uc_ref="UCS-EDR-01", uc_name="x", tc_ref="TC-EDR-01", tc_name="y",
        mitre_tactic="TA0006", mitre_tactic_name="Credential Access",
        mitre_technique="T1003", mitre_technique_name="OS Credential Dumping",
        execution_identity={"default": "root"},
        push_supported=True, pull_supported=True,
        stitch_context=stitch_context,
        steps=steps or [{
            "id": "step-01", "name": "s", "command": "bash /tmp/linpeas.sh",
            "expected_detections": [
                {"detection_id": "d-1", "detection_type": "BIOC", "name": "n"},
            ],
        }],
    )


async def _seed(db, scenario):
    db.add(scenario)
    db.add(_agent())
    await db.commit()


async def _counts(db):
    from models import Result, Run  # noqa: PLC0415

    runs = (await db.execute(select(func.count()).select_from(Run))).scalar_one()
    results = (await db.execute(select(func.count()).select_from(Result))).scalar_one()
    return runs, results


def _raise_payload_error(_scenario):
    from engine.payload_shelf import PayloadResolutionError  # noqa: PLC0415

    raise PayloadResolutionError(
        "PAYLOAD_NOT_STAGED",
        "Payload not staged",
        "linpeas.sh is declared by TOOL-LINPEAS but is not on this shelf",
    )


def _raise_stitch_error(spec, *, seed=None, target=None):
    from engine import stitch_context as sc  # noqa: PLC0415

    raise sc.StitchContextValidationError(
        "unknown directive 'teleport'", key="principal", directive="teleport",
    )


def _launch(session_factory, scenario, *, target="a1", mode="pull"):
    """Launch once, watching the real event bus. Returns
    (result, (runs, results), run.status frames, queued tasks for target)."""
    from engine import orchestrator as orch  # noqa: PLC0415
    from events import event_bus  # noqa: PLC0415

    async def _go():
        q = event_bus.subscribe(None)
        try:
            o = orch.Orchestrator()
            async with session_factory() as db:
                await _seed(db, scenario)
                result = await o.launch(
                    scenario_id=scenario.scenario_id, mode=mode, db=db,
                    target_agent_id=target,
                )
                counts = await _counts(db)
            frames = []
            while not q.empty():
                frames.append(q.get_nowait())
            statuses = [f for f in frames if f.get("type") == "run.status"]
            return result, counts, statuses, o.peek_queue(target or "")
        finally:
            event_bus.unsubscribe(None, q)

    return asyncio.run(_go())


def _assert_nothing_written(result, counts, statuses, queued):
    assert result.success is False
    assert result.run_id is None
    assert counts == (0, 0), "a refused launch wrote a Run or Result row"
    assert statuses == [], "a refused launch published a run.status frame"
    assert queued == []


# ===========================================================================
# 1 · Refusals that used to fire after the Run row was written
# ===========================================================================

def test_a_payload_refusal_writes_nothing(session_factory, monkeypatch):
    """The shelf's fail-closed guard (a run must never reach a customer
    endpoint half-armed) now refuses before the run exists."""
    from engine import orchestrator as orch  # noqa: PLC0415

    monkeypatch.setattr(orch, "_compose_artifacts", _raise_payload_error)
    result, counts, statuses, queued = _launch(session_factory, _scenario("SIM-EDR-999"))
    _assert_nothing_written(result, counts, statuses, queued)
    assert result.error_code == "PAYLOAD_NOT_STAGED"
    assert result.http_status == 409
    # The shelf's own envelope still travels with the refusal.
    assert result.error_detail["code"] == "PAYLOAD_NOT_STAGED"


def test_a_pull_launch_without_a_target_writes_nothing(session_factory):
    result, counts, statuses, queued = _launch(
        session_factory, _scenario("SIM-EDR-999"), target=None)
    _assert_nothing_written(result, counts, statuses, queued)
    assert result.error_code == "PULL_TARGET_MISSING"
    assert result.http_status == 422


def test_an_unknown_mode_writes_nothing(session_factory):
    result, counts, statuses, queued = _launch(
        session_factory, _scenario("SIM-EDR-999"), mode="sideways")
    _assert_nothing_written(result, counts, statuses, queued)
    assert result.error_code == "INVALID_MODE"
    assert "sideways" in result.error


def test_a_stitch_refusal_writes_nothing_and_carries_its_code(session_factory, monkeypatch):
    """It used to be LAUNCH_FAILED with the code only in the prose."""
    from engine import stitch_context as sc  # noqa: PLC0415

    monkeypatch.setattr(sc, "resolve_stitch_context", _raise_stitch_error)
    result, counts, statuses, queued = _launch(session_factory, _scenario(
        "SIM-EDR-998",
        stitch_context={"principal": {"directive": "static", "value": "svc-backup"}},
        steps=[{"id": "step-01", "name": "s", "command": "id",
                "expected_detections": [
                    {"detection_id": "d-1", "detection_type": "BIOC", "name": "n"}]}],
    ))
    _assert_nothing_written(result, counts, statuses, queued)
    assert result.error_code == "STITCH_CONTEXT_INVALID"
    assert result.error_detail["key"] == "principal"


def test_a_phantom_second_endpoint_writes_nothing(session_factory):
    """A composed run naming an endpoint that is not enrolled is refused all or
    nothing, before any task for its real endpoints could be queued."""
    result, counts, statuses, queued = _launch(session_factory, _scenario(
        "SIM-EDR-997",
        steps=[{"id": "step-01", "name": "s", "command": "id",
                "target": "phantom-endpoint",
                "expected_detections": [
                    {"detection_id": "d-1", "detection_type": "BIOC", "name": "n"}]}],
    ))
    _assert_nothing_written(result, counts, statuses, queued)
    assert result.error_code == "TARGET_AGENT_NOT_ENROLLED"
    assert result.error_detail["missing_agents"] == ["phantom-endpoint"]


def test_an_unknown_scenario_has_its_own_code(session_factory):
    from engine import orchestrator as orch  # noqa: PLC0415

    async def _go():
        async with session_factory() as db:
            result = await orch.Orchestrator().launch(
                scenario_id="SIM-NOPE-000", mode="pull", db=db, target_agent_id="a1")
            return result, await _counts(db)

    result, counts = asyncio.run(_go())
    assert counts == (0, 0)
    assert result.error_code == "SCENARIO_NOT_FOUND"
    assert result.http_status == 404


# ===========================================================================
# 2 · The restart sweep has nothing to misreport
# ===========================================================================

def test_a_refused_launch_leaves_no_orphan_for_the_restart_sweep(session_factory, monkeypatch):
    """``rehydrate()`` reports every non-terminal run with no surviving task as
    an orphan whose "queued task was lost". A refused launch has no run, so
    there is nothing to misreport."""
    from engine import orchestrator as orch  # noqa: PLC0415

    monkeypatch.setattr(orch, "_compose_artifacts", _raise_payload_error)

    async def _go():
        async with session_factory() as db:
            await _seed(db, _scenario("SIM-EDR-999"))
            await orch.Orchestrator().launch(
                scenario_id="SIM-EDR-999", mode="pull", db=db, target_agent_id="a1")
            return await orch.Orchestrator().rehydrate(db)

    counts = asyncio.run(_go())
    assert counts["failed_orphans"] == 0


# ===========================================================================
# 3 · The rule these refusals now share with the consent gate
# ===========================================================================

def test_a_consent_refusal_still_creates_no_run(session_factory, monkeypatch):
    """An unauthorized launch must leave no trace: a `failed` run in the list
    would read as "we tried", and we did not."""
    from engine import orchestrator as orch  # noqa: PLC0415

    monkeypatch.setattr(
        orch, "_check_launch_consent",
        lambda scenario, consent, *, mode=None: "refused: c2_authorized is not set",
    )
    result, counts, statuses, queued = _launch(session_factory, _scenario("SIM-EDR-999"))
    _assert_nothing_written(result, counts, statuses, queued)


def test_a_launch_that_clears_every_check_still_runs(session_factory):
    """The other half: moving the checks must not stop a good launch."""
    from engine import orchestrator as orch  # noqa: PLC0415

    async def _go():
        o = orch.Orchestrator()
        async with session_factory() as db:
            await _seed(db, _scenario("SIM-EDR-996", steps=[{
                "id": "step-01", "name": "s", "command": "id",
                "expected_detections": [
                    {"detection_id": "d-1", "detection_type": "BIOC", "name": "n"}]}]))
            result = await o.launch(
                scenario_id="SIM-EDR-996", mode="pull", db=db, target_agent_id="a1")
            return result, await _counts(db), o.peek_queue("a1")

    result, counts, queued = asyncio.run(_go())
    assert result.success is True
    assert counts == (1, 1)
    assert len(queued) == 1

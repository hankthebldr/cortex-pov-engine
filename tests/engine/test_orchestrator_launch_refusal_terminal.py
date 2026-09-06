"""A refused launch must leave a TERMINAL run, never a dangling `pending` one.

``launch()`` creates the Run row and seeds its Result rows BEFORE several early
returns that refuse the launch. Each of those returns hands the API a
``LaunchResult(success=False)``, the API raises 409/422 — and the committed Run
row was left sitting in ``pending`` forever.

Why that is not cosmetic. ``pending`` is a NON-terminal state with a specific
meaning everywhere else in this engine: *work that is still owed*. A dangling
refused run therefore

  * is counted by every "runs in flight" surface the console shows a DC,
  * is picked up by ``orchestrator.rehydrate()`` on the next SimCore restart and
    reported as an ORPHANED run whose "queued task was lost" — an invented
    infrastructure failure that never happened, and
  * is indistinguishable, in the run list, from a launch that really is waiting
    on a beacon.

That is the Gate A5 failure mode in miniature: a run that was refused reads as a
run that is still owed. The refusal is the honest outcome and it must be
recorded as one.

The seeded Result rows are deliberately KEPT. They record what the scenario
expected to detect; a failed run with its expectations intact is evidence, an
empty one is amnesia.
"""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


# Mirrors core/api/runs.py::_TERMINAL_STATES. Duplicated on purpose: if that set
# is ever narrowed, this test must fail rather than silently follow it.
TERMINAL_STATES = {"complete", "failed", "aborted", "staged"}


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


async def _seed_scenario(db, scenario_id="SIM-EDR-999"):
    from models import Scenario  # noqa: PLC0415

    db.add(Scenario(
        scenario_id=scenario_id, name="T", version="1.0", status="active", plane="EDR",
        uc_ref="UCS-EDR-01", uc_name="x", tc_ref="TC-EDR-01", tc_name="y",
        mitre_tactic="TA0006", mitre_tactic_name="Credential Access",
        mitre_technique="T1003", mitre_technique_name="OS Credential Dumping",
        execution_identity={"default": "root"},
        push_supported=True, pull_supported=True,
        steps=[{
            "id": "step-01", "name": "s", "command": "bash /tmp/linpeas.sh",
            "expected_detections": [
                {"detection_id": "d-1", "detection_type": "BIOC", "name": "n"},
            ],
        }],
    ))
    await db.commit()


async def _run_row(db, run_id):
    from models import Run  # noqa: PLC0415

    return (await db.execute(select(Run).where(Run.run_id == run_id))).scalar_one()


def _raise_payload_error(_scenario):
    from engine.payload_shelf import PayloadResolutionError  # noqa: PLC0415

    raise PayloadResolutionError(
        "PAYLOAD_NOT_STAGED",
        "Payload not staged",
        "linpeas.sh is declared by TOOL-LINPEAS but is not on this shelf",
    )


# ===========================================================================
# 1 · The payload refusal (PAYLOAD_NOT_STAGED / PAYLOAD_PIN_MISMATCH)
# ===========================================================================

def test_payload_refusal_leaves_the_run_terminal_failed(session_factory, monkeypatch):
    """A run refused because its tooling could not be resolved is FINISHED.

    Without this, the shelf's own fail-closed guard — the one that stops a
    half-armed run reaching a customer endpoint — leaves behind exactly the
    artefact it was protecting against being misread.
    """
    from engine import orchestrator as orch  # noqa: PLC0415

    monkeypatch.setattr(orch, "_compose_artifacts", _raise_payload_error)

    async def _go():
        async with session_factory() as db:
            await _seed_scenario(db)
            result = await orch.Orchestrator().launch(
                scenario_id="SIM-EDR-999", mode="pull", db=db, target_agent_id="a1",
            )
            return result, await _run_row(db, result.run_id)

    result, run = asyncio.run(_go())

    assert result.success is False
    assert "PAYLOAD_NOT_STAGED" in result.error
    # The row exists (the operator can look up what they just launched)...
    assert run.run_id == result.run_id
    # ...and it is DONE.
    assert run.status == "failed"
    assert run.status in TERMINAL_STATES
    assert run.completed_at is not None
    # The reason travels with the run, not only with the HTTP response the
    # operator may have already dismissed.
    assert "PAYLOAD_NOT_STAGED" in (run.output or "")


def test_payload_refusal_keeps_the_seeded_results(session_factory, monkeypatch):
    """Marking the run failed must not quietly delete its expectations."""
    from engine import orchestrator as orch  # noqa: PLC0415
    from models import Result  # noqa: PLC0415

    monkeypatch.setattr(orch, "_compose_artifacts", _raise_payload_error)

    async def _go():
        async with session_factory() as db:
            await _seed_scenario(db)
            result = await orch.Orchestrator().launch(
                scenario_id="SIM-EDR-999", mode="pull", db=db, target_agent_id="a1",
            )
            rows = (await db.execute(
                select(Result).where(Result.run_id == result.run_id)
            )).scalars().all()
            return rows

    assert len(asyncio.run(_go())) == 1


def test_payload_refusal_is_invisible_to_the_restart_orphan_sweep(
    session_factory, monkeypatch,
):
    """The sharpest consequence: ``rehydrate()`` marks every non-terminal run
    with no surviving task as an orphan whose "queued task was lost". A refused
    launch has no task because it was REFUSED — reporting it as a lost task
    invents a SimCore failure and puts that sentence in a POV report."""
    from engine import orchestrator as orch  # noqa: PLC0415

    monkeypatch.setattr(orch, "_compose_artifacts", _raise_payload_error)

    async def _go():
        async with session_factory() as db:
            await _seed_scenario(db)
            o = orch.Orchestrator()
            result = await o.launch(
                scenario_id="SIM-EDR-999", mode="pull", db=db, target_agent_id="a1",
            )
            counts = await orch.Orchestrator().rehydrate(db)
            return counts, await _run_row(db, result.run_id)

    counts, run = asyncio.run(_go())

    assert counts["failed_orphans"] == 0
    assert "queued task was lost" not in (run.output or "")


def test_payload_refusal_publishes_the_terminal_status(session_factory, monkeypatch):
    """A console watching the global stream must see the run reach its end.

    Subscribes to the REAL event bus — the frame a browser would receive.
    """
    from engine import orchestrator as orch  # noqa: PLC0415

    monkeypatch.setattr(orch, "_compose_artifacts", _raise_payload_error)

    async def _go():
        from events import event_bus  # noqa: PLC0415

        q = event_bus.subscribe(None)
        try:
            async with session_factory() as db:
                await _seed_scenario(db)
                result = await orch.Orchestrator().launch(
                    scenario_id="SIM-EDR-999", mode="pull", db=db, target_agent_id="a1",
                )
            frames = []
            while not q.empty():
                frames.append(q.get_nowait())
            return result, frames
        finally:
            event_bus.unsubscribe(None, q)

    result, frames = asyncio.run(_go())

    statuses = [
        f["data"]["status"] for f in frames
        if f.get("type") == "run.status" and f.get("run_id") == result.run_id
    ]
    assert statuses == ["failed"]


# ===========================================================================
# 2 · The other early returns that follow _seed_results
# ===========================================================================

def test_pull_without_a_target_agent_leaves_the_run_terminal_failed(session_factory):
    """Same defect, one branch earlier — and reachable from the API, which
    accepts a pull launch body with no ``target_agent_id``."""
    from engine import orchestrator as orch  # noqa: PLC0415

    async def _go():
        async with session_factory() as db:
            await _seed_scenario(db)
            result = await orch.Orchestrator().launch(
                scenario_id="SIM-EDR-999", mode="pull", db=db, target_agent_id=None,
            )
            return result, await _run_row(db, result.run_id)

    result, run = asyncio.run(_go())

    assert result.success is False
    assert run.status == "failed"
    assert run.completed_at is not None
    assert "target_agent_id" in (run.output or "")


def test_unknown_mode_leaves_the_run_terminal_failed(session_factory):
    from engine import orchestrator as orch  # noqa: PLC0415

    async def _go():
        async with session_factory() as db:
            await _seed_scenario(db)
            result = await orch.Orchestrator().launch(
                scenario_id="SIM-EDR-999", mode="sideways", db=db,
            )
            return result, await _run_row(db, result.run_id)

    result, run = asyncio.run(_go())

    assert result.success is False
    assert run.status == "failed"
    assert run.completed_at is not None
    assert "sideways" in (run.output or "")


# ===========================================================================
# 3 · The line the fix must not cross
# ===========================================================================

def test_a_refusal_before_seeding_still_creates_no_run(session_factory, monkeypatch):
    """The consent gate refuses BEFORE the Run exists, and must keep doing so.

    Terminal-failing a refused launch is the right answer only for refusals that
    happen after the row is committed. An unauthorized launch must leave no trace
    at all — a `failed` run in the list would read as "we tried", and we did not.
    """
    from engine import orchestrator as orch  # noqa: PLC0415
    from models import Run  # noqa: PLC0415

    monkeypatch.setattr(
        orch, "_check_launch_consent",
        lambda scenario, consent, *, mode=None: "refused: c2_authorized is not set",
    )

    async def _go():
        async with session_factory() as db:
            await _seed_scenario(db)
            result = await orch.Orchestrator().launch(
                scenario_id="SIM-EDR-999", mode="pull", db=db, target_agent_id="a1",
            )
            return result, (await db.execute(select(Run))).scalars().all()

    result, rows = asyncio.run(_go())

    assert result.success is False
    assert result.run_id is None
    assert rows == []


# ===========================================================================
# 4 · The Composer-era refusals (Phase 2 stitch, Phase 3a channel dispatch)
# ===========================================================================

async def _seed_stitch_scenario(db, scenario_id="SIM-EDR-998"):
    from models import Scenario  # noqa: PLC0415

    db.add(Scenario(
        scenario_id=scenario_id, name="T", version="1.0", status="active", plane="EDR",
        uc_ref="UCS-EDR-01", uc_name="x", tc_ref="TC-EDR-01", tc_name="y",
        mitre_tactic="TA0006", mitre_tactic_name="Credential Access",
        mitre_technique="T1003", mitre_technique_name="OS Credential Dumping",
        execution_identity={"default": "root"},
        push_supported=True, pull_supported=True,
        stitch_context={"principal": {"directive": "static", "value": "svc-backup"}},
        steps=[{
            "id": "step-01", "name": "s", "command": "id",
            "expected_detections": [
                {"detection_id": "d-1", "detection_type": "BIOC", "name": "n"},
            ],
        }],
    ))
    await db.commit()


def test_stitch_refusal_leaves_the_run_terminal_failed(session_factory, monkeypatch):
    """A persisted ``stitch_context`` that no longer validates refuses at launch.

    The refusal is right — a half-resolved binding must never reach a customer
    endpoint. But it left the same dangling ``pending`` run, so the fail-closed
    guard produced a run that reads as still-in-flight.
    """
    from engine import stitch_context as sc  # noqa: PLC0415
    from engine import orchestrator as orch  # noqa: PLC0415

    def _boom(spec, *, seed=None, target=None):
        raise sc.StitchContextValidationError(
            "unknown directive 'teleport'", key="principal", directive="teleport",
        )

    monkeypatch.setattr(sc, "resolve_stitch_context", _boom)

    async def _go():
        async with session_factory() as db:
            await _seed_stitch_scenario(db)
            result = await orch.Orchestrator().launch(
                scenario_id="SIM-EDR-998", mode="pull", db=db, target_agent_id="a1",
            )
            return result, await _run_row(db, result.run_id)

    result, run = asyncio.run(_go())

    assert result.success is False
    assert "STITCH_CONTEXT_INVALID" in result.error
    assert run.status == "failed"
    assert run.status in TERMINAL_STATES
    assert run.completed_at is not None
    assert "STITCH_CONTEXT_INVALID" in (run.output or "")


async def _seed_multichannel_scenario(db, scenario_id="SIM-EDR-997"):
    from models import Scenario  # noqa: PLC0415

    db.add(Scenario(
        scenario_id=scenario_id, name="T", version="1.0", status="active", plane="EDR",
        uc_ref="UCS-EDR-01", uc_name="x", tc_ref="TC-EDR-01", tc_name="y",
        mitre_tactic="TA0006", mitre_tactic_name="Credential Access",
        mitre_technique="T1003", mitre_technique_name="OS Credential Dumping",
        execution_identity={"default": "root"},
        push_supported=True, pull_supported=True,
        steps=[{
            "id": "step-01", "name": "s", "command": "id",
            "target": "phantom-endpoint",
            "expected_detections": [
                {"detection_id": "d-1", "detection_type": "BIOC", "name": "n"},
            ],
        }],
    ))
    await db.commit()


def test_target_not_enrolled_refusal_records_the_reason_on_the_run(session_factory):
    """This path already set ``failed`` inline — but never wrote WHY.

    A hand-rolled copy of the terminalisation drifted from the one every other
    refusal uses: no reason in ``run.output``. An operator reading the run finds
    a failure with no cause, and the HTTP detail that carried the cause is long
    gone.
    """
    from engine import orchestrator as orch  # noqa: PLC0415

    async def _go():
        async with session_factory() as db:
            await _seed_multichannel_scenario(db)
            result = await orch.Orchestrator().launch(
                scenario_id="SIM-EDR-997", mode="pull", db=db, target_agent_id="a1",
            )
            return result, await _run_row(db, result.run_id)

    result, run = asyncio.run(_go())

    assert result.success is False
    assert result.error_code == "TARGET_AGENT_NOT_ENROLLED"
    assert result.error_detail["missing_agents"] == ["phantom-endpoint"]
    assert run.status == "failed"
    assert "TARGET_AGENT_NOT_ENROLLED" in (run.output or "")


def test_target_not_enrolled_refusal_publishes_the_terminal_status(session_factory):
    """...and never told the live stream either, so a console watching the run
    saw it stop at `pending` even though the DB said `failed`."""
    from engine import orchestrator as orch  # noqa: PLC0415

    async def _go():
        from events import event_bus  # noqa: PLC0415

        q = event_bus.subscribe(None)
        try:
            async with session_factory() as db:
                await _seed_multichannel_scenario(db)
                result = await orch.Orchestrator().launch(
                    scenario_id="SIM-EDR-997", mode="pull", db=db, target_agent_id="a1",
                )
            frames = []
            while not q.empty():
                frames.append(q.get_nowait())
            return result, frames
        finally:
            event_bus.unsubscribe(None, q)

    result, frames = asyncio.run(_go())

    statuses = [
        f["data"]["status"] for f in frames
        if f.get("type") == "run.status" and f.get("run_id") == result.run_id
    ]
    assert statuses == ["failed"]

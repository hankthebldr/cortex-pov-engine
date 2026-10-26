"""Terminalise a run whose beacon died mid-run.

When the Cortex XDR agent is not in BAS mode it may kill the beacon itself, so
``/complete`` never arrives and the run would sit in ``running`` forever (the
only other reconciliation is restart-time ``rehydrate()``, which deliberately
leaves a delivered run alone). The heartbeat sweep calls
:func:`terminalise_lost_runs` for every offline agent.

Honesty: a lost beacon is a host witness of *nothing* -- the step was cut short
by something we cannot see. The severed step is stamped ``beacon_lost`` so the
prevention verdict caps at ``pending`` (``PREVENTION_UNCONFIRMED_BEACON_LOST``)
and can never read ``prevented``.

All datetimes are naive UTC, as ``delivered_at`` / ``started_at`` are stored.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional, Sequence

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from engine.host_evidence import apply_host_outcomes
from engine.outcome import (
    TERMINATION_BEACON_LOST,
    TERMINATION_NOT_RUN,
    UPSTREAM_SEVERED,
)
from models import Result, Run, Scenario, StepOutcome

logger = logging.getLogger(__name__)

BEACON_LOST_MARKER = (
    "\n--- BEACON LOST — agent went offline mid-run; "
    "enable BAS mode so only the simulation is terminated ---\n"
)


# -- pure policy -------------------------------------------------------------


def age_reference(delivered_at: datetime,
                  started_ats: Sequence[Optional[datetime]]) -> datetime:
    """Latest step start, else the delivery time."""
    starts = [s for s in started_ats if s is not None]
    return max(starts) if starts else delivered_at


def is_lost(reference: datetime, now: datetime, after_seconds: int) -> bool:
    return (now - reference).total_seconds() >= after_seconds


def pick_severed(outcomes: Sequence[Any], step_ids: Sequence[str]) -> Optional[str]:
    """Step with the latest ``started_at``, else the first step."""
    started = [o for o in outcomes if o.started_at is not None]
    if started:
        return max(started, key=lambda o: o.started_at).step_id
    return step_ids[0] if step_ids else None


def steps_after(step_ids: Sequence[str], severed: str) -> list[str]:
    if severed not in step_ids:
        return []
    return list(step_ids[list(step_ids).index(severed) + 1:])


# -- orchestration -----------------------------------------------------------


async def _scenario_step_ids(db: AsyncSession, run: Run,
                             results: Sequence[Result]) -> list[str]:
    scenario = (await db.execute(
        select(Scenario).where(Scenario.scenario_id == run.scenario_id))).scalar_one_or_none()
    ids = [s.get("id") for s in (scenario.steps or []) if isinstance(s, dict) and s.get("id")] \
        if scenario is not None else []
    if ids:
        return ids
    seen: list[str] = []  # scenario gone: fall back to the seeded Results' order
    for r in results:
        if r.step_id not in seen:
            seen.append(r.step_id)
    return seen


def _upsert(existing: dict[str, StepOutcome], db: AsyncSession, run_id: str, step_id: str,
            now: datetime, **fields: Any) -> StepOutcome:
    row = existing.get(step_id)
    if row is None:
        row = StepOutcome(run_id=run_id, step_id=step_id, recorded_at=now)
        db.add(row)
        existing[step_id] = row
    for k, v in fields.items():
        setattr(row, k, v)
    return row


async def terminalise_lost_runs(db: AsyncSession, agent_id: str, now: datetime,
                                *, after_seconds: int) -> list[str]:
    """Terminalise ``agent_id``'s delivered ``running`` runs that went silent."""
    from api.runs import _safe_publish  # noqa: PLC0415
    from connectors.service import score_run_safely  # noqa: PLC0415

    runs = (await db.execute(
        select(Run).where(Run.status == "running", Run.target == agent_id,
                          Run.delivered_at.is_not(None)))).scalars().all()

    done: list[str] = []
    for run in runs:
        outcomes = (await db.execute(
            select(StepOutcome).where(StepOutcome.run_id == run.run_id)
            .order_by(StepOutcome.id))).scalars().all()
        ref = age_reference(run.delivered_at, [o.started_at for o in outcomes])
        if not is_lost(ref, now, after_seconds):
            continue
        results = (await db.execute(
            select(Result).where(Result.run_id == run.run_id))).scalars().all()
        step_ids = await _scenario_step_ids(db, run, results)
        severed = pick_severed(outcomes, step_ids)
        if severed is None:
            continue

        existing = {o.step_id: o for o in outcomes}
        _upsert(existing, db, run.run_id, severed, now, termination=TERMINATION_BEACON_LOST)
        for sid in steps_after(step_ids, severed):
            row = existing.get(sid)
            if row is not None and row.termination is not None:
                continue  # a decided termination stands
            _upsert(existing, db, run.run_id, sid, now,
                    termination=TERMINATION_NOT_RUN, reason=UPSTREAM_SEVERED)

        apply_host_outcomes(
            results, [o for o in existing.values() if o.termination is not None],
            agent_profile_mode=run.agent_profile_mode)

        run.status = "complete"
        run.completed_at = now
        run.severed_at_step = severed
        await db.execute(
            update(Run).where(Run.run_id == run.run_id)
            .values(output=func.coalesce(Run.output, "") + BEACON_LOST_MARKER)
            .execution_options(synchronize_session=False))
        await db.commit()
        await db.refresh(run)

        await _safe_publish(
            run.run_id,
            {"type": "run.status", "run_id": run.run_id,
             "data": {"status": "complete", "step_id": None}})
        await score_run_safely(db, run, source="beacon_lost")
        logger.warning("beacon_lost run_id=%s agent=%s severed_at=%s",
                       run.run_id, agent_id, severed)
        done.append(run.run_id)
    return done

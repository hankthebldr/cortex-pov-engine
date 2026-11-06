"""Terminalise a run whose beacon died mid-run.

When the Cortex XDR agent is not in BAS mode it may kill the beacon itself, so
``/complete`` never arrives and the run would sit in ``running`` forever (the
only other reconciliation is restart-time ``rehydrate()``, which deliberately
leaves a delivered run alone). The heartbeat sweep calls
:func:`terminalise_lost_runs` for every offline agent.

A second path, :func:`terminalise_abandoned_runs`, covers the beacon that is
alive but no longer working on the run (its ``/complete`` lost across a SimCore
restart): its agent keeps polling ``/tasks``, so the offline sweep never fires,
but the run's own control channel has gone silent (OPS-03).

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
    HOST_WITNESS,
    TERMINATION_BEACON_LOST,
    TERMINATION_NOT_RUN,
    UPSTREAM_SEVERED,
)
from models import Agent, QueuedTask, Result, Run, Scenario, StepOutcome

logger = logging.getLogger(__name__)

BEACON_LOST_MARKER = (
    "\n--- BEACON LOST — agent went offline mid-run; "
    "enable BAS mode so only the simulation is terminated ---\n"
)

UNCLASSIFIED_MARKER = (
    "\n--- BEACON LOST — run could not be classified (no scenario steps, "
    "no results); agent went offline mid-run ---\n"
)


# -- pure policy -------------------------------------------------------------


def age_reference(delivered_at: datetime,
                  started_ats: Sequence[Optional[datetime]],
                  agent_last_seen: Optional[datetime] = None) -> datetime:
    """Latest of step starts, delivery time and the agent's last contact.

    ``last_seen`` is refreshed by ``/tasks`` and by the ``/control`` poll the
    beacon makes every 2 s while a step runs, so a live-but-silent beacon is
    never "lost".
    """
    cands = [s for s in started_ats if s is not None]
    cands.append(delivered_at)
    if agent_last_seen is not None:
        cands.append(agent_last_seen)
    return max(cands)


def is_lost(reference: datetime, now: datetime, after_seconds: int) -> bool:
    return (now - reference).total_seconds() >= after_seconds


def pick_severed(outcomes: Sequence[Any], step_ids: Sequence[str]) -> Optional[str]:
    """The step the beacon died in: the FIRST scenario step with no row.

    ``/output`` writes a StepOutcome row only after a step ends, so the
    in-flight step has none. If every step already has a row, the last step is
    the severed one (its witnessed termination, if any, stands).
    """
    for o in outcomes:  # a host witness already on record stands
        if o.termination in HOST_WITNESS and o.step_id in step_ids:
            return o.step_id
    have = {o.step_id for o in outcomes}
    for sid in step_ids:
        if sid not in have:
            return sid
    return step_ids[-1] if step_ids else None


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


async def _candidates(db: AsyncSession, agent_id: str) -> list[Run]:
    return list((await db.execute(
        select(Run).where(Run.status == "running", Run.target == agent_id,
                          Run.delivered_at.is_not(None)))).scalars().all())


async def _terminalise_run(db: AsyncSession, run: Run, outcomes: Sequence[StepOutcome],
                           now: datetime, *, marker: str, unclassified_marker: str,
                           source: str, frame_extra: Optional[dict[str, Any]] = None,
                           log_what: str = "beacon_lost") -> Optional[str]:
    """Terminalise ONE delivered run whose beacon stopped reporting on it.

    Shared by the offline-agent path (:func:`terminalise_lost_runs`) and the
    abandoned-task path (:func:`terminalise_abandoned_runs`) so the two cannot
    disagree about host evidence: the severed step is stamped ``beacon_lost``
    (a witness of nothing — prevention caps at ``pending``), later steps
    ``not_run`` / ``UPSTREAM_SEVERED``, and a run that cannot be classified is
    failed with its rows stamped ``not_executed``.

    Returns the final status, or ``None`` when a racing ``/complete`` / abort
    got there first (the guarded flip matched nothing and nothing is touched).
    """
    from api.runs import _in_flight_clause, _safe_publish  # noqa: PLC0415
    from connectors.service import score_run_safely  # noqa: PLC0415

    run_id = run.run_id
    results = (await db.execute(
        select(Result).where(Result.run_id == run_id))).scalars().all()
    step_ids = await _scenario_step_ids(db, run, results)
    severed = pick_severed(outcomes, step_ids)

    # FIRST: the guarded atomic flip. A racing /complete or abort that got
    # there first leaves rowcount 0 and we touch nothing.
    values: dict[str, Any] = {
        "completed_at": now,
        "output": func.coalesce(Run.output, "") + (
            marker if severed is not None else unclassified_marker),
    }
    if severed is not None:
        values.update(status="complete", severed_at_step=severed)
    else:
        values.update(status="failed")
    res = await db.execute(
        update(Run).where(Run.run_id == run_id, _in_flight_clause())
        .values(**values).execution_options(synchronize_session=False))
    if res.rowcount == 0:
        await db.rollback()
        return None

    if severed is None:
        # Nothing says which step (if any) ran, so no row may be credited
        # an alert later: the run is failed and its rows leave every
        # denominator (OPS-01), in the same commit as the flip.
        from engine.orchestrator import (  # noqa: PLC0415
            REASON_BEACON_LOST_UNCLASSIFIED,
            stamp_not_executed,
        )

        await stamp_not_executed(db, run_id, REASON_BEACON_LOST_UNCLASSIFIED,
                                 commit=False)
        await db.commit()
        logger.warning("%s run_id=%s agent=%s could not be classified; failed",
                       log_what, run_id, run.target)
        final = "failed"
    else:
        existing = {o.step_id: o for o in outcomes}
        sev_row = existing.get(severed)
        if sev_row is None or sev_row.termination is None:
            _upsert(existing, db, run_id, severed, now, termination=TERMINATION_BEACON_LOST)
        # else: a witnessed termination keeps its witness
        for sid in steps_after(step_ids, severed):
            row = existing.get(sid)
            if row is not None and row.termination is not None:
                continue  # a decided termination stands
            _upsert(existing, db, run_id, sid, now,
                    termination=TERMINATION_NOT_RUN, reason=UPSTREAM_SEVERED)
        apply_host_outcomes(
            results, [o for o in existing.values() if o.termination is not None],
            agent_profile_mode=run.agent_profile_mode)
        await db.commit()
        logger.warning("%s run_id=%s agent=%s severed_at=%s",
                       log_what, run_id, run.target, severed)
        final = "complete"

    await db.refresh(run)
    data: dict[str, Any] = {"status": final, "step_id": None}
    if frame_extra:
        data.update(frame_extra)
    await _safe_publish(run_id, {"type": "run.status", "run_id": run_id, "data": data})
    try:
        from engine.orchestrator import orchestrator  # noqa: PLC0415

        orchestrator.forget_control_poll(run_id)
    except Exception:  # pragma: no cover - defensive
        logger.exception("control-poll cleanup failed run_id=%s", run_id)
    if final == "complete":
        await score_run_safely(db, run, source=source)
    return final


async def terminalise_lost_runs(db: AsyncSession, agent_id: str, now: datetime,
                                *, after_seconds: int) -> list[str]:
    """Terminalise ``agent_id``'s delivered ``running`` runs that went silent."""
    runs = await _candidates(db, agent_id)
    last_seen = (await db.execute(
        select(Agent.last_seen).where(Agent.agent_id == agent_id))).scalar_one_or_none()

    done: list[str] = []
    for run in runs:
        outcomes = (await db.execute(
            select(StepOutcome).where(StepOutcome.run_id == run.run_id)
            .order_by(StepOutcome.id))).scalars().all()
        ref = age_reference(run.delivered_at, [o.started_at for o in outcomes], last_seen)
        if not is_lost(ref, now, after_seconds):
            continue
        final = await _terminalise_run(
            db, run, outcomes, now, marker=BEACON_LOST_MARKER,
            unclassified_marker=UNCLASSIFIED_MARKER, source="beacon_lost")
        if final is not None:
            done.append(run.run_id)
    return done


# -- abandoned tasks (OPS-03) ------------------------------------------------
#
# The offline-agent path above cannot see this one. After a SimCore restart a
# delivered run is (correctly) left `running` by rehydrate(); if its beacon had
# POSTed /complete into the restart window, that message is gone, and the
# beacon goes back to idle-polling /tasks. Its agent's last_seen therefore stays
# fresh forever and the run reads `running` forever: never scored, never
# reported, counted "in flight" by every console surface.
#
# What says "a beacon is executing THIS run" is the run's own control channel:
# GET /api/runs/{id}/control is polled before every step and every 2 s while a
# step runs, by whichever endpoint holds the run (a fan-out included). Silence
# there is necessary but not sufficient: an endpoint staging a large artifact
# polls nothing, and a fan-out's second task may not be collected yet. What
# says "this endpoint has let go" is that endpoint polling /tasks again — the
# beacon runs one task at a time inside its poll loop.

TERMINATION_CODE_ABANDONED = "BEACON_ABANDONED_TASK"


def _abandoned_marker(silent_for: float) -> str:
    return (
        f"\n--- BEACON ABANDONED TASK — the target's beacon is alive (it keeps "
        f"polling for new work) but has not polled this run's control channel "
        f"for {int(silent_for)}s, so it is no longer executing it; its /complete "
        "was most likely lost across a SimCore restart. Steps without host "
        "evidence are not a coverage measurement ---\n"
    )


def _abandoned_unclassified_marker(silent_for: float) -> str:
    return (
        f"\n--- BEACON ABANDONED TASK — run could not be classified (no scenario "
        f"steps, no results); no control poll for {int(silent_for)}s while its "
        "beacon kept polling for new work ---\n"
    )


def abandoned_reference(*, booted_at: datetime, delivered_at: datetime,
                        last_control_poll: Optional[datetime],
                        step_starts: Sequence[Optional[datetime]]) -> datetime:
    """The last moment anything said a beacon was working on the run.

    ``max(boot, delivered_at, last control poll, step /output arrivals)``. Boot
    is in it because the control-poll record is in memory: a restart must
    measure silence from when this process could first have heard a poll,
    never from before it lived. The agent's ``last_seen`` is deliberately NOT
    in it — that is the very signal that stays fresh while the run is dead.
    """
    cands = [booted_at, delivered_at, *[s for s in step_starts if s is not None]]
    if last_control_poll is not None:
        cands.append(last_control_poll)
    return max(cands)


def run_endpoints(run: Run) -> set[str]:
    """Every agent that was handed (or is still owed) one of the run's tasks.

    A single-endpoint run: its launch target. A multi-endpoint fan-out: every
    distinct ``target`` of an agent-channel step in its routing ledger
    (``Run.channel_dispatch``). ``delivered_at`` and ``Run.target`` alone say
    nothing about the second endpoint — the first is stamped by the FIRST
    partition's delivery, the second is only the launch target.
    """
    ledger = run.channel_dispatch or []
    eps = {e["target"] for e in ledger
           if isinstance(e, dict) and e.get("channel") == "agent" and e.get("target")}
    if not eps and run.target:
        eps = {run.target}
    return eps


async def _still_held(db: AsyncSession, run: Run, control_polls: Any) -> Optional[str]:
    """Why some endpoint may still be working on ``run``, or ``None``.

    A beacon executes one task at a time inside its poll loop, so a ``GET
    /tasks`` from an agent after it collected the run's task proves it has let
    go of that partition. The run is abandoned only when no task for it is
    still queued and EVERY endpoint has done so.
    """
    if control_polls.has_queued_task(run.run_id):
        return "a task for it is still queued"
    queued = (await db.execute(
        select(func.count()).select_from(QueuedTask)
        .where(QueuedTask.run_id == run.run_id))).scalar_one()
    if queued:
        return "a task for it is still queued"
    for ep in sorted(run_endpoints(run)):
        if not control_polls.idle_since_delivery(run.run_id, ep):
            return f"endpoint {ep} has not polled for new work since it could have collected its task"
    return None


async def terminalise_abandoned_runs(db: AsyncSession, now: datetime, *,
                                     after_seconds: int,
                                     agent_id: Optional[str] = None,
                                     control_polls: Any = None) -> list[str]:
    """Terminalise delivered ``running`` runs whose beacons all walked away.

    A run qualifies when

    * its launch target is ALIVE (``last_seen`` younger than ``after_seconds``
      — an offline target is :func:`terminalise_lost_runs`'s),
    * nothing has said a beacon is working on it for ``after_seconds``
      (:func:`abandoned_reference`: no control poll, no step start), and
    * nothing still holds it (:func:`_still_held`): no task for it is queued,
      and every endpoint in :func:`run_endpoints` has polled ``/tasks`` since
      it could have collected its task. For a single-endpoint run reaped on
      its own agent's poll that is the poll itself; for a fan-out it is what
      keeps a second endpoint that is still queued, busy with another run, or
      staging a large artifact from having its live partition ended under it.

    It is terminalised through the same machinery as a lost beacon, with a
    ``BEACON ABANDONED TASK`` marker and ``termination: BEACON_ABANDONED_TASK``
    on its ``run.status`` frame.

    ``agent_id`` narrows the sweep to the runs that agent was an endpoint of
    (the /tasks poll path); ``control_polls`` is the orchestrator singleton by
    default — anything exposing ``started_at``, ``last_control_poll(run_id)``,
    ``has_queued_task(run_id)`` and ``idle_since_delivery(run_id, agent_id)``.

    Known limit: an endpoint that collected its task and then died without
    ever polling again leaves the run to the offline-agent path, which looks
    only at the launch target. A fan-out whose SECOND endpoint died therefore
    stays ``running`` until an operator aborts it — the same as before this
    sweep existed, and never a false explanation in the report.
    """
    if control_polls is None:
        from engine.orchestrator import orchestrator as control_polls  # noqa: PLC0415

    q = select(Run).where(Run.status == "running", Run.delivered_at.is_not(None))
    runs = list((await db.execute(q)).scalars().all())
    if agent_id is not None:
        runs = [r for r in runs if agent_id in run_endpoints(r)]

    done: list[str] = []
    for run in runs:
        if not run.target:
            continue
        last_seen = (await db.execute(
            select(Agent.last_seen).where(Agent.agent_id == run.target))).scalar_one_or_none()
        if last_seen is None or (now - last_seen).total_seconds() >= after_seconds:
            continue  # not demonstrably alive: the offline-agent path owns it
        held = await _still_held(db, run, control_polls)
        if held is not None:
            logger.debug("beacon_abandoned skip run_id=%s: %s", run.run_id, held)
            continue
        outcomes = (await db.execute(
            select(StepOutcome).where(StepOutcome.run_id == run.run_id)
            .order_by(StepOutcome.id))).scalars().all()
        ref = abandoned_reference(
            booted_at=control_polls.started_at, delivered_at=run.delivered_at,
            last_control_poll=control_polls.last_control_poll(run.run_id),
            step_starts=[o.started_at for o in outcomes])
        silent_for = (now - ref).total_seconds()
        if silent_for < after_seconds:
            continue
        final = await _terminalise_run(
            db, run, outcomes, now, marker=_abandoned_marker(silent_for),
            unclassified_marker=_abandoned_unclassified_marker(silent_for),
            source="beacon_abandoned",
            frame_extra={"termination": TERMINATION_CODE_ABANDONED},
            log_what="beacon_abandoned")
        if final is not None:
            done.append(run.run_id)
    return done

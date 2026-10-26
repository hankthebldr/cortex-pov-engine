"""
CortexSim API — /api/results router.

Endpoints:
  GET  /api/results              — all detection results
  GET  /api/results/{run_id}     — results for a specific run (with coverage stats)
  PUT  /api/results/{id}/validate — DC marks a detection as observed (sets observed_at for MTTD)
  PUT  /api/results/{id}/notes   — DC adds/updates notes on a result
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from connectors.measures import denominator_exclusion, empty_exclusions
from database import get_db
from engine.outcome import (
    EXCLUDED_FROM_DENOMINATOR,
    OUTCOME_DETECTED,
    OUTCOME_PREVENTED,
    is_hit,
)
from events import event_bus
from models import Result

logger = logging.getLogger("cortexsim.api.results")

router = APIRouter(prefix="/results", tags=["results"])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class ValidateRequest(BaseModel):
    observed: bool
    notes: Optional[str] = None


class NotesRequest(BaseModel):
    notes: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("")
async def list_results(db: AsyncSession = Depends(get_db)):
    """Return all detection results across all runs."""
    stmt = select(Result).order_by(Result.timestamp.desc())
    result = await db.execute(stmt)
    results = result.scalars().all()
    logger.info("list_results count=%d", len(results))
    return {"results": [r.to_dict() for r in results], "total": len(results)}


@router.get("/{run_id}")
async def get_results_for_run(run_id: str, db: AsyncSession = Depends(get_db)):
    """Return all detection results for a specific run_id with coverage statistics."""
    stmt = select(Result).where(Result.run_id == run_id).order_by(Result.step_id, Result.id)
    result = await db.execute(stmt)
    results = result.scalars().all()

    if not results:
        # Verify the run exists to distinguish "no results yet" from "wrong run_id"
        from models import Run  # noqa: PLC0415
        run_result = await db.execute(select(Run).where(Run.run_id == run_id))
        if run_result.scalar_one_or_none() is None:
            raise HTTPException(
                status_code=404,
                detail={"error": "Run not found", "code": "RUN_NOT_FOUND", "detail": f"run_id='{run_id}'"},
            )

    # Coverage over the rows that could have produced evidence. Rows whose step
    # never ran (``not_reached`` / ``not_executed``) and soak-pending
    # Analytics/ABIOC rows leave numerator AND denominator — the one rule,
    # ``engine.outcome.is_in_denominator``, via ``measures.denominator_exclusion``
    # (the same split ``detection_accuracy`` and the run report use). A
    # prevented row is a hit: the agent saw it and stopped it.
    total = len(results)
    excluded = empty_exclusions()
    in_den = []
    for r in results:
        why = denominator_exclusion(r)
        if why is None:
            in_den.append(r)
        else:
            excluded[why] += 1
    denominator = len(in_den)
    # Hits through the one predicate (R1): a ``pending`` row is never a hit,
    # whatever ``observed`` says; ``observed`` decides only without an outcome.
    observed_count = sum(1 for r in results if is_hit(r.outcome, r.observed))
    observed_in_den = sum(1 for r in in_den if is_hit(r.outcome, r.observed))
    prevented = sum(1 for r in in_den if r.outcome == OUTCOME_PREVENTED)
    coverage_pct = round((observed_in_den / denominator * 100), 1) if denominator > 0 else 0.0

    # Per signal-type breakdown, over the same denominator.
    by_type: dict[str, dict[str, int]] = {}
    for r in in_den:
        st = r.signal_type
        if st not in by_type:
            by_type[st] = {"total": 0, "observed": 0}
        by_type[st]["total"] += 1
        if is_hit(r.outcome, r.observed):
            by_type[st]["observed"] += 1

    coverage_by_type = {
        k: {**v, "pct": round(v["observed"] / v["total"] * 100, 1) if v["total"] > 0 else 0.0}
        for k, v in by_type.items()
    }

    # MTTD stats (only for observed results with timing data)
    mttd_values = [r.mttd_seconds for r in results if r.mttd_seconds is not None]
    mttd_stats = None
    if mttd_values:
        mttd_stats = {
            "count": len(mttd_values),
            "avg_seconds": round(sum(mttd_values) / len(mttd_values), 1),
            "min_seconds": round(min(mttd_values), 1),
            "max_seconds": round(max(mttd_values), 1),
        }

    logger.info("get_results run_id=%s count=%d coverage=%.1f%%", run_id, total, coverage_pct)
    return {
        "run_id": run_id,
        "results": [r.to_dict() for r in results],
        "total": total,
        "coverage": {
            "observed": observed_count,
            "total": total,
            "denominator": denominator,
            "observed_in_denominator": observed_in_den,
            "prevented": prevented,
            "excluded": excluded,
            "pct": coverage_pct,
            "by_type": coverage_by_type,
        },
        "mttd": mttd_stats,
    }


async def _has_engine_evidence(db: AsyncSession, result: Result) -> bool:
    """Did a reconcile match an alert to this row? ``apply_verdicts`` records
    one ``Observation`` per (result, alert); an attestation records none."""
    from models import Observation  # noqa: PLC0415

    hit = await db.execute(select(Observation.id).where(
        Observation.result_id == result.id).limit(1))
    return hit.first() is not None


@router.put("/{result_id}/validate")
async def validate_result(
    result_id: int,
    body: ValidateRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    DC marks a detection as observed or not observed -- an operator
    ATTESTATION (residual R5), so it writes the outcome every surface renders.

    * ``observed=true`` on a row whose outcome is None / ``pending`` /
      ``missed`` sets ``outcome = detected`` (reason cleared). The provenance
      stays what the storyline already reads: ``observed_at`` with no matched
      alert. A not-yet-observed row gets ``observed_at = now`` (MTTD); an
      already-observed row keeps its ``observed_at``.
    * ``observed=false`` RETRACTS the attestation: clears ``observed`` and
      ``observed_at``; a ``detected`` the attestation wrote (no matched-alert
      ``Observation``) reverts to None; an engine-written outcome stands. A
      retraction never writes ``missed``.
    * ``observed=true`` on an ``expected_outcome: prevent`` row (any mode):
      409 ``ATTESTATION_NOT_A_WITNESS`` -- a hand mark cannot stand in for the
      alert witness. Re-asserting on an already-observed prevent row updates
      the note only (round 3 R5c).
    * ``not_reached`` / ``not_executed``: 409 ``RESULT_STEP_NOT_RUN`` -- no
      attestation for a step that never ran.
    * ``prevented``: 409 ``RESULT_PREVENTED`` -- an attestation never
      downgrades a two-witness verdict. Re-asserting ``observed=true`` on an
      already-observed prevented row (the console's "save notes") is not an
      attestation: it updates the note and nothing else.
    """
    stmt = select(Result).where(Result.id == result_id)
    row = await db.execute(stmt)
    result: Optional[Result] = row.scalar_one_or_none()

    if result is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "Result not found", "code": "RESULT_NOT_FOUND", "detail": f"id={result_id}"},
        )

    if result.outcome in EXCLUDED_FROM_DENOMINATOR:
        raise HTTPException(status_code=409, detail={
            "error": "Cannot attest a step that never ran",
            "code": "RESULT_STEP_NOT_RUN",
            "detail": f"id={result_id} outcome={result.outcome} reason={result.outcome_reason}",
        })
    if result.outcome == OUTCOME_PREVENTED:
        if not (body.observed and result.observed):
            raise HTTPException(status_code=409, detail={
                "error": "An attestation never downgrades a two-witness prevented verdict",
                "code": "RESULT_PREVENTED",
                "detail": f"id={result_id} outcome=prevented",
            })
        if body.notes is not None:
            result.notes = body.notes
            await db.commit()
        return result.to_dict()

    if body.observed and result.expected_outcome == "prevent":
        if result.observed:
            # Re-asserting on an already-observed prevent row (the console's
            # "save notes") attests nothing: the note, and only the note.
            if body.notes is not None:
                result.notes = body.notes
                await db.commit()
            return result.to_dict()
        raise HTTPException(status_code=409, detail={
            "error": ("A manual attestation cannot stand in for the alert witness; "
                      "reconcile against the tenant"),
            "code": "ATTESTATION_NOT_A_WITNESS",
            "detail": f"id={result_id} expected_outcome=prevent outcome={result.outcome}",
        })

    # Re-asserting `observed: true` on an already-observed row is idempotent:
    # it must NOT move observed_at. The console's "save notes" sends exactly
    # that call, and re-stamping replaced a reconcile-matched alert time (real
    # MTTD) with the moment a DC typed a note — the POV report's MTTD, and the
    # next score against a `<= N s` threshold, then measured the typing.
    already_observed = bool(result.observed) and result.observed_at is not None
    result.observed = body.observed
    if body.observed:
        if not already_observed:
            result.observed_at = datetime.utcnow()
        if result.outcome != OUTCOME_DETECTED:
            result.outcome = OUTCOME_DETECTED
            result.outcome_reason = None
    else:
        # A retraction (round 3 R5b): it withdraws the operator's attestation
        # and nothing else. A `detected` with no matched-alert Observation was
        # written BY an attestation -> back to unjudged (the next reconcile
        # re-judges). An engine-written outcome (an alert matched, or R7's miss)
        # stands. A retraction never writes `missed`.
        if result.outcome == OUTCOME_DETECTED and not await _has_engine_evidence(db, result):
            result.outcome = None
            result.outcome_reason = None
        result.observed_at = None

    if body.notes is not None:
        result.notes = body.notes

    await db.commit()

    # Publish a live detection event so the run's SSE stream shows the
    # confirmation in real time. Never let a bus error fail the validate.
    try:
        await event_bus.publish(
            result.run_id,
            {
                "type": "result.observed",
                "run_id": result.run_id,
                "data": {
                    "result_id": result.id,
                    "observed": result.observed,
                    "outcome": result.outcome,
                    "plane": result.plane,
                    "mttd_seconds": result.mttd_seconds,
                },
            },
        )
    except Exception:  # pragma: no cover - defensive
        logger.exception("event_bus publish failed result_id=%d", result_id)

    logger.info(
        "validate result_id=%d observed=%s mttd=%s",
        result_id,
        body.observed,
        result.mttd_seconds,
    )
    return result.to_dict()


@router.put("/{result_id}/notes")
async def update_notes(
    result_id: int,
    body: NotesRequest,
    db: AsyncSession = Depends(get_db),
):
    """Update notes on a detection result."""
    stmt = select(Result).where(Result.id == result_id)
    row = await db.execute(stmt)
    result: Optional[Result] = row.scalar_one_or_none()

    if result is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "Result not found", "code": "RESULT_NOT_FOUND", "detail": f"id={result_id}"},
        )

    result.notes = body.notes
    await db.commit()

    logger.info("notes updated result_id=%d", result_id)
    return result.to_dict()

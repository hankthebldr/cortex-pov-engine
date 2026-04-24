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

from database import get_db
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

    # Compute coverage stats
    total = len(results)
    observed_count = sum(1 for r in results if r.observed)
    coverage_pct = round((observed_count / total * 100), 1) if total > 0 else 0.0

    # Per signal-type breakdown
    by_type: dict[str, dict[str, int]] = {}
    for r in results:
        st = r.signal_type
        if st not in by_type:
            by_type[st] = {"total": 0, "observed": 0}
        by_type[st]["total"] += 1
        if r.observed:
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
            "pct": coverage_pct,
            "by_type": coverage_by_type,
        },
        "mttd": mttd_stats,
    }


@router.put("/{result_id}/validate")
async def validate_result(
    result_id: int,
    body: ValidateRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    DC marks a detection as observed or not observed.
    When observed=true, sets observed_at to now (enabling MTTD calculation).
    When observed=false, clears observed_at.
    """
    stmt = select(Result).where(Result.id == result_id)
    row = await db.execute(stmt)
    result: Optional[Result] = row.scalar_one_or_none()

    if result is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "Result not found", "code": "RESULT_NOT_FOUND", "detail": f"id={result_id}"},
        )

    result.observed = body.observed
    if body.observed:
        result.observed_at = datetime.utcnow()
    else:
        result.observed_at = None

    if body.notes is not None:
        result.notes = body.notes

    await db.commit()

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

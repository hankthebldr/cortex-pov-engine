"""
CortexSim API — /api/runs/{run_id}/storyline router (Detection Proof Layer).

Endpoints:
  GET  /api/runs/{run_id}/storyline   — assemble the per-run Detection Storyline

The Detection Storyline is the *spine* of the Detection Proof Layer: one ordered
kill-chain where each entry is ``step → expected detection → observed Cortex
alert → real MTTD → persisted evidence``. The SAME structure drives both the
live presenter timeline and the executive scorecard.

This router is a thin, read-only HTTP face over the pure builder in
``engine.detection_storyline`` (Detection Proof Layer module A). It loads the run +
scenario + seeded ``Result`` rows from the DB (async; mirrors ``api.runs``
exactly) and hands their ``.to_dict()`` projections to
``build_storyline(run_dict, results, scenario_steps)``. The builder imports no
ORM/session — everything it needs is already on the dicts SimCore emits today
(``Run.to_dict`` / ``Result.to_dict`` / ``Scenario.steps``), so this endpoint is
a pure DB-read + serialize.

Design rules honoured:
  * All responses are structured JSON — including errors:
    ``{"error": ..., "code": ..., "detail": ...}`` (404 on unknown run, 503 when
    the storyline engine module has not landed yet, 500 on a builder fault).
  * Zero edits to existing files — this is a new router registered in
    ``core/main.py`` alongside the other ``/runs``-prefixed routers (see the
    connector ``runs_reconcile_router`` precedent, which already mounts a second
    router under the ``/runs`` prefix).

--------------------------------------------------------------------------------
Live variant — how this maps onto the existing SSE stream
--------------------------------------------------------------------------------
This endpoint is the *snapshot* face of the storyline. The *live* face is the
already-shipped SSE stream at ``GET /api/runs/{run_id}/events`` (scoped) and
``GET /api/events`` (global), served by ``core/api/events.py`` over the
``core/events.py`` ``event_bus``.

The UI store (module E ``useDetectionStoryline``) folds the same storyline shape
in real time by subscribing to that SSE stream and applying incremental frames:

    SSE frame (type)      → storyline mutation
    ─────────────────────────────────────────────────────────────────────
    run.step (start/done) → StorylineStep lights at step-start / marks done
    result.observed       → the targeted StorylineDetection flips
                            pending → detected, stamping observed_at + real
                            MTTD + the Cortex-engine badge (detection_type)
    run.coverage          → DetectionStoryline.coverage {detected, expected, pct}
    run.reconcile         → "Cortex analyzing — K of M pending" heartbeat
    run.status            → terminal run state (complete/failed/aborted)

So the contract is: **call this endpoint once for the authoritative snapshot
(initial paint + degraded poll-fallback), then let the SSE stream drive
incremental updates.** Both read the identical module-A JSON shape, so a client
can reconcile a reconnect by re-fetching this endpoint and replaying the SSE
backfill (``?since`` / ``Last-Event-ID``) without a schema mismatch. This
endpoint deliberately performs no streaming itself — separation of concerns
keeps it cache-friendly and trivially testable.
"""

from __future__ import annotations

import importlib
import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import Result, Run, Scenario

logger = logging.getLogger("cortexsim.api.storyline")

# Mounted under the conventional ``/runs`` prefix so the storyline endpoint sits
# beside the rest of the run surface at ``/api/runs/{run_id}/storyline`` — the
# same pattern the connector reconcile router uses.
router = APIRouter(prefix="/runs", tags=["storyline"])


def _serialize_storyline(obj: Any) -> dict[str, Any]:
    """Coerce whatever ``build_storyline`` returns into a plain JSON dict.

    Module A may ship a frozen dataclass, a Pydantic v2 model, or already a
    dict — this endpoint codes against the *frozen JSON shape*, not a concrete
    type, so we accept any of them defensively:

      * ``dict``                       → returned as-is
      * ``.to_dict()`` (repo convention, matches every ORM model + the
        scorecard/evidence models) → called first
      * ``.model_dump()`` (Pydantic v2) → fallback
      * ``dataclasses.asdict``          → fallback for a plain frozen dataclass
    """
    if isinstance(obj, dict):
        return obj

    to_dict = getattr(obj, "to_dict", None)
    if callable(to_dict):
        return to_dict()

    model_dump = getattr(obj, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")

    import dataclasses  # noqa: PLC0415

    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)

    raise TypeError(
        f"build_storyline returned an unserializable type: {type(obj)!r}"
    )


def _load_build_storyline():
    """Lazily resolve ``engine.detection_storyline.build_storyline``.

    Deferred to call time (not module import) so this router — and therefore the
    whole app — boots cleanly even before Detection Proof Layer module A has
    landed on disk. When the engine module is absent, callers get a structured
    503 rather than an import-time crash that would take down every route.
    """
    try:
        storyline_engine = importlib.import_module("engine.detection_storyline")
    except ImportError as exc:  # module A not present yet
        logger.warning("storyline engine unavailable: %s", exc)
        raise HTTPException(
            status_code=503,
            detail={
                "error": "Detection Storyline engine not available",
                "code": "STORYLINE_ENGINE_UNAVAILABLE",
                "detail": "engine.detection_storyline could not be imported on this build.",
            },
        )

    builder = getattr(storyline_engine, "build_storyline", None)
    if not callable(builder):
        logger.error("engine.detection_storyline has no callable build_storyline")
        raise HTTPException(
            status_code=503,
            detail={
                "error": "Detection Storyline engine incomplete",
                "code": "STORYLINE_ENGINE_UNAVAILABLE",
                "detail": "engine.detection_storyline lacks a callable build_storyline.",
            },
        )
    return builder


@router.get("/{run_id}/storyline")
async def get_storyline(run_id: str, db: AsyncSession = Depends(get_db)):
    """Assemble and return the Detection Storyline for a single run.

    Loads the run, its scenario (for the ordered step spec), and every seeded
    ``Result`` row, then delegates to the pure ``build_storyline`` builder.
    Read-only — no DB mutation, no outbound tenant calls.
    """
    # 1. Run — 404 if unknown (same envelope as api.runs).
    run_result = await db.execute(select(Run).where(Run.run_id == run_id))
    run: Optional[Run] = run_result.scalar_one_or_none()
    if run is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "Run not found", "code": "RUN_NOT_FOUND",
                    "detail": f"run_id='{run_id}'"},
        )

    # 2. Scenario — optional (a run whose scenario was removed still yields a
    #    storyline; the builder tolerates an empty step spec).
    scen_result = await db.execute(
        select(Scenario).where(Scenario.scenario_id == run.scenario_id),
    )
    scenario: Optional[Scenario] = scen_result.scalar_one_or_none()
    scenario_steps: list[dict] = list(scenario.steps or []) if scenario else []

    # 3. Results — ordered exactly like the report endpoints (step then id).
    res_result = await db.execute(
        select(Result).where(Result.run_id == run_id)
                      .order_by(Result.step_id, Result.id),
    )
    results = res_result.scalars().all()

    # 4. Resolve the builder (structured 503 if module A is absent), then build.
    builder = _load_build_storyline()
    try:
        storyline = builder(
            run.to_dict(),
            [r.to_dict() for r in results],
            scenario_steps,
        )
        payload = _serialize_storyline(storyline)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — surface a structured 500, never a stack
        logger.exception("build_storyline failed run_id=%s", run_id)
        raise HTTPException(
            status_code=500,
            detail={"error": "Failed to build detection storyline",
                    "code": "STORYLINE_BUILD_FAILED", "detail": str(exc)},
        )

    logger.info("storyline built run_id=%s steps=%d results=%d",
                run_id, len(scenario_steps), len(results))
    return {"detection_storyline": payload}

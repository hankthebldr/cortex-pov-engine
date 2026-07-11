"""
CortexSim API — /api/runs/{run_id}/causality router (Causality Graph).

Endpoints:
  GET  /api/runs/{run_id}/causality   — assemble the per-run Causality Graph

The Causality Graph is the **third projection** of the same seeded-``Result``
denominator the Detection Storyline and Efficacy Scorecard already share. Where
the storyline renders detections as a flat, ordered checklist, the causality
graph promotes each storyline entry to an *alert node*, synthesizes one
Causality-Group-Owner (CGO) root per run (modeling ``xdr_data``'s
``causality_id``), and derives the typed edges — ``process_lineage``,
``network_session``, endpoint↔network stitch, ``temporal``, ``shared_entity``,
``sequence`` and ``exposure_exploit`` — from the always-offline step spec
(identity-harness resolution + command head-binary + scenario order + the
``control_layer``/``asset_ref`` metadata) as EXPECTED edges, UPGRADED to
CONFIRMED (or flagged BROKEN outside window) by matcher verdicts.

This router is a thin, read-only HTTP face over the pure builder in
``engine.causality_graph`` (Causality-Graph module A). It loads the run +
scenario + seeded ``Result`` rows from the DB (async; mirrors ``api.storyline``
exactly) and hands their ``.to_dict()`` projections — plus the parsed scenario
stitch metadata (``stitching_key`` / ``correlation_window_seconds`` /
``required_planes_in_incident`` and any top-level ``causality:`` block) — to
``build_causality_graph(run_dict, results, scenario_steps, scenario_meta=...)``.
The builder imports no ORM/session and delegates coverage/MTTD to
``detection_storyline.build_summary`` so the timeline, scorecard and graph can
never diverge — this endpoint is a pure DB-read + serialize.

Design rules honoured:
  * All responses are structured JSON — including errors:
    ``{"error": ..., "code": ..., "detail": ...}`` (404 on unknown run, 503 when
    the causality engine module has not landed yet, 500 on a builder fault).
  * Zero edits to existing files — this is a new router registered in
    ``core/main.py`` alongside the other ``/runs``-prefixed routers (see the
    ``storyline_router`` precedent, which already mounts a second router under
    the ``/runs`` prefix).

--------------------------------------------------------------------------------
Live variant — how this maps onto the existing SSE stream
--------------------------------------------------------------------------------
This endpoint is the *snapshot* face of the causality graph. The *live* face is
the already-shipped SSE stream at ``GET /api/runs/{run_id}/events`` (scoped) and
``GET /api/events`` (global). The React ``CausalityGraph`` view folds the same
graph shape in real time by subscribing to that SSE stream — exactly as
``DetectionStoryline`` does — flipping edges EXPECTED → CONFIRMED (or BROKEN) as
``result.observed`` frames arrive. So the contract is: **call this endpoint once
for the authoritative snapshot, then let the SSE stream drive incremental edge
upgrades.** This endpoint deliberately performs no streaming itself.
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

logger = logging.getLogger("cortexsim.api.causality")

# Mounted under the conventional ``/runs`` prefix so the causality endpoint sits
# beside the rest of the run surface at ``/api/runs/{run_id}/causality`` — the
# same pattern the storyline router uses.
router = APIRouter(prefix="/runs", tags=["causality"])

# The scenario keys that carry the stitch contract the graph derives its
# cross-plane edges from. Currently declared in scenario YAML (mp-001:73-77) and,
# per the loader integration, persisted onto the Scenario ORM. Read defensively
# via ``getattr`` so this endpoint works whether or not those columns have landed
# on this build — the builder tolerates a missing/empty ``scenario_meta`` (edges
# simply stay EXPECTED).
_STITCH_META_KEYS = (
    "stitching_key",
    "correlation_window_seconds",
    "required_planes_in_incident",
    "causality",
)


def _serialize_graph(obj: Any) -> dict[str, Any]:
    """Coerce whatever ``build_causality_graph`` returns into a plain JSON dict.

    Module A may ship a frozen dataclass, a Pydantic v2 model, or already a
    dict — this endpoint codes against the *frozen JSON shape*, not a concrete
    type, so we accept any of them defensively:

      * ``dict``                       → returned as-is
      * ``.to_dict()`` (repo convention, matches every ORM model + the
        storyline/scorecard models) → called first
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
        f"build_causality_graph returned an unserializable type: {type(obj)!r}"
    )


def _scenario_meta(scenario: Optional[Scenario]) -> dict[str, Any]:
    """Extract the parsed stitch/causality metadata the builder derives edges from.

    Pulled defensively from whatever the Scenario ORM exposes (``getattr`` with a
    ``None`` fallback) so the endpoint is forward-compatible with the loader that
    persists ``stitching_key`` / ``correlation_window_seconds`` /
    ``required_planes_in_incident`` / the top-level ``causality:`` block. Only
    keys that are actually present (non-``None``) are forwarded; the ``plane`` is
    always included as the graph's lane hint when a scenario is known.
    """
    if scenario is None:
        return {}
    meta: dict[str, Any] = {}
    plane = getattr(scenario, "plane", None)
    if plane is not None:
        meta["plane"] = plane
    for key in _STITCH_META_KEYS:
        value = getattr(scenario, key, None)
        if value is not None:
            meta[key] = value
    return meta


def _load_build_causality_graph():
    """Lazily resolve ``engine.causality_graph.build_causality_graph``.

    Deferred to call time (not module import) so this router — and therefore the
    whole app — boots cleanly even before Causality-Graph module A has landed on
    disk. When the engine module is absent, callers get a structured 503 rather
    than an import-time crash that would take down every route.
    """
    try:
        causality_engine = importlib.import_module("engine.causality_graph")
    except ImportError as exc:  # module A not present yet
        logger.warning("causality engine unavailable: %s", exc)
        raise HTTPException(
            status_code=503,
            detail={
                "error": "Causality Graph engine not available",
                "code": "CAUSALITY_ENGINE_UNAVAILABLE",
                "detail": "engine.causality_graph could not be imported on this build.",
            },
        )

    builder = getattr(causality_engine, "build_causality_graph", None)
    if not callable(builder):
        logger.error("engine.causality_graph has no callable build_causality_graph")
        raise HTTPException(
            status_code=503,
            detail={
                "error": "Causality Graph engine incomplete",
                "code": "CAUSALITY_ENGINE_UNAVAILABLE",
                "detail": "engine.causality_graph lacks a callable build_causality_graph.",
            },
        )
    return builder


@router.get("/{run_id}/causality")
async def get_causality(run_id: str, db: AsyncSession = Depends(get_db)):
    """Assemble and return the Causality Graph for a single run.

    Loads the run, its scenario (for the ordered step spec + parsed stitch
    metadata), and every seeded ``Result`` row, then delegates to the pure
    ``build_causality_graph`` builder. Read-only — no DB mutation, no outbound
    tenant calls.
    """
    # 1. Run — 404 if unknown (same envelope as api.storyline / api.runs).
    run_result = await db.execute(select(Run).where(Run.run_id == run_id))
    run: Optional[Run] = run_result.scalar_one_or_none()
    if run is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "Run not found", "code": "RUN_NOT_FOUND",
                    "detail": f"run_id='{run_id}'"},
        )

    # 2. Scenario — optional (a run whose scenario was removed still yields a
    #    graph; the builder tolerates an empty step spec + empty stitch meta).
    scen_result = await db.execute(
        select(Scenario).where(Scenario.scenario_id == run.scenario_id),
    )
    scenario: Optional[Scenario] = scen_result.scalar_one_or_none()
    scenario_steps: list[dict] = list(scenario.steps or []) if scenario else []
    scenario_meta = _scenario_meta(scenario)

    # 3. Results — ordered exactly like the storyline / report endpoints.
    res_result = await db.execute(
        select(Result).where(Result.run_id == run_id)
                      .order_by(Result.step_id, Result.id),
    )
    results = res_result.scalars().all()

    # 4. Resolve the builder (structured 503 if module A is absent), then build.
    builder = _load_build_causality_graph()
    try:
        graph = builder(
            run.to_dict(),
            [r.to_dict() for r in results],
            scenario_steps,
            scenario_meta=scenario_meta,
        )
        payload = _serialize_graph(graph)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — surface a structured 500, never a stack
        logger.exception("build_causality_graph failed run_id=%s", run_id)
        raise HTTPException(
            status_code=500,
            detail={"error": "Failed to build causality graph",
                    "code": "CAUSALITY_BUILD_FAILED", "detail": str(exc)},
        )

    logger.info("causality graph built run_id=%s steps=%d results=%d",
                run_id, len(scenario_steps), len(results))
    return {"causality_graph": payload}

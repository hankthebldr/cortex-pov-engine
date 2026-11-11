"""
CortexSim API — dataset prerequisites.

  GET /api/prerequisites/datasets                — the registry, joined to the cards that read each dataset
  GET /api/scenarios/{scenario_id}/prerequisites — what one scenario needs on the Cortex and source side

Read-only, local files and the scenario table only: no outbound call, no tenant claim
(``tenant_verified`` is 0). An absent registry answers 200 with ``registry_loaded: false``;
a registry that exists but is malformed answers a structured 500, never a 200 that hides it.

Mount this router BEFORE ``scenarios_router`` in main.py so the sub-path is not swallowed.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from engine.dataset_requirements import (
    DatasetRequirementsError,
    card_datasets,
    load,
    registry_source,
)
from engine.prerequisites import derive
from engine.ttp_catalog import catalog
from models import Scenario

logger = logging.getLogger("cortexsim.api.prerequisites")

router = APIRouter(tags=["prerequisites"])


def _card_shape(exc: Exception) -> JSONResponse:
    logger.error("card shape unrecognised: %s", exc)
    return JSONResponse(
        status_code=500,
        content={"error": "A detection card or scenario step has an unrecognised shape",
                 "code": "CARD_SHAPE_UNRECOGNISED", "detail": str(exc)},
    )


def _malformed(exc: Exception) -> JSONResponse:
    logger.error("dataset requirement registry malformed: %s", exc)
    return JSONResponse(
        status_code=500,
        content={
            "error": "Dataset requirement registry is malformed",
            "code": "DATASET_REGISTRY_MALFORMED",
            "detail": str(exc),
        },
    )


@router.get("/prerequisites/datasets")
async def list_dataset_requirements():
    try:
        registry = load()
        source = registry_source()
    except DatasetRequirementsError as exc:
        return _malformed(exc)
    try:
        by_card = card_datasets(catalog.all_raw())
    except DatasetRequirementsError as exc:
        return _card_shape(exc)
    rows = []
    for name in sorted(registry or {}):
        row = registry[name].to_dict()
        row["cards"] = sorted(by_card.get(name, ()))
        rows.append(row)
    return {
        "registry_loaded": registry is not None,
        "path": source["path"],
        "count": len(rows),
        "datasets": rows,
        # Computed here, not via check_datasets_registered: that one raises under the strict
        # flag, which is a boot decision, not something a read route may turn into a 500.
        "unregistered": sorted(n for n in by_card if registry is None or n not in registry),
    }


@router.get("/scenarios/{scenario_id}/prerequisites")
async def get_scenario_prerequisites(scenario_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Scenario).where(Scenario.scenario_id == scenario_id))
    scenario: Optional[Scenario] = result.scalar_one_or_none()
    if scenario is None:
        return JSONResponse(
            status_code=404,
            content={"error": "Scenario not found", "code": "SCENARIO_NOT_FOUND",
                     "detail": f"scenario_id='{scenario_id}'"},
        )
    try:
        registry = load()
    except DatasetRequirementsError as exc:
        return _malformed(exc)
    try:
        return derive(scenario.to_dict(), catalog.all_raw(), registry)
    except (DatasetRequirementsError, ValueError) as exc:
        return _card_shape(exc)

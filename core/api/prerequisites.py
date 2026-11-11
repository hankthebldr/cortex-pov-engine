"""
CortexSim API — dataset prerequisites.

  GET /api/prerequisites/datasets                — the registry, joined to the cards that read each dataset
  GET /api/scenarios/{scenario_id}/prerequisites — what one scenario needs on the Cortex and source side
  GET /api/scenarios/{scenario_id}/query-library — the scenario's hunt queries as paste-ready text

Read-only, local files and the scenario table only: no outbound call, no tenant claim
(``tenant_verified`` is 0). An absent registry answers 200 with ``registry_loaded: false``;
a registry that exists but is malformed answers a structured 500, never a 200 that hides it.

Mount this router BEFORE ``scenarios_router`` in main.py so the sub-path is not swallowed.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from engine.dataset_requirements import (
    DatasetRequirementsError,
    card_dataset_index,
    load,
    registry_source,
)
from engine.prerequisites import derive
from engine.query_library import ALLOWED_PURPOSES, render as render_query_library
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
        index = card_dataset_index(catalog.all_raw())
    except DatasetRequirementsError as exc:
        return _card_shape(exc)
    rows = []
    for name in sorted(registry or {}):
        row = registry[name].to_dict()
        readers = index.get(name, {})
        row["cards"] = sorted(readers)
        row["read_by"] = sorted(set().union(*readers.values())) if readers else []
        rows.append(row)
    return {
        "registry_loaded": registry is not None,
        "path": source["path"],
        "count": len(rows),
        "datasets": rows,
        # Computed here, not via check_datasets_registered: that one raises under the strict
        # flag, which is a boot decision, not something a read route may turn into a 500.
        "unregistered": sorted(n for n in index if registry is None or n not in registry),
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


@router.get("/scenarios/{scenario_id}/query-library")
async def get_scenario_query_library(
    scenario_id: str,
    purpose: str = Query("hunt,investigation"),
    db: AsyncSession = Depends(get_db),
):
    purposes = tuple(p.strip() for p in purpose.split(","))
    if any(p not in ALLOWED_PURPOSES for p in purposes) or len(set(purposes)) != len(purposes):
        return JSONResponse(
            status_code=422,
            content={"error": "purpose must be a comma-separated list of distinct known purposes",
                     "code": "PURPOSE_UNRECOGNISED",
                     "detail": f"purpose={purpose!r}; allowed: {', '.join(ALLOWED_PURPOSES)}"},
        )
    result = await db.execute(select(Scenario).where(Scenario.scenario_id == scenario_id))
    scenario: Optional[Scenario] = result.scalar_one_or_none()
    if scenario is None:
        return JSONResponse(
            status_code=404,
            content={"error": "Scenario not found", "code": "SCENARIO_NOT_FOUND",
                     "detail": f"scenario_id='{scenario_id}'"},
        )
    try:
        text = render_query_library(scenario.to_dict(), catalog.all_raw(), purposes)
    except ValueError as exc:
        return _card_shape(exc)
    return PlainTextResponse(text, media_type="text/plain; charset=utf-8")

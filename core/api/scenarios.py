"""
CortexSim API — /api/scenarios router.

Endpoints:
  GET  /api/scenarios                          — list all (optional ?plane= and ?uc_ref= filters)
  GET  /api/scenarios/{scenario_id}            — single scenario detail
  GET  /api/scenarios/{scenario_id}/download   — download bash or K8s bundle
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from engine.push_generator import generate_bash, generate_k8s
from models import Scenario

logger = logging.getLogger("cortexsim.api.scenarios")

router = APIRouter(prefix="/scenarios", tags=["scenarios"])


@router.get("")
async def list_scenarios(
    plane: Optional[str] = Query(None, description="Filter by detection plane (e.g. CDR)"),
    uc_ref: Optional[str] = Query(None, description="Filter by UC reference (e.g. UCS-CDR-03)"),
    db: AsyncSession = Depends(get_db),
):
    """List all scenarios, with optional plane and uc_ref filters."""
    stmt = select(Scenario)
    if plane:
        stmt = stmt.where(Scenario.plane == plane.upper())
    if uc_ref:
        stmt = stmt.where(Scenario.uc_ref == uc_ref)

    result = await db.execute(stmt)
    scenarios = result.scalars().all()

    logger.info("list_scenarios plane=%s uc_ref=%s count=%d", plane, uc_ref, len(scenarios))
    return {"scenarios": [s.to_dict() for s in scenarios], "total": len(scenarios)}


@router.get("/{scenario_id}")
async def get_scenario(
    scenario_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Return full detail for a single scenario."""
    result = await db.execute(
        select(Scenario).where(Scenario.scenario_id == scenario_id)
    )
    scenario: Optional[Scenario] = result.scalar_one_or_none()
    if scenario is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "Scenario not found", "code": "SCENARIO_NOT_FOUND", "detail": f"scenario_id='{scenario_id}'"},
        )

    logger.info("get_scenario scenario_id=%s", scenario_id)
    return scenario.to_dict()


@router.get("/{scenario_id}/download")
async def download_bundle(
    scenario_id: str,
    format: str = Query("bash", description="Output format: bash | k8s"),
    db: AsyncSession = Depends(get_db),
):
    """
    Generate and download a self-contained execution bundle.
    format=bash  → shell script
    format=k8s   → Kubernetes YAML manifest
    """
    result = await db.execute(
        select(Scenario).where(Scenario.scenario_id == scenario_id)
    )
    scenario: Optional[Scenario] = result.scalar_one_or_none()
    if scenario is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "Scenario not found", "code": "SCENARIO_NOT_FOUND", "detail": f"scenario_id='{scenario_id}'"},
        )

    scenario_dict = scenario.to_dict()

    if format == "bash":
        content = generate_bash(scenario_dict)
        filename = f"cortexsim-{scenario_id}.sh"
        media_type = "text/x-shellscript"
    elif format == "k8s":
        content = generate_k8s(scenario_dict)
        filename = f"cortexsim-{scenario_id}-k8s.yaml"
        media_type = "application/x-yaml"
    else:
        raise HTTPException(
            status_code=400,
            detail={"error": "Invalid format", "code": "INVALID_FORMAT", "detail": "format must be 'bash' or 'k8s'"},
        )

    logger.info("download_bundle scenario_id=%s format=%s", scenario_id, format)
    return PlainTextResponse(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

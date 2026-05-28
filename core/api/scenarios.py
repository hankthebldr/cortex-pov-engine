"""
CortexSim API — /api/scenarios router.

Endpoints:
  GET  /api/scenarios                            — list all (optional ?plane= and ?uc_ref= filters)
  GET  /api/scenarios/{scenario_id}              — single scenario detail
  GET  /api/scenarios/{scenario_id}/infra-hints  — adapter_refs + iac_modules a scenario implies
  GET  /api/scenarios/{scenario_id}/download     — download bash or K8s bundle
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
from tools.adapter_catalog import catalog as adapter_catalog

logger = logging.getLogger("cortexsim.api.scenarios")

router = APIRouter(prefix="/scenarios", tags=["scenarios"])


@router.get("")
async def list_scenarios(
    plane: Optional[str] = Query(None, description="Filter by detection plane (e.g. CDR)"),
    uc_ref: Optional[str] = Query(None, description="Filter by UC reference (e.g. UCS-CDR-03)"),
    ttp_ref: Optional[str] = Query(None, description="Filter to scenarios whose steps[].expected_detections[].ttp_ref cites this TTP id"),
    db: AsyncSession = Depends(get_db),
):
    """List all scenarios, with optional plane / uc_ref / ttp_ref filters."""
    stmt = select(Scenario)
    if plane:
        stmt = stmt.where(Scenario.plane == plane.upper())
    if uc_ref:
        stmt = stmt.where(Scenario.uc_ref == uc_ref)

    result = await db.execute(stmt)
    scenarios = result.scalars().all()

    # ttp_ref filter — applied in Python because expected_detections is
    # nested in a JSON column and SQLite has no portable accessor for it.
    # The scenario catalog is small (~50 today) so a full scan is fine.
    if ttp_ref:
        scenarios = [s for s in scenarios if _scenario_cites_ttp(s, ttp_ref)]

    logger.info(
        "list_scenarios plane=%s uc_ref=%s ttp_ref=%s count=%d",
        plane, uc_ref, ttp_ref, len(scenarios),
    )
    return {"scenarios": [s.to_dict() for s in scenarios], "total": len(scenarios)}


def _scenario_cites_ttp(scenario: Scenario, ttp_ref: str) -> bool:
    """Return True if any step's expected_detections cites ``ttp_ref``."""
    for step in (scenario.steps or []):
        if not isinstance(step, dict):
            continue
        for det in (step.get("expected_detections") or []):
            if isinstance(det, dict) and det.get("ttp_ref") == ttp_ref:
                return True
    return False


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


@router.get("/{scenario_id}/infra-hints")
async def get_infra_hints(
    scenario_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Resolve a scenario's ``external_tools[]`` into IaC generator hints.

    Walks each entry's ``adapter_ref``, looks it up in the catalog, and
    returns:

      * ``adapter_refs``       — full list of refs the scenario declared
      * ``resolved_adapters``  — adapters that resolved (with name, tier,
                                 safety_class, iac_module if any)
      * ``unresolved_refs``    — refs the catalog rejected (stale ids,
                                 typos) — surfaced so the operator can
                                 see the gap in the UI
      * ``suggested_modules``  — unioned set of ``install.iac_module``
                                 values from resolved adapters, deduped
                                 + sorted. Plug straight into
                                 ``/api/infra/generate?modules=...``

    UI workflow: DC opens the Lab view, picks a scenario_id, hits this
    endpoint, and the LabView auto-fills the modules + tool-adapters
    pickers. The actual generation goes through ``/api/infra/generate``
    with the same ``adapter_refs[]`` so PR #48's auto-pull provenance
    trail (ADAPTERS.md) lights up.
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

    adapter_refs: list[str] = []
    resolved: list[dict] = []
    unresolved: list[str] = []
    modules: list[str] = []

    for entry in (scenario.external_tools or []):
        if not isinstance(entry, dict):
            continue
        ref = entry.get("adapter_ref")
        if not ref:
            continue
        adapter_refs.append(ref)
        adapter = adapter_catalog.find(ref)
        if adapter is None:
            unresolved.append(ref)
            continue
        iac_module = adapter.install.iac_module
        resolved.append({
            "adapter_ref":  ref,
            "name":         adapter.name,
            "tier":         adapter.tier,
            "safety_class": adapter.safety_class,
            "iac_module":   iac_module,
        })
        if iac_module and iac_module not in modules:
            modules.append(iac_module)

    logger.info(
        "infra_hints scenario_id=%s refs=%d resolved=%d unresolved=%d modules=%s",
        scenario_id, len(adapter_refs), len(resolved), len(unresolved), modules,
    )
    return {
        "scenario_id":       scenario_id,
        "plane":             scenario.plane,
        "adapter_refs":      adapter_refs,
        "resolved_adapters": resolved,
        "unresolved_refs":   unresolved,
        "suggested_modules": sorted(modules),
    }


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

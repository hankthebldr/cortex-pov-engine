"""
CortexSim API — /api/eal router.

Exposes the EAL Traffic Simulator subsystem to the React UI and to the CLI.
The control plane is intentionally narrow:

  GET  /api/eal/plugins                  list registered simulator plugins
  GET  /api/eal/plugins/{name}           plugin metadata + JSON schema
  POST /api/eal/campaigns                persist a campaign definition
  GET  /api/eal/campaigns                list persisted campaigns
  GET  /api/eal/campaigns/{id}           single campaign detail
  POST /api/eal/campaigns/{id}/launch    launch (dry-run by default)
  GET  /api/eal/runs                     list executed runs
  GET  /api/eal/runs/{run_id}            single run detail (with step results)

Long-running campaigns execute in the background via FastAPI BackgroundTasks
and update the ``EalCampaignRun`` row when complete. Operators poll
``GET /api/eal/runs/{run_id}`` to track progress.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import AsyncSessionLocal, get_db
from eal_simulator import (
    Campaign,
    CampaignExecutor,
    PluginRegistry,
    SafetyError,
    get_default_registry,
)
from models import EalCampaign, EalCampaignRun


logger = logging.getLogger("cortexsim.api.eal")

router = APIRouter(prefix="/eal", tags=["eal-simulator"])


# Module-level executor so the registry import + plugin discovery happen once
# on first hit. Tests reset this via _reset_executor().
_executor: Optional[CampaignExecutor] = None
_registry: Optional[PluginRegistry] = None


def _get_executor() -> CampaignExecutor:
    global _executor, _registry
    if _executor is None:
        _registry = get_default_registry()
        _executor = CampaignExecutor(registry=_registry)
    return _executor


def _reset_executor() -> None:
    """Test helper — drop cached executor + registry."""
    global _executor, _registry
    _executor = None
    _registry = None


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class LaunchRequest(BaseModel):
    dry_run: Optional[bool] = None
    operator: Optional[str] = None


class LaunchResponse(BaseModel):
    run_id: str
    campaign_id: str
    status: str
    dry_run: bool


# ---------------------------------------------------------------------------
# Plugin endpoints
# ---------------------------------------------------------------------------


@router.get("/plugins")
async def list_plugins() -> dict[str, Any]:
    reg = _get_executor().registry
    plugins = reg.manifest()
    return {"plugins": plugins, "total": len(plugins)}


@router.get("/plugins/{name}")
async def get_plugin(name: str) -> dict[str, Any]:
    reg = _get_executor().registry
    if not reg.has(name):
        raise HTTPException(
            status_code=404,
            detail={"error": "Plugin not found", "code": "PLUGIN_NOT_FOUND",
                    "detail": f"plugin='{name}'"},
        )
    return reg.get(name).metadata()


# ---------------------------------------------------------------------------
# Campaign CRUD
# ---------------------------------------------------------------------------


@router.post("/campaigns", status_code=201)
async def create_campaign(
    campaign: Campaign,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Persist a campaign definition.

    The Pydantic ``Campaign`` model has already done full schema validation;
    here we additionally verify each step references a registered plugin and
    that its params validate against that plugin's params model.
    """
    reg = _get_executor().registry
    for step in campaign.steps:
        if not reg.has(step.plugin):
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "Unknown plugin",
                    "code": "PLUGIN_NOT_FOUND",
                    "detail": f"step '{step.step_id}' references plugin '{step.plugin}'",
                },
            )
        try:
            reg.get(step.plugin).validate_params(step.params)
        except Exception as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "Invalid step params",
                    "code": "PARAMS_INVALID",
                    "detail": f"step '{step.step_id}': {exc}",
                },
            ) from exc

    existing = await db.execute(
        select(EalCampaign).where(EalCampaign.campaign_id == campaign.campaign_id)
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=409,
            detail={"error": "Campaign already exists", "code": "DUPLICATE_CAMPAIGN",
                    "detail": campaign.campaign_id},
        )

    row = EalCampaign(
        campaign_id=campaign.campaign_id,
        name=campaign.name,
        description=campaign.description,
        spec=campaign.to_dict(),
        authorized_by=campaign.authorized_by,
        simulation_authorized=campaign.simulation_authorized,
        target_allowlist=list(campaign.target_allowlist),
        tags=list(campaign.tags),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    logger.info("campaign created campaign_id=%s steps=%d", campaign.campaign_id, len(campaign.steps))
    return row.to_dict()


@router.get("/campaigns")
async def list_campaigns(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    result = await db.execute(select(EalCampaign).order_by(EalCampaign.created_at.desc()))
    rows = result.scalars().all()
    return {"campaigns": [r.to_dict() for r in rows], "total": len(rows)}


@router.get("/campaigns/{campaign_id}")
async def get_campaign(campaign_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    result = await db.execute(
        select(EalCampaign).where(EalCampaign.campaign_id == campaign_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "Campaign not found", "code": "CAMPAIGN_NOT_FOUND",
                    "detail": campaign_id},
        )
    return row.to_dict()


# ---------------------------------------------------------------------------
# Launch + runs
# ---------------------------------------------------------------------------


@router.post("/campaigns/{campaign_id}/launch", response_model=LaunchResponse)
async def launch_campaign(
    campaign_id: str,
    body: LaunchRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> LaunchResponse:
    """Launch a campaign in the background. Polls via /api/eal/runs/{run_id}."""
    result = await db.execute(
        select(EalCampaign).where(EalCampaign.campaign_id == campaign_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "Campaign not found", "code": "CAMPAIGN_NOT_FOUND",
                    "detail": campaign_id},
        )

    spec = dict(row.spec or {})
    if body.dry_run is not None:
        spec["dry_run"] = bool(body.dry_run)

    try:
        campaign = Campaign.model_validate(spec)
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "Invalid campaign spec", "code": "SPEC_INVALID",
                    "detail": str(exc)},
        ) from exc

    executor = _get_executor()

    # Pre-flight the safety policy synchronously so launch returns a useful
    # error instead of silently failing in the background.
    try:
        from eal_simulator.safety import SafetyPolicy

        SafetyPolicy(
            simulation_authorized=campaign.simulation_authorized,
            authorized_by=campaign.authorized_by,
            target_allowlist=campaign.target_allowlist,
            dry_run=campaign.dry_run,
        ).assert_campaign_authorized()
    except SafetyError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "Safety violation", "code": "SAFETY_VIOLATION",
                    "detail": str(exc)},
        ) from exc

    # Create the run row up front so /runs/{id} returns "pending" immediately.
    import uuid

    run_id = str(uuid.uuid4())
    run_row = EalCampaignRun(
        run_id=run_id,
        campaign_id=campaign.campaign_id,
        status="pending",
        dry_run=campaign.dry_run,
        operator=body.operator,
        step_results=[],
    )
    db.add(run_row)
    await db.commit()

    # Pass the persisted run_id into the executor so audit events and the DB
    # row share a single identifier — clients polling /api/eal/runs/{run_id}
    # see the same id that ECS audit lines carry.
    background_tasks.add_task(_run_campaign_in_background, executor, campaign, run_id)

    logger.info(
        "campaign launched campaign_id=%s run_id=%s dry_run=%s",
        campaign.campaign_id, run_id, campaign.dry_run,
    )
    return LaunchResponse(
        run_id=run_id,
        campaign_id=campaign.campaign_id,
        status="pending",
        dry_run=campaign.dry_run,
    )


async def _run_campaign_in_background(
    executor: CampaignExecutor,
    campaign: Campaign,
    run_id: str,
) -> None:
    """Background task: execute the campaign, mirror final state to the DB."""
    try:
        state = await executor.execute(campaign, run_id=run_id)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("campaign execution crashed run_id=%s", run_id)
        async with AsyncSessionLocal() as session:
            await _update_run(session, run_id, status="failed", error=str(exc))
        return

    async with AsyncSessionLocal() as session:
        await _update_run(
            session,
            run_id,
            status=state.status,
            error=state.error,
            completed_at=state.completed_at,
            step_results=[r.to_dict() for r in state.step_results],
        )


async def _update_run(
    session: AsyncSession,
    run_id: str,
    *,
    status: str,
    error: Optional[str] = None,
    completed_at: Optional[datetime] = None,
    step_results: Optional[list[dict[str, Any]]] = None,
) -> None:
    result = await session.execute(
        select(EalCampaignRun).where(EalCampaignRun.run_id == run_id)
    )
    run = result.scalar_one_or_none()
    if run is None:
        logger.warning("background update could not find run_id=%s", run_id)
        return
    run.status = status
    run.error = error
    if completed_at is not None:
        run.completed_at = completed_at
    if step_results is not None:
        run.step_results = step_results
    await session.commit()


@router.get("/runs")
async def list_runs(
    campaign_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    stmt = select(EalCampaignRun).order_by(EalCampaignRun.started_at.desc())
    if campaign_id:
        stmt = stmt.where(EalCampaignRun.campaign_id == campaign_id)
    result = await db.execute(stmt)
    rows = result.scalars().all()
    return {"runs": [r.to_dict() for r in rows], "total": len(rows)}


@router.get("/runs/{run_id}")
async def get_run(run_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    result = await db.execute(
        select(EalCampaignRun).where(EalCampaignRun.run_id == run_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "Run not found", "code": "RUN_NOT_FOUND",
                    "detail": run_id},
        )
    return row.to_dict()

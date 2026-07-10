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
    # Launch-time consent for a C2-shaped campaign. Mirrors the scenario /
    # tool-adapter launch gate's ``consent.c2_authorized`` (see
    # core/engine/orchestrator.py). When provided it overrides the persisted
    # campaign spec value so an operator can grant C2 consent at launch without
    # rewriting the stored campaign.
    c2_authorized: Optional[bool] = None


class LaunchResponse(BaseModel):
    run_id: str
    campaign_id: str
    status: str
    dry_run: bool


class AbortResponse(BaseModel):
    run_id: str
    campaign_id: str
    status: str
    previous_status: str


# Core run lifecycle vocabulary (shared with models.Run / ExecutorState):
#   pending | running | complete | failed | aborted
# An EAL run in one of these non-terminal states can still be aborted.
_ABORTABLE_STATUSES = frozenset({"pending", "running"})


# ---------------------------------------------------------------------------
# C2 classification helper — joins a campaign's steps to plugin registry
# metadata so the launch gate can MITRE-classify (not just name-match).
# ---------------------------------------------------------------------------


def _campaign_c2_plugins(campaign: Campaign) -> list[str]:
    """Return the C2-shaped plugin names referenced by ``campaign``.

    Each step plugin name is joined to its registry metadata so the
    classifier can consider the plugin's declared MITRE techniques in addition
    to the explicit name allowlist. Unknown plugins fall back to name-only
    classification.
    """
    from eal_simulator.safety import classify_c2_plugins  # noqa: PLC0415

    reg = _get_executor().registry
    pairs: list[tuple[str, Optional[list[str]]]] = []
    for step in campaign.steps:
        techniques: Optional[list[str]] = None
        if reg.has(step.plugin):
            meta = reg.get(step.plugin).metadata()
            techniques = list(meta.get("mitre_techniques") or [])
        pairs.append((step.plugin, techniques))
    return classify_c2_plugins(pairs)


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
    # Launch-time C2 consent overrides the persisted campaign value so an
    # operator can grant C2 authorisation at launch (mirrors the scenario
    # launch path, where consent.c2_authorized is supplied per-launch).
    if body.c2_authorized is not None:
        spec["c2_authorized"] = bool(body.c2_authorized)

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
    # error instead of silently failing in the background. The C2 gate is
    # enforced here with the full registry-backed MITRE classification, mirroring
    # the scenario adapter gate's consent.c2_authorized requirement for
    # safety_class:c2-framework adapters.
    try:
        from eal_simulator.safety import SafetyPolicy

        policy = SafetyPolicy(
            simulation_authorized=campaign.simulation_authorized,
            authorized_by=campaign.authorized_by,
            target_allowlist=campaign.target_allowlist,
            dry_run=campaign.dry_run,
            c2_authorized=campaign.c2_authorized,
        )
        policy.assert_campaign_authorized()
        policy.assert_c2_authorized(_campaign_c2_plugins(campaign))
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


@router.post("/runs/{run_id}/abort", response_model=AbortResponse)
async def abort_run(
    run_id: str,
    db: AsyncSession = Depends(get_db),
) -> AbortResponse:
    """Abort an in-flight EAL campaign run.

    Reconciles the EAL run lifecycle with the core Run vocabulary
    (``pending | running | complete | failed | aborted``): a run in a
    non-terminal state (``pending``/``running``) is flipped to ``aborted`` and
    that state is made *sticky* — the background executor's terminal write is
    suppressed for an already-aborted run (see ``_update_run``).

    This is a cooperative/best-effort abort: FastAPI ``BackgroundTasks`` cannot
    be force-cancelled, so an already-emitting plugin may finish its current
    step. The durable run state, however, is authoritative and terminal once
    aborted. A fully pre-emptive cancel is deferred — it requires threading a
    cancellation token through the executor (out of this lane; tracked under
    GAP-API-011 follow-up).
    """
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

    previous = row.status
    if previous not in _ABORTABLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "Run is not abortable",
                "code": "RUN_NOT_ABORTABLE",
                "detail": (
                    f"run_id={run_id} is in terminal state '{previous}'. Only "
                    f"runs in {sorted(_ABORTABLE_STATUSES)} can be aborted."
                ),
            },
        )

    row.status = "aborted"
    row.error = row.error or "aborted by operator"
    row.completed_at = datetime.utcnow()
    await db.commit()

    logger.info("eal run aborted run_id=%s previous=%s", run_id, previous)
    return AbortResponse(
        run_id=run_id,
        campaign_id=row.campaign_id,
        status="aborted",
        previous_status=previous,
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
    # An operator abort wins: once a run is durably 'aborted', the executor's
    # terminal write must not resurrect it as complete/failed. Keep the abort
    # state sticky (the background coroutine cannot be force-cancelled, so it
    # may still call this after the abort flip).
    if run.status == "aborted":
        logger.info(
            "skipping background update for aborted run_id=%s (would-be status=%s)",
            run_id, status,
        )
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

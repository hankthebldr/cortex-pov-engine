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
  GET  /api/eal/campaigns/{id}/collectors        where this campaign's signal goes
  POST /api/eal/campaigns/{id}/collectors/preflight   canary-POST every collector
  POST /api/eal/campaigns/{id}/bundle    build the offline (no-SimCore) bundle
  GET  /api/eal/bundles/{bundle_id}/download     download that bundle's tar.gz
  GET  /api/eal/runs                     list executed runs
  GET  /api/eal/runs/{run_id}            single run detail (with step results)

Long-running campaigns execute in the background via FastAPI BackgroundTasks
and update the ``EalCampaignRun`` row when complete. Operators poll
``GET /api/eal/runs/{run_id}`` to track progress — which now carries a
``delivery`` rollup stating how many records a collector actually accepted, so
a campaign that "completed" while ingesting nothing cannot read as green.

The collector + bundle endpoints exist because the log-streamer families POST
into an operator-supplied collector that usually lives inside the customer
network: ``/collectors`` makes the destination inspectable before launch, and
``/bundle`` exports the campaign as a self-contained artifact the DC runs from
a host that can actually reach it.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import AsyncSessionLocal, get_db
from eal_simulator import (
    Campaign,
    CampaignExecutor,
    PluginRegistry,
    SafetyError,
    get_default_registry,
)
from eal_simulator.bundle import BundleError, archive_path, generate_bundle
from eal_simulator.collector import (
    preflight_collector,
    resolve_campaign_collectors,
    resolve_step_collector,
)
from eal_simulator.delivery import summarise_step_results
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


class PreflightRequest(BaseModel):
    # The ingestion token is supplied per-call and never persisted: preflight is
    # a "does this door open" probe, not a place to store a customer credential.
    auth_token: Optional[str] = None
    verify_tls: bool = False
    step_id: Optional[str] = None


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


def _plugin_family(manifest: dict[str, Any]) -> str:
    """Classify a plugin manifest into a console destination family.

    ``analytics_log_streamer`` — a collector-POST emitter that lands records in
    a named dataset (declares ``data_sources`` / ``data_sources_partial``);
    ``network_eal`` — everything else (live network-behaviour plugins). This is
    what the console reads to split Traffic / EAL from Data Streams.
    """
    if manifest.get("data_sources") or manifest.get("data_sources_partial"):
        return "analytics_log_streamer"
    return "network_eal"


@router.get("/plugins")
async def list_plugins() -> dict[str, Any]:
    reg = _get_executor().registry
    plugins = reg.manifest()
    for m in plugins:
        m["family"] = _plugin_family(m)
    return {"plugins": plugins, "total": len(plugins)}


@router.get("/data-streams")
async def data_streams(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """The analytics log-streamer family + its coverage against the vendor
    catalogue — the backend surface behind the Data Streams console.

    Returns the full 34-source coverage table (gaps included, authored != proven),
    the per-emitter manifests (dataset, detectors, negative-control support), and
    each emitter's most recent delivery verdict derived from EAL run history.
    Gaps are rendered as gaps — an unlisted gap reads as no gap.
    """
    from eal_simulator.analytics_catalogue import (  # noqa: PLC0415
        coverage_report,
        family_manifests,
    )

    reg = _get_executor().registry
    coverage = coverage_report(reg)
    emitters = family_manifests(reg)

    # Most recent delivery verdict per emitter, derived from run history so the
    # console can show "did this source's last run actually ingest?" without a
    # second round-trip. A 2xx is not proof of ingest (see delivery.py); this is
    # a delivery verdict, never a detection-fired claim.
    result = await db.execute(
        select(EalCampaignRun).order_by(EalCampaignRun.started_at.desc()).limit(200)
    )
    runs = result.scalars().all()
    latest_by_plugin: dict[str, dict[str, Any]] = {}
    for run in runs:
        for step in (run.step_results or []):
            plugin = step.get("plugin")
            if not plugin or plugin in latest_by_plugin:
                continue
            summary = summarise_step_results([step])
            latest_by_plugin[plugin] = {
                "run_id": run.run_id,
                "delivery_verdict": summary["delivery_verdict"],
                "records_delivered": summary["records_delivered"],
                "records_attempted": summary["records_attempted"],
                "started_at": run.started_at.isoformat() if run.started_at else None,
                "dry_run": run.dry_run,
            }

    for m in emitters:
        m["latest_delivery"] = latest_by_plugin.get(m["name"])

    return {
        "catalogue_source": coverage["catalogue_source"],
        "catalogue_version": coverage["catalogue_version"],
        "counts": coverage["counts"],
        "sources": coverage["sources"],
        "emitters": emitters,
        "authored_not_proven": coverage["authored_not_proven"],
    }


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


# ---------------------------------------------------------------------------
# Collector configuration — make the destination visible before launch.
# ---------------------------------------------------------------------------


async def _load_campaign(campaign_id: str, db: AsyncSession) -> Campaign:
    """Load + revalidate a persisted campaign spec, or 404/422."""
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
    try:
        return Campaign.model_validate(dict(row.spec or {}))
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "Invalid campaign spec", "code": "SPEC_INVALID",
                    "detail": str(exc)},
        ) from exc


@router.get("/campaigns/{campaign_id}/collectors")
async def get_campaign_collectors(
    campaign_id: str, db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Where will this campaign's signal actually go?

    Resolves every step's collector destination from its validated params and
    flags the mistakes that quietly break a live run — a collector host outside
    ``target_allowlist``, a pathless URL, plaintext http. Auth tokens are
    reported as present/absent only; the secret never crosses this boundary.
    """
    campaign = await _load_campaign(campaign_id, db)
    return resolve_campaign_collectors(campaign, _get_executor().registry)


@router.post("/campaigns/{campaign_id}/collectors/preflight")
async def preflight_campaign_collectors(
    campaign_id: str,
    body: PreflightRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """POST one discard-safe canary record to each collector and report back.

    Answers "can this collector actually ingest?" in the delivery taxonomy
    before a customer is watching. The campaign's ``target_allowlist`` gates it:
    a collector host the safety policy would refuse is never probed.
    """
    from eal_simulator.safety import SafetyPolicy  # noqa: PLC0415

    campaign = await _load_campaign(campaign_id, db)
    registry = _get_executor().registry

    policy = SafetyPolicy(
        simulation_authorized=campaign.simulation_authorized,
        authorized_by=campaign.authorized_by,
        target_allowlist=campaign.target_allowlist,
        # A canary POST is a live send, so the policy must judge it as one.
        dry_run=False,
    )

    probes: list[dict[str, Any]] = []
    for step in campaign.steps:
        if body.step_id and step.step_id != body.step_id:
            continue
        if not registry.has(step.plugin):
            continue
        try:
            params = registry.get(step.plugin).validate_params(step.params)
        except Exception:
            continue
        target = resolve_step_collector(
            step.step_id, step.plugin, params,
            target_allowlist=list(campaign.target_allowlist),
        )
        if target is None:
            continue
        try:
            policy.authorise(target.host)
        except SafetyError as exc:
            probes.append({
                "step_id": step.step_id,
                "url": target.url,
                "reachable": False,
                "delivered": False,
                "status_code": None,
                "code": "target_not_authorised",
                "error": str(exc),
                "remediation": (
                    "Add the collector host to the campaign target_allowlist "
                    "before preflighting or launching."
                ),
            })
            continue
        probes.append(await preflight_collector(
            target,
            campaign_id=campaign.campaign_id,
            auth_token=body.auth_token,
            verify_tls=body.verify_tls,
        ))

    if not probes:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "No collector to preflight",
                "code": "NO_COLLECTOR",
                "detail": (
                    f"campaign '{campaign_id}' has no step that POSTs to an "
                    "operator-supplied collector"
                ),
            },
        )

    delivered = [p for p in probes if p["delivered"]]
    return {
        "campaign_id": campaign.campaign_id,
        "probes": probes,
        "collectors_probed": len(probes),
        "collectors_delivering": len(delivered),
        "ready": len(delivered) == len(probes),
    }


# ---------------------------------------------------------------------------
# Offline bundle — run the campaign where the collector lives.
# ---------------------------------------------------------------------------


def _bundles_dir() -> Path:
    return Path(settings.CORTEXSIM_BASE_DIR) / "data" / "eal_bundles"


@router.post("/campaigns/{campaign_id}/bundle", status_code=201)
async def build_campaign_bundle(
    campaign_id: str, db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Export the campaign as a self-contained offline bundle.

    The artifact runs on a clean Ubuntu host with python3 and nothing else —
    no SimCore, no pip install, no callback — so a DC can carry it to a jumpbox
    inside the customer network where the Broker VM / HTTP collector lives.
    """
    campaign = await _load_campaign(campaign_id, db)
    try:
        result = generate_bundle(campaign, _get_executor().registry, _bundles_dir())
    except BundleError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "Bundle generation failed", "code": "BUNDLE_FAILED",
                    "detail": str(exc)},
        ) from exc

    manifest = result["manifest"]
    logger.info(
        "eal bundle built campaign_id=%s bundle_id=%s records=%d",
        campaign_id, result["bundle_id"], manifest["total_records"],
    )
    return {
        "bundle_id": result["bundle_id"],
        "campaign_id": campaign_id,
        "download_url": result["download_url"],
        "steps": [s["step_id"] for s in manifest["steps"]],
        "skipped_steps": manifest["skipped_steps"],
        "total_records": manifest["total_records"],
        "destinations": sorted({s["collector_url"] for s in manifest["steps"]}),
    }


@router.get("/bundles/{bundle_id}/download")
async def download_campaign_bundle(bundle_id: str) -> FileResponse:
    archive = archive_path(_bundles_dir(), bundle_id)
    if archive is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "Bundle not found", "code": "BUNDLE_NOT_FOUND",
                    "detail": f"bundle_id='{bundle_id}'"},
        )
    return FileResponse(
        path=str(archive),
        media_type="application/gzip",
        filename=f"cortexsim-eal-{bundle_id}.tar.gz",
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
    return {"runs": [_run_dict(r) for r in rows], "total": len(rows)}


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
    return _run_dict(row)


def _run_dict(row: EalCampaignRun) -> dict[str, Any]:
    """Run row + the delivery rollup, derived at read time.

    Computed from the persisted ``step_results`` rather than stored, so runs
    written before the delivery ledger existed still render (they simply report
    ``not_applicable``) and no schema migration is needed.
    """
    payload = row.to_dict()
    payload["delivery"] = summarise_step_results(payload.get("step_results") or [])
    return payload

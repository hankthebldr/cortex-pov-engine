"""CortexSim API — /api/assertions.

The read + evaluate surface for the POS / PLT / AUT proof mechanism.

Three properties this surface is built around:

* **Dry run is the default and it is genuinely useful.** It renders the exact
  question the engine will ask — canary and nonce substituted — without
  touching a tenant. A DC walks into a POV with the queries in hand and the
  customer never watches a preview go wrong.
* **No tenant means ``pending``, never green.** There is no code path here that
  produces ``pass`` without a credential-backed, read-only query having
  actually run and returned a measurement.
* **Rejections are visible.** ``GET /api/assertions`` reports every artifact the
  loader refused and why. An assertion that could not fail is a load error, and
  a load error that nobody sees is the same as no guard at all.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from engine import assertions as assertion_engine
from models import AssertionCheck, AssertionRun

logger = logging.getLogger("cortexsim.api.assertions")

router = APIRouter(prefix="/assertions", tags=["assertions"])

# Default XQL window for probe queries. Overridable per request; deliberately
# generous because posture scan cycles and automation settle windows are slow.
_DEFAULT_TIMEFRAME_SECONDS = 3600


class RunBody(BaseModel):
    """Evaluate an assertion. ``dry_run`` defaults true — previewing must never
    require the courage to point at a customer tenant."""

    dry_run: bool = True
    integration: Optional[str] = Field(
        None, description="name of a registered xsiam_tenant integration")
    context: dict[str, str] = Field(
        default_factory=dict,
        description="values substituted into {{...}} placeholders (nonce, run_id, author vars)")
    trigger_run_id: Optional[str] = Field(
        None, description="the CortexSim run whose outcome this assertion asserts")
    entitled_addons: Optional[list[str]] = Field(
        None,
        description="tenant's declared add-ons. Omit to treat the tenant as "
                    "entitled — silence must not buy a not_applicable.")
    timeframe_seconds: int = Field(_DEFAULT_TIMEFRAME_SECONDS, ge=60, le=2_592_000)
    persist: bool = True


def _catalog() -> assertion_engine.AssertionCatalog:
    return assertion_engine.catalog.ensure_loaded()


def _spec_payload(spec: assertion_engine.AssertionSpec) -> dict[str, Any]:
    base, addons = assertion_engine.entitlements(spec)
    return {
        "assertion_id": spec.assertion_id,
        "name": spec.name,
        "version": spec.version,
        "status": spec.status,
        "validation_class": spec.validation_class,
        "kind": spec.kind,
        "plane": spec.plane,
        "uc_ref": spec.uc_ref,
        "tc_ref": spec.tc_ref,
        "tc_refs": spec.tc_refs,
        "scope_limitations": spec.scope_limitations,
        "primary_kpi": spec.primary_kpi or spec.primary_check.threshold.kpi,
        "moat_tier": spec.moat_tier,
        "methodology_family": spec.methodology_family,
        "success_criteria": spec.success_criteria,
        "stimulus": spec.stimulus.model_dump(),
        "settle": spec.settle.model_dump() if spec.settle else None,
        "context_vars": spec.context_vars,
        "index": assertion_engine.index_meta(spec),
        "tc_scoreable": assertion_engine.tc_scoreable(spec),
        "required_base_platform": base,
        "required_addons": addons,
        "tags": spec.tags,
        "author": spec.author,
        "checks": [
            {
                "check_id": c.check_id,
                "title": c.title,
                "probe": c.probe,
                "primary": c.primary,
                "weight": c.weight,
                "threshold": {**c.threshold.as_dict(),
                              "source": c.threshold.source,
                              "rationale": c.threshold.rationale},
                "negative_control": c.negative_control.model_dump(),
                "failure_conditions": list(
                    assertion_engine.PROBE_REGISTRY[c.probe].FAILURE_CONDITIONS),
                "measured_unit": assertion_engine.PROBE_REGISTRY[c.probe].unit_for(c.params),
                "params": c.params,
            }
            for c in spec.checks
        ],
    }


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

@router.get("")
async def list_assertions(
    validation_class: Optional[str] = Query(None),
    tc_ref: Optional[str] = Query(None),
    kind: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    probe: Optional[str] = Query(None),
) -> dict[str, Any]:
    """List authored assertions, plus everything the loader refused.

    ``rejected`` is not an afterthought: the falsifiability guard (A-17/A-18) is
    only worth something if the artifacts it blocked are visible to the person
    who authored them.
    """
    cat = _catalog()
    specs = cat.all()
    if validation_class:
        specs = [s for s in specs if s.validation_class == validation_class.upper()]
    if tc_ref:
        specs = [s for s in specs if tc_ref in s.tc_refs]
    if kind:
        specs = [s for s in specs if s.kind == kind]
    if status:
        specs = [s for s in specs if s.status == status]
    if probe:
        specs = [s for s in specs if any(c.probe == probe for c in s.checks)]

    by_class: dict[str, int] = {}
    for s in cat.all():
        by_class[s.validation_class] = by_class.get(s.validation_class, 0) + 1

    return {
        "assertions": [_spec_payload(s) for s in specs],
        "count": len(specs),
        "total": cat.count(),
        "by_validation_class": by_class,
        "rejected": cat.rejected,
        "warnings": cat.warnings,
        "probes": probes_payload(),
    }


def probes_payload() -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "domain": cls.DOMAIN,
            "unit": cls.UNIT,
            "read_only": cls.READ_ONLY,
            "required_params": list(cls.REQUIRED_PARAMS),
            "query_params": list(cls.QUERY_PARAMS),
            "failure_conditions": list(cls.FAILURE_CONDITIONS),
        }
        for name, cls in sorted(assertion_engine.PROBE_REGISTRY.items())
    ]


@router.get("/probes")
async def list_probes() -> dict[str, Any]:
    """The probe contract content agents author against."""
    return {"probes": probes_payload()}


@router.get("/runs")
async def list_runs(
    assertion_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    stmt = select(AssertionRun).order_by(AssertionRun.id.desc()).limit(limit)
    if assertion_id:
        stmt = stmt.where(AssertionRun.assertion_id == assertion_id)
    runs = list((await db.execute(stmt)).scalars().all())
    return {"runs": [r.to_dict() for r in runs], "count": len(runs)}


@router.get("/runs/{run_id}")
async def get_run(run_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    run = (await db.execute(
        select(AssertionRun).where(AssertionRun.run_id == run_id)
    )).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail={
            "error": "Assertion run not found", "code": "ASSERTION_RUN_NOT_FOUND",
            "detail": f"run_id={run_id}"})
    checks = list((await db.execute(
        select(AssertionCheck).where(AssertionCheck.run_id == run_id)
    )).scalars().all())
    return {**run.to_dict(), "checks": [c.to_dict() for c in checks]}


@router.get("/{assertion_id}")
async def get_assertion(assertion_id: str) -> dict[str, Any]:
    spec = _catalog().get(assertion_id)
    if spec is None:
        raise HTTPException(status_code=404, detail={
            "error": "Assertion not found", "code": "ASSERTION_NOT_FOUND",
            "detail": f"assertion_id={assertion_id}"})
    return _spec_payload(spec)


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

@router.post("/{assertion_id}/run")
async def run_assertion_endpoint(
    assertion_id: str,
    body: RunBody,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Evaluate an assertion and score it through the existing verifier.

    With ``dry_run`` (the default) this renders every query and returns a
    ``pending`` verdict with ``reason: dry_run`` — an itemised list of what is
    still owed, which is strictly more useful than a premature green tick.

    With ``dry_run: false`` and a registered tenant it runs each probe read-only
    and returns a real ``pass``/``fail``. Without a tenant it returns ``pending``
    with ``reason: no_tenant_integration``. There is no path to ``pass`` here
    that does not go through a measurement.
    """
    spec = _catalog().get(assertion_id)
    if spec is None:
        raise HTTPException(status_code=404, detail={
            "error": "Assertion not found", "code": "ASSERTION_NOT_FOUND",
            "detail": f"assertion_id={assertion_id}"})

    context = dict(body.context)
    context.setdefault("run_id", body.trigger_run_id or "")
    context.setdefault("nonce", uuid.uuid4().hex[:8])
    context.setdefault("lookback_seconds", str(body.timeframe_seconds))

    runner = None
    tenant = body.integration
    if not body.dry_run:
        runner, tenant = await _resolve_runner(db, body)

    outcome = await assertion_engine.run_assertion(
        spec, runner=runner, context=context,
        entitled_addons=body.entitled_addons, dry_run=body.dry_run,
    )

    payload: dict[str, Any] = {
        **outcome.to_dict(),
        "dry_run": body.dry_run,
        "tenant": tenant,
        "scope_limitations": spec.scope_limitations,
        "tc_scoreable": assertion_engine.tc_scoreable(spec),
        "index": assertion_engine.index_meta(spec),
        "stimulus": spec.stimulus.model_dump(),
    }

    if body.dry_run or not body.persist:
        return payload

    run_id = f"AR-{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}"
    await assertion_engine.upsert_assertions(db, [spec])
    await assertion_engine.persist_outcome(
        db, outcome, run_id=run_id, tenant=tenant,
        trigger_run_id=body.trigger_run_id,
    )
    payload["run_id"] = run_id
    return payload


async def _resolve_runner(db: AsyncSession, body: RunBody):
    """Build a read-only ScalarRunner from a registered tenant credential.

    Returns ``(None, name)`` when nothing usable is configured — the caller then
    resolves ``pending``. A missing credential must never raise into a 500: "no
    tenant wired" is an honest verdict, not a server error.
    """
    from engine.verifier import xsiam_rows_runner  # noqa: PLC0415
    from integrations.xsiam.loader import XSIAM_KIND, load_xsiam_client  # noqa: PLC0415
    from security.credentials import CredentialStore  # noqa: PLC0415

    store = CredentialStore(db)
    integrations = await store.list_integrations(kind=XSIAM_KIND)
    if not integrations:
        return None, None
    integ = (next((i for i in integrations if i.name == body.integration), None)
             if body.integration else integrations[0])
    if integ is None:
        raise HTTPException(status_code=404, detail={
            "error": "Integration not found", "code": "INTEGRATION_NOT_FOUND",
            "detail": f"no xsiam_tenant integration named '{body.integration}'"})

    try:
        client = await load_xsiam_client(db, integ.name)
    except Exception as exc:  # noqa: BLE001 — resolves to pending, not a 500
        logger.warning("could not build XSIAM client for '%s': %s", integ.name, exc)
        return None, integ.name

    timeframe = {"relativeTime": body.timeframe_seconds * 1000}
    return xsiam_rows_runner(client, timeframe), integ.name

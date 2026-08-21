"""CortexSim API — /api/connectors + run reconciliation (measurement loop).

Closes the detection-efficacy loop. Two ingest paths feed the same matcher:

  * **Manual batch** — ``POST /api/runs/{run_id}/observations`` accepts a list
    of observed alerts (a DC exports them from the Cortex console as JSON/CSV).
    No credentials required; works fully offline.
  * **Connector pull** — ``POST /api/runs/{run_id}/reconcile?connector=xsiam``
    uses a configured integration credential to pull alerts from the live tenant
    for the run's time window. Requires an integration of the connector's kind.

Both correlate observations to the run's seeded ``Result`` rows (on technique /
detection id / name within a time window), set ``observed_at`` for matches —
yielding real, evidence-backed MTTD — and emit ``result.observed`` SSE frames.

``GET /api/connectors`` lists the available connector kinds and, for each,
whether a usable integration credential is configured and verified.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from connectors import ObservedAlert, available_connectors, reconcile
from connectors.matcher import DEFAULT_WINDOW_SECONDS
from database import get_db
from models import Result, Run
from integrations.xsiam.loader import XSIAM_KIND as XSIAM_TENANT_KIND
from security.credentials import CredentialStore

logger = logging.getLogger("cortexsim.api.connectors")

# Two routers: connector discovery lives under /connectors; the run-scoped
# ingest/reconcile endpoints are mounted on /runs to sit beside the run surface.
router = APIRouter(prefix="/connectors", tags=["connectors"])
runs_reconcile_router = APIRouter(prefix="/runs", tags=["connectors"])


class ObservationsBody(BaseModel):
    """Manual batch of observed alerts (exported from the Cortex console)."""

    observations: list[dict[str, Any]] = Field(default_factory=list)
    window_seconds: int = DEFAULT_WINDOW_SECONDS
    reevaluate: bool = False  # if true, also re-check already-observed results


# ---------------------------------------------------------------------------
# Connector discovery
# ---------------------------------------------------------------------------

@router.get("")
async def list_connectors(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """List available read-back connectors and whether each has a usable
    integration credential configured (and its last verification status)."""
    store = CredentialStore(db)
    integrations = await store.list_integrations()
    by_kind: dict[str, list] = {}
    for integ in integrations:
        by_kind.setdefault(integ.kind, []).append(integ)

    def _project(rows: list) -> list[dict[str, Any]]:
        return [
            {
                "name": i.name,
                "kind": i.kind,
                "verified_ok": i.last_verified_ok,
                "last_verified_at": (i.last_verified_at.isoformat()
                                     if i.last_verified_at else None),
                # Absent == the legacy default. Never inferred as ["read_alerts",
                # "xql"]: XQL is a separate authorisation because it spends a
                # metered resource the customer's own analysts share.
                "capabilities": ((i.config or {}).get("capabilities")
                                 or ["read_alerts"]),
            }
            for i in rows
        ]

    out = []
    for c in available_connectors():
        configured = by_kind.get(c["kind"], [])
        # A DC who correctly registered an `xsiam_tenant` credential used to see
        # `configured: false` here, because this listing only ever looked at the
        # reconcile kind. Report both; the reconcile connector reads either
        # spelling of the tenant URL.
        equivalent = (by_kind.get(XSIAM_TENANT_KIND, [])
                      if c["kind"] == "xsiam" else [])
        out.append({
            **c,
            "configured": bool(configured or equivalent),
            "integrations": _project(configured),
            "equivalent_integrations": _project(equivalent),
            "preflight_url": f"/api/connectors/{c['kind']}/preflight",
        })
    return {"connectors": out,
            "tenant_integrations": _project(by_kind.get(XSIAM_TENANT_KIND, []))}


# ---------------------------------------------------------------------------
# Shared reconciliation core
# ---------------------------------------------------------------------------

async def _load_run_results(db: AsyncSession, run_id: str) -> tuple[Run, list[Result]]:
    run = (await db.execute(select(Run).where(Run.run_id == run_id))).scalar_one_or_none()
    if run is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "Run not found", "code": "RUN_NOT_FOUND", "detail": f"run_id={run_id}"},
        )
    results = list((await db.execute(
        select(Result).where(Result.run_id == run_id)
    )).scalars().all())
    return run, results


# ---------------------------------------------------------------------------
# Manual batch ingest
# ---------------------------------------------------------------------------

@runs_reconcile_router.post("/{run_id}/observations")
async def ingest_observations(
    run_id: str,
    body: ObservationsBody,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Ingest a batch of observed alerts and auto-validate matching results."""
    from connectors.service import apply_verdicts

    _run, results = await _load_run_results(db, run_id)
    alerts, dropped = ObservedAlert.from_dicts(body.observations, default_source="manual")
    verdicts = reconcile(results, alerts, window_seconds=body.window_seconds,
                         only_unobserved=not body.reevaluate)
    summary, _ = await apply_verdicts(db, run_id, results, verdicts, source="manual-import")
    summary["ingested"] = len(alerts)
    summary["verdicts"] = [v.to_dict() for v in verdicts if v.matched]
    if dropped:
        # These alerts were submitted and NOT counted. Silently shortening the
        # list would understate coverage with no explanation; the old code was
        # worse still — it dated them to `now`, which inflated MTTD and produced
        # threshold FAILs out of a formatting problem.
        summary["dropped_unparseable_timestamps"] = dropped
        summary["warning"] = (
            f"{dropped} of {len(body.observations)} observations had an unreadable "
            f"timestamp and were not counted. Coverage below is a floor, not a "
            f"measurement. Use ISO-8601 (2026-06-10T12:00:30Z) or epoch ms."
        )
    return summary


# ---------------------------------------------------------------------------
# Credential-backed connector pull
# ---------------------------------------------------------------------------

_RECONCILE_ERROR_STATUS = {
    "CONNECTOR_NOT_FOUND": 404,
    "NO_INTEGRATION": 400,
    "CREDENTIAL_ERROR": 500,
    "CONNECTOR_PULL_FAILED": 502,
}


@runs_reconcile_router.post("/{run_id}/reconcile")
async def reconcile_run_endpoint(
    run_id: str,
    connector: str = Query(..., description="connector kind, e.g. 'xsiam'"),
    integration: Optional[str] = Query(None, description="integration name (defaults to the first of this kind)"),
    window_seconds: int = Query(DEFAULT_WINDOW_SECONDS, ge=60, le=86400),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Pull observations from a configured integration and auto-validate.

    Resolves the integration credential from the encrypted vault, pulls alerts
    for the run's execution window (± a margin), correlates, and sets MTTD.
    Delegates to the shared reconcile service (also used by the auto loop).
    """
    from connectors.service import ReconcileError, reconcile_run

    run, results = await _load_run_results(db, run_id)
    try:
        outcome = await reconcile_run(
            db, run, results, connector_kind=connector,
            integration_name=integration, window_seconds=window_seconds,
        )
    except ReconcileError as e:
        status = _RECONCILE_ERROR_STATUS.get(e.code, 500)
        raise HTTPException(status_code=status, detail={
            "error": str(e), "code": e.code, "detail": e.detail})
    return outcome.summary


# ---------------------------------------------------------------------------
# Preflight — answer "is my connection working?" BEFORE the customer is watching
# ---------------------------------------------------------------------------


@router.post("/{kind}/preflight")
async def preflight_connector(
    kind: str,
    integration: Optional[str] = Query(None, description="integration name (defaults to the first of this kind)"),
    datasets: Optional[str] = Query(
        None,
        description=("comma-separated datasets to probe (xsiam_tenant only). "
                     "OPT-IN: each one costs the customer a live XQL query."),
    ),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Staged, read-only reachability/scope check for a registered credential.

    Read-only in the strongest sense: it touches no Run, no Result and no
    verdict. It is diagnosis, and diagnosis that changes the measurement is not
    diagnosis.

    Returns **200 with ``overall: "blocked"``** rather than an HTTP error when
    the tenant refuses — the check itself succeeded, and the DC needs the whole
    stage list, not an exception that hides the six stages after the failure.
    """
    from connectors import preflight as pf

    store = CredentialStore(db)
    rows = await store.list_integrations(kind=kind)
    if not rows:
        # Explicit "nothing registered" beats an empty green report.
        return pf.PreflightReport(
            tenant=integration, kind=kind,
            stages=[pf.Stage(
                "config", pf.BLOCKED, pf.PF_NO_INTEGRATION,
                f"no integration of kind '{kind}' is registered",
                remediation=("Register the credential first. With none configured "
                             "every verdict resolves `pending` — CortexSim reports "
                             "nothing measured rather than nothing detected."),
            )],
        ).to_dict()
    row = (next((r for r in rows if r.name == integration), None)
           if integration else rows[0])
    if row is None:
        raise HTTPException(status_code=404, detail={
            "error": "Integration not found", "code": "INTEGRATION_NOT_FOUND",
            "detail": f"kind={kind} name={integration}"})

    try:
        secret = await store.get_integration_secret(row.name)
    except Exception as e:  # noqa: BLE001 — a rotated master key is a config answer
        return pf.PreflightReport(
            tenant=row.name, kind=kind,
            stages=[pf.Stage(
                "config", pf.BLOCKED, pf.PF_CREDENTIAL_UNDECRYPTABLE,
                # str(e) is the store's own message and never contains the
                # plaintext; the key is by definition undecryptable here.
                str(e),
                remediation=("CORTEXSIM_SECRET was rotated or the ciphertext is "
                             "corrupt. Re-enter the API key for this integration."),
            )],
        ).to_dict()

    if kind == "xsiam":
        return pf.preflight_reconcile(row.config or {}, secret,
                                      integration_name=row.name).to_dict()
    if kind == XSIAM_TENANT_KIND:
        return (await _preflight_tenant(db, row, datasets)).to_dict()

    raise HTTPException(status_code=400, detail={
        "error": "No preflight for this credential kind", "code": "PREFLIGHT_UNSUPPORTED",
        "detail": f"kind='{kind}'"})


async def _preflight_tenant(db: AsyncSession, row: Any, datasets: Optional[str]) -> Any:
    """Build the tenant client and run the XQL/dataset/clock stages."""
    from connectors import preflight as pf
    from integrations.xsiam.exceptions import XsiamConfigError
    from integrations.xsiam.loader import load_xsiam_client

    names = [d.strip() for d in (datasets or "").split(",") if d.strip()]
    config = row.config or {}
    caps = config.get("capabilities")
    # Absent capabilities == the legacy default, read-only. Never infer XQL from
    # the mere existence of a registration: that would silently grant a metered
    # authorisation to every credential already in the field.
    xql_authorised = (not isinstance(caps, list)
                      or "xql" in {str(c).lower() for c in caps})
    host = str(config.get("base_url", "")).split("://")[-1] or None

    try:
        client = await load_xsiam_client(db, row.name)
    except XsiamConfigError as exc:
        return pf.PreflightReport(
            tenant=row.name, kind=XSIAM_TENANT_KIND, base_url_host=host,
            stages=[pf.Stage("config", pf.BLOCKED, pf.PF_CONFIG_INVALID, str(exc),
                             remediation=("Fix the tenant config and re-register. No "
                                          "request was sent, so the API key did not "
                                          "leave this process."))],
        )
    return await pf.preflight_tenant(
        client, integration_name=row.name, base_url_host=host,
        datasets=names, xql_authorised=xql_authorised)


# ---------------------------------------------------------------------------
# Health component — mounted by the health builder; this router owns the probe
# ---------------------------------------------------------------------------


async def connector_health_component(db: AsyncSession) -> dict[str, Any]:
    """Connector-subsystem health, for ``/api/health`` to mount.

    Offered as a function rather than by editing the health module, so the
    connector stack owns what "healthy" means for itself. Deliberately makes
    **zero outbound calls**: /api/health is polled, and a health check that
    spends the customer's tenant quota is a denial-of-service with good
    intentions. It reports what is CONFIGURED and what the last real probe saw.
    """
    from connectors.base import available_connectors
    from integrations.xsiam import ledger as ledger_mod
    from connectors import tuning

    store = CredentialStore(db)
    integrations = await store.list_integrations()
    by_kind: dict[str, list] = {}
    for integ in integrations:
        by_kind.setdefault(integ.kind, []).append(integ)

    kinds = [c["kind"] for c in available_connectors()]
    configured = [k for k in kinds if by_kind.get(k)]
    tenants = by_kind.get(XSIAM_TENANT_KIND, [])
    limit = tuning.xsiam_max_queries_per_day()
    # Key by tenant HOST, exactly as engine.verifier charges and trips it. This
    # component used to snapshot by integration NAME, so it read a row nothing
    # ever wrote: the breaker could be open and every verify degrading to
    # pending while /api/health reported breaker_open false on a full quota.
    from engine.verifier import tenant_ledger_key  # noqa: PLC0415

    budgets = []
    for t in tenants:
        cfg = getattr(t, "config", None) or {}
        base = cfg.get("base_url") or cfg.get("fqdn") or cfg.get("tenant_url") or ""
        snap = ledger_mod.ledger.snapshot(tenant_ledger_key(str(base)), limit=limit)
        # Carry the operator-facing name too — the key is a host, and a DC needs
        # to know WHICH integration is throttled.
        budgets.append({**snap, "integration": t.name})

    return {
        "status": "ok",
        "connectors": kinds,
        "configured_kinds": configured,
        "tenant_integrations": [t.name for t in tenants],
        # An unverified credential is not a failure — it is an untested one, and
        # saying "ok" for it would be the green-proving-nothing pattern.
        "verified": {i.name: i.last_verified_ok for i in integrations},
        "quota": budgets,
        "breaker_open": any(b["breaker_open"] for b in budgets),
        "note": ("Configuration only — /api/health makes no tenant calls. Run "
                 "POST /api/connectors/{kind}/preflight to actually reach a tenant."),
    }

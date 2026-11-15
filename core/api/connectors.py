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
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from connectors import ObservedAlert, available_connectors, reconcile
from connectors.matcher import DEFAULT_WINDOW_SECONDS
from connectors.service import credential_kinds_for, resolve_connector_integrations
from database import get_db
from models import Result, Run
from integrations.panos import PANOS_KIND
from integrations.xsiam.loader import XSIAM_KIND as XSIAM_TENANT_KIND
from security.credentials import CredentialStore

logger = logging.getLogger("cortexsim.api.connectors")

# Two routers: connector discovery lives under /connectors; the run-scoped
# ingest/reconcile endpoints are mounted on /runs to sit beside the run surface.
router = APIRouter(prefix="/connectors", tags=["connectors"])
runs_reconcile_router = APIRouter(prefix="/runs", tags=["connectors"])


#: OPS-06 — one manual batch is bounded. Matching is O(results x alerts) in
#: process, so an unbounded paste is a request that can pin the event loop.
MAX_OBSERVATIONS_PER_BATCH = 5000


#: DNS name limit; a scope host longer than this is not a host name.
MAX_SCOPE_HOST_LENGTH = 253

_HOST_OVERRIDE_HELP = (
    "the endpoint this run executed on, as Cortex names it. Scopes the match to "
    "that host (recorded as host_source='operator'). Needed for a push run, "
    "which has no target to resolve a host from: without it a matched detect "
    "expectation is recorded pending (UNSCOPED_MATCH), never detected.")


def _host_override(value: Optional[str]) -> Optional[str]:
    """A blank override is no override, never a host named ''."""
    return (value or "").strip() or None


class ObservationsBody(BaseModel):
    """Manual batch of observed alerts (exported from the Cortex console)."""

    observations: list[dict[str, Any]] = Field(
        default_factory=list, max_length=MAX_OBSERVATIONS_PER_BATCH)
    window_seconds: int = DEFAULT_WINDOW_SECONDS
    reevaluate: bool = False  # if true, also re-check already-observed results
    host: Optional[str] = Field(None, max_length=MAX_SCOPE_HOST_LENGTH,
                                description=_HOST_OVERRIDE_HELP)


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
        dedicated, *equivalent_kinds = credential_kinds_for(c["kind"])
        configured = by_kind.get(dedicated, [])
        # A DC who correctly registered an `xsiam_tenant` credential used to see
        # `configured: false` here, because this listing only ever looked at the
        # reconcile kind. Report both — from the SAME table the reconcile
        # resolver, the preflight and the auto sweep consult, so "configured"
        # here is a promise those three keep.
        equivalent = [i for k in equivalent_kinds for i in by_kind.get(k, [])]
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
    """Ingest a batch of observed alerts and auto-validate matching results.

    Scoped exactly like a connector pull (``matcher.scope_to_host`` against the
    run's resolved host): a hand-pasted alert from another endpoint is not
    evidence for this run. The batch is ``unscoped`` -- every pass withheld
    (``PASS_WITHHELD_UNSCOPED``) -- when the run has no resolvable target or any
    submitted alert names no host, because then nothing ties it to this run.
    ``host`` in the body names the endpoint instead of resolving it — the only
    way a push run's batch is scoped (``host_source: "operator"``).
    """
    from connectors.matcher import scope_to_host
    from connectors.service import (NEVER_DELIVERED_WARNING, apply_verdicts,
                                    host_source, match_window_ends,
                                    reconcile_candidates, reported_step_ids,
                                    resolve_run_host, window_by_type)

    run, results = await _load_run_results(db, run_id)
    # OPS-01 defence in depth: a pull run no beacon ever collected ran nothing,
    # so none of its beacon-step rows may be credited a pasted alert; an
    # aborted one only on the steps its host reported (RG-RUNS-01).
    candidates, never_delivered = reconcile_candidates(
        run, results, reported_steps=await reported_step_ids(db, run_id))
    submitted, dropped = ObservedAlert.from_dicts(body.observations, default_source="manual")
    override = _host_override(body.host)
    scope_host = override if override is not None else await resolve_run_host(db, run)
    alerts, host_scope = scope_to_host(submitted, scope_host)
    unscoped = host_scope["host"] is None or host_scope["without_host"] > 0
    verdicts = reconcile(candidates, alerts, window_seconds=body.window_seconds,
                         only_unobserved=not body.reevaluate,
                         window_seconds_by_type=window_by_type(),
                         window_end_by_result=await match_window_ends(
                             db, run_id, results, window_seconds=body.window_seconds))
    matched_ext = [v.alert_external_id for v in verdicts if v.matched and v.alert_external_id]
    by_ext = {a.external_id: a for a in alerts if a.external_id}
    summary, _ = await apply_verdicts(
        db, run_id, results, verdicts, source="manual-import",
        # The DC chose these alerts by hand, so silence is never a miss
        # (basis `manual`); a pass stands only when every alert was host-scoped.
        # host_unresolved: the run has no host at all (push runs) — a detect
        # match is then UNSCOPED_MATCH, never detected (DOCS-03).
        pull_context={"basis": "manual", "unscoped": unscoped, "truncated": False,
                      "host_unresolved": host_scope["host"] is None,
                      "alert_incidents": {e: by_ext[e].incident_id for e in matched_ext
                                          if e in by_ext}},
        observations=alerts, window_seconds=body.window_seconds)
    summary["host_scope"] = host_scope
    summary["host_source"] = host_source(override, host_scope)
    if unscoped:
        summary["unscoped"] = True
    summary["ingested"] = len(submitted)
    summary["verdicts"] = [v.to_dict() for v in verdicts if v.matched]
    warnings: list[str] = []
    if never_delivered:
        summary["never_delivered"] = True
        warnings.append(NEVER_DELIVERED_WARNING)
    if dropped:
        # These alerts were submitted and NOT counted. Silently shortening the
        # list would understate coverage with no explanation; the old code was
        # worse still — it dated them to `now`, which inflated MTTD and produced
        # threshold FAILs out of a formatting problem.
        summary["dropped_unparseable_timestamps"] = dropped
        warnings.append(
            f"{dropped} of {len(body.observations)} observations had an unreadable "
            f"timestamp and were not counted. Coverage below is a floor, not a "
            f"measurement. Use ISO-8601 (2026-06-10T12:00:30Z) or epoch ms."
        )
    excluded = host_scope.get("excluded_other_host") or 0
    if excluded:
        # Same shape as the drop above (R6): a pasted alert that names another
        # host is not evidence for this run, but vanishing without a word reads
        # as "Cortex never alerted". Say how many and which host was expected,
        # so a short-name vs FQDN mismatch is visible.
        summary["excluded_other_host"] = excluded
        warnings.append(
            f"{excluded} of {len(body.observations)} observations named another host "
            f"than this run's target ({host_scope['host']}) and were not counted. If "
            f"they are this run's endpoint under another name, re-submit them with "
            f"the run's host name."
        )
    if warnings:
        summary["warning"] = " ".join(warnings)
    return summary


# ---------------------------------------------------------------------------
# Credential-backed connector pull
# ---------------------------------------------------------------------------

_RECONCILE_ERROR_STATUS = {
    "CONNECTOR_NOT_FOUND": 404,
    "NO_INTEGRATION": 400,
    "INTEGRATION_NOT_FOUND": 404,
    "CREDENTIAL_ERROR": 500,
    "CONNECTOR_PULL_FAILED": 502,
}


@runs_reconcile_router.post("/{run_id}/reconcile")
async def reconcile_run_endpoint(
    run_id: str,
    connector: str = Query(..., description="connector kind, e.g. 'xsiam'"),
    integration: Optional[str] = Query(None, description="integration name (defaults to the first of this kind)"),
    window_seconds: int = Query(DEFAULT_WINDOW_SECONDS, ge=60, le=86400),
    host: Optional[str] = Query(None, max_length=MAX_SCOPE_HOST_LENGTH,
                                description=_HOST_OVERRIDE_HELP),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Pull observations from a configured integration and auto-validate.

    Resolves the integration credential from the encrypted vault, pulls alerts
    for the run's execution window (± a margin), correlates, and sets MTTD.
    Delegates to the shared reconcile service (also used by the auto loop).
    ``?host=`` names the endpoint to scope to instead of resolving it from the
    run's target — the only way a push run is reconciled scoped.
    """
    from connectors.service import ReconcileError, reconcile_run

    run, results = await _load_run_results(db, run_id)
    try:
        outcome = await reconcile_run(
            db, run, results, connector_kind=connector,
            integration_name=integration, window_seconds=window_seconds,
            host=_host_override(host),
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
    agents: Optional[str] = Query(
        None,
        description=("comma-separated agent ids to check the policy path for "
                     "(panos_ngfw only; default: every enrolled agent)"),
    ),
    response_layer: bool = Query(
        True,
        description=("xsiam_tenant only: also run the read-only SOC Framework "
                     "(response layer) detection stage and store its result"),
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
    if kind == PANOS_KIND:
        # First: the resolution below emits PF_ codes; the firewall owns its own (PN_).
        from api.ngfw import preflight_panos  # noqa: PLC0415
        return await preflight_panos(db, integration, agents)

    from connectors import preflight as pf

    store = CredentialStore(db)
    rows = await resolve_connector_integrations(store, kind)
    if not rows:
        kinds = " or ".join(f"'{k}'" for k in credential_kinds_for(kind))
        # Explicit "nothing registered" beats an empty green report.
        return pf.PreflightReport(
            tenant=integration, kind=kind,
            stages=[pf.Stage(
                "config", pf.BLOCKED, pf.PF_NO_INTEGRATION,
                f"no integration of kind {kinds} is registered",
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

    # Read before the preflight: a failed store rolls the session back, which
    # expires the row, and an async lazy reload here would turn a good report
    # into a 500.
    credential = {"name": row.name, "kind": row.kind}
    if kind == "xsiam":
        report = pf.preflight_reconcile(row.config or {}, secret,
                                        integration_name=row.name).to_dict()
    elif kind == XSIAM_TENANT_KIND:
        report = (await _preflight_tenant(db, row, datasets, response_layer)).to_dict()
    else:
        raise HTTPException(status_code=400, detail={
            "error": "No preflight for this credential kind", "code": "PREFLIGHT_UNSUPPORTED",
            "detail": f"kind='{kind}'"})
    # Name the credential actually exercised: an `xsiam` preflight may have run
    # on the equivalent `xsiam_tenant` row, and a DC reading "ok" must be able
    # to tell which vault entry that verdict belongs to.
    report["credential"] = credential
    return report


async def _preflight_tenant(db: AsyncSession, row: Any, datasets: Optional[str],
                            response_layer: bool = True) -> Any:
    """Build the tenant client and run the XQL/dataset/clock stages."""
    from connectors import preflight as pf
    from integrations.xsiam.exceptions import XsiamConfigError
    from integrations.xsiam.guards import xql_capability_granted
    from integrations.xsiam.loader import load_xsiam_client

    names = [d.strip() for d in (datasets or "").split(",") if d.strip()]
    config = row.config or {}
    # Absent capabilities == the legacy default, read-only. Never infer XQL from
    # the mere existence of a registration: that would silently grant a metered
    # authorisation to every credential already in the field.
    xql_authorised = xql_capability_granted(config)
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
    report = await pf.preflight_tenant(
        client, integration_name=row.name, base_url_host=host,
        datasets=names, xql_authorised=xql_authorised,
        response_layer=response_layer)
    # Persist only a stage that actually read something: a skipped stage (an
    # earlier stage blocked the ladder) carries no payload and must not erase
    # the last good read. The stage verdict is stored WITH the layer: an XQL
    # failure leaves the layer PRESENT / RESPONSE_LAYER_OK while the stage is
    # degraded, and storing the layer alone would show it green on Readiness.
    for stage in report.stages:
        if stage.id == "response_layer" and "response_layer" in (stage.extra or {}):
            try:
                row.response_layer = {
                    **stage.extra["response_layer"],
                    "stage": {"status": stage.status, "code": stage.code,
                              "detail": stage.detail},
                }
                row.response_layer_read_at = datetime.utcnow()
                await db.commit()
                stage.extra["stored"] = True
            except Exception as exc:  # noqa: BLE001 — a failed store must not drop the report
                logger.warning("response_layer: preflight result for %s not stored: %s",
                               report.tenant, exc, exc_info=True)
                try:
                    await db.rollback()
                except Exception:  # noqa: BLE001
                    logger.warning("response_layer: rollback failed", exc_info=True)
                stage.extra["stored"] = False
                stage.extra["stored_note"] = (
                    f"this result was read but not stored ({type(exc).__name__}); "
                    "Readiness and the launch advisory still show the previous stored "
                    "read, if any. Run preflight again.")
            break
    return report


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

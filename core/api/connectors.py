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

    out = []
    for c in available_connectors():
        configured = by_kind.get(c["kind"], [])
        out.append({
            **c,
            "configured": bool(configured),
            "integrations": [
                {
                    "name": i.name,
                    "verified_ok": i.last_verified_ok,
                    "last_verified_at": i.last_verified_at.isoformat() if i.last_verified_at else None,
                }
                for i in configured
            ],
        })
    return {"connectors": out}


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
    alerts = [ObservedAlert.from_dict(o, default_source="manual") for o in body.observations]
    verdicts = reconcile(results, alerts, window_seconds=body.window_seconds,
                         only_unobserved=not body.reevaluate)
    summary, _ = await apply_verdicts(db, run_id, results, verdicts, source="manual-import")
    summary["ingested"] = len(alerts)
    summary["verdicts"] = [v.to_dict() for v in verdicts if v.matched]
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

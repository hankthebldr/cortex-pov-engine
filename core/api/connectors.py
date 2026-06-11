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
from datetime import datetime, timedelta
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from connectors import (
    ConnectorConfig,
    ObservedAlert,
    available_connectors,
    get_connector,
    reconcile,
)
from connectors.matcher import DEFAULT_WINDOW_SECONDS
from database import get_db
from events import event_bus
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


async def _apply_verdicts(
    db: AsyncSession, run_id: str, results: list[Result], verdicts: list, source: str
) -> dict[str, Any]:
    """Persist matched verdicts (set observed/observed_at), emit SSE, summarise."""
    by_id = {r.id: r for r in results}
    matched = 0
    for v in verdicts:
        if not v.matched:
            continue
        result = by_id.get(v.result_id)
        if result is None:
            continue
        result.observed = True
        result.observed_at = v.observed_at
        note = (f"auto-validated via {source} "
                f"(matched on {', '.join(v.matched_on)}; "
                f"alert={v.alert_external_id or v.alert_name or 'n/a'})")
        result.notes = ((result.notes + " | ") if result.notes else "") + note
        matched += 1

    await db.commit()

    for v in verdicts:
        if not v.matched:
            continue
        result = by_id.get(v.result_id)
        if result is None:
            continue
        try:
            await event_bus.publish(run_id, {
                "type": "result.observed",
                "run_id": run_id,
                "data": {
                    "result_id": result.id, "observed": True, "plane": result.plane,
                    "mttd_seconds": result.mttd_seconds, "source": source,
                    "matched_on": v.matched_on,
                },
            })
        except Exception:  # pragma: no cover - defensive
            logger.exception("event_bus publish failed result_id=%d", result.id)

    return _efficacy_summary(results, matched, source)


def _efficacy_summary(results: list[Result], newly_matched: int, source: str) -> dict[str, Any]:
    total = len(results)
    observed = sum(1 for r in results if r.observed)
    mttds = [r.mttd_seconds for r in results if r.mttd_seconds is not None]
    return {
        "source": source,
        "total_expected": total,
        "observed": observed,
        "newly_matched": newly_matched,
        "coverage_pct": round(observed / total * 100, 1) if total else 0.0,
        "mttd": {
            "count": len(mttds),
            "avg_seconds": round(sum(mttds) / len(mttds), 1) if mttds else None,
            "min_seconds": round(min(mttds), 1) if mttds else None,
            "max_seconds": round(max(mttds), 1) if mttds else None,
        },
    }


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
    _run, results = await _load_run_results(db, run_id)
    alerts = [ObservedAlert.from_dict(o, default_source="manual") for o in body.observations]
    verdicts = reconcile(results, alerts, window_seconds=body.window_seconds,
                         only_unobserved=not body.reevaluate)
    summary = await _apply_verdicts(db, run_id, results, verdicts, source="manual-import")
    summary["ingested"] = len(alerts)
    summary["verdicts"] = [v.to_dict() for v in verdicts if v.matched]
    return summary


# ---------------------------------------------------------------------------
# Credential-backed connector pull
# ---------------------------------------------------------------------------

@runs_reconcile_router.post("/{run_id}/reconcile")
async def reconcile_run(
    run_id: str,
    connector: str = Query(..., description="connector kind, e.g. 'xsiam'"),
    integration: Optional[str] = Query(None, description="integration name (defaults to the first of this kind)"),
    window_seconds: int = Query(DEFAULT_WINDOW_SECONDS, ge=60, le=86400),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Pull observations from a configured integration and auto-validate.

    Resolves the integration credential from the encrypted vault, pulls alerts
    for the run's execution window (± a margin), correlates, and sets MTTD.
    """
    conn = get_connector(connector)
    if conn is None:
        raise HTTPException(status_code=404, detail={
            "error": "Unknown connector", "code": "CONNECTOR_NOT_FOUND",
            "detail": f"connector={connector}"})

    run, results = await _load_run_results(db, run_id)

    # Resolve the integration credential.
    store = CredentialStore(db)
    integrations = await store.list_integrations(kind=connector)
    if not integrations:
        raise HTTPException(status_code=400, detail={
            "error": "No integration configured", "code": "NO_INTEGRATION",
            "detail": f"configure an integration of kind '{connector}' first"})
    integ = next((i for i in integrations if i.name == integration), integrations[0]) \
        if integration else integrations[0]
    try:
        secret = await store.get_integration_secret(integ.name)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail={
            "error": "Credential decrypt failed", "code": "CREDENTIAL_ERROR",
            "detail": str(e)})

    cfg = ConnectorConfig(integration_name=integ.name, config=integ.config or {}, secret=secret)

    # Window: from the earliest step execution to now (+ a small margin), or the
    # run's started/completed bounds.
    exec_times = [r.executed_at for r in results if r.executed_at]
    since = min(exec_times) if exec_times else (run.started_at or datetime.utcnow() - timedelta(hours=1))
    until = datetime.utcnow() + timedelta(minutes=5)

    pull = conn.pull(cfg, since=since, until=until)
    if not pull.ok:
        # Record the verification failure on the integration for visibility.
        await store.mark_integration_verified(integ.name, ok=False, error=pull.error)
        await db.commit()
        raise HTTPException(status_code=502, detail={
            "error": "Connector pull failed", "code": "CONNECTOR_PULL_FAILED",
            "detail": pull.error, "connector_detail": pull.detail})

    await store.mark_integration_verified(integ.name, ok=True, error=None)
    verdicts = reconcile(results, pull.observations, window_seconds=window_seconds)
    summary = await _apply_verdicts(db, run_id, results, verdicts,
                                    source=f"{connector}:{integ.name}")
    summary["pulled"] = len(pull.observations)
    summary["verdicts"] = [v.to_dict() for v in verdicts if v.matched]
    return summary

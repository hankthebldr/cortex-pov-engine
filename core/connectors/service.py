"""Shared reconciliation service — used by the API handler AND the auto loop.

The on-demand endpoint (``POST /api/runs/{id}/reconcile``) and the background
auto-reconcile sweep must behave identically, so the credential resolution +
pull + match + apply core lives here once. Functions return structured results
and raise plain exceptions (the API layer maps them to HTTP; the loop logs and
moves on).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from connectors import ConnectorConfig, get_connector, reconcile
from connectors.base import HttpFetcher
from events import event_bus
from models import Result, Run
from security.credentials import CredentialStore

logger = logging.getLogger("cortexsim.connectors.service")


class ReconcileError(Exception):
    """Raised when a reconcile cannot proceed (unknown connector / no
    integration / pull failure). Carries a machine ``code`` for the API."""

    def __init__(self, code: str, message: str, *, detail: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail


@dataclass
class ReconcileOutcome:
    summary: dict[str, Any]
    newly_matched: int


async def reconcile_run(
    db: AsyncSession,
    run: Run,
    results: list[Result],
    *,
    connector_kind: str,
    integration_name: Optional[str] = None,
    window_seconds: int = 3600,
    fetcher: Optional[HttpFetcher] = None,
) -> ReconcileOutcome:
    """Resolve the integration credential, pull observations for the run's
    window, correlate to ``results``, persist matches, emit SSE.

    Raises :class:`ReconcileError` (never HTTP) on any failure.
    """
    conn = get_connector(connector_kind, fetcher=fetcher)
    if conn is None:
        raise ReconcileError("CONNECTOR_NOT_FOUND",
                             f"unknown connector '{connector_kind}'")

    store = CredentialStore(db)
    integrations = await store.list_integrations(kind=connector_kind)
    if not integrations:
        raise ReconcileError("NO_INTEGRATION",
                             f"configure an integration of kind '{connector_kind}' first")
    integ = (next((i for i in integrations if i.name == integration_name), integrations[0])
             if integration_name else integrations[0])
    try:
        secret = await store.get_integration_secret(integ.name)
    except Exception as e:  # noqa: BLE001
        raise ReconcileError("CREDENTIAL_ERROR", str(e))

    cfg = ConnectorConfig(integration_name=integ.name, config=integ.config or {}, secret=secret)

    exec_times = [r.executed_at for r in results if r.executed_at]
    since = (min(exec_times) if exec_times
             else (run.started_at or datetime.utcnow() - timedelta(hours=1)))
    until = datetime.utcnow() + timedelta(minutes=5)

    pull = conn.pull(cfg, since=since, until=until)
    if not pull.ok:
        await store.mark_integration_verified(integ.name, ok=False, error=pull.error)
        await db.commit()
        raise ReconcileError("CONNECTOR_PULL_FAILED", pull.error or "pull failed",
                             detail=pull.detail)

    await store.mark_integration_verified(integ.name, ok=True, error=None)
    verdicts = reconcile(results, pull.observations, window_seconds=window_seconds)
    summary, newly = await apply_verdicts(db, run.run_id, results, verdicts,
                                          source=f"{connector_kind}:{integ.name}")
    summary["pulled"] = len(pull.observations)
    summary["verdicts"] = [v.to_dict() for v in verdicts if v.matched]
    return ReconcileOutcome(summary=summary, newly_matched=newly)


async def apply_verdicts(
    db: AsyncSession, run_id: str, results: list[Result], verdicts: list, source: str
) -> tuple[dict[str, Any], int]:
    """Persist matched verdicts (observed/observed_at), emit SSE, summarise.

    Returns ``(summary, newly_matched)``.
    """
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
                "type": "result.observed", "run_id": run_id,
                "data": {"result_id": result.id, "observed": True, "plane": result.plane,
                         "mttd_seconds": result.mttd_seconds, "source": source,
                         "matched_on": v.matched_on},
            })
        except Exception:  # pragma: no cover - defensive
            logger.exception("event_bus publish failed result_id=%d", result.id)

    return efficacy_summary(results, matched, source), matched


def efficacy_summary(results: list[Result], newly_matched: int, source: str) -> dict[str, Any]:
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
# Auto-reconcile sweep (opt-in background loop)
# ---------------------------------------------------------------------------

async def auto_reconcile_once(
    db: AsyncSession, *, lookback_seconds: int, window_seconds: int,
    fetcher: Optional[HttpFetcher] = None,
) -> dict[str, Any]:
    """One sweep: for every configured connector kind, reconcile each recent run
    that still has unobserved detections. Returns counts for logging.

    A run is a candidate when it finished recently (``completed_at`` within
    ``lookback_seconds``, or no completion but started within it) and at least
    one of its results is still unobserved. Connectors with no integration are
    skipped silently.
    """
    from connectors.base import available_connectors  # noqa: PLC0415

    store = CredentialStore(db)
    configured_kinds = {i.kind for i in await store.list_integrations()}
    active_kinds = [c["kind"] for c in available_connectors() if c["kind"] in configured_kinds]
    if not active_kinds:
        return {"runs_considered": 0, "runs_reconciled": 0, "newly_matched": 0, "kinds": []}

    cutoff = datetime.utcnow() - timedelta(seconds=lookback_seconds)
    runs = list((await db.execute(
        select(Run).where(Run.started_at >= cutoff)
    )).scalars().all())

    runs_considered = 0
    runs_reconciled = 0
    newly_total = 0
    for run in runs:
        results = list((await db.execute(
            select(Result).where(Result.run_id == run.run_id)
        )).scalars().all())
        if not any((not r.observed) and r.executed_at for r in results):
            continue
        runs_considered += 1
        for kind in active_kinds:
            try:
                outcome = await reconcile_run(
                    db, run, results, connector_kind=kind,
                    window_seconds=window_seconds, fetcher=fetcher,
                )
            except ReconcileError as e:
                logger.debug("auto-reconcile skip run=%s kind=%s: %s",
                             run.run_id, kind, e.code)
                continue
            if outcome.newly_matched:
                runs_reconciled += 1
                newly_total += outcome.newly_matched

    return {
        "runs_considered": runs_considered,
        "runs_reconciled": runs_reconciled,
        "newly_matched": newly_total,
        "kinds": active_kinds,
    }


async def auto_reconcile_loop(interval_seconds: int, lookback_seconds: int,
                              window_seconds: int) -> None:
    """Run :func:`auto_reconcile_once` forever on a fixed cadence. Opt-in;
    started from the lifespan only when CORTEXSIM_AUTO_RECONCILE is set.
    Transient errors are logged and never kill the loop."""
    import asyncio  # noqa: PLC0415
    from database import AsyncSessionLocal  # noqa: PLC0415

    logger.info("auto-reconcile loop started interval=%ds lookback=%ds",
                interval_seconds, lookback_seconds)
    try:
        while True:
            await asyncio.sleep(interval_seconds)
            try:
                async with AsyncSessionLocal() as db:
                    stats = await auto_reconcile_once(
                        db, lookback_seconds=lookback_seconds,
                        window_seconds=window_seconds)
                if stats["newly_matched"]:
                    logger.info("auto-reconcile: %d detection(s) validated across %d run(s) via %s",
                                stats["newly_matched"], stats["runs_reconciled"], stats["kinds"])
            except Exception:  # pragma: no cover - defensive
                logger.exception("auto-reconcile sweep failed — continuing")
    except asyncio.CancelledError:  # pragma: no cover - shutdown path
        logger.info("auto-reconcile loop cancelled")
        raise

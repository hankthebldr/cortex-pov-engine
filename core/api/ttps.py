"""
CortexSim API — /api/ttps router.

Read-only browser surface over the TTP corpus that lives under
``detection_scanner/ttps/*.json``. The TTP catalog singleton
(``engine.ttp_catalog.catalog``) is populated at startup; this router
serves the corpus as JSON for the Coverage → TTP Browser sub-tab.

Endpoints:
  GET  /api/ttps                  — list TTP cards (status / plane /
                                    tactic filters)
  GET  /api/ttps/{ttp_id}         — full card detail + reverse cross-
                                    references to the tool-adapter catalog
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from engine.ttp_catalog import catalog as ttp_catalog
from models import Result, Run
from tools.adapter_catalog import catalog as adapter_catalog

logger = logging.getLogger("cortexsim.api.ttps")

router = APIRouter(prefix="/ttps", tags=["ttps"])


# ---------------------------------------------------------------------------
# Card summary helpers
# ---------------------------------------------------------------------------


def _summary(ttp_id: str, raw: dict[str, Any]) -> dict[str, Any]:
    """Slim card payload for the grid. Detail panel fetches the full
    document via ``/api/ttps/{ttp_id}``."""
    identity = raw.get("identity") or {}
    metadata = raw.get("metadata") or {}
    pov = metadata.get("pov_engine") or {}
    mitre = raw.get("mitre_attack") or {}
    threat = raw.get("threat_context") or {}
    detections = raw.get("detections") or {}
    panw = raw.get("panw_mapping") or {}

    techniques = mitre.get("techniques") or []
    technique_ids = [
        (t.get("subtechnique_id") or t.get("technique_id"))
        for t in techniques
        if isinstance(t, dict)
    ]
    technique_ids = [t for t in technique_ids if t]

    tactic_ids: set[str] = set()
    for t in techniques:
        if isinstance(t, dict):
            for tid in (t.get("tactic_ids") or []):
                if isinstance(tid, str):
                    tactic_ids.add(tid)

    actor_names = [
        a.get("name") for a in (threat.get("actors") or [])
        if isinstance(a, dict) and isinstance(a.get("name"), str)
    ]

    return {
        "id":                ttp_id,
        "name":              identity.get("name") or ttp_id,
        "status":            raw.get("status") or "unknown",
        "summary":           identity.get("summary") or "",
        "tags":              list(metadata.get("tags") or []),
        "platforms":         list(pov.get("platforms") or []),
        "simulation_class":  pov.get("simulation_class"),
        "destructive":       bool(pov.get("destructive")),
        "technique_ids":     technique_ids,
        "tactic_ids":        sorted(tactic_ids),
        "kill_chain_phase":  mitre.get("kill_chain_phase"),
        "actor_names":       actor_names,
        "detection_counts": {
            "iocs":              len(detections.get("iocs")              or []),
            "biocs":             len(detections.get("biocs")             or []),
            "xql_queries":       len(detections.get("xql_queries")       or []),
            "correlation_rules": len(detections.get("correlation_rules") or []),
            "analytics_modules": len(detections.get("analytics_modules") or []),
        },
        "panw_products": [
            p.get("module") for p in (panw.get("products") or [])
            if isinstance(p, dict) and isinstance(p.get("module"), str)
        ],
    }


def _adapters_referencing(ttp_id: str) -> list[dict[str, Any]]:
    """Reverse lookup: every adapter whose ``ttp_refs[]`` cites this TTP.

    This is the cross-reference that closes the loop for the TTP detail
    panel — the operator sees "Mimikatz, Rubeus, pypykatz" exercise this
    detection, with one click each to jump to the Tool Adapter catalog.
    """
    out: list[dict[str, Any]] = []
    for adapter in adapter_catalog.all():
        if ttp_id in (adapter.ttp_refs or []):
            out.append({
                "adapter_id":   adapter.adapter_id,
                "name":         adapter.name,
                "tier":         adapter.tier,
                "category":     adapter.category,
                "safety_class": adapter.safety_class,
            })
    out.sort(key=lambda d: d["adapter_id"])
    return out


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("")
async def list_ttps(
    status: Optional[str] = Query(None, description="Filter by status (active|draft|deprecated)"),
    tactic: Optional[str] = Query(None, description="Filter by MITRE tactic id (e.g. TA0006)"),
    platform: Optional[str] = Query(None, description="Filter by pov_engine.platforms entry (e.g. linux)"),
):
    """
    List every TTP card with optional filters. Filters compose with
    logical AND; unknown filter values quietly return an empty list
    (defensive against UI taxonomy drift, mirroring the adapter list
    endpoint's behaviour).
    """
    raw_by_ttp = ttp_catalog.all_raw()
    items: list[dict[str, Any]] = []
    for ttp_id, raw in raw_by_ttp.items():
        s = _summary(ttp_id, raw)
        if status and s["status"] != status:
            continue
        if tactic and tactic not in s["tactic_ids"]:
            continue
        if platform and platform not in s["platforms"]:
            continue
        items.append(s)

    items.sort(key=lambda d: d["id"])
    logger.info(
        "list_ttps count=%d filters=status=%s tactic=%s platform=%s",
        len(items), status, tactic, platform,
    )
    return {"ttps": items, "total": len(items)}


@router.get("/{ttp_id}")
async def get_ttp(ttp_id: str):
    """
    Full TTP card document + reverse cross-references to the tool-
    adapter catalog. The browser detail panel renders identity.summary,
    threat_context.actors, mitre_attack chain, detections by kind, and
    the panw_mapping product list — straight from the raw JSON.
    """
    raw = ttp_catalog.raw(ttp_id)
    if raw is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error":  "TTP not found",
                "code":   "TTP_NOT_FOUND",
                "detail": f"ttp_id='{ttp_id}'",
            },
        )
    return {
        **raw,
        "referenced_by_adapters": _adapters_referencing(ttp_id),
    }


@router.get("/{ttp_id}/runs")
async def get_ttp_runs(
    ttp_id: str,
    limit: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """
    Run history for a TTP — every Run whose seeded Results cite this
    ``ttp_id`` in ``Result.ttp_ref``.

    Closes the temporal loop on the TTP card: the static detection
    content + adapter cross-refs answer "what does this look like and
    what runs it"; this endpoint answers "did we actually exercise it,
    and how did it land?"

    Each entry rolls up the per-Result rows for that TTP within the
    Run into a single line: scenario_id · executed_at · observed /
    expected counts · min MTTD (seconds) across observed detections.
    Sorted newest-first.
    """
    if ttp_catalog.raw(ttp_id) is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error":  "TTP not found",
                "code":   "TTP_NOT_FOUND",
                "detail": f"ttp_id='{ttp_id}'",
            },
        )

    # Join Result → Run so we can surface scenario_id + run.started_at
    # in one shot. Results are filtered by ttp_ref (indexed column).
    stmt = (
        select(Result, Run)
        .join(Run, Result.run_id == Run.run_id)
        .where(Result.ttp_ref == ttp_id)
        .order_by(Run.started_at.desc(), Result.id.asc())
    )
    rows = (await db.execute(stmt)).all()

    # Roll up per run_id — the same run can fire multiple Result rows
    # for one TTP (one per expected_detection).
    by_run: dict[str, dict[str, Any]] = {}
    for result, run in rows:
        bucket = by_run.get(run.run_id)
        if bucket is None:
            bucket = {
                "run_id":      run.run_id,
                "scenario_id": run.scenario_id,
                "run_status":  run.status,
                "started_at":  run.started_at.isoformat() if run.started_at else None,
                "expected":    0,
                "observed":    0,
                "min_mttd_seconds": None,
                "detection_ids": [],
            }
            by_run[run.run_id] = bucket
        bucket["expected"] += 1
        if result.observed:
            bucket["observed"] += 1
        if result.detection_id and result.detection_id not in bucket["detection_ids"]:
            bucket["detection_ids"].append(result.detection_id)
        mttd = result.mttd_seconds
        if mttd is not None:
            cur = bucket["min_mttd_seconds"]
            bucket["min_mttd_seconds"] = mttd if cur is None else min(cur, mttd)

    runs = list(by_run.values())[:limit]
    logger.info("get_ttp_runs ttp_id=%s runs=%d", ttp_id, len(runs))
    return {"ttp_id": ttp_id, "runs": runs, "total": len(runs)}

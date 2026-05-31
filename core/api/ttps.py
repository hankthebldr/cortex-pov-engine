"""
CortexSim API — /api/ttps router.

Read-only browser surface over the TTP corpus that lives under
``detection_scanner/ttps/*.json``. The TTP catalog singleton
(``engine.ttp_catalog.catalog``) is populated at startup; this router
serves the corpus as JSON for the Coverage → TTP Browser sub-tab.

Endpoints:
  GET  /api/ttps                       — list TTP cards (status / plane /
                                         tactic filters)
  GET  /api/ttps/_schema               — JSON Schema for authoring (live validate)
  GET  /api/ttps/{ttp_id}              — full card detail + reverse cross-refs
  GET  /api/ttps/{ttp_id}/runs         — run history for a TTP

Authoring endpoints (gated on ``CORTEXSIM_AUTHORING_ENABLED=true``):
  POST /api/ttps                       — create a draft TTP under _drafts/
  PUT  /api/ttps/{ttp_id}              — update an existing TTP (draft or active)
  POST /api/ttps/{ttp_id}/promote      — move draft → active corpus
  POST /api/ttps/_reload               — hot-reload the catalog from disk

## Authoring — architectural decisions (issue #59)

These were the three open questions issue #59 flagged. Resolutions
chosen for the lab/detection-testing use case:

1. **Write story.** Direct filesystem write to ``detection_scanner/
   ttps/_drafts/`` for new cards; promotion moves to the root dir.
   Customer-deploy environments stay read-only via the
   ``CORTEXSIM_AUTHORING_ENABLED`` env gate (default False). DC
   laptops + lab boxes flip the gate on; SaaS does not.

2. **Reload story.** Each write triggers a synchronous
   ``ttp_catalog.load(corpus_dir)`` so the next request sees the
   change. Existing read requests resolve against the previous
   in-memory snapshot (Python attribute reads are atomic) — no
   write-while-execute hazard. Runs in flight don't observe the
   change, which matches the expectation that scenario seeding
   captures the corpus state at run start.

3. **Schema validation.** Each POST / PUT validates against
   ``detection_scanner/schema/ttp-entry.schema.json`` before write.
   Returns 422 with the jsonschema error path so the UI can mark
   the offending field. The schema is the same one the existing
   ``test_every_active_ttp_validates`` floor uses — authoring can't
   land a card that the test floor would reject.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

import jsonschema
from fastapi import APIRouter, Body, Depends, HTTPException, Query
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


# ---------------------------------------------------------------------------
# /_schema must come BEFORE /{ttp_id} so FastAPI's first-match-wins router
# doesn't shadow it with the catch-all. The implementation lives at the
# bottom of the file (next to the other authoring helpers); this is just a
# pass-through registration to preserve route order.
# ---------------------------------------------------------------------------


@router.get("/_schema")
async def _route_schema():
    return _load_schema()


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


# ---------------------------------------------------------------------------
# Authoring — gated on CORTEXSIM_AUTHORING_ENABLED env var
# ---------------------------------------------------------------------------


# Resolve corpus + schema paths at import time. Both are relative to the repo
# root (which CORTEXSIM_BASE_DIR points at on a deployed install).
_BASE_DIR = Path(os.environ.get("CORTEXSIM_BASE_DIR", Path(__file__).resolve().parents[2]))
_CORPUS_DIR  = _BASE_DIR / "detection_scanner" / "ttps"
_DRAFTS_DIR  = _CORPUS_DIR / "_drafts"
_SCHEMA_PATH = _BASE_DIR / "detection_scanner" / "schema" / "ttp-entry.schema.json"

# Filenames the catalog loader will index. Authoring writes always live under
# one of these two roots; everything else is read-only.
_ALLOWED_PARENTS = {_CORPUS_DIR.resolve(), _DRAFTS_DIR.resolve()}

# TTP ids must look like TTP-YYYY-NNNN — locks out path-traversal attempts via
# the ``{ttp_id}`` URL param (``../../etc/passwd`` etc.).
_TTP_ID_RE = re.compile(r"^TTP-\d{4}-\d{4}$")

# Filenames mirror the existing corpus pattern: ``{ttp_id}-{kebab-name}.json``.
# Slugified from identity.name; the operator never types the filename.
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _authoring_enabled() -> bool:
    return os.environ.get("CORTEXSIM_AUTHORING_ENABLED", "").lower() in {"1", "true", "yes"}


def _require_authoring() -> None:
    if not _authoring_enabled():
        raise HTTPException(
            status_code=403,
            detail={
                "error":  "Authoring disabled",
                "code":   "AUTHORING_DISABLED",
                "detail": "Set CORTEXSIM_AUTHORING_ENABLED=true on the server to enable TTP authoring.",
            },
        )


def _load_schema() -> dict[str, Any]:
    if not _SCHEMA_PATH.is_file():
        raise HTTPException(
            status_code=500,
            detail={
                "error":  "Schema missing",
                "code":   "SCHEMA_NOT_FOUND",
                "detail": f"expected schema at {_SCHEMA_PATH}",
            },
        )
    try:
        return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=500,
            detail={"error": "Schema invalid JSON", "code": "SCHEMA_DECODE", "detail": str(e)},
        )


def _validate_or_422(doc: dict[str, Any]) -> None:
    """Validate ``doc`` against the TTP schema. Raises 422 on failure."""
    schema = _load_schema()
    try:
        jsonschema.validate(doc, schema)
    except jsonschema.ValidationError as e:
        raise HTTPException(
            status_code=422,
            detail={
                "error":  "Schema validation failed",
                "code":   "TTP_SCHEMA_INVALID",
                "detail": e.message,
                "path":   list(e.absolute_path),
            },
        )


def _validate_ttp_id(ttp_id: str) -> None:
    if not _TTP_ID_RE.match(ttp_id):
        raise HTTPException(
            status_code=400,
            detail={
                "error":  "Invalid TTP id",
                "code":   "TTP_ID_INVALID",
                "detail": f"ttp_id must match {_TTP_ID_RE.pattern}",
            },
        )


def _slug_name(name: str) -> str:
    return _SLUG_RE.sub("-", name.lower()).strip("-")[:80]


def _find_existing_path(ttp_id: str) -> Optional[Path]:
    """Locate the on-disk file for a TTP id, in either active or drafts dir."""
    for parent in (_CORPUS_DIR, _DRAFTS_DIR):
        if not parent.is_dir():
            continue
        for p in parent.glob(f"{ttp_id}-*.json"):
            return p
    return None


def _ensure_safe_path(p: Path) -> None:
    """Defensive: confirm a write target is under an allowed parent dir."""
    resolved = p.resolve()
    if not any(resolved.is_relative_to(a) for a in _ALLOWED_PARENTS):
        raise HTTPException(
            status_code=400,
            detail={
                "error":  "Refusing write outside corpus",
                "code":   "PATH_ESCAPE",
                "detail": str(resolved),
            },
        )


def _reload_catalog() -> int:
    """Reload the in-memory catalog from disk. Returns count loaded."""
    return ttp_catalog.load(str(_CORPUS_DIR))


@router.post("/_reload")
async def reload_catalog():
    """Hot-reload the catalog from disk. Authoring-gated. Useful when the
    operator edits a file out-of-band (e.g. via their editor) and wants the
    UI to pick it up without restarting SimCore."""
    _require_authoring()
    count = _reload_catalog()
    logger.info("catalog reloaded count=%d", count)
    return {"loaded": count, "entries": len(ttp_catalog.all_entries())}


@router.post("", status_code=201)
async def create_ttp(payload: dict[str, Any] = Body(...)):
    """
    Create a new draft TTP card under ``detection_scanner/ttps/_drafts/``.

    The payload must validate against the TTP schema *and* carry a
    fresh ``id`` (no overwrite). The new card always lands in
    ``_drafts/`` regardless of the payload's ``status`` field — the
    catalog loader filters drafts out of the active corpus, so
    promotion is a deliberate second step.
    """
    _require_authoring()

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail={
            "error": "Body must be a JSON object", "code": "BAD_PAYLOAD",
        })

    ttp_id = payload.get("id")
    if not isinstance(ttp_id, str):
        raise HTTPException(status_code=400, detail={
            "error": "id required", "code": "MISSING_ID",
        })
    _validate_ttp_id(ttp_id)

    if _find_existing_path(ttp_id) is not None:
        raise HTTPException(status_code=409, detail={
            "error":  "TTP id already exists",
            "code":   "TTP_ID_CONFLICT",
            "detail": f"{ttp_id} already on disk — use PUT to update",
        })

    # Force status to draft on create; promotion is a separate endpoint.
    payload["status"] = "draft"
    _validate_or_422(payload)

    _DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    name = (payload.get("identity") or {}).get("name") or ttp_id
    filename = f"{ttp_id}-{_slug_name(name)}.json"
    target = _DRAFTS_DIR / filename
    _ensure_safe_path(target)
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    # Drafts aren't indexed by the loader, but reload anyway so list_ttps
    # callers don't have a stale view of any peer files that may have
    # been edited out-of-band.
    _reload_catalog()
    logger.info("created draft ttp_id=%s path=%s", ttp_id, target)
    return {"ttp_id": ttp_id, "status": "draft", "path": str(target.relative_to(_BASE_DIR))}


@router.put("/{ttp_id}")
async def update_ttp(ttp_id: str, payload: dict[str, Any] = Body(...)):
    """
    Update an existing TTP card in place. The payload must validate
    against the schema and its ``id`` must equal ``ttp_id``. Edits
    preserve the existing file location (drafts stay drafts, active
    stays active); use ``POST /{ttp_id}/promote`` to move drafts to
    active.
    """
    _require_authoring()
    _validate_ttp_id(ttp_id)
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail={
            "error": "Body must be a JSON object", "code": "BAD_PAYLOAD",
        })
    if payload.get("id") != ttp_id:
        raise HTTPException(status_code=400, detail={
            "error":  "Payload id must match URL",
            "code":   "ID_MISMATCH",
            "detail": f"url={ttp_id} payload_id={payload.get('id')}",
        })

    existing = _find_existing_path(ttp_id)
    if existing is None:
        raise HTTPException(status_code=404, detail={
            "error": "TTP not found", "code": "TTP_NOT_FOUND",
            "detail": f"ttp_id='{ttp_id}'",
        })

    _validate_or_422(payload)
    _ensure_safe_path(existing)
    existing.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    _reload_catalog()
    logger.info("updated ttp_id=%s path=%s", ttp_id, existing)
    return {
        "ttp_id": ttp_id,
        "status": payload.get("status"),
        "path":   str(existing.relative_to(_BASE_DIR)),
    }


@router.post("/{ttp_id}/promote")
async def promote_ttp(ttp_id: str):
    """
    Promote a draft TTP from ``_drafts/`` to the active corpus root.
    Sets ``status`` to ``active`` on disk and re-validates against the
    schema so a previously-saved-but-invalid draft can't sneak in. No-op
    if the file is already in the active root.
    """
    _require_authoring()
    _validate_ttp_id(ttp_id)

    existing = _find_existing_path(ttp_id)
    if existing is None:
        raise HTTPException(status_code=404, detail={
            "error": "TTP not found", "code": "TTP_NOT_FOUND",
        })

    # Idempotent: already-active card is left alone.
    if existing.parent.resolve() == _CORPUS_DIR.resolve():
        return {"ttp_id": ttp_id, "status": "active", "moved": False,
                "path": str(existing.relative_to(_BASE_DIR))}

    doc = json.loads(existing.read_text(encoding="utf-8"))
    doc["status"] = "active"
    _validate_or_422(doc)

    target = _CORPUS_DIR / existing.name
    _ensure_safe_path(target)
    target.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    existing.unlink()
    _reload_catalog()
    logger.info("promoted ttp_id=%s from=%s to=%s", ttp_id, existing, target)
    return {"ttp_id": ttp_id, "status": "active", "moved": True,
            "path": str(target.relative_to(_BASE_DIR))}

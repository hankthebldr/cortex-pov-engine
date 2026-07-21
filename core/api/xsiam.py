# core/api/xsiam.py
"""XSIAM live-tenant operations router (decision B).

Tenant CRUD lives in the generic /api/credentials/integrations endpoints
(kind="xsiam_tenant"). This router only does things that require talking to the
live tenant: liveness, health, ingestion metrics, and XQL.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import get_db
from security import CredentialStore
from integrations.xsiam.loader import XSIAM_KIND, load_xsiam_client
from integrations.xsiam.queries import INGESTION_HEALTH_XQL, shape_ingestion_results
from integrations.xsiam.exceptions import XsiamApiError, XsiamConfigError, XsiamError
from integrations.xsiam.operations.catalog import catalog as op_catalog

router = APIRouter(prefix="/xsiam", tags=["xsiam"])

_DEFAULT_TIMEFRAME = {"relativeTime": 24 * 60 * 60 * 1000}  # last 24h


class XqlRequest(BaseModel):
    query: str = Field(..., min_length=1)
    timeframe: dict[str, Any] = Field(default_factory=lambda: dict(_DEFAULT_TIMEFRAME))


@router.post("/tenants/{name}/test")
async def test_tenant(name: str, session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    store = CredentialStore(session)
    client = await load_xsiam_client(session, name)
    try:
        try:
            status = await client.healthcheck()
        except XsiamApiError as exc:
            if exc.upstream_status == 403:  # healthcheck is license-gated
                count = await client.ping_via_endpoints()
                status = {"status": "reachable (healthcheck license-gated)",
                          "endpoint_sample": count}
            else:
                raise
    except XsiamError as exc:
        await store.mark_integration_verified(name, ok=False, error=exc.detail)
        await session.commit()
        raise
    await store.mark_integration_verified(name, ok=True)
    await session.commit()
    return {"ok": True, "status": status}


@router.get("/tenants/{name}/health")
async def tenant_health(name: str, session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    store = CredentialStore(session)
    row = await store.get_integration(name)
    if row is None or row.kind != XSIAM_KIND:
        raise HTTPException(status_code=404, detail=f"XSIAM tenant '{name}' not found")
    return {
        "name": name,
        "last_verified_ok": row.last_verified_ok,
        "last_verified_at": row.last_verified_at.isoformat() if row.last_verified_at else None,
        "last_verified_error": row.last_verified_error,
    }


@router.get("/tenants/{name}/metrics")
async def tenant_metrics(name: str, session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    client = await load_xsiam_client(session, name)
    reply = await client.run_xql(INGESTION_HEALTH_XQL, dict(_DEFAULT_TIMEFRAME))
    return {"sources": shape_ingestion_results(reply),
            "remaining_quota": reply.get("remaining_quota")}


@router.post("/tenants/{name}/xql")
async def start_xql(name: str, body: XqlRequest,
                    session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    client = await load_xsiam_client(session, name)
    query_id = await client.start_xql_query(body.query, body.timeframe)
    return {"query_id": query_id}


@router.get("/tenants/{name}/xql/{query_id}")
async def get_xql(name: str, query_id: str,
                  session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    client = await load_xsiam_client(session, name)
    reply = await client.get_query_results(query_id)
    return {"status": reply.get("status"),
            "results": reply.get("results"),
            "remaining_quota": reply.get("remaining_quota")}


# ── API operation catalog + gated executor ──────────────────────────────────
# The harness turns the declarative operation catalog into callable endpoints.
# Reads run against a configured tenant; write/destructive ops are gated by a
# global off-switch (settings) AND per-request consent, and default to dry-run.


class OperationExecuteRequest(BaseModel):
    path_params: dict[str, Any] = Field(default_factory=dict)
    query: dict[str, Any] = Field(default_factory=dict)
    body: Optional[dict[str, Any]] = None
    # dry_run None → live for reads, preview for write/destructive (safe default).
    dry_run: Optional[bool] = None
    consent: dict[str, bool] = Field(default_factory=dict)


def _op_or_404(op_id: str):
    op = op_catalog.find(op_id)
    if op is None:
        raise HTTPException(
            status_code=404,
            detail={"error": f"XSIAM operation '{op_id}' not found",
                    "code": "XSIAM_OP_NOT_FOUND", "detail": op_id},
        )
    return op


def _resolve_path(op, path_params: dict[str, Any]) -> str:
    path = op.path
    for p in op.path_params:
        val = path_params.get(p)
        if val in (None, ""):
            raise XsiamConfigError(f"operation {op.op_id} requires path param '{p}'")
        path = path.replace("{" + p + "}", str(val))
    return path


def _effective_dry_run(op, dry_run: Optional[bool], consent: dict[str, bool]) -> bool:
    """Decide whether this call is a preview, enforcing the write/destructive gate.

    Reads: live unless dry_run is explicitly True.
    Write/destructive: preview (dry-run) unless explicitly dry_run=False AND both
    the global flag and the matching consent key are set — otherwise blocked.
    """
    if op.access_class == "read":
        return bool(dry_run)
    if dry_run is None or dry_run is True:
        return True  # never mutate implicitly
    if op.access_class == "write":
        if not (settings.CORTEXSIM_XSIAM_ALLOW_WRITE and consent.get("write_authorized")):
            raise XsiamConfigError(
                "write operation blocked: set CORTEXSIM_XSIAM_ALLOW_WRITE=1 and "
                "consent.write_authorized=true, or keep dry_run to preview")
    else:  # destructive
        if not (settings.CORTEXSIM_XSIAM_ALLOW_DESTRUCTIVE and consent.get("destructive_authorized")):
            raise XsiamConfigError(
                "destructive operation blocked: set CORTEXSIM_XSIAM_ALLOW_DESTRUCTIVE=1 "
                "and consent.destructive_authorized=true, or keep dry_run to preview")
    return False


@router.get("/operations")
async def list_operations(
    category: Optional[str] = Query(None, description="filter by category"),
    access_class: Optional[str] = Query(None, description="read | write | destructive"),
) -> dict[str, Any]:
    ops = op_catalog.all()
    if category:
        ops = [o for o in ops if o.category == category]
    if access_class:
        ops = [o for o in ops if o.access_class == access_class]
    return {
        "count": len(ops),
        "categories": op_catalog.categories(),
        "operations": [o.summary() for o in ops],
    }


@router.get("/operations/{op_id}")
async def get_operation(op_id: str) -> dict[str, Any]:
    return _op_or_404(op_id).model_dump()


@router.post("/tenants/{name}/operations/{op_id}")
async def execute_operation(
    name: str, op_id: str, body: OperationExecuteRequest,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    op = _op_or_404(op_id)
    path = _resolve_path(op, body.path_params)
    dry = _effective_dry_run(op, body.dry_run, body.consent)

    payload = body.body
    if op.method == "POST" and payload is None:
        payload = {"request_data": {}}  # minimal envelope most POST ops expect
    request_echo = {
        "method": op.method, "path": path,
        "body": payload, "query": body.query or None,
    }
    if dry:
        return {"dry_run": True, "op_id": op.op_id,
                "access_class": op.access_class, "request": request_echo}

    client = await load_xsiam_client(session, name)
    reply = await client.request(
        op.method, path, json=payload, params=(body.query or None),
    )
    return {"dry_run": False, "op_id": op.op_id,
            "access_class": op.access_class, "reply": reply}

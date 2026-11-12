# core/api/xsiam.py
"""XSIAM live-tenant operations router (decision B).

Tenant CRUD lives in the generic /api/credentials/integrations endpoints
(kind="xsiam_tenant"). This router only does things that require talking to the
live tenant: liveness, health, ingestion metrics, and XQL.
"""
from __future__ import annotations

import contextlib
from typing import Any, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import get_db
from security import CredentialStore
from integrations.xsiam.client import (
    inspect_operation_body, is_xql_start_path, refuse_xql_write_stages,
)
from integrations.xsiam.loader import XSIAM_KIND, load_xsiam_client
from integrations.xsiam.queries import INGESTION_HEALTH_XQL, shape_ingestion_results
from integrations.xsiam.exceptions import (
    XsiamApiError, XsiamConfigError, XsiamError, XsiamQuotaError,
)
from integrations.xsiam import ledger as ledger_mod
from integrations.xsiam.guards import (
    XQL_OPERATION_IDS, XQL_POLL_OPERATION_IDS, ledger_key_for,
    refuse_xql_write_stage, require_xql_capability, tenant_budget, xql_query_text,
)
from integrations.xsiam.operations.catalog import catalog as op_catalog

router = APIRouter(prefix="/xsiam", tags=["xsiam"])

_DEFAULT_TIMEFRAME = {"relativeTime": 24 * 60 * 60 * 1000}  # last 24h


_START_XQL_OP_ID = "OP-XQL-START-XQL-QUERY"


@contextlib.asynccontextmanager
async def _budgeted(client: Any):
    """guards.tenant_budget, with ledger refusals rendered as a structured 429.

    ``ledger.charge`` raises RuntimeError subclasses that the global XsiamError
    handler does not know; without this they would surface as a 500. Raised
    before the body runs, so a refused budget never reaches the tenant.
    """
    try:
        async with tenant_budget(client):
            yield
    except (ledger_mod.QuotaBreakerOpen, ledger_mod.DailyBudgetExhausted) as exc:
        raise XsiamQuotaError(str(exc)) from exc


def _ledger_snapshot(client: Any) -> dict[str, Any]:
    from connectors import tuning  # noqa: PLC0415
    return ledger_mod.ledger.snapshot(
        ledger_key_for(client), limit=tuning.xsiam_max_queries_per_day())


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
        raise HTTPException(
            status_code=404,
            detail={
                "error": "XSIAM tenant not found",
                "code": "TENANT_NOT_FOUND",
                "detail": f"name='{name}'",
            },
        )
    return {
        "name": name,
        "last_verified_ok": row.last_verified_ok,
        "last_verified_at": row.last_verified_at.isoformat() if row.last_verified_at else None,
        "last_verified_error": row.last_verified_error,
    }


@router.get("/tenants/{name}/metrics")
async def tenant_metrics(name: str, session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    client = await load_xsiam_client(session, name)
    require_xql_capability(getattr(client, "capabilities", None), tenant=name)
    async with _budgeted(client):
        reply = await client.run_xql(INGESTION_HEALTH_XQL, dict(_DEFAULT_TIMEFRAME))
    return {"sources": shape_ingestion_results(reply),
            "remaining_quota": reply.get("remaining_quota"),
            "queries_issued": 1,
            "ledger": _ledger_snapshot(client)}


@router.post("/tenants/{name}/xql")
async def start_xql(name: str, body: XqlRequest,
                    session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    # SEC-05: refuse a write stage BEFORE the credential is decrypted. The
    # client enforces the same guard again at send time — this is the same
    # function, called early, not a second implementation.
    refuse_xql_write_stages(body.query)
    client = await load_xsiam_client(session, name)
    require_xql_capability(getattr(client, "capabilities", None), tenant=name)
    refuse_xql_write_stage(body.query)
    async with _budgeted(client):
        query_id = await client.start_xql_query(body.query, body.timeframe)
    return {"query_id": query_id, "queries_issued": 1,
            "ledger": _ledger_snapshot(client)}


@router.get("/tenants/{name}/xql/{query_id}")
async def get_xql(name: str, query_id: str,
                  limit: int = Query(100, ge=1, le=1000),
                  session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    client = await load_xsiam_client(session, name)
    require_xql_capability(getattr(client, "capabilities", None), tenant=name)
    reply = await client.get_query_results(query_id, limit=limit)
    return {"status": reply.get("status"),
            "results": reply.get("results"),
            "remaining_quota": reply.get("remaining_quota"),
            "queries_issued": 0,  # polling a started query is not metered
            "ledger": _ledger_snapshot(client)}


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
    """Substitute path params, each as exactly ONE encoded path segment.

    The catalog path is what selects the operation — and therefore which gate
    (read / write / destructive) applies. A raw substitution let the param
    rewrite it: httpx resolves dot-segments, so ``group_id="../delete/123"`` on
    the write-gated ``asset-groups/update/{group_id}`` sent
    ``POST asset-groups/delete/123`` on write consent alone, skipping the
    destructive gate; ``?``/``#`` could smuggle a query. A value that is a
    dot-segment or contains a path separator is refused (an ID never is one);
    everything else is percent-encoded so it cannot leave its segment.
    """
    path = op.path
    for p in op.path_params:
        val = path_params.get(p)
        if val in (None, ""):
            raise XsiamConfigError(f"operation {op.op_id} requires path param '{p}'")
        raw = str(val)
        if raw in (".", "..") or "/" in raw or "\\" in raw:
            raise XsiamConfigError(
                f"operation {op.op_id} path param '{p}' must be a single path "
                f"segment (no '/', '\\', '.' or '..'); got {raw!r}")
        path = path.replace("{" + p + "}", quote(raw, safe=""))
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
        # Minimal envelope most POST ops expect. A few (BYOS ingest) reject the
        # wrapper; they say so with a tag, so the preview shows what would be sent.
        payload = {} if "no-request-data-wrapper" in op.tags else {"request_data": {}}
    if "body-inspected" in op.tags or is_xql_start_path(path):
        # SEC-05: an op whose effect depends on its BODY (XQL `target` writes)
        # cannot be gated by access_class alone. Inspected for dry-run too: a
        # preview of a request SimCore would refuse is a misleading preview.
        inspect_operation_body(path, payload)
    request_echo = {
        "method": op.method, "path": path,
        "body": payload, "query": body.query or None,
    }
    if dry:
        return {"dry_run": True, "op_id": op.op_id,
                "access_class": op.access_class, "request": request_echo,
                "queries_issued": 0, "ledger": None}

    client = await load_xsiam_client(session, name)
    queries_issued = 1
    if op.op_id in XQL_OPERATION_IDS:
        require_xql_capability(getattr(client, "capabilities", None), tenant=name)
        if op.op_id == _START_XQL_OP_ID:
            refuse_xql_write_stage(xql_query_text(payload))
            async with _budgeted(client):
                reply = await client.request(
                    op.method, path, json=payload, params=(body.query or None))
        else:
            if op.op_id in XQL_POLL_OPERATION_IDS:
                queries_issued = 0  # polling a started query is not metered
            reply = await client.request(
                op.method, path, json=payload, params=(body.query or None))
    else:
        reply = await client.request(
            op.method, path, json=payload, params=(body.query or None))
    return {"dry_run": False, "op_id": op.op_id,
            "access_class": op.access_class, "reply": reply,
            "queries_issued": queries_issued,
            "ledger": _ledger_snapshot(client)}

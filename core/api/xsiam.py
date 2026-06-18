# core/api/xsiam.py
"""XSIAM live-tenant operations router (decision B).

Tenant CRUD lives in the generic /api/credentials/integrations endpoints
(kind="xsiam_tenant"). This router only does things that require talking to the
live tenant: liveness, health, ingestion metrics, and XQL.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from security import CredentialStore
from integrations.xsiam.loader import XSIAM_KIND, load_xsiam_client
from integrations.xsiam.queries import INGESTION_HEALTH_XQL, shape_ingestion_results
from integrations.xsiam.exceptions import XsiamApiError, XsiamError

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

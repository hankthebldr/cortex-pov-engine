"""The ``fake_simcore`` harness: a SimCoreClient over ``httpx.MockTransport``.

No network, no sockets. Routes are keyed ``(METHOD, path)``; a value is a
dict (200 JSON), an ``httpx.Response``, or a callable taking the request and
returning either. An unknown route answers SimCore's own 404 shape. Every
response carries a ``Date`` header, because ``as_of`` is SimCore's clock.
``GET /api/readiness`` defaults to :func:`readiness_body` unless overridden.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

import httpx
import pytest
from mcp.server.fastmcp import FastMCP

from mcp_server.client import SimCoreClient
from mcp_server.naming import wire_name
from mcp_server.ratelimit import TokenBucket

DATE = "Mon, 02 Nov 2026 18:04:11 GMT"
AS_OF = "2026-11-02T18:04:11+00:00"


def readiness_body(tenant_verified: int = 0, integrations: Optional[list] = None) -> dict:
    return {
        "server_time": AS_OF,
        "tenant_verified": tenant_verified,
        "tenant_verified_names": [],
        "integrations": list(integrations or []),
        "proves": {},
        "outbound_calls": 0,
        "statement": f"No tenant call is made by this route. tenant-verified: {tenant_verified}",
    }


def ledger_snapshot(key: str = "api-deathstar.xdr.us.paloaltonetworks.com", used: int = 7) -> dict:
    return {"tenant": key, "queries_last_24h": used, "limit": 500, "remaining": 500 - used,
            "breaker_open": False, "breaker_open_until": None, "retry_after_seconds": 0, "trips": 0}


def integration(name: str, kind: str = "xsiam_tenant", ledger: Optional[dict] = None) -> dict:
    return {"name": name, "kind": kind, "capabilities": None, "xql_capability_granted": False,
            "last_verified_ok": None, "last_verified_at": None, "last_verified_error": None,
            "ledger": ledger,
            "rungs": {"authored": True, "configured": True, "reachable": False, "verified": False}}


def make_fake_simcore(routes: dict[tuple[str, str], Any]) -> tuple[SimCoreClient, list[httpx.Request]]:
    table = dict(routes)
    table.setdefault(("GET", "/api/readiness"), readiness_body())
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        value = table.get((request.method, request.url.path))
        if value is None:
            resp = httpx.Response(404, json={"error": "Not found", "code": "NOT_FOUND",
                                             "detail": f"{request.method} {request.url.path}"})
        else:
            if callable(value):
                value = value(request)
            resp = value if isinstance(value, httpx.Response) else httpx.Response(200, json=value)
        if "date" not in resp.headers:
            resp.headers["Date"] = DATE
        return resp

    return SimCoreClient("http://simcore.test", transport=httpx.MockTransport(handler)), calls


@pytest.fixture
def fake_simcore() -> Callable[..., tuple[SimCoreClient, list[httpx.Request]]]:
    return make_fake_simcore


def make_ctx(client: SimCoreClient, *, bucket: Optional[TokenBucket] = None):
    from mcp_server.registry import ToolContext

    return ToolContext(client=client, bucket=bucket or TokenBucket(30))


def mcp_with(specs, ctx) -> FastMCP:
    from mcp_server.registry import register

    mcp = FastMCP("cortexsim-test")
    register(mcp, specs, ctx)
    return mcp


async def call(mcp: FastMCP, canonical: str, **args: Any):
    return await mcp.call_tool(wire_name(canonical), args)


def paths(calls: list[httpx.Request], method: Optional[str] = None) -> list[str]:
    return [r.url.path for r in calls if method is None or r.method == method]

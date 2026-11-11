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


# --- stubbed_app: the real SimCore xsiam + readiness routers, a stub tenant ---
class TenantStub:
    """The TENANT witness: every method the xsiam routes call on a tenant client
    appends to ``calls``. ``replies`` maps a catalog path to a reply, an
    exception to raise, or a callable; ``polls`` is the sequence of
    ``get_query_results`` replies (the last one repeats)."""

    _base = "https://api-stub.xdr.us.paloaltonetworks.com"

    def __init__(self) -> None:
        self.capabilities: list[str] = ["xql"]
        self.calls: list[dict] = []
        self.replies: dict[str, Any] = {}
        self.polls: list[dict] = [{"status": "SUCCESS", "number_of_results": 0,
                                   "results": {"data": []}}]

    async def request(self, method, path, *, json=None, params=None):
        self.calls.append({"method": method, "path": path, "json": json, "params": params})
        value = self.replies.get(path, {"ok": True, "echo_path": path})
        if isinstance(value, BaseException):
            raise value
        return value() if callable(value) else value

    async def start_xql_query(self, query, timeframe):
        self.calls.append({"method": "start_xql_query", "query": query, "timeframe": timeframe})
        return "qid-1"

    async def get_query_results(self, query_id, *, limit=100):
        self.calls.append({"method": "get_query_results", "query_id": query_id, "limit": limit})
        reply = self.polls.pop(0) if len(self.polls) > 1 else self.polls[0]
        if isinstance(reply, BaseException):
            raise reply
        return reply


class StubbedApp:
    """``_build_app`` (tests/integration/xsiam) + the readiness router, driven
    through ``httpx.ASGITransport``. ``stub.calls`` is the tenant witness;
    ``simcore_calls`` records every ``"METHOD /path"`` the MCP layer sent."""

    def __init__(self, app, stub: TenantStub, xsiam_api, override_db) -> None:
        self.app = app
        self.stub = stub
        self.xsiam_api = xsiam_api
        self.simcore_calls: list[str] = []
        self._override_db = override_db

    async def _recording(self, scope, receive, send) -> None:
        if scope["type"] == "http":
            self.simcore_calls.append(f"{scope['method']} {scope['path']}")
        await self.app(scope, receive, send)

    async def seed_tenant(self, name: str, kind: str = "xsiam_tenant",
                          capabilities: Optional[list] = ("xql",)) -> None:
        from security import CredentialStore

        config: dict[str, Any] = {"base_url": TenantStub._base}
        if capabilities is not None:
            config["capabilities"] = list(capabilities)
        if kind == "xsiam_tenant":
            # The route reads the capability off the loaded client, readiness
            # off the vault row: keep the one stub and the row in agreement.
            self.stub.capabilities = list(capabilities or [])
        gen = self._override_db()
        session = await gen.__anext__()
        try:
            await CredentialStore(session).put_integration(
                name=name, kind=kind, plaintext_secret="k" * 40, config=config)
            await session.commit()
        finally:
            await gen.aclose()

    def client(self) -> SimCoreClient:
        return SimCoreClient("http://simcore", transport=httpx.ASGITransport(app=self._recording))

    def mcp(self, specs=None, *, bucket: Optional[TokenBucket] = None):
        if specs is None:
            from mcp_server.tools.xsiam import specs as xsiam_specs

            specs = xsiam_specs()
        return mcp_with(specs, make_ctx(self.client(), bucket=bucket))


@pytest.fixture
def stubbed_app(tmp_path, monkeypatch, restore_core_modules) -> StubbedApp:
    # Imported here, not at module level: _build_app's module pulls in
    # starlette.testclient, and the harness must not run at collection.
    from tests.integration.xsiam.test_operations_api import _build_app, _ret

    test_client, _unused_stub, xsiam_api = _build_app(tmp_path, monkeypatch)
    app = test_client.app
    stub = TenantStub()
    monkeypatch.setattr(xsiam_api, "load_xsiam_client", lambda session, name: _ret(stub))

    import api.readiness as readiness_api
    from database import get_db

    app.include_router(readiness_api.router, prefix="/api")
    return StubbedApp(app, stub, xsiam_api, app.dependency_overrides[get_db])

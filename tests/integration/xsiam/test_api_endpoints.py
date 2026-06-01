# tests/integration/xsiam/test_api_endpoints.py
from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def _build_app(tmp_path, monkeypatch, stub_client):
    """FastAPI app with just the xsiam router + the XsiamError handler,
    backed by tmp SQLite, with load_xsiam_client patched to a stub."""
    import sys
    monkeypatch.setenv("CORTEXSIM_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("CORTEXSIM_SECRET", "test-master-key-please-ignore-32+chars")
    monkeypatch.setenv("CORTEXSIM_ENV", "development")
    for mod in ("database", "models", "config"):
        sys.modules.pop(mod, None)
    for mod in [m for m in sys.modules if m.startswith("security") or m.startswith("api.")]:
        sys.modules.pop(mod, None)

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path/'api.db'}",
        connect_args={"check_same_thread": False})
    from database import Base, get_db
    import models  # noqa: F401
    asyncio.run(_create_all(engine, Base))
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_db():
        async with Session() as s:
            yield s

    import api.xsiam as xsiam_api
    from integrations.xsiam.exceptions import XsiamError

    # Patch the loader the router calls so no real network is hit.
    monkeypatch.setattr(xsiam_api, "load_xsiam_client", lambda session, name: _ret(stub_client))

    app = FastAPI()
    app.include_router(xsiam_api.router, prefix="/api")
    app.dependency_overrides[get_db] = _override_db

    @app.exception_handler(XsiamError)
    async def _h(request: Request, exc: XsiamError):
        return JSONResponse(status_code=exc.http_status,
                            content={"error": "XSIAM integration error",
                                     "code": exc.code, "detail": exc.detail})
    return TestClient(app)


async def _create_all(engine, Base):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _ret(value):  # tiny awaitable wrapper for the patched loader
    return value


class _StubClient:
    def __init__(self, *, health=None, raise_exc=None, xql_reply=None, qid="q1"):
        self._health, self._raise = health, raise_exc
        self._xql_reply, self._qid = xql_reply, qid
    async def healthcheck(self):
        if self._raise:
            raise self._raise
        return self._health or {"status": "ok"}
    async def ping_via_endpoints(self):
        return 1
    async def run_xql(self, *a, **k):
        return self._xql_reply or {"status": "SUCCESS", "results": {"data": []}, "remaining_quota": 1.0}
    async def start_xql_query(self, *a, **k):
        return self._qid
    async def get_query_results(self, qid, **k):
        return {"status": "SUCCESS", "results": {"data": []}, "remaining_quota": 1.0}


def test_test_endpoint_ok(tmp_path, monkeypatch):
    client = _build_app(tmp_path, monkeypatch, _StubClient(health={"status": "healthy"}))
    r = client.post("/api/xsiam/tenants/acme/test")
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


def test_test_endpoint_auth_failure_maps_envelope(tmp_path, monkeypatch):
    from integrations.xsiam.exceptions import XsiamAuthError
    client = _build_app(tmp_path, monkeypatch, _StubClient(raise_exc=XsiamAuthError("401")))
    r = client.post("/api/xsiam/tenants/acme/test")
    assert r.status_code == 502
    body = r.json()
    assert body["code"] == "XSIAM_AUTH_ERROR"
    assert set(body) >= {"error", "code", "detail"}


def test_test_endpoint_403_falls_back_to_endpoints(tmp_path, monkeypatch):
    from integrations.xsiam.exceptions import XsiamApiError
    stub = _StubClient(raise_exc=XsiamApiError("license", upstream_status=403))
    client = _build_app(tmp_path, monkeypatch, stub)
    r = client.post("/api/xsiam/tenants/acme/test")
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


def test_metrics_endpoint(tmp_path, monkeypatch):
    stub = _StubClient(xql_reply={"status": "SUCCESS", "remaining_quota": 0.5,
                                  "results": {"data": [{"source": "okta", "events": 3}]}})
    client = _build_app(tmp_path, monkeypatch, stub)
    r = client.get("/api/xsiam/tenants/acme/metrics")
    assert r.status_code == 200, r.text
    assert r.json()["sources"][0]["source"] == "okta"


def test_xql_start_and_get(tmp_path, monkeypatch):
    client = _build_app(tmp_path, monkeypatch, _StubClient(qid="qX"))
    r = client.post("/api/xsiam/tenants/acme/xql", json={"query": "dataset=x"})
    assert r.status_code == 200, r.text
    assert r.json()["query_id"] == "qX"
    r2 = client.get("/api/xsiam/tenants/acme/xql/qX")
    assert r2.status_code == 200
    assert r2.json()["status"] == "SUCCESS"

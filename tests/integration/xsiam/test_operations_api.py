# tests/integration/xsiam/test_operations_api.py
"""API tests for the operation-catalog list/detail endpoints and the gated executor."""
from __future__ import annotations

import asyncio
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


async def _create_all(engine, Base):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _ret(value):
    return value


class _StubClient:
    """Records request() calls so tests can assert a live call did/didn't happen."""

    def __init__(self):
        self.calls = []

    async def request(self, method, path, *, json=None, params=None):
        self.calls.append({"method": method, "path": path, "json": json, "params": params})
        return {"ok": True, "echo_path": path}


def _build_app(tmp_path, monkeypatch):
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

    # Ensure the singleton catalog is populated (boot lifespan doesn't run in tests).
    import integrations.xsiam.operations.catalog as _catmod
    xsiam_api.op_catalog.load(os.path.join(os.path.dirname(_catmod.__file__), "packs"))

    stub = _StubClient()
    monkeypatch.setattr(xsiam_api, "load_xsiam_client", lambda session, name: _ret(stub))

    app = FastAPI()
    app.include_router(xsiam_api.router, prefix="/api")
    app.dependency_overrides[get_db] = _override_db

    @app.exception_handler(XsiamError)
    async def _h(request: Request, exc: XsiamError):
        return JSONResponse(status_code=exc.http_status,
                            content={"error": "XSIAM integration error",
                                     "code": exc.code, "detail": exc.detail})
    return TestClient(app), stub, xsiam_api


# ── list / detail ────────────────────────────────────────────────────────────

def test_list_operations(tmp_path, monkeypatch):
    client, _stub, _ = _build_app(tmp_path, monkeypatch)
    r = client.get("/api/xsiam/operations")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] >= 100
    assert "Audit log" in body["categories"]


def test_list_operations_filtered_by_access(tmp_path, monkeypatch):
    client, _stub, _ = _build_app(tmp_path, monkeypatch)
    r = client.get("/api/xsiam/operations", params={"access_class": "destructive"})
    assert r.status_code == 200
    assert all(o["access_class"] == "destructive" for o in r.json()["operations"])


def test_operation_detail_and_404(tmp_path, monkeypatch):
    client, _stub, _ = _build_app(tmp_path, monkeypatch)
    r = client.get("/api/xsiam/operations/OP-INDICATORS-GET")
    assert r.status_code == 200
    assert r.json()["method"] == "POST"

    miss = client.get("/api/xsiam/operations/OP-DOES-NOT-EXIST")
    assert miss.status_code == 404
    detail = miss.json()["detail"]
    assert detail["code"] == "XSIAM_OP_NOT_FOUND"


# ── executor: reads ──────────────────────────────────────────────────────────

def test_execute_read_op_runs_live(tmp_path, monkeypatch):
    client, stub, _ = _build_app(tmp_path, monkeypatch)
    r = client.post("/api/xsiam/tenants/acme/operations/OP-INDICATORS-GET",
                    json={"body": {"request_data": {}}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dry_run"] is False
    assert body["reply"]["ok"] is True
    assert len(stub.calls) == 1
    assert stub.calls[0]["path"] == "/public_api/v1/indicators/get"


# ── executor: write gating ───────────────────────────────────────────────────

def test_write_op_defaults_to_dry_run(tmp_path, monkeypatch):
    """A write op with no dry_run/consent must preview, never call the tenant."""
    client, stub, _ = _build_app(tmp_path, monkeypatch)
    r = client.post("/api/xsiam/tenants/acme/operations/OP-BIOC-INSERT", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dry_run"] is True
    assert body["request"]["path"] == "/public_api/v1/bioc/insert"
    assert stub.calls == []  # nothing sent


def test_write_op_blocked_without_flag_and_consent(tmp_path, monkeypatch):
    client, stub, _ = _build_app(tmp_path, monkeypatch)
    r = client.post("/api/xsiam/tenants/acme/operations/OP-BIOC-INSERT",
                    json={"dry_run": False})
    assert r.status_code == 400
    assert r.json()["code"] == "XSIAM_CONFIG_ERROR"
    assert stub.calls == []


def test_write_op_allowed_with_flag_and_consent(tmp_path, monkeypatch):
    client, stub, xsiam_api = _build_app(tmp_path, monkeypatch)
    monkeypatch.setattr(xsiam_api.settings, "CORTEXSIM_XSIAM_ALLOW_WRITE", True)
    r = client.post("/api/xsiam/tenants/acme/operations/OP-BIOC-INSERT",
                    json={"dry_run": False,
                          "body": {"request_data": {"insert": []}},
                          "consent": {"write_authorized": True}})
    assert r.status_code == 200, r.text
    assert r.json()["dry_run"] is False
    assert len(stub.calls) == 1
    assert stub.calls[0]["path"] == "/public_api/v1/bioc/insert"


def test_destructive_op_blocked_even_with_write_flag(tmp_path, monkeypatch):
    """Write flag must not unlock destructive ops — they need their own flag."""
    client, stub, xsiam_api = _build_app(tmp_path, monkeypatch)
    monkeypatch.setattr(xsiam_api.settings, "CORTEXSIM_XSIAM_ALLOW_WRITE", True)
    r = client.post("/api/xsiam/tenants/acme/operations/OP-BIOC-DELETE",
                    json={"dry_run": False, "consent": {"write_authorized": True}})
    assert r.status_code == 400
    assert r.json()["code"] == "XSIAM_CONFIG_ERROR"
    assert stub.calls == []


def test_missing_path_param_is_rejected(tmp_path, monkeypatch):
    client, stub, _ = _build_app(tmp_path, monkeypatch)
    # update asset group requires {group_id}; omit it (dry-run write still resolves path first)
    r = client.post("/api/xsiam/tenants/acme/operations/OP-ASSET-GROUPS-UPDATE-GROUP-ID",
                    json={})
    assert r.status_code == 400
    assert r.json()["code"] == "XSIAM_CONFIG_ERROR"

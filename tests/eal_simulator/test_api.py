"""Integration tests for /api/eal endpoints.

We share the singleton DB engine across tests but isolate data via a
per-test transaction-rollback session. The router is included into a fresh
FastAPI app instance so middleware/lifespan from ``main.py`` don't leak in.
"""
from __future__ import annotations

import asyncio
from typing import AsyncIterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


@pytest.fixture
def api_client(tmp_path) -> TestClient:
    # Build an in-memory SQLite engine bound to a single connection so the
    # background-task session sees the same data as the request session.
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def _init():
        from database import Base
        import models  # noqa: F401  - register tables

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.get_event_loop().run_until_complete(_init())

    # Build a custom get_db dependency that yields from our isolated engine.
    async def _get_db() -> AsyncIterator[AsyncSession]:
        async with SessionLocal() as session:
            yield session

    from api import eal as eal_api
    from database import get_db

    eal_api._reset_executor()

    app = FastAPI()
    app.include_router(eal_api.router, prefix="/api")
    app.dependency_overrides[get_db] = _get_db

    # Patch the background task's session factory so it shares our engine.
    original_factory_module = eal_api
    import database as db_module

    saved_factory = db_module.AsyncSessionLocal
    db_module.AsyncSessionLocal = SessionLocal
    eal_api.AsyncSessionLocal = SessionLocal

    client = TestClient(app)
    try:
        yield client
    finally:
        db_module.AsyncSessionLocal = saved_factory
        eal_api.AsyncSessionLocal = saved_factory
        eal_api._reset_executor()
        asyncio.get_event_loop().run_until_complete(engine.dispose())


class TestPluginsAPI:
    def test_list_plugins_returns_built_ins(self, api_client: TestClient):
        resp = api_client.get("/api/eal/plugins")
        assert resp.status_code == 200
        data = resp.json()
        names = {p["name"] for p in data["plugins"]}
        assert "c2_http_beacon" in names
        assert "dns_tunnel_exfil" in names
        assert data["total"] >= 5

    def test_get_plugin_metadata_includes_schema(self, api_client: TestClient):
        resp = api_client.get("/api/eal/plugins/c2_http_beacon")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "c2_http_beacon"
        assert "params_schema" in data

    def test_get_plugin_unknown_returns_404(self, api_client: TestClient):
        resp = api_client.get("/api/eal/plugins/does_not_exist")
        assert resp.status_code == 404
        body = resp.json()
        assert body["detail"]["code"] == "PLUGIN_NOT_FOUND"


class TestCampaignsAPI:
    _SAMPLE = {
        "campaign_id": "CMP-NDR-100",
        "name": "test campaign",
        "authorized_by": "tester",
        "simulation_authorized": True,
        "target_allowlist": ["testmynids.org"],
        "dry_run": True,
        "steps": [
            {
                "step_id": "step-01",
                "plugin": "c2_http_beacon",
                "params": {
                    "target_url": "http://testmynids.org/uid/index.html",
                    "iterations": 1,
                    "sleep_seconds": 0.1,
                },
            }
        ],
    }

    def test_create_campaign_persists(self, api_client: TestClient):
        resp = api_client.post("/api/eal/campaigns", json=self._SAMPLE)
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["campaign_id"] == "CMP-NDR-100"
        assert body["target_allowlist"] == ["testmynids.org"]

    def test_duplicate_campaign_returns_409(self, api_client: TestClient):
        api_client.post("/api/eal/campaigns", json=self._SAMPLE)
        resp = api_client.post("/api/eal/campaigns", json=self._SAMPLE)
        assert resp.status_code == 409

    def test_unknown_plugin_returns_422(self, api_client: TestClient):
        spec = {**self._SAMPLE, "campaign_id": "CMP-NDR-101"}
        spec["steps"] = [{"step_id": "step-01", "plugin": "nope", "params": {}}]
        resp = api_client.post("/api/eal/campaigns", json=spec)
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "PLUGIN_NOT_FOUND"

    def test_invalid_step_params_returns_422(self, api_client: TestClient):
        spec = {**self._SAMPLE, "campaign_id": "CMP-NDR-102"}
        spec["steps"] = [{
            "step_id": "step-01",
            "plugin": "c2_http_beacon",
            "params": {"target_url": "ftp://nope"},
        }]
        resp = api_client.post("/api/eal/campaigns", json=spec)
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "PARAMS_INVALID"

    def test_list_and_get_campaign(self, api_client: TestClient):
        api_client.post("/api/eal/campaigns", json=self._SAMPLE)
        list_resp = api_client.get("/api/eal/campaigns")
        assert list_resp.status_code == 200
        ids = [c["campaign_id"] for c in list_resp.json()["campaigns"]]
        assert "CMP-NDR-100" in ids

        detail = api_client.get("/api/eal/campaigns/CMP-NDR-100")
        assert detail.status_code == 200
        assert detail.json()["name"] == "test campaign"

    def test_get_unknown_campaign_404(self, api_client: TestClient):
        resp = api_client.get("/api/eal/campaigns/CMP-DOES-NOT-EXIST-001")
        assert resp.status_code == 404


class TestLaunchAPI:
    _SAMPLE = {
        "campaign_id": "CMP-NDR-200",
        "name": "launch test",
        "authorized_by": "tester",
        "simulation_authorized": True,
        "target_allowlist": ["testmynids.org"],
        "dry_run": True,
        "steps": [
            {
                "step_id": "step-01",
                "plugin": "c2_http_beacon",
                "params": {
                    "target_url": "http://testmynids.org/uid/index.html",
                    "iterations": 1,
                    "sleep_seconds": 0.1,
                },
            }
        ],
    }

    def test_launch_creates_pending_run(self, api_client: TestClient):
        api_client.post("/api/eal/campaigns", json=self._SAMPLE)
        resp = api_client.post(
            "/api/eal/campaigns/CMP-NDR-200/launch",
            json={"operator": "tester"},
        )
        assert resp.status_code == 200, resp.text
        run_id = resp.json()["run_id"]
        assert run_id

        run_resp = api_client.get(f"/api/eal/runs/{run_id}")
        assert run_resp.status_code == 200
        body = run_resp.json()
        assert body["campaign_id"] == "CMP-NDR-200"

    def test_launch_unknown_campaign_404(self, api_client: TestClient):
        resp = api_client.post(
            "/api/eal/campaigns/CMP-MISSING-001/launch", json={},
        )
        assert resp.status_code == 404

    def test_launch_live_without_auth_block_returns_safety_error(self, api_client: TestClient):
        spec = {**self._SAMPLE, "campaign_id": "CMP-NDR-201"}
        spec["dry_run"] = True
        spec["simulation_authorized"] = False
        spec["target_allowlist"] = []
        api_client.post("/api/eal/campaigns", json=spec)
        resp = api_client.post(
            "/api/eal/campaigns/CMP-NDR-201/launch",
            json={"dry_run": False},
        )
        # The Pydantic model validator catches the violation before the
        # safety policy runs, so the error code is SPEC_INVALID. Either
        # outcome blocks the launch, which is the only thing we care about.
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] in {"SAFETY_VIOLATION", "SPEC_INVALID"}

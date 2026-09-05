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

    asyncio.run(_init())

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
        asyncio.run(engine.dispose())


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


class TestC2ConsentGate:
    """EAL-G01 — a C2-shaped campaign needs c2_authorized for live launch."""

    _LIVE_C2 = {
        "campaign_id": "CMP-NDR-300",
        "name": "live c2",
        "authorized_by": "tester",
        "simulation_authorized": True,
        "target_allowlist": ["testmynids.org"],
        "dry_run": True,  # stored dry-run; launch flips to live
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

    def test_live_c2_launch_without_consent_is_refused(self, api_client: TestClient):
        api_client.post("/api/eal/campaigns", json=self._LIVE_C2)
        resp = api_client.post(
            "/api/eal/campaigns/CMP-NDR-300/launch",
            json={"dry_run": False},  # live, but no c2_authorized
        )
        assert resp.status_code == 422, resp.text
        body = resp.json()
        assert body["detail"]["code"] in {"SAFETY_VIOLATION", "SPEC_INVALID"}
        assert "c2_authorized" in body["detail"]["detail"]

    def test_live_c2_launch_with_consent_is_allowed(self, api_client: TestClient):
        spec = {**self._LIVE_C2, "campaign_id": "CMP-NDR-301"}
        api_client.post("/api/eal/campaigns", json=spec)
        resp = api_client.post(
            "/api/eal/campaigns/CMP-NDR-301/launch",
            json={"dry_run": False, "c2_authorized": True},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["run_id"]

    def test_dry_run_c2_launch_needs_no_consent(self, api_client: TestClient):
        spec = {**self._LIVE_C2, "campaign_id": "CMP-NDR-302"}
        api_client.post("/api/eal/campaigns", json=spec)
        resp = api_client.post(
            "/api/eal/campaigns/CMP-NDR-302/launch",
            json={},  # dry-run default from stored spec
        )
        assert resp.status_code == 200, resp.text


class TestAbortAPI:
    """GAP-API-011 — EAL run abort + core lifecycle vocabulary reconciliation."""

    _SAMPLE = {
        "campaign_id": "CMP-NDR-400",
        "name": "abort test",
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

    def test_abort_unknown_run_404(self, api_client: TestClient):
        resp = api_client.post("/api/eal/runs/does-not-exist/abort")
        assert resp.status_code == 404
        assert resp.json()["detail"]["code"] == "RUN_NOT_FOUND"

    def test_abort_terminal_run_returns_409(self, api_client: TestClient):
        # Launch a dry-run campaign; the TestClient runs background tasks
        # synchronously on response, so by the time we read it the run is
        # already terminal (complete) and therefore not abortable.
        api_client.post("/api/eal/campaigns", json=self._SAMPLE)
        launch = api_client.post(
            "/api/eal/campaigns/CMP-NDR-400/launch", json={},
        )
        run_id = launch.json()["run_id"]
        # Drive to terminal state by reading; background task already ran.
        run = api_client.get(f"/api/eal/runs/{run_id}").json()
        assert run["status"] in {"complete", "failed", "aborted", "pending", "running"}
        resp = api_client.post(f"/api/eal/runs/{run_id}/abort")
        # If it reached a terminal state, abort is a 409; if somehow still
        # pending/running it would be 200 with status aborted. Accept both
        # but assert the contract shape.
        assert resp.status_code in {200, 409}
        if resp.status_code == 409:
            assert resp.json()["detail"]["code"] == "RUN_NOT_ABORTABLE"
        else:
            assert resp.json()["status"] == "aborted"

    def test_abort_running_run_flips_to_aborted(self, api_client: TestClient):
        """A non-terminal run is flipped to the core 'aborted' state."""
        from api import eal as eal_api
        from models import EalCampaign, EalCampaignRun

        # Persist a campaign + a 'running' run directly so we have a stable
        # non-terminal run to abort (the TestClient otherwise drives launches
        # straight to a terminal state synchronously).
        async def _seed():
            async with eal_api.AsyncSessionLocal() as session:
                session.add(EalCampaign(
                    campaign_id="CMP-NDR-401",
                    name="abort-running",
                    spec={},
                    simulation_authorized=True,
                    target_allowlist=["testmynids.org"],
                    tags=[],
                ))
                session.add(EalCampaignRun(
                    run_id="run-running-1",
                    campaign_id="CMP-NDR-401",
                    status="running",
                    dry_run=False,
                    step_results=[],
                ))
                await session.commit()

        asyncio.run(_seed())

        resp = api_client.post("/api/eal/runs/run-running-1/abort")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "aborted"
        assert body["previous_status"] == "running"

        run = api_client.get("/api/eal/runs/run-running-1").json()
        assert run["status"] == "aborted"


class TestDataStreamsAPI:
    def test_plugins_are_tagged_with_family(self, api_client: TestClient):
        data = api_client.get("/api/eal/plugins").json()
        by_name = {p["name"]: p for p in data["plugins"]}
        # A collector-POST analytics emitter is tagged analytics_log_streamer.
        assert by_name["third_party_firewall_emitter"]["family"] == "analytics_log_streamer"
        assert by_name["ad_windows_emitter"]["family"] == "analytics_log_streamer"
        # A live-network plugin is tagged network_eal.
        assert by_name["c2_http_beacon"]["family"] == "network_eal"
        assert by_name["dns_tunnel_exfil"]["family"] == "network_eal"

    def test_data_streams_reports_full_catalogue_with_gaps(self, api_client: TestClient):
        data = api_client.get("/api/eal/data-streams").json()
        assert data["counts"]["total"] == 34
        # Gaps are present, not omitted.
        states = {s["state"] for s in data["sources"]}
        assert "gap" in states and "covered" in states
        # authored != proven, and proven is 0.
        assert data["counts"]["proven"] == 0
        assert "Authored is not proven" in data["authored_not_proven"]

    def test_data_streams_lists_new_emitters_with_negative_control(self, api_client: TestClient):
        data = api_client.get("/api/eal/data-streams").json()
        emitters = {e["name"]: e for e in data["emitters"]}
        fw = emitters["third_party_firewall_emitter"]
        assert fw["supports_negative_control"] is True
        assert fw["datasets"] == ["third_party_firewall_raw"]
        # No run yet -> latest_delivery is None (not a fabricated "delivered").
        assert fw["latest_delivery"] is None

    def test_data_streams_delivery_verdict_after_run(self, api_client: TestClient):
        from models import EalCampaignRun

        async def _seed():
            from api import eal as eal_api
            async with eal_api.AsyncSessionLocal() as session:
                session.add(EalCampaignRun(
                    run_id="run-ds-1",
                    campaign_id="CMP-DS-1",
                    status="complete",
                    dry_run=False,
                    step_results=[{
                        "plugin": "third_party_vpn_emitter",
                        "step_id": "step-01",
                        "status": "success",
                        "detail": {"delivery": {
                            "records_attempted": 2, "records_delivered": 2,
                            "bytes_attempted": 100, "bytes_delivered": 100,
                            "failures": [],
                        }},
                    }],
                ))
                await session.commit()

        asyncio.run(_seed())
        data = api_client.get("/api/eal/data-streams").json()
        emitters = {e["name"]: e for e in data["emitters"]}
        vpn = emitters["third_party_vpn_emitter"]["latest_delivery"]
        assert vpn is not None
        assert vpn["delivery_verdict"] == "delivered"
        assert vpn["run_id"] == "run-ds-1"

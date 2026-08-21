"""/api/eal — collector visibility, preflight, offline bundle, delivery rollup.

These are the surfaces that let a DC answer "where is the signal going, will
the collector take it, and how do I run this from inside the customer network"
without reading Python.
"""
from __future__ import annotations

import tarfile

import httpx
import pytest
from fastapi.testclient import TestClient

from api import eal as eal_api


_COLLECTOR = "https://collector.cortexsim-canary.invalid/logs/v1/event"


@pytest.fixture
def client(make_client, tmp_path, monkeypatch) -> TestClient:
    eal_api._reset_executor()
    monkeypatch.setattr(eal_api, "_bundles_dir", lambda: tmp_path / "eal_bundles")
    try:
        yield make_client(eal_api.router)
    finally:
        eal_api._reset_executor()


def _campaign_body(steps=None, allowlist=None):
    return {
        "campaign_id": "CMP-DEPLOY-001",
        "name": "deployment surfaces",
        "authorized_by": "tester@example.com",
        "simulation_authorized": True,
        "target_allowlist": allowlist if allowlist is not None
        else ["collector.cortexsim-canary.invalid"],
        "dry_run": True,
        "steps": steps or [{
            "step_id": "step-01",
            "plugin": "k8s_audit_emitter",
            "params": {
                "collector_url": _COLLECTOR,
                "provider": "kubernetes",
                "event_pattern": "pod_exec",
                "iterations": 1,
            },
        }],
    }


def _create(client, body=None):
    resp = client.post("/api/eal/campaigns", json=body or _campaign_body())
    assert resp.status_code == 201, resp.text
    return resp.json()["campaign_id"]


# --------------------------------------------------------------------------
# GET /campaigns/{id}/collectors
# --------------------------------------------------------------------------


class TestCollectorVisibility:
    def test_lists_the_destination_and_its_shape(self, client):
        campaign_id = _create(client)
        resp = client.get(f"/api/eal/campaigns/{campaign_id}/collectors")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["destinations"] == [_COLLECTOR]
        target = body["collectors"][0]
        assert target["host"] == "collector.cortexsim-canary.invalid"
        assert target["path"] == "/logs/v1/event"
        assert target["dataset"] == "kubernetes_audit_logs"
        assert target["has_auth_token"] is False

    def test_flags_a_collector_the_allowlist_would_refuse(self, client):
        campaign_id = _create(client, _campaign_body(allowlist=["elsewhere.test"]))
        body = client.get(f"/api/eal/campaigns/{campaign_id}/collectors").json()
        assert body["blocked_by_allowlist"] == ["step-01"]
        assert body["warnings"]

    def test_unknown_campaign_is_structured_404(self, client):
        resp = client.get("/api/eal/campaigns/CMP-NOPE-001/collectors")
        assert resp.status_code == 404
        assert resp.json()["detail"]["code"] == "CAMPAIGN_NOT_FOUND"


# --------------------------------------------------------------------------
# POST /campaigns/{id}/collectors/preflight
# --------------------------------------------------------------------------


class _StubClient:
    def __init__(self, status_code=None, raise_exc=None):
        self.status_code = status_code
        self.raise_exc = raise_exc
        self.requests = []

    async def post(self, url, *, headers=None, content=None):
        self.requests.append({"url": url, "headers": dict(headers or {})})
        if self.raise_exc is not None:
            raise self.raise_exc

        class _R:
            def __init__(self, code):
                self.status_code = code

        return _R(self.status_code)

    async def aclose(self):
        return None


class TestCollectorPreflight:
    def _patch(self, monkeypatch, stub):
        from eal_simulator import collector as collector_mod

        monkeypatch.setattr(
            collector_mod, "_default_client", lambda timeout, verify: stub,
        )

    def test_ready_when_the_collector_accepts_the_canary(self, client, monkeypatch):
        stub = _StubClient(status_code=202)
        self._patch(monkeypatch, stub)
        campaign_id = _create(client)

        resp = client.post(
            f"/api/eal/campaigns/{campaign_id}/collectors/preflight", json={},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ready"] is True
        assert body["collectors_delivering"] == 1
        assert body["probes"][0]["status_code"] == 202
        assert len(stub.requests) == 1

    def test_rejecting_collector_reports_a_taxonomy_code(self, client, monkeypatch):
        self._patch(monkeypatch, _StubClient(status_code=401))
        campaign_id = _create(client)

        body = client.post(
            f"/api/eal/campaigns/{campaign_id}/collectors/preflight", json={},
        ).json()
        assert body["ready"] is False
        probe = body["probes"][0]
        assert probe["reachable"] is True
        assert probe["delivered"] is False
        assert probe["code"] == "collector_unauthorized"
        assert probe["remediation"]

    def test_unreachable_collector_does_not_500(self, client, monkeypatch):
        self._patch(monkeypatch, _StubClient(raise_exc=httpx.ConnectError("no route")))
        campaign_id = _create(client)

        resp = client.post(
            f"/api/eal/campaigns/{campaign_id}/collectors/preflight", json={},
        )
        assert resp.status_code == 200
        assert resp.json()["probes"][0]["code"] == "collector_unreachable"

    def test_host_outside_allowlist_is_never_probed(self, client, monkeypatch):
        stub = _StubClient(status_code=202)
        self._patch(monkeypatch, stub)
        campaign_id = _create(client, _campaign_body(allowlist=["elsewhere.test"]))

        body = client.post(
            f"/api/eal/campaigns/{campaign_id}/collectors/preflight", json={},
        ).json()
        assert body["probes"][0]["code"] == "target_not_authorised"
        assert stub.requests == [], "a non-allowlisted host must not be contacted"

    def test_campaign_without_a_collector_is_422(self, client, monkeypatch):
        campaign_id = _create(client, _campaign_body(steps=[{
            "step_id": "step-01", "plugin": "c2_http_beacon",
            "params": {"target_url": "http://testmynids.org/uid/index.html"},
        }], allowlist=["testmynids.org"]))

        resp = client.post(
            f"/api/eal/campaigns/{campaign_id}/collectors/preflight", json={},
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "NO_COLLECTOR"


# --------------------------------------------------------------------------
# POST /campaigns/{id}/bundle  +  GET /bundles/{id}/download
# --------------------------------------------------------------------------


class TestOfflineBundleAPI:
    def test_build_then_download_round_trips(self, client, tmp_path):
        campaign_id = _create(client)

        resp = client.post(f"/api/eal/campaigns/{campaign_id}/bundle")
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["steps"] == ["step-01"]
        assert body["total_records"] > 0
        assert body["destinations"] == [_COLLECTOR]

        download = client.get(body["download_url"])
        assert download.status_code == 200
        assert download.headers["content-type"] == "application/gzip"

        archive = tmp_path / "bundle.tar.gz"
        archive.write_bytes(download.content)
        with tarfile.open(archive) as tar:
            names = tar.getnames()
        assert any(n.endswith("/send.py") for n in names)
        assert any(n.endswith("/manifest.json") for n in names)
        assert any("/records/" in n for n in names)

    def test_bundle_reports_steps_it_could_not_pre_render(self, client):
        campaign_id = _create(client, _campaign_body(
            steps=_campaign_body()["steps"] + [{
                "step_id": "step-02", "plugin": "c2_http_beacon",
                "params": {"target_url": "http://testmynids.org/uid/index.html"},
            }],
            allowlist=["collector.cortexsim-canary.invalid", "testmynids.org"],
        ))
        body = client.post(f"/api/eal/campaigns/{campaign_id}/bundle").json()
        assert [s["step_id"] for s in body["skipped_steps"]] == ["step-02"]

    def test_campaign_with_nothing_bundleable_is_422(self, client):
        campaign_id = _create(client, _campaign_body(steps=[{
            "step_id": "step-01", "plugin": "c2_http_beacon",
            "params": {"target_url": "http://testmynids.org/uid/index.html"},
        }], allowlist=["testmynids.org"]))
        resp = client.post(f"/api/eal/campaigns/{campaign_id}/bundle")
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "BUNDLE_FAILED"

    def test_download_unknown_bundle_is_structured_404(self, client):
        resp = client.get("/api/eal/bundles/not-a-bundle/download")
        assert resp.status_code == 404
        assert resp.json()["detail"]["code"] == "BUNDLE_NOT_FOUND"

    def test_download_rejects_path_traversal(self, client):
        resp = client.get("/api/eal/bundles/..%2F..%2Fetc%2Fpasswd/download")
        assert resp.status_code == 404


# --------------------------------------------------------------------------
# Run delivery rollup
# --------------------------------------------------------------------------


class TestRunDeliveryRollup:
    def _seed_run(self, session_factory, step_results):
        import asyncio

        from models import EalCampaignRun

        async def _insert():
            async with session_factory() as session:
                session.add(EalCampaignRun(
                    run_id="run-1", campaign_id="CMP-DEPLOY-001",
                    status="complete", dry_run=False,
                    step_results=step_results,
                ))
                await session.commit()

        asyncio.run(_insert())

    def test_run_that_ingested_nothing_is_not_reported_as_green(
        self, client, session_factory,
    ):
        self._seed_run(session_factory, [{
            "step_id": "step-01", "status": "error",
            "detail": {"delivery": {
                "records_attempted": 8, "records_delivered": 0,
                "bytes_attempted": 4096, "bytes_delivered": 0,
                "failures": [{"code": "collector_unauthorized", "records": 8}],
            }},
        }])

        body = client.get("/api/eal/runs/run-1").json()
        assert body["status"] == "complete"
        assert body["delivery"]["delivery_verdict"] == "not_delivered"
        assert body["delivery"]["dominant_failure"] == "collector_unauthorized"
        assert body["delivery"]["remediation"]

    def test_fully_delivered_run_reads_delivered(self, client, session_factory):
        self._seed_run(session_factory, [{
            "step_id": "step-01", "status": "success",
            "detail": {"delivery": {
                "records_attempted": 8, "records_delivered": 8,
                "bytes_attempted": 4096, "bytes_delivered": 4096, "failures": [],
            }},
        }])
        body = client.get("/api/eal/runs/run-1").json()
        assert body["delivery"]["delivery_verdict"] == "delivered"

    def test_legacy_run_without_delivery_detail_still_renders(
        self, client, session_factory,
    ):
        self._seed_run(session_factory, [{"step_id": "step-01", "status": "success"}])
        body = client.get("/api/eal/runs/run-1").json()
        assert body["delivery"]["delivery_verdict"] == "not_applicable"

    def test_list_runs_carries_the_rollup_too(self, client, session_factory):
        self._seed_run(session_factory, [{
            "step_id": "step-01", "status": "success",
            "detail": {"delivery": {
                "records_attempted": 2, "records_delivered": 1,
                "bytes_attempted": 20, "bytes_delivered": 10,
                "failures": [{"code": "collector_throttled", "records": 1}],
            }},
        }])
        body = client.get("/api/eal/runs").json()
        assert body["runs"][0]["delivery"]["delivery_verdict"] == "partial"

"""Guards: every in-process EAL consumer takes its destination from the
instance's collector targets, and a live launch refuses a missing or failing one.
"""
from __future__ import annotations

import asyncio
import io
import tarfile
from typing import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

KEY = "target-key-must-never-leak-7c1d"
TARGET_URL = "https://api-acme.example.test/logs/v1/event"


@pytest.fixture
def env(tmp_path, monkeypatch):
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Session = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def _init():
        from database import Base
        import models  # noqa: F401
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    asyncio.run(_init())

    async def _get_db() -> AsyncIterator[AsyncSession]:
        async with Session() as s:
            yield s

    from api import collector_targets as ct_api
    from api import eal as eal_api
    from config import settings
    from database import get_db
    import database as db_module

    monkeypatch.setattr(settings, "CORTEXSIM_BASE_DIR", str(tmp_path))
    eal_api._reset_executor()
    app = FastAPI()
    app.include_router(eal_api.router, prefix="/api")
    app.include_router(ct_api.router, prefix="/api")
    app.dependency_overrides[get_db] = _get_db
    saved = db_module.AsyncSessionLocal
    db_module.AsyncSessionLocal = Session
    eal_api.AsyncSessionLocal = Session

    # Capture what the live run actually sends, with no network.
    from eal_simulator.plugins.k8s_audit_emitter import K8sAuditEmitter
    sent: list[dict] = []
    original = K8sAuditEmitter._build_client

    class _Stub:
        async def post(self, url, *, headers=None, content=None):
            sent.append({"url": url})

            class _R:
                status_code = 202
                headers = {"content-type": "application/json"}
                content = b"{}"
            return _R()

        async def aclose(self):
            return None

    def _capture(self, params):
        real = original(self, params)
        sent.append({"authorization": real.headers.get("authorization")})
        return _Stub()

    monkeypatch.setattr(K8sAuditEmitter, "_build_client", _capture)

    client = TestClient(app)
    try:
        yield client, app, sent
    finally:
        db_module.AsyncSessionLocal = saved
        eal_api.AsyncSessionLocal = saved
        eal_api._reset_executor()
        asyncio.run(engine.dispose())


def _campaign(cid="CMP-TGT-001", params=None):
    return {
        "campaign_id": cid, "name": "k8s audit via target",
        "authorized_by": "tester", "simulation_authorized": True,
        "target_allowlist": [], "dry_run": True,
        "steps": [{"step_id": "step-01", "plugin": "k8s_audit_emitter",
                   "params": {"provider": "kubernetes", "event_pattern": "pod_exec",
                              **(params or {})}}],
    }


def _target(c, name="k8s-hec", **over):
    body = {"kind": "xsiam_http_collector", "url": TARGET_URL,
            "plugins": ["k8s_audit_emitter"], "token": KEY}
    body.update(over)
    r = c.put(f"/api/collector-targets/{name}", json=body)
    assert r.status_code == 200, r.text


def _check(c, app, name, status):
    from api import collector_targets as ct_api

    async def ok_connect(*a):
        return None

    def factory(timeout, verify_tls):
        return httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(status)))
    app.dependency_overrides[ct_api.preflight_transports] = lambda: (ok_connect, factory)
    r = c.post(f"/api/collector-targets/{name}/preflight", json={"write_canary": True})
    del app.dependency_overrides[ct_api.preflight_transports]
    return r.json()["verdict"]


def _launch_live(c, cid="CMP-TGT-001"):
    return c.post(f"/api/eal/campaigns/{cid}/launch", json={"dry_run": False, "operator": "t"})


def test_a_campaign_can_be_authored_without_a_collector_url(env):
    c, _, _ = env
    assert c.post("/api/eal/campaigns", json=_campaign()).status_code == 201
    view = c.get("/api/eal/campaigns/CMP-TGT-001/collectors").json()
    assert view["resolutions"][0]["code"] == "COLLECTOR_TARGET_NOT_CONFIGURED"
    assert view["blocking"][0]["step_id"] == "step-01"


def test_live_launch_with_nothing_to_send_to_is_refused(env):
    c, _, sent = env
    c.post("/api/eal/campaigns", json=_campaign())
    r = _launch_live(c)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "COLLECTOR_TARGET_NOT_CONFIGURED"
    assert sent == []
    assert c.get("/api/eal/runs").json()["total"] == 0


def test_dry_run_with_nothing_configured_runs_and_records_why(env):
    c, _, _ = env
    c.post("/api/eal/campaigns", json=_campaign())
    r = c.post("/api/eal/campaigns/CMP-TGT-001/launch", json={"dry_run": True})
    assert r.status_code == 200, r.text
    run = c.get(f"/api/eal/runs/{r.json()['run_id']}").json()
    assert run["collector_resolution"]["steps"][0]["source"] == "none"


def test_live_launch_uses_the_target_url_and_key_and_says_it_is_unproven(env):
    c, _, sent = env
    _target(c)
    c.post("/api/eal/campaigns", json=_campaign())
    r = _launch_live(c)
    assert r.status_code == 200, r.text
    assert [d["code"] for d in r.json()["degraded"]] == ["COLLECTOR_TARGET_UNPROVEN"]
    assert {"authorization": KEY} in sent and {"url": TARGET_URL} in sent
    run = c.get(f"/api/eal/runs/{r.json()['run_id']}")
    res = run.json()["collector_resolution"]["steps"][0]
    assert res["target"] == "k8s-hec" and res["source"] == "binding" and res["has_key"] is True
    for resp in (r, run, c.get("/api/eal/campaigns/CMP-TGT-001"), c.get("/api/eal/runs")):
        assert KEY not in resp.text


def test_a_target_that_refused_its_test_record_refuses_the_launch(env):
    c, app, sent = env
    _target(c)
    assert _check(c, app, "k8s-hec", 401) == "refused"
    c.post("/api/eal/campaigns", json=_campaign())
    r = _launch_live(c)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "COLLECTOR_TARGET_FAILING"
    assert sent == []


def test_an_accepted_target_launches_clean(env):
    c, app, _ = env
    _target(c)
    assert _check(c, app, "k8s-hec", 202) == "accepted"
    c.post("/api/eal/campaigns", json=_campaign())
    r = _launch_live(c)
    assert r.status_code == 200 and r.json()["degraded"] == []


def test_an_unknown_target_name_refuses_the_launch(env):
    c, _, _ = env
    _target(c)
    c.post("/api/eal/campaigns", json=_campaign(params={"collector_target": "typo"}))
    r = _launch_live(c)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "COLLECTOR_TARGET_UNKNOWN"


def test_collector_view_reports_the_target_without_its_key(env):
    c, _, _ = env
    _target(c)
    c.post("/api/eal/campaigns", json=_campaign())
    r = c.get("/api/eal/campaigns/CMP-TGT-001/collectors")
    body = r.json()
    assert body["collectors"][0]["url"] == TARGET_URL
    assert body["collectors"][0]["collector_target"] == "k8s-hec"
    assert body["collectors"][0]["has_auth_token"] is True
    assert body["collectors"][0]["allowlisted"] is True
    assert body["blocking"] == []
    assert KEY not in r.text


def test_campaign_preflight_uses_the_target_key(env):
    c, _, _ = env
    _target(c)
    c.post("/api/eal/campaigns", json=_campaign())
    seen = []

    def factory(timeout, verify_tls):
        def handler(req):
            seen.append(req.headers.get("authorization"))
            return httpx.Response(202)
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    from eal_simulator import collector as collector_mod
    saved = collector_mod._default_client
    collector_mod._default_client = factory
    try:
        r = c.post("/api/eal/campaigns/CMP-TGT-001/collectors/preflight", json={})
    finally:
        collector_mod._default_client = saved
    assert r.status_code == 200, r.text
    assert seen == [KEY]
    assert KEY not in r.text


def test_bundle_carries_the_target_url_and_never_the_key(env, tmp_path):
    c, _, _ = env
    _target(c)
    c.post("/api/eal/campaigns", json=_campaign())
    r = c.post("/api/eal/campaigns/CMP-TGT-001/bundle")
    assert r.status_code == 201, r.text
    assert r.json()["destinations"] == [TARGET_URL]
    archive = c.get(r.json()["download_url"]).content
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        for member in tar.getmembers():
            if member.isfile():
                assert KEY.encode() not in tar.extractfile(member).read(), member.name


def test_bundle_with_nothing_to_send_to_is_refused(env):
    c, _, _ = env
    c.post("/api/eal/campaigns", json=_campaign())
    r = c.post("/api/eal/campaigns/CMP-TGT-001/bundle")
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "COLLECTOR_TARGET_NOT_CONFIGURED"


# -- detector readiness reads the resolved route (LogSimv2 port, phase 1) -----

def _fw_campaign(cid):
    return {
        "campaign_id": cid, "name": "firewall via target",
        "authorized_by": "tester", "simulation_authorized": True,
        "target_allowlist": [], "dry_run": True,
        "steps": [{"step_id": "step-01", "plugin": "third_party_firewall_emitter",
                   "params": {}}],
    }


def test_readiness_judges_the_route_of_the_resolved_target(env):
    # The step carries no collector_url; the instance target supplies it, and
    # readiness must judge that URL, as launch would send to it.
    c, _, _ = env
    _target(c, name="fw-hec", plugins=["third_party_firewall_emitter"])
    assert c.post("/api/eal/campaigns", json=_fw_campaign("CMP-RDY-001")).status_code == 201
    body = c.post("/api/eal/campaigns/CMP-RDY-001/detector-readiness",
                  json={"soakless_attested": True}).json()
    assert body["routes"] == {"third_party_firewall_emitter": "http_collector"}
    assert {d["readiness"]["code"] for d in body["detectors"]} == {
        "DETECTOR_ROUTE_REQUIRES_XDM",
    }


def test_readiness_never_reads_the_unresolved_stand_in_as_a_collector(env):
    # No target and no inline URL: the resolver stands in an .invalid URL whose
    # path ends /logs/v1/event. Judging it as a real HTTP collector would be a
    # route claim about a door that does not exist.
    c, _, _ = env
    assert c.post("/api/eal/campaigns", json=_fw_campaign("CMP-RDY-002")).status_code == 201
    body = c.post("/api/eal/campaigns/CMP-RDY-002/detector-readiness",
                  json={"soakless_attested": True,
                        "xdm_modeled_sources": ["third_party_firewalls"]}).json()
    assert body["routes"] == {"third_party_firewall_emitter": "undeclared"}
    assert body["counts"]["armed"] == 0
    assert {d["readiness"]["code"] for d in body["detectors"]} == {
        "DETECTOR_ROUTE_UNRESOLVED",
    }

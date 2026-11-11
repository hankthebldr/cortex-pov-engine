"""POST /api/runs surfaces the Auto Triage advisory and still launches."""
from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

_URL = "https://api-secret-tenant.xdr.us.paloaltonetworks.com"
LIVE = {"state": "PRESENT", "auto_triage": {
    "live": True, "window_hours": 6, "score_threshold": 30,
    "score_off": False, "source": "tenant_config"}}


@pytest.fixture
def client(make_client, monkeypatch):
    from api.runs import router, compat_router

    def _boom(*a, **k):
        raise AssertionError("outbound")

    monkeypatch.setattr("integrations.xsiam.loader.load_xsiam_client", _boom)
    return make_client(router, compat_router)


def _seed(SessionLocal, *, layer, det_type="BIOC", with_tenant=True):
    from models import Agent, Scenario
    from security.credentials import CredentialStore

    async def _go():
        async with SessionLocal() as db:
            db.add(Scenario(
                scenario_id="SIM-EDR-996", name="adv", version="1.0", status="active",
                plane="EDR", uc_ref="UCS-EDR-01", uc_name="x", tc_ref="TC-EDR-01",
                tc_name="y", tc_refs=["TC-EDR-01"], mitre_tactic="TA0006",
                mitre_tactic_name="Credential Access", mitre_technique="T1003",
                mitre_technique_name="OS Credential Dumping",
                execution_identity={"default": "direct", "options": ["direct"]},
                push_supported=True, pull_supported=True,
                steps=[{"id": "step-01", "name": "s", "identity": "direct",
                        "command": "id", "mitre_technique": "T1003",
                        "expected_detections": [
                            {"plane": "EDR", "type": det_type, "description": "d"}]}]))
            db.add(Agent(agent_id="vm-1", hostname="h", os="linux",
                         capabilities=[], interpreters=[]))
            await db.commit()
            if with_tenant:
                store = CredentialStore(db)
                await store.put_integration(
                    name="lab", kind="xsiam_tenant", plaintext_secret="k" * 40,
                    config={"base_url": _URL, "api_key_id": "7", "capabilities": ["xql"]})
                row = (await store.list_integrations())[0]
                row.response_layer = layer
                row.response_layer_read_at = datetime.utcnow()
                await db.commit()
    asyncio.run(_go())


def _launch(client, **extra):
    return client.post("/api/runs", json={
        "scenario_id": "SIM-EDR-996", "mode": "pull", "target_agent_id": "vm-1", **extra})


def test_launch_returns_advisory_and_still_launches(client, session_factory):
    _seed(session_factory, layer=LIVE, det_type="ABIOC")
    resp = _launch(client)
    assert resp.status_code in (200, 201), resp.text
    w = [w for w in resp.json()["warnings"] if w["code"] == "SOCFW_TRIAGE_MAY_SEAL"]
    assert w and w[0]["level"] == "info" and w[0]["tenant"] == "lab"


def test_block_mode_launch_is_paired_and_warns(client, session_factory):
    _seed(session_factory, layer=LIVE)
    resp = _launch(client, agent_profile_mode="block")
    assert resp.status_code in (200, 201), resp.text
    assert [w["level"] for w in resp.json()["warnings"]
            if w["code"] == "SOCFW_TRIAGE_MAY_SEAL"] == ["warn"]


def test_malformed_stored_layer_never_blocks_launch(client, session_factory):
    _seed(session_factory, layer={"state": "PRESENT", "auto_triage": 7}, det_type="ABIOC")
    resp = _launch(client)
    assert resp.status_code in (200, 201), resp.text
    assert [w["code"] for w in resp.json()["warnings"]] == ["SOCFW_RESPONSE_LAYER_UNKNOWN"]


def test_no_tenant_integration_means_no_advisory(client, session_factory):
    _seed(session_factory, layer=None, det_type="ABIOC", with_tenant=False)
    resp = _launch(client)
    assert resp.status_code in (200, 201), resp.text
    assert "warnings" not in resp.json()

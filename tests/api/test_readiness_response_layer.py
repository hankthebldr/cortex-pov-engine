"""Readiness shows the stored response_layer probe, with no outbound call."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest

_URL = "https://api-secret-tenant.xdr.us.paloaltonetworks.com"


@pytest.fixture
def client(make_client, monkeypatch):
    from api import readiness

    def _boom(*a, **k):
        raise AssertionError("outbound")

    monkeypatch.setattr("integrations.xsiam.loader.load_xsiam_client", _boom)
    return make_client(readiness.router)


def _seed(SessionLocal, *, layer=None, read_at=None):
    from security.credentials import CredentialStore

    async def _go():
        async with SessionLocal() as db:
            store = CredentialStore(db)
            await store.put_integration(
                name="lab", kind="xsiam_tenant", plaintext_secret="k" * 40,
                config={"base_url": _URL, "api_key_id": "7", "capabilities": ["xql"]})
            row = (await store.list_integrations())[0]
            row.response_layer = layer
            row.response_layer_read_at = read_at
            await db.commit()
    asyncio.run(_go())


def test_no_stored_probe_is_none(client, session_factory):
    _seed(session_factory)
    body = client.get("/api/readiness").json()
    assert body["integrations"][0]["response_layer"] is None


def test_stored_probe_is_surfaced_with_proves_strings(client, session_factory):
    _seed(session_factory, layer={"state": "PRESENT"},
          read_at=datetime.utcnow() - timedelta(hours=1))
    rl = client.get("/api/readiness").json()["integrations"][0]["response_layer"]
    assert rl["state"] == "PRESENT"
    assert rl["layer"]["state"] == "PRESENT"
    assert rl["stale"] is False
    assert rl["read_at"].endswith("+00:00")
    assert rl["proves"] == "the SOC Framework is configured on this tenant"
    assert rl["does_not_prove"] == "that the framework handled any CortexSim case"


def test_readiness_marks_stale_after_24h(client, session_factory):
    _seed(session_factory, layer={"state": "PRESENT"},
          read_at=datetime.utcnow() - timedelta(hours=25))
    assert client.get("/api/readiness").json()["integrations"][0]["response_layer"]["stale"] is True


def test_readiness_makes_no_outbound_call(client, session_factory):
    _seed(session_factory, layer={"state": "ABSENT"}, read_at=datetime.utcnow())
    body = client.get("/api/readiness").json()
    assert body["outbound_calls"] == 0

"""panos_ngfw credential kind: validated at PUT time, key never listed."""
from __future__ import annotations

import json

import pytest


@pytest.fixture
def cred_client(make_client):
    from api.credentials import router
    return make_client(router)


def _put(c, config, secret="K" * 40):
    return c.put("/api/credentials/integrations", json={
        "name": "fw1", "kind": "panos_ngfw", "plaintext_secret": secret, "config": config})


def test_bad_host_rejected(cred_client):
    r = _put(cred_client, {"host": "https://x"})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "INTEGRATION_CONFIG_INVALID"


def test_missing_host_rejected(cred_client):
    assert _put(cred_client, {}).status_code == 400


def test_valid_host_stores_defaulted_config(cred_client):
    r = _put(cred_client, {"host": "192.0.2.1"})
    assert r.status_code == 200
    assert r.json()["config"]["verify_tls"] is True
    got = cred_client.get("/api/credentials/integrations/fw1").json()
    assert got["config"]["verify_tls"] is True and got["config"]["vsys"] == "vsys1"


def test_backing_secret_not_listed(cred_client):
    _put(cred_client, {"host": "192.0.2.1"}, secret="SUPER-PANOS-KEY-ZZZZ")
    body = cred_client.get("/api/credentials/secrets").json()
    blob = json.dumps(body)
    assert body["secrets"] == [] and "ZZZZ" not in blob and "__integration__" not in blob


def test_type_hint_forced_for_panos(cred_client, session_factory):
    import asyncio
    r = cred_client.put("/api/credentials/integrations", json={
        "name": "fw1", "kind": "panos_ngfw", "plaintext_secret": "K" * 40,
        "secret_type_hint": "generic", "config": {"host": "192.0.2.1"}})
    assert r.status_code == 200

    async def _hint():
        from security import CredentialStore
        async with session_factory() as s:
            row = await CredentialStore(s)._get_secret_row("__integration__/fw1")
            return row.type_hint, None
    hint, tail = asyncio.run(_hint())
    assert hint == "panos_api_key_credential" and tail is None
    assert cred_client.get("/api/credentials/secrets/__integration__/fw1").status_code == 404

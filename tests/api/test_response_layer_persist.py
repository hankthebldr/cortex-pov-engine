"""A tenant preflight persists its ``response_layer`` result on the integration row."""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import create_engine, inspect, text

_URL = "https://api-t.xdr.us.paloaltonetworks.com"


@pytest.fixture
def client(make_client):
    from api.connectors import router, runs_reconcile_router
    return make_client(router, runs_reconcile_router)


@pytest.fixture
def cred_client(make_client):
    from api.credentials import router
    return make_client(router)


def _register(cred_client):
    r = cred_client.put("/api/credentials/integrations", json={
        "name": "acme", "kind": "xsiam_tenant", "plaintext_secret": "k" * 40,
        "config": {"base_url": _URL, "api_key_id": "1", "capabilities": ["xql"]}})
    assert r.status_code < 300, r.text


def _patch_preflight(monkeypatch, stages, seen=None):
    from connectors import preflight as pf
    import integrations.xsiam.loader as loader

    async def fake_client(db, name):
        return object()

    async def fake_preflight(client, **kw):
        if seen is not None:
            seen.update(kw)
        return pf.PreflightReport(tenant="acme", kind="xsiam_tenant", stages=stages)

    monkeypatch.setattr(loader, "load_xsiam_client", fake_client)
    monkeypatch.setattr(pf, "preflight_tenant", fake_preflight)


def _row(session_factory):
    from security.credentials import CredentialStore

    async def _go():
        async with session_factory() as db:
            rows = await CredentialStore(db).list_integrations()
            return rows[0]
    return asyncio.run(_go())


def test_preflight_persists_response_layer(client, cred_client, session_factory, monkeypatch):
    from connectors import preflight as pf
    _register(cred_client)
    seen: dict = {}
    _patch_preflight(monkeypatch, [pf.Stage(
        "response_layer", pf.OK, pf.PF_OK,
        extra={"response_layer": {"state": "PRESENT", "framework_version": "3.31.4"}})], seen)
    assert client.post("/api/connectors/xsiam_tenant/preflight").status_code == 200
    assert seen["response_layer"] is True
    row = _row(session_factory)
    assert row.response_layer["state"] == "PRESENT"
    assert row.response_layer_read_at is not None
    d = row.to_dict()
    assert d["response_layer"]["state"] == "PRESENT"
    assert isinstance(d["response_layer_read_at"], str)


def test_skipped_stage_does_not_overwrite_stored_result(client, cred_client, session_factory, monkeypatch):
    from connectors import preflight as pf
    _register(cred_client)
    _patch_preflight(monkeypatch, [pf.Stage(
        "response_layer", pf.OK, pf.PF_OK, extra={"response_layer": {"state": "PRESENT"}})])
    client.post("/api/connectors/xsiam_tenant/preflight")
    first = _row(session_factory).response_layer_read_at
    _patch_preflight(monkeypatch, [pf.Stage("response_layer", pf.SKIPPED, "PF_SKIPPED")])
    client.post("/api/connectors/xsiam_tenant/preflight")
    row = _row(session_factory)
    assert row.response_layer["state"] == "PRESENT"
    assert row.response_layer_read_at == first


def test_response_layer_query_param_false_is_passed_through(client, cred_client, monkeypatch):
    _register(cred_client)
    seen: dict = {}
    _patch_preflight(monkeypatch, [], seen)
    client.post("/api/connectors/xsiam_tenant/preflight?response_layer=false")
    assert seen["response_layer"] is False


def test_migration_adds_columns_to_existing_db(tmp_path):
    from database import _migrate_integration_response_layer_columns

    eng = create_engine(f"sqlite:///{tmp_path/'legacy.db'}")
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE integration_credentials ("
                          "id INTEGER PRIMARY KEY, name VARCHAR, kind VARCHAR)"))
    for _ in range(2):  # second pass proves idempotence
        with eng.begin() as conn:
            _migrate_integration_response_layer_columns(conn)
    with eng.begin() as conn:
        cols = {c["name"] for c in inspect(conn).get_columns("integration_credentials")}
    assert {"response_layer", "response_layer_read_at"} <= cols


def test_reregistering_a_tenant_clears_the_stored_response_layer(session_factory):
    # A new key or URL may point at a different tenant: the previous tenant's
    # read must not stay "fresh" for up to 24 h (it would drive Readiness and
    # the launch advisory).
    from datetime import datetime

    from security.credentials import CredentialStore

    async def _go():
        async with session_factory() as db:
            store = CredentialStore(db)
            await store.put_integration(
                name="acme", kind="xsiam_tenant", plaintext_secret="k" * 40,
                config={"base_url": _URL, "api_key_id": "1"})
            row = (await store.list_integrations())[0]
            row.response_layer = {"state": "PRESENT"}
            row.response_layer_read_at = datetime.utcnow()
            await db.commit()
        async with session_factory() as db:
            store = CredentialStore(db)
            await store.put_integration(
                name="acme", kind="xsiam_tenant", plaintext_secret="n" * 40,
                config={"base_url": "https://api-other.xdr.us.paloaltonetworks.com",
                        "api_key_id": "2"})
            await db.commit()
        async with session_factory() as db:
            return (await CredentialStore(db).list_integrations())[0]

    row = asyncio.run(_go())
    assert row.response_layer is None
    assert row.response_layer_read_at is None

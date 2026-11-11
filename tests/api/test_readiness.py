"""GET /api/readiness — tenant-verified stated server-side, zero outbound calls."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime

import pytest

from integrations.xsiam.ledger import ledger, tenant_ledger_key

SECRET_BASE = "https://api-secret-tenant.xdr.us.paloaltonetworks.com"


@pytest.fixture(autouse=True)
def _reset_ledger():
    ledger.reset()
    yield
    ledger.reset()


@pytest.fixture
def client(make_client, monkeypatch):
    from api import readiness

    def _boom(*a, **k):
        raise AssertionError("outbound")

    # Any import of an outbound-capable symbol into the router must be dead.
    if hasattr(readiness, "load_xsiam_client"):
        monkeypatch.setattr(readiness, "load_xsiam_client", _boom)
    monkeypatch.setattr("integrations.xsiam.loader.load_xsiam_client", _boom)
    return make_client(readiness.router)


def _seed(SessionLocal, *, integrations=(), scenario=False, runs=(), assertion_runs=()):
    from models import AssertionRun, Run, Scenario
    from security.credentials import CredentialStore

    async def _go():
        async with SessionLocal() as db:
            store = CredentialStore(db)
            for name, kind, config in integrations:
                await store.put_integration(
                    name=name, kind=kind, plaintext_secret="k" * 40, config=config)
            if scenario:
                db.add(Scenario(
                    scenario_id="SIM-X-001", name="n", version="1", plane="EDR",
                    status="active", uc_ref="U", tc_ref="T", uc_name="u", tc_name="t",
                    mitre_tactic="TA0001", mitre_tactic_name="x",
                    mitre_technique="T1", mitre_technique_name="x", steps=[]))
            await db.flush()
            for kw in runs:
                db.add(kw)
            for kw in assertion_runs:
                db.add(kw)
            await db.commit()

    asyncio.run(_go())


def test_tenant_verified_is_zero_on_an_empty_db(client):
    body = client.get("/api/readiness").json()
    assert body["tenant_verified"] == 0
    assert body["tenant_verified_names"] == []
    assert body["integrations"] == []
    assert body["statement"].endswith("tenant-verified: 0")


def test_readiness_makes_no_outbound_call_and_carries_server_time_offset(client, session_factory):
    _seed(session_factory,
          integrations=[("lab", "xsiam_tenant",
                         {"base_url": SECRET_BASE, "api_key_id": "7", "capabilities": ["xql"]})],
          scenario=True)
    r = client.get("/api/readiness")
    assert r.status_code == 200
    body = r.json()
    assert body["outbound_calls"] == 0
    assert body["server_time"].endswith("+00:00")
    datetime.fromisoformat(body["server_time"])
    row = body["integrations"][0]
    assert row["rungs"] == {"authored": True, "configured": True,
                            "reachable": False, "verified": False}
    assert row["xql_capability_granted"] is True
    assert row["capabilities"] == ["xql"]
    assert set(body["proves"]) == {"authored", "configured", "reachable", "verified"}
    assert body["proves"]["verified"]["does_not_prove"] == "—"
    assert body["proves"]["authored"]["proves"] == "a claim exists in the corpus"


def test_ledger_matches_snapshot_for_same_key(client, session_factory):
    from connectors import tuning

    _seed(session_factory, integrations=[
        ("lab", "xsiam_tenant", {"base_url": SECRET_BASE, "api_key_id": "7"}),
        ("recon", "xsiam", {"fqdn": "x.example", "api_key_id": "1"}),
    ])
    key = tenant_ledger_key(SECRET_BASE)
    limit = tuning.xsiam_max_queries_per_day()
    ledger.charge(key, limit=limit)
    body = client.get("/api/readiness").json()
    by = {i["name"]: i for i in body["integrations"]}
    assert by["lab"]["ledger"] == ledger.snapshot(key, limit=limit)
    assert by["lab"]["ledger"]["queries_last_24h"] == 1
    assert by["recon"]["ledger"] is None
    assert by["lab"]["xql_capability_granted"] is False


def test_xsiam_tenant_without_base_url_has_no_ledger(client, session_factory):
    _seed(session_factory, integrations=[("lab", "xsiam_tenant", {"api_key_id": "7"})])
    row = client.get("/api/readiness").json()["integrations"][0]
    assert row["ledger"] is None


def test_no_credential_values_leak(client, session_factory):
    _seed(session_factory, integrations=[
        ("lab", "xsiam_tenant", {"base_url": SECRET_BASE, "api_key_id": "7",
                                 "fqdn": "leaky.example", "capabilities": ["xql"]}),
    ])
    text = json.dumps(client.get("/api/readiness").json())
    # NOTE: ledger["tenant"] is the ledger key (the tenant host) by the
    # snapshot contract, so the host itself is deliberately not asserted absent.
    for needle in ("api_key_id", "base_url", "fqdn", "leaky.example", "https://"):
        assert needle not in text


def test_verified_rung_and_count_follow_terminal_tenant_verdicts(client, session_factory):
    from models import AssertionRun

    _seed(session_factory,
          integrations=[("lab", "xsiam_tenant", {"base_url": SECRET_BASE})],
          assertion_runs=[
              AssertionRun(run_id="ar-1", assertion_id="A-1", tenant="lab",
                           tc_verdict="pass", started_at=datetime.utcnow()),
          ])
    body = client.get("/api/readiness").json()
    assert body["tenant_verified"] == 1
    assert body["tenant_verified_names"] == ["lab"]
    assert body["integrations"][0]["rungs"]["verified"] is True
    assert body["statement"].endswith("tenant-verified: 1")

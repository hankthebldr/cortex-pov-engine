"""API surface for the connector stack: preflight, credential hygiene, health.

The preflight endpoint is the answer to "is my connection working, and can I
read the datasets I need?" — asked before a POV, not discovered during one.
"""
from __future__ import annotations

import asyncio
import json

import pytest


@pytest.fixture
def client(make_client):
    from api.connectors import router, runs_reconcile_router
    return make_client(router, runs_reconcile_router)


@pytest.fixture
def cred_client(make_client):
    from api.credentials import router
    return make_client(router)


def _put_integration(cred_client, *, name, kind, config, secret="k" * 40):
    return cred_client.put("/api/credentials/integrations", json={
        "name": name, "kind": kind, "plaintext_secret": secret, "config": config})


_GOOD_FQDN = "api-t.xdr.us.paloaltonetworks.com"
_GOOD_URL = f"https://{_GOOD_FQDN}"


# ── preflight endpoint ─────────────────────────────────────────────────────


def test_preflight_with_nothing_registered_says_so_explicitly(client):
    """Not an empty green report, and not a 500. "Nothing configured" is a
    legitimate state with a specific remediation."""
    r = client.post("/api/connectors/xsiam/preflight")
    assert r.status_code == 200
    body = r.json()
    assert body["overall"] == "blocked"
    assert body["stages"][0]["code"] == "PF_NO_INTEGRATION"
    assert "`pending`" in body["stages"][0]["remediation"]
    assert body["queries_issued"] == 0


def test_preflight_returns_200_with_blocked_rather_than_an_http_error(
        client, cred_client, monkeypatch):
    """An exception would hide the six stages after the failure. The CHECK
    succeeded; the tenant is what is blocked."""
    import connectors.base as base
    from connectors.base import ConnectorError

    _put_integration(cred_client, name="acme", kind="xsiam",
                     config={"fqdn": _GOOD_FQDN, "api_key_id": "1"})

    def boom(*a, **kw):
        raise ConnectorError("transport error reaching https://api-t...: no route")

    # The endpoint builds its own connector, so patch the DEFAULT fetcher the
    # Connector base picks up — this exercises the real resolution path rather
    # than handing preflight a stub it would never see in production.
    monkeypatch.setattr(base, "default_http_fetcher", boom)

    r = client.post("/api/connectors/xsiam/preflight")
    assert r.status_code == 200
    assert r.json()["overall"] == "blocked"
    # Every stage is present, none silently omitted: an absent stage reads as
    # "fine", an explicit `skipped` reads as "unknown".
    assert [s["id"] for s in r.json()["stages"]] == [
        "config", "dns_tls", "auth", "scope_alerts"]


def test_preflight_on_an_unknown_kind_is_a_structured_400(client):
    r = client.post("/api/connectors/nope/preflight")
    assert r.status_code in (200, 400)
    if r.status_code == 400:
        assert r.json()["detail"]["code"] == "PREFLIGHT_UNSUPPORTED"


def test_the_connector_listing_reports_the_tenant_kind_too(client, cred_client):
    """A DC who correctly registered an `xsiam_tenant` credential used to read
    `configured: false` here, because the listing only looked at the reconcile
    kind."""
    _put_integration(cred_client, name="acme-tenant", kind="xsiam_tenant",
                     config={"base_url": _GOOD_URL, "region": "us", "api_key_id": "1"})
    body = client.get("/api/connectors").json()
    xsiam = {c["kind"]: c for c in body["connectors"]}["xsiam"]
    assert xsiam["configured"] is True
    assert [i["name"] for i in xsiam["equivalent_integrations"]] == ["acme-tenant"]
    assert body["tenant_integrations"][0]["name"] == "acme-tenant"
    assert xsiam["preflight_url"] == "/api/connectors/xsiam/preflight"


def test_capabilities_default_to_read_only_and_never_imply_xql(client, cred_client):
    """The single-registration change must NOT silently grant XQL to every
    credential already in the field. Absent == read only."""
    _put_integration(cred_client, name="legacy", kind="xsiam_tenant",
                     config={"base_url": _GOOD_URL, "region": "us", "api_key_id": "1"})
    body = client.get("/api/connectors").json()
    assert body["tenant_integrations"][0]["capabilities"] == ["read_alerts"]


# ── F-04 write half: a bad tenant URL is a 400 at PUT, not a leak later ────


@pytest.mark.parametrize("bad", [
    "http://10.0.0.5:8080", "https://evil.example.com",
    "https://user:pass@api-t.xdr.us.paloaltonetworks.com",
])
@pytest.mark.parametrize("kind", ["xsiam", "xsiam_tenant"])
def test_registering_a_non_tenant_url_is_rejected_at_write_time(cred_client, bad, kind):
    """Pre-fix this was accepted silently and only became a problem at the first
    reconcile — by which point the API key had been sent to that host."""
    r = _put_integration(cred_client, name="bad", kind=kind, config={"fqdn": bad})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "INTEGRATION_CONFIG_INVALID"


@pytest.mark.parametrize("kind", ["xsiam", "xsiam_tenant"])
def test_a_real_tenant_url_still_registers(cred_client, kind):
    r = _put_integration(cred_client, name=f"ok-{kind}", kind=kind,
                         config={"base_url": _GOOD_URL, "region": "us",
                                 "api_key_id": "1"})
    assert r.status_code == 200


def test_a_non_tenant_integration_kind_is_unaffected(cred_client):
    r = _put_integration(cred_client, name="s3", kind="aws",
                         config={"bucket": "x"})
    assert r.status_code == 200


# ── F-07: the secrets listing leaked a preview of every tenant API key ────


def test_the_secrets_listing_never_returns_integration_backing_rows(cred_client):
    """`put_integration` stores the API key as `__integration__/{name}` and its
    docstring claims that keeps it out of `list()`. It did not — `list()` is an
    unfiltered SELECT — so this endpoint returned `preview_tail`, the last four
    characters of the customer's API key, unauthenticated by default."""
    _put_integration(cred_client, name="cust-prod", kind="xsiam_tenant",
                     secret="SUPER-SECRET-TENANT-KEY-ZZZZ",
                     config={"base_url": _GOOD_URL, "region": "us", "api_key_id": "1"})

    body = cred_client.get("/api/credentials/secrets").json()
    blob = json.dumps(body)
    assert "__integration__" not in blob
    assert "ZZZZ" not in blob
    assert body["secrets"] == []


def test_the_backing_secret_is_not_addressable_by_name_either(cred_client):
    """The filter on the listing is worthless if the row is still fetchable."""
    _put_integration(cred_client, name="cust-prod", kind="xsiam_tenant",
                     secret="SUPER-SECRET-TENANT-KEY-ZZZZ",
                     config={"base_url": _GOOD_URL, "region": "us", "api_key_id": "1"})
    r = cred_client.get("/api/credentials/secrets/__integration__/cust-prod")
    assert r.status_code == 404
    assert "ZZZZ" not in r.text


def test_an_ordinary_secret_still_lists_with_its_preview(cred_client):
    """The fix must not blind the operator to their own secrets."""
    cred_client.put("/api/credentials/secrets", json={
        "name": "my-token", "plaintext": "a" * 30 + "TAIL", "type_hint": "generic"})
    body = cred_client.get("/api/credentials/secrets").json()
    assert [s["name"] for s in body["secrets"]] == ["my-token"]
    assert body["secrets"][0]["preview_tail"] == "...TAIL"


def test_no_api_response_carries_a_key_preview_for_an_integration(cred_client):
    """Belt and braces: even a row that escapes the name filter must carry no
    key material, because a future export or report may reach it another way."""
    _put_integration(cred_client, name="cust-prod", kind="xsiam_tenant",
                     secret="SUPER-SECRET-TENANT-KEY-ZZZZ",
                     config={"base_url": _GOOD_URL, "region": "us", "api_key_id": "1"})
    integrations = cred_client.get("/api/credentials/integrations").json()
    blob = json.dumps(integrations)
    assert "ZZZZ" not in blob and "SUPER-SECRET" not in blob


# ── the health component this router hands to the health builder ──────────


def test_connector_health_component_makes_no_tenant_calls(session_factory,
                                                          monkeypatch):
    """/api/health is POLLED. A health check that spends the customer's tenant
    quota is a denial-of-service with good intentions."""
    from api.connectors import connector_health_component
    import connectors.base as base

    def boom(*a, **kw):  # pragma: no cover — must never be called
        raise AssertionError("health must not reach the network")

    monkeypatch.setattr(base, "default_http_fetcher", boom)

    async def _do():
        async with session_factory() as db:
            return await connector_health_component(db)

    body = asyncio.run(_do())
    assert body["status"] == "ok"
    assert "xsiam" in body["connectors"]
    assert body["configured_kinds"] == []
    assert body["breaker_open"] is False
    assert "makes no tenant calls" in body["note"]


def test_connector_health_reports_an_open_breaker(session_factory):
    """The health component must read the row the VERIFIER actually writes.

    It used to snapshot the ledger by INTEGRATION NAME while
    ``engine.verifier._tenant_name`` charges and trips it by TENANT HOST, so the
    two looked at different rows: a breaker could be open with every verify
    degrading to pending, and /api/health would report ``breaker_open: false``
    on a full quota. This test tripped it by name too, so it passed against the
    bug. It now trips it exactly as the verifier does.
    """
    from api.connectors import connector_health_component
    from engine.verifier import tenant_ledger_key
    from integrations.xsiam.ledger import ledger
    from security.credentials import CredentialStore

    key = tenant_ledger_key(_GOOD_URL)
    assert key != "acme", "the point of this test is that the key is NOT the name"

    async def _do():
        async with session_factory() as db:
            await CredentialStore(db).put_integration(
                name="acme", kind="xsiam_tenant", plaintext_secret="k" * 40,
                config={"base_url": _GOOD_URL, "region": "us", "api_key_id": "1"})
            await db.commit()
            ledger.trip(key, cooldown_seconds=900)
            return await connector_health_component(db)

    body = asyncio.run(_do())
    assert body["breaker_open"] is True
    assert body["quota"][0]["tenant"] == key
    # The operator-facing name still travels, because a DC needs to know WHICH
    # integration is throttled — the host is the key, not the label.
    assert body["quota"][0]["integration"] == "acme"
    ledger.reset()


def test_health_sees_a_breaker_tripped_through_the_verifiers_own_path(session_factory):
    """The regression guard: trip via the verifier's key function, read via health.

    If either side changes how it derives the key, this fails — which is the
    only thing that stops the two silently diverging again.
    """
    from api.connectors import connector_health_component
    from engine.verifier import tenant_ledger_key
    from integrations.xsiam.ledger import ledger
    from security.credentials import CredentialStore

    class _Client:
        _base = _GOOD_URL

    from engine.verifier import _tenant_name

    async def _do():
        async with session_factory() as db:
            await CredentialStore(db).put_integration(
                name="acme", kind="xsiam_tenant", plaintext_secret="k" * 40,
                config={"base_url": _GOOD_URL, "region": "us", "api_key_id": "1"})
            await db.commit()
            ledger.trip(_tenant_name(_Client()), cooldown_seconds=900)
            return await connector_health_component(db)

    body = asyncio.run(_do())
    assert body["breaker_open"] is True, (
        "the verifier tripped the breaker and /api/health cannot see it — the "
        "two surfaces are keying the ledger differently again"
    )
    assert _tenant_name(_Client()) == tenant_ledger_key(_GOOD_URL)
    ledger.reset()


# ── manual ingest reports what it dropped ─────────────────────────────────


def test_observations_with_unreadable_timestamps_are_reported(client, session_factory):
    """Pre-fix these were dated to `now`, which inflated MTTD and produced
    threshold FAILs out of a formatting problem."""
    from datetime import datetime as _dt

    from models import Result, Run

    async def _seed():
        async with session_factory() as s:
            t0 = _dt(2026, 6, 10, 12, 0, 0)
            s.add(Run(run_id="run-drop", scenario_id="SIM-EDR-001", mode="pull",
                      status="complete", started_at=t0))
            s.add(Result(run_id="run-drop", step_id="s1", plane="EDR",
                         signal_type="BIOC", expected_detection="x", observed=False,
                         executed_at=t0, mitre_technique="T1003.001"))
            await s.commit()
    asyncio.run(_seed())

    r = client.post("/api/runs/run-drop/observations", json={"observations": [
        {"technique": "T1003.001", "observed_at": "2026-06-10T12:00:30Z"},
        {"technique": "T1003.001", "observed_at": "10/06/2026 12:00"},
        {"technique": "T1003.001"},
    ]})
    body = r.json()
    assert body["ingested"] == 1
    assert body["dropped_unparseable_timestamps"] == 2
    assert "floor, not a measurement" in body["warning"]

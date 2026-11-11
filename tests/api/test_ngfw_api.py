"""NGFW API surface (spec §4.2, §4.3): registration list, keygen, per-agent source
overrides and the panos_ngfw preflight branch.

Every firewall here is an ``httpx.MockTransport`` injected through
``api.ngfw._transport_override``; nothing contacts a real firewall."""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "panos"
CANARY = "Pw-canary-7f3a"
USER = "cortexsim-ro"
KEY = (FIXTURES / "keygen__ok.xml").read_text().split("<key>")[1].split("</key>")[0]


class Recorder:
    """A fake management plane: answers keygen from a fixture (or a status), every other
    request with HTTP 500 (an upstream error: reached, never short-circuits)."""

    def __init__(self, keygen: object = "keygen__ok.xml"):
        self.keygen = keygen
        self.requests: list[httpx.Request] = []
        self.transport = httpx.MockTransport(self._handle)

    def forms(self) -> list[dict]:
        return [parse_qs(r.content.decode()) for r in self.requests]

    def hosts(self) -> set[str]:
        return {r.url.host for r in self.requests}

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        form = parse_qs(request.content.decode())
        if form.get("type", [""])[0] == "keygen":
            if isinstance(self.keygen, int):
                return httpx.Response(self.keygen, content=b"")
            return httpx.Response(200, content=(FIXTURES / self.keygen).read_bytes())
        return httpx.Response(500, content=b"unmapped")


@pytest.fixture
def fw(monkeypatch):
    import api.ngfw as ngfw

    def install(**kw) -> Recorder:
        rec = Recorder(**kw)
        monkeypatch.setattr(ngfw, "_transport_override", rec.transport)
        return rec
    return install


@pytest.fixture
def client(make_client):
    from api.connectors import router as connectors_router
    from api.credentials import router as credentials_router
    from api.ngfw import router as ngfw_router
    return make_client(credentials_router, ngfw_router, connectors_router)


def _keygen(client, **over):
    body = {"name": "fw-a", "host": "192.0.2.1", "verify_tls": True,
            "username": USER, "password": CANARY, **over}
    return client.post("/api/ngfw/keygen", json=body)


def _register(client, name, host, secret="K" * 40, config=None):
    r = client.put("/api/credentials/integrations", json={
        "name": name, "kind": "panos_ngfw", "plaintext_secret": secret,
        "config": {"host": host, **(config or {})}})
    assert r.status_code == 200, r.text
    return r


def _seed_agents(session_factory, *agents):
    async def go():
        from models import Agent
        async with session_factory() as s:
            for agent_id, hostname, last_ip in agents:
                s.add(Agent(agent_id=agent_id, hostname=hostname, os="linux",
                            capabilities=[], interpreters=[], last_ip=last_ip))
            await s.commit()
    asyncio.run(go())


def _dump_db(session_factory) -> str:
    """Every row of every table, as text."""
    async def go():
        from sqlalchemy import text
        out = []
        async with session_factory() as s:
            names = (await s.execute(text(
                "SELECT name FROM sqlite_master WHERE type='table'"))).scalars().all()
            for n in names:
                for row in (await s.execute(text(f'SELECT * FROM "{n}"'))).all():
                    out.append(repr(tuple(row)))
        return "\n".join(out)
    return asyncio.run(go())


def _secret(session_factory, name):
    async def go():
        from security import CredentialStore
        async with session_factory() as s:
            return await CredentialStore(s).get_integration_secret(name)
    return asyncio.run(go())


# ------------------------------------------------------------------ keygen


def test_keygen_stores_key_and_returns_metadata_only(client, fw, session_factory):
    rec = fw()
    r = _keygen(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "fw-a" and body["kind"] == "panos_ngfw"
    assert body["config"]["host"] == "192.0.2.1" and body["config"]["vsys"] == "vsys1"
    blob = json.dumps(body)
    assert "key" not in body and "password" not in body and "username" not in body
    assert KEY not in blob and CANARY not in blob and USER not in blob
    assert _secret(session_factory, "fw-a") == KEY
    # Positive control: the password really was sent, in the form body, once.
    assert len(rec.requests) == 1
    form = rec.forms()[0]
    assert form["type"] == ["keygen"] and form["password"] == [CANARY]
    assert CANARY not in str(rec.requests[0].url)


def test_keygen_success_leaves_no_password_in_logs_responses_or_db(
        client, fw, session_factory, caplog):
    caplog.set_level(logging.DEBUG)
    fw()
    r = _keygen(client)
    assert r.status_code == 200
    assert CANARY not in r.text
    assert CANARY not in caplog.text
    assert CANARY not in _dump_db(session_factory)


def test_keygen_rejected_403_leaves_no_password_anywhere(client, fw, session_factory, caplog):
    caplog.set_level(logging.DEBUG)
    fw(keygen=403)
    r = _keygen(client)
    assert r.status_code == 401
    d = r.json()["detail"]
    assert d["code"] == "PN_KEYGEN_REJECTED" and d["error"] and d["detail"]
    for text in (r.text, caplog.text, _dump_db(session_factory)):
        assert CANARY not in text and USER not in text
    assert client.get("/api/ngfw").json()["firewalls"] == []


def test_keygen_panos_error_body_is_rejected(client, fw):
    fw(keygen="error__403.xml")
    r = _keygen(client)
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "PN_KEYGEN_REJECTED"
    assert "Invalid credentials" not in r.text  # fixed text, never the firewall's


@pytest.mark.parametrize("exc_kind,code", [
    ("connect", "PN_UNREACHABLE"), ("http500", "PN_UPSTREAM_ERROR")])
def test_keygen_transport_failures_are_502(client, monkeypatch, exc_kind, code):
    import api.ngfw as ngfw

    def handle(request):
        if exc_kind == "connect":
            raise httpx.ConnectError("no route", request=request)
        return httpx.Response(500, content=CANARY.encode())
    monkeypatch.setattr(ngfw, "_transport_override", httpx.MockTransport(handle))
    r = _keygen(client)
    assert r.status_code == 502
    assert r.json()["detail"]["code"] == code
    assert CANARY not in r.text


def test_keygen_bad_host_is_400_and_sends_nothing(client, fw):
    rec = fw()
    r = _keygen(client, host="https://192.0.2.1/api")
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "INTEGRATION_CONFIG_INVALID"
    assert rec.requests == []
    assert CANARY not in r.text


@pytest.mark.parametrize("body", [
    {"name": "fw-a", "host": "192.0.2.1", "username": USER, "password": CANARY,
     "verify_tls": "not-a-bool-" + CANARY},
    {"name": "", "host": "192.0.2.1", "username": USER, "password": CANARY},
    {"host": "192.0.2.1", "username": USER, "password": CANARY},
    {"name": "fw-a", "host": "192.0.2.1", "username": USER, "password": [CANARY]},
    {"name": "fw-a", "host": "192.0.2.1", "username": USER, "password": CANARY,
     "vsys": {"x": CANARY}},
])
def test_keygen_422_never_echoes_the_password(client, fw, caplog, body):
    caplog.set_level(logging.DEBUG)
    rec = fw()
    r = client.post("/api/ngfw/keygen", json=body)
    assert r.status_code == 422
    assert CANARY not in r.text and CANARY not in caplog.text
    assert r.json()["detail"]["code"] == "REQUEST_INVALID"
    assert rec.requests == []


def test_keygen_non_json_body_is_422_without_echo(client, fw):
    fw()
    r = client.post("/api/ngfw/keygen", content=b"password=" + CANARY.encode(),
                    headers={"content-type": "application/json"})
    assert r.status_code == 422 and CANARY not in r.text


def test_keygen_refuses_to_overwrite_another_kind(client, fw):
    rec = fw()
    client.put("/api/credentials/integrations", json={
        "name": "fw-a", "kind": "xsiam", "plaintext_secret": "x" * 40,
        "config": {"fqdn": "api-t.xdr.us.paloaltonetworks.com", "api_key_id": "1"}})
    r = _keygen(client)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "INTEGRATION_KIND_CONFLICT"
    assert rec.requests == []


def test_rekeygen_keeps_operator_source_overrides(client, fw):
    fw()
    assert _keygen(client).status_code == 200
    assert client.put("/api/ngfw/fw-a/sources/a1",
                      json={"observed_source": "192.0.2.77"}).status_code == 200
    r = _keygen(client, verify_tls=False)
    assert r.status_code == 200
    cfg = r.json()["config"]
    assert cfg["verify_tls"] is False and cfg["observed_sources"] == {"a1": "192.0.2.77"}


# ------------------------------------------------------------------ GET /api/ngfw


def test_list_firewalls_metadata_and_write_flag(client, monkeypatch):
    monkeypatch.setenv("CORTEXSIM_PANOS_ALLOW_WRITE", "true")
    _register(client, "fw-b", "192.0.2.2", config={"forwarding_path": "syslog"})
    _register(client, "fw-a", "192.0.2.1")
    client.put("/api/credentials/integrations", json={
        "name": "tenant", "kind": "xsiam", "plaintext_secret": "x" * 40,
        "config": {"fqdn": "api-t.xdr.us.paloaltonetworks.com", "api_key_id": "1"}})
    body = client.get("/api/ngfw").json()
    assert body["write_enabled"] is False and body["tenant_verified"] == 0
    assert [f["name"] for f in body["firewalls"]] == ["fw-a", "fw-b"]
    b = body["firewalls"][1]
    assert set(b) == {"name", "host", "verify_tls", "vsys", "forwarding_path",
                      "last_verified_ok", "last_verified_error", "last_verified_at"}
    assert b["host"] == "192.0.2.2" and b["forwarding_path"] == "syslog"
    assert "K" * 40 not in json.dumps(body)


def test_write_enabled_false_even_when_settings_flag_is_on(client, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "CORTEXSIM_PANOS_ALLOW_WRITE", True)
    assert client.get("/api/ngfw").json()["write_enabled"] is False


# ------------------------------------------------------------------ sources PUT


def test_sources_put_round_trips_and_keeps_verification(client, fw, session_factory):
    fw()
    _register(client, "fw-a", "192.0.2.1")

    async def mark():
        from security import CredentialStore
        async with session_factory() as s:
            await CredentialStore(s).mark_integration_verified("fw-a", ok=True)
            await s.commit()
    asyncio.run(mark())

    r = client.put("/api/ngfw/fw-a/sources/a1", json={
        "observed_source": "192.0.2.50", "agent_addresses": ["10.0.0.5", "fe80::1"]})
    assert r.status_code == 200, r.text
    assert r.json() == {"firewall": "fw-a", "agent_id": "a1", "observed_source": "192.0.2.50",
                        "agent_addresses": ["10.0.0.5", "fe80::1"]}
    got = client.get("/api/credentials/integrations/fw-a").json()
    assert got["config"]["observed_sources"] == {"a1": "192.0.2.50"}
    assert got["config"]["agent_addresses"] == {"a1": ["10.0.0.5", "fe80::1"]}
    assert got["last_verified_ok"] is True
    assert _secret(session_factory, "fw-a") == "K" * 40

    # Omitted field: unchanged. Explicit null: removed.
    r = client.put("/api/ngfw/fw-a/sources/a1", json={"observed_source": None})
    assert r.json() == {"firewall": "fw-a", "agent_id": "a1", "observed_source": None,
                        "agent_addresses": ["10.0.0.5", "fe80::1"]}
    r = client.put("/api/ngfw/fw-a/sources/a1", json={"agent_addresses": None})
    assert r.json()["agent_addresses"] is None
    got = client.get("/api/credentials/integrations/fw-a").json()
    assert got["config"]["observed_sources"] == {} and got["config"]["agent_addresses"] == {}
    assert got["last_verified_ok"] is True


def test_sources_put_bad_ip_is_400_and_changes_nothing(client):
    _register(client, "fw-a", "192.0.2.1")
    r = client.put("/api/ngfw/fw-a/sources/a1", json={"observed_source": "not-an-ip"})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "INTEGRATION_CONFIG_INVALID"
    r = client.put("/api/ngfw/fw-a/sources/a1", json={"agent_addresses": ["10.0.0.1", "x"]})
    assert r.status_code == 400
    got = client.get("/api/credentials/integrations/fw-a").json()
    assert got["config"]["observed_sources"] == {} and got["config"]["agent_addresses"] == {}


def test_sources_put_unknown_firewall_is_404(client):
    r = client.put("/api/ngfw/nope/sources/a1", json={"observed_source": "192.0.2.5"})
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "INTEGRATION_NOT_FOUND"


def test_sources_put_on_a_non_panos_integration_is_404(client):
    client.put("/api/credentials/integrations", json={
        "name": "tenant", "kind": "xsiam", "plaintext_secret": "x" * 40,
        "config": {"fqdn": "api-t.xdr.us.paloaltonetworks.com", "api_key_id": "1"}})
    r = client.put("/api/ngfw/tenant/sources/a1", json={"observed_source": "192.0.2.5"})
    assert r.status_code == 404


# ------------------------------------------------------------------ preflight


def test_preflight_dispatches_to_panos_with_pn_codes(client, fw, session_factory):
    rec = fw()
    _register(client, "fw-a", "192.0.2.1")
    _seed_agents(session_factory, ("a1", "pov-target", "192.0.2.50"))
    r = client.post("/api/connectors/panos_ngfw/preflight")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "panos_ngfw" and body["firewall"] == "fw-a"
    assert body["tenant_verified"] == 0
    assert body["credential"] == {"name": "fw-a", "kind": "panos_ngfw"}
    stage_codes = [s["code"] for s in body["stages"]]
    assert all(c.startswith("PN_") for c in stage_codes), stage_codes
    assert not any(c.startswith("PF_") for c in stage_codes)
    assert body["queries_issued"] == len(rec.requests) > 0
    assert [s["agent_id"] for s in body["sources"]] == ["a1"]
    assert "K" * 40 not in r.text


def test_preflight_with_no_integration_is_pn_no_integration(client, fw):
    rec = fw()
    r = client.post("/api/connectors/panos_ngfw/preflight")
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "panos_ngfw" and body["overall"] == "blocked"
    assert body["stages"][0]["code"] == "PN_NO_INTEGRATION"
    assert body["stages"][0]["remediation"]
    assert body["queries_issued"] == 0 and rec.requests == []


def test_preflight_named_integration_missing_is_404(client, fw):
    fw()
    _register(client, "fw-a", "192.0.2.1")
    r = client.post("/api/connectors/panos_ngfw/preflight?integration=nope")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "INTEGRATION_NOT_FOUND"


def test_preflight_undecryptable_credential(client, fw, monkeypatch):
    rec = fw()
    _register(client, "fw-a", "192.0.2.1")
    from security.credentials import CredentialStore

    async def boom(self, name):
        raise RuntimeError("ciphertext could not be decrypted with the current master key")
    monkeypatch.setattr(CredentialStore, "get_integration_secret", boom)
    r = client.post("/api/connectors/panos_ngfw/preflight")
    assert r.status_code == 200
    body = r.json()
    assert body["overall"] == "blocked"
    assert body["stages"][0]["code"] == "PN_CREDENTIAL_UNDECRYPTABLE"
    assert rec.requests == []
    monkeypatch.undo()
    got = client.get("/api/credentials/integrations/fw-a").json()
    assert got["last_verified_ok"] is False
    assert got["last_verified_error"] == "PN_CREDENTIAL_UNDECRYPTABLE"


def test_preflight_marks_integration_verified(client, monkeypatch):
    import api.ngfw as ngfw

    def handle(request):
        raise httpx.ConnectError("no route", request=request)
    monkeypatch.setattr(ngfw, "_transport_override", httpx.MockTransport(handle))
    _register(client, "fw-a", "192.0.2.1")
    body = client.post("/api/connectors/panos_ngfw/preflight").json()
    assert body["overall"] == "blocked"
    got = client.get("/api/credentials/integrations/fw-a").json()
    assert got["last_verified_ok"] is False
    assert got["last_verified_error"] == "PN_UNREACHABLE"
    assert got["last_verified_at"] is not None
    listed = client.get("/api/ngfw").json()["firewalls"][0]
    assert listed["last_verified_error"] == "PN_UNREACHABLE"


def test_preflight_agents_filter(client, fw, session_factory):
    fw()
    _register(client, "fw-a", "192.0.2.1")
    _seed_agents(session_factory, ("a1", "h1", "192.0.2.50"), ("a2", "h2", "192.0.2.51"),
                 ("a3", "h3", "192.0.2.52"))
    body = client.post("/api/connectors/panos_ngfw/preflight?agents=a3,a1").json()
    assert sorted(s["agent_id"] for s in body["sources"]) == ["a1", "a3"]
    body = client.post("/api/connectors/panos_ngfw/preflight").json()
    assert sorted(s["agent_id"] for s in body["sources"]) == ["a1", "a2", "a3"]


def test_preflight_no_agents_is_degraded_entry(client, fw):
    fw()
    _register(client, "fw-a", "192.0.2.1")
    body = client.post("/api/connectors/panos_ngfw/preflight").json()
    assert [s["code"] for s in body["sources"]] == ["PN_NO_AGENTS"]


def test_two_firewalls_selection_and_isolated_overrides(client, fw, session_factory):
    """Review Focus 4: the named firewall is the one contacted, the default is the
    first by name, and a per-agent override is per-firewall, never global."""
    rec = fw()
    _register(client, "fw-b", "192.0.2.2")
    _register(client, "fw-a", "192.0.2.1")
    _seed_agents(session_factory, ("a1", "h1", "192.0.2.50"))
    assert client.put("/api/ngfw/fw-a/sources/a1",
                      json={"observed_source": "198.18.0.9"}).status_code == 200

    body = client.post("/api/connectors/panos_ngfw/preflight?integration=fw-b").json()
    assert body["firewall"] == "fw-b" and body["mgmt_host"] == "192.0.2.2"
    assert rec.hosts() == {"192.0.2.2"}
    src = body["sources"][0]
    assert src["fw_observed_src"] == "192.0.2.50"
    assert src["fw_src_provenance"] == "inferred:last_ip"
    assert "198.18.0.9" not in json.dumps(body)

    rec.requests.clear()
    body = client.post("/api/connectors/panos_ngfw/preflight").json()
    assert body["firewall"] == "fw-a" and rec.hosts() == {"192.0.2.1"}
    src = body["sources"][0]
    assert src["fw_observed_src"] == "198.18.0.9" and src["fw_src_provenance"] == "entered"


def test_panos_is_not_a_reconcile_connector(client):
    body = client.get("/api/connectors").json()
    assert "panos_ngfw" not in json.dumps(body.get("connectors", body))

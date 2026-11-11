"""Phase 3a — Agent.last_ip capture on request-bearing endpoints
(core/api/agents.py).

The stitch-context resolver promotes a target agent's REAL observed source IP
to the run's ``src_ip`` (see engine.stitch_context.from_agent). That address is
captured HERE, on the beacon's request-bearing calls:

  - ``POST /api/agents/register``  — both the create and update branches;
  - ``GET  /api/agents/{id}/tasks`` — the heartbeat that also stamps last_seen;
  - ``POST /api/agents/enroll``     — the installer runs on the jumpbox, so the
    request source IS the target.

Source is the socket peer (``request.client.host``). ``X-Forwarded-For`` is
honoured ONLY when that peer is a proxy the operator declared in
``CORTEXSIM_TRUSTED_PROXIES`` (comma-separated CIDRs, empty by default). It was
honoured from anyone: any host that could reach SimCore could stamp an
arbitrary address onto an agent row, and that address flows into the run's
``src_ip`` — the field detections are stitched on. NULLABLE, NEVER fabricated.

The FastAPI ``TestClient`` presents ``request.client.host == "testclient"`` by
default; tests that need a real peer address build a client with ``client=``.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

TRUSTED = "10.0.0.0/8, fd00::/8"


@pytest.fixture(autouse=True)
def _no_ambient_trusted_proxies(monkeypatch):
    """With the env var unset the reader falls back to `config.settings`, which
    was built from whatever `.env` sits in the developer's checkout. Pin that
    to the shipped default so "ignored by default" tests mean what they say."""
    import config

    monkeypatch.setattr(config.settings, "CORTEXSIM_TRUSTED_PROXIES", "")


@pytest.fixture
def client(make_client):
    from api.agents import router
    return make_client(router)


@pytest.fixture
def client_from(memory_db, monkeypatch):
    """Factory: a TestClient whose socket peer is ``peer``, with
    ``CORTEXSIM_TRUSTED_PROXIES`` set to ``trusted``."""
    get_db_override, _ = memory_db

    def _build(peer: str, trusted: str = "") -> TestClient:
        from api.agents import router
        from database import get_db

        monkeypatch.setenv("CORTEXSIM_TRUSTED_PROXIES", trusted)
        app = FastAPI()
        app.include_router(router, prefix="/api")
        app.dependency_overrides[get_db] = get_db_override
        return TestClient(app, client=(peer, 50000))

    return _build


def _agent(client, agent_id: str) -> dict:
    for a in client.get("/api/agents").json()["agents"]:
        if a["agent_id"] == agent_id:
            return a
    raise AssertionError(f"agent {agent_id!r} not listed")


def _register(client, agent_id: str, **kw):
    return client.post(
        "/api/agents/register",
        json={"agent_id": agent_id, "hostname": "lab", "os": "linux", "capabilities": ["bash"]},
        **kw)


# ---------------------------------------------------------------------------
# register — captures the request source on create and on update
# ---------------------------------------------------------------------------


def test_register_records_last_ip_from_client_host(client):
    r = _register(client, "a-1")
    assert r.status_code == 200
    assert _agent(client, "a-1")["last_ip"] == "testclient"


def test_a_spoofed_forwarded_for_from_an_untrusted_peer_is_ignored(client_from):
    """SEC-15: before this, any caller could set its own last_ip — and with it
    the run's stitched src_ip — by sending one header."""
    c = client_from("198.51.100.50")  # no trusted proxies configured (the default)
    r = _register(c, "a-spoof", headers={"X-Forwarded-For": "203.0.113.66"})
    assert r.status_code == 200
    assert _agent(c, "a-spoof")["last_ip"] == "198.51.100.50"


def test_forwarded_for_is_ignored_when_the_peer_is_outside_every_trusted_cidr(client_from):
    c = client_from("192.0.2.9", trusted=TRUSTED)
    _register(c, "a-out", headers={"X-Forwarded-For": "203.0.113.66"}).raise_for_status()
    assert _agent(c, "a-out")["last_ip"] == "192.0.2.9"


def test_register_through_a_trusted_proxy_records_the_real_client(client_from):
    # client -> proxy1 (10.0.0.1) -> proxy2 (10.0.0.2) -> SimCore, peer 10.0.0.3
    c = client_from("10.0.0.3", trusted=TRUSTED)
    r = _register(c, "a-xff", headers={"X-Forwarded-For": "198.51.100.7, 10.0.0.1, 10.0.0.2"})
    assert r.status_code == 200
    assert _agent(c, "a-xff")["last_ip"] == "198.51.100.7"


def test_a_client_cannot_prepend_a_fake_hop_past_a_trusted_proxy(client_from):
    """A proxy APPENDS the address it saw; whatever the client sent is to the
    left of it. Reading the first hop let the client pick its own address even
    through a trusted proxy — the right-most untrusted hop is the real one."""
    c = client_from("10.0.0.3", trusted=TRUSTED)
    _register(c, "a-prepend",
              headers={"X-Forwarded-For": "1.1.1.1, 198.51.100.7"}).raise_for_status()
    assert _agent(c, "a-prepend")["last_ip"] == "198.51.100.7"


def test_register_update_branch_refreshes_last_ip(client_from):
    c = client_from("10.0.0.3", trusted=TRUSTED)
    _register(c, "a-2", headers={"X-Forwarded-For": "203.0.113.1"}).raise_for_status()
    assert _agent(c, "a-2")["last_ip"] == "203.0.113.1"
    # Re-register from a different observed address updates the row.
    _register(c, "a-2", headers={"X-Forwarded-For": "203.0.113.99"}).raise_for_status()
    assert _agent(c, "a-2")["last_ip"] == "203.0.113.99"


# ---------------------------------------------------------------------------
# heartbeat (poll_tasks) — refreshes last_ip beside last_seen
# ---------------------------------------------------------------------------


def test_poll_records_last_ip(client_from):
    c = client_from("10.0.0.3", trusted=TRUSTED)
    _register(c, "a-3").raise_for_status()
    c.get("/api/agents/a-3/tasks", headers={"X-Forwarded-For": "192.0.2.55"}).raise_for_status()
    assert _agent(c, "a-3")["last_ip"] == "192.0.2.55"


def test_poll_ignores_a_spoofed_header_from_an_untrusted_peer(client_from):
    c = client_from("198.51.100.50")
    _register(c, "a-4").raise_for_status()
    c.get("/api/agents/a-4/tasks", headers={"X-Forwarded-For": "192.0.2.55"}).raise_for_status()
    assert _agent(c, "a-4")["last_ip"] == "198.51.100.50"


# ---------------------------------------------------------------------------
# enroll — the installer runs on the jumpbox, so the source IS the target
# ---------------------------------------------------------------------------


def _seed_token(session_factory) -> str:
    import asyncio
    from datetime import datetime, timedelta

    from models import EnrollmentToken

    async def _go() -> str:
        async with session_factory() as s:
            tok = EnrollmentToken(
                token="cxs_testtoken", label="t",
                created_at=datetime.utcnow(),
                expires_at=datetime.utcnow() + timedelta(hours=1),
                max_uses=5, used_count=0, revoked=False,
            )
            s.add(tok)
            await s.commit()
            return tok.token

    return asyncio.run(_go())


def test_enroll_records_last_ip(client_from, session_factory):
    c = client_from("10.0.0.3", trusted=TRUSTED)
    token = _seed_token(session_factory)
    r = c.post("/api/agents/enroll",
               json={"token": token, "hostname": "jumpbox", "os": "linux"},
               headers={"X-Forwarded-For": "198.51.100.200"})
    assert r.status_code == 200, r.text
    assert _agent(c, r.json()["agent_id"])["last_ip"] == "198.51.100.200"


def test_enroll_ignores_a_spoofed_header_from_an_untrusted_peer(client_from, session_factory):
    c = client_from("198.51.100.50")
    token = _seed_token(session_factory)
    r = c.post("/api/agents/enroll",
               json={"token": token, "hostname": "jumpbox", "os": "linux"},
               headers={"X-Forwarded-For": "203.0.113.66"})
    assert r.status_code == 200, r.text
    assert _agent(c, r.json()["agent_id"])["last_ip"] == "198.51.100.50"


# ---------------------------------------------------------------------------
# _client_ip helper — the honest extraction, never invents an address
# ---------------------------------------------------------------------------


class _Req:
    """Minimal request stand-in: headers dict + a client with .host."""

    def __init__(self, host=None, xff=None):
        self.headers = {"x-forwarded-for": xff} if xff else {}

        class _C:
            pass

        if host is None:
            self.client = None
        else:
            c = _C()
            c.host = host
            self.client = c


def test_client_ip_falls_back_to_socket_peer(monkeypatch):
    from api.agents import _client_ip
    monkeypatch.delenv("CORTEXSIM_TRUSTED_PROXIES", raising=False)
    assert _client_ip(_Req(host="10.0.0.9")) == "10.0.0.9"


def test_client_ip_ignores_forwarded_for_by_default(monkeypatch):
    from api.agents import _client_ip
    monkeypatch.delenv("CORTEXSIM_TRUSTED_PROXIES", raising=False)
    assert _client_ip(_Req(host="10.0.0.9", xff="1.2.3.4, 5.6.7.8")) == "10.0.0.9"


def test_client_ip_takes_the_rightmost_untrusted_hop_behind_a_trusted_proxy(monkeypatch):
    from api.agents import _client_ip
    monkeypatch.setenv("CORTEXSIM_TRUSTED_PROXIES", "10.0.0.0/8")
    assert _client_ip(_Req(host="10.0.0.9", xff="1.2.3.4, 5.6.7.8")) == "5.6.7.8"
    monkeypatch.setenv("CORTEXSIM_TRUSTED_PROXIES", "10.0.0.0/8,5.6.7.8/32")
    assert _client_ip(_Req(host="10.0.0.9", xff="1.2.3.4, 5.6.7.8")) == "1.2.3.4"


def test_client_ip_handles_ipv6_proxies_and_clients(monkeypatch):
    from api.agents import _client_ip
    monkeypatch.setenv("CORTEXSIM_TRUSTED_PROXIES", "fd00::/8")
    assert _client_ip(_Req(host="fd00::1", xff="2001:db8::7")) == "2001:db8::7"
    assert _client_ip(_Req(host="2001:db8::9", xff="2001:db8::7")) == "2001:db8::9"


def test_client_ip_never_stores_a_forwarded_value_that_is_not_an_address(monkeypatch):
    from api.agents import _client_ip
    monkeypatch.setenv("CORTEXSIM_TRUSTED_PROXIES", "10.0.0.0/8")
    assert _client_ip(_Req(host="10.0.0.9", xff="unknown")) is None
    assert _client_ip(_Req(host="10.0.0.9", xff="<script>")) is None


def test_a_malformed_trusted_proxy_list_degrades_to_no_observation(monkeypatch):
    """A typo'd CIDR list must not silently decide trust either way: trusting
    would let anyone spoof, distrusting would record the PROXY as the agent's
    address. Neither is an observation, so neither is stored."""
    from api.agents import _client_ip
    monkeypatch.setenv("CORTEXSIM_TRUSTED_PROXIES", "10.0.0.0/8;172.16.0.0/12")
    assert _client_ip(_Req(host="10.0.0.9", xff="198.51.100.7")) is None
    # With no forwarded header there is nothing to trust or distrust.
    assert _client_ip(_Req(host="10.0.0.9")) == "10.0.0.9"


def test_client_ip_none_when_no_client_and_no_header():
    from api.agents import _client_ip
    assert _client_ip(_Req(host=None)) is None


# ---------------------------------------------------------------------------
# The setting reaches the reader from a host-run .env too
# ---------------------------------------------------------------------------
#
# CORTEXSIM_TRUSTED_PROXIES was read from os.environ only and was not a field
# on Settings. A DC running SimCore on the host who put it in `.env` got a
# boot-time ValidationError (extra_forbidden); had it booted, the value would
# have been ignored without a word, and every agent behind the proxy would
# have recorded the proxy's address as its own.


def _settings_from_dotenv(tmp_path, monkeypatch, body: str):
    """A Settings built from a dotenv alone, installed as `config.settings`."""
    import os

    import config

    for name in [k for k in os.environ if k.startswith("CORTEXSIM_")]:
        monkeypatch.delenv(name, raising=False)
    dotenv = tmp_path / ".env"
    dotenv.write_text(body)
    built = config.Settings(_env_file=str(dotenv))
    monkeypatch.setattr(config, "settings", built)
    return built


def test_a_value_supplied_only_through_a_dotenv_is_honoured(tmp_path, monkeypatch):
    from api.agents import _client_ip

    built = _settings_from_dotenv(tmp_path, monkeypatch, "CORTEXSIM_TRUSTED_PROXIES=10.0.0.0/8\n")
    assert built.CORTEXSIM_TRUSTED_PROXIES == "10.0.0.0/8"
    # Behind the proxy named in .env the forwarded client is recorded...
    assert _client_ip(_Req(host="10.0.0.9", xff="198.51.100.7")) == "198.51.100.7"
    # ...and a peer outside it still cannot stamp an address.
    assert _client_ip(_Req(host="203.0.113.5", xff="198.51.100.7")) == "203.0.113.5"


def test_the_process_environment_outranks_the_dotenv(tmp_path, monkeypatch):
    """Environment first, then the Settings field: the order pydantic-settings
    itself uses. An exported empty value means "no proxy is trusted" and is not
    overridden by a stale .env line."""
    from api.agents import _client_ip

    _settings_from_dotenv(tmp_path, monkeypatch, "CORTEXSIM_TRUSTED_PROXIES=10.0.0.0/8\n")
    monkeypatch.setenv("CORTEXSIM_TRUSTED_PROXIES", "")
    assert _client_ip(_Req(host="10.0.0.9", xff="198.51.100.7")) == "10.0.0.9"
    monkeypatch.setenv("CORTEXSIM_TRUSTED_PROXIES", "192.0.2.0/24")
    assert _client_ip(_Req(host="192.0.2.10", xff="198.51.100.7")) == "198.51.100.7"
    assert _client_ip(_Req(host="10.0.0.9", xff="198.51.100.7")) == "10.0.0.9"


def test_a_malformed_dotenv_value_degrades_exactly_as_a_malformed_env_value(tmp_path, monkeypatch):
    from api.agents import _client_ip

    _settings_from_dotenv(tmp_path, monkeypatch, "CORTEXSIM_TRUSTED_PROXIES=10.0.0.0/8;172.16.0.0/12\n")
    assert _client_ip(_Req(host="10.0.0.9", xff="198.51.100.7")) is None

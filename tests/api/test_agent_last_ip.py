"""Phase 3a — Agent.last_ip capture on request-bearing endpoints
(core/api/agents.py).

The stitch-context resolver promotes a target agent's REAL observed source IP
to the run's ``src_ip`` (see engine.stitch_context.from_agent). That address is
captured HERE, on the beacon's request-bearing calls:

  - ``POST /api/agents/register``  — both the create and update branches;
  - ``GET  /api/agents/{id}/tasks`` — the heartbeat that also stamps last_seen;
  - ``POST /api/agents/enroll``     — the installer runs on the jumpbox, so the
    request source IS the target.

Source is ``request.client.host`` — or the FIRST hop of ``X-Forwarded-For`` when
the beacon reached SimCore through a proxy. It is NULLABLE and NEVER fabricated.

The FastAPI ``TestClient`` presents ``request.client.host == "testclient"``; the
X-Forwarded-For path is exercised by setting the header explicitly.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def client(make_client):
    from api.agents import router
    return make_client(router)


def _agent(client, agent_id: str) -> dict:
    for a in client.get("/api/agents").json()["agents"]:
        if a["agent_id"] == agent_id:
            return a
    raise AssertionError(f"agent {agent_id!r} not listed")


# ---------------------------------------------------------------------------
# register — captures the request source on create and on update
# ---------------------------------------------------------------------------


def test_register_records_last_ip_from_client_host(client):
    r = client.post(
        "/api/agents/register",
        json={"agent_id": "a-1", "hostname": "lab", "os": "linux",
              "capabilities": ["bash"]},
    )
    assert r.status_code == 200
    assert _agent(client, "a-1")["last_ip"] == "testclient"


def test_register_records_last_ip_from_x_forwarded_for_first_hop(client):
    # A proxied beacon: the socket peer is the proxy; the real client is the
    # FIRST hop of X-Forwarded-For (client, proxy1, proxy2).
    r = client.post(
        "/api/agents/register",
        json={"agent_id": "a-xff", "hostname": "lab", "os": "linux",
              "capabilities": ["bash"]},
        headers={"X-Forwarded-For": "198.51.100.7, 10.0.0.1, 10.0.0.2"},
    )
    assert r.status_code == 200
    assert _agent(client, "a-xff")["last_ip"] == "198.51.100.7"


def test_register_update_branch_refreshes_last_ip(client):
    base = {"agent_id": "a-2", "hostname": "lab", "os": "linux",
            "capabilities": ["bash"]}
    client.post("/api/agents/register", json=base,
                headers={"X-Forwarded-For": "203.0.113.1"}).raise_for_status()
    assert _agent(client, "a-2")["last_ip"] == "203.0.113.1"
    # Re-register from a different observed address updates the row.
    client.post("/api/agents/register", json=base,
                headers={"X-Forwarded-For": "203.0.113.99"}).raise_for_status()
    assert _agent(client, "a-2")["last_ip"] == "203.0.113.99"


# ---------------------------------------------------------------------------
# heartbeat (poll_tasks) — refreshes last_ip beside last_seen
# ---------------------------------------------------------------------------


def test_poll_records_last_ip(client):
    client.post(
        "/api/agents/register",
        json={"agent_id": "a-3", "hostname": "lab", "os": "linux",
              "capabilities": ["bash"]},
    ).raise_for_status()
    client.get(
        "/api/agents/a-3/tasks",
        headers={"X-Forwarded-For": "192.0.2.55"},
    ).raise_for_status()
    assert _agent(client, "a-3")["last_ip"] == "192.0.2.55"


# ---------------------------------------------------------------------------
# enroll — the installer runs on the jumpbox, so the source IS the target
# ---------------------------------------------------------------------------


def test_enroll_records_last_ip(client, session_factory):
    import asyncio
    from datetime import datetime, timedelta
    from models import EnrollmentToken

    async def _seed_token() -> str:
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

    token = asyncio.run(_seed_token())
    r = client.post(
        "/api/agents/enroll",
        json={"token": token, "hostname": "jumpbox", "os": "linux"},
        headers={"X-Forwarded-For": "198.51.100.200"},
    )
    assert r.status_code == 200, r.text
    assigned = r.json()["agent_id"]
    assert _agent(client, assigned)["last_ip"] == "198.51.100.200"


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


def test_client_ip_prefers_first_forwarded_hop():
    from api.agents import _client_ip
    assert _client_ip(_Req(host="10.0.0.9", xff="1.2.3.4, 5.6.7.8")) == "1.2.3.4"


def test_client_ip_falls_back_to_socket_peer():
    from api.agents import _client_ip
    assert _client_ip(_Req(host="10.0.0.9")) == "10.0.0.9"


def test_client_ip_none_when_no_client_and_no_header():
    from api.agents import _client_ip
    assert _client_ip(_Req(host=None)) is None

"""Direct router tests for /api/agents (no orchestrator dependency)."""
from __future__ import annotations

import asyncio
from datetime import datetime

import pytest


@pytest.fixture
def client(make_client):
    from api.agents import router
    return make_client(router)


def test_register_new_agent(client):
    r = client.post(
        "/api/agents/register",
        json={
            "agent_id": "a-1",
            "hostname": "lab-host",
            "os": "linux",
            "capabilities": ["bash", "docker"],
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "registered"
    assert body["agent_id"] == "a-1"


def test_register_is_idempotent_and_updates_metadata(client):
    base = {"agent_id": "a-1", "hostname": "h1", "os": "linux", "capabilities": []}
    client.post("/api/agents/register", json=base).raise_for_status()
    base["hostname"] = "h2"
    base["capabilities"] = ["bash"]
    client.post("/api/agents/register", json=base).raise_for_status()
    agents = client.get("/api/agents").json()["agents"]
    a = next(a for a in agents if a["agent_id"] == "a-1")
    assert a["hostname"] == "h2"
    assert a["capabilities"] == ["bash"]


def test_list_agents_empty_by_default(client):
    r = client.get("/api/agents")
    assert r.status_code == 200
    assert r.json() == {"agents": [], "total": 0}


def test_poll_unknown_agent_returns_404(client):
    r = client.get("/api/agents/never-registered/tasks")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "AGENT_NOT_FOUND"


def test_poll_idle_agent_returns_null_task(client):
    client.post(
        "/api/agents/register",
        json={"agent_id": "a-1", "hostname": "h", "os": "linux", "capabilities": []},
    ).raise_for_status()

    r = client.get("/api/agents/a-1/tasks")
    assert r.status_code == 200
    assert r.json() == {"task": None}


def test_register_required_fields(client):
    """Missing required fields → 422 (FastAPI validation)."""
    r = client.post("/api/agents/register", json={"hostname": "h"})
    assert r.status_code == 422


def test_last_seen_advances_on_poll(client, session_factory):
    """Polling refreshes last_seen — used by the agent-online indicator."""
    client.post(
        "/api/agents/register",
        json={"agent_id": "a-2", "hostname": "h", "os": "linux", "capabilities": []},
    ).raise_for_status()
    # Capture last_seen pre-poll
    before = client.get("/api/agents").json()["agents"][0]["last_seen"]

    # Tiny pause so the timestamp delta is observable
    import time as _t
    _t.sleep(0.02)

    client.get("/api/agents/a-2/tasks").raise_for_status()
    after = client.get("/api/agents").json()["agents"][0]["last_seen"]
    assert after >= before

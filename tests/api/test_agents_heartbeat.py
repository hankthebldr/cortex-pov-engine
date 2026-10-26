"""Tests for Phase 2 agent heartbeat staleness derivation (core/api/agents.py).

Status is derived from ``last_seen`` age at read time (not stored as the
source of truth):

    age < 30s        -> online
    30s <= age < 5m  -> stale
    age >= 5m        -> offline

Covered two ways:
  - the pure ``_derive_status`` helper across the threshold boundaries
    (explicit timestamps — no real sleeps);
  - the ``GET /api/agents`` endpoint, seeding agents with crafted ``last_seen``
    values and asserting the derived ``status`` + ``last_seen_age_seconds``
    override whatever is stored in the column.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest


# ---------------------------------------------------------------------------
# Pure helper: _derive_status
# ---------------------------------------------------------------------------


def test_derive_status_online_when_fresh():
    from api.agents import _derive_status

    now = datetime(2026, 6, 7, 12, 0, 0)
    last_seen = now - timedelta(seconds=5)
    status, age = _derive_status(last_seen, now)
    assert status == "online"
    assert age == pytest.approx(5.0)


def test_derive_status_stale_in_mid_window():
    from api.agents import _derive_status

    now = datetime(2026, 6, 7, 12, 0, 0)
    last_seen = now - timedelta(seconds=120)  # 2 minutes
    status, _ = _derive_status(last_seen, now)
    assert status == "stale"


def test_derive_status_offline_when_old():
    from api.agents import _derive_status

    now = datetime(2026, 6, 7, 12, 0, 0)
    last_seen = now - timedelta(seconds=600)  # 10 minutes
    status, _ = _derive_status(last_seen, now)
    assert status == "offline"


def test_derive_status_none_last_seen_is_offline():
    from api.agents import _derive_status

    status, age = _derive_status(None, datetime.utcnow())
    assert status == "offline"
    assert age >= 1e9


@pytest.mark.parametrize(
    "age_seconds,expected",
    [
        (0, "online"),
        (29.9, "online"),
        (30.0, "stale"),     # boundary: 30s is no longer online
        (299.9, "stale"),
        (300.0, "offline"),  # boundary: 5m is offline
        (10_000, "offline"),
    ],
)
def test_derive_status_boundaries(age_seconds, expected):
    from api.agents import _derive_status

    now = datetime(2026, 6, 7, 12, 0, 0)
    last_seen = now - timedelta(seconds=age_seconds)
    status, _ = _derive_status(last_seen, now)
    assert status == expected


# ---------------------------------------------------------------------------
# Endpoint: GET /api/agents derives status from last_seen
# ---------------------------------------------------------------------------


@pytest.fixture
def client(make_client):
    from api.agents import router
    return make_client(router)


def _seed_agent(session_factory, agent_id: str, age_seconds: float, stored_status: str):
    """Insert an agent whose last_seen is ``age_seconds`` in the past with a
    deliberately-wrong stored status to prove read-time derivation wins."""
    from models import Agent

    async def _seed():
        async with session_factory() as db:
            db.add(
                Agent(
                    agent_id=agent_id,
                    hostname="h",
                    os="linux",
                    capabilities=[],
                    status=stored_status,
                    last_seen=datetime.utcnow() - timedelta(seconds=age_seconds),
                )
            )
            await db.commit()

    asyncio.run(_seed())


def test_list_agents_derives_online_stale_offline(client, session_factory):
    # Seed three agents at different ages, each with a stale "online" column to
    # prove the endpoint recomputes rather than trusting the stored value.
    _seed_agent(session_factory, "fresh", age_seconds=5, stored_status="online")
    _seed_agent(session_factory, "mid", age_seconds=120, stored_status="online")
    _seed_agent(session_factory, "old", age_seconds=600, stored_status="online")

    agents = {a["agent_id"]: a for a in client.get("/api/agents").json()["agents"]}
    assert agents["fresh"]["status"] == "online"
    assert agents["mid"]["status"] == "stale"
    assert agents["old"]["status"] == "offline"


def test_list_agents_includes_last_seen_age_seconds(client, session_factory):
    _seed_agent(session_factory, "aged", age_seconds=42, stored_status="online")
    agents = client.get("/api/agents").json()["agents"]
    aged = next(a for a in agents if a["agent_id"] == "aged")
    assert "last_seen_age_seconds" in aged
    # ~42s old (allow scheduling slack).
    assert 40 <= aged["last_seen_age_seconds"] <= 60


def test_list_agents_read_time_overrides_stored_status(client, session_factory):
    """An agent whose column says 'online' but whose last_seen is ancient must
    surface as offline — the staleness gap that motivated Phase 2."""
    _seed_agent(session_factory, "zombie", age_seconds=3600, stored_status="online")
    agents = client.get("/api/agents").json()["agents"]
    zombie = next(a for a in agents if a["agent_id"] == "zombie")
    assert zombie["status"] == "offline"


# ---------------------------------------------------------------------------
# Sweep: an offline agent's delivered running run is terminalised
# ---------------------------------------------------------------------------


def _seed_running_run(session_factory, agent_id: str, started_ago: float):
    from models import Result, Run, StepOutcome

    async def _seed():
        async with session_factory() as db:
            t0 = datetime.utcnow() - timedelta(seconds=started_ago)
            db.add(Run(run_id="rl", scenario_id="SIM-X", mode="pull", target=agent_id,
                       status="running", open_tasks=1, started_at=t0, delivered_at=t0))
            db.add(Result(run_id="rl", step_id="step-01", plane="EDR", signal_type="BIOC",
                          expected_detection="d"))
            db.add(StepOutcome(run_id="rl", step_id="step-01", started_at=t0))
            await db.commit()

    asyncio.run(_seed())


def _run_status(session_factory):
    from models import Run
    from sqlalchemy import select

    async def _go():
        async with session_factory() as db:
            r = (await db.execute(select(Run).where(Run.run_id == "rl"))).scalar_one()
            return r.status, r.severed_at_step

    return asyncio.run(_go())


def test_sweep_invokes_terminalisation_for_offline_agents(session_factory, monkeypatch):
    import database
    from api.agents import sweep_agents_once

    monkeypatch.setattr(database, "AsyncSessionLocal", session_factory)
    _seed_agent(session_factory, "gone", age_seconds=900, stored_status="offline")
    _seed_agent(session_factory, "back", age_seconds=5, stored_status="online")
    _seed_running_run(session_factory, "gone", started_ago=800)

    asyncio.run(sweep_agents_once())
    assert _run_status(session_factory) == ("complete", "step-01")


def test_sweep_leaves_a_reconnected_agents_run_running(session_factory, monkeypatch):
    import database
    from api.agents import sweep_agents_once

    monkeypatch.setattr(database, "AsyncSessionLocal", session_factory)
    _seed_agent(session_factory, "back", age_seconds=5, stored_status="offline")
    _seed_running_run(session_factory, "back", started_ago=800)

    asyncio.run(sweep_agents_once())
    assert _run_status(session_factory) == ("running", None)

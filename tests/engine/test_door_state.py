"""Door state timeline: last conclusive event wins, unknown never changes it."""
from __future__ import annotations

import asyncio
import socket
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from engine import door_state
from models import DoorEvent

T0 = datetime(2026, 11, 1, 12, 0, 0)


def ev(state, at, basis="check", rung="verified", id=None, **kw):
    return DoorEvent(id=id, door_key="cloud_aws", door_id="cloud_aws", state=state,
                     basis=basis, rung=rung, at=at, evidence={}, **kw)


def test_effective_state_ignores_unknown():
    events = [ev("open", T0), ev("unknown", T0 + timedelta(hours=1))]
    state, e = door_state.effective_state(events, T0 + timedelta(hours=2))
    assert state == "open" and e is events[0]


def test_effective_state_before_any_event_unknown():
    assert door_state.effective_state([], T0) == ("unknown", None)
    assert door_state.effective_state([ev("open", T0)], T0 - timedelta(seconds=1)) == ("unknown", None)
    # only unknown events: still unknown, no event
    assert door_state.effective_state([ev("unknown", T0)], T0 + timedelta(days=1)) == ("unknown", None)


def test_attested_then_closed_check_closes():
    events = [ev("open", T0, basis="attested", rung="configured", attested_by="henry"),
              ev("closed", T0 + timedelta(hours=1))]
    state, e = door_state.effective_state(events, T0 + timedelta(hours=2))
    assert state == "closed" and e.basis == "check"
    # and at an earlier instant the attestation still stands
    state, e = door_state.effective_state(events, T0 + timedelta(minutes=30))
    assert state == "open" and e.basis == "attested"


def test_closed_then_unknown_stays_closed():
    events = [ev("closed", T0), ev("unknown", T0 + timedelta(hours=1))]
    assert door_state.effective_state(events, T0 + timedelta(days=2))[0] == "closed"


def test_no_expiry_open_stays_open_after_30_days():
    events = [ev("open", T0)]
    assert door_state.effective_state(events, T0 + timedelta(days=30))[0] == "open"


def test_events_in_any_order_ties_break_by_id():
    a, b = ev("open", T0, id=1), ev("closed", T0, id=2)
    assert door_state.effective_state([b, a], T0)[0] == "closed"
    assert door_state.effective_state([a, b], T0)[0] == "closed"


def test_naive_utc_only():
    with pytest.raises(ValueError):
        door_state.effective_state([ev("open", T0)], datetime(2026, 11, 2, tzinfo=timezone.utc))


def test_unrecognised_state_raises():
    with pytest.raises(ValueError):
        door_state.effective_state([ev("maybe", T0)], T0)


# ---- DB-backed ------------------------------------------------------------

def _fresh_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:",
                                 connect_args={"check_same_thread": False},
                                 poolclass=StaticPool)

    async def _init():
        from database import Base
        import models  # noqa: F401
        import security  # noqa: F401
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    return async_sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def Session():
    return _fresh_session()


def test_record_and_current(Session):
    async def go():
        async with Session() as db:
            await door_state.record(db, door_key="cloud_aws", door_id="cloud_aws",
                                    state="open", basis="attested", rung="configured",
                                    evidence={"n": 1}, attested_by="henry",
                                    at=datetime.utcnow() - timedelta(days=40))
            await door_state.record(db, door_key="cloud_aws", door_id="cloud_aws",
                                    state="unknown", basis="check", rung="configured",
                                    evidence={}, queries_issued=1)
            return await door_state.current(db)

    cur = asyncio.run(go())["cloud_aws"]
    assert cur["state"] == "open" and cur["basis"] == "attested"
    assert cur["age_seconds"] > 39 * 86400
    assert cur["last_event_at"] > cur["last_conclusive_at"]


def test_record_rejects_bad_shapes(Session):
    async def go(**over):
        async with Session() as db:
            kw = dict(door_key="k", door_id="cloud_aws", state="open", basis="check",
                      rung="verified", evidence={})
            kw.update(over)
            await door_state.record(db, **kw)

    for bad in ({"state": "ajar"}, {"basis": "vibes"}, {"rung": "sky"},
                {"at": datetime(2026, 1, 1, tzinfo=timezone.utc)}):
        with pytest.raises(ValueError):
            asyncio.run(go(**bad))


def test_configured_facts_zero_outbound(Session, monkeypatch):
    from models import Agent, CollectorTargetConfig
    from security import CredentialStore

    def boom(*a, **k):
        raise AssertionError("outbound call attempted")

    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket, "create_connection", boom)

    async def go(seed):
        async with Session() as db:
            if seed:
                store = CredentialStore(db)
                await store.put_integration(name="t", kind="xsiam_tenant",
                                            plaintext_secret="x", config={"capabilities": ["xql"]})
                await store.put_integration(name="fw", kind="panos_ngfw",
                                            plaintext_secret="x", config={})
                db.add(CollectorTargetConfig(name="lab", kind="xsiam_http_collector",
                                             url="https://example.invalid/logs"))
                now = datetime.utcnow()
                for aid, seen in (("on", now), ("off", now - timedelta(days=3))):
                    db.add(Agent(agent_id=aid, hostname=aid, os="linux", last_seen=seen))
                await db.commit()
            return await door_state.configured_facts(db)

    empty = asyncio.run(go(False))
    assert empty["cloud_aws"] == "authored" and empty["ngfw_traffic"] == "authored"
    assert not any(k.startswith("http_collector:") for k in empty)

    f = asyncio.run(go(True))
    assert f["cloud_aws"] == f["saas_okta"] == f["windows_events"] == "configured"
    assert f["syslog_cisco_asa"] == f["proofpoint_tap"] == "configured"
    assert f["ngfw_traffic"] == f["ngfw_globalprotect"] == "configured"
    assert f["http_collector:lab"] == "configured"
    assert f["xdr_agent:on"] == f["xdr_agent_xth:on"] == f["ngfw_stitch:on"] == "configured"
    assert f["xdr_agent:off"] == f["ngfw_stitch:off"] == "authored"


def test_online_beacon_alone_does_not_configure_agent_doors():
    """An online beacon is the CortexSim beacon, not the Cortex XDR agent (spec §1 defect 1):
    the agent doors read configured only with an XQL tenant credential to check them with,
    and ngfw_stitch also needs a registered firewall."""
    from models import Agent
    from security import CredentialStore

    async def go(Session, *, tenant_caps, firewall):
        async with Session() as db:
            store = CredentialStore(db)
            if tenant_caps is not None:
                await store.put_integration(name="t", kind="xsiam_tenant",
                                            plaintext_secret="x", config={"capabilities": tenant_caps})
            if firewall:
                await store.put_integration(name="fw", kind="panos_ngfw",
                                            plaintext_secret="x", config={})
            db.add(Agent(agent_id="on", hostname="on", os="linux", last_seen=datetime.utcnow()))
            await db.commit()
            return await door_state.configured_facts(db)

    agent_doors = ("xdr_agent:on", "xdr_agent_xth:on", "ngfw_stitch:on")
    beacon_only = asyncio.run(go(_fresh_session(), tenant_caps=None, firewall=True))
    assert {beacon_only[k] for k in agent_doors} == {"authored"}

    no_xql = asyncio.run(go(_fresh_session(), tenant_caps=[], firewall=True))
    assert {no_xql[k] for k in agent_doors} == {"authored"}

    no_fw = asyncio.run(go(_fresh_session(), tenant_caps=["xql"], firewall=False))
    assert no_fw["xdr_agent:on"] == no_fw["xdr_agent_xth:on"] == "configured"
    assert no_fw["ngfw_stitch:on"] == "authored"


def test_tenant_without_xql_stays_authored(Session):
    from security import CredentialStore

    async def go():
        async with Session() as db:
            await CredentialStore(db).put_integration(
                name="t", kind="xsiam_tenant", plaintext_secret="x", config={"capabilities": []})
            return await door_state.configured_facts(db)

    assert asyncio.run(go())["cloud_aws"] == "authored"

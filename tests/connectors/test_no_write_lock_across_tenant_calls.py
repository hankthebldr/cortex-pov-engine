"""No write transaction is held open across an outbound call (#326).

SQLite allows one writer. Decrypting a credential stamps
``secrets.last_accessed_at``, which opens a write transaction on the caller's
session, and every caller then went straight to the network with it open:
Tier-2 verify across its XQL calls, reconcile across the alert, incident and
case reads, the preflights across their probes. For as long as the tenant took
to answer, every other writer waited out the busy timeout and failed
``database is locked``. On 2026-11-14 that was the beacon's ``GET /tasks`` and
the heartbeat sweep, 44 times in one minute, during a verify of four queries.

Each test here stands inside the outbound call (the injected transport) and
makes the write a beacon's poll makes, on its own connection, against a real
SQLite FILE: an in-memory ``StaticPool`` engine shares one connection and
cannot show a lock at all.
"""
from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

import httpx
import pytest
from sqlalchemy import select

from tests.connectors.test_reconcile_case_ledger import ACC, _alerts, _case, _tenant
from tests.connectors.test_reconcile_queries_issued import _reconcile, _setup
from tests.connectors.test_verification_service import _seed

#: How long these connections wait on the write lock before SQLite gives up.
#: Short, so a held lock fails a test in a second and not in five.
BUSY_SECONDS = 1.0
LONG_AGO = datetime.utcnow() - timedelta(hours=1)
TENANT_URL = "https://api-t.xdr.us.paloaltonetworks.com"


@pytest.fixture
def file_db(tmp_path):
    """``(path, sessionmaker)`` over a SQLite file: one connection per session,
    as the production engine opens them."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    path = tmp_path / "cortexsim.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{path}",
        connect_args={"check_same_thread": False, "timeout": BUSY_SECONDS})
    SessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def _init() -> None:
        from database import Base
        import models  # noqa: F401

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    yield path, SessionLocal
    asyncio.run(engine.dispose())


@pytest.fixture(autouse=True)
def _clean_orchestrator():
    from engine.orchestrator import orchestrator

    def _reset():
        orchestrator._aborted.clear()
        orchestrator._queue.clear()
        orchestrator.reset_liveness()

    _reset()
    yield
    _reset()


def beacon_poll_write(path) -> Optional[str]:
    """The write a beacon's poll makes (``agents.last_seen``), on its own
    connection. ``None`` when it landed, else SQLite's own error text."""
    conn = sqlite3.connect(str(path), timeout=BUSY_SECONDS)
    try:
        conn.execute("UPDATE agents SET last_seen = ? WHERE agent_id = 'agent-1'",
                     (datetime.utcnow().isoformat(sep=" "),))
        conn.commit()
        return None
    except sqlite3.OperationalError as exc:
        return str(exc)
    finally:
        conn.close()


def _add_agent(SessionLocal) -> None:
    from models import Agent

    async def _go():
        async with SessionLocal() as db:
            db.add(Agent(agent_id="agent-1", hostname="web-01", os="linux",
                         capabilities=["shell"], interpreters=[], status="offline",
                         registered_at=LONG_AGO, last_seen=LONG_AGO))
            await db.commit()

    asyncio.run(_go())


def _client(SessionLocal, *routers):
    """A TestClient over the file database, with the real routers mounted."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from database import get_db

    app = FastAPI()
    for router in routers:
        app.include_router(router, prefix="/api")

    async def _get_db():
        async with SessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = _get_db
    return TestClient(app), app


# ---------------------------------------------------------------------------
# Tier-2 verify: the path the issue names
# ---------------------------------------------------------------------------


def test_a_slow_tenant_call_inside_verify_does_not_lock_out_the_beacon_poll(
        file_db, monkeypatch):
    """The 2026-11-14 minute, in one process: a verify is waiting on the tenant
    and the beacon polls. The poll's write must land, and the verify must still
    finish and store what it measured."""
    import engine.verifier as verifier_mod
    from api.agents import router as agents_router
    from connectors.service import verify_run_now
    from database import get_db
    from fastapi import FastAPI
    from models import Agent, Result, Run, Secret
    from security.credentials import CredentialStore

    path, SessionLocal = file_db
    _seed(SessionLocal, verification_xql="dataset = xdr_data | limit 1", results=1)
    _add_agent(SessionLocal)

    app = FastAPI()
    app.include_router(agents_router, prefix="/api")

    async def _get_db():
        async with SessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = _get_db

    async def _go():
        in_tenant, answer = asyncio.Event(), asyncio.Event()

        def slow_tenant(client, timeframe):
            async def _run(query: str) -> int:
                in_tenant.set()
                await answer.wait()  # the tenant is taking its time
                return 3
            return _run

        # Only the network is replaced: the credential is resolved and
        # decrypted by the real resolver, which is where the lock came from.
        monkeypatch.setattr(verifier_mod, "xsiam_query_runner", slow_tenant)

        async with SessionLocal() as db:
            await CredentialStore(db).put_integration(
                name="lab", kind="xsiam_tenant", plaintext_secret="k" * 40,
                config={"base_url": TENANT_URL, "region": "us", "api_key_id": "1",
                        "capabilities": ["xql"]})
            await db.commit()

        async with SessionLocal() as db:
            run = (await db.execute(select(Run))).scalar_one()
            verify = asyncio.create_task(verify_run_now(db, run))
            await asyncio.wait_for(in_tenant.wait(), 10)
            try:
                # The tenant has not answered yet. A beacon polls now.
                locked = await asyncio.to_thread(beacon_poll_write, path)
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                             base_url="http://simcore") as beacon:
                    poll = await beacon.get("/api/agents/agent-1/tasks")
            finally:
                answer.set()
            outcome = await asyncio.wait_for(verify, 10)

        async with SessionLocal() as db:
            agent = (await db.execute(select(Agent))).scalar_one()
            result = (await db.execute(select(Result))).scalar_one()
            secret = (await db.execute(select(Secret))).scalar_one()
        return locked, poll, outcome, agent, result, secret

    locked, poll, outcome, agent, result, secret = asyncio.run(_go())

    assert locked is None, f"a write during the tenant call failed: {locked}"
    assert poll.status_code == 200 and poll.json() == {"task": None}
    # The poll's own liveness stamp landed; it was not skipped around a lock.
    assert agent.status == "online" and agent.last_seen > LONG_AGO + timedelta(minutes=30)
    # The verify finished and stored what the tenant answered.
    assert outcome.queries_issued == 1 and outcome.tenant == "lab"
    assert result.kpi_verdict == "pass" and result.verified_at is not None
    # The credential's access stamp was kept, not rolled back to free the lock.
    assert secret.last_accessed_at is not None


# ---------------------------------------------------------------------------
# Reconcile, the case-ledger refresh: synchronous tenant reads
# ---------------------------------------------------------------------------


def _probing(inner, path, seen: list):
    """``inner`` fetcher, making the beacon poll's write before every request."""

    def fetcher(method, url, headers, body, timeout):
        seen.append(beacon_poll_write(path))
        return inner(method, url, headers, body, timeout)

    return fetcher


def test_reconcile_holds_no_write_lock_while_it_reads_the_tenant(file_db):
    """Three tenant reads in one reconcile: the alerts, then (after the
    credential is marked verified) the cases and the issue statuses."""
    path, SessionLocal = file_db
    _seed(SessionLocal, results=2, threshold=ACC, primary_kpi="Detection Accuracy")
    asyncio.run(_setup(SessionLocal, techniques=("T1003.001", "T1059")))
    inner, calls = _tenant(_alerts(), [_case()])
    seen: list = []

    summary, run = asyncio.run(_reconcile(SessionLocal, _probing(inner, path, seen)))

    assert calls == {"alerts": 1, "cases": 1, "issues": 1}
    assert seen == [None, None, None], seen
    # And the reconcile did its work.
    assert summary["observed"] == 2
    assert run.case_ledger["merge_class"] == "ONE_CASE"


def test_a_case_ledger_refresh_holds_no_write_lock_while_it_reads_the_cases(file_db):
    from connectors.service import refresh_case_ledger
    from models import Run

    path, SessionLocal = file_db
    _seed(SessionLocal, results=2, threshold=ACC, primary_kpi="Detection Accuracy")
    asyncio.run(_setup(SessionLocal, techniques=("T1003.001", "T1059")))
    inner, _ = _tenant(_alerts(), [_case()])
    asyncio.run(_reconcile(SessionLocal, inner))
    seen: list = []

    async def _refresh():
        async with SessionLocal() as db:
            run = (await db.execute(select(Run))).scalars().first()
            return await refresh_case_ledger(
                db, run, fetcher=_probing(inner, path, seen), force=True)

    out = asyncio.run(_refresh())

    assert out["refreshed"] is True and out["queries_issued"] == 1
    assert seen == [None], seen


def test_a_tenant_evidence_read_holds_no_write_lock_while_it_reads(file_db):
    """Its connector calls run in a worker thread, so SimCore's loop is free
    to serve the beacon during them: the case where a held lock bites."""
    import copy

    from tests.connectors.tenant_fakes import FakeTenant
    from tests.connectors.test_tenant_evidence_service import ISSUES, read, seed

    path, SessionLocal = file_db
    seed(SessionLocal)
    fake = FakeTenant(issues=copy.deepcopy(ISSUES))
    seen: list = []

    out = read(SessionLocal, _probing(fake, path, seen))

    assert out["read"] is True, out
    assert seen and set(seen) == {None}, seen


# ---------------------------------------------------------------------------
# The preflights
# ---------------------------------------------------------------------------


def test_a_connector_preflight_holds_no_write_lock_while_it_probes(file_db, monkeypatch):
    import connectors.base as base
    from api.connectors import router as connectors_router
    from api.credentials import router as credentials_router
    from connectors.base import ConnectorError

    path, SessionLocal = file_db
    _add_agent(SessionLocal)
    client, _ = _client(SessionLocal, credentials_router, connectors_router)
    put = client.put("/api/credentials/integrations", json={
        "name": "acme", "kind": "xsiam", "plaintext_secret": "k" * 40,
        "config": {"fqdn": "api-t.xdr.us.paloaltonetworks.com", "api_key_id": "1"}})
    assert put.status_code == 200, put.text
    seen: list = []

    def unreachable(*_a, **_kw):
        seen.append(beacon_poll_write(path))
        raise ConnectorError("transport error reaching the tenant: no route")

    monkeypatch.setattr(base, "default_http_fetcher", unreachable)

    report = client.post("/api/connectors/xsiam/preflight")

    assert report.status_code == 200 and report.json()["overall"] == "blocked"
    assert seen and set(seen) == {None}, seen


def test_a_firewall_preflight_holds_no_write_lock_while_it_calls_the_firewall(
        file_db, monkeypatch):
    import api.ngfw as ngfw
    from api.connectors import router as connectors_router
    from api.credentials import router as credentials_router

    path, SessionLocal = file_db
    _add_agent(SessionLocal)
    client, _ = _client(SessionLocal, credentials_router, ngfw.router, connectors_router)
    put = client.put("/api/credentials/integrations", json={
        "name": "fw-a", "kind": "panos_ngfw", "plaintext_secret": "K" * 40,
        "config": {"host": "192.0.2.1"}})
    assert put.status_code == 200, put.text
    seen: list = []

    def firewall(request: httpx.Request) -> httpx.Response:
        seen.append(beacon_poll_write(path))
        return httpx.Response(500, content=b"unmapped")

    monkeypatch.setattr(ngfw, "_transport_override", httpx.MockTransport(firewall))

    report = client.post("/api/connectors/panos_ngfw/preflight")

    assert report.status_code == 200, report.text
    assert seen and set(seen) == {None}, seen


def test_a_collector_preflight_holds_no_write_lock_while_it_sends(file_db):
    """The collector key is decrypted only on a path that sends, so the send
    is where its access stamp used to keep the database locked."""
    from api import collector_targets as ct_api

    path, SessionLocal = file_db
    _add_agent(SessionLocal)
    client, app = _client(SessionLocal, ct_api.router)
    put = client.put("/api/collector-targets/okta-hec", json={
        "kind": "xsiam_http_collector", "url": "https://api-acme.example.test/logs/v1/event",
        "dataset": "okta_sso_raw", "plugins": ["idp_signin_emulator"],
        "token": "xsiam-collector-key-41c0"})
    assert put.status_code == 200, put.text
    seen: list = []

    def factory(timeout, verify_tls):
        def collector(request: httpx.Request) -> httpx.Response:
            seen.append(beacon_poll_write(path))
            return httpx.Response(202)
        return httpx.AsyncClient(transport=httpx.MockTransport(collector))

    async def connect(*_a):
        seen.append(beacon_poll_write(path))

    app.dependency_overrides[ct_api.preflight_transports] = lambda: (connect, factory)

    out = client.post("/api/collector-targets/okta-hec/preflight",
                      json={"write_canary": True})

    assert out.status_code == 200 and out.json()["verdict"] == "accepted", out.text
    assert len(seen) >= 2 and set(seen) == {None}, seen

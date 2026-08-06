"""Fixtures for the connectors/measurement-service tests.

Mirrors ``tests/api/conftest.py``'s isolated in-memory DB, minus the router
plumbing — these tests drive the service layer directly.
"""
from __future__ import annotations

import asyncio
from typing import AsyncIterator, Callable

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


@pytest.fixture
def memory_db() -> tuple[Callable[[], AsyncIterator[AsyncSession]], async_sessionmaker]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def _init() -> None:
        from database import Base
        import models  # noqa: F401 — register tables on Base.metadata

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())

    async def _get_db() -> AsyncIterator[AsyncSession]:
        async with SessionLocal() as session:
            yield session

    return _get_db, SessionLocal


@pytest.fixture
def session_factory(memory_db) -> async_sessionmaker:
    _, SessionLocal = memory_db
    return SessionLocal


@pytest.fixture(autouse=True)
def neutral_uctc_registry(monkeypatch):
    """Pin the UC/TC index singleton to an EMPTY registry for these tests.

    ``verifier.tc_scoreable_for`` degrades open with no snapshot loaded, so a
    verdict here is a function of the run's own data and nothing else. Without
    this the outcome depends on whether some *earlier* test in the session
    happened to load the global singleton: in a full-suite run it is loaded,
    and the fixture's ``TC-EDR-01`` is one of the 123 qualitative index rows,
    so every ``pass`` would silently clamp to ``not_applicable``.

    The clamp itself is not skipped — it has its own explicit test that loads
    the real snapshot and asserts the downgrade.
    """
    from engine.uctc_registry import UcTcRegistry  # noqa: PLC0415

    monkeypatch.setattr("engine.uctc_registry.registry", UcTcRegistry())


@pytest.fixture(autouse=True)
def reset_tenant_quota_ledger():
    """Clear the process-wide XQL quota ledger between tests.

    ``integrations.xsiam.ledger.ledger`` is a module singleton by design — it is
    the cross-CALL budget, so a per-call reset would defeat it. That also makes
    it the one piece of connector state that survives a test, and a breaker
    tripped by the quota test would silently zero out every later test's query
    count. Reset here rather than weakening the singleton.
    """
    from integrations.xsiam.ledger import ledger

    ledger.reset()
    yield
    ledger.reset()

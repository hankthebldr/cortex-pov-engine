"""Shared API-test fixtures: isolated in-memory DB with router-level apps."""
from __future__ import annotations

import asyncio
from typing import AsyncIterator, Callable

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


@pytest.fixture
def memory_db() -> tuple[Callable[[], AsyncIterator[AsyncSession]], async_sessionmaker]:
    """Spin up an in-memory SQLite + return (get_db_override, SessionLocal)."""
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

    asyncio.get_event_loop().run_until_complete(_init())

    async def _get_db() -> AsyncIterator[AsyncSession]:
        async with SessionLocal() as session:
            yield session

    return _get_db, SessionLocal


@pytest.fixture
def make_client(memory_db):
    """Factory: build a TestClient with the given router(s) mounted + DB wired."""
    get_db_override, SessionLocal = memory_db

    def _build(*routers, prefix: str = "/api") -> TestClient:
        from database import get_db
        app = FastAPI()
        for r in routers:
            app.include_router(r, prefix=prefix)
        app.dependency_overrides[get_db] = get_db_override
        return TestClient(app)

    return _build


@pytest.fixture
def session_factory(memory_db) -> async_sessionmaker:
    """Direct DB access for seeding rows in tests."""
    _, SessionLocal = memory_db
    return SessionLocal

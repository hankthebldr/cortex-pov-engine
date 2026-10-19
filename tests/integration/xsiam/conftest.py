# tests/integration/xsiam/conftest.py
"""Fixtures for XSIAM integration tests: isolated SQLite + tenant seeding."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
import pytest_asyncio

from tests._core_reimport import reimport_core

os.environ.setdefault("CORTEXSIM_SECRET", "test-master-key-please-ignore-32+chars-entropy")
os.environ.setdefault("CORTEXSIM_ENV", "development")


@pytest_asyncio.fixture
async def db_session(tmp_path: Path, monkeypatch, restore_core_modules):
    db_path = tmp_path / "xsiam-test.db"
    monkeypatch.setenv("CORTEXSIM_BASE_DIR", str(tmp_path))
    reimport_core("database", "models", "config", prefixes=("security", "integrations"))

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    from database import Base  # noqa: PLC0415
    import models  # noqa: F401, PLC0415
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as session:
        yield session
    await engine.dispose()


async def seed_tenant(session, *, name, base_url, api_key,
                      region="us", auth_mode="standard", api_key_id="1"):
    """Insert an xsiam_tenant IntegrationCredential the way the generic CRUD would."""
    from security import CredentialStore
    from integrations.xsiam.loader import XSIAM_KIND
    store = CredentialStore(session)
    await store.put_integration(
        name=name, kind=XSIAM_KIND, plaintext_secret=api_key,
        config={"base_url": base_url, "region": region,
                "auth_mode": auth_mode, "api_key_id": api_key_id},
    )
    await session.commit()

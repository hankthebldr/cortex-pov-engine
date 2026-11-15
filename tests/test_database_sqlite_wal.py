"""The run-history database opens in WAL with a busy timeout (#326).

``core/database.py`` built its engine with SQLite's defaults: a rollback
journal, where a reader blocks a writer's commit and a committing writer blocks
every reader. Under a Tier-2 verify the beacon's poll and the heartbeat sweep
threw ``database is locked`` 44 times in one minute.

These tests drive the PRODUCTION module: ``database`` is re-imported against a
throwaway ``CORTEXSIM_BASE_DIR`` and booted through its own ``init_db()``, so
what is asserted is what a deployed SimCore opens, on a fresh file and on one
an older release left behind.
"""
from __future__ import annotations

import asyncio
import sqlite3

import pytest
from sqlalchemy import text

from tests._core_reimport import reimport_core


@pytest.fixture
def prod_db(tmp_path, monkeypatch, restore_core_modules):
    """``(database module, path of its cortexsim.db)`` under ``tmp_path``.

    The file does not exist yet: a test that wants an existing database writes
    one at the returned path BEFORE calling ``init_db()``.
    """
    monkeypatch.setenv("CORTEXSIM_BASE_DIR", str(tmp_path))
    reimport_core("database", "models", "config")
    import database  # noqa: PLC0415

    path = tmp_path / "data" / "cortexsim.db"
    assert database.db_path() == str(path)
    yield database, path
    asyncio.run(database.engine.dispose())


def _pragmas(database) -> dict:
    """What a connection handed out by the production engine is set to."""

    async def _read():
        async with database.engine.connect() as conn:
            return {name: (await conn.execute(text(f"PRAGMA {name}"))).scalar()
                    for name in ("journal_mode", "busy_timeout", "synchronous")}

    return asyncio.run(_read())


def test_a_fresh_database_opens_in_wal_with_a_busy_timeout(prod_db):
    database, path = prod_db
    assert not path.exists()

    asyncio.run(database.init_db())

    assert path.exists()
    assert _pragmas(database) == {
        "journal_mode": "wal",
        "busy_timeout": 5000,
        "synchronous": 1,  # NORMAL
    }


def test_an_existing_database_is_switched_to_wal_and_keeps_its_rows(prod_db):
    """The upgrade path: a file an older release created, rollback journal and
    all, with run history in it."""
    database, path = prod_db
    old = sqlite3.connect(str(path))
    assert old.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    old.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY, run_id VARCHAR, status VARCHAR)")
    old.execute("INSERT INTO runs (run_id, status) VALUES ('5cc8c6b4', 'complete')")
    old.commit()
    old.close()

    asyncio.run(database.init_db())

    assert _pragmas(database) == {"journal_mode": "wal", "busy_timeout": 5000,
                                  "synchronous": 1}
    # The mode is stored in the file, so any other reader of it sees WAL too,
    # and the history is where it was.
    after = sqlite3.connect(str(path))
    try:
        assert after.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert after.execute("SELECT run_id, status FROM runs").fetchall() == [
            ("5cc8c6b4", "complete")]
    finally:
        after.close()


def test_every_connection_gets_the_settings_not_only_the_first(prod_db):
    """The engine opens a new connection per session, and ``busy_timeout`` and
    ``synchronous`` are per-connection settings SQLite does not store."""
    database, _ = prod_db
    asyncio.run(database.init_db())

    async def _two_sessions():
        out = []
        for _ in range(2):
            async with database.AsyncSessionLocal() as db:
                out.append({
                    name: (await db.execute(text(f"PRAGMA {name}"))).scalar()
                    for name in ("journal_mode", "busy_timeout", "synchronous")})
        return out

    first, second = asyncio.run(_two_sessions())
    assert first == second == {"journal_mode": "wal", "busy_timeout": 5000,
                               "synchronous": 1}


def test_a_reader_no_longer_blocks_a_write(prod_db):
    """Under a rollback journal a connection that is still reading holds a
    shared lock, and a writer's commit waits on it until the busy timeout and
    then fails ``database is locked``. In WAL the write lands at once."""
    database, path = prod_db
    asyncio.run(database.init_db())

    async def _seed_and_write():
        from models import Agent  # noqa: PLC0415 - the re-imported module

        async with database.AsyncSessionLocal() as db:
            db.add(Agent(agent_id="agent-1", hostname="h1", os="linux",
                         capabilities=[], interpreters=[], status="offline"))
            await db.commit()

        reader = sqlite3.connect(str(path), isolation_level=None)
        reader.execute("BEGIN")
        assert reader.execute("SELECT status FROM agents").fetchall() == [("offline",)]
        try:
            # The beacon poll's write, while the reader is still open.
            async with database.AsyncSessionLocal() as db:
                await db.execute(text(
                    "UPDATE agents SET status = 'online' WHERE agent_id = 'agent-1'"))
                await db.commit()
        finally:
            reader.execute("ROLLBACK")
            reader.close()

        async with database.AsyncSessionLocal() as db:
            return (await db.execute(text("SELECT status FROM agents"))).scalar()

    assert asyncio.run(_seed_and_write()) == "online"


def test_an_in_memory_engine_takes_the_settings_without_harm():
    """The test suites build in-memory engines. SQLite has no WAL for one and
    answers ``memory``; nothing raises and the engine works."""
    import database  # noqa: PLC0415
    from sqlalchemy.ext.asyncio import create_async_engine  # noqa: PLC0415
    from sqlalchemy.pool import StaticPool  # noqa: PLC0415

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool)
    database.configure_sqlite(engine)

    async def _use():
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE t (x INTEGER)"))
            await conn.execute(text("INSERT INTO t VALUES (1)"))
        async with engine.connect() as conn:
            mode = (await conn.execute(text("PRAGMA journal_mode"))).scalar()
            busy = (await conn.execute(text("PRAGMA busy_timeout"))).scalar()
            rows = (await conn.execute(text("SELECT x FROM t"))).scalars().all()
        await engine.dispose()
        return mode, busy, rows

    assert asyncio.run(_use()) == ("memory", 5000, [1])


def test_a_file_that_is_not_a_database_still_gets_its_named_boot_error(prod_db):
    """The settings run on connect, before ``init_db`` creates anything. A
    corrupt file must still surface as ``DB_CORRUPT``, not as a traceback from
    the connect hook."""
    database, path = prod_db
    path.write_text("version https://git-lfs.github.com/spec/v1\noid sha256:deadbeef\n")

    with pytest.raises(database.DatabaseBootError) as caught:
        asyncio.run(database.init_db())

    assert caught.value.code == "DB_CORRUPT"

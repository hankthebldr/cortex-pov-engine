"""Boot must fail with a fix, not a traceback.

`config.validate_master_key` is the model in this repo: a named condition, a
plain-English cause, and a copy-pasteable remediation. Two equally likely
deployment faults had nothing — observed on a live prod image:

    data/cortexsim.db is not a SQLite file
      → sqlalchemy.exc.DatabaseError: (sqlite3.DatabaseError) file is not a
        database + 30 lines of traceback, exit 3

    data/ mounted read-only
      → sqlalchemy.exc.OperationalError: (sqlite3.OperationalError) unable to
        open database file + traceback, exit 3

Both look like a CortexSim defect to the DC who hit them. They are deployment
faults with one-line fixes. Exit 4 distinguishes "the storage is wrong" from the
master-key guard's 3 ("the secret is wrong") so an operator scripting the boot
can branch.

These tests drive the REAL sqlite3 conditions — a genuinely corrupt file and a
genuinely read-only directory — not a mocked exception, because the whole point
is that the classifier keys on the message sqlite actually emits.
"""
from __future__ import annotations

import os
import sqlite3
import stat

import pytest


# ---------------------------------------------------------------------------
# The classifier, against real sqlite3 errors
# ---------------------------------------------------------------------------


def _real_error(path: str) -> BaseException:
    """Provoke a genuine sqlite3 error by opening `path` and writing to it."""
    try:
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE IF NOT EXISTS probe (x INTEGER)")
        conn.commit()
        conn.close()
    except Exception as exc:  # noqa: BLE001
        return exc
    raise AssertionError(f"expected {path} to raise, but it opened cleanly")


def test_corrupt_db_is_named_and_remediated(tmp_path):
    import database

    bad = tmp_path / "cortexsim.db"
    # A Git LFS pointer is the real-world shape of this: a small text file
    # committed where the database should be.
    bad.write_text(
        "version https://git-lfs.github.com/spec/v1\noid sha256:deadbeef\nsize 4096\n"
    )
    exc = _real_error(str(bad))
    assert "file is not a database" in str(exc).lower()

    err = database.classify_db_error(exc)
    assert err.code == "DB_CORRUPT"
    text = str(err)
    assert "CORRUPT DATABASE" in text
    assert "mv " in text and ".bad" in text, "must give the exact recovery command"
    # It must say what is actually lost, or the operator will not run the fix.
    assert "run history" in text.lower()
    assert "Traceback" not in text


def test_readonly_data_dir_is_named_and_remediated(tmp_path):
    import database

    if os.getuid() == 0:
        pytest.skip("root ignores directory permissions; run as a normal uid")

    ro_dir = tmp_path / "data"
    ro_dir.mkdir()
    os.chmod(ro_dir, stat.S_IRUSR | stat.S_IXUSR)
    try:
        exc = _real_error(str(ro_dir / "cortexsim.db"))
        assert "unable to open database file" in str(exc).lower()

        err = database.classify_db_error(exc)
        assert err.code == "DB_NOT_WRITABLE"
        text = str(err)
        assert "NOT WRITABLE" in text
        assert ":ro" in text, "Docker is the most common cause; name the mount flag"
        assert "chown" in text
        assert "Traceback" not in text
    finally:
        os.chmod(ro_dir, stat.S_IRWXU)


def test_disk_full_is_named():
    import database

    err = database.classify_db_error(
        sqlite3.OperationalError("database or disk is full")
    )
    assert err.code == "DB_DISK_FULL"
    assert "df -h" in str(err)


def test_an_unrecognised_fault_is_still_legible():
    """A new failure mode must not fall back to a bare traceback — it gets a
    code, the original text, and an honest 'no dedicated remediation yet'."""
    import database

    err = database.classify_db_error(sqlite3.OperationalError("no such collation"))
    assert err.code == "DB_INIT_FAILED"
    assert "no such collation" in str(err)
    assert "report it" in str(err)


def test_boot_exit_code_is_distinct_from_the_master_key_guard():
    import database

    assert database.DB_BOOT_EXIT_CODE == 4


# ---------------------------------------------------------------------------
# init_db translates rather than leaking
# ---------------------------------------------------------------------------


def test_init_db_raises_the_named_error_not_the_raw_sqlalchemy_one(monkeypatch):
    import asyncio

    import database

    async def _boom():
        raise sqlite3.DatabaseError("file is not a database")

    monkeypatch.setattr(database, "_init_db_inner", _boom)
    with pytest.raises(database.DatabaseBootError) as caught:
        asyncio.run(database.init_db())
    assert caught.value.code == "DB_CORRUPT"


def test_init_db_does_not_double_wrap(monkeypatch):
    import asyncio

    import database

    async def _boom():
        raise database.DatabaseBootError("DB_CORRUPT", "already classified")

    monkeypatch.setattr(database, "_init_db_inner", _boom)
    with pytest.raises(database.DatabaseBootError) as caught:
        asyncio.run(database.init_db())
    assert str(caught.value) == "already classified"


# ---------------------------------------------------------------------------
# SQL echo — the boot log a DC actually reads
# ---------------------------------------------------------------------------


def test_sql_echo_is_off_unless_explicitly_opted_in():
    """Statement echo used to be tied to CORTEXSIM_ENV=development, which
    printed every aiosqlite statement TWICE (engine echo + the sqlalchemy.engine
    logger). A DC reading the boot log for one real error scrolled past
    hundreds of duplicated lines."""
    from config import Settings

    assert Settings().CORTEXSIM_SQL_ECHO is False


def test_dev_env_alone_does_not_enable_statement_logging(monkeypatch):
    import importlib
    import logging

    import config
    import main

    monkeypatch.setattr(config.settings, "CORTEXSIM_ENV", "development")
    monkeypatch.setattr(config.settings, "CORTEXSIM_SQL_ECHO", False)
    main._configure_logging()
    assert logging.getLogger("sqlalchemy.engine").level == logging.WARNING

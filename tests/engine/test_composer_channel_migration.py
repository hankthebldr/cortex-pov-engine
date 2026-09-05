"""Idempotent ADD COLUMN migration for the Phase-3a composer channel columns.

``create_all`` never adds a COLUMN to an existing table, so a dev box carrying a
pre-3a ``cortexsim.db`` would SELECT-fail on ``agents.last_ip`` (and
``runs.channel_dispatch``) unless ``_migrate_composer_channel_columns`` adds
them. This walks the exact upgrade path a legacy DB takes — an ``agents``/``runs``
pair WITHOUT the new columns, the migration run once (adds them) then a second
time (a no-op) — and proves a legacy agent row reads ``last_ip`` back as NULL,
i.e. an agent that never had a captured source IP is honestly null, never
fabricated.

Scope: this file's unit (``agent-last-ip``) owns ``agents.last_ip``; the
migration also adds ``runs.channel_dispatch`` (the channel-dispatch unit's
column) in the same idempotent place, so both are asserted here.
"""

from __future__ import annotations

from sqlalchemy import create_engine, inspect, text

from database import _migrate_composer_channel_columns


def _legacy_engine(tmp_path):
    """A file-backed sqlite DB shaped like a pre-3a CortexSim DB: the two tables
    exist but lack the composer channel columns."""
    eng = create_engine(f"sqlite:///{tmp_path/'legacy.db'}")
    with eng.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE agents ("
                "id INTEGER PRIMARY KEY, agent_id VARCHAR, hostname VARCHAR, "
                "os VARCHAR)"
            )
        )
        conn.execute(
            text("CREATE TABLE runs (run_id VARCHAR PRIMARY KEY, tc_verdict VARCHAR)")
        )
    return eng


def test_migration_adds_channel_columns_and_is_idempotent(tmp_path):
    eng = _legacy_engine(tmp_path)

    with eng.begin() as conn:
        assert "last_ip" not in {c["name"] for c in inspect(conn).get_columns("agents")}
        assert "channel_dispatch" not in {
            c["name"] for c in inspect(conn).get_columns("runs")
        }

    with eng.begin() as conn:
        _migrate_composer_channel_columns(conn)
    with eng.begin() as conn:
        assert "last_ip" in {c["name"] for c in inspect(conn).get_columns("agents")}
        assert "channel_dispatch" in {
            c["name"] for c in inspect(conn).get_columns("runs")
        }

    # Second run is a no-op — must not raise a duplicate-column error.
    with eng.begin() as conn:
        _migrate_composer_channel_columns(conn)


def test_legacy_agent_row_reads_last_ip_null(tmp_path):
    eng = _legacy_engine(tmp_path)
    with eng.begin() as conn:
        _migrate_composer_channel_columns(conn)
    with eng.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO agents (agent_id, hostname, os) "
                "VALUES ('a-legacy', 'h', 'linux')"
            )
        )
    with eng.begin() as conn:
        row = conn.execute(
            text("SELECT last_ip FROM agents WHERE agent_id='a-legacy'")
        ).one()
        assert row[0] is None

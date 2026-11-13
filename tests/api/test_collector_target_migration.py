"""The additive startup migration for collector_targets' declaration / last_arrival."""
from __future__ import annotations

from sqlalchemy import create_engine, inspect, text

OLD_DDL = """
CREATE TABLE collector_targets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name VARCHAR NOT NULL UNIQUE,
    kind VARCHAR NOT NULL,
    url VARCHAR NOT NULL,
    dataset VARCHAR,
    auth_header VARCHAR NOT NULL,
    auth_scheme VARCHAR NOT NULL,
    plugins JSON NOT NULL,
    is_default BOOLEAN NOT NULL,
    secret_name VARCHAR,
    last_check JSON,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
)
"""


def test_migration_adds_the_columns_to_an_old_table(tmp_path):
    from database import _migrate_collector_target_columns

    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as conn:
        conn.execute(text(OLD_DDL))
        conn.execute(text(
            "INSERT INTO collector_targets (name, kind, url, auth_header, auth_scheme,"
            " plugins, is_default, created_at, updated_at) VALUES"
            " ('t', 'xsiam_http_collector', 'https://x.test', 'Authorization', '', '[]', 0,"
            " '2026-01-01', '2026-01-01')"))
        _migrate_collector_target_columns(conn)
        cols = {c["name"] for c in inspect(conn).get_columns("collector_targets")}
        assert {"declaration", "last_arrival"} <= cols
        # A second run is a no-op, and the old row reads NULL.
        _migrate_collector_target_columns(conn)
        row = conn.execute(text("SELECT declaration, last_arrival FROM collector_targets")).one()
    assert tuple(row) == (None, None)
    engine.dispose()


def test_migration_skips_a_missing_table(tmp_path):
    from database import _migrate_collector_target_columns

    engine = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    with engine.begin() as conn:
        _migrate_collector_target_columns(conn)
        assert "collector_targets" not in inspect(conn).get_table_names()
    engine.dispose()

"""Idempotent ADD COLUMN migration for the Phase-2 stitch columns.

``create_all`` never adds a COLUMN to a table that already exists, so a dev box
carrying an older ``cortexsim.db`` (one written before Phase 2) would SELECT-fail
on ``scenarios.stitch_context`` / ``runs.stitch_binding`` unless
``_migrate_scenarios_columns`` adds them. This walks the exact upgrade path a
legacy DB takes — a ``scenarios``/``runs`` pair WITHOUT the new columns, the
migration run once (adds them) then a second time (a no-op) — and proves a row
inserted with no stitch context reads back NULL, i.e. context-less scenarios
load byte-identically to today.
"""

from __future__ import annotations

from sqlalchemy import create_engine, inspect, text

from database import _migrate_scenarios_columns


def _legacy_engine(tmp_path):
    """A file-backed sqlite DB shaped like a pre-Phase-2 CortexSim DB: the two
    tables exist but lack the stitch columns."""
    eng = create_engine(f"sqlite:///{tmp_path/'legacy.db'}")
    with eng.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE scenarios ("
                "scenario_id VARCHAR PRIMARY KEY, name VARCHAR, cgo_anchor JSON)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE runs ("
                "run_id VARCHAR PRIMARY KEY, tc_verdict VARCHAR)"
            )
        )
    return eng


def test_migration_adds_stitch_columns_and_is_idempotent(tmp_path):
    eng = _legacy_engine(tmp_path)

    # Precondition: the legacy DB lacks both columns.
    with eng.begin() as conn:
        assert "stitch_context" not in {
            c["name"] for c in inspect(conn).get_columns("scenarios")
        }
        assert "stitch_binding" not in {
            c["name"] for c in inspect(conn).get_columns("runs")
        }

    # First run adds them.
    with eng.begin() as conn:
        _migrate_scenarios_columns(conn)
    with eng.begin() as conn:
        assert "stitch_context" in {
            c["name"] for c in inspect(conn).get_columns("scenarios")
        }
        assert "stitch_binding" in {
            c["name"] for c in inspect(conn).get_columns("runs")
        }

    # Second run is a no-op (guarded by `if col in existing: continue`) — it must
    # not raise a duplicate-column error.
    with eng.begin() as conn:
        _migrate_scenarios_columns(conn)


def test_context_less_row_reads_back_null_after_migration(tmp_path):
    eng = _legacy_engine(tmp_path)
    with eng.begin() as conn:
        _migrate_scenarios_columns(conn)
    # Insert a scenario the pre-Phase-2 way — no stitch_context supplied.
    with eng.begin() as conn:
        conn.execute(
            text("INSERT INTO scenarios (scenario_id, name) VALUES ('SIM-X-1', 'x')")
        )
    with eng.begin() as conn:
        row = conn.execute(
            text("SELECT stitch_context FROM scenarios WHERE scenario_id='SIM-X-1'")
        ).one()
        assert row[0] is None

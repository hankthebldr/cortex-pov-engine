"""``scenarios.push_supported_declared`` — what the author DECLARED, kept apart
from what the engine DERIVED.

``Scenario.push_supported`` answers "may a push bundle be offered". For a corpus
row that is the YAML's own value; for a Composer draft it is derived from the
draft's command text, narrowed by a declared refusal. A draft forked from a
refusing origin must keep refusing across every later PUT, while a draft whose
derivation merely said ``false`` once must be allowed to recover once its steps
are fixed — so the row has to remember WHICH of the two its ``false`` was. This
column is that memory. ``create_all`` never adds a column to an existing table,
so a box carrying an older ``cortexsim.db`` gets it from
``_migrate_scenarios_columns`` on boot, like ``indicators`` before it.
"""
from __future__ import annotations

from pathlib import Path

import yaml
from sqlalchemy import create_engine, inspect, text

FIXTURE = (
    Path(__file__).resolve().parent.parent / "fixtures" / "scenarios" / "sim-test-prev-001.yml"
)


def _columns(conn) -> set[str]:
    return {c["name"] for c in inspect(conn).get_columns("scenarios")}


def test_migration_adds_push_supported_declared_and_is_idempotent(tmp_path):
    from database import _migrate_scenarios_columns  # noqa: PLC0415

    eng = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with eng.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE scenarios ("
                "scenario_id VARCHAR PRIMARY KEY, push_supported BOOLEAN)"
            )
        )
        assert "push_supported_declared" not in _columns(conn)

    with eng.begin() as conn:
        _migrate_scenarios_columns(conn)
    with eng.begin() as conn:
        assert "push_supported_declared" in _columns(conn)

    # Second run is a no-op — it must not raise a duplicate-column error.
    with eng.begin() as conn:
        _migrate_scenarios_columns(conn)

    # A row written before the column existed reads back NULL: "not declared",
    # never a fabricated true or false.
    with eng.begin() as conn:
        conn.execute(
            text("INSERT INTO scenarios (scenario_id, push_supported) VALUES ('SIM-X-1', 1)")
        )
        row = conn.execute(
            text("SELECT push_supported_declared FROM scenarios WHERE scenario_id='SIM-X-1'")
        ).one()
    assert row[0] is None


def test_orm_carries_push_supported_declared_in_to_dict():
    from models import Scenario  # noqa: PLC0415

    declared = Scenario(push_supported=False, push_supported_declared=False).to_dict()
    assert declared["push_supported_declared"] is False
    # Nothing declared stays NULL — it does not inherit push_supported.
    assert Scenario(push_supported=True).to_dict()["push_supported_declared"] is None


def test_loader_records_the_yaml_value_as_declared():
    """A corpus scenario's ``push_supported`` IS its declaration, both ways
    round, so the console can read ONE field for a corpus origin and a draft."""
    from engine.scenario_loader import ScenarioSchema, _schema_to_orm_kwargs  # noqa: PLC0415
    from tests._composer_fork import load_corpus_doc  # noqa: PLC0415

    assert load_corpus_doc("SIM-MP-031")["push_supported_declared"] is False

    fixture = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    assert fixture["push_supported"] is True, "fixture no longer declares push_supported: true"
    assert _schema_to_orm_kwargs(ScenarioSchema(**fixture))["push_supported_declared"] is True

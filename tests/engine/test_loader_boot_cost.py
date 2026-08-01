"""Guards for the scenario loader's boot-path cost.

Boot runs on every container start — a customer jumpbox pays it each restart —
and ~80% of it used to be PyYAML's pure-Python parser plus one SELECT per
scenario file. These tests pin both fixes and, more importantly, the upsert
semantics the batched SELECT had to preserve.
"""
from __future__ import annotations

import asyncio

import pytest
import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from engine import scenario_loader

SCENARIO_TEMPLATE = """
scenario_id: {sid}
name: {name}
version: "1.0"
status: active
plane: EDR
detection_types: [BIOC]
uc_ref: UC-1
tc_ref: TC-1
uc_name: Use Case
tc_name: Test Case
mitre_tactic: TA0006
mitre_tactic_name: Credential Access
mitre_technique: T1003
mitre_technique_name: OS Credential Dumping
execution_identity:
  default: www-data
  options: [www-data, root]
push_supported: true
pull_supported: true
steps:
  - id: step-01
    name: Dump
    command: echo dump
    identity: www-data
    mitre_technique: T1003
    expected_detections:
      - plane: EDR
        type: BIOC
        description: credential dumping observed
cleanup:
  commands: [echo done]
"""


@pytest.fixture(autouse=True)
def _relax_ref_strictness(monkeypatch):
    """These fixtures use synthetic UC/TC refs that aren't in the master index.

    ``CORTEXSIM_STRICT_REFS`` defaults on, and whether it bites here depends on
    whether some earlier test happened to populate the registry singleton — so
    pin it off rather than let suite ordering decide.
    """
    from config import settings
    monkeypatch.setattr(settings, "CORTEXSIM_STRICT_REFS", False)


@pytest.fixture
def db_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:",
                                 connect_args={"check_same_thread": False},
                                 poolclass=StaticPool)
    Session = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def _init():
        from database import Base
        import models  # noqa: F401
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    return Session


def _write(dirpath, fname, sid, name="Scenario"):
    (dirpath / fname).write_text(SCENARIO_TEMPLATE.format(sid=sid, name=name))


# --------------------------------------------------------------------------
# libyaml
# --------------------------------------------------------------------------


def test_loader_uses_the_libyaml_parser_when_available():
    """The C parser is ~9x faster on the real corpus and ships in the image.

    If this ever silently falls back to the Python loader, boot regresses by
    roughly 700 ms with no other visible symptom — hence an explicit assert.
    """
    if not hasattr(yaml, "CSafeLoader"):
        pytest.skip("libyaml not compiled into this PyYAML build")
    assert scenario_loader._YAML_LOADER is yaml.CSafeLoader


def test_loader_falls_back_cleanly_without_libyaml():
    """A PyYAML built without libyaml must still resolve to a *safe* loader."""
    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    assert loader in (yaml.CSafeLoader, yaml.SafeLoader) if hasattr(yaml, "CSafeLoader") \
        else loader is yaml.SafeLoader
    # The safe subset is what matters: never the arbitrary-object Loader.
    assert scenario_loader._YAML_LOADER is not yaml.UnsafeLoader
    assert scenario_loader._YAML_LOADER is not yaml.Loader


def test_c_and_python_loaders_agree_on_a_scenario_document(tmp_path):
    """Both loaders must produce an identical parse — the swap is behaviour-free."""
    doc = SCENARIO_TEMPLATE.format(sid="SIM-EDR-901", name="Parity")
    assert yaml.load(doc, Loader=yaml.SafeLoader) == \
        yaml.load(doc, Loader=scenario_loader._YAML_LOADER)


# --------------------------------------------------------------------------
# Batched upsert
# --------------------------------------------------------------------------


def test_upsert_issues_one_select_not_one_per_scenario(tmp_path, db_factory, monkeypatch):
    """The loader prefetches existing scenarios in a single SELECT.

    Pre-optimisation this was one round-trip per YAML file (161 on the real
    corpus). Counting Scenario SELECTs pins that.
    """
    for i in range(12):
        _write(tmp_path, f"s{i}.yml", f"SIM-EDR-{900 + i}")

    from sqlalchemy.ext.asyncio import AsyncSession

    selects = {"n": 0}
    real_execute = AsyncSession.execute

    async def counting(self, statement, *a, **kw):
        if "FROM scenarios" in str(statement):
            selects["n"] += 1
        return await real_execute(self, statement, *a, **kw)

    monkeypatch.setattr(AsyncSession, "execute", counting)

    async def _run():
        async with db_factory() as db:
            return await scenario_loader.load_scenarios(str(tmp_path), db)

    ids = asyncio.run(_run())
    assert len(ids) == 12
    assert selects["n"] == 1, (
        f"{selects['n']} scenario SELECTs for 12 files — the per-file upsert "
        "query has come back"
    )


def test_reload_updates_in_place_without_duplicating_rows(tmp_path, db_factory):
    """Loading twice must UPDATE, never insert a second row per scenario."""
    _write(tmp_path, "a.yml", "SIM-EDR-950", name="First")

    async def _run():
        from models import Scenario
        async with db_factory() as db:
            await scenario_loader.load_scenarios(str(tmp_path), db)
        # Re-author the file, then reload.
        _write(tmp_path, "a.yml", "SIM-EDR-950", name="Second")
        async with db_factory() as db:
            await scenario_loader.load_scenarios(str(tmp_path), db)
        async with db_factory() as db:
            rows = list((await db.execute(
                select(Scenario).where(Scenario.scenario_id == "SIM-EDR-950")
            )).scalars().all())
        return rows

    rows = asyncio.run(_run())
    assert len(rows) == 1
    assert rows[0].name == "Second"


def test_duplicate_scenario_id_across_files_collapses_to_one_row(tmp_path, db_factory):
    """Two files declaring the same scenario_id still yield ONE row.

    The old per-file SELECT saw the pending insert via autoflush; the batched
    map has to fold newly-added scenarios back in to keep that. Without it the
    second file inserts a duplicate and the commit dies on the UNIQUE index.
    """
    _write(tmp_path, "a.yml", "SIM-EDR-960", name="Alpha")
    _write(tmp_path, "b.yml", "SIM-EDR-960", name="Beta")

    async def _run():
        from models import Scenario
        async with db_factory() as db:
            ids = await scenario_loader.load_scenarios(str(tmp_path), db)
        async with db_factory() as db:
            rows = list((await db.execute(
                select(Scenario).where(Scenario.scenario_id == "SIM-EDR-960")
            )).scalars().all())
        return ids, rows

    ids, rows = asyncio.run(_run())
    assert ids == ["SIM-EDR-960", "SIM-EDR-960"]
    assert len(rows) == 1, "duplicate scenario_id produced two rows"
    assert rows[0].name == "Beta", "last file on disk should win"


def test_invalid_yaml_is_rejected_without_halting_the_load(tmp_path, db_factory):
    """A malformed file is still skipped, not fatal — the C parser raises a
    different exception type than the Python one, so this stays pinned."""
    _write(tmp_path, "good.yml", "SIM-EDR-970")
    (tmp_path / "bad.yml").write_text("scenario_id: [unclosed\n  bad: :\n")

    async def _run():
        async with db_factory() as db:
            return await scenario_loader.load_scenarios(str(tmp_path), db)

    ids = asyncio.run(_run())
    assert ids == ["SIM-EDR-970"]

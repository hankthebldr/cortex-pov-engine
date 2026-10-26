"""Schema for the prevention-efficacy contract: Run launch context, Result
outcome axis, step_outcomes (host evidence) and observations (tenant evidence)."""
from __future__ import annotations

import asyncio
from datetime import datetime

import pytest
from sqlalchemy.exc import IntegrityError


def test_run_and_result_carry_outcome_fields(session_factory):
    from models import Observation, Result, Run, StepOutcome

    async def _go():
        async with session_factory() as s:
            run = Run(run_id="r1", scenario_id="SIM-X", mode="pull",
                      agent_profile_mode="block", soakless_attested=True)
            res = Result(run_id="r1", step_id="step-02", plane="EDR", signal_type="BIOC",
                         expected_detection="d", expected_outcome="prevent")
            so = StepOutcome(run_id="r1", step_id="step-02",
                             termination="anchor_terminated", signal=9)
            ob = Observation(run_id="r1", source="xsiam", external_id="a1",
                             observed_at=datetime(2026, 1, 1),
                             action_pretty="Prevented (Blocked)",
                             alert_outcome="prevented", raw={"x": 1})
            s.add_all([run, res, so, ob])
            await s.commit()
            return run.to_dict(), res.to_dict(), so.to_dict(), ob.to_dict()

    run_d, res_d, so_d, ob_d = asyncio.run(_go())
    assert run_d["agent_profile_mode"] == "block"
    assert run_d["soakless_attested"] is True
    for k in ("bas_mode_attested", "attested_by", "attested_at", "paired_with_run_id",
              "canary_run_id", "severed_at_step", "block_mode_status"):
        assert k in run_d and run_d[k] is None
    assert res_d["expected_outcome"] == "prevent" and res_d["outcome"] is None
    for k in ("prevention_module", "outcome_reason", "alert_action", "alert_outcome"):
        assert k in res_d and res_d[k] is None
    assert so_d["termination"] == "anchor_terminated" and so_d["signal"] == 9
    assert so_d["exit_code"] is None and so_d["recorded_at"]
    assert ob_d["raw"] == {"x": 1}
    assert ob_d["observed_at"] == "2026-01-01T00:00:00"
    assert ob_d["result_id"] is None


def test_defaults(session_factory):
    from models import Result, Run

    async def _go():
        async with session_factory() as s:
            run = Run(run_id="r2", scenario_id="SIM-X", mode="pull")
            res = Result(run_id="r2", plane="EDR", signal_type="BIOC", expected_detection="d")
            s.add_all([run, res])
            await s.commit()
            return run.agent_profile_mode, res.expected_outcome

    assert asyncio.run(_go()) == ("unknown", "detect")


def test_duplicate_step_outcome_is_refused(session_factory):
    from models import StepOutcome

    async def _go():
        async with session_factory() as s:
            s.add(StepOutcome(run_id="r3", step_id="step-02"))
            await s.commit()
        async with session_factory() as s:
            s.add(StepOutcome(run_id="r3", step_id="step-02"))
            await s.commit()

    with pytest.raises(IntegrityError):
        asyncio.run(_go())


def test_migration_adds_columns_and_is_idempotent():
    from sqlalchemy import create_engine, inspect, text
    from database import _migrate_prevention_columns

    eng = create_engine("sqlite://")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE runs (id INTEGER PRIMARY KEY, run_id VARCHAR)"))
        c.execute(text("CREATE TABLE results (id INTEGER PRIMARY KEY, run_id VARCHAR)"))
        c.execute(text("INSERT INTO runs (run_id) VALUES ('old')"))
        c.execute(text("INSERT INTO results (run_id) VALUES ('old')"))
        _migrate_prevention_columns(c)
        _migrate_prevention_columns(c)
        run_cols = {x["name"] for x in inspect(c).get_columns("runs")}
        res_cols = {x["name"] for x in inspect(c).get_columns("results")}
        assert {"agent_profile_mode", "bas_mode_attested", "soakless_attested", "attested_by",
                "attested_at", "paired_with_run_id", "canary_run_id", "severed_at_step",
                "block_mode_status"} <= run_cols
        assert {"expected_outcome", "prevention_module", "outcome", "outcome_reason",
                "alert_action", "alert_outcome"} <= res_cols
        assert c.execute(text("SELECT agent_profile_mode FROM runs")).scalar() == "unknown"
        assert c.execute(text("SELECT expected_outcome FROM results")).scalar() == "detect"


def test_migrated_legacy_db_gets_index_and_new_tables():
    from sqlalchemy import create_engine, inspect, text
    from database import Base, _migrate_prevention_columns
    import models  # noqa: F401

    eng = create_engine("sqlite://")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE runs (id INTEGER PRIMARY KEY, run_id VARCHAR)"))
        c.execute(text("CREATE TABLE results (id INTEGER PRIMARY KEY, run_id VARCHAR)"))
        Base.metadata.create_all(c)  # creates only the missing tables
        for _ in range(2):
            _migrate_prevention_columns(c)
            insp = inspect(c)
            assert "ix_runs_paired_with_run_id" in {i["name"] for i in insp.get_indexes("runs")}
        assert {"step_outcomes", "observations"} <= set(inspect(c).get_table_names())

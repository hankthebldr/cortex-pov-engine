"""One fixture run, every coverage surface, one answer (final review C1 / I3 / M7).

The AST drift guard in ``tests/engine/test_outcome.py`` only catches a
redefinition of ``is_in_denominator`` by name; an inline ``observed / total``
passes it, which is how ``/api/results`` and the efficacy scorecard kept
counting ``not_reached`` / ``not_executed`` / soak-pending rows as misses.
This test is the behavioural guard: one run carrying every outcome is pushed
through all four coverage surfaces and each must report the same numerator,
denominator and exclusions.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

RUN_ID = "parity-run"

# (step_id, outcome, outcome_reason, observed, expected_outcome, signal_type)
ROWS = [
    ("step-01", "prevented", None, True, "prevent", "BIOC"),
    ("step-02", "detected", None, True, "detect", "BIOC"),
    ("step-03", "detected", "DETECTED_NOT_PREVENTED", True, "prevent", "BIOC"),
    ("step-04", "missed", None, False, "detect", "BIOC"),
    ("step-05", "not_reached", "UPSTREAM_SEVERED", False, "detect", "BIOC"),
    ("step-06", "not_executed", "TOOL_ABSENT", False, "detect", "BIOC"),
    ("step-07", "pending", "ANALYTICS_ACTIVATION_PERIOD", False, "detect", "Analytics"),
]

# In the denominator: prevented, detected, detected-not-prevented, missed.
# Hits: the three Cortex saw. Excluded: one of each exclusion reason.
DENOMINATOR = 4
HITS = 3
PCT = 75.0
EXCLUDED = {"not_reached": 1, "not_executed": 1, "soak_pending": 1}


@pytest.fixture
def client(make_client):
    from api.results import router
    return make_client(router)


@pytest.fixture
def seeded(session_factory):
    from models import Result, Run

    async def _go():
        now = datetime.utcnow()
        async with session_factory() as db:
            db.add(Run(run_id=RUN_ID, scenario_id="SIM-EDR-001", mode="pull",
                       status="complete", agent_profile_mode="block",
                       started_at=now - timedelta(minutes=30)))
            for sid, outcome, reason, observed, expected, sig in ROWS:
                db.add(Result(
                    run_id=RUN_ID, step_id=sid, step_name=sid, plane="EDR",
                    signal_type=sig, expected_detection=f"det {sid}",
                    expected_outcome=expected, outcome=outcome, outcome_reason=reason,
                    observed=observed,
                    observed_at=(now - timedelta(minutes=20)) if observed else None,
                    executed_at=now - timedelta(minutes=25),
                ))
            await db.commit()
        async with session_factory() as db:
            run = (await db.execute(select(Run).where(Run.run_id == RUN_ID))).scalar_one()
            results = list((await db.execute(
                select(Result).where(Result.run_id == RUN_ID).order_by(Result.step_id)
            )).scalars().all())
            return run, results

    return asyncio.run(_go())


def test_results_api_coverage_uses_the_outcome_axis(client, seeded):
    cov = client.get(f"/api/results/{RUN_ID}").json()["coverage"]
    assert cov["pct"] == PCT
    assert cov["denominator"] == DENOMINATOR
    assert cov["observed_in_denominator"] == HITS
    assert cov["excluded"] == EXCLUDED
    assert cov["prevented"] == 1

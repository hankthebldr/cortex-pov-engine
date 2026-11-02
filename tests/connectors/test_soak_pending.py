"""Task 10 — a silent Analytics/ABIOC detector is not a miss.

Analytics detectors carry an activation/test period on a fresh tenant and
suppress a repeat of the same alert inside their dedup window. Silence in
either case says nothing about whether the detector works, so the row stays
``pending`` with the reason — and it leaves the accuracy denominator.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest

from tests.connectors.test_measurement_service import _setup, _tenant

pytestmark = pytest.mark.usefixtures("non_utc_host")


@pytest.fixture(autouse=True)
def short_analytics_window(monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "CORTEXSIM_RECONCILE_WINDOW_ANALYTICS", 3600)


def _seed_analytics(SessionLocal, *, run_id, detection_id="anl-1", observed=False,
                    hours_ago=3, soakless=None, paired_with=None, with_scenario=True):
    from models import Result, Run, Scenario

    executed = datetime.utcnow() - timedelta(hours=hours_ago)

    async def _do():
        async with SessionLocal() as db:
            if with_scenario:
                db.add(Scenario(
                    scenario_id="SIM-EDR-001", name="X", plane="EDR", version="1.0",
                    status="active", uc_ref="UCS-EDR-01", uc_name="X", tc_ref="TC-EDR-01",
                    tc_name="Y", mitre_tactic="TA0006", mitre_tactic_name="Credential Access",
                    mitre_technique="T1003.001", mitre_technique_name="LSASS Memory",
                    steps=[]))
            db.add(Run(run_id=run_id, scenario_id="SIM-EDR-001", mode="pull",
                       status="complete", started_at=executed, completed_at=datetime.utcnow(),
                       soakless_attested=soakless, paired_with_run_id=paired_with))
            db.add(Result(run_id=run_id, step_id="step-01", plane="EDR",
                          signal_type="Analytics", expected_detection="analytics detection",
                          detection_id=detection_id, observed=observed,
                          executed_at=executed,
                          observed_at=executed + timedelta(seconds=60) if observed else None))
            await db.commit()

    asyncio.run(_do())


async def _reconcile_run_id(SessionLocal, run_id, fetcher):
    from connectors.service import reconcile_run
    from models import Result, Run
    from sqlalchemy import select

    async with SessionLocal() as db:
        run = (await db.execute(select(Run).where(Run.run_id == run_id))).scalar_one()
        results = list((await db.execute(
            select(Result).where(Result.run_id == run_id))).scalars().all())
        out = await reconcile_run(db, run, results, connector_kind="xsiam", fetcher=fetcher)
        return out.summary, results


def test_silent_analytics_without_soakless_is_pending_not_missed(session_factory):
    from connectors.measures import detection_accuracy

    _seed_analytics(session_factory, run_id="run-1", soakless=None)
    asyncio.run(_setup(session_factory))
    fetcher, _ = _tenant([])                         # the tenant said nothing
    summary, results = asyncio.run(_reconcile_run_id(session_factory, "run-1", fetcher))

    r = results[0]
    assert r.observed is False
    assert r.outcome == "pending"
    assert r.outcome != "missed"
    assert r.outcome_reason == "ANALYTICS_ACTIVATION_PERIOD"

    acc = detection_accuracy(results)
    assert tuple(acc) == (None, 0, 0)                # out of the denominator, not 0 %
    assert acc.excluded == {"not_reached": 0, "not_executed": 0, "soak_pending": 1}
    assert summary["excluded"]["soak_pending"] == 1


def test_second_run_silence_is_dedup_window(session_factory):
    # First run saw the detector fire; the paired second run is silent because
    # the tenant deduplicates the same alert inside its window.
    _seed_analytics(session_factory, run_id="run-1", observed=True, soakless=True)
    # Spec §5.5: the dedup hold applies with soakless NOT attested on the run.
    _seed_analytics(session_factory, run_id="run-2", observed=False, soakless=False,
                    paired_with="run-1", with_scenario=False)
    asyncio.run(_setup(session_factory))
    fetcher, _ = _tenant([])
    _summary, results = asyncio.run(_reconcile_run_id(session_factory, "run-2", fetcher))

    r = results[0]
    assert r.outcome == "pending"
    assert r.outcome_reason == "DEDUP_WINDOW"


def test_dedup_key_matches_on_either_spelling(session_factory):
    # First run's row carries only expected_detection; the second run's row has
    # a detection_id AND the same expected_detection.
    _seed_analytics(session_factory, run_id="run-1", detection_id=None, observed=True,
                    soakless=True)
    _seed_analytics(session_factory, run_id="run-2", detection_id="anl-1", observed=False,
                    soakless=False, paired_with="run-1", with_scenario=False)
    asyncio.run(_setup(session_factory))
    fetcher, _ = _tenant([])
    _summary, results = asyncio.run(_reconcile_run_id(session_factory, "run-2", fetcher))
    assert results[0].outcome_reason == "DEDUP_WINDOW"

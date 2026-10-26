"""Task 10 fix round 1 — the reconcile funnel may only certify on complete,
scoped evidence; soak reasons apply from the first sweep; a Tier-2 fail is
sticky; host-severed rows never feed MTTD."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from tests.connectors.test_measurement_service import (
    _alert,
    _observations,
    _prepare_prevention,
    _prevented_alert,
    _reconcile,
    _results_by_step,
    _setup,
    _tenant,
)
from tests.connectors.test_verification_service import _seed

pytestmark = pytest.mark.usefixtures("non_utc_host")

COMPLETE = {"basis": "reconcile", "unscoped": False, "truncated": False, "alert_incidents": {}}


async def _mutate(SessionLocal, fn):
    from models import Result, Run
    from sqlalchemy import select

    async with SessionLocal() as db:
        run = (await db.execute(select(Run))).scalars().first()
        rows = {r.step_id: r for r in (await db.execute(select(Result))).scalars().all()}
        fn(run, rows)
        await db.commit()


async def _apply_silent(SessionLocal, pull_context, *, window_seconds=60):
    from connectors.service import apply_verdicts
    from models import Result
    from sqlalchemy import select

    async with SessionLocal() as db:
        results = list((await db.execute(select(Result))).scalars().all())
        return await apply_verdicts(db, "run-1", results, [], source="t",
                                    pull_context=pull_context, window_seconds=window_seconds)


def _silent_prevent_row(SessionLocal):
    """One `prevent` Result, block mode, executed 10 min ago, no host record."""
    _seed(SessionLocal, results=1)

    def fn(run, rows):
        run.agent_profile_mode = "block"
        rows["step-01"].expected_outcome = "prevent"
    asyncio.run(_mutate(SessionLocal, fn))


# ── Critical 1 — a silent prevent row fails only on complete evidence ──────

def test_truncated_pull_leaves_silent_prevent_row_pending(session_factory):
    _silent_prevent_row(session_factory)
    asyncio.run(_apply_silent(session_factory, {**COMPLETE, "truncated": True}))
    r = asyncio.run(_results_by_step(session_factory))["step-01"]
    assert (r.outcome, r.outcome_reason, r.kpi_verdict) == ("pending", "EVIDENCE_INCOMPLETE", "pending")


def test_unscoped_pull_leaves_silent_prevent_row_pending(session_factory):
    _silent_prevent_row(session_factory)
    asyncio.run(_apply_silent(session_factory, {**COMPLETE, "unscoped": True}))
    r = asyncio.run(_results_by_step(session_factory))["step-01"]
    assert (r.outcome, r.outcome_reason) == ("pending", "EVIDENCE_INCOMPLETE")


def test_complete_pull_makes_silent_prevent_row_missed(session_factory):
    _silent_prevent_row(session_factory)
    asyncio.run(_apply_silent(session_factory, COMPLETE))
    r = asyncio.run(_results_by_step(session_factory))["step-01"]
    assert (r.outcome, r.kpi_verdict) == ("missed", "fail")


def test_manual_import_never_makes_unsubmitted_prevent_row_missed(session_factory):
    from api.connectors import ObservationsBody, ingest_observations

    _silent_prevent_row(session_factory)

    async def _ingest():
        async with session_factory() as db:
            return await ingest_observations("run-1", ObservationsBody(
                observations=[], window_seconds=60), db=db)
    asyncio.run(_ingest())
    r = asyncio.run(_results_by_step(session_factory))["step-01"]
    assert (r.outcome, r.outcome_reason, r.kpi_verdict) == ("pending", "EVIDENCE_INCOMPLETE", "pending")


# ── Important 1 — soak-pending applies from the first sweep ────────────────

def test_silent_abioc_row_is_soak_pending_while_window_open(session_factory):
    from connectors.measures import detection_accuracy

    _seed(session_factory, results=1)

    def fn(run, rows):
        rows["step-01"].signal_type = "ABIOC"
    asyncio.run(_mutate(session_factory, fn))
    _summary, _ = asyncio.run(_apply_silent(session_factory, COMPLETE, window_seconds=86400))
    rows = asyncio.run(_results_by_step(session_factory))
    r = rows["step-01"]
    assert (r.outcome, r.outcome_reason) == ("pending", "ANALYTICS_ACTIVATION_PERIOD")
    acc = detection_accuracy(list(rows.values()))
    assert acc.excluded["soak_pending"] == 1 and tuple(acc) == (None, 0, 0)


# ── Important 2 — a stale soak reason never hides a real detection ─────────

def test_match_clears_stale_soak_reason(session_factory):
    from connectors.measures import detection_accuracy

    _seed(session_factory, results=1)
    asyncio.run(_setup(session_factory))

    def fn(run, rows):
        rows["step-01"].signal_type = "Analytics"
        rows["step-01"].outcome = "pending"
        rows["step-01"].outcome_reason = "ANALYTICS_ACTIVATION_PERIOD"
    asyncio.run(_mutate(session_factory, fn))
    a = _alert("a")
    a["source"] = "XDR Analytics"
    fetcher, _ = _tenant([a])
    asyncio.run(_reconcile(session_factory, fetcher))
    r = asyncio.run(_results_by_step(session_factory))["step-01"]
    assert r.observed is True
    assert (r.outcome, r.outcome_reason) == ("detected", None)
    assert tuple(detection_accuracy([r])) == (100.0, 1, 1)


def test_soak_exclusion_applies_only_to_pending_rows():
    from connectors.measures import denominator_exclusion

    assert denominator_exclusion(SimpleNamespace(
        outcome="detected", outcome_reason="ANALYTICS_ACTIVATION_PERIOD")) is None
    assert denominator_exclusion(SimpleNamespace(
        outcome="pending", outcome_reason="DEDUP_WINDOW")) == "soak_pending"


# ── Important 3 — host-severed rows never feed observed / MTTD ─────────────

def test_matched_not_reached_row_stays_unobserved_but_keeps_evidence(session_factory):
    _seed(session_factory, results=1)
    asyncio.run(_setup(session_factory))

    def fn(run, rows):
        rows["step-01"].outcome = "not_reached"
        rows["step-01"].outcome_reason = "UPSTREAM_SEVERED"
    asyncio.run(_mutate(session_factory, fn))
    fetcher, _ = _tenant([_alert("a")])
    asyncio.run(_reconcile(session_factory, fetcher))
    r = asyncio.run(_results_by_step(session_factory))["step-01"]
    assert r.observed is False and r.observed_at is None
    assert r.outcome == "not_reached"
    obs = asyncio.run(_observations(session_factory))
    assert len(obs) == 1 and obs[0].result_id == r.id


def test_mttd_average_excludes_not_reached_rows(session_factory):
    from connectors.service import score_run_for_run
    from models import Run
    from sqlalchemy import select

    _seed(session_factory, results=2, threshold={"kpi": "MTTD", "op": "≤", "value": 60,
                                                 "unit": "seconds"}, primary_kpi="MTTD")

    def fn(run, rows):
        for sid, secs in (("step-01", 30), ("step-02", 600)):
            rows[sid].observed = True
            rows[sid].observed_at = rows[sid].executed_at + timedelta(seconds=secs)
        rows["step-02"].outcome = "not_reached"
    asyncio.run(_mutate(session_factory, fn))

    async def _score():
        async with session_factory() as db:
            run = (await db.execute(select(Run))).scalars().first()
            await score_run_for_run(db, run, source="t")
            return run.tc_verdict, run.tc_verdict_detail
    verdict, detail = asyncio.run(_score())
    assert detail["primary"]["actual"] == 30.0
    assert verdict == "pass"


# ── Important 4 — a Tier-2 fail is sticky; None never overwrites ───────────

def test_write_prevent_never_erases_or_softens_a_verdict():
    from connectors.service import _write_prevent
    from engine.outcome import PreventVerdict

    r = SimpleNamespace(outcome=None, outcome_reason=None, kpi_verdict="fail")
    _write_prevent(r, PreventVerdict("prevented", "pass", None))
    assert r.kpi_verdict == "fail"

    r = SimpleNamespace(outcome=None, outcome_reason=None, kpi_verdict=None)
    _write_prevent(r, PreventVerdict("detected", None, "ATTESTATION_CONTRADICTED"))
    assert r.kpi_verdict is None

    r = SimpleNamespace(outcome=None, outcome_reason=None, kpi_verdict="pending")
    _write_prevent(r, PreventVerdict("prevented", "pass", None))
    assert r.kpi_verdict == "pass"


def test_prevent_match_keeps_a_tier2_fail(session_factory):
    _seed(session_factory, results=2)
    asyncio.run(_prepare_prevention(session_factory, termination="anchor_terminated"))

    def fn(run, rows):
        rows["step-02"].kpi_verdict = "fail"
    asyncio.run(_mutate(session_factory, fn))
    fetcher, _ = _tenant([_prevented_alert("p1")])
    asyncio.run(_reconcile(session_factory, fetcher))
    assert asyncio.run(_results_by_step(session_factory))["step-02"].kpi_verdict == "fail"


# ── Important 5 — an unscoped pull cannot pass a row ───────────────────────

def test_unscoped_prevented_alert_is_withheld(session_factory):
    from models import Run
    from sqlalchemy import select

    _seed(session_factory, results=2)
    asyncio.run(_prepare_prevention(session_factory, termination="anchor_terminated"))

    async def _unscope():
        async with session_factory() as db:
            run = (await db.execute(select(Run))).scalars().first()
            run.target = None
            await db.commit()
    asyncio.run(_unscope())
    fetcher, _ = _tenant([_prevented_alert("p1")])
    summary, _ = asyncio.run(_reconcile(session_factory, fetcher))
    assert summary["host_scope"]["host"] is None
    r = asyncio.run(_results_by_step(session_factory))["step-02"]
    assert (r.outcome, r.outcome_reason, r.kpi_verdict) == ("pending", "PASS_WITHHELD_UNSCOPED", "pending")


# ── Item 6 — re-evaluation does not duplicate Observation rows ─────────────

def _manual_alert(ext="a"):
    d = {"name": "manual alert", "technique": "T1003.001",
         "timestamp": (datetime.utcnow() - timedelta(minutes=9)).isoformat() + "Z"}
    if ext:
        d["alert_id"] = ext
    return d


def test_reevaluate_twice_records_one_observation(session_factory):
    from api.connectors import ObservationsBody, ingest_observations

    _seed(session_factory, results=1)
    asyncio.run(_setup(session_factory))
    body = ObservationsBody(observations=[_manual_alert("a")], reevaluate=True)

    async def _ingest():
        async with session_factory() as db:
            return await ingest_observations("run-1", body, db=db)
    first = asyncio.run(_ingest())
    assert first["verdicts"], "the manual alert must match"
    asyncio.run(_ingest())
    assert len(asyncio.run(_observations(session_factory))) == 1


def test_reevaluate_twice_without_external_id_records_one_observation(session_factory):
    from api.connectors import ObservationsBody, ingest_observations

    _seed(session_factory, results=1)
    asyncio.run(_setup(session_factory))
    body = ObservationsBody(observations=[_manual_alert(None)], reevaluate=True)

    async def _ingest():
        async with session_factory() as db:
            return await ingest_observations("run-1", body, db=db)
    first = asyncio.run(_ingest())
    assert first["verdicts"], "the manual alert must match"
    asyncio.run(_ingest())
    assert len(asyncio.run(_observations(session_factory))) == 1

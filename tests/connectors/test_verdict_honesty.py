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


async def _record_exited(SessionLocal, step_id="step-01"):
    """What a /complete with `steps[]` leaves behind: the host says it exited."""
    from models import StepOutcome

    async with SessionLocal() as db:
        db.add(StepOutcome(run_id="run-1", step_id=step_id, exit_code=0,
                           termination="exited"))
        await db.commit()


# ── Critical 1 — a silent prevent row fails only on complete evidence ──────

def test_truncated_pull_leaves_silent_prevent_row_pending(session_factory):
    _silent_prevent_row(session_factory)
    asyncio.run(_record_exited(session_factory))
    asyncio.run(_apply_silent(session_factory, {**COMPLETE, "truncated": True}))
    r = asyncio.run(_results_by_step(session_factory))["step-01"]
    assert (r.outcome, r.outcome_reason, r.kpi_verdict) == ("pending", "EVIDENCE_INCOMPLETE", "pending")


def test_unscoped_pull_leaves_silent_prevent_row_pending(session_factory):
    _silent_prevent_row(session_factory)
    asyncio.run(_record_exited(session_factory))
    asyncio.run(_apply_silent(session_factory, {**COMPLETE, "unscoped": True}))
    r = asyncio.run(_results_by_step(session_factory))["step-01"]
    assert (r.outcome, r.outcome_reason) == ("pending", "EVIDENCE_INCOMPLETE")


def test_complete_pull_makes_silent_prevent_row_missed(session_factory):
    # Missed only because the host recorded `exited` for the step (spec §5.3).
    _silent_prevent_row(session_factory)
    asyncio.run(_record_exited(session_factory))
    asyncio.run(_apply_silent(session_factory, COMPLETE))
    r = asyncio.run(_results_by_step(session_factory))["step-01"]
    assert (r.outcome, r.kpi_verdict) == ("missed", "fail")


def test_complete_pull_without_host_record_holds_prevent_row_pending(session_factory):
    # Same run, but /complete carried no `steps[]` (old beacon, staging
    # failure, push mode): nothing says the step ran, so silence is no miss.
    _silent_prevent_row(session_factory)
    asyncio.run(_apply_silent(session_factory, COMPLETE))
    r = asyncio.run(_results_by_step(session_factory))["step-01"]
    assert (r.outcome, r.outcome_reason, r.kpi_verdict) == (
        "pending", "HOST_EVIDENCE_ABSENT", "pending")


def test_manual_import_never_makes_unsubmitted_prevent_row_missed(session_factory):
    from api.connectors import ObservationsBody, ingest_observations

    _silent_prevent_row(session_factory)
    asyncio.run(_record_exited(session_factory))

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


# ── Round 2 / Important A — a Tier-1 missed/fail yields to two real witnesses ─

def test_missed_fail_is_replaced_by_a_late_two_witness_prevention():
    from connectors.service import _write_prevent
    from engine.outcome import PreventVerdict

    r = SimpleNamespace(outcome="missed", outcome_reason=None, kpi_verdict="fail")
    _write_prevent(r, PreventVerdict("prevented", "pass", None))
    assert (r.outcome, r.kpi_verdict) == ("prevented", "pass")


def test_detected_not_prevented_fail_stays_sticky():
    from connectors.service import _write_prevent
    from engine.outcome import PreventVerdict

    r = SimpleNamespace(outcome="detected", outcome_reason="DETECTED_NOT_PREVENTED",
                        kpi_verdict="fail")
    _write_prevent(r, PreventVerdict("prevented", "pass", None))
    assert r.kpi_verdict == "fail"


def test_late_prevented_alert_after_complete_miss_scores_pass(session_factory):
    from models import StepOutcome

    _seed(session_factory, results=1)
    asyncio.run(_setup(session_factory))

    def fn(run, rows):
        run.agent_profile_mode = "block"
        rows["step-01"].expected_outcome = "prevent"
    asyncio.run(_mutate(session_factory, fn))
    asyncio.run(_record_exited(session_factory))
    asyncio.run(_apply_silent(session_factory, COMPLETE))
    r = asyncio.run(_results_by_step(session_factory))["step-01"]
    assert (r.outcome, r.kpi_verdict) == ("missed", "fail")

    async def _witness():
        # The /complete upsert lets a host witness replace `exited`.
        from sqlalchemy import select
        async with session_factory() as db:
            so = (await db.execute(select(StepOutcome).where(
                StepOutcome.step_id == "step-01"))).scalar_one()
            so.termination = "anchor_terminated"
            await db.commit()
    asyncio.run(_witness())
    fetcher, _ = _tenant([_prevented_alert("late")])
    asyncio.run(_reconcile(session_factory, fetcher))
    r = asyncio.run(_results_by_step(session_factory))["step-01"]
    assert (r.outcome, r.kpi_verdict) == ("prevented", "pass")


# ── Round 2 / Important B.1 — the clamp never invents a kpi for report mode ──

def test_report_mode_incomplete_clamp_leaves_kpi_none(session_factory):
    _seed(session_factory, results=1)

    def fn(run, rows):
        run.agent_profile_mode = "report"
        rows["step-01"].expected_outcome = "prevent"
    asyncio.run(_mutate(session_factory, fn))
    asyncio.run(_apply_silent(session_factory, {**COMPLETE, "truncated": True}))
    r = asyncio.run(_results_by_step(session_factory))["step-01"]
    assert (r.outcome, r.outcome_reason) == ("pending", "EVIDENCE_INCOMPLETE")
    assert r.kpi_verdict is None


# ── final review I4 — a manual import is scoped exactly like a pull ────────

def _manual_prevented(ext="mp1", *, host=None):
    d = {"alert_id": ext, "name": f"manual {ext}", "technique": "T1003.001",
         "source": "XDR BIOC", "action": "BLOCKED", "action_pretty": "Prevented (Blocked)",
         "timestamp": (datetime.utcnow() - timedelta(minutes=9)).isoformat() + "Z"}
    if host is not None:
        d["host"] = host
    return d


def _manual_ingest(SessionLocal, observations):
    from api.connectors import ObservationsBody, ingest_observations

    async def _go():
        async with SessionLocal() as db:
            return await ingest_observations("run-1", ObservationsBody(
                observations=observations, window_seconds=3600), db=db)
    return asyncio.run(_go())


def _manual_prevention_run(SessionLocal, *, target=True):
    """Block mode, step-02 `prevent` with a host witness (`signaled`)."""
    from models import Run
    from sqlalchemy import select

    _seed(SessionLocal, results=2)
    asyncio.run(_prepare_prevention(SessionLocal, termination="signaled"))
    if not target:
        async def _untarget():
            async with SessionLocal() as db:
                (await db.execute(select(Run))).scalars().first().target = None
                await db.commit()
        asyncio.run(_untarget())


def test_manual_prevented_alert_on_unscoped_run_is_withheld(session_factory):
    # Another host's alert, pasted by hand, on a run whose target cannot be
    # resolved: nothing can exclude it, so a pass is withheld, never prevented.
    _manual_prevention_run(session_factory, target=False)
    summary = _manual_ingest(session_factory, [_manual_prevented(host="other-host")])
    r = asyncio.run(_results_by_step(session_factory))["step-02"]
    assert (r.outcome, r.outcome_reason, r.kpi_verdict) == (
        "pending", "PASS_WITHHELD_UNSCOPED", "pending")
    assert summary["host_scope"]["host"] is None


def test_manual_prevented_alert_from_another_host_is_excluded(session_factory):
    _manual_prevention_run(session_factory)
    summary = _manual_ingest(session_factory, [_manual_prevented(host="other-host")])
    r = asyncio.run(_results_by_step(session_factory))["step-02"]
    assert r.outcome != "prevented" and r.observed is False
    assert summary["host_scope"]["excluded_other_host"] == 1


def test_manual_prevented_alert_without_a_host_is_withheld(session_factory):
    _manual_prevention_run(session_factory)
    _manual_ingest(session_factory, [_manual_prevented(host=None)])
    r = asyncio.run(_results_by_step(session_factory))["step-02"]
    assert (r.outcome, r.outcome_reason, r.kpi_verdict) == (
        "pending", "PASS_WITHHELD_UNSCOPED", "pending")


def test_manual_prevented_alert_from_the_run_host_still_scores(session_factory):
    # Positive control: scoping withholds what it cannot attribute, nothing more.
    _manual_prevention_run(session_factory)
    _manual_ingest(session_factory, [_manual_prevented(host="web-01")])
    r = asyncio.run(_results_by_step(session_factory))["step-02"]
    assert (r.outcome, r.kpi_verdict) == ("prevented", "pass")


# ── Residual R3 — an operator abort is not a miss ──────────────────────────

async def _record_termination(SessionLocal, termination, step_id="step-01"):
    from models import StepOutcome

    async with SessionLocal() as db:
        db.add(StepOutcome(run_id="run-1", step_id=step_id, exit_code=None,
                           termination=termination))
        await db.commit()


def test_aborted_block_mode_prevent_row_is_pending_run_aborted(session_factory):
    """An `aborted` termination on a still-open run (a fan-out endpoint, an
    agent-side cancel) used to be judged `missed / fail`: the run's verdict is
    undefined, so the row is pending with RUN_ABORTED and the report never
    renders it as a miss."""
    from api.runs import _result_icon
    from engine.report_generator import _status_from_result

    _silent_prevent_row(session_factory)
    asyncio.run(_record_termination(session_factory, "aborted"))
    asyncio.run(_apply_silent(session_factory, COMPLETE))
    r = asyncio.run(_results_by_step(session_factory))["step-01"]
    assert (r.outcome, r.outcome_reason, r.kpi_verdict) == ("pending", "RUN_ABORTED", "pending")
    assert _result_icon(r) == "⏳ (RUN_ABORTED)"
    assert _status_from_result(r.observed, r.observed_at is not None, outcome=r.outcome) == "PENDING"


def test_timeout_block_mode_prevent_row_is_still_missed(session_factory):
    # Ruled: a timeout ran its whole budget unprevented -- missed is honest.
    _silent_prevent_row(session_factory)
    asyncio.run(_record_termination(session_factory, "timeout"))
    asyncio.run(_apply_silent(session_factory, COMPLETE))
    r = asyncio.run(_results_by_step(session_factory))["step-01"]
    assert (r.outcome, r.kpi_verdict) == ("missed", "fail")


# ── Residual R6 — a host-scoping drop is never silent ──────────────────────

def test_manual_alert_from_another_host_is_counted_and_warned(session_factory):
    _manual_prevention_run(session_factory)
    summary = _manual_ingest(session_factory, [_manual_prevented("a1", host="other-host"),
                                               _manual_prevented("a2", host="web-01")])
    # Same shape as the unparseable-timestamp drop: a count and a warning.
    assert summary["excluded_other_host"] == 1
    assert "1 of 2 observations" in summary["warning"]
    assert "web-01" in summary["warning"]


def test_manual_drop_warnings_combine(session_factory):
    _manual_prevention_run(session_factory)
    bad = _manual_prevented("a3", host="web-01")
    bad["timestamp"] = "not a time"
    summary = _manual_ingest(session_factory, [_manual_prevented("a1", host="other-host"), bad])
    assert summary["excluded_other_host"] == 1
    assert summary["dropped_unparseable_timestamps"] == 1
    assert "unreadable timestamp" in summary["warning"]
    assert "another host" in summary["warning"]


def test_manual_import_without_exclusions_carries_no_host_warning(session_factory):
    _manual_prevention_run(session_factory)
    summary = _manual_ingest(session_factory, [_manual_prevented(host="web-01")])
    assert "excluded_other_host" not in summary
    assert "warning" not in summary

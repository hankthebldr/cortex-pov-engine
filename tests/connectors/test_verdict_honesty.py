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
        # The step reported when it ran, 10 min ago (``_seed``'s executed_at):
        # the silent-row window runs from the host record (round 4 C-1).
        ran = datetime.utcnow() - timedelta(minutes=10)
        db.add(StepOutcome(run_id="run-1", step_id=step_id, exit_code=0,
                           termination="exited", started_at=ran, recorded_at=ran))
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
    # The host says the step ran (round 4 C-1: without it there is no miss to clamp).
    asyncio.run(_record_exited(session_factory))
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
        ran = datetime.utcnow() - timedelta(minutes=10)
        db.add(StepOutcome(run_id="run-1", step_id=step_id, exit_code=None,
                           termination=termination, started_at=ran, recorded_at=ran))
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


# ── Round 3 R7 — the engine writes `missed` for a silent detect row ────────

def _silent_detect_row(SessionLocal, *, mode=None, termination=None, status=None):
    """One `detect` Result (BIOC), executed 10 min ago, nothing matched.
    ``termination`` records the host's word for the step (None = no record)."""
    _seed(SessionLocal, results=1)

    def fn(run, rows):
        if mode is not None:
            run.agent_profile_mode = mode
        if status is not None:
            run.status = status
        rows["step-01"].expected_outcome = "detect"
    asyncio.run(_mutate(SessionLocal, fn))
    if termination is not None:
        asyncio.run(_record_termination(SessionLocal, termination))


def _matched_verdict(SessionLocal, step_id="step-01"):
    from connectors.matcher import MatchVerdict
    r = asyncio.run(_results_by_step(SessionLocal))[step_id]
    return MatchVerdict(result_id=r.id, matched=True,
                        observed_at=datetime.utcnow() - timedelta(minutes=9),
                        matched_on=["technique"], alert_external_id="late-1",
                        alert_name="late alert", alert_outcome="detected")


def test_silent_detect_row_on_complete_evidence_is_missed(session_factory):
    _silent_detect_row(session_factory, termination="exited")
    asyncio.run(_apply_silent(session_factory, COMPLETE))
    r = asyncio.run(_results_by_step(session_factory))["step-01"]
    # kpi_verdict is the Tier-2 verifier's: the miss does not invent a verdict.
    assert (r.outcome, r.outcome_reason, r.kpi_verdict, r.observed) == (
        "missed", None, None, False)


def test_silent_detect_row_on_truncated_pull_is_evidence_incomplete(session_factory):
    _silent_detect_row(session_factory, termination="exited")
    asyncio.run(_apply_silent(session_factory, {**COMPLETE, "truncated": True}))
    r = asyncio.run(_results_by_step(session_factory))["step-01"]
    assert (r.outcome, r.outcome_reason) == ("pending", "EVIDENCE_INCOMPLETE")


def test_silent_detect_row_inside_its_window_stays_unjudged(session_factory):
    _silent_detect_row(session_factory, termination="exited")
    asyncio.run(_apply_silent(session_factory, COMPLETE, window_seconds=3600))
    r = asyncio.run(_results_by_step(session_factory))["step-01"]
    assert (r.outcome, r.outcome_reason) == (None, None)


def test_a_later_alert_replaces_a_silent_miss_with_detected(session_factory):
    from connectors.service import apply_verdicts
    from models import Result
    from sqlalchemy import select

    _silent_detect_row(session_factory, termination="exited")
    asyncio.run(_apply_silent(session_factory, COMPLETE))
    v = _matched_verdict(session_factory)

    async def _late():
        async with session_factory() as db:
            results = list((await db.execute(select(Result))).scalars().all())
            await apply_verdicts(db, "run-1", results, [v], source="t",
                                 pull_context=COMPLETE, window_seconds=60)
    asyncio.run(_late())
    r = asyncio.run(_results_by_step(session_factory))["step-01"]
    assert (r.outcome, r.observed) == ("detected", True)


# ── Round 3 R3b — `aborted` is pending in every mode, for every expectation ─

@pytest.mark.parametrize("mode", ["report", "unknown", "block"])
def test_aborted_detect_row_is_pending_run_aborted_in_every_mode(session_factory, mode):
    _silent_detect_row(session_factory, mode=mode)
    asyncio.run(_record_termination(session_factory, "aborted"))
    asyncio.run(_apply_silent(session_factory, COMPLETE))
    r = asyncio.run(_results_by_step(session_factory))["step-01"]
    assert (r.outcome, r.outcome_reason) == ("pending", "RUN_ABORTED")


def test_aborted_report_mode_prevent_row_is_pending_run_aborted(session_factory):
    _silent_prevent_row(session_factory)

    def fn(run, rows):
        run.agent_profile_mode = "report"
    asyncio.run(_mutate(session_factory, fn))
    asyncio.run(_record_termination(session_factory, "aborted"))
    asyncio.run(_apply_silent(session_factory, COMPLETE))
    r = asyncio.run(_results_by_step(session_factory))["step-01"]
    assert (r.outcome, r.outcome_reason) == ("pending", "RUN_ABORTED")



# ── Round 4 C-1 — a miss needs the host's word that the step ran ───────────

def _host_terminate(SessionLocal, termination, step_id="step-01"):
    """What a later /complete does: update the step's record, then project it."""
    from engine.host_evidence import apply_host_outcomes
    from models import Result, Run, StepOutcome
    from sqlalchemy import select

    async def _go():
        async with SessionLocal() as db:
            so = (await db.execute(select(StepOutcome).where(
                StepOutcome.step_id == step_id))).scalar_one()
            so.termination = termination
            run = (await db.execute(select(Run))).scalars().first()
            rows = list((await db.execute(select(Result))).scalars().all())
            apply_host_outcomes(rows, [so], agent_profile_mode=run.agent_profile_mode)
            await db.commit()
    asyncio.run(_go())


def test_in_flight_run_without_a_host_record_never_reads_missed(session_factory):
    """The reviewer's repro: a run still `running` (or queued, or aborted with
    its late /complete rejected) has no StepOutcome. A complete pull must not
    turn its steps into misses."""
    _silent_detect_row(session_factory, status="running")
    asyncio.run(_apply_silent(session_factory, COMPLETE))
    r = asyncio.run(_results_by_step(session_factory))["step-01"]
    assert (r.outcome, r.outcome_reason) == ("pending", "HOST_EVIDENCE_ABSENT")


def test_then_exited_on_record_makes_the_silent_row_missed(session_factory):
    _silent_detect_row(session_factory, status="running")
    asyncio.run(_apply_silent(session_factory, COMPLETE))
    asyncio.run(_record_termination(session_factory, "exited"))
    asyncio.run(_apply_silent(session_factory, COMPLETE))
    r = asyncio.run(_results_by_step(session_factory))["step-01"]
    assert (r.outcome, r.outcome_reason) == ("missed", None)


def test_then_aborted_on_record_replaces_the_tier1_miss(session_factory):
    _silent_detect_row(session_factory, termination="exited")
    asyncio.run(_apply_silent(session_factory, COMPLETE))
    assert asyncio.run(_results_by_step(session_factory))["step-01"].outcome == "missed"
    _host_terminate(session_factory, "aborted")
    r = asyncio.run(_results_by_step(session_factory))["step-01"]
    assert (r.outcome, r.outcome_reason) == ("pending", "RUN_ABORTED")


def test_aborted_never_replaces_a_decided_hit(session_factory):
    _silent_detect_row(session_factory, termination="exited")

    def fn(run, rows):
        rows["step-01"].outcome = "detected"
        rows["step-01"].observed = True
    asyncio.run(_mutate(session_factory, fn))
    _host_terminate(session_factory, "aborted")
    assert asyncio.run(_results_by_step(session_factory))["step-01"].outcome == "detected"


@pytest.mark.parametrize("termination", ["timeout", "signaled", "anchor_terminated"])
def test_a_detect_step_the_host_ran_or_stopped_is_a_miss(session_factory, termination):
    _silent_detect_row(session_factory, termination=termination)
    asyncio.run(_apply_silent(session_factory, COMPLETE))
    r = asyncio.run(_results_by_step(session_factory))["step-01"]
    assert (r.outcome, r.outcome_reason) == ("missed", None)


def test_beacon_lost_detect_step_is_not_a_miss(session_factory):
    _silent_detect_row(session_factory, termination="beacon_lost")
    asyncio.run(_apply_silent(session_factory, COMPLETE))
    r = asyncio.run(_results_by_step(session_factory))["step-01"]
    assert r.outcome == "pending"


@pytest.mark.parametrize("mode", ["report", "unknown"])
def test_report_mode_prevent_row_without_host_record_is_not_a_miss(session_factory, mode):
    _silent_prevent_row(session_factory)

    def fn(run, rows):
        run.agent_profile_mode = mode
    asyncio.run(_mutate(session_factory, fn))
    asyncio.run(_apply_silent(session_factory, COMPLETE))
    r = asyncio.run(_results_by_step(session_factory))["step-01"]
    assert (r.outcome, r.outcome_reason) == ("pending", "HOST_EVIDENCE_ABSENT")


def test_push_staged_run_never_reads_missed_from_reconcile(session_factory):
    """Rule 4: a push run has no beacon and so no host record."""
    _silent_detect_row(session_factory, status="staged")

    def fn(run, rows):
        run.mode = "push"
    asyncio.run(_mutate(session_factory, fn))
    asyncio.run(_apply_silent(session_factory, COMPLETE))
    r = asyncio.run(_results_by_step(session_factory))["step-01"]
    assert (r.outcome, r.outcome_reason) == ("pending", "HOST_EVIDENCE_ABSENT")


# ── Round 4 C-1 rule 3 — the silent-row window starts when the step reported ─

def _late_step(SessionLocal, *, launch_ago, reported_ago):
    """A detect step seeded at launch ``launch_ago`` and reported (``exited``)
    ``reported_ago`` -- a queued beacon or a long multi-step run. Returns the
    seeded executed_at."""
    from models import StepOutcome

    _silent_detect_row(SessionLocal)
    asyncio.run(_setup(SessionLocal))
    launch = datetime.utcnow() - launch_ago

    def fn(run, rows):
        rows["step-01"].executed_at = launch
    asyncio.run(_mutate(SessionLocal, fn))

    async def _reported():
        async with SessionLocal() as db:
            ran = datetime.utcnow() - reported_ago
            db.add(StepOutcome(run_id="run-1", step_id="step-01", exit_code=0,
                               termination="exited", started_at=ran, recorded_at=ran))
            await db.commit()
    asyncio.run(_reported())
    return launch


def _alert_at(when, ext="late-1"):
    from tests.connectors.test_measurement_service import _epoch_ms
    return {"alert_id": ext, "name": f"alert {ext}", "host_name": "web-01",
            "source": "XDR BIOC", "mitre_technique_ids": ["T1003.001"],
            "detection_timestamp": _epoch_ms(when)}


def test_queued_step_window_runs_from_its_host_record_not_the_launch(session_factory):
    """A run queued for a stale beacon: seeded at launch 2 h ago, the step
    reported 5 min ago, its in-step alert fired 8 min ago -- past seed + 1 h,
    inside the step's own window. It is a detection, never a miss (round 5)."""
    launch = _late_step(session_factory, launch_ago=timedelta(hours=2),
                        reported_ago=timedelta(minutes=5))
    alert_at = datetime.utcnow() - timedelta(minutes=8)
    fetcher, _ = _tenant([_alert_at(alert_at)])
    asyncio.run(_reconcile(session_factory, fetcher))
    r = asyncio.run(_results_by_step(session_factory))["step-01"]
    assert (r.outcome, r.observed) == ("detected", True)
    assert r.mttd_seconds == pytest.approx((alert_at - launch).total_seconds(), abs=1.0)


W = timedelta(seconds=3600)  # reconcile_run's default window


def test_round5_alert_after_seed_window_but_inside_step_window_matches(session_factory):
    """Round 5 C-1R: executed_at = launch, host record at launch + 3W, alert at
    launch + 2W (inside the step, outside the old [seed, seed + W])."""
    launch = _late_step(session_factory, launch_ago=4 * W + timedelta(minutes=6),
                        reported_ago=W + timedelta(minutes=6))
    alert_at = launch + 2 * W
    fetcher, _ = _tenant([_alert_at(alert_at)])
    asyncio.run(_reconcile(session_factory, fetcher))
    r = asyncio.run(_results_by_step(session_factory))["step-01"]
    assert (r.outcome, r.observed) == ("detected", True)
    assert r.mttd_seconds == pytest.approx((2 * W).total_seconds(), abs=1.0)


def test_round5_same_shape_without_an_alert_is_missed_only_after_host_record_plus_w(
        session_factory):
    # Host record 30 min ago: its window is still open -> unjudged.
    _late_step(session_factory, launch_ago=3 * W + timedelta(minutes=30),
               reported_ago=timedelta(minutes=30))
    fetcher, _ = _tenant([])
    asyncio.run(_reconcile(session_factory, fetcher))
    r = asyncio.run(_results_by_step(session_factory))["step-01"]
    assert (r.outcome, r.outcome_reason) == (None, None)


def test_round5_without_an_alert_after_host_record_plus_w_is_missed(session_factory):
    _late_step(session_factory, launch_ago=4 * W + timedelta(minutes=6),
               reported_ago=W + timedelta(minutes=6))
    fetcher, _ = _tenant([])
    asyncio.run(_reconcile(session_factory, fetcher))
    r = asyncio.run(_results_by_step(session_factory))["step-01"]
    assert (r.outcome, r.outcome_reason) == ("missed", None)


def test_the_window_closes_an_hour_after_the_host_record(session_factory):
    from models import StepOutcome

    _silent_detect_row(session_factory)

    def fn(run, rows):
        rows["step-01"].executed_at = datetime.utcnow() - timedelta(hours=3)
    asyncio.run(_mutate(session_factory, fn))

    async def _reported():
        async with session_factory() as db:
            db.add(StepOutcome(run_id="run-1", step_id="step-01", exit_code=0,
                               termination="exited",
                               started_at=datetime.utcnow() - timedelta(minutes=61)))
            await db.commit()
    asyncio.run(_reported())
    asyncio.run(_apply_silent(session_factory, COMPLETE, window_seconds=3600))
    r = asyncio.run(_results_by_step(session_factory))["step-01"]
    assert r.outcome == "missed"
    # The match lower bound and MTTD start stay the seeded value (see report).
    assert r.executed_at < datetime.utcnow() - timedelta(hours=2)


def test_every_production_reconcile_call_passes_the_shared_window_end():
    """Round 5 drift guard: the matcher's upper bound comes from the one helper
    (``service.result_window_end`` via ``match_window_ends``); a production
    caller that omits it silently falls back to [seed, seed + window]."""
    import ast
    import pathlib

    core = pathlib.Path(__file__).resolve().parents[2] / "core"
    calls = []
    for path in core.rglob("*.py"):
        if "__pycache__" in str(path):
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "reconcile"):
                calls.append((str(path.relative_to(core)),
                              {k.arg for k in node.keywords}))
    assert calls, "no reconcile() call found"
    assert all("window_end_by_result" in kws for _, kws in calls), calls
    defs = [str(p) for p in core.rglob("*.py") if "def result_window_end" in p.read_text()]
    assert len(defs) == 1 and defs[0].endswith("connectors/service.py"), defs

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
    # Seeded unjudged; the ENGINE writes `missed` below (round 3 R7): the
    # fixture's miss comes from `_judge_silent_results`, never a hand-set value.
    ("step-04", None, None, False, "detect", "BIOC"),
    ("step-05", "not_reached", "UPSTREAM_SEVERED", False, "detect", "BIOC"),
    ("step-06", "not_executed", "TOOL_ABSENT", False, "detect", "BIOC"),
    ("step-07", "pending", "ANALYTICS_ACTIVATION_PERIOD", False, "detect", "Analytics"),
    # Residuals R4/R1: the three rows the outcome axis alone cannot settle.
    # Seeded, never reconciled, never validated: awaiting judgement, NOT a miss.
    ("step-08", None, None, False, "detect", "BIOC"),
    # An alert but no host witness: the reconcile sets observed=True, the
    # verdict is pending. A hit nowhere.
    ("step-09", "pending", "PREVENTION_ALERT_WITHOUT_TERMINATION", True, "prevent", "BIOC"),
    # A legacy / manual validation with no outcome: a hit everywhere.
    ("step-10", None, None, True, "detect", "BIOC"),
    # Round 4 C-1: window closed, nothing matched, but NO host record for the
    # step (in flight, queued, aborted with a rejected /complete, push): the
    # judgement writes pending / HOST_EVIDENCE_ABSENT, never missed.
    ("step-11", None, None, False, "detect", "BIOC"),
]

# In the denominator: prevented, detected, detected-not-prevented, missed,
# the unjudged row, the alert-without-witness pending row, the legacy hit, the
# no-host-record pending row. Hits: prevented, detected, DNP, legacy manual.
DENOMINATOR = 8
HITS = 4
PCT = 50.0

# Per-row status on every surface that names one (R4 / R1).
UNJUDGED, ALERT_NO_WITNESS, LEGACY_HIT = "step-08", "step-09", "step-10"
NO_HOST_RECORD = "step-11"
PARITY_TECHNIQUE, PARITY_TTP = "T1003.001", "TTP-2026-0032"
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
            from models import Scenario
            # The scenario + card the heatmap and the TTP badge key on (I-1).
            db.add(Scenario(
                scenario_id="SIM-EDR-001", name="Credential Dumping", plane="EDR",
                version="1.0", status="active", uc_ref="UCS-EDR-01", uc_name="X",
                tc_ref="TC-EDR-01", tc_name="Y", mitre_tactic="TA0006",
                mitre_tactic_name="Credential Access", mitre_technique=PARITY_TECHNIQUE,
                mitre_technique_name="LSASS Memory", steps=[]))
            db.add(Run(run_id=RUN_ID, scenario_id="SIM-EDR-001", mode="pull",
                       status="complete", agent_profile_mode="block",
                       started_at=now - timedelta(minutes=30)))
            for sid, outcome, reason, observed, expected, sig in ROWS:
                db.add(Result(
                    run_id=RUN_ID, step_id=sid, step_name=sid, plane="EDR",
                    signal_type=sig, expected_detection=f"det {sid}",
                    mitre_technique=PARITY_TECHNIQUE, ttp_ref=PARITY_TTP,
                    expected_outcome=expected, outcome=outcome, outcome_reason=reason,
                    observed=observed,
                    observed_at=(now - timedelta(minutes=20)) if observed else None,
                    # step-08 ran a moment ago: its window is still open, so the
                    # judgement leaves it unjudged (awaiting reconcile).
                    executed_at=now - (timedelta(seconds=5) if sid == UNJUDGED
                                       else timedelta(minutes=25)),
                ))
            await db.commit()
        async with session_factory() as db:
            # A complete, scoped reconcile that matched nothing: the engine's own
            # silent-row judgement decides step-04.
            from connectors.service import _judge_silent_results
            from models import StepOutcome
            # The host's word that step-04 ran (round 4 C-1): only a row with a
            # record can read missed. step-11 has none.
            db.add(StepOutcome(run_id=RUN_ID, step_id="step-04", exit_code=0,
                               termination="exited", started_at=now - timedelta(minutes=24)))
            await db.flush()
            run = (await db.execute(select(Run).where(Run.run_id == RUN_ID))).scalar_one()
            rows = list((await db.execute(
                select(Result).where(Result.run_id == RUN_ID))).scalars().all())
            terminations = {so.step_id: so.termination for so in (await db.execute(
                select(StepOutcome).where(StepOutcome.run_id == RUN_ID))).scalars().all()}
            await _judge_silent_results(db, run, rows, terminations, window_seconds=600,
                                        evidence_complete=True)
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


def test_every_coverage_surface_agrees_on_one_run(client, seeded):
    from connectors.measures import detection_accuracy
    from engine.detection_storyline import build_storyline
    from engine.efficacy_scorecard import build_efficacy_scorecard

    run, results = seeded

    api = client.get(f"/api/results/{RUN_ID}").json()["coverage"]
    api_view = (api["observed_in_denominator"], api["denominator"], api["pct"], api["excluded"])

    sc = build_efficacy_scorecard([r.to_dict() for r in results], run_ids=[RUN_ID])
    c = sc.coverage
    sc_view = (c.detected + c.prevented + c.detected_not_prevented, c.expected, c.pct,
               c.excluded)

    story = build_storyline(run, results, [{"id": sid} for sid, *_ in ROWS])["summary"]
    story_excluded = {
        "not_reached": story["not_reached"], "not_executed": story["not_executed"],
        "soak_pending": story["total"] - story["denominator"]
        - story["not_reached"] - story["not_executed"],
    }
    story_view = (story["detected"] + story["prevented"], story["denominator"],
                  story["coverage_pct"], story_excluded)

    acc = detection_accuracy(results)
    acc_view = (acc[1], acc[2], acc[0], acc.excluded)

    from api.runs import _coverage_stats
    from connectors.service import efficacy_summary
    rep = _coverage_stats(results)
    rep_view = (rep["observed_in_denominator"], rep["denominator"], rep["pct"],
                rep["total"] - rep["denominator"])
    es = efficacy_summary(results, 0, "parity")
    es_view = (es["observed"], es["denominator"], es["coverage_pct"], es["excluded"])

    expected = (HITS, DENOMINATOR, PCT, EXCLUDED)
    n_excluded = sum(EXCLUDED.values())
    assert {"results.py": api_view, "efficacy_scorecard": sc_view,
            "detection_storyline": story_view, "measures.detection_accuracy": acc_view,
            "runs._coverage_stats (report headline)": rep_view,
            "service.efficacy_summary": es_view} == {
        "results.py": expected, "efficacy_scorecard": expected,
        "detection_storyline": expected, "measures.detection_accuracy": expected,
        "runs._coverage_stats (report headline)": (HITS, DENOMINATOR, PCT, n_excluded),
        "service.efficacy_summary": expected}


def test_every_surface_names_the_same_status_for_the_unsettled_rows(seeded):
    """R4: an outcome-less unvalidated row is awaiting judgement, never missed.
    R1: an observed row whose outcome is pending is never a hit. A legacy
    outcome-less observed row stays a hit. Same answer on every surface."""
    from api.runs import _result_icon
    from engine.detection_storyline import build_storyline
    from engine.efficacy_scorecard import _status as scorecard_status
    from engine.report_generator import build_detection_matrix

    run, results = seeded
    by_step = {r.step_id: r for r in results}
    story = {e["step_id"]: e["status"] for e in build_storyline(
        run, results, [{"id": sid} for sid, *_ in ROWS])["entries"]}
    matrix = build_detection_matrix(run.to_dict(), None, [r.to_dict() for r in results])
    matrix_by_step = {r.step_id: m.status for r, m in zip(results, matrix)}

    def view(step):
        r = by_step[step]
        return {"scorecard": scorecard_status(r.to_dict()), "storyline": story[step],
                "report_matrix": matrix_by_step[step], "report_icon": _result_icon(r)[0]}

    assert {s: view(s) for s in (UNJUDGED, ALERT_NO_WITNESS, LEGACY_HIT, NO_HOST_RECORD)} == {
        UNJUDGED: {"scorecard": "pending", "storyline": "pending",
                   "report_matrix": "PENDING", "report_icon": "⏳"},
        ALERT_NO_WITNESS: {"scorecard": "pending", "storyline": "pending",
                           "report_matrix": "PENDING", "report_icon": "⏳"},
        LEGACY_HIT: {"scorecard": "detected", "storyline": "detected",
                     "report_matrix": "ENABLED", "report_icon": "✅"},
        NO_HOST_RECORD: {"scorecard": "pending", "storyline": "pending",
                         "report_matrix": "PENDING", "report_icon": "⏳"},
    }


def test_scorecard_never_reads_detected_not_prevented_as_detected(seeded):
    from engine.efficacy_scorecard import build_efficacy_scorecard

    _, results = seeded
    c = build_efficacy_scorecard([r.to_dict() for r in results]).coverage
    # step-02 and the legacy manual step-10 are detected.
    assert (c.detected, c.prevented, c.detected_not_prevented, c.missed) == (2, 1, 1, 1)


def test_the_fixture_miss_is_engine_written(seeded):
    """R7: the only `missed` row is the one the silent-row judgement wrote."""
    _, results = seeded
    outcomes = {r.step_id: (r.outcome, r.outcome_reason) for r in results}
    assert outcomes["step-04"] == ("missed", None)
    assert outcomes[UNJUDGED] == (None, None)
    assert outcomes[NO_HOST_RECORD] == ("pending", "HOST_EVIDENCE_ABSENT")


def test_exec_summary_coverage_uses_the_one_denominator_and_numerator(seeded):
    """The exec summary's coverage line: numerator through ``is_hit``,
    denominator through ``is_in_denominator`` -- same answer as every surface."""
    from engine.report_generator import render_exec_summary_markdown

    run, results = seeded
    md = render_exec_summary_markdown(run.to_dict(), None, [r.to_dict() for r in results])
    assert f"{HITS} / {DENOMINATOR} expected detections observed ({PCT}%)" in md


def test_heatmap_and_ttp_badge_agree_with_every_surface(make_client, seeded):
    """Round 4 I-1: the MITRE heatmap tally and the TTP runs badge count hits
    through ``is_hit`` over the one denominator rule -- the same 4 / 8."""
    import pathlib

    from api.mitre import router as mitre_router
    from api.ttps import router as ttps_router
    from engine.ttp_catalog import catalog as ttp_catalog

    ttp_catalog.load(str(pathlib.Path(__file__).resolve().parents[2]
                         / "detection_scanner" / "ttps"))

    c = make_client(mitre_router, ttps_router)
    cov = c.get("/api/mitre/coverage").json()
    techs = cov["techniques"] if isinstance(cov, dict) else cov
    t = next(x for x in techs if x["technique_id"] == PARITY_TECHNIQUE)
    badge = c.get(f"/api/ttps/{PARITY_TTP}/runs").json()["runs"][0]
    assert {"mitre": (t["observed_detections"], t["total_detections"]),
            "ttps": (badge["observed"], badge["expected"])} == {
        "mitre": (HITS, DENOMINATOR), "ttps": (HITS, DENOMINATOR)}

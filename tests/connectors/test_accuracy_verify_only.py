"""Issue #272 — Detection Accuracy and Tier-2-verify-only XQL rows.

An XQL row whose card object is a query raises no alert in XSIAM, so the
matcher can credit it only when a correlation alert sharing its technique
lands in its window. On a healthy tenant it is written ``missed``, and
Detection Accuracy counted it as one: 43 scenarios with a 90 % bar could
never reach it and read FAIL. An unmatched verify-only row now leaves the
Detection Accuracy denominator, the way ``not_reached`` / ``not_executed``
rows already do; a matched one still counts, once, on both sides.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from connectors.measures import build_measurement, detection_accuracy
from engine.verifier import score_run

BAR_90 = {"kpi": "Detection Accuracy", "op": "≥", "value": 90, "unit": "%"}
BAR_50 = {"kpi": "Detection Accuracy", "op": "≥", "value": 50, "unit": "%"}
COMPLETE = {"basis": "reconcile", "unscoped": False, "truncated": False}


def _row(signal_type, outcome, *, kind=None, verification_xql=None):
    """A Result as reconcile leaves it: ``detected`` rows observed, the rest not."""
    return SimpleNamespace(
        signal_type=signal_type, detection_kind=kind, ttp_ref=None, detection_id=None,
        verification_xql=verification_xql, executed_at=datetime.utcnow(),
        outcome=outcome, outcome_reason=None, observed=outcome == "detected",
        kpi_verdict="pending" if verification_xql else None, kpi_contribution=None,
        mttd_seconds=None, expected_detection="", id=None)


def _bioc(outcome):
    return _row("BIOC", outcome, kind="bioc")


def _xql(outcome, **kw):
    return _row("XQL", outcome, kind="xql", **kw)


def _score(rows, threshold):
    value, record = build_measurement("Detection Accuracy", threshold, rows, COMPLETE)
    run = score_run(rows, threshold=threshold, primary_kpi="Detection Accuracy",
                    measured_value=value)
    return value, record, run


# ── the rule ────────────────────────────────────────────────────────────────

def test_verify_only_is_an_xql_row_that_no_alert_raising_card_object_backs():
    from connectors.measures import is_verify_only

    assert is_verify_only(_row("XQL", None, kind="xql"))
    assert is_verify_only(_row("xql", None, kind="xql"))          # case-folded
    assert is_verify_only(_row("XQL", None, kind="modeling"))     # XDM rule: no alert
    assert is_verify_only(_row("XQL", None, kind=None))           # no card at all
    # An XQL-typed row whose card object raises alerts is alert-backed.
    assert not is_verify_only(_row("XQL", None, kind="correlation"))
    assert not is_verify_only(_row("XQL", None, kind="bioc"))
    # Only the declared type XQL: an Analytics row backed by a hunting query
    # is still raised by the built-in detector the row names.
    assert not is_verify_only(_row("Analytics", None, kind="xql"))
    assert not is_verify_only(_bioc(None))
    assert not is_verify_only(SimpleNamespace())                  # legacy row


def test_the_card_object_is_read_from_the_catalog_when_the_row_has_no_kind():
    """EAL-seeded rows carry no ``detection_kind``; the card is looked up."""
    from connectors.measures import is_verify_only
    from engine.ttp_catalog import catalog

    catalog.load(os.path.join(os.environ.get("CORTEXSIM_BASE_DIR", "."),
                              "detection_scanner", "ttps"))
    xql = SimpleNamespace(signal_type="XQL", detection_kind=None, ttp_ref="TTP-2026-0037",
                          detection_id="xql-itdr-001-impossible-travel-two-distant-geos-within-window")
    assert is_verify_only(xql)
    corr = SimpleNamespace(signal_type="XQL", detection_kind=None, ttp_ref="TTP-2026-0037",
                           detection_id="CR-ITDR-0001")
    assert not is_verify_only(corr)


# ── the denominator ─────────────────────────────────────────────────────────

def test_unmatched_verify_only_rows_no_longer_hold_a_healthy_run_below_its_bar():
    """Every alert-backed row detected, every verify-only row silent: 100 %, PASS.
    Before #272 this read 2 of 4 = 50 % and FAILED a 90 % bar."""
    rows = [_bioc("detected"), _bioc("detected"), _xql("missed"), _xql("missed")]
    value, record, run = _score(rows, BAR_90)
    assert run.verdict == "pass"
    assert (value, record["observed"], record["seeded"]) == (100.0, 2, 2)
    assert record["verify_only"] == {"excluded": 2, "credited": 0, "no_verification_xql": 2}


def test_an_alert_backed_miss_still_fails_the_bar():
    rows = [_bioc("detected"), _bioc("missed"), _xql("missed")]
    value, record, run = _score(rows, BAR_90)
    assert (value, record["observed"], record["seeded"]) == (50.0, 1, 2)
    assert run.verdict == "fail"


def test_a_matched_verify_only_row_is_credited_once_on_both_sides():
    """A correlation alert that matched an XQL row is a detection of that step:
    it counts in the numerator AND the denominator, as it did before #272, so
    the value can never exceed 100 %. Only the UNMATCHED row leaves."""
    rows = [_bioc("detected"), _bioc("missed"), _xql("detected"), _xql("missed")]
    acc = detection_accuracy(rows)
    assert tuple(acc) == (66.7, 2, 3)
    assert acc.verify_only == {"excluded": 1, "credited": 1, "no_verification_xql": 1}


@pytest.mark.parametrize("rows, threshold", [
    # both verify-only rows matched: was 2 of 2 = 100 %, PASS
    ([_xql("detected"), _xql("detected")], BAR_90),
    # one of two matched: was 50 %, PASS on a 50 % bar
    ([_xql("detected"), _xql("missed")], BAR_50),
    # none matched
    ([_xql("missed"), _xql("missed")], BAR_50),
])
def test_a_run_whose_every_row_is_verify_only_is_never_pass(rows, threshold):
    """With no alert-backed row a matched verify-only row would be its own
    denominator — matched over matched is 100 % by construction and proves
    nothing. The measure is withheld and the run reads pending."""
    value, record, run = _score(rows, threshold)
    assert run.verdict == "pending"
    assert value is None and record["value"] is None and record["scored"] is None
    assert "verify-only" in record["withheld"]


def test_rows_outside_the_denominator_for_other_reasons_do_not_count_as_alert_backed():
    """A BIOC whose step never ran is not an alert-backed row to measure against."""
    rows = [_row("BIOC", "not_executed", kind="bioc"), _xql("detected")]
    value, record, run = _score(rows, BAR_50)
    assert value is None and "verify-only" in record["withheld"]
    assert record["excluded"]["not_executed"] == 1
    assert run.verdict == "pending"


def test_a_verify_only_row_with_a_verification_xql_is_left_to_tier_2():
    """Tier 2 owns its verdict: a FAIL there still fails the run."""
    rows = [_bioc("detected"), _xql("missed", verification_xql="dataset = x")]
    rows[1].kpi_verdict = "fail"
    value, record, run = _score(rows, BAR_90)
    assert value == 100.0
    assert record["verify_only"] == {"excluded": 1, "credited": 0, "no_verification_xql": 0}
    assert run.verdict == "fail" and run.counts["fail"] == 1


# ── one real corpus scenario from the 43, end to end ────────────────────────

ITDR_001 = os.path.join("scenarios", "itdr", "sim-itdr-001-impossible-travel.yml")


async def _seed_itdr_001(SessionLocal):
    """SIM-ITDR-001 through the real loader, the real seeding path and the
    real catalog: step-01 BIOC, step-02 two XQL, step-03 one XQL."""
    from config import settings
    from engine.orchestrator import orchestrator
    from engine.scenario_loader import _parse_and_validate, _schema_to_orm_kwargs
    from engine.ttp_catalog import catalog, default_corpus_dir
    from models import Agent, Run, Scenario, StepOutcome
    from security.credentials import CredentialStore

    catalog.load(default_corpus_dir(settings.CORTEXSIM_BASE_DIR))
    schema, err = _parse_and_validate(os.path.join(settings.CORTEXSIM_BASE_DIR, ITDR_001))
    assert schema is not None, err
    executed = datetime.utcnow() - timedelta(minutes=10)
    async with SessionLocal() as db:
        await CredentialStore(db).put_integration(
            name="acme", kind="xsiam", plaintext_secret="k" * 40,
            config={"fqdn": "api-t.xdr.us.paloaltonetworks.com", "api_key_id": "1"})
        scenario = Scenario(**_schema_to_orm_kwargs(schema))
        db.add(scenario)
        db.add(Agent(agent_id="agent-1", hostname="web-01", os="linux"))
        db.add(Run(run_id="run-1", scenario_id="SIM-ITDR-001", mode="pull",
                   status="complete", target="agent-1", started_at=executed,
                   completed_at=datetime.utcnow()))
        for step in ("step-01", "step-02", "step-03"):
            db.add(StepOutcome(run_id="run-1", step_id=step, exit_code=0,
                               termination="exited", started_at=executed,
                               recorded_at=executed))
        await db.commit()
        await orchestrator._seed_results("run-1", scenario, executed, db)


async def _reconcile_itdr_001(SessionLocal, fetcher):
    from connectors.service import reconcile_run
    from models import Result, Run
    from sqlalchemy import select

    async with SessionLocal() as db:
        run = (await db.execute(select(Run))).scalars().first()
        results = list((await db.execute(select(Result))).scalars().all())
        out = await reconcile_run(db, run, results, connector_kind="xsiam",
                                  fetcher=fetcher, window_seconds=60)
        await db.refresh(run)
        rows = {(r.step_id, r.signal_type, r.detection_id): r for r in results}
        return out.summary, run, rows


@pytest.mark.usefixtures("non_utc_host")
def test_sim_itdr_001_reaches_its_bar_when_its_one_alert_backed_row_fires(session_factory):
    """The BIOC on step-01 fires; the three XQL rows are silent and the detect
    branch writes them ``missed``. Before #272: 1 of 4 = 25 %, FAIL on a
    healthy tenant. Now: 1 of 1 = 100 %, PASS, the XQL rows still ``missed``."""
    from tests.connectors.test_measurement_service import _alert, _tenant

    asyncio.run(_seed_itdr_001(session_factory))
    fetcher, _ = _tenant([_alert("bioc-1", technique="T1078.004")])
    summary, run, rows = asyncio.run(_reconcile_itdr_001(session_factory, fetcher))

    by_type = sorted((k[1], r.detection_kind, r.outcome) for k, r in rows.items())
    assert by_type == [("BIOC", "bioc", "detected"), ("XQL", "xql", "missed"),
                       ("XQL", "xql", "missed"), ("XQL", "xql", "missed")]

    assert run.tc_verdict == "pass"
    m = summary["measurement"]
    assert (m["value"], m["observed"], m["seeded"]) == (100.0, 1, 1)
    assert m["verify_only"] == {"excluded": 3, "credited": 0, "no_verification_xql": 3}
    assert run.tc_verdict_detail["primary"]["actual"] == 100.0

"""Replay of issue #346's sweep: what each alert credits (#346, #347).

Seventeen runs back to back on one endpoint raised 25 alerts there. On 1.2.0,
reconcile credited 5 rows: 3 from an alert of their own run, 1 from an NGFW
alert that names no host, and 1 FALSE one: SIM-EDR-018's ABIOC row, from the
alert SIM-MP-003 raised 32 minutes later (base technique T1059 + the ABIOC
family), with an MTTD of 1,920 s.

The fixture is the issue's table (``tests/fixtures/sweep2_replay``); the rows
are seeded from the real scenario files; the reconcile is the production
``reconcile_run`` against an injected fetcher. No tenant, no network.
"""
from __future__ import annotations

import asyncio

import pytest

from tests.connectors.sweep2_replay import Sweep

# The windows are naive UTC; a fixture that mints epochs in host-local time
# must fail here, not on a laptop.
pytestmark = pytest.mark.usefixtures("non_utc_host")

EDR_018 = "0033b4b4"
MP_003 = "981df947"
FOREIGN_ALERT = "1502502"
FALSE_ROW = "abioc-ai-soc-72-rapid-privilege-escalation-after-valid-account-compromise"


@pytest.fixture
def sweep(session_factory) -> Sweep:
    s = Sweep()
    asyncio.run(s.seed(session_factory))
    return s


def _key(c: dict) -> tuple:
    return (c["run"], c["alert_id"])


# ── the fixture is the issue ────────────────────────────────────────────────

def test_the_fixture_holds_the_issues_25_host_alerts_and_17_runs(sweep):
    on_host = [a for a in sweep.data["alerts"] if a.get("host", sweep.host) is not None]
    assert len(on_host) == 25
    assert len(sweep.data["alerts"]) == 26          # + the NGFW alert with no host
    assert len({a["run"] for a in on_host}) == len(sweep.runs) == 17
    # Every alert falls between its run's launch and the next listed launch.
    launches = sorted((sweep.when(r["launched"]), short) for short, r in sweep.runs.items())
    for a in sweep.data["alerts"]:
        at = sweep.when(a["time"])
        on_the_host = [short for when, short in launches if when <= at][-1]
        assert on_the_host == a["run"], a


def test_the_seeded_rows_are_the_counts_the_issue_reports(sweep, session_factory):
    from models import Result
    from sqlalchemy import func, select

    async def _counts():
        async with session_factory() as db:
            return dict((await db.execute(
                select(Result.run_id, func.count()).group_by(Result.run_id))).all())

    counts = asyncio.run(_counts())
    for short, r in sweep.runs.items():
        if r["rows_in_issue"] is not None:
            assert counts[sweep.run_id(short)] == r["rows_in_issue"], r["scenario"]


# ── before: 1.2.0 ───────────────────────────────────────────────────────────

def test_without_the_one_run_rule_the_replay_reproduces_the_false_credit(sweep, session_factory):
    """The control. Taking every alert in a row's window, as 1.2.0 did, gives
    the issue's five credits exactly: 3 own-run, 1 host-less, 1 cross-run."""
    credits = asyncio.run(sweep.without_the_one_run_rule(session_factory))

    expected = {(c["run"], c["alert_id"]): c for c in sweep.data["credited_in_1_2_0"]}
    assert {_key(c) for c in credits} == set(expected)
    for c in credits:
        assert c["mttd_seconds"] == expected[_key(c)]["mttd_seconds"], c
        assert c["own_run"] is expected[_key(c)]["own_run"], c

    on_host_own = [c for c in credits if c["own_run"] and c["alert_id"] != "1502539"]
    cross = [c for c in credits if not c["own_run"]]
    assert len(on_host_own) == 3
    assert [(c["run"], c["alert_id"], c["raised_in"], c["mttd_seconds"], c["matched_on"])
            for c in cross] == [
        (EDR_018, FOREIGN_ALERT, MP_003, 1920.0, ["technique-base", "source"])]


# ── after ───────────────────────────────────────────────────────────────────

def test_no_run_is_credited_with_another_runs_alert(sweep, session_factory):
    summaries = asyncio.run(sweep.reconcile_all(session_factory))
    credits = asyncio.run(sweep.credits(session_factory))

    assert [c for c in credits if not c["own_run"]] == []            # zero cross-run credits
    assert {_key(c) for c in credits} == {
        ("a0425626", "1502466"), ("24726ff9", "1502493"),
        ("3894a972", "1502499"), ("f0653380", "1502539")}
    assert all(c["mttd_seconds"] in (48.0, 45.0, 215.0, 3.0) for c in credits)  # no 1,920 s

    # SIM-EDR-018 is told which alert it was not given, and whose it is.
    edr018 = summaries[EDR_018]
    assert edr018["observed"] == 0 and edr018["mttd"]["count"] == 0
    refused = {e["alert_external_id"]: e for e in edr018["not_credited"]}
    assert refused[FOREIGN_ALERT]["reason"] == "LATER_RUN_STARTED"
    assert refused[FOREIGN_ALERT]["run_id"] == sweep.run_id(MP_003)
    assert refused[FOREIGN_ALERT]["scenario_id"] == "SIM-MP-003"
    assert "unattributed" not in edr018                              # it has an owner


def test_the_false_row_is_unobserved_with_no_mttd(sweep, session_factory):
    from models import Result
    from sqlalchemy import select

    asyncio.run(sweep.reconcile_all(session_factory))

    async def _row():
        async with session_factory() as db:
            return (await db.execute(select(Result).where(
                Result.run_id == sweep.run_id(EDR_018),
                Result.detection_id == FALSE_ROW))).scalar_one()

    row = asyncio.run(_row())
    assert row.observed is False and row.observed_at is None and row.mttd_seconds is None
    assert row.outcome != "detected"


def test_the_order_the_runs_are_reconciled_in_changes_nothing(sweep, session_factory):
    asyncio.run(sweep.reconcile_all(session_factory, order=list(reversed(list(sweep.runs)))))
    credits = asyncio.run(sweep.credits(session_factory))
    assert [c for c in credits if not c["own_run"]] == []
    assert len(credits) == 4


# ── a run stored with the false credit, reviewed again ──────────────────────

def _store_the_1_2_0_credit(sweep, session_factory) -> None:
    """Write SIM-EDR-018's rows the way 1.2.0 left them: the matcher with no
    one-run rule, through ``apply_verdicts``."""
    from connectors.matcher import reconcile, scope_to_host
    from connectors.service import apply_verdicts, match_window_ends, window_by_type
    from connectors.xsiam import XsiamConnector
    from models import Result
    from sqlalchemy import select

    async def _go():
        normalize = XsiamConnector()._normalize_alert
        eligible, _ = scope_to_host([normalize(a) for a in sweep.tenant_alerts()], sweep.host)
        async with session_factory() as db:
            results = list((await db.execute(select(Result).where(
                Result.run_id == sweep.run_id(EDR_018)))).scalars().all())
            ends = await match_window_ends(db, sweep.run_id(EDR_018), results,
                                           window_seconds=3600)
            verdicts = reconcile(results, eligible, window_seconds=3600,
                                 window_seconds_by_type=window_by_type(),
                                 window_end_by_result=ends)
            await apply_verdicts(db, sweep.run_id(EDR_018), results, verdicts,
                                 source="xsiam:replay",
                                 pull_context={"basis": "reconcile", "unscoped": False,
                                               "host_unresolved": False, "truncated": False,
                                               "dropped": 0, "alert_incidents": {}},
                                 observations=eligible, window_seconds=3600)

    asyncio.run(_go())


def test_a_stored_cross_run_credit_is_withdrawn_when_the_run_is_reviewed_again(
        sweep, session_factory):
    from models import Observation, Result
    from sqlalchemy import select

    _store_the_1_2_0_credit(sweep, session_factory)
    before = asyncio.run(sweep.credits(session_factory))
    assert [(c["run"], c["alert_id"], c["mttd_seconds"]) for c in before] == [
        (EDR_018, FOREIGN_ALERT, 1920.0)]

    summary = asyncio.run(sweep.reconcile(session_factory, EDR_018))

    # Said out loud, in the summary ...
    assert [(w["alert_external_id"], w["reason"], w["run_id"], w["mttd_seconds"])
            for w in summary["withdrawn_credits"]] == [
        (FOREIGN_ALERT, "LATER_RUN_STARTED", sweep.run_id(MP_003), 1920.0)]
    assert any("withdrawn" in w for w in summary["warnings"])
    assert summary["observed"] == 0 and summary["mttd"]["count"] == 0

    async def _state():
        async with session_factory() as db:
            row = (await db.execute(select(Result).where(
                Result.run_id == sweep.run_id(EDR_018),
                Result.detection_id == FALSE_ROW))).scalar_one()
            obs = (await db.execute(select(Observation).where(
                Observation.external_id == FOREIGN_ALERT))).scalars().all()
            return row, obs

    row, obs = asyncio.run(_state())
    # ... on the row ...
    assert row.observed is False and row.observed_at is None and row.mttd_seconds is None
    assert row.outcome != "detected"
    assert "credit withdrawn on re-review: alert 1502502" in row.notes
    assert "after run 981df947 (SIM-MP-003) had started on this host" in row.notes
    # ... and the alert stays on record, attached to no row.
    assert len(obs) == 1 and obs[0].result_id is None

    # A second review has nothing left to withdraw and credits nothing.
    again = asyncio.run(sweep.reconcile(session_factory, EDR_018))
    assert "withdrawn_credits" not in again and again["observed"] == 0


def test_a_stored_own_run_credit_is_left_alone_on_re_review(sweep, session_factory):
    first = asyncio.run(sweep.reconcile(session_factory, "24726ff9"))
    assert first["observed"] == 1
    again = asyncio.run(sweep.reconcile(session_factory, "24726ff9"))
    assert "withdrawn_credits" not in again and again["observed"] == 1
    assert [c["mttd_seconds"] for c in asyncio.run(sweep.credits(session_factory))] == [45.0]


def test_an_operators_attestation_is_never_withdrawn(sweep, session_factory):
    """A row a person marked observed has no alert on record: the re-review
    has nothing to weigh and leaves it."""
    from models import Result
    from sqlalchemy import select

    async def _attest():
        async with session_factory() as db:
            row = (await db.execute(select(Result).where(
                Result.run_id == sweep.run_id(EDR_018),
                Result.detection_id == FALSE_ROW))).scalar_one()
            row.observed = True
            row.observed_at = sweep.when("06:07:05")
            row.outcome = "detected"
            await db.commit()

    asyncio.run(_attest())
    summary = asyncio.run(sweep.reconcile(session_factory, EDR_018))
    assert "withdrawn_credits" not in summary and summary["observed"] == 1

"""Tests for the verification harness — "did it meet the bar", not "did we see it".

The load-bearing property here is negative: an unscoreable test case must never
come back ``pass``. 57 of the master index's 107 detection-backable test cases
carry no measurable threshold and the corpus binds 37 of them, so a silent pass
would turn a meaningless run into a green POV readout.
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeResult:
    """Minimal stand-in for the Result ORM row the verifier touches."""

    def __init__(self, *, verification_xql=None, kpi_contribution=None,
                 kpi_verdict=None, mttd_seconds=None, detection_id="d1",
                 ttp_ref=None, rid=1):
        self.id = rid
        self.verification_xql = verification_xql
        self.kpi_contribution = kpi_contribution
        self.kpi_verdict = kpi_verdict
        self.verified_at = None
        self.mttd_seconds = mttd_seconds
        self.detection_id = detection_id
        self.expected_detection = detection_id
        self.ttp_ref = ttp_ref


class FakeRun:
    def __init__(self):
        self.run_id = "run-1"
        self.tc_verdict = None
        self.tc_verdict_detail = None


class FakeScenario:
    def __init__(self, threshold=None, primary_kpi=None):
        self.threshold = threshold
        self.primary_kpi = primary_kpi


def runner_returning(rows):
    async def _run(_query):
        return rows
    return _run


def runner_raising(exc):
    async def _run(_query):
        raise exc
    return _run


# ---------------------------------------------------------------------------
# is_scoreable
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("threshold", [
    None, "", "Qualitative pass", "qualitative pass", "TBD", "N/A",
])
def test_unscoreable_thresholds_rejected(threshold):
    from engine.verifier import is_scoreable  # noqa: PLC0415

    assert is_scoreable(threshold) is False


@pytest.mark.parametrize("threshold", [
    {"kpi": "MTTD", "op": "<=", "value": 300, "unit": "s"},
    {"kpi": "Detection Accuracy", "op": ">", "value": 0.8},
    {"kpi": "x", "op": "≥", "value": 1},
])
def test_scoreable_thresholds_accepted(threshold):
    from engine.verifier import is_scoreable  # noqa: PLC0415

    assert is_scoreable(threshold) is True


def test_raw_index_threshold_string_is_not_structured_enough():
    """'>60%' reads as scoreable to a human but carries no parsed op/value —
    it must not be silently treated as a pass."""
    from engine.verifier import NOT_APPLICABLE, evaluate_threshold  # noqa: PLC0415

    v = evaluate_threshold(">60%", 0.9)
    assert v.verdict == NOT_APPLICABLE
    assert "structure it" in v.detail


# ---------------------------------------------------------------------------
# evaluate_threshold
# ---------------------------------------------------------------------------


def test_threshold_pass():
    from engine.verifier import PASS, evaluate_threshold  # noqa: PLC0415

    v = evaluate_threshold({"kpi": "MTTD", "op": "<=", "value": 300, "unit": "s"}, 120.0)
    assert v.verdict == PASS
    assert v.actual == 120.0 and v.expected == 300.0


def test_threshold_fail():
    from engine.verifier import FAIL, evaluate_threshold  # noqa: PLC0415

    v = evaluate_threshold({"kpi": "MTTD", "op": "<=", "value": 300, "unit": "s"}, 900.0)
    assert v.verdict == FAIL


def test_threshold_pending_without_measurement():
    from engine.verifier import PENDING, evaluate_threshold  # noqa: PLC0415

    v = evaluate_threshold({"kpi": "MTTD", "op": "<=", "value": 300}, None)
    assert v.verdict == PENDING


def test_unicode_operators_supported():
    """The index writes thresholds with both ASCII and unicode comparators."""
    from engine.verifier import PASS, evaluate_threshold  # noqa: PLC0415

    assert evaluate_threshold({"kpi": "k", "op": "≤", "value": 10}, 5).verdict == PASS
    assert evaluate_threshold({"kpi": "k", "op": "≥", "value": 10}, 20).verdict == PASS


def test_qualitative_threshold_never_passes():
    """The load-bearing negative."""
    from engine.verifier import NOT_APPLICABLE, evaluate_threshold  # noqa: PLC0415

    for actual in (None, 0.0, 1.0, 99999.0):
        assert evaluate_threshold("Qualitative pass", actual).verdict == NOT_APPLICABLE


# ---------------------------------------------------------------------------
# verify_results
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_passes_when_rows_meet_floor():
    from engine.verifier import PASS, verify_results  # noqa: PLC0415

    r = FakeResult(verification_xql="dataset = xdr_data")
    counts = await verify_results([r], runner_returning(3), expected_rows_min=lambda _r: 1)
    assert r.kpi_verdict == PASS
    assert r.verified_at is not None
    assert counts[PASS] == 1


@pytest.mark.asyncio
async def test_verify_fails_when_rows_below_floor():
    from engine.verifier import FAIL, verify_results  # noqa: PLC0415

    r = FakeResult(verification_xql="dataset = xdr_data")
    await verify_results([r], runner_returning(0), expected_rows_min=lambda _r: 1)
    assert r.kpi_verdict == FAIL


@pytest.mark.asyncio
async def test_verify_respects_a_higher_row_floor():
    from engine.verifier import FAIL, verify_results  # noqa: PLC0415

    r = FakeResult(verification_xql="q")
    await verify_results([r], runner_returning(3), expected_rows_min=lambda _r: 5)
    assert r.kpi_verdict == FAIL


@pytest.mark.asyncio
async def test_results_without_verification_xql_are_untouched():
    """Stamping a verdict on a row this harness does not own would
    misrepresent coverage."""
    from engine.verifier import verify_results  # noqa: PLC0415

    r = FakeResult(verification_xql=None)
    counts = await verify_results([r], runner_returning(9), expected_rows_min=lambda _r: 1)
    assert r.kpi_verdict is None
    assert r.verified_at is None
    assert counts["skipped"] == 1


@pytest.mark.asyncio
async def test_tenant_error_is_pending_not_fail():
    """A tenant outage is not a detection failure — calling it one produces
    false red in a customer readout."""
    from engine.verifier import PENDING, verify_results  # noqa: PLC0415

    r = FakeResult(verification_xql="q")
    await verify_results([r], runner_raising(RuntimeError("503")),
                         expected_rows_min=lambda _r: 1)
    assert r.kpi_verdict == PENDING
    assert r.verified_at is None


# ---------------------------------------------------------------------------
# score_run
# ---------------------------------------------------------------------------


def test_run_passes_when_all_scoreable_results_pass():
    from engine.verifier import PASS, score_run  # noqa: PLC0415

    s = score_run([FakeResult(kpi_verdict="pass"), FakeResult(kpi_verdict="pass")])
    assert s.verdict == PASS
    assert s.counts["pass"] == 2


def test_one_failure_sinks_the_run():
    from engine.verifier import FAIL, score_run  # noqa: PLC0415

    s = score_run([FakeResult(kpi_verdict="pass"), FakeResult(kpi_verdict="fail")])
    assert s.verdict == FAIL


def test_pending_outranks_pass():
    """Do not call a run passed while verification is still outstanding."""
    from engine.verifier import PENDING, score_run  # noqa: PLC0415

    s = score_run([FakeResult(kpi_verdict="pass"), FakeResult(kpi_verdict="pending")])
    assert s.verdict == PENDING


def test_fail_outranks_pending():
    from engine.verifier import FAIL, score_run  # noqa: PLC0415

    s = score_run([FakeResult(kpi_verdict="fail"), FakeResult(kpi_verdict="pending")])
    assert s.verdict == FAIL


def test_all_unscoreable_run_is_not_applicable_not_pass():
    """The whole point of the four-valued verdict."""
    from engine.verifier import NOT_APPLICABLE, score_run  # noqa: PLC0415

    s = score_run([
        FakeResult(kpi_verdict="not_applicable", detection_id="d1"),
        FakeResult(kpi_verdict="not_applicable", detection_id="d2"),
    ])
    assert s.verdict == NOT_APPLICABLE
    assert s.unscoreable == ["d1", "d2"]
    assert "no measurable threshold" in s.detail


def test_kpi_contribution_weights_the_aggregate():
    from engine.verifier import score_run  # noqa: PLC0415

    s = score_run([
        FakeResult(kpi_verdict="pass", kpi_contribution={"kpi": "x", "op": ">", "value": 0.7}),
        FakeResult(kpi_verdict="pass", kpi_contribution={"kpi": "x", "op": ">", "value": 0.3}),
    ])
    assert s.weighted_total == pytest.approx(1.0)
    assert s.weighted_pass == pytest.approx(1.0)


def test_unweighted_results_count_as_one():
    from engine.verifier import score_run  # noqa: PLC0415

    s = score_run([FakeResult(kpi_verdict="pass"), FakeResult(kpi_verdict="fail")])
    assert s.weighted_total == pytest.approx(2.0)
    assert s.weighted_pass == pytest.approx(1.0)


def test_scenario_threshold_failure_overrides_passing_results():
    """Every detection firing does not mean the test case passed — the run
    still has to clear the scenario's own KPI bar."""
    from engine.verifier import FAIL, score_run  # noqa: PLC0415

    s = score_run(
        [FakeResult(kpi_verdict="pass")],
        threshold={"kpi": "MTTD", "op": "<=", "value": 300, "unit": "s"},
        primary_kpi="MTTD",
        mttd_seconds=900.0,
    )
    assert s.verdict == FAIL
    assert "primary KPI failed" in s.detail
    assert s.primary["actual"] == 900.0


def test_scenario_threshold_alone_can_pass_a_run():
    from engine.verifier import PASS, score_run  # noqa: PLC0415

    s = score_run(
        [],
        threshold={"kpi": "MTTD", "op": "<=", "value": 300, "unit": "s"},
        primary_kpi="MTTD",
        mttd_seconds=42.0,
    )
    assert s.verdict == PASS


def test_qualitative_scenario_threshold_does_not_pass_an_empty_run():
    from engine.verifier import NOT_APPLICABLE, score_run  # noqa: PLC0415

    s = score_run([], threshold="Qualitative pass", primary_kpi="Analyst judgement")
    assert s.verdict == NOT_APPLICABLE


# ---------------------------------------------------------------------------
# verify_run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_run_persists_verdict_onto_the_run():
    from engine.verifier import PASS, verify_run  # noqa: PLC0415

    run, scen = FakeRun(), FakeScenario()
    results = [FakeResult(verification_xql="q", rid=1),
               FakeResult(verification_xql="q", rid=2)]
    score = await verify_run(None, run, scen, results, runner_returning(2))
    assert score.verdict == PASS
    assert run.tc_verdict == PASS
    assert run.tc_verdict_detail["counts"]["pass"] == 2


@pytest.mark.asyncio
async def test_verify_run_scores_mttd_against_the_scenario_threshold():
    from engine.verifier import FAIL, verify_run  # noqa: PLC0415

    run = FakeRun()
    scen = FakeScenario(threshold={"kpi": "MTTD", "op": "<=", "value": 60, "unit": "s"},
                        primary_kpi="MTTD")
    results = [FakeResult(verification_xql="q", mttd_seconds=600.0)]
    score = await verify_run(None, run, scen, results, runner_returning(1))
    assert score.verdict == FAIL
    assert run.tc_verdict == FAIL


@pytest.mark.asyncio
async def test_verify_run_end_to_end_pass_against_a_mocked_tenant():
    """The Phase 2 DoD: a scenario with threshold {MTTD <= 300s} produces a
    run-level pass/fail without touching a real tenant."""
    from engine.verifier import PASS, verify_run  # noqa: PLC0415

    run = FakeRun()
    scen = FakeScenario(threshold={"kpi": "MTTD", "op": "<=", "value": 300, "unit": "s"},
                        primary_kpi="MTTD")
    results = [FakeResult(verification_xql="dataset = xdr_data | filter x", mttd_seconds=120.0)]
    score = await verify_run(None, run, scen, results, runner_returning(5))
    assert score.verdict == PASS
    assert run.tc_verdict_detail["primary"]["verdict"] == PASS


# ---------------------------------------------------------------------------
# xsiam_query_runner
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_xsiam_runner_prefers_the_reported_total():
    from engine.verifier import xsiam_query_runner  # noqa: PLC0415

    class C:
        async def run_xql(self, q, tf):
            return {"number_of_results": 42, "results": [{"a": 1}]}

    assert await xsiam_query_runner(C(), {})("q") == 42


@pytest.mark.asyncio
async def test_xsiam_runner_falls_back_to_row_count():
    from engine.verifier import xsiam_query_runner  # noqa: PLC0415

    class C:
        async def run_xql(self, q, tf):
            return {"results": [{"a": 1}, {"a": 2}]}

    assert await xsiam_query_runner(C(), {})("q") == 2

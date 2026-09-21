"""Sprint 2a/2b — the two measured values the read-back loop can now produce.

Before this, ``score_run`` had exactly one native input (mean MTTD) and 47
Detection-Accuracy scenarios plus 20 correlation scenarios resolved ``pending``
forever. The reconcile loop already computed matched and seeded; it threw the
ratio away.
"""
from __future__ import annotations

from types import SimpleNamespace

from connectors.measures import (
    correlation_rate,
    detection_accuracy,
    guard_measurement,
    kpi_family,
)


def _r(observed, executed=True):
    return SimpleNamespace(observed=observed, executed_at=object() if executed else None)


# ── which family does a scenario's KPI belong to? ──────────────────────────

def test_kpi_family_reads_the_threshold_first_and_the_unit_second():
    assert kpi_family("MTTD", {"kpi": "MTTD", "op": "≤", "unit": "seconds"}) == "mttd"
    assert kpi_family("Detection Accuracy", {"op": "≥", "unit": "%", "value": 90}) == "accuracy"
    # 3 scenarios label an MTTD bar "Detection Accuracy ≤ N seconds": the unit wins.
    assert kpi_family("Detection Accuracy", {"op": "≤", "unit": "seconds", "value": 300}) == "mttd"
    assert kpi_family("Cross-Source Correlation Rate", {"op": "≥", "unit": "%"}) == "correlation"
    assert kpi_family("Stitch Completeness", {"op": "≥", "unit": "%"}) == "correlation"
    assert kpi_family("Causality Chain Completeness", {"op": "≥", "unit": "planes"}) is None
    assert kpi_family("Asset Discovery Coverage", {"op": "≥", "unit": "%"}) is None
    assert kpi_family(None, None) is None


# ── detection accuracy ──────────────────────────────────────────────────────

def test_detection_accuracy_is_observed_over_seeded_percent():
    value, num, den = detection_accuracy([_r(True), _r(True), _r(False), _r(False)])
    assert (value, num, den) == (50.0, 2, 4)


def test_detection_accuracy_ignores_results_that_never_executed():
    value, num, den = detection_accuracy([_r(True), _r(False, executed=False)])
    assert (value, num, den) == (100.0, 1, 1)


def test_detection_accuracy_with_nothing_seeded_is_none_not_zero():
    assert detection_accuracy([]) == (None, 0, 0)


# ── the honesty guard ───────────────────────────────────────────────────────

THRESH = {"kpi": "Detection Accuracy", "op": "≥", "value": 80, "unit": "%"}


def test_guard_passes_a_clean_measurement_through():
    value, reason = guard_measurement(THRESH, 90.0, unscoped=False, truncated=False)
    assert value == 90.0 and reason is None


def test_guard_withholds_a_pass_when_the_pull_was_unscoped():
    """Unscoped means any endpoint's alert could have been credited: a number
    that clears the bar might be someone else's detection. A number that
    fails the bar is safe — extra alerts can only have raised it."""
    value, reason = guard_measurement(THRESH, 90.0, unscoped=True, truncated=False)
    assert value is None and "unscoped" in reason
    value, reason = guard_measurement(THRESH, 40.0, unscoped=True, truncated=False)
    assert value == 40.0 and reason is None


def test_guard_withholds_a_fail_when_the_pull_was_truncated():
    """Truncated means the tenant held more alerts than were fetched: the
    number is a floor. A floor that clears the bar is a pass; a floor below
    the bar proves nothing."""
    value, reason = guard_measurement(THRESH, 40.0, unscoped=False, truncated=True)
    assert value is None and "floor" in reason
    value, reason = guard_measurement(THRESH, 90.0, unscoped=False, truncated=True)
    assert value == 90.0 and reason is None


def test_guard_never_scores_zero_matches_as_a_fail():
    """Zero matched after one reconcile is 'not landed yet', not 'missed'. The
    sweep's attempt cap is what records giving up, and it records `pending`."""
    value, reason = guard_measurement(THRESH, 0.0, unscoped=False, truncated=False,
                                      matched=0)
    assert value is None and "no alert matched" in reason


# ── correlation rate ────────────────────────────────────────────────────────

def test_correlation_rate_is_the_collapse_ratio():
    # 6 alerts into 1 incident → fully correlated
    assert correlation_rate({"a": "I1", "b": "I1", "c": "I1", "d": "I1", "e": "I1", "f": "I1"})[0] == 100.0
    # 6 alerts into 6 incidents → nothing correlated
    assert correlation_rate({k: f"I{k}" for k in "abcdef"})[0] == 0.0
    # 4 alerts into 2 incidents → (4-2)/(4-1)
    v, a, k = correlation_rate({"a": "I1", "b": "I1", "c": "I2", "d": "I2"})
    assert (round(v, 1), a, k) == (66.7, 4, 2)


def test_correlation_rate_needs_at_least_two_alerts_to_mean_anything():
    assert correlation_rate({"a": "I1"})[0] is None
    assert correlation_rate({})[0] is None
    # alerts with no incident id are not evidence either way
    assert correlation_rate({"a": None, "b": None})[0] is None


def test_correlation_rate_from_incident_rows_uses_alert_counts():
    from connectors.measures import correlation_rate_from_incidents
    rows = [SimpleNamespace(incident_id="1", alert_count=5), SimpleNamespace(incident_id="2", alert_count=1)]
    v, a, k = correlation_rate_from_incidents(rows)
    assert (a, k) == (6, 2) and round(v, 1) == 80.0

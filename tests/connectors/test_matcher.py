"""Unit tests for the connector reconciliation matcher (pure, no DB)."""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

from connectors.base import ObservedAlert
from connectors.matcher import reconcile


def _result(rid, *, executed_at, technique=None, detection_id=None,
            expected="", observed=False):
    return SimpleNamespace(
        id=rid, executed_at=executed_at, mitre_technique=technique,
        detection_id=detection_id, expected_detection=expected, observed=observed,
    )


def _alert(*, observed_at, techniques=None, detection_id=None, name=None, ext="A1"):
    return ObservedAlert(
        source="test", observed_at=observed_at, external_id=ext,
        name=name, techniques=techniques or [], detection_id=detection_id,
    )


T0 = datetime(2026, 6, 10, 12, 0, 0)


def test_match_on_technique_sets_mttd():
    results = [_result(1, executed_at=T0, technique="T1003.001")]
    alerts = [_alert(observed_at=T0 + timedelta(seconds=42), techniques=["T1003.001"])]
    [v] = reconcile(results, alerts)
    assert v.matched
    assert v.mttd_seconds == 42.0
    assert "technique" in v.matched_on


def test_match_on_base_technique():
    results = [_result(1, executed_at=T0, technique="T1003.001")]
    alerts = [_alert(observed_at=T0 + timedelta(seconds=10), techniques=["T1003"])]
    [v] = reconcile(results, alerts)
    assert v.matched
    assert "technique-base" in v.matched_on


def test_no_match_when_alert_precedes_execution():
    results = [_result(1, executed_at=T0, technique="T1003")]
    alerts = [_alert(observed_at=T0 - timedelta(seconds=5), techniques=["T1003"])]
    [v] = reconcile(results, alerts)
    assert not v.matched


def test_no_match_outside_window():
    results = [_result(1, executed_at=T0, technique="T1003")]
    alerts = [_alert(observed_at=T0 + timedelta(hours=2), techniques=["T1003"])]
    [v] = reconcile(results, alerts, window_seconds=3600)
    assert not v.matched


def test_time_only_is_insufficient():
    # Alert in window but shares no technique/detection/name → not a match.
    results = [_result(1, executed_at=T0, technique="T1003", expected="LSASS dump")]
    alerts = [_alert(observed_at=T0 + timedelta(seconds=5), techniques=["T9999"],
                     name="totally unrelated thing")]
    [v] = reconcile(results, alerts)
    assert not v.matched


def test_match_on_detection_id():
    results = [_result(1, executed_at=T0, detection_id="bioc-lsass-001")]
    alerts = [_alert(observed_at=T0 + timedelta(seconds=8), detection_id="bioc-lsass-001")]
    [v] = reconcile(results, alerts)
    assert v.matched
    assert "detection_id" in v.matched_on


def test_match_on_name_overlap():
    results = [_result(1, executed_at=T0,
                       expected="Credential file access shadow read attempt")]
    alerts = [_alert(observed_at=T0 + timedelta(seconds=3),
                     name="Shadow file credential access from service account")]
    [v] = reconcile(results, alerts)
    assert v.matched
    assert "name" in v.matched_on


def test_earliest_alert_wins():
    results = [_result(1, executed_at=T0, technique="T1003")]
    alerts = [
        _alert(observed_at=T0 + timedelta(seconds=100), techniques=["T1003"], ext="late"),
        _alert(observed_at=T0 + timedelta(seconds=20), techniques=["T1003"], ext="early"),
    ]
    [v] = reconcile(results, alerts)
    assert v.matched
    assert v.alert_external_id == "early"
    assert v.mttd_seconds == 20.0


def test_already_observed_skipped_by_default():
    results = [_result(1, executed_at=T0, technique="T1003", observed=True)]
    alerts = [_alert(observed_at=T0 + timedelta(seconds=5), techniques=["T1003"])]
    verdicts = reconcile(results, alerts)
    assert verdicts == []  # candidate filtered out


def test_results_without_executed_at_are_skipped():
    results = [_result(1, executed_at=None, technique="T1003")]
    alerts = [_alert(observed_at=T0, techniques=["T1003"])]
    assert reconcile(results, alerts) == []

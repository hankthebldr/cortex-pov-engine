"""Sprint 2c — a step with N expected detections needs N alerts, not one alert N times."""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

from connectors.base import ObservedAlert
from connectors.matcher import reconcile

T0 = datetime(2026, 6, 10, 12, 0, 0)


def _r(rid, *, name=None, technique="T1003.001", signal_type="BIOC"):
    return SimpleNamespace(id=rid, executed_at=T0, mitre_technique=technique,
                           detection_id=None, expected_detection="x", observed=False,
                           signal_type=signal_type, detection_name=name)


def _a(name, *, ext, seconds=30, source="XDR BIOC"):
    return ObservedAlert(source="xsiam", observed_at=T0 + timedelta(seconds=seconds),
                         external_id=ext, name=name, techniques=["T1003.001"], alert_source=source)


def test_exact_rule_name_is_a_strong_key():
    v = reconcile([_r(1, name="LSASS Memory Access")], [_a("lsass memory access", ext="A")])[0]
    assert v.matched and "rule_name" in v.matched_on


def test_one_alert_no_longer_satisfies_two_differently_named_expectations():
    """Both results share the step technique; before, one BIOC alert marked both."""
    results = [_r(1, name="Shadow file read"), _r(2, name="SSH key harvesting")]
    verdicts = {v.result_id: v for v in reconcile(results, [_a("Shadow file read", ext="A")])}
    assert verdicts[1].matched and verdicts[1].alert_external_id == "A"
    assert not verdicts[2].matched


def test_two_expectations_of_the_same_rule_need_two_alerts():
    results = [_r(1, name="Shadow file read"), _r(2, name="Shadow file read")]
    one = reconcile(results, [_a("Shadow file read", ext="A")])
    assert sum(v.matched for v in one) == 1
    two = reconcile(results, [_a("Shadow file read", ext="A"), _a("Shadow file read", ext="B", seconds=40)])
    assert sorted(v.alert_external_id for v in two if v.matched) == ["A", "B"]


def test_results_without_a_rule_name_keep_the_old_technique_behaviour():
    results = [_r(1), _r(2)]
    verdicts = reconcile(results, [_a("anything", ext="A")])
    assert all(v.matched for v in verdicts)

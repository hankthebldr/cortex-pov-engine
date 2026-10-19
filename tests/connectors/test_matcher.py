"""Unit tests for the connector reconciliation matcher (pure, no DB)."""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

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


# --------------------------------------------------------------------------
# detection_id containment must be specific. It used to be a raw substring
# test in both directions, so a tenant rule id of "2" (custom BIOC rule ids are
# small integers), "0", "edr" or "shell" was "contained in" a scenario slug
# like "bioc-edr-002-bash-devtcp-reverse-shell-redirection" — and an
# unrelated alert (different technique, unrelated name) on the same host
# credited the detection with a real-looking MTTD.
# --------------------------------------------------------------------------

_SLUG = "bioc-edr-002-bash-devtcp-reverse-shell-redirection"


@pytest.mark.parametrize("tenant_id", ["2", "0", "002", "edr", "shell", "bioc-edr",
                                       "reverse-shell"])
def test_a_short_or_generic_rule_id_does_not_credit_a_detection(tenant_id):
    results = [_result(1, executed_at=T0, technique="T1059.004", detection_id=_SLUG,
                       expected="Bash devtcp reverse shell redirection")]
    alerts = [_alert(observed_at=T0 + timedelta(seconds=30), techniques=["T1003.001"],
                     detection_id=tenant_id, name="Possible LSASS memory dump")]
    [v] = reconcile(results, alerts)
    assert v.matched is False, f"rule id {tenant_id!r} credited {_SLUG!r} on {v.matched_on}"


def test_digit_runs_are_not_split_into_matches():
    """'bioc-001' is not contained in 'bioc-0011' — different rules."""
    results = [_result(1, executed_at=T0, detection_id="bioc-0011")]
    alerts = [_alert(observed_at=T0 + timedelta(seconds=5), detection_id="bioc-001")]
    [v] = reconcile(results, alerts)
    assert v.matched is False


@pytest.mark.parametrize("tenant_id", [
    _SLUG,                                   # exact
    _SLUG.upper(),                           # case-folded exact
    f"cortexsim-{_SLUG}",                    # tenant prefixed the slug
    f"{_SLUG}-v2",                           # tenant versioned the rule
    f"CortexSim: {_SLUG} (copy)",            # rule named after the slug
])
def test_the_slug_embedded_in_a_tenant_rule_id_still_matches(tenant_id):
    results = [_result(1, executed_at=T0, detection_id=_SLUG)]
    alerts = [_alert(observed_at=T0 + timedelta(seconds=5), detection_id=tenant_id)]
    [v] = reconcile(results, alerts)
    assert v.matched and "detection_id" in v.matched_on


def test_an_exact_numeric_rule_id_still_matches():
    results = [_result(1, executed_at=T0, detection_id="1234")]
    alerts = [_alert(observed_at=T0 + timedelta(seconds=5), detection_id="1234")]
    [v] = reconcile(results, alerts)
    assert v.matched and v.matched_on == ["detection_id"]

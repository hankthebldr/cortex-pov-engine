"""The ``prevention`` family: the XDR agent's own modules are not a rule family."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from connectors.base import ObservedAlert
from connectors.matcher import reconcile, signal_family

T0 = datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc)


class _R:
    def __init__(self, rid, *, signal_type="BIOC", technique="T1003.001"):
        self.id = rid
        self.mitre_technique = technique
        self.detection_id = None
        self.expected_detection = "LSASS memory access"
        self.signal_type = signal_type
        self.executed_at = T0
        self.observed = False


def _a(*, source, seconds=30, **kw):
    return ObservedAlert(source="xsiam", observed_at=T0 + timedelta(seconds=seconds),
                         external_id="A1", techniques=["T1003.001"], alert_source=source, **kw)


def test_xdr_agent_source_is_prevention_family():
    assert signal_family("XDR Agent") == "prevention"
    assert signal_family("Prevention") == "prevention"
    assert signal_family("xdr agent") == "prevention"
    assert signal_family("XDR BIOC") == "bioc"          # unchanged


def test_prevention_alert_cannot_credit_bioc_expectation():
    v = reconcile([_R(1, signal_type="BIOC")], [_a(source="XDR Agent")])
    assert not v[0].matched


def test_bioc_alert_cannot_credit_prevention_expectation():
    v = reconcile([_R(1, signal_type="Prevention")], [_a(source="XDR BIOC")])
    assert not v[0].matched


def test_prevention_expectation_matches_agent_alert_and_carries_action():
    alert = _a(source="XDR Agent", action_pretty="Prevented (Blocked)", action="BLOCKED")
    v = reconcile([_R(1, signal_type="Prevention")], [alert])
    assert v[0].matched and "source" in v[0].matched_on
    assert v[0].alert_outcome == "prevented"
    assert v[0].alert_action == "Prevented (Blocked)"
    d = v[0].to_dict()
    assert d["alert_outcome"] == "prevented" and d["alert_action"] == "Prevented (Blocked)"


def test_alert_action_falls_back_to_raw_action():
    v = reconcile([_R(1, signal_type="Prevention")],
                  [_a(source="XDR Agent", action="BLOCKED")])
    assert v[0].alert_action == "BLOCKED" and v[0].alert_outcome == "prevented"


def test_unmatched_verdict_has_no_action():
    v = reconcile([_R(1, signal_type="BIOC")], [_a(source="XDR Agent")])
    assert v[0].alert_action is None and v[0].alert_outcome is None


def test_per_type_window_widens_analytics_only():
    late = _a(source=None, seconds=5000)
    both = [_R(1, signal_type="Analytics"), _R(2, signal_type="BIOC")]
    base = reconcile(both, [late])
    assert not base[0].matched and not base[1].matched
    wide = reconcile(both, [late], window_seconds_by_type={"Analytics": 7200})
    by = {x.result_id: x for x in wide}
    assert by[1].matched and not by[2].matched

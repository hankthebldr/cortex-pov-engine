"""Source-aware matching, host scoping, multi-technique extraction (audit items 2, 4, 6)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from connectors.base import ConnectorConfig, ObservedAlert
from connectors.matcher import reconcile, scope_to_host, signal_family
from connectors.xsiam import XsiamConnector

T0 = datetime(2026, 6, 10, 12, 0, 0)


class _R:
    def __init__(self, rid, *, technique="T1003.001", signal_type="BIOC",
                 expected="LSASS memory access", executed_at=T0):
        self.id = rid
        self.mitre_technique = technique
        self.detection_id = None
        self.expected_detection = expected
        self.signal_type = signal_type
        self.executed_at = executed_at
        self.observed = False


def _a(*, seconds=30, source=None, host=None, techniques=("T1003.001",), ext="A1", name=None):
    return ObservedAlert(source="xsiam", observed_at=T0 + timedelta(seconds=seconds),
                         external_id=ext, name=name, techniques=list(techniques),
                         host=host, alert_source=source)


# ── source families ─────────────────────────────────────────────────────────

def test_signal_family_vocabulary():
    assert signal_family("XDR BIOC") == "bioc"
    assert signal_family("XDR Analytics BIOC") == "abioc"
    assert signal_family("XDR Analytics") == "analytics"
    assert signal_family("Correlation") == "correlation"
    assert signal_family("XDR IOC") == "ioc"
    assert signal_family("XDR Agent") is None          # not a rule family
    assert signal_family(None) is None
    # Result.signal_type side
    assert signal_family("ABIOC") == "abioc"
    assert signal_family("XQL") == "correlation"      # an XQL only alerts via a correlation rule


def test_a_bioc_alert_does_not_satisfy_a_correlation_expectation():
    """One BIOC firing used to mark BIOC + XQL + Correlation all observed."""
    results = [_R(1, signal_type="BIOC"), _R(2, signal_type="Correlation"), _R(3, signal_type="ABIOC")]
    verdicts = reconcile(results, [_a(source="XDR BIOC")])
    by = {v.result_id: v for v in verdicts}
    assert by[1].matched and "source" in by[1].matched_on
    assert not by[2].matched
    assert not by[3].matched


def test_an_unknown_source_is_unconstrained_but_recorded():
    results = [_R(1, signal_type="Correlation")]
    verdicts = reconcile(results, [_a(source="XDR Agent")])
    assert verdicts[0].matched
    assert "source" not in verdicts[0].matched_on
    assert verdicts[0].alert_source == "XDR Agent"


# ── host scoping ────────────────────────────────────────────────────────────

def test_scope_to_host_drops_other_hosts_keeps_hostless():
    alerts = [_a(host="web-01", ext="same"), _a(host="db-02.corp.local", ext="other"),
              _a(host=None, ext="none")]
    kept, scope = scope_to_host(alerts, "WEB-01.corp.local")
    assert [a.external_id for a in kept] == ["same", "none"]
    assert scope == {"host": "web-01", "eligible": 2, "excluded_other_host": 1,
                     "without_host": 1}


def test_scope_to_host_without_a_host_is_a_no_op_that_says_so():
    alerts = [_a(host="x"), _a(host="y")]
    kept, scope = scope_to_host(alerts, None)
    assert kept == alerts
    assert scope["host"] is None and scope["eligible"] == 2


def test_an_earlier_alert_from_another_host_no_longer_wins_mttd():
    results = [_R(1)]
    other_first = [_a(seconds=10, host="db-02", ext="other"), _a(seconds=40, host="web-01", ext="mine")]
    kept, _ = scope_to_host(other_first, "web-01")
    v = reconcile(results, kept)[0]
    assert v.matched and v.alert_external_id == "mine" and v.mttd_seconds == 40.0


# ── connector-level normalisation ───────────────────────────────────────────

def _pull(alert):
    def fetcher(method, url, headers, body, timeout):
        return 200, json.dumps({"reply": {"total_count": 1, "alerts": [alert]}})
    cfg = ConnectorConfig(integration_name="x", secret="k", config={
        "fqdn": "api-t.xdr.us.paloaltonetworks.com", "api_key_id": "1"})
    r = XsiamConnector(fetcher=fetcher).pull(cfg, since=T0, until=T0 + timedelta(hours=1))
    assert r.ok, r.error
    return r.observations[0]


def test_multi_technique_string_keeps_every_id():
    obs = _pull({"alert_id": "1", "detection_timestamp": int(T0.timestamp() * 1000) + 1000,
                 "mitre_technique_id_and_name": "T1059.001 - PowerShell, T1003.008 - /etc/passwd"})
    assert obs.techniques == ["T1059.001", "T1003.008"]


def test_source_and_matching_service_rule_id_are_captured():
    obs = _pull({"alert_id": "1", "detection_timestamp": int(T0.timestamp() * 1000) + 1000,
                 "source": "XDR Analytics BIOC", "matching_service_rule_id": "abioc-77",
                 "category": "Credential Access"})
    assert obs.alert_source == "XDR Analytics BIOC"
    assert obs.detection_id == "abioc-77"
    assert obs.to_dict()["alert_source"] == "XDR Analytics BIOC"

"""Grouping-key analysis (spec §5.1-5.3, plan Task C1).

Each scenario is a small inline dict over a fixture card corpus written to
``tmp_path``, so the derivation rules are pinned independently of the real
corpus (which today carries no ``alert_fields`` at all).
"""
from __future__ import annotations

import json

import pytest

from engine.grouping_keys import analyze_scenario, detection_keys
from engine.ttp_catalog import TtpCatalog

TTP = "TTP-T-0001"


def _rule(rule_id, severity=None, alert_fields=None):
    r = {"rule_id": rule_id, "name": rule_id}
    if severity is not None:
        r["severity"] = severity
    if alert_fields is not None:
        r["alert_fields"] = alert_fields
    return r


@pytest.fixture
def catalog(tmp_path):
    card = {
        "id": TTP,
        "status": "active",
        "detections": {
            "biocs": [
                {"name": "med a", "detection_id": "bioc-med-a", "severity": "Medium"},
                {"name": "high b", "detection_id": "bioc-high-b", "severity": "high"},
                {"name": "low c", "detection_id": "bioc-low-c", "severity": "Low"},
                {"name": "no sev", "detection_id": "bioc-nosev"},
            ],
            "xql_queries": [{"name": "hunt q", "detection_id": "xql-hunt-q", "query": "dataset = xdr_data"}],
            "correlation_rules": [
                _rule("CR-HOST-MED", "medium", {"agent_hostname": "host_name"}),
                _rule("CR-HOST-NOSEV", None, {"agent_hostname": "host_name"}),
                _rule("CR-USER-HIGH", "high", {"actor_effective_username": "user_name"}),
                _rule("CR-IP-MED", "medium", {"action_remote_ip": "remote_ip"}),
                _rule("CR-CUSTOM", "medium",
                      {"Suspicious_User": "user_principal", "agent_hostname": "host_name"}),
                _rule("CR-NOAF", "medium"),
            ],
        },
    }
    (tmp_path / f"{TTP}.json").write_text(json.dumps(card), encoding="utf-8")
    cat = TtpCatalog()
    cat.load(str(tmp_path))
    return cat


def det(dtype, detection_id, plane="EDR"):
    return {"plane": plane, "type": dtype, "description": detection_id,
            "ttp_ref": TTP, "detection_id": detection_id}


def scen(*steps, **extra):
    return {"scenario_id": "SIM-T-001",
            "steps": [{"id": f"step-{i + 1:02d}", "expected_detections": list(dets)}
                      for i, dets in enumerate(steps)],
            **extra}


def codes(findings):
    return sorted(f.code for f in findings)


def test_bioc_medium_pair_shares_host_no_finding(catalog):
    out = analyze_scenario(scen([det("BIOC", "bioc-med-a")], [det("BIOC", "bioc-high-b")]), catalog)
    assert out == []
    keys = detection_keys("step-01", det("BIOC", "bioc-med-a"), catalog)
    assert keys.keys_known and keys.key_classes == frozenset({"host", "user"})
    assert keys.severity == "medium"


def test_xql_detection_yields_g04_not_silence(catalog):
    out = analyze_scenario(scen([det("XQL", "xql-hunt-q")]), catalog)
    assert codes(out) == ["G-04"]
    (f,) = out
    assert f.level == "INFO" and f.step_id == "step-01"
    assert "GROUPING_KEY_UNKNOWN" in f.detail and "xql-hunt-q" in f.detail


def test_unknown_severity_yields_g04_and_is_excluded_from_g01(catalog):
    # Included in the comparison, CR-HOST-NOSEV ({host}) and CR-USER-HIGH
    # ({user}) share no key and would be a G-01. Unknown severity keeps it out.
    out = analyze_scenario(scen([det("Correlation", "CR-HOST-NOSEV")],
                                [det("Correlation", "CR-USER-HIGH")]), catalog)
    assert codes(out) == ["G-04"]
    assert out[0].step_id == "step-01"


def test_two_medium_no_shared_key_is_g01(catalog):
    out = analyze_scenario(scen([det("Correlation", "CR-HOST-MED")],
                                [det("Correlation", "CR-USER-HIGH")]), catalog)
    assert codes(out) == ["G-01"]
    assert out[0].level == "WARN" and out[0].step_id is None


def test_ip_vs_host_is_g05_bridgeable_not_g01(catalog):
    out = analyze_scenario(scen([det("BIOC", "bioc-high-b")],
                                [det("Correlation", "CR-IP-MED", plane="NDR")]), catalog)
    assert codes(out) == ["G-05"]
    assert out[0].level == "INFO" and "BRIDGEABLE_IP_HOST" in out[0].detail


def test_stitch_scenario_all_low_is_g02(catalog):
    s = scen([det("BIOC", "bioc-low-c")], [det("BIOC", "bioc-high-b")],
             required_planes_in_incident=["EDR", "NDR"])
    out = analyze_scenario(s, catalog)
    g02 = [f for f in out if f.code == "G-02"]
    assert [f.step_id for f in g02] == ["step-01"]
    assert g02[0].level == "WARN"


def test_correlation_alert_fields_custom_destination_is_g03(catalog):
    s = scen([det("Correlation", "CR-CUSTOM")], stitching_key="user_principal")
    out = analyze_scenario(s, catalog)
    g03 = [f for f in out if f.code == "G-03"]
    assert len(g03) == 1
    assert g03[0].level == "ERROR" and g03[0].step_id == "step-01"
    assert "Suspicious_User" in g03[0].detail


def test_g03_dormant_without_alert_fields(catalog):
    s = scen([det("Correlation", "CR-NOAF")], stitching_key="user_principal")
    out = analyze_scenario(s, catalog)
    assert "G-03" not in codes(out)
    assert codes(out) == ["G-04"]

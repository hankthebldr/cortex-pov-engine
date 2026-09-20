"""The ``alert_shape`` preflight rung (audit §5 item 1).

"Will matching work?" used to be answerable only by running a POV and reading
zero matches. This rung samples ONE alert and reports which of the keys the
connector reads are actually present on this tenant's alert objects.
"""
from __future__ import annotations

import json

from connectors import preflight as pf

_CFG = {"fqdn": "api-t.xdr.us.paloaltonetworks.com", "api_key_id": "1"}


def _stage(report, sid):
    return next(s for s in report.stages if s.id == sid)


def _tenant(alert_or_none):
    calls = []

    def fetcher(method, url, headers, body, timeout):
        calls.append(json.loads(body)["request_data"])
        alerts = [alert_or_none] if alert_or_none else []
        return 200, json.dumps({"reply": {"total_count": len(alerts), "alerts": alerts}})

    return fetcher, calls


_FULL = {"alert_id": "1", "name": "LSASS Memory Access", "source": "XDR BIOC",
         "detection_timestamp": 1780000000000, "host_name": "web-01",
         "mitre_technique_id_and_name": ["T1003.001 - LSASS Memory"],
         "matching_service_rule_id": "bioc-12"}


def test_a_full_alert_reads_ok_and_names_what_was_found():
    fetcher, calls = _tenant(_FULL)
    r = pf.preflight_reconcile(_CFG, "k", fetcher=fetcher)
    s = _stage(r, "alert_shape")
    assert s.status == pf.OK and s.code == pf.PF_OK
    assert s.extra["keys"]["technique"] == "mitre_technique_id_and_name"
    assert s.extra["keys"]["rule_id"] == "matching_service_rule_id"
    assert s.extra["keys"]["host"] == "host_name"
    assert s.extra["source"] == "XDR BIOC"
    # the 1-minute scope probe already returned an alert: no second call.
    assert len(calls) == 1 and r.queries_issued == 1


def test_missing_rule_id_key_is_degraded_and_says_matching_rests_on_technique_and_name():
    alert = {k: v for k, v in _FULL.items() if k != "matching_service_rule_id"}
    fetcher, _ = _tenant(alert)
    r = pf.preflight_reconcile(_CFG, "k", fetcher=fetcher)
    s = _stage(r, "alert_shape")
    assert s.status == pf.DEGRADED and s.code == pf.PF_ALERT_SHAPE_PARTIAL
    assert s.extra["keys"]["rule_id"] is None
    assert "technique" in s.remediation and "name" in s.remediation
    assert r.overall == pf.DEGRADED


def test_an_alert_with_neither_technique_nor_name_is_blocked():
    fetcher, _ = _tenant({"alert_id": "1", "detection_timestamp": 1780000000000})
    r = pf.preflight_reconcile(_CFG, "k", fetcher=fetcher)
    s = _stage(r, "alert_shape")
    assert s.status == pf.BLOCKED and s.code == pf.PF_ALERT_SHAPE_UNMATCHABLE
    assert "read_alerts" in r.capabilities_confirmed   # auth/scope still fine
    assert r.overall == pf.NOT_READY


def test_no_alert_in_lookback_is_unknown_not_ok():
    fetcher, calls = _tenant(None)
    r = pf.preflight_reconcile(_CFG, "k", fetcher=fetcher)
    s = _stage(r, "alert_shape")
    assert s.status == pf.DEGRADED and s.code == pf.PF_ALERT_SHAPE_UNKNOWN
    # one scope probe + one 7-day lookback, both 1-row
    assert len(calls) == 2 and r.queries_issued == 2
    assert all(c["search_to"] == 1 for c in calls)

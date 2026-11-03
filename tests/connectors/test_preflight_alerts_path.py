"""Preflight probes the OTHER alerts API version before calling a tenant empty.

The reconcile connector reads ``/public_api/v1/alerts/get_alerts_multi_events``
unless ``config["alerts_path"]`` says otherwise; the vendor lists v1 as legacy.
cortex-bot, a field-built MCP server, reads alerts on **v2** against a live
tenant. Two failure modes follow, and only one of them was loud:

* **Loud.** v1 is gone (404). The pull fails, reconcile refuses to score, the
  DC sees an upstream error with no hint that one config value fixes it.
* **Quiet.** v1 answers 200 with nothing while v2 holds the alerts. Reconcile
  "works", matches nothing, and every seeded detection reads ``missed`` — a
  manufactured false negative on the customer's stack, in a POV report.

So when the configured path errors with a path-shaped status, or answers empty
over the whole lookback, preflight spends ONE more 1-row query on the other
version. If that one has alerts, the DC is told exactly which value to set.
Preflight never switches the path itself: the connector keeps reading what the
credential says, so a working setup cannot change under a DC mid-POV.
"""
from __future__ import annotations

import json

from connectors import preflight as pf

_CFG = {"fqdn": "api-t.xdr.us.paloaltonetworks.com", "api_key_id": "1"}
V1 = "/public_api/v1/alerts/get_alerts_multi_events"
V2 = "/public_api/v2/alerts/get_alerts_multi_events"

_ALERT = {"alert_id": "1", "name": "LSASS Memory Access", "source": "XDR BIOC",
          "detection_timestamp": 1780000000000, "host_name": "web-01",
          "mitre_technique_id_and_name": ["T1003.001 - LSASS Memory"],
          "matching_service_rule_id": "bioc-12",
          "action_pretty": "Detected (Reported)"}


def _stage(report, sid):
    return next(s for s in report.stages if s.id == sid)


def _tenant(routes):
    """routes: {path: (status, alerts-list-or-text)}; records every path called."""
    calls: list[str] = []

    def fetcher(method, url, headers, body, timeout):
        path = "/" + url.split("://", 1)[1].split("/", 1)[1]
        calls.append(path)
        status, payload = routes[path]
        if status != 200:
            return status, payload
        return 200, json.dumps({"reply": {"total_count": len(payload), "alerts": payload}})

    return fetcher, calls


# ── loud: the configured path does not exist on this tenant ─────────────────

def test_v1_404_with_v2_answering_names_the_value_to_set():
    fetcher, calls = _tenant({V1: (404, "not found"), V2: (200, [_ALERT])})
    r = pf.preflight_reconcile(_CFG, "k", fetcher=fetcher)

    scope = _stage(r, "scope_alerts")
    assert scope.status == pf.BLOCKED
    assert scope.code == pf.PF_ALERTS_PATH_MISMATCH
    assert scope.extra["configured_path"] == V1
    assert scope.extra["working_path"] == V2
    assert "alerts_path" in scope.remediation and V2 in scope.remediation
    assert calls == [V1, V2]
    assert r.queries_issued == 2
    # The key CAN read alerts, just not where reconcile looks: neither list
    # may claim the capability one way or the other.
    assert "read_alerts" not in r.capabilities_confirmed
    assert "read_alerts" not in r.capabilities_denied
    assert r.overall == pf.BLOCKED


def test_both_versions_404_stays_an_upstream_error():
    fetcher, calls = _tenant({V1: (404, "not found"), V2: (404, "not found")})
    r = pf.preflight_reconcile(_CFG, "k", fetcher=fetcher)

    assert _stage(r, "scope_alerts").code == pf.PF_UPSTREAM_ERROR
    assert calls == [V1, V2]
    assert r.queries_issued == 2
    assert r.capabilities_denied == ["read_alerts"]


def test_a_configured_v2_that_404s_points_back_to_v1():
    fetcher, calls = _tenant({V2: (404, "not found"), V1: (200, [_ALERT])})
    r = pf.preflight_reconcile({**_CFG, "alerts_path": V2}, "k", fetcher=fetcher)

    scope = _stage(r, "scope_alerts")
    assert scope.code == pf.PF_ALERTS_PATH_MISMATCH
    assert scope.extra["working_path"] == V1
    assert calls == [V2, V1]


def test_role_and_quota_failures_never_spend_a_probe_on_the_other_path():
    """403 and 429 say nothing about the path; probing would just burn quota."""
    for status in (401, 403, 429, 500):
        fetcher, calls = _tenant({V1: (status, "no"), V2: (200, [_ALERT])})
        r = pf.preflight_reconcile(_CFG, "k", fetcher=fetcher)
        assert calls == [V1], f"HTTP {status} must not trigger the alternate probe"
        assert r.queries_issued == 1


def test_a_custom_path_has_no_alternate_to_probe():
    custom = "/public_api/v1/alerts/get_alerts"
    fetcher, calls = _tenant({custom: (404, "not found"), V2: (200, [_ALERT])})
    r = pf.preflight_reconcile({**_CFG, "alerts_path": custom}, "k", fetcher=fetcher)

    assert calls == [custom]
    assert _stage(r, "scope_alerts").code == pf.PF_UPSTREAM_ERROR


# ── quiet: the configured path answers, with nothing in it ──────────────────

def test_v1_empty_while_v2_has_alerts_is_blocked_not_unknown():
    """The manufactured-false-negative case. Unknown would be a lie of omission."""
    fetcher, calls = _tenant({V1: (200, []), V2: (200, [_ALERT])})
    r = pf.preflight_reconcile(_CFG, "k", fetcher=fetcher)

    assert _stage(r, "scope_alerts").status == pf.OK, "the call itself worked"
    shape = _stage(r, "alert_shape")
    assert shape.status == pf.BLOCKED
    assert shape.code == pf.PF_ALERTS_PATH_MISMATCH
    assert shape.extra["configured_path"] == V1
    assert shape.extra["working_path"] == V2
    assert "missed" in shape.remediation
    assert calls == [V1, V1, V2], "scope probe, lookback, then ONE alternate probe"
    assert r.queries_issued == 3
    assert "read_alerts" not in r.capabilities_confirmed
    assert r.overall == pf.BLOCKED


def test_both_versions_empty_stays_unknown_and_costs_one_more_query():
    fetcher, calls = _tenant({V1: (200, []), V2: (200, [])})
    r = pf.preflight_reconcile(_CFG, "k", fetcher=fetcher)

    assert _stage(r, "alert_shape").code == pf.PF_ALERT_SHAPE_UNKNOWN
    assert calls == [V1, V1, V2]
    assert r.queries_issued == 3
    assert r.capabilities_confirmed == ["read_alerts"]


def test_a_tenant_with_alerts_on_the_configured_path_spends_nothing_extra():
    fetcher, calls = _tenant({V1: (200, [_ALERT]), V2: (200, [_ALERT])})
    r = pf.preflight_reconcile(_CFG, "k", fetcher=fetcher)

    assert calls == [V1]
    assert r.queries_issued == 1
    assert r.overall == pf.READY

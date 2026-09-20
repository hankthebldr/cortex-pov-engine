"""Pagination + truncation accounting for the XSIAM alert pull (audit item 1).

Before this change the connector asked for ONE page of 100 alerts, sorted
ascending, over the whole run window with no host filter — so on a busy tenant
the run's own (newest) alerts were the ones silently dropped, and nothing in
the summary said so. ``reply.total_count`` was never read.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from connectors.base import ConnectorConfig
from connectors.xsiam import XsiamConnector

SINCE = datetime(2026, 6, 10, 12, 0, 0)
UNTIL = datetime(2026, 6, 10, 13, 0, 0)


def _cfg(**config):
    base = {"fqdn": "api-tenant.xdr.us.paloaltonetworks.com",
            "api_key_id": "42", "auth_mode": "standard"}
    base.update(config)
    return ConnectorConfig(integration_name="xsiam-prod", config=base, secret="k")


def _alert(i):
    return {"alert_id": str(i), "name": f"alert {i}",
            "detection_timestamp": int((SINCE + timedelta(seconds=i)).timestamp() * 1000),
            "mitre_technique_ids": ["T1003"]}


def _tenant(total, *, total_count=None):
    """A tenant holding ``total`` alerts that honours search_from/search_to."""
    calls: list[dict] = []

    def fetcher(method, url, headers, body, timeout):
        rd = json.loads(body)["request_data"]
        calls.append(rd)
        page = [_alert(i) for i in range(total)][rd["search_from"]:rd["search_to"]]
        return 200, json.dumps({"reply": {
            "total_count": total if total_count is None else total_count,
            "result_count": len(page), "alerts": page}})

    return fetcher, calls


def test_pull_pages_until_total_count_is_satisfied():
    fetcher, calls = _tenant(150)
    result = XsiamConnector(fetcher=fetcher).pull(_cfg(), since=SINCE, until=UNTIL)
    assert result.ok, result.error
    assert len(result.observations) == 150
    assert [c["search_from"] for c in calls] == [0, 100]
    assert result.detail["pages"] == 2
    assert result.detail["total_count"] == 150
    assert result.detail.get("truncated") is False


def test_page_cap_marks_the_pull_truncated_not_complete():
    fetcher, calls = _tenant(150)
    result = XsiamConnector(fetcher=fetcher).pull(
        _cfg(max_pages=1), since=SINCE, until=UNTIL)
    assert result.ok
    assert len(result.observations) == 100
    assert len(calls) == 1
    assert result.detail["truncated"] is True
    t = result.detail["truncation"]
    assert t["total_count"] == 150 and t["returned"] == 100
    assert "floor" in t["consequence"]


def test_capped_total_count_string_is_read_as_truncation():
    """Cortex caps total_count at "9,999+" — a string, not an int."""
    fetcher, _ = _tenant(100, total_count="9,999+")
    result = XsiamConnector(fetcher=fetcher).pull(
        _cfg(max_pages=1), since=SINCE, until=UNTIL)
    assert result.ok
    assert result.detail["truncated"] is True
    assert result.detail["total_count_capped"] is True


def test_an_explicit_limit_issues_exactly_one_query():
    """Preflight probes with limit=1 and must never be turned into a harvest."""
    fetcher, calls = _tenant(500)
    result = XsiamConnector(fetcher=fetcher).pull(
        _cfg(), since=SINCE, until=UNTIL, filters={"limit": 1})
    assert result.ok
    assert len(calls) == 1
    assert len(result.observations) == 1
    assert result.detail["truncated"] is True


def test_a_page_error_mid_harvest_fails_the_pull_not_the_page():
    """A 429 on page 2 must not return page 1 as if it were the whole tenant."""
    fetcher, calls = _tenant(150)

    def flaky(method, url, headers, body, timeout):
        if json.loads(body)["request_data"]["search_from"] > 0:
            return 429, "{}"
        return fetcher(method, url, headers, body, timeout)

    result = XsiamConnector(fetcher=flaky).pull(_cfg(), since=SINCE, until=UNTIL)
    assert not result.ok
    assert result.code == "XSIAM_QUOTA_ERROR"
    assert result.observations == []

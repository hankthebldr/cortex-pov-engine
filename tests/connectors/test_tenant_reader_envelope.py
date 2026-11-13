"""Strict issue and case reads over one body builder (spec §7.3, envelope rules).

Issues and cases answer in ``reply.DATA`` with ``TOTAL_COUNT`` (tenant-wide) and
``FILTER_COUNT`` (the filter's match count). A reply without ``DATA`` is an
envelope this connector does not recognise, never "no rows"; a missing
``FILTER_COUNT`` is an unknown, so ``truncated`` is null, never False.
Transports are injected (``FakeTenant``); nothing reaches a tenant.
"""
from __future__ import annotations

import pytest

from connectors.base import ConnectorConfig
from connectors.xsiam import IssueReadError, XsiamConnector
from integrations.xsiam import codes, queries
from tests.connectors.tenant_fakes import OMIT, FakeTenant, fixture

CFG = ConnectorConfig(
    integration_name="t",
    config={"base_url": "https://api-lab.xdr.us.paloaltonetworks.com", "api_key_id": "7"},
    secret="k",
)

LOWERCASE_DATA = {"reply": {"data": [], "total_count": 0}}
SORT = {"field": "observation_time", "keyword": "asc"}
WINDOW = [{"field": "_insert_time", "operator": "gte", "value": 1762000000000},
          {"field": "_insert_time", "operator": "lte", "value": 1762003600000}]


def _issue(i: int, **kw) -> dict:
    return {"id": 1000 + i, "_insert_time": 1762000000000 + i * 1000,
            "detection.method": "TRAPS", "domain": "Security",
            "status.progress": "New", "case_ids": [500], **kw}


def _window_body(search_from: int, page_size: int) -> dict:
    return queries.issue_search_body(filters=WINDOW, search_from=search_from,
                                     search_to=search_from + page_size, sort=SORT,
                                     include_fields=["normalized_fields"])


def _case_body(search_from: int, page_size: int) -> dict:
    return queries.case_search_body(
        filters=[{"field": "case_id", "operator": "in", "value": [501, 502]}],
        search_from=search_from, search_to=search_from + page_size)


def _conn(tenant: FakeTenant) -> XsiamConnector:
    return XsiamConnector(fetcher=tenant)


# ── the brief's red-before tests ───────────────────────────────────────────

def test_by_id_issue_read_with_lowercase_data_raises_not_empty():
    tenant = FakeTenant(replies={"issues_by_id": LOWERCASE_DATA})
    with pytest.raises(IssueReadError) as ei:
        _conn(tenant).pull_issue_status(CFG, ["1001"])
    assert ei.value.code == codes.XSIAM_PARSE_ERROR
    assert "XSIAM_ENVELOPE_UNRECOGNISED" in str(ei.value)


def test_issue_search_lowercase_data_is_parse_error_naming_envelope():
    res = _conn(FakeTenant(replies={"issues": LOWERCASE_DATA})).search_issues(
        CFG, body_for=_window_body)
    assert res.ok is False and res.code == codes.XSIAM_PARSE_ERROR
    assert "XSIAM_ENVELOPE_UNRECOGNISED" in res.error
    assert "'data'" in res.error
    assert res.observations == []


def test_case_search_lowercase_data_is_parse_error():
    res = _conn(FakeTenant(replies={"cases": LOWERCASE_DATA})).search_cases(
        CFG, body_for=_case_body)
    assert res.ok is False and res.code == codes.XSIAM_PARSE_ERROR
    assert "XSIAM_ENVELOPE_UNRECOGNISED" in res.error
    assert res.incidents == [] and res.observations == []


def test_missing_filter_count_gives_truncated_null_never_false():
    tenant = FakeTenant(issues=[_issue(1)], filter_count=OMIT)
    res = _conn(tenant).search_issues(CFG, body_for=_window_body)
    assert res.ok, res.error
    assert res.detail["filter_count"] is None
    assert res.detail["truncated"] is None
    assert res.detail["total_count"] == 4200


def test_filter_count_above_returned_is_truncated_true():
    tenant = FakeTenant(issues=[_issue(i) for i in range(250)])
    res = _conn(tenant).search_issues(CFG, body_for=_window_body)
    assert res.ok, res.error
    assert len(res.observations) == 100 and len(tenant.calls["issues"]) == 1
    assert res.detail["filter_count"] == 250
    assert res.detail["truncated"] is True
    assert res.detail["truncation"]["filter_count"] == 250


def test_issue_search_body_shape():
    full = queries.issue_search_body(filters=WINDOW, search_from=100, search_to=200,
                                     sort=SORT, include_fields=["normalized_fields"])
    assert full == {"request_data": {
        "filters": WINDOW, "search_from": 100, "search_to": 200,
        "sort": {"field": "observation_time", "keyword": "asc"},
        "include_fields": ["normalized_fields"]}}
    bare = queries.issue_search_body(filters=[], search_from=0, search_to=100)
    assert bare == {"request_data": {"filters": [], "search_from": 0, "search_to": 100}}
    assert "sort" not in bare["request_data"] and "include_fields" not in bare["request_data"]


def test_recorded_issue_fixture_parses_strictly():
    tenant = FakeTenant(replies={"issues": fixture("issue_search_reply.json")})
    res = _conn(tenant).search_issues(CFG, body_for=_window_body)
    assert res.ok, res.error
    assert [r["id"] for r in res.observations] == [1001, 1002, 1003]
    assert res.detail["filter_count"] == 3
    assert res.detail["total_count"] == 4200
    assert res.detail["truncated"] is False


# ── behaviour around the strict reads ──────────────────────────────────────

def test_tenant_wide_total_count_is_not_read_as_truncation():
    """TOTAL_COUNT counts the whole tenant; only FILTER_COUNT speaks for the window."""
    tenant = FakeTenant(issues=[_issue(i) for i in range(3)], tenant_total=37023)
    res = _conn(tenant).search_issues(CFG, body_for=_window_body, max_pages=5)
    assert res.ok and len(res.observations) == 3
    assert res.detail["total_count"] == 37023 and res.detail["truncated"] is False


def test_window_paging_stops_at_filter_count():
    tenant = FakeTenant(issues=[_issue(i) for i in range(200)])
    res = _conn(tenant).search_issues(CFG, body_for=_window_body, max_pages=5)
    assert res.ok and len(res.observations) == 200
    assert [c["search_from"] for c in tenant.calls["issues"]] == [0, 100]
    assert tenant.calls["issues"][1] == _window_body(100, 100)["request_data"]
    assert res.detail["pages"] == 2 and res.detail["truncated"] is False


def test_explicit_limit_is_one_page_of_that_size():
    tenant = FakeTenant(issues=[_issue(i) for i in range(30)])
    res = _conn(tenant).search_issues(CFG, body_for=_window_body, limit=5, max_pages=5)
    assert res.ok and len(res.observations) == 5 and len(tenant.calls["issues"]) == 1
    assert tenant.calls["issues"][0]["search_to"] == 5
    assert res.detail["truncated"] is True


def test_search_issues_normalize_shapes_rows():
    tenant = FakeTenant(issues=[_issue(1), _issue(2)])
    res = _conn(tenant).search_issues(CFG, body_for=_window_body,
                                      normalize=lambda r: str(r["id"]))
    assert res.observations == ["1001", "1002"]


def test_issues_by_id_sends_ints_in_chunks_of_100_over_the_shared_builder():
    ids = [str(1000 + i) for i in range(229)] + ["abc"]
    tenant = FakeTenant(issues=[_issue(1), _issue(150)])
    res = _conn(tenant).issues_by_id(CFG, ids)
    assert res.ok, res.error
    calls = tenant.calls["issues_by_id"]
    assert [len(c["filters"][0]["value"]) for c in calls] == [100, 100, 30]
    assert calls[0] == queries.issue_search_body(
        filters=[{"field": "id", "operator": "in", "value": list(range(1000, 1100))}],
        search_from=0, search_to=100)["request_data"]
    assert calls[2]["filters"][0]["value"][-1] == "abc"
    assert sorted(r["id"] for r in res.observations) == [1001, 1150]
    assert res.detail["pages"] == 3


def test_issues_by_id_with_no_ids_sends_nothing():
    tenant = FakeTenant()
    res = _conn(tenant).issues_by_id(CFG, [])
    assert res.ok and tenant.total_calls == 0 and res.detail["pages"] == 0


def test_case_search_reads_the_case_fixture():
    rows = fixture("case_search_reply.json")["reply"]["DATA"]
    tenant = FakeTenant(cases=rows)
    res = _conn(tenant).search_cases(CFG, body_for=_case_body)
    assert res.ok, res.error
    assert [c["case_id"] for c in res.incidents] == [501, 502]
    assert res.incidents[0]["issue_ids"] == [1001, 1002]
    assert tenant.calls["cases"] == [_case_body(0, 100)["request_data"]]
    assert res.detail["filter_count"] == 2 and res.detail["truncated"] is False


def test_get_endpoints_is_one_request_in_the_lowercase_envelope():
    rows = fixture("endpoint_get_reply.json")["reply"]["endpoints"]
    tenant = FakeTenant(endpoints=rows)
    res = _conn(tenant).get_endpoints(CFG, ["target-01"])
    assert res.ok, res.error
    assert tenant.calls["endpoints"] == [queries.endpoint_get_body(["target-01"])["request_data"]]
    assert [e["endpoint_id"] for e in res.observations] == ["synthetic-endpoint-1"]
    assert res.detail["pages"] == 1 and res.detail["truncated"] is False


def test_endpoint_body_shape():
    assert queries.endpoint_get_body(["a", "b"]) == {"request_data": {
        "filters": [{"field": "hostname", "operator": "in", "value": ["a", "b"]}],
        "search_from": 0, "search_to": 100}}
    assert queries.endpoint_get_body(("a",), search_from=100, search_to=150)[
        "request_data"]["search_to"] == 150


def test_case_search_body_shape():
    assert queries.case_search_body(filters=WINDOW, search_from=0, search_to=100) == {
        "request_data": {"filters": WINDOW, "search_from": 0, "search_to": 100}}


@pytest.mark.parametrize("read", ["search_issues", "issues_by_id", "search_cases",
                                  "get_endpoints"])
def test_every_new_read_spends_the_tenant_request_bucket(monkeypatch, read):
    monkeypatch.setenv("CORTEXSIM_XSIAM_MAX_REQUESTS_PER_MINUTE", "1")
    tenant = FakeTenant(issues=[_issue(1)], cases=[{"case_id": 501}],
                        endpoints=[{"endpoint_id": "e1", "endpoint_name": "target-01"}])
    conn = _conn(tenant)
    call = {
        "search_issues": lambda: conn.search_issues(CFG, body_for=_window_body),
        "issues_by_id": lambda: conn.issues_by_id(CFG, ["1001"]),
        "search_cases": lambda: conn.search_cases(CFG, body_for=_case_body),
        "get_endpoints": lambda: conn.get_endpoints(CFG, ["target-01"]),
    }[read]
    assert call().ok
    refused = call()
    assert refused.ok is False and refused.code == codes.XSIAM_QUOTA_ERROR
    assert refused.detail["refused_locally"] is True
    assert tenant.total_calls == 1


def test_alert_host_reads_the_alert_host_fields_in_order():
    from connectors.xsiam import alert_host

    assert alert_host({"host_name": " web-01 ", "endpoint_name": "x"}) == "web-01"
    assert alert_host({"endpoint_name": "web-02"}) == "web-02"
    assert alert_host({"hosts": ["web-03", "web-04"]}) == "web-03"
    assert alert_host({"hosts": []}) is None and alert_host({}) is None

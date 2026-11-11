"""By-id reads for the run case ledger: ``pull_cases`` and ``pull_issue_status``.

All transports are injected; nothing touches a tenant (tenant-verified is 0).
The issue fixture is copied from a reply observed on the lab tenant
(``reply.DATA``, flat dotted ``status.*`` keys, numeric ``id``).
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from connectors.base import ConnectorConfig
from connectors.xsiam import IssueReadError, XsiamConnector
from integrations.xsiam import codes

CFG = ConnectorConfig(
    integration_name="t",
    config={"fqdn": "api-lab.xdr.us.paloaltonetworks.com", "api_key_id": "7"},
    secret="k",
)

ISSUE = {
    "external_id": "b027f129523a48b28fdcfa6b5441715c",
    "status.progress": "Resolved",
    "status.resolution_reason": "RESOLVED_OTHER",
    "status.resolution_comment": "Dev tenant reset before a Turla replay test (SOCFW).",
    "id": 1500462,
    "case_ids": [51877],
}


class Recorder:
    def __init__(self, reply: Any = None, status: int = 200, raises: Exception = None,
                 responder=None):
        self.calls: list[dict] = []
        self.urls: list[str] = []
        self._reply, self._status, self._raises, self._responder = reply, status, raises, responder

    def __call__(self, method, url, headers, body, timeout):
        assert method == "POST"
        self.urls.append(url)
        req = json.loads(body)
        self.calls.append(req)
        if self._raises:
            raise self._raises
        if self._responder:
            return 200, json.dumps(self._responder(req))
        return self._status, json.dumps(self._reply)


def _case(i: int, **kw) -> dict:
    return {"incident_id": str(i), "status": "resolved_known_issue", "alert_count": 3,
            "hosts": ["web-01:aef3"], "creation_time": 1794367800000,
            "resolve_comment": "closed by SOCFW", **kw}


def _incidents_reply(rows: list[dict]) -> dict:
    return {"reply": {"incidents": rows, "result_count": len(rows),
                      "total_count": len(rows)}}


def test_pull_cases_body_shape():
    rec = Recorder(_incidents_reply([_case(5)]))
    XsiamConnector(fetcher=rec).pull_cases(CFG, ["5", "6"])
    assert rec.calls == [{"request_data": {
        "filters": [{"field": "incident_id_list", "operator": "in", "value": ["5", "6"]}],
        "search_from": 0, "search_to": 2}}]
    assert rec.urls[0].endswith("/public_api/v1/incidents/get_incidents")


def test_pull_cases_honours_incidents_path_override():
    rec = Recorder(_incidents_reply([_case(5)]))
    cfg = ConnectorConfig("t", {**CFG.config, "incidents_path": "/public_api/v2/cases/get"}, "k")
    XsiamConnector(fetcher=rec).pull_cases(cfg, ["5"])
    assert rec.urls[0].endswith("/public_api/v2/cases/get")


def test_pull_cases_chunks_at_100():
    rec = Recorder(responder=lambda req: _incidents_reply(
        [_case(int(v)) for v in req["request_data"]["filters"][0]["value"]]))
    res = XsiamConnector(fetcher=rec).pull_cases(CFG, [str(i) for i in range(230)])
    assert res.ok and len(rec.calls) == 3
    assert [len(c["request_data"]["filters"][0]["value"]) for c in rec.calls] == [100, 100, 30]
    assert res.detail["pages"] == 3
    assert len(res.incidents) == 230


def test_pull_cases_keeps_raw_status_and_comment():
    rec = Recorder(_incidents_reply([_case(5)]))
    res = XsiamConnector(fetcher=rec).pull_cases(CFG, ["5"])
    inc = res.incidents[0]
    assert inc.incident_id == "5" and inc.status == "resolved_known_issue"
    assert inc.raw["resolve_comment"] == "closed by SOCFW"
    assert inc.hosts == ["web-01"]


def test_pull_cases_http_401_is_auth_code():
    res = XsiamConnector(fetcher=Recorder({}, status=401)).pull_cases(CFG, ["5"])
    assert res.ok is False and res.code == codes.XSIAM_AUTH_ERROR


def test_unrecognised_envelope_raises_not_empty():
    rec = Recorder({"reply": {"something_else": []}})
    res = XsiamConnector(fetcher=rec).pull_cases(CFG, ["5"])
    assert res.ok is False and res.code == codes.XSIAM_PARSE_ERROR
    assert res.incidents == []


def test_pull_cases_later_chunk_failure_fails_whole_read():
    def responder(req):
        return _incidents_reply([_case(1)])
    calls = {"n": 0}

    def fetch(method, url, headers, body, timeout):
        calls["n"] += 1
        if calls["n"] == 2:
            return 500, "boom"
        return 200, json.dumps(_incidents_reply([_case(1)]))
    res = XsiamConnector(fetcher=fetch).pull_cases(CFG, [str(i) for i in range(150)])
    assert res.ok is False and res.code == codes.XSIAM_API_ERROR


def test_pull_issue_status_reads_dotted_fields():
    rec = Recorder({"reply": {"DATA": [ISSUE], "FILTER_COUNT": 1, "TOTAL_COUNT": 37023}})
    out = XsiamConnector(fetcher=rec).pull_issue_status(CFG, ["1500462"])
    assert out == {"1500462": {
        "status": "Resolved", "reason": "RESOLVED_OTHER",
        "comment": "Dev tenant reset before a Turla replay test (SOCFW)."}}
    assert rec.calls == [{"request_data": {
        "filters": [{"field": "id", "operator": "in", "value": [1500462]}],
        "search_from": 0, "search_to": 1}}]
    assert rec.urls[0].endswith("/public_api/v1/issue/search/")


def test_pull_issue_status_null_reason_and_empty_comment():
    row = {**ISSUE, "status.progress": "In Progress", "status.resolution_reason": None,
           "status.resolution_comment": ""}
    out = XsiamConnector(fetcher=Recorder({"reply": {"DATA": [row]}})).pull_issue_status(
        CFG, ["1500462"])
    assert out["1500462"] == {"status": "In Progress", "reason": None, "comment": None}


def test_pull_issue_status_omits_unreturned_ids():
    rec = Recorder({"reply": {"DATA": [ISSUE]}})
    out = XsiamConnector(fetcher=rec).pull_issue_status(CFG, ["1500462", "999"])
    assert set(out) == {"1500462"}
    assert rec.calls[0]["request_data"]["filters"][0]["value"] == [1500462, 999]


def test_pull_issue_status_non_numeric_id_sent_as_string():
    rec = Recorder({"reply": {"DATA": []}})
    assert XsiamConnector(fetcher=rec).pull_issue_status(CFG, ["abc", "7"]) == {}
    assert rec.calls[0]["request_data"]["filters"][0]["value"] == ["abc", 7]


def test_pull_issue_status_chunks_at_100():
    rec = Recorder(responder=lambda req: {"reply": {"DATA": []}})
    XsiamConnector(fetcher=rec).pull_issue_status(CFG, [str(i) for i in range(1, 231)])
    assert [len(c["request_data"]["filters"][0]["value"]) for c in rec.calls] == [100, 100, 30]


def test_pull_issue_status_empty_ids_posts_nothing():
    rec = Recorder({})
    assert XsiamConnector(fetcher=rec).pull_issue_status(CFG, []) == {}
    assert rec.calls == []


@pytest.mark.parametrize("fetcher, code", [
    (Recorder({}, status=401), codes.XSIAM_AUTH_ERROR),
    (Recorder({}, status=500), codes.XSIAM_API_ERROR),
    (Recorder(raises=OSError("down")), codes.XSIAM_TRANSPORT_ERROR),
    (Recorder({"reply": {"nope": 1}}), codes.XSIAM_PARSE_ERROR),
    (Recorder({"reply": {"err_code": 500, "err_msg": "denied"}}), codes.XSIAM_PARSE_ERROR),
])
def test_failed_issue_read_raises_typed_error_never_empty(fetcher, code):
    with pytest.raises(IssueReadError) as ei:
        XsiamConnector(fetcher=fetcher).pull_issue_status(CFG, ["1500462"])
    assert ei.value.code == code


def test_issue_row_without_status_progress_raises():
    bad = {k: v for k, v in ISSUE.items() if k != "status.progress"}
    with pytest.raises(IssueReadError) as ei:
        XsiamConnector(fetcher=Recorder({"reply": {"DATA": [bad]}})).pull_issue_status(
            CFG, ["1500462"])
    assert ei.value.code == codes.XSIAM_PARSE_ERROR


def test_issue_read_bad_config_raises_config_code():
    with pytest.raises(IssueReadError) as ei:
        XsiamConnector(fetcher=Recorder({})).pull_issue_status(
            ConnectorConfig("t", {}, "k"), ["1"])
    assert ei.value.code == codes.XSIAM_CONFIG_ERROR

# tests/integration/xsiam/test_operations_budget_and_gates.py
"""The executor and XQL routes report what a call cost, charge the ledger and refuse.

Op ids are real catalog entries. OP-XQL-GET-DATASETS is read-class and is NOT in
guards.XQL_OPERATION_IDS despite its prefix, so it reports 1 query but is
neither gated nor charged.
"""
from __future__ import annotations

import pytest

from tests.integration.xsiam.test_operations_api import _build_app

pytestmark = pytest.mark.usefixtures("restore_core_modules")

START = "/api/xsiam/tenants/acme/operations/OP-XQL-START-XQL-QUERY"
RESULTS = "/api/xsiam/tenants/acme/operations/OP-XQL-GET-QUERY-RESULTS"
DATASETS = "/api/xsiam/tenants/acme/operations/OP-XQL-GET-DATASETS"
WRITE_QUERY = "dataset=x | target type = lookup y"


def _app(tmp_path, monkeypatch):
    client, stub, xsiam_api = _build_app(tmp_path, monkeypatch)
    from integrations.xsiam import ledger as ledger_mod
    fresh = ledger_mod.TenantQuotaLedger()
    monkeypatch.setattr(ledger_mod, "ledger", fresh)
    stub.start_xql_query = _async_recorder(stub, "start_xql_query", "q1")
    stub.get_query_results = _async_recorder(stub, "get_query_results",
                                             {"status": "SUCCESS", "results": {"data": []}})
    stub.run_xql = _async_recorder(stub, "run_xql", {"status": "SUCCESS", "results": {"data": []}})
    return client, stub, fresh


def _async_recorder(stub, name, ret):
    async def _f(*a, **k):
        stub.calls.append({"method": name, "args": a, "kwargs": k})
        return ret
    return _f


def _key(stub):
    from integrations.xsiam.guards import ledger_key_for
    return ledger_key_for(stub)


def test_live_read_reports_one_query_and_a_ledger_snapshot(tmp_path, monkeypatch):
    client, stub, _ = _app(tmp_path, monkeypatch)
    r = client.post(DATASETS, json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["queries_issued"] == 1
    assert body["ledger"]["queries_last_24h"] == 0  # not XQL-class: not charged
    assert len(stub.calls) == 1


def test_xql_start_is_charged_and_results_poll_is_free(tmp_path, monkeypatch):
    client, stub, _ = _app(tmp_path, monkeypatch)
    r = client.post(START, json={"body": {"request_data": {"query": "dataset=x | limit 1"}}})
    assert r.status_code == 200, r.text
    assert r.json()["queries_issued"] == 1
    assert r.json()["ledger"]["queries_last_24h"] == 1
    r2 = client.post(RESULTS, json={"body": {"request_data": {"query_id": "q1"}}})
    assert r2.status_code == 200, r2.text
    assert r2.json()["queries_issued"] == 0
    assert r2.json()["ledger"]["queries_last_24h"] == 1


def test_preview_reports_zero_and_no_ledger(tmp_path, monkeypatch):
    client, stub, _ = _app(tmp_path, monkeypatch)
    r = client.post("/api/xsiam/tenants/acme/operations/OP-ALERTS-INSERT-PARSED-ALERTS", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dry_run"] is True
    assert body["queries_issued"] == 0
    assert body["ledger"] is None
    assert stub.calls == []


def test_capability_missing_is_403_with_zero_stub_calls(tmp_path, monkeypatch):
    client, stub, _ = _app(tmp_path, monkeypatch)
    stub.capabilities = ["read_alerts"]
    for method, url, kw in (
        ("post", START, {"json": {"body": {"request_data": {"query": "dataset=x"}}}}),
        ("post", "/api/xsiam/tenants/acme/xql", {"json": {"query": "dataset=x"}}),
        ("get", "/api/xsiam/tenants/acme/metrics", {}),
    ):
        r = getattr(client, method)(url, **kw)
        assert r.status_code == 403, (url, r.status_code, r.text)
        assert r.json()["code"] == "XSIAM_CAPABILITY_MISSING"
    assert stub.calls == []


def test_write_stage_is_refused_on_both_xql_paths(tmp_path, monkeypatch):
    client, stub, ledger = _app(tmp_path, monkeypatch)
    r = client.post("/api/xsiam/tenants/acme/xql", json={"query": WRITE_QUERY})
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "XQL_WRITE_STAGE_REFUSED"
    r = client.post(START, json={"body": {"request_data": {"query": WRITE_QUERY}}})
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "XQL_WRITE_STAGE_REFUSED"
    assert stub.calls == []
    assert ledger.snapshot(_key(stub), limit=10)["queries_last_24h"] == 0  # refused before charge


def test_exhausted_budget_is_429_before_the_tenant_is_called(tmp_path, monkeypatch):
    client, stub, ledger = _app(tmp_path, monkeypatch)
    from connectors import tuning
    limit = tuning.xsiam_max_queries_per_day()
    for _ in range(limit):
        ledger.charge(_key(stub), limit=limit)
    r = client.post(START, json={"body": {"request_data": {"query": "dataset=x"}}})
    assert r.status_code == 429, r.text
    assert r.json()["code"] == "XSIAM_QUOTA_ERROR"
    r = client.post("/api/xsiam/tenants/acme/xql", json={"query": "dataset=x"})
    assert r.status_code == 429, r.text
    assert stub.calls == []


def test_get_xql_limit_is_capped_at_1000(tmp_path, monkeypatch):
    client, stub, _ = _app(tmp_path, monkeypatch)
    assert client.get("/api/xsiam/tenants/acme/xql/q1?limit=5000").status_code == 422
    r = client.get("/api/xsiam/tenants/acme/xql/q1?limit=1000")
    assert r.status_code == 200, r.text
    assert r.json()["queries_issued"] == 0
    assert "ledger" in r.json()
    assert stub.calls[-1]["kwargs"]["limit"] == 1000


def test_metrics_is_charged_and_refused_when_budget_is_spent(tmp_path, monkeypatch):
    client, stub, ledger = _app(tmp_path, monkeypatch)
    from connectors import tuning
    limit = tuning.xsiam_max_queries_per_day()
    assert ledger.snapshot(_key(stub), limit=limit)["queries_last_24h"] == 0
    r = client.get("/api/xsiam/tenants/acme/metrics")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["queries_issued"] == 1
    assert isinstance(body["ledger"], dict)
    assert body["ledger"]["queries_last_24h"] == 1
    assert "sources" in body and "remaining_quota" in body
    for _ in range(limit - 1):
        ledger.charge(_key(stub), limit=limit)
    stub.calls.clear()
    r = client.get("/api/xsiam/tenants/acme/metrics")
    assert r.status_code == 429, r.text
    assert r.json()["code"] == "XSIAM_QUOTA_ERROR"
    assert stub.calls == []

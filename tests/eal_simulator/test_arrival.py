"""The arrival check: a running query is pending, never zero; a failure is never a count."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from eal_simulator.arrival import arrival_query, check_arrival
from integrations.xsiam import ledger as ledger_mod
from integrations.xsiam.exceptions import (
    XsiamApiError, XsiamAuthError, XsiamCapabilityError, XsiamConfigError,
    XsiamQueryError, XsiamQuotaError,
)

TOKEN = "wiz-a-202611131200-ab12"
OK_ROW = {"found": 3, "first_seen": 1, "last_seen": 2}


@pytest.fixture(autouse=True)
def _fresh_ledger():
    # The tenant ledger is process-wide; a tripped breaker must not leak between tests.
    ledger_mod.ledger.reset()
    yield
    ledger_mod.ledger.reset()


class _Stub:
    capabilities = ["xql"]
    _base = "https://api-acme.example.test"

    def __init__(self, reply=None, start_exc=None, read_exc=None):
        self.reply, self.start_exc, self.read_exc = reply, start_exc, read_exc
        self.starts, self.reads, self.timeframes = 0, 0, []

    async def start_xql_query(self, query, timeframe):
        self.starts += 1
        self.timeframes.append(timeframe)
        if self.start_exc:
            raise self.start_exc
        return "q1"

    async def get_query_results(self, query_id, limit=100):
        self.reads += 1
        if self.read_exc:
            raise self.read_exc
        return self.reply


def run(stub, **over):
    kw = dict(dataset="d_raw", token=TOKEN, delivered=3, since=None, query_id=None)
    kw.update(over)
    return asyncio.run(check_arrival(stub, **kw))


def test_pending_is_pending_never_zero():
    r = run(_Stub(reply={"status": "PENDING"}))
    assert r["state"] == "pending" and r["found"] is None and r["query_id"] == "q1"


def test_missing_status_is_pending_too():
    r = run(_Stub(reply={}))
    assert r["state"] == "pending" and r["found"] is None


def test_counted_reads_found_and_times():
    r = run(_Stub(reply={"status": "SUCCESS", "results": {"data": [OK_ROW]}}))
    assert (r["state"], r["found"], r["queries_issued"]) == ("counted", 3, 1)
    assert (r["first_seen"], r["last_seen"], r["delivered"]) == (1, 2, 3)
    assert r["proves"] == ["These records, sent from SimCore just now, were ingested into `d_raw`."]
    assert len(r["does_not_prove"]) == 3


def test_zero_rows_counts_zero_only_on_success():
    r = run(_Stub(reply={"status": "SUCCESS", "results": {"data": []}}))
    assert (r["state"], r["found"]) == ("counted", 0)


@pytest.mark.parametrize("row", [{"found": "3"}, {"found": True}, {"first_seen": 1}])
def test_row_without_int_found_is_unrecognised(row):
    r = run(_Stub(reply={"status": "SUCCESS", "results": {"data": [row]}}))
    assert (r["state"], r["code"], r["found"]) == ("not_checked", "UNRECOGNISED_REPLY", None)


def test_unknown_envelope_is_unrecognised():
    r = run(_Stub(reply={"status": "SUCCESS", "results": "weird"}))
    assert (r["state"], r["code"]) == ("not_checked", "UNRECOGNISED_REPLY")


def test_other_status_is_query_failed():
    r = run(_Stub(reply={"status": "FAIL"}))
    assert (r["state"], r["code"], r["found"]) == ("not_checked", "QUERY_FAILED", None)


class _NoXql(_Stub):
    capabilities = []


@pytest.mark.parametrize("stub,code", [
    (_NoXql(reply={}), "XQL_NOT_AUTHORISED"),
    (_Stub(start_exc=XsiamConfigError("x")), "TENANT_NOT_CONFIGURED"),
    (_Stub(start_exc=XsiamCapabilityError("x")), "XQL_NOT_AUTHORISED"),
    (_Stub(start_exc=ledger_mod.DailyBudgetExhausted("t", 1)), "QUOTA"),
    (_Stub(start_exc=XsiamQuotaError("x")), "QUOTA"),
    (_Stub(start_exc=XsiamAuthError("x")), "TENANT_UNAUTHORIZED"),
    (_Stub(start_exc=XsiamApiError("x", upstream_status=401)), "TENANT_UNAUTHORIZED"),
    (_Stub(start_exc=XsiamApiError("x", upstream_status=403)), "TENANT_UNAUTHORIZED"),
    (_Stub(start_exc=XsiamApiError("x", upstream_status=500)), "TENANT_ERROR"),
    (_Stub(read_exc=XsiamApiError("x", upstream_status=503)), "TENANT_ERROR"),
    (_Stub(read_exc=XsiamQueryError("x")), "QUERY_FAILED"),
])
def test_every_refusal_is_not_checked_with_its_code(stub, code):
    r = run(stub)
    assert (r["state"], r["code"], r["found"]) == ("not_checked", code, None)
    assert r["reason"]


def test_open_breaker_is_quota():
    key = ledger_mod.tenant_ledger_key(_Stub._base)
    ledger_mod.ledger.trip(key, cooldown_seconds=60)
    r = run(_Stub(reply={"status": "SUCCESS"}))
    assert (r["state"], r["code"]) == ("not_checked", "QUOTA")


def test_a_start_that_fails_after_the_charge_still_counts_one_query():
    r = run(_Stub(start_exc=XsiamApiError("boom", upstream_status=500)))
    key = ledger_mod.tenant_ledger_key(_Stub._base)
    assert r["code"] == "TENANT_ERROR" and r["queries_issued"] == 1
    assert ledger_mod.ledger.snapshot(key, limit=1000)["queries_last_24h"] == 1


def test_a_ledger_refusal_reads_zero_queries():
    key = ledger_mod.tenant_ledger_key(_Stub._base)
    ledger_mod.ledger.trip(key, cooldown_seconds=60)
    r = run(_Stub(reply={"status": "SUCCESS"}))
    assert (r["code"], r["queries_issued"]) == ("QUOTA", 0)


def test_proves_names_the_queried_dataset():
    r = run(_Stub(reply={"status": "SUCCESS", "results": {"data": [OK_ROW]}}), dataset="acme_okta_raw")
    assert r["proves"] == ["These records, sent from SimCore just now, were ingested into "
                           "`acme_okta_raw`."]


def test_a_partial_count_says_k_of_n_arrived():
    row = {"found": 2, "first_seen": 1, "last_seen": 2}
    r = run(_Stub(reply={"status": "SUCCESS", "results": {"data": [row]}}), dataset="acme_okta_raw")
    assert len(r["proves"]) == 1 and "2 of the 3" in r["proves"][0] and "`acme_okta_raw`" in r["proves"][0]


@pytest.mark.parametrize("stub", [
    _Stub(reply={"status": "PENDING"}),
    _Stub(reply={"status": "FAIL"}),
    _Stub(reply={"status": "SUCCESS", "results": {"data": []}}),
    _Stub(start_exc=XsiamApiError("x", upstream_status=500)),
])
def test_proves_is_empty_unless_records_were_counted(stub):
    r = run(stub)
    assert r["proves"] == [] and len(r["does_not_prove"]) == 3


@pytest.mark.parametrize("dataset", [
    'xdr_data | limit 3 | alter cortexsim_canary = "x"', "a b", "1abc", "a-b", "", "d_raw\n| limit 1",
])
def test_a_dataset_that_is_not_an_identifier_is_never_queried(dataset):
    stub = _Stub(reply={"status": "SUCCESS", "results": {"data": [OK_ROW]}})
    r = run(stub, dataset=dataset)
    assert (r["state"], r["code"], r["found"], r["queries_issued"]) == (
        "not_checked", "DATASET_NOT_QUERYABLE", None, 0)
    assert stub.starts == 0 and stub.reads == 0 and r["proves"] == []
    key = ledger_mod.tenant_ledger_key(_Stub._base)
    assert ledger_mod.ledger.snapshot(key, limit=1000)["queries_last_24h"] == 0


@pytest.mark.parametrize("reply", [None, [], "SUCCESS"])
def test_a_reply_that_is_not_a_dict_is_unrecognised_and_keeps_the_charge(reply):
    r = run(_Stub(reply=reply))
    assert (r["state"], r["code"], r["found"], r["queries_issued"]) == (
        "not_checked", "UNRECOGNISED_REPLY", None, 1)


def test_a_tenant_reported_query_error_is_query_failed_not_unrecognised():
    for stub in (_Stub(start_exc=XsiamQueryError("syntax")), _Stub(read_exc=XsiamQueryError("no dataset"))):
        r = run(stub)
        assert (r["state"], r["code"], r["found"]) == ("not_checked", "QUERY_FAILED", None)


def test_a_caller_query_id_refused_locally_does_not_claim_the_query_is_running():
    from integrations.xsiam.exceptions import XsiamQuotaError
    exc = XsiamQuotaError("x")
    exc.local = True
    r = run(_Stub(read_exc=exc), query_id="q9")
    assert r["state"] == "pending" and r["query_id"] == "q9"
    assert "running on the tenant" not in r["reason"] and "q9" not in r["reason"]
    assert "query id" in r["reason"]


def test_unexpected_exception_propagates():
    with pytest.raises(ZeroDivisionError):
        run(_Stub(start_exc=ZeroDivisionError()))


def test_reading_a_started_query_issues_no_metered_call():
    stub = _Stub(reply={"status": "SUCCESS", "results": {"data": [OK_ROW]}})
    r = run(stub, query_id="q9")
    assert stub.starts == 0 and stub.reads == 1
    assert (r["state"], r["queries_issued"], r["query_id"]) == ("counted", 0, "q9")


def test_timeframe_is_last_day_or_since_to_now():
    stub = _Stub(reply={"status": "PENDING"})
    run(stub)
    assert stub.timeframes[-1] == {"relativeTime": 86_400_000}
    since = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)
    run(stub, since=since)
    tf = stub.timeframes[-1]
    assert tf["from"] == int(since.timestamp() * 1000) and tf["to"] > tf["from"]
    run(stub, since=datetime(2026, 11, 13, 12, 0))  # naive reads as UTC, not host-local
    assert stub.timeframes[-1]["from"] == int(since.timestamp() * 1000)


def test_query_text_is_the_spec_query():
    assert arrival_query("d_raw", "wiz-x-1") == (
        'dataset = d_raw\n'
        '| filter cortexsim_canary = "wiz-x-1"\n'
        '| comp count() as found, min(_insert_time) as first_seen, '
        'max(_insert_time) as last_seen')


def _queries(stub):
    key = ledger_mod.tenant_ledger_key(stub._base)
    return ledger_mod.ledger.snapshot(key, limit=1000)["queries_last_24h"]


def test_local_refusal_before_the_start_left_is_quota_and_refunded():
    stub = _Stub(start_exc=XsiamQuotaError("own bucket empty", local=True))
    r = run(stub)
    assert (r["state"], r["code"], r["found"]) == ("not_checked", "QUOTA", None)
    assert r["queries_issued"] == 0 and _queries(stub) == 0
    assert "POVengine" in r["reason"] and "before" in r["reason"]


@pytest.mark.parametrize("started", ["q1", None])
def test_local_refusal_of_the_poll_after_a_start_is_pending_with_the_id(started):
    # The public get_query_results never names the started query, so the
    # refusal may carry query_started=None; the phase decides, not the field.
    stub = _Stub(read_exc=XsiamQuotaError("poll refused", local=True, query_started=started))
    r = run(stub)
    assert (r["state"], r["found"], r["query_id"]) == ("pending", None, "q1")
    assert r["queries_issued"] == 1 and _queries(stub) == 1
    assert "locally" in r["reason"] and "again later" in r["reason"]


def test_local_poll_refusal_for_a_caller_supplied_id_issues_nothing():
    stub = _Stub(read_exc=XsiamQuotaError("poll refused", local=True))
    r = run(stub, query_id="q9")
    assert (r["state"], r["query_id"], r["queries_issued"]) == ("pending", "q9", 0)
    assert stub.starts == 0 and _queries(stub) == 0


def test_tenant_429_stays_quota_and_charged():
    stub = _Stub(start_exc=XsiamQuotaError("429"))
    r = run(stub)
    assert (r["state"], r["code"], r["queries_issued"]) == ("not_checked", "QUOTA", 1)
    assert _queries(stub) == 1

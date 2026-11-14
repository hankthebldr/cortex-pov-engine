"""``read_run_evidence``: the run-scoped tenant read (spec §7.3 steps 1–7).

Driven through the service with an injected fetcher (``FakeTenant``); nothing
reaches a tenant. Window bounds are epoch-ms UTC on every host, so the window
test runs in two non-UTC zones.
"""
from __future__ import annotations

import asyncio
import copy
import time
from datetime import datetime, timedelta

import pytest

from tests.connectors.tenant_fakes import FakeTenant, fixture

ISSUES = fixture("issue_search_reply.json")["reply"]["DATA"]
CASES = fixture("case_search_reply.json")["reply"]["DATA"]
ENDPOINTS = fixture("endpoint_get_reply.json")["reply"]["endpoints"]

STEP_AT = datetime(2025, 11, 1, 12, 26, 0)       # naive UTC
RUN_HOST = "target-01"
FAR_FUTURE = datetime(2030, 1, 1)


def seed(SessionLocal, *, host=RUN_HOST, observations=(), second_step=True):
    from models import Agent, Observation, Result, Run
    from security.credentials import CredentialStore

    async def go():
        async with SessionLocal() as db:
            if host is not None:
                db.add(Agent(agent_id="ag-1", hostname=host, os="linux"))
            db.add(Run(run_id="r1", scenario_id="SIM-X", mode="pull", status="complete",
                       target="ag-1" if host is not None else None, started_at=STEP_AT))
            r1 = Result(run_id="r1", step_id="step-01", plane="EDR", signal_type="BIOC",
                        expected_detection="d1", executed_at=STEP_AT)
            db.add(r1)
            if second_step:
                db.add(Result(run_id="r1", step_id="step-02", plane="EDR", signal_type="BIOC",
                              expected_detection="d2",
                              executed_at=STEP_AT + timedelta(seconds=30)))
            await db.flush()
            for o in observations:
                db.add(Observation(run_id="r1", result_id=r1.id, source="xsiam:acme", **o))
            await db.commit()
            await CredentialStore(db).put_integration(
                name="acme", kind="xsiam", plaintext_secret="k" * 40,
                config={"fqdn": "api-acme.xdr.us.paloaltonetworks.com", "api_key_id": "1"})
            await db.commit()
    asyncio.run(go())


async def _read(SessionLocal, fetcher, **kw):
    from connectors.tenant_evidence import read_run_evidence
    from models import Run
    from sqlalchemy import select

    async with SessionLocal() as db:
        run = (await db.execute(select(Run).where(Run.run_id == "r1"))).scalar_one()
        return await read_run_evidence(db, run, fetcher=fetcher, **kw)


def read(SessionLocal, fetcher, **kw):
    kw.setdefault("now", FAR_FUTURE)
    return asyncio.run(_read(SessionLocal, fetcher, **kw))


def _issue(n: int, *, host=RUN_HOST, domain="Security", **kw) -> dict:
    row = copy.deepcopy(ISSUES[0])
    row.update({"id": n, "_insert_time": 1762000001000 + n, "observation_time": 1762000005000 + n,
                "domain": domain, "case_ids": [501]})
    row["normalized_fields"]["xdm.source.host.hostname"] = host
    row.update(kw)
    return row


def _filter(request_data: dict, field: str, op: str):
    for f in request_data["filters"]:
        if f["field"] == field and f["operator"] == op:
            return f["value"]
    raise AssertionError(f"no {field} {op} filter in {request_data['filters']}")


# ── the brief's tests ──────────────────────────────────────────────────────

def test_window_bounds_are_utc_epoch_ms(session_factory, non_utc_host):
    from connectors.xsiam import _epoch_ms_utc

    seed(session_factory)
    fake = FakeTenant(issues=copy.deepcopy(ISSUES))
    out = read(session_factory, fake)
    assert out["read"] is True, out
    first = fake.calls["issues"][0]
    assert _filter(first, "_insert_time", "gte") == _epoch_ms_utc(STEP_AT - timedelta(seconds=300))
    # until = the latest matcher window end (step-02 + 3600 s), capped at now.
    assert _filter(first, "_insert_time", "lte") == _epoch_ms_utc(
        STEP_AT + timedelta(seconds=30 + 3600))
    assert _filter(first, "issue_domain", "in") == ["Security"]
    assert first["sort"] == {"field": "observation_time", "keyword": "asc"}
    assert first["include_fields"] == ["normalized_fields"]


def test_window_end_is_capped_at_now(session_factory):
    from connectors.xsiam import _epoch_ms_utc

    seed(session_factory)
    fake = FakeTenant(issues=copy.deepcopy(ISSUES))
    now = STEP_AT + timedelta(seconds=600)
    read(session_factory, fake, now=now)
    assert _filter(fake.calls["issues"][0], "_insert_time", "lte") == _epoch_ms_utc(now)


def test_duplicate_issue_across_pages_counts_once(session_factory):
    seed(session_factory)
    rows = [_issue(2000 + i) for i in range(150)]
    rows[120] = _issue(2003)                           # page 2 repeats page 1's id 2003
    fake = FakeTenant(issues=rows, cases=copy.deepcopy(CASES))
    out = read(session_factory, fake)
    issues = out["snapshot"]["payload"]["issues"]
    assert len(fake.calls["issues"]) == 2
    ids = [i["id"] for i in issues["rows"]]
    assert len(ids) == 149 and len(set(ids)) == 149
    assert issues["host_scope"]["attributed"] == 149


def test_reader_io_does_not_block_the_event_loop(session_factory):
    """Every connector call runs in a worker thread: a 0.3 s blocking fetch must
    let a 50 ms ticker keep ticking."""
    seed(session_factory, host=None)
    fake = FakeTenant(issues=[])

    def slow(method, url, headers, body, timeout):
        time.sleep(0.3)
        return fake(method, url, headers, body, timeout)

    async def main():
        ticks = 0
        done = asyncio.Event()

        async def ticker():
            nonlocal ticks
            while not done.is_set():
                await asyncio.sleep(0.05)
                ticks += 1

        task = asyncio.create_task(ticker())
        out = await _read(session_factory, slow, now=FAR_FUTURE)
        seen = ticks
        done.set()
        await task
        return out, seen

    out, seen = asyncio.run(main())
    assert fake.total_calls == 2 and out["read"] is True          # issues, then tenant info
    assert seen >= 3, f"the event loop ticked {seen} time(s) during a 0.3 s tenant read"


# ── behaviour the reader adds ──────────────────────────────────────────────

def test_non_security_rows_are_excluded_and_counted(session_factory):
    seed(session_factory)
    rows = copy.deepcopy(ISSUES) + [_issue(4001, domain="Health")]
    out = read(session_factory, FakeTenant(issues=rows, cases=copy.deepcopy(CASES)))
    issues = out["snapshot"]["payload"]["issues"]
    assert issues["excluded_non_security"] == 1
    assert "4001" not in [i["id"] for i in issues["rows"] + issues["unattributed_rows"]]


def test_lag_is_shown_only_for_an_issue_joined_to_a_matcher_row(session_factory):
    """PD1: "observed after step" is timed from the joined row's Result.executed_at."""
    seed(session_factory, observations=[{
        "external_id": "1001", "name": "synthetic-3",
        "observed_at": datetime(2025, 11, 1, 12, 26, 45), "raw": {"host_name": RUN_HOST}}])
    out = read(session_factory, FakeTenant(issues=copy.deepcopy(ISSUES),
                                           cases=copy.deepcopy(CASES)))
    rows = {i["id"]: i for i in out["snapshot"]["payload"]["issues"]["rows"]}
    assert rows["1001"]["observed_after_step_seconds"] == 45.0
    assert rows["1002"]["observed_after_step_seconds"] is None


def test_quota_on_a_later_window_page_keeps_the_pages_already_read(session_factory):
    seed(session_factory)
    fake = FakeTenant(issues=[_issue(2000 + i) for i in range(150)])
    issue_pages = {"n": 0}

    def second_page_429(method, url, headers, body, timeout):
        if "issue/search" in url:
            issue_pages["n"] += 1
            if issue_pages["n"] == 2:
                return 429, "{}"
        return fake(method, url, headers, body, timeout)

    out = read(session_factory, second_page_429)
    p = out["snapshot"]["payload"]
    assert out["code"] == "QUOTA_REFUSED" and p["complete"] is False
    assert p["issues"]["code"] == "QUOTA_REFUSED"
    assert p["issues"]["refused_locally"] is False
    assert len(p["issues"]["rows"]) == 100
    assert p["queries_issued"] == 2
    assert (p["cases"]["code"], p["endpoint"]["code"]) == ("QUOTA_REFUSED", "QUOTA_REFUSED")
    # Review 1: the pages read give floors, labelled as such; the case ids of
    # the unread pages are unknown, so the case read is refused, not empty.
    assert p["issues"]["counts_are_floors"] is True
    assert p["issues"]["host_scope"]["attributed"] == 100
    assert p["cases"]["attempted"] is False


def test_local_budget_refusal_is_never_described_as_the_tenant(session_factory, monkeypatch):
    seed(session_factory)
    monkeypatch.setenv("CORTEXSIM_XSIAM_MAX_REQUESTS_PER_MINUTE", "1")
    fake = FakeTenant(issues=copy.deepcopy(ISSUES), cases=copy.deepcopy(CASES),
                      endpoints=copy.deepcopy(ENDPOINTS))
    out = read(session_factory, fake)
    p = out["snapshot"]["payload"]
    assert len(fake.calls["issues"]) == 1 and len(fake.calls["cases"]) == 0
    assert p["cases"]["code"] == "QUOTA_REFUSED" and p["cases"]["refused_locally"] is True
    assert "CortexSim's own per-tenant request budget" in p["cases"]["detail"]
    assert p["queries_issued"] == 1


# ── carried rulings 1–3 ────────────────────────────────────────────────────

def test_probe_carries_hosts_and_a_hit_outside_the_window_joins_by_id(session_factory):
    """Ruling 1: the probe asks for normalized_fields, so a hit on an issue
    outside the window still attributes on its own hosts."""
    seed(session_factory, observations=[{
        "external_id": "9001", "name": "synthetic-x",
        "observed_at": datetime(2025, 11, 1, 12, 26, 50), "raw": {"host_name": RUN_HOST}}])
    fake = FakeTenant(issues=copy.deepcopy(ISSUES), probe=[_issue(9001)],
                      cases=copy.deepcopy(CASES))
    out = read(session_factory, fake)
    assert fake.calls["issues_by_id"][0]["include_fields"] == ["normalized_fields"]
    agreement = out["snapshot"]["payload"]["agreement"]
    assert agreement["both"]["id"] == 1
    assert agreement["id_space"] == {"probed": 1, "hit": 1, "no_hit": 0, "code": "OK"}


def test_failed_probe_is_unknown_not_no_hit(session_factory):
    """Ruling 2: a failed probe chunk leaves id-class counts null, never no_hit."""
    seed(session_factory, observations=[{
        "external_id": "1001", "name": "synthetic-3",
        "observed_at": datetime(2025, 11, 1, 12, 26, 45), "raw": {"host_name": RUN_HOST}}])
    fake = FakeTenant(issues=copy.deepcopy(ISSUES), cases=copy.deepcopy(CASES),
                      endpoints=copy.deepcopy(ENDPOINTS), status={"issues_by_id": 500})
    agreement = read(session_factory, fake)["snapshot"]["payload"]["agreement"]
    assert agreement["id_space"] == {"probed": 1, "hit": None, "no_hit": None,
                                     "code": "XSIAM_API_ERROR"}
    assert agreement["both"] == {"id": None, "name_host_window": 1}


def test_matcher_row_host_comes_from_the_alert_record(session_factory):
    """Ruling 3: an alert recorded on another host never joins by name; one
    with no host on its record is the run's (the matcher scoped it)."""
    on_other = {"external_id": None, "name": "synthetic-3",
                "observed_at": datetime(2025, 11, 1, 12, 26, 45),
                "raw": {"host_name": "lab-box-9"}}
    seed(session_factory, observations=[on_other])
    out = read(session_factory, FakeTenant(issues=copy.deepcopy(ISSUES),
                                           cases=copy.deepcopy(CASES)))
    agreement = out["snapshot"]["payload"]["agreement"]
    assert agreement["both"]["name_host_window"] == 0 and agreement["matcher_only"] == 1


def test_matcher_row_without_a_recorded_host_takes_the_run_host(session_factory):
    hostless = {"external_id": None, "name": "synthetic-3",
                "observed_at": datetime(2025, 11, 1, 12, 26, 45), "raw": {}}
    seed(session_factory, observations=[hostless])
    out = read(session_factory, FakeTenant(issues=copy.deepcopy(ISSUES),
                                           cases=copy.deepcopy(CASES)))
    assert out["snapshot"]["payload"]["agreement"]["both"]["name_host_window"] == 1


# ── fix round 1 ────────────────────────────────────────────────────────────

def _spend_the_only_token():
    from integrations.xsiam.ledger import ledger, tenant_ledger_key
    from integrations.xsiam.tenant_url import normalize_tenant_base_url

    base = normalize_tenant_base_url("api-acme.xdr.us.paloaltonetworks.com")
    ledger.acquire_request(tenant_ledger_key(base), per_minute=1)


def test_window_refused_before_any_page_publishes_no_counts(session_factory, monkeypatch):
    """Review 1: nothing was read, so nothing is counted — null, never 0."""
    seed(session_factory, observations=[{
        "external_id": "1001", "name": "synthetic-3",
        "observed_at": datetime(2025, 11, 1, 12, 26, 45), "raw": {"host_name": RUN_HOST}}])
    monkeypatch.setenv("CORTEXSIM_XSIAM_MAX_REQUESTS_PER_MINUTE", "1")
    _spend_the_only_token()
    fake = FakeTenant(issues=copy.deepcopy(ISSUES), cases=copy.deepcopy(CASES),
                      endpoints=copy.deepcopy(ENDPOINTS))
    out = read(session_factory, fake)
    p = out["snapshot"]["payload"]
    assert out["code"] == "QUOTA_REFUSED" and fake.total_calls == 0
    assert p["queries_issued"] == 0 and p["tenant_answered"] is None
    issues = p["issues"]
    assert issues["host_scope"] == {"host": RUN_HOST, "attributed": None, "other_host": None,
                                    "without_host": None}
    assert (issues["excluded_non_security"], issues["duplicate_rows"]) == (None, None)
    assert issues["rows"] is None and issues["unattributed_rows"] is None
    assert issues["counts_are_floors"] is None
    assert p["cases"]["code"] == "QUOTA_REFUSED" and p["cases"]["attempted"] is False
    assert p["cases"]["rows"] is None
    # A section skipped after a LOCAL refusal never blames the tenant either.
    for skipped in (p["cases"], p["endpoint"]):
        assert "CortexSim's own per-tenant request budget" in skipped["detail"]
        assert "tenant's API/XQL quota" not in skipped["remediation"]
    assert "floors" not in out["detail"] and "before any row was read" in out["detail"]
    a = p["agreement"]
    assert a["both"] == {"id": None, "name_host_window": None}
    assert (a["matcher_only"], a["reader_only"], a["ambiguous"]) == (None, None, None)
    assert a["unjoinable"] == 0
    assert a["id_space"]["code"] == "QUOTA_REFUSED" and a["id_space"]["hit"] is None


def test_concurrent_reads_of_one_run_send_one_read(session_factory):
    """Review 2: a double click is one metered read; the second waits and gets it."""
    from models import TenantEvidenceRead
    from sqlalchemy import func, select

    seed(session_factory)
    fake = FakeTenant(issues=copy.deepcopy(ISSUES), cases=copy.deepcopy(CASES),
                      endpoints=copy.deepcopy(ENDPOINTS))

    def slow(method, url, headers, body, timeout):
        time.sleep(0.1)
        return fake(method, url, headers, body, timeout)

    async def both():
        outs = await asyncio.gather(_read(session_factory, slow), _read(session_factory, slow))
        async with session_factory() as db:
            stored = (await db.execute(
                select(func.count()).select_from(TenantEvidenceRead))).scalar()
        return outs, stored

    outs, stored = asyncio.run(both())
    assert sorted(o["read"] for o in outs) == [False, True]
    assert {o["code"] for o in outs} == {"OK", "TENANT_EVIDENCE_READ_RECENTLY"}
    assert stored == 1
    assert fake.total_calls == 4          # issues, cases, endpoint, tenant info: one read


def test_truncated_window_is_incomplete_and_named_first(session_factory, monkeypatch):
    """Review 4: a cut window is not a complete answer, and a zero over it
    points at the cut before anything else."""
    from config import settings

    monkeypatch.setattr(settings, "CORTEXSIM_TENANT_EVIDENCE_MAX_PAGES", 1)
    seed(session_factory)
    rows = [_issue(2000 + i, host="lab-box-9") for i in range(150)]
    out = read(session_factory, FakeTenant(issues=rows))
    p = out["snapshot"]["payload"]
    assert p["issues"]["truncated"] is True
    assert out["code"] == "NO_ATTRIBUTED_ISSUES"
    assert p["complete"] is False and out["status"] == "degraded"
    detail = out["detail"]
    assert "truncat" in detail
    assert detail.index("truncat") < detail.index("ingestion latency")


def test_window_without_filter_count_is_incomplete(session_factory):
    from tests.connectors.tenant_fakes import OMIT

    seed(session_factory)
    out = read(session_factory, FakeTenant(issues=copy.deepcopy(ISSUES), filter_count=OMIT,
                                           cases=copy.deepcopy(CASES),
                                           endpoints=copy.deepcopy(ENDPOINTS)))
    p = out["snapshot"]["payload"]
    assert p["issues"]["truncated"] is None
    assert out["code"] == "OK"
    assert p["complete"] is False and out["status"] == "degraded"


def test_parse_error_reports_the_page_it_was_sent_on():
    """Review 7: a parse failure on page N sent N requests, and says so."""
    import json

    from connectors.base import ConnectorConfig
    from connectors.service import _pages_issued
    from connectors.xsiam import XsiamConnector

    cfg = ConnectorConfig(integration_name="t", secret="k", config={
        "base_url": "https://api-lab.xdr.us.paloaltonetworks.com", "api_key_id": "7"})
    page = {"alerts": [{"alert_id": f"a{i}", "detection_timestamp": 1762000000000 + i}
                       for i in range(100)], "total_count": 250, "result_count": 100}
    sent = {"n": 0}

    def second_page_unreadable(method, url, headers, body, timeout):
        sent["n"] += 1
        return (200, json.dumps({"reply": page})) if sent["n"] == 1 else (200, "<html>")

    res = XsiamConnector(fetcher=second_page_unreadable).pull(
        cfg, since=datetime(2025, 11, 1), until=datetime(2025, 11, 2))
    assert res.ok is False and res.code == "XSIAM_PARSE_ERROR"
    assert res.detail["page"] == 2 and _pages_issued(res.detail) == 2 == sent["n"]


# ── fix round 2 ────────────────────────────────────────────────────────────

_OBS_1001 = {"external_id": "1001", "name": "synthetic-3",
             "observed_at": datetime(2025, 11, 1, 12, 26, 45), "raw": {"host_name": RUN_HOST}}


def test_refusal_after_a_window_without_filter_count_never_claims_complete_rows(session_factory):
    """Review 2.1: a later refusal does not make an unknown-completeness window complete."""
    from tests.connectors.tenant_fakes import OMIT

    seed(session_factory, observations=[_OBS_1001])
    fake = FakeTenant(issues=copy.deepcopy(ISSUES), filter_count=OMIT,
                      status={"issues_by_id": 429})
    out = read(session_factory, fake)
    assert out["code"] == "QUOTA_REFUSED"
    assert out["snapshot"]["payload"]["complete"] is False
    assert "rows are complete" not in out["detail"]
    assert "FILTER_COUNT" in out["detail"]


def test_refusal_after_a_truncated_window_names_the_truncation(session_factory, monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "CORTEXSIM_TENANT_EVIDENCE_MAX_PAGES", 1)
    seed(session_factory, observations=[_OBS_1001])
    rows = [_issue(2000 + i) for i in range(150)]
    out = read(session_factory, FakeTenant(issues=rows, status={"issues_by_id": 429}))
    assert out["code"] == "QUOTA_REFUSED"
    assert out["snapshot"]["payload"]["issues"]["truncated"] is True
    assert "rows are complete" not in out["detail"] and "truncated" in out["detail"]


def test_waiter_returns_the_read_that_finished_while_it_waited(session_factory, monkeypatch):
    """Review 2.2: a first read that outlasts the minimum interval (here 0 s)
    still serves the request that waited behind it; no second read."""
    from config import settings
    from models import TenantEvidenceRead
    from sqlalchemy import func, select

    monkeypatch.setattr(settings, "CORTEXSIM_TENANT_EVIDENCE_MIN_INTERVAL_SECONDS", 0)
    seed(session_factory)
    fake = FakeTenant(issues=copy.deepcopy(ISSUES), cases=copy.deepcopy(CASES),
                      endpoints=copy.deepcopy(ENDPOINTS))

    def slow(method, url, headers, body, timeout):
        time.sleep(0.1)
        return fake(method, url, headers, body, timeout)

    async def both():
        outs = await asyncio.gather(_read(session_factory, slow), _read(session_factory, slow))
        async with session_factory() as db:
            stored = (await db.execute(
                select(func.count()).select_from(TenantEvidenceRead))).scalar()
        return outs, stored

    outs, stored = asyncio.run(both())
    assert sorted(o["read"] for o in outs) == [False, True]
    waiter = next(o for o in outs if not o["read"])
    first = next(o for o in outs if o["read"])
    assert waiter["code"] == "TENANT_EVIDENCE_READ_RECENTLY"
    assert waiter["snapshot"]["id"] == first["snapshot"]["id"]
    assert stored == 1 and fake.total_calls == 4

    # With the interval at 0 a later, separate request reads again: only a
    # read that finished while the request waited is reused.
    again = asyncio.run(_read(session_factory, slow))
    assert again["read"] is True

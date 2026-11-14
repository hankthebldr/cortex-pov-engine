"""POST / GET /api/runs/{run_id}/tenant-evidence (spec §7.3, §7.4).

An explicit, metered, read-only tenant read for one run, stored as a snapshot
in ``tenant_evidence_reads``. GET makes no outbound call. The reader writes
nothing scoring reads. Transports are injected (``FakeTenant``); nothing
reaches a tenant.

The fixture issues (``tests/fixtures/tenant_reader/``) are 1001 and 1002 on
``target-01`` (case 501, observed 12:26:45 and 12:27:20 on 2025-11-01 UTC) and
1003, an ``FW`` row with no host (case 502, 12:27:55).
"""
from __future__ import annotations

import asyncio
import copy
import json
from datetime import datetime, timedelta

import pytest

from tests.connectors.tenant_fakes import ROUTES, FakeTenant, fixture

ISSUES = fixture("issue_search_reply.json")["reply"]["DATA"]
CASES = fixture("case_search_reply.json")["reply"]["DATA"]
ENDPOINTS = fixture("endpoint_get_reply.json")["reply"]["endpoints"]

#: The run's first step, naive UTC: 45 s before issue 1001 was observed.
STEP_AT = datetime(2025, 11, 1, 12, 26, 0)
RUN_HOST = "target-01"


@pytest.fixture(autouse=True)
def _fresh_request_bucket():
    """The per-tenant request bucket is a process singleton; a 429 here trips it."""
    from integrations.xsiam.ledger import ledger

    ledger.reset()
    yield
    ledger.reset()


@pytest.fixture
def client(make_client):
    from api.results import router as results_router
    from api.tenant_evidence import router

    return make_client(router, results_router)


@pytest.fixture
def inject(monkeypatch):
    """Route every get_connector call through the injected fake tenant."""
    import connectors.service as svc

    real = svc.get_connector

    def install(fake: FakeTenant) -> FakeTenant:
        monkeypatch.setattr(svc, "get_connector",
                            lambda kind, fetcher=None: real(kind, fetcher=fake))
        return fake
    return install


def default_tenant(**kw) -> FakeTenant:
    kw.setdefault("issues", copy.deepcopy(ISSUES))
    kw.setdefault("cases", copy.deepcopy(CASES))
    kw.setdefault("endpoints", copy.deepcopy(ENDPOINTS))
    return FakeTenant(**kw)


def obs_1001(**kw) -> dict:
    """The matcher's alert for issue 1001, linked to step-01's Result."""
    return {"external_id": "1001", "name": "synthetic-3",
            "observed_at": datetime(2025, 11, 1, 12, 26, 45),
            "raw": {"host_name": RUN_HOST}, **kw}


def seed(SessionLocal, *, run_id="r1", host=RUN_HOST, executed=True, credential=True,
         observations=(), config=None, observed=False, tc_verdict=None, mode="pull",
         extra_credentials=()):
    """One pull run with two Results; ``host=None`` leaves the run with no agent."""
    from models import Agent, Observation, Result, Run
    from security.credentials import CredentialStore

    async def go():
        async with SessionLocal() as db:
            if host is not None:
                db.add(Agent(agent_id="ag-1", hostname=host, os="linux"))
            db.add(Run(run_id=run_id, scenario_id="SIM-X", mode=mode,
                       status="staged" if mode == "push" else "complete",
                       target="ag-1" if host is not None else None, started_at=STEP_AT,
                       tc_verdict=tc_verdict,
                       tc_verdict_detail={"reason": "seeded"} if tc_verdict else None))
            r1 = Result(run_id=run_id, step_id="step-01", plane="EDR", signal_type="BIOC",
                        expected_detection="d1",
                        executed_at=STEP_AT if executed else None,
                        observed=observed,
                        observed_at=(datetime(2025, 11, 1, 12, 26, 45) if observed else None),
                        outcome="detected" if observed else None)
            r2 = Result(run_id=run_id, step_id="step-02", plane="EDR", signal_type="BIOC",
                        expected_detection="d2",
                        executed_at=STEP_AT + timedelta(seconds=30) if executed else None)
            db.add_all([r1, r2])
            await db.flush()
            for o in observations:
                db.add(Observation(run_id=run_id, result_id=r1.id, source="xsiam:acme", **o))
            await db.commit()
            if credential:
                await CredentialStore(db).put_integration(
                    name="acme", kind="xsiam", plaintext_secret="k" * 40,
                    config=config or {"fqdn": "api-acme.xdr.us.paloaltonetworks.com",
                                      "api_key_id": "1"})
                for name, kind, conf in extra_credentials:
                    await CredentialStore(db).put_integration(
                        name=name, kind=kind, plaintext_secret="k" * 40, config=conf)
                await db.commit()
    asyncio.run(go())


def post(client, run_id="r1", **params):
    return client.post(f"/api/runs/{run_id}/tenant-evidence", params=params)


def payload(body: dict) -> dict:
    return body["snapshot"]["payload"]


# ── the brief's tests ──────────────────────────────────────────────────────

def test_no_credential_is_pending_no_tenant_configured_with_null_issues(
        client, session_factory, inject):
    seed(session_factory, credential=False)
    fake = inject(default_tenant())
    r = post(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "pending" and body["code"] == "NO_TENANT_CONFIGURED"
    assert body["issues"] is None and body["read"] is False
    assert fake.total_calls == 0


def test_get_makes_zero_outbound_calls(client, session_factory, inject):
    seed(session_factory, observations=[obs_1001()])
    fake = inject(default_tenant())
    never = client.get("/api/runs/r1/tenant-evidence")
    assert never.status_code == 200, never.text
    assert never.json()["snapshot"] is None
    assert never.json()["code"] == "TENANT_EVIDENCE_NOT_READ"

    first = post(client)
    assert first.status_code == 200, first.text
    assert first.json()["read"] is True and fake.total_calls > 0
    fake.calls = {route: [] for route in ROUTES}

    r = client.get("/api/runs/r1/tenant-evidence")
    assert r.status_code == 200, r.text
    assert fake.total_calls == 0
    body = r.json()
    assert body["snapshot"] == first.json()["snapshot"]
    assert body["code"] == first.json()["code"]
    assert [h["id"] for h in body["history"]] == [first.json()["snapshot"]["id"]]
    assert "case_ledger" in body


def test_scoring_invariance_after_a_tenant_evidence_read(client, session_factory, inject):
    """A2: the read changes nothing the scorer, the results surface or MTTD reads."""
    from models import Result, Run
    from sqlalchemy import select

    seed(session_factory, observations=[obs_1001()], observed=True, tc_verdict="pending")
    inject(default_tenant())

    def scoring_state() -> str:
        async def go():
            async with session_factory() as db:
                run = (await db.execute(select(Run).where(Run.run_id == "r1"))).scalar_one()
                rows = (await db.execute(select(Result).where(Result.run_id == "r1")
                                         .order_by(Result.id))).scalars().all()
                return {"tc_verdict": run.tc_verdict, "tc_verdict_detail": run.tc_verdict_detail,
                        "results": [[r.observed, r.observed_at.isoformat() if r.observed_at
                                     else None, r.mttd_seconds, r.outcome] for r in rows]}
        api = client.get("/api/results/r1")
        assert api.status_code == 200, api.text
        return json.dumps({"api": api.json(), "db": asyncio.run(go())}, sort_keys=True)

    before = scoring_state()
    r = post(client)
    assert r.status_code == 200 and r.json()["read"] is True, r.text
    assert scoring_state() == before


def test_read_inside_min_interval_returns_latest_without_a_call(client, session_factory, inject):
    seed(session_factory, observations=[obs_1001()])
    fake = inject(default_tenant())
    first = post(client).json()
    sent = fake.total_calls

    again = post(client)
    assert again.status_code == 200, again.text
    body = again.json()
    assert body["read"] is False and body["code"] == "TENANT_EVIDENCE_READ_RECENTLY"
    assert isinstance(body["retry_after_seconds"], int)
    assert 0 < body["retry_after_seconds"] <= 60
    assert body["snapshot"] == first["snapshot"]
    assert fake.total_calls == sent

    forced = post(client, force="true")
    assert forced.status_code == 200, forced.text
    assert forced.json()["read"] is True
    assert fake.total_calls > sent
    assert forced.json()["snapshot"]["id"] != first["snapshot"]["id"]
    history = client.get("/api/runs/r1/tenant-evidence").json()["history"]
    assert [h["id"] for h in history] == [forced.json()["snapshot"]["id"],
                                         first["snapshot"]["id"]]


def test_read_with_no_attributed_issues_is_degraded_never_missed(client, session_factory, inject):
    seed(session_factory, host="lab-box-9")
    inject(default_tenant())
    r = post(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["code"] == "NO_ATTRIBUTED_ISSUES" and body["status"] == "degraded"
    detail = body["detail"]
    since, until = payload(body)["window"]
    assert "ingestion latency" in detail
    assert "lab-box-9" in detail
    assert since in detail and until in detail
    assert "Security" in detail and "domain" in detail
    assert "miss" not in r.text.lower()
    scope = body["issues"]["host_scope"]
    assert (scope["attributed"], scope["other_host"], scope["without_host"]) == (0, 2, 1)


def test_run_without_executed_at_issues_no_tenant_read(client, session_factory, inject):
    seed(session_factory, executed=False)
    fake = inject(default_tenant())
    r = post(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["code"] == "NO_RUN_WINDOW" and body["status"] == "degraded"
    assert body["read"] is False and body["issues"] is None
    assert fake.total_calls == 0
    assert client.get("/api/runs/r1/tenant-evidence").json()["code"] == "TENANT_EVIDENCE_NOT_READ"


def test_quota_refusal_keeps_rows_and_marks_incomplete(client, session_factory, inject):
    seed(session_factory, observations=[obs_1001()])
    fake = inject(default_tenant(status={"issues_by_id": 429}))
    r = post(client)
    assert r.status_code == 200, r.text
    body = r.json()
    p = payload(body)
    assert body["code"] == "QUOTA_REFUSED" and body["status"] == "degraded"
    assert isinstance(body["retry_after_seconds"], int)
    assert p["complete"] is False
    assert [i["id"] for i in p["issues"]["rows"]] == ["1001", "1002"]
    assert p["agreement"]["id_space"]["code"] == "QUOTA_REFUSED"
    assert p["agreement"]["id_space"]["hit"] is None
    assert p["cases"]["code"] == "QUOTA_REFUSED"
    assert p["endpoint"]["code"] == "QUOTA_REFUSED"
    assert (len(fake.calls["cases"]), len(fake.calls["endpoints"])) == (0, 0)


def test_failed_case_read_leaves_issues_visible(client, session_factory, inject):
    seed(session_factory)
    inject(default_tenant(status={"cases": 500}))
    body = post(client).json()
    p = payload(body)
    assert p["issues"]["code"] == "OK"
    assert [i["id"] for i in p["issues"]["rows"]] == ["1001", "1002"]
    assert p["cases"]["code"] == "XSIAM_API_ERROR"
    assert [(c["case_id"], c["issue_count"]) for c in p["cases"]["rows"]] == [("501", None)]
    assert p["endpoint"]["code"] == "OK"
    assert body["status"] == "degraded"


def test_ambiguous_endpoint_lists_each_record_and_claims_none(client, session_factory, inject):
    second = {**copy.deepcopy(ENDPOINTS[0]), "endpoint_id": "synthetic-endpoint-2",
              "last_seen": 1761000000000}
    seed(session_factory)
    inject(default_tenant(endpoints=[copy.deepcopy(ENDPOINTS[0]), second]))
    ep = payload(post(client).json())["endpoint"]
    assert ep["code"] == "AMBIGUOUS_ENDPOINT_RECORDS"
    assert ep["record"] is None
    assert [r["endpoint_id"] for r in ep["records"]] == ["synthetic-endpoint-1",
                                                         "synthetic-endpoint-2"]
    assert all(set(r) == {"endpoint_id", "last_seen"} for r in ep["records"])


def test_snapshot_carries_statement_data_notice_and_tenant_verified_0(
        client, session_factory, inject):
    from connectors.tenant_reader import DATA_NOTICE, STATEMENT

    seed(session_factory, observations=[obs_1001()])
    inject(default_tenant())
    body = post(client).json()
    p = payload(body)
    assert p["statement"] == STATEMENT
    assert p["data_notice"] == DATA_NOTICE
    assert p["tenant_verified"] == 0
    assert p["integration"] == "acme" and p["tenant"]
    assert p["complete"] is True and p["tenant_answered"] is True
    assert isinstance(p["queries_issued"], int) and p["queries_issued"] == 5
    assert body["snapshot"]["queries_issued"] == 5
    assert body["snapshot"]["reader_version"] == "1"
    assert body["code"] == "OK" and body["status"] == "ok"


def test_snapshot_records_tenant_version_or_null(client, session_factory, inject, monkeypatch):
    """PD9: one more request reads get_tenant_info, last; the snapshot records
    its version, or null with that sub-read's code. It never moves the
    snapshot's own code, status or completeness."""
    from integrations.xsiam.ledger import ledger

    from tests.connectors.tenant_fakes import TENANT_INFO

    def read_now(fake: FakeTenant, **params) -> tuple[dict, dict]:
        inject(fake)
        body = post(client, **params).json()
        assert body["read"] is True, body
        assert (body["code"], body["status"]) == ("OK", "ok")
        assert payload(body)["complete"] is True
        return body, payload(body)

    seed(session_factory, observations=[obs_1001()])

    # The reply PANW documents: licence fields, no version field.
    fake = default_tenant()
    _, p = read_now(fake)
    assert (p["tenant_version"], p["tenant_version_code"]) == (None, "TENANT_VERSION_NOT_REPORTED")
    assert len(fake.calls["tenant_info"]) == 1 and fake.calls["tenant_info"] == [{}]
    assert p["queries_issued"] == 5           # the four evidence requests, then this one

    # A reply that carries a version: recorded, neutralised.
    _, p = read_now(default_tenant(tenant_info={**TENANT_INFO, "version": "3.6.0​"}),
                    force="true")
    assert (p["tenant_version"], p["tenant_version_code"]) == ("3.6.0", "OK")

    # A numeric version is a version too; any other type is not one.
    _, p = read_now(default_tenant(tenant_info={**TENANT_INFO, "version": 3.6}), force="true")
    assert (p["tenant_version"], p["tenant_version_code"]) == ("3.6", "OK")
    _, p = read_now(default_tenant(tenant_info={**TENANT_INFO, "version": True}), force="true")
    assert (p["tenant_version"], p["tenant_version_code"]) == (None, "XSIAM_PARSE_ERROR")

    # A failed sub-read: null with its code; the request still counts.
    _, p = read_now(default_tenant(status={"tenant_info": 500}), force="true")
    assert (p["tenant_version"], p["tenant_version_code"]) == (None, "XSIAM_API_ERROR")
    assert p["queries_issued"] == 5

    # CortexSim's own budget refuses it once the evidence requests spent it: a
    # local refusal sends nothing, and the snapshot is not QUOTA_REFUSED.
    ledger.reset()
    monkeypatch.setenv("CORTEXSIM_XSIAM_MAX_REQUESTS_PER_MINUTE", "4")
    fake = default_tenant()
    body, p = read_now(fake, force="true")
    assert (p["tenant_version"], p["tenant_version_code"]) == (None, "QUOTA_REFUSED")
    assert fake.calls["tenant_info"] == [] and p["queries_issued"] == 4
    assert body["retry_after_seconds"] is None


def test_unknown_run_is_404_and_makes_no_call(client, inject):
    fake = inject(default_tenant())
    r = post(client, run_id="nope")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "RUN_NOT_FOUND"
    g = client.get("/api/runs/nope/tenant-evidence")
    assert g.status_code == 404 and g.json()["detail"]["code"] == "RUN_NOT_FOUND"
    assert fake.total_calls == 0


# ── carried rulings that change behaviour ──────────────────────────────────

def test_case_row_without_case_id_degrades_only_the_cases_section(
        client, session_factory, inject):
    """Ruling 5: a malformed case row is the cases section's parse error, never the read's."""
    seed(session_factory)
    bad = {"reply": {"DATA": [{"case_name": "synthetic-case-x"}], "TOTAL_COUNT": 1,
                     "FILTER_COUNT": 1}}
    inject(default_tenant(replies={"cases": bad}))
    p = payload(post(client).json())
    assert p["cases"]["code"] == "XSIAM_PARSE_ERROR"
    assert [(c["case_id"], c["issue_count"]) for c in p["cases"]["rows"]] == [("501", None)]
    assert [i["id"] for i in p["issues"]["rows"]] == ["1001", "1002"]
    assert p["issues"]["code"] == "OK"


def test_unscoped_run_publishes_unattributed_rows_and_null_other_host(
        client, session_factory, inject):
    """Ruling 6: no run host is UNSCOPED, never a hostless count claim."""
    seed(session_factory, host=None)
    fake = inject(default_tenant())
    body = post(client).json()
    assert body["code"] == "UNSCOPED" and body["status"] == "degraded"
    issues = body["issues"]
    assert issues["host_scope"] == {"host": None, "attributed": None, "other_host": None,
                                    "without_host": 1}
    assert issues["rows"] == []
    assert [i["id"] for i in issues["unattributed_rows"]] == ["1001", "1002", "1003"]
    assert payload(body)["endpoint"]["code"] == "UNSCOPED"
    assert len(fake.calls["endpoints"]) == 0


def test_reader_paths_follow_the_integration_overrides(client, session_factory, inject):
    """Ruling 4: the snapshot names the paths the connector actually read."""
    seed(session_factory, config={"fqdn": "api-acme.xdr.us.paloaltonetworks.com",
                                  "api_key_id": "1",
                                  "issues_path": "/public_api/v2/issue/search/"})
    fake = inject(default_tenant())
    p = payload(post(client).json())
    assert p["reader"]["issues"] == "POST /public_api/v2/issue/search/"
    assert p["reader"]["cases"] == "POST /public_api/v1/case/search/"
    assert any("/public_api/v2/issue/search/" in u for u in fake.urls)


# ── fix round 1 ────────────────────────────────────────────────────────────

def test_unscoped_agreement_claims_no_join_but_keeps_the_id_space(
        client, session_factory, inject):
    """Review 3: with no run host nothing can join, so no join count is claimed."""
    seed(session_factory, host=None, observations=[obs_1001()])
    inject(default_tenant())
    agreement = payload(post(client).json())["agreement"]
    assert agreement["both"] == {"id": None, "name_host_window": None}
    assert (agreement["matcher_only"], agreement["reader_only"], agreement["ambiguous"]) == (
        None, None, None)
    assert agreement["pairs"] == []
    assert agreement["unjoinable"] == 0
    assert agreement["id_space"] == {"probed": 1, "hit": 1, "no_hit": 0, "code": "OK"}


def test_malformed_endpoint_record_is_a_parse_error_not_not_found(
        client, session_factory, inject):
    """Review 5: a record the reader cannot read is never "no record"."""
    seed(session_factory)
    nameless = {"reply": {"endpoints": [{"endpoint_id": "synthetic-endpoint-9",
                                         "endpoint_status": "CONNECTED"}],
                          "total_count": 1, "result_count": 1}}
    inject(default_tenant(replies={"endpoints": nameless}))
    ep = payload(post(client).json())["endpoint"]
    assert ep["code"] == "XSIAM_PARSE_ERROR"
    assert ep["record"] is None


def test_min_interval_is_keyed_by_tenant_not_credential(client, session_factory, inject):
    """Review 6: two credentials for one tenant share the minimum interval."""
    seed(session_factory, extra_credentials=[(
        "acme-t", "xsiam_tenant",
        {"base_url": "https://api-acme.xdr.us.paloaltonetworks.com", "api_key_id": "1"})])
    fake = inject(default_tenant())
    first = post(client, integration="acme").json()
    assert first["read"] is True
    sent = fake.total_calls
    again = post(client, integration="acme-t").json()
    assert again["read"] is False and again["code"] == "TENANT_EVIDENCE_READ_RECENTLY"
    assert again["snapshot"]["id"] == first["snapshot"]["id"]
    assert fake.total_calls == sent


def test_push_run_has_no_window_and_makes_no_call(client, session_factory, inject):
    """Review 9: a push run's executed_at is bundle time, not execution time."""
    seed(session_factory, mode="push", host=None)
    fake = inject(default_tenant())
    body = post(client).json()
    assert body["code"] == "NO_RUN_WINDOW" and body["read"] is False
    assert "push_execution_time_unknown" in body["detail"]
    assert fake.total_calls == 0
    assert client.get("/api/runs/r1/tenant-evidence").json()["code"] == "TENANT_EVIDENCE_NOT_READ"


def test_reconcile_parse_error_on_page_one_reports_one_request_to_mcp(
        session_factory, make_client):
    """Review 7: a reconcile whose page 1 the tenant answered unreadably sent
    one request; the MCP reconcile tool reads that count off the route's error."""
    import httpx

    import connectors.service as svc
    from api.connectors import runs_reconcile_router
    from mcp_server.client import SimCoreClient, SimCoreError
    from mcp_server.tools.cortexsim import _pull_failed_page

    seed(session_factory)

    async def delivered():
        from models import Run
        from sqlalchemy import update
        async with session_factory() as db:
            await db.execute(update(Run).where(Run.run_id == "r1").values(delivered_at=STEP_AT))
            await db.commit()
    asyncio.run(delivered())

    real = svc.get_connector
    unreadable = lambda method, url, headers, body, timeout: (200, "<html>not json</html>")  # noqa: E731
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(svc, "get_connector", lambda kind, fetcher=None: real(kind, fetcher=unreadable))
        r = make_client(runs_reconcile_router).post(
            "/api/runs/r1/reconcile", params={"connector": "xsiam"})
    assert r.status_code == 502, r.text
    assert r.json()["detail"]["detail"]["page"] == 1

    transport = httpx.MockTransport(lambda req: httpx.Response(r.status_code, content=r.content,
                                                               headers=r.headers))

    async def via_mcp_client():
        mcp_client = SimCoreClient("http://simcore", transport=transport)
        try:
            await mcp_client.post("/api/runs/r1/reconcile")
        except SimCoreError as err:
            return err
        finally:
            await mcp_client.aclose()
    err = asyncio.run(via_mcp_client())
    assert err.code == "CONNECTOR_PULL_FAILED"
    assert _pull_failed_page(err) == 1

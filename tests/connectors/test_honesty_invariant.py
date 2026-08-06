"""THE honesty invariant, enforced as a table.

**Every infrastructure failure resolves `pending`.** Not `fail` (which claims
the customer's detection did not fire) and not `pass` (which claims it did).
CortexSim has no basis for either when it could not measure.

`pass` and `fail` are explicitly excluded from the allowed set in every case
below, so a future "tolerant" fix that returns 0 rows on an unknown shape — the
exact top-severity bug — cannot make these green again.

The one deliberate exception is the LAST test: zero rows from a healthy tenant
IS `fail`. That is the real answer, and an invariant that also swallowed it
would be useless.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta

import httpx
import pytest

NEVER = ("pass", "fail")


# ---------------------------------------------------------------------------
# Tier 2 — verification against a tenant
# ---------------------------------------------------------------------------


def _result():
    class _R:
        verification_xql = "dataset = xdr_data | filter x = 1"
        kpi_verdict = "pending"
        verified_at = None
        id = 1
        detection_id = "bioc-1"
        ttp_ref = None
    return _R()


def _client_over(handler):
    from integrations.xsiam.client import XsiamClient
    from integrations.xsiam.config import XsiamTenantConfig

    cfg = XsiamTenantConfig(base_url="https://api-t.xdr.us.paloaltonetworks.com",
                            region="us", auth_mode="standard", api_key_id="1")
    return XsiamClient(cfg, "key", transport=httpx.MockTransport(handler))


def _verdict_for(handler) -> str:
    """Drive the REAL client + runner + verifier over a scripted tenant."""
    from engine.verifier import verify_results, xsiam_query_runner

    r = _result()
    runner = xsiam_query_runner(_client_over(handler), {"relativeTime": 1000})
    asyncio.run(verify_results([r], runner))
    return r.kpi_verdict


def _xql(status=200, *, json_body=None, text=None, exc=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if exc is not None:
            raise exc
        if text is not None:
            return httpx.Response(status, text=text)
        if request.url.path.endswith("start_xql_query"):
            return httpx.Response(status, json={"reply": "q1"})
        return httpx.Response(status, json=json_body or {})
    return handler


@pytest.mark.parametrize("label,handler", [
    ("dns failure", _xql(exc=httpx.ConnectError("Name or service not known"))),
    ("connection refused", _xql(exc=httpx.ConnectError("[Errno 111] refused"))),
    ("tls error", _xql(exc=httpx.ConnectError("certificate verify failed"))),
    ("read timeout", _xql(exc=httpx.ReadTimeout("timed out"))),
    ("401", _xql(401)),
    ("403", _xql(403)),
    ("429", _xql(429)),
    ("500", _xql(500)),
    ("malformed json", _xql(200, text="<html>gateway</html>")),
    ("xql syntax error", _xql(200, json_body={"reply": {
        "err_code": "XQL_1001", "err_msg": "syntax error near 'x'"}})),
    ("200 quota envelope", _xql(200, json_body={"reply": {
        "err_code": "XQL_0003", "err_msg": "Daily quota exceeded"}})),
    ("unknown status", _xql(200, json_body={"reply": {"status": "FAILED"}})),
    ("unrecognised envelope", _xql(200, json_body={"reply": {
        "status": "SUCCESS", "results": "drifted-shape"}})),
])
def test_every_tenant_failure_resolves_pending(label, handler):
    verdict = _verdict_for(handler)
    assert verdict not in NEVER, (
        f"{label} produced '{verdict}' — an infrastructure problem was reported "
        f"as a statement about the customer's detection coverage")
    assert verdict == "pending", label


def test_query_timeout_resolves_pending():
    """A tenant stuck PENDING past max_wait. Kept separate because it needs the
    real polling loop rather than a single response."""
    from engine.verifier import verify_results, xsiam_query_runner

    def handler(request):
        if request.url.path.endswith("start_xql_query"):
            return httpx.Response(200, json={"reply": "q1"})
        return httpx.Response(200, json={"reply": {"status": "PENDING"}})

    r = _result()
    runner = xsiam_query_runner(_client_over(handler), {})

    async def _run(q):
        # max_wait=0 -> one poll then raise, so the test does not sleep.
        from integrations.xsiam.queries import row_count
        return row_count(await _client_over(handler).run_xql(
            q, {}, max_wait=0, interval=0))

    asyncio.run(verify_results([r], _run))
    assert r.kpi_verdict == "pending"
    assert runner is not None


def test_a_spent_query_budget_resolves_pending_never_fail(session_factory):
    from connectors.service import _BudgetExhausted, _budgeted, _new_budget
    from engine.verifier import verify_results

    async def _boom(_q):  # pragma: no cover — never reached, budget is 0
        return 5

    budget = _new_budget(0)
    budget["tenant"] = "acme"
    r = _result()
    asyncio.run(verify_results([r], _budgeted(_boom, budget)))
    assert r.kpi_verdict == "pending"
    assert _BudgetExhausted is not None


def test_an_open_quota_breaker_resolves_pending_on_every_path():
    """Driven through the REAL runner, because the breaker is charged there so
    that assertion runs — which never reach connectors.service._budgeted — are
    stopped too."""
    from engine.verifier import verify_results, xsiam_query_runner
    from integrations.xsiam.client import XsiamClient
    from integrations.xsiam.config import XsiamTenantConfig
    from integrations.xsiam.ledger import ledger

    ledger.reset()
    calls: list = []

    def handler(request):  # pragma: no cover — must never be reached
        calls.append(str(request.url))
        return httpx.Response(200, json={"reply": "q1"})

    cfg = XsiamTenantConfig(base_url="https://api-acme.xdr.us.paloaltonetworks.com",
                            region="us", auth_mode="standard", api_key_id="1")
    client = XsiamClient(cfg, "key", transport=httpx.MockTransport(handler))
    ledger.trip("api-acme.xdr.us.paloaltonetworks.com", cooldown_seconds=900)

    r = _result()
    asyncio.run(verify_results([r], xsiam_query_runner(client, {})))
    assert r.kpi_verdict == "pending"
    assert calls == [], "an open breaker must cost the tenant nothing at all"
    ledger.reset()


# ── the reason a DC is given, not just the verdict ─────────────────────────


def _seed(SessionLocal, run_id="run-x", *, xql="dataset = xdr_data"):
    from models import Result, Run, Scenario

    async def _do():
        async with SessionLocal() as db:
            db.add(Scenario(scenario_id="SIM-EDR-001", name="X", plane="EDR",
                            version="1.0", status="active", uc_ref="UCS-EDR-01",
                            uc_name="X", tc_ref="TC-EDR-01", tc_name="Y",
                            mitre_tactic="TA0006", mitre_tactic_name="CA",
                            mitre_technique="T1003.001", mitre_technique_name="L",
                            steps=[]))
            db.add(Run(run_id=run_id, scenario_id="SIM-EDR-001", mode="pull",
                       status="complete",
                       started_at=datetime.utcnow() - timedelta(minutes=5)))
            db.add(Result(run_id=run_id, step_id="step-01", plane="EDR",
                          signal_type="BIOC", expected_detection="d", observed=False,
                          executed_at=datetime.utcnow() - timedelta(minutes=5),
                          verification_xql=xql, kpi_verdict="pending"))
            await db.commit()
    asyncio.run(_do())


@pytest.mark.parametrize("config,secret_ok,expect_reason", [
    # A registered tenant whose config is invalid is NOT "no tenant configured".
    ({"base_url": "https://api-t.xdr.us.paloaltonetworks.com", "region": "us"},
     True, "tenant_config_invalid"),
    # Registered, valid, but not authorised for XQL.
    ({"base_url": "https://api-t.xdr.us.paloaltonetworks.com", "region": "us",
      "api_key_id": "1", "capabilities": ["read_alerts"]},
     True, "xql_not_authorised"),
])
def test_the_reason_names_the_real_problem_not_a_missing_tenant(
        session_factory, config, secret_ok, expect_reason):
    """F-09. The verdict (`pending`) was always right; the REASON was a lie, and
    it sent the DC to configure a tenant they had already configured."""
    from connectors.service import verify_run_now
    from models import Run
    from security.credentials import CredentialStore
    from sqlalchemy import select

    _seed(session_factory)

    async def _do():
        async with session_factory() as db:
            await CredentialStore(db).put_integration(
                name="acme", kind="xsiam_tenant", plaintext_secret="k"*40,
                config=config)
            await db.commit()
            run = (await db.execute(select(Run))).scalars().first()
            return await verify_run_now(db, run)

    out = asyncio.run(_do())
    assert out.tc_verdict == "pending"
    assert out.reason == expect_reason
    assert out.remediation, "a reason with no remediation is not actionable"
    assert out.queries_issued == 0


def test_no_integration_at_all_still_says_so(session_factory):
    from connectors.service import verify_run_now
    from models import Run
    from sqlalchemy import select

    _seed(session_factory)

    async def _do():
        async with session_factory() as db:
            run = (await db.execute(select(Run))).scalars().first()
            return await verify_run_now(db, run)

    out = asyncio.run(_do())
    assert out.tc_verdict == "pending"
    assert out.reason == "no_tenant_integration"


def test_a_run_with_no_verification_xql_is_pending_not_pass(session_factory):
    from connectors.service import verify_run_now
    from models import Run
    from sqlalchemy import select

    _seed(session_factory, xql=None)

    async def _do():
        async with session_factory() as db:
            run = (await db.execute(select(Run))).scalars().first()
            return await verify_run_now(db, run)

    out = asyncio.run(_do())
    assert out.tc_verdict not in ("pass", "fail")


# ── reconcile / Tier 1 ─────────────────────────────────────────────────────


def test_a_pull_that_returns_no_alerts_leaves_the_verdict_pending(session_factory):
    """Not `fail`. An empty tenant window means the detection has not been seen
    YET; MTTD is unmeasured, and unmeasured is `pending`."""
    from connectors.service import reconcile_run
    from models import Result, Run
    from security.credentials import CredentialStore
    from sqlalchemy import select

    _seed(session_factory, xql=None)

    def fetcher(method, url, headers, body, timeout):
        return 200, json.dumps({"reply": {"alerts": []}})

    async def _do():
        async with session_factory() as db:
            await CredentialStore(db).put_integration(
                name="acme", kind="xsiam", plaintext_secret="k"*40,
                config={"fqdn": "api-t.xdr.us.paloaltonetworks.com", "api_key_id": "1"})
            await db.commit()
            run = (await db.execute(select(Run))).scalars().first()
            results = list((await db.execute(select(Result))).scalars().all())
            await reconcile_run(db, run, results, connector_kind="xsiam", fetcher=fetcher)
            await db.refresh(run)
            return run.tc_verdict

    assert asyncio.run(_do()) not in ("pass", "fail")


def test_alerts_with_unreadable_timestamps_are_reported_not_counted(session_factory):
    """The observations exist in the tenant; CortexSim could not date them. That
    is a gap in the MEASUREMENT, and the summary has to say so — otherwise the
    coverage number silently understates and the DC calls the detection weak."""
    from connectors.service import reconcile_run
    from models import Result, Run
    from security.credentials import CredentialStore
    from sqlalchemy import select

    _seed(session_factory, xql=None)
    body = {"reply": {"alerts": [
        {"alert_id": str(i), "detection_timestamp": "who-knows", "name": "x"}
        for i in range(3)]}}

    def fetcher(method, url, headers, b, timeout):
        return 200, json.dumps(body)

    async def _do():
        async with session_factory() as db:
            await CredentialStore(db).put_integration(
                name="acme", kind="xsiam", plaintext_secret="k"*40,
                config={"fqdn": "api-t.xdr.us.paloaltonetworks.com", "api_key_id": "1"})
            await db.commit()
            run = (await db.execute(select(Run))).scalars().first()
            results = list((await db.execute(select(Result))).scalars().all())
            return (await reconcile_run(db, run, results, connector_kind="xsiam",
                                        fetcher=fetcher)).summary

    summary = asyncio.run(_do())
    assert summary["dropped_unparseable_timestamps"] == 3
    assert "floor, not a measurement" in summary["warning"]


# ── the deliberate NON-exception ───────────────────────────────────────────


def test_zero_rows_from_a_healthy_tenant_is_still_fail():
    """This is the real answer and must survive every guard above. An invariant
    that also swallowed a genuine zero-row result would make the harness
    incapable of ever reporting a missed detection."""
    handler = _xql(200, json_body={"reply": {"status": "SUCCESS",
                                             "results": {"data": []}}})
    assert _verdict_for(handler) == "fail"


def test_rows_from_a_healthy_tenant_is_pass():
    handler = _xql(200, json_body={"reply": {"status": "SUCCESS",
                                             "results": {"data": [{"a": 1}]}}})
    assert _verdict_for(handler) == "pass"

"""A refusal by CortexSim's OWN request budget is never something the tenant did."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime

import httpx
import pytest

from connectors import preflight as pf
from connectors.xsiam import XsiamConnector, IssueReadError
from integrations.xsiam.config import XsiamTenantConfig
from integrations.xsiam.ledger import ledger, tenant_ledger_key

HOST = "api-local.xdr.us.paloaltonetworks.com"
BASE = f"https://{HOST}"
CFG = XsiamTenantConfig(base_url=BASE, region="us", auth_mode="standard", api_key_id="1")


def _client(handler):
    from integrations.xsiam.client import XsiamClient
    return XsiamClient(CFG, "key", transport=httpx.MockTransport(handler))


def _snap():
    return ledger.snapshot(tenant_ledger_key(BASE), limit=500)


@pytest.mark.asyncio
async def test_local_refusal_inside_tenant_budget_does_not_open_the_breaker(monkeypatch):
    from integrations.xsiam.exceptions import XsiamQuotaError
    from integrations.xsiam.guards import tenant_budget

    monkeypatch.setenv("CORTEXSIM_XSIAM_MAX_REQUESTS_PER_MINUTE", "1")
    client = _client(lambda r: httpx.Response(200, json={"reply": "qid-1"}))
    with pytest.raises(XsiamQuotaError) as ei:
        async with tenant_budget(client):
            await client.run_xql("dataset = x", {"relativeTime": 1000}, max_wait=5)
    assert ei.value.local is True and ei.value.upstream_status is None
    snap = _snap()
    assert snap["breaker_open"] is False and snap["trips"] == 0


@pytest.mark.asyncio
async def test_server_time_429_trips_the_breaker():
    from integrations.xsiam.exceptions import XsiamQuotaError

    client = _client(lambda r: httpx.Response(429))
    with pytest.raises(XsiamQuotaError) as ei:
        await client.server_time()
    assert ei.value.tripped is True and ei.value.local is False
    assert _snap()["trips"] == 1


def test_reconcile_preflight_with_open_breaker_claims_no_tenant_answer():
    ledger.trip(tenant_ledger_key(BASE), cooldown_seconds=600)
    calls = []

    def fetcher(*a):
        calls.append(a)
        return 200, "{}"

    r = pf.preflight_reconcile({"fqdn": HOST, "api_key_id": "1"}, "k", fetcher=fetcher)
    assert calls == []
    assert r.queries_issued == 0
    by = {s.id: s for s in r.stages}
    for sid in ("dns_tls", "auth", "scope_alerts"):
        assert by[sid].status != pf.OK, sid
    assert "tenant answered" not in json.dumps(r.to_dict())
    assert r.overall != pf.READY
    assert "before it was sent" in by["dns_tls"].detail


@pytest.mark.asyncio
async def test_tenant_preflight_local_refusal_is_not_unreachable():
    ledger.trip(tenant_ledger_key(BASE), cooldown_seconds=600)
    seen = []
    client = _client(lambda r: seen.append(1) or httpx.Response(200, json={}))
    r = await pf.preflight_tenant(client, integration_name="t", base_url_host=HOST,
                                  response_layer=False)
    assert seen == []
    assert r.queries_issued == 0
    auth = next(s for s in r.stages if s.id == "auth")
    assert auth.code != pf.PF_UNREACHABLE
    assert "could not reach the tenant" not in json.dumps(r.to_dict())
    assert "before it was sent" in auth.detail
    assert r.overall != pf.READY


def test_chunked_read_counts_only_requests_actually_sent(monkeypatch):
    monkeypatch.setenv("CORTEXSIM_XSIAM_MAX_REQUESTS_PER_MINUTE", "1")
    from connectors.base import ConnectorConfig

    sent = []

    def fetcher(method, url, headers, body, timeout):
        sent.append(1)
        return 200, json.dumps({"reply": {"DATA": [], "result_count": 0, "total_count": 0}})

    cfg = ConnectorConfig(integration_name="x", secret="k",
                          config={"base_url": BASE, "api_key_id": "1"})
    with pytest.raises(IssueReadError) as ei:
        XsiamConnector(fetcher=fetcher).pull_issue_status(
            cfg, [str(i) for i in range(150)])
    assert len(sent) == 1
    assert ei.value.detail["pages"] == 1
    assert ei.value.detail.get("refused_locally") is True


def test_reconcile_local_refusal_keeps_the_credential_and_the_remediation_honest(
        session_factory):
    from connectors.service import ReconcileError, reconcile_run
    from integrations.xsiam.codes import LOCAL_REFUSAL_REMEDIATION
    from models import Result, Run
    from security.credentials import CredentialStore
    from sqlalchemy import select
    from tests.connectors.test_honesty_invariant import _seed

    _seed(session_factory, xql=None)
    ledger.trip(tenant_ledger_key(BASE), cooldown_seconds=600)

    async def _do():
        async with session_factory() as db:
            store = CredentialStore(db)
            await store.put_integration(
                name="acme", kind="xsiam", plaintext_secret="k" * 40,
                config={"fqdn": HOST, "api_key_id": "1"})
            await db.commit()
            run = (await db.execute(select(Run))).scalars().first()
            results = list((await db.execute(select(Result))).scalars().all())
            with pytest.raises(ReconcileError) as ei:
                await reconcile_run(db, run, results, connector_kind="xsiam",
                                    fetcher=lambda *a: (200, "{}"))
            row = await store.get_integration("acme")
            return ei.value, row.last_verified_ok, row.last_verified_error

    err, ok, last_err = asyncio.run(_do())
    assert err.detail["refused_locally"] is True
    assert err.detail["remediation"] == LOCAL_REFUSAL_REMEDIATION
    assert "analysts share" not in err.detail["remediation"]
    assert ok is None and last_err is None


@pytest.mark.asyncio
async def test_error_body_carries_retry_after_only_when_set():
    from integrations.xsiam.exceptions import XsiamQuotaError
    from types import SimpleNamespace

    from main import xsiam_error_handler

    req = SimpleNamespace(method="POST", url="http://t/x")
    with_hint = await xsiam_error_handler(req, XsiamQuotaError("x", retry_after_seconds=7))
    without = await xsiam_error_handler(req, XsiamQuotaError("x"))
    assert json.loads(with_hint.body)["retry_after_seconds"] == 7
    assert "retry_after_seconds" not in json.loads(without.body)


# ── round 2 ────────────────────────────────────────────────────────────────

def _fx(name):
    from pathlib import Path
    return json.loads((Path(__file__).resolve().parents[1] / "fixtures" / "socfw" / name).read_text())


def _socfw_tenant(seen):
    docs = {"SOCFWTenantConfig": _fx("tenant_config.json"),
            "SOCFWConfigModes": _fx("config_modes.json")}

    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        seen.append(p)
        if p.endswith("healthcheck"):
            return httpx.Response(200, json={"reply": {"status": "ok"}},
                                  headers={"date": "Wed, 11 Nov 2026 12:00:00 GMT"})
        if p.endswith("contentpacks/metadata/installed"):
            return httpx.Response(200, json=_fx("installed_packs.json"))
        if p.endswith("lists/names"):
            return httpx.Response(200, json=_fx("list_names.json"))
        if "/lists/" in p:
            return httpx.Response(200, json=docs[p.rsplit("/", 1)[1]])
        if p.endswith("start_xql_query"):
            return httpx.Response(200, json={"reply": "q1"})
        return httpx.Response(200, json={"reply": {"status": "SUCCESS",
                                                   "results": {"data": []}}})
    return handler


@pytest.mark.asyncio
async def test_response_layer_local_list_refusal_is_not_a_tenant_429_and_is_not_counted(
        monkeypatch):
    monkeypatch.setenv("CORTEXSIM_XSIAM_MAX_REQUESTS_PER_MINUTE", "3")
    seen: list = []
    client = _client(_socfw_tenant(seen))
    report = pf.PreflightReport(tenant="t", kind="xsiam_tenant")
    stage = await pf._probe_response_layer(client, report, datetime(2026, 11, 11, 12))
    assert len(seen) == 3                       # packs, names, ONE list
    assert report.queries_issued == 3           # only what left
    assert "HTTP 429" not in json.dumps(stage.__dict__, default=str)
    assert "rate or quota limit" not in json.dumps(stage.__dict__, default=str)


@pytest.mark.asyncio
async def test_mid_poll_local_refusal_says_the_query_was_started(monkeypatch):
    monkeypatch.setenv("CORTEXSIM_XSIAM_MAX_REQUESTS_PER_MINUTE", "3")
    seen: list = []

    # healthcheck(1), start(2), first poll(3) answers PENDING; the second poll is
    # refused by our own bucket AFTER the start reached the tenant.
    def handler(request):
        if request.url.path.endswith("get_query_results"):
            seen.append(request.url.path)
            return httpx.Response(200, json={"reply": {"status": "PENDING"}})
        return _socfw_tenant(seen)(request)
    client = _client(handler)
    r = await pf.preflight_tenant(client, integration_name="t", base_url_host=HOST,
                                  response_layer=False)
    st = next(s for s in r.stages if s.id == "scope_xql")
    assert st.code == pf.PF_NOT_PROBED
    assert "WAS started on the tenant" in st.detail
    assert "was not contacted" not in st.detail and "before it was sent" not in st.detail
    assert r.queries_issued == 2                # healthcheck + the started query
    assert "xql" not in r.capabilities_denied


@pytest.mark.asyncio
async def test_mid_poll_refusal_keeps_the_daily_count_and_a_unsent_start_refunds_it(
        monkeypatch):
    from integrations.xsiam.exceptions import XsiamQuotaError
    from integrations.xsiam.guards import tenant_budget

    # unsent start: breaker not open, bucket 0 tokens left
    monkeypatch.setenv("CORTEXSIM_XSIAM_MAX_REQUESTS_PER_MINUTE", "1")
    from connectors import tuning
    ledger.acquire_request(tenant_ledger_key(BASE), per_minute=1)  # spend the only token
    client = _client(lambda r: httpx.Response(200, json={"reply": "q"}))
    for _ in range(3):
        with pytest.raises(XsiamQuotaError) as ei:
            async with tenant_budget(client):
                await client.start_xql_query("dataset = x", None)
        assert ei.value.local and not ei.value.query_started
    assert _snap()["queries_last_24h"] == 0


def test_lookback_refused_locally_does_not_claim_no_alert_exists(monkeypatch):
    monkeypatch.setenv("CORTEXSIM_XSIAM_MAX_REQUESTS_PER_MINUTE", "1")
    calls = []

    def fetcher(method, url, headers, body, timeout):
        calls.append(url)
        return 200, json.dumps({"reply": {"alerts": [], "total_count": 0,
                                          "result_count": 0}})

    r = pf.preflight_reconcile({"fqdn": HOST, "api_key_id": "1"}, "k", fetcher=fetcher)
    assert len(calls) == 1 and r.queries_issued == 1
    shape = next(s for s in r.stages if s.id == "alert_shape")
    assert shape.code == pf.PF_NOT_PROBED
    assert "no alert in the last" not in shape.detail
    assert "Raise one alert" not in (shape.remediation or "")


def test_multi_page_local_refusal_says_what_was_fetched(monkeypatch):
    monkeypatch.setenv("CORTEXSIM_XSIAM_MAX_REQUESTS_PER_MINUTE", "1")
    from connectors.base import ConnectorConfig

    def fetcher(method, url, headers, body, timeout):
        rd = json.loads(body)["request_data"]
        page = [{"alert_id": str(i), "name": "a", "detection_timestamp": 1780000000000,
                 "mitre_technique_ids": ["T1003"]} for i in range(100)]
        return 200, json.dumps({"reply": {"total_count": 300, "result_count": 100,
                                          "alerts": page}})

    cfg = ConnectorConfig(integration_name="x", secret="k",
                          config={"base_url": BASE, "api_key_id": "1"})
    res = XsiamConnector(fetcher=fetcher).pull(cfg, since=datetime(2026, 1, 1),
                                               until=datetime(2026, 1, 2))
    assert res.ok is False and res.detail["pages"] == 1
    assert "not contacted" not in res.error
    assert "already fetched" in res.error


def test_skipped_stages_after_a_local_refusal_are_not_labelled_blocked():
    ledger.trip(tenant_ledger_key(BASE), cooldown_seconds=600)
    r = pf.preflight_reconcile({"fqdn": HOST, "api_key_id": "1"}, "k",
                               fetcher=lambda *a: (200, "{}"))
    skipped = [s for s in r.stages if s.status == pf.SKIPPED]
    assert skipped
    for s in skipped:
        assert s.code == pf.PF_NOT_PROBED and "blocked" not in s.detail


def test_installed_packs_local_refusal_gets_the_local_remediation(monkeypatch):
    monkeypatch.setenv("CORTEXSIM_XSIAM_MAX_REQUESTS_PER_MINUTE", "1")
    from integrations.xsiam.codes import LOCAL_REFUSAL_REMEDIATION

    ledger.acquire_request(tenant_ledger_key(BASE), per_minute=1)   # bucket empty
    client = _client(lambda r: httpx.Response(200, json={}))
    report = pf.PreflightReport(tenant="t", kind="xsiam_tenant")
    stage = asyncio.run(pf._probe_response_layer(client, report, datetime(2026, 11, 11, 12)))
    assert stage.remediation == LOCAL_REFUSAL_REMEDIATION
    assert "role of the xsiam_tenant API key" not in (stage.remediation or "")
    assert report.queries_issued == 0


@pytest.mark.asyncio
async def test_budgeted_refusal_message_says_it_was_not_sent():
    from api.xsiam import _budgeted
    from integrations.xsiam.exceptions import XsiamQuotaError

    ledger.trip(tenant_ledger_key(BASE), cooldown_seconds=600)
    client = _client(lambda r: httpx.Response(200, json={}))
    with pytest.raises(XsiamQuotaError) as ei:
        async with _budgeted(client):
            pass
    assert "before it was sent" in ei.value.detail and ei.value.local
    assert ei.value.retry_after_seconds >= 1


def test_trip_log_no_longer_says_xql_breaker(caplog):
    import logging
    with caplog.at_level(logging.WARNING):
        ledger.trip(tenant_ledger_key(BASE), cooldown_seconds=5)
    assert "XQL quota breaker" not in caplog.text


def test_refresh_case_ledger_local_refusal_uses_local_remediation(session_factory):
    from connectors.service import ReconcileError, refresh_case_ledger
    from integrations.xsiam.codes import LOCAL_REFUSAL_REMEDIATION
    from models import Run
    from sqlalchemy import select
    from tests.connectors.test_case_ledger_sweep import seed, stored_ledger

    seed(session_factory, [("r1", datetime.utcnow(), stored_ledger())])
    ledger.trip(tenant_ledger_key("api-acme.xdr.us.paloaltonetworks.com"), cooldown_seconds=600)

    async def go():
        async with session_factory() as db:
            run = (await db.execute(select(Run))).scalar_one()
            with pytest.raises(ReconcileError) as ei:
                await refresh_case_ledger(db, run, fetcher=lambda *a: (200, "{}"), force=True)
            return ei.value

    err = asyncio.run(go())
    assert err.detail["remediation"] == LOCAL_REFUSAL_REMEDIATION
    assert "analysts share" not in err.detail["remediation"]


def test_issue_read_local_refusal_uses_local_remediation(session_factory, monkeypatch):
    monkeypatch.setenv("CORTEXSIM_XSIAM_MAX_REQUESTS_PER_MINUTE", "1")
    from connectors.base import ConnectorConfig, ObservedAlert
    from connectors.service import build_and_store_case_ledger
    from integrations.xsiam.codes import LOCAL_REFUSAL_REMEDIATION
    from models import Run
    from tests.connectors.test_case_ledger_sweep import tenant

    fetcher, _ = tenant()
    cfg = ConnectorConfig(integration_name="acme", secret="k",
                          config={"fqdn": "api-acme.xdr.us.paloaltonetworks.com",
                                  "api_key_id": "1"})
    alerts = [ObservedAlert(source="xsiam", observed_at=datetime(2026, 11, 11, 10),
                            external_id="a1", severity="high", incident_id="900")]

    async def go():
        async with session_factory() as db:
            run = Run(run_id="rx", scenario_id="SIM-X", mode="pull", status="complete")
            led, _q = await build_and_store_case_ledger(
                db, run, conn=XsiamConnector(fetcher=fetcher), cfg=cfg,
                alerts=alerts, basis="alert_incident_ids")
            return led

    led = asyncio.run(go())
    assert led["issue_read"]["ok"] is False
    assert led["issue_read"]["remediation"] == LOCAL_REFUSAL_REMEDIATION


def test_auto_reconcile_reports_a_local_refusal_apart_from_quota_tripped(session_factory):
    from connectors.service import auto_reconcile_once
    from tests.connectors.test_honesty_invariant import _seed
    from security.credentials import CredentialStore

    _seed(session_factory, xql=None)
    ledger.trip(tenant_ledger_key(HOST), cooldown_seconds=600)

    async def go():
        async with session_factory() as db:
            await CredentialStore(db).put_integration(
                name="acme", kind="xsiam", plaintext_secret="k" * 40,
                config={"fqdn": HOST, "api_key_id": "1"})
            await db.commit()
            return await auto_reconcile_once(db, lookback_seconds=3600,
                                             window_seconds=600, fetcher=lambda *a: (200, "{}"))

    stats = asyncio.run(go())
    assert stats["refused_locally"] is True
    assert stats["quota_tripped"] is False


# ── round 3 ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_public_get_query_results_never_claims_the_query_was_started(monkeypatch):
    from integrations.xsiam.exceptions import XsiamQuotaError

    monkeypatch.setenv("CORTEXSIM_XSIAM_MAX_REQUESTS_PER_MINUTE", "1")
    ledger.acquire_request(tenant_ledger_key(BASE), per_minute=1)   # bucket empty
    seen = []
    client = _client(lambda r: seen.append(1) or httpx.Response(200, json={}))
    with pytest.raises(XsiamQuotaError) as ei:
        await client.get_query_results("never-issued-id")
    assert seen == []
    assert ei.value.query_started is None
    assert "WAS started" not in ei.value.detail
    assert "before it was sent" in ei.value.detail


def test_path_mismatch_is_not_claimed_after_the_lookback_was_refused(monkeypatch):
    monkeypatch.setenv("CORTEXSIM_XSIAM_MAX_REQUESTS_PER_MINUTE", "1")
    alert = {"alert_id": "1", "name": "x", "source": "XDR BIOC",
             "detection_timestamp": 1780000000000,
             "mitre_technique_id_and_name": ["T1003.001 - LSASS Memory"]}

    def fetcher(method, url, headers, body, timeout):
        rows = [alert] if "/v2/" in url else []
        return 200, json.dumps({"reply": {"alerts": rows, "total_count": len(rows),
                                          "result_count": len(rows)}})

    real = pf._pull_alternate

    def refilled(*a, **k):          # a token arrives between lookback and alternate
        ledger.reset()
        return real(*a, **k)

    monkeypatch.setattr(pf, "_pull_alternate", refilled)
    r = pf.preflight_reconcile({"fqdn": HOST, "api_key_id": "1"}, "k", fetcher=fetcher)
    shape = next(s for s in r.stages if s.id == "alert_shape")
    assert shape.code != pf.PF_ALERTS_PATH_MISMATCH
    assert shape.code == pf.PF_NOT_PROBED


def test_case_refresh_sweep_reports_a_local_refusal_apart_from_quota_tripped(
        session_factory):
    from tests.connectors.test_case_ledger_sweep import seed, stored_ledger, sweep

    seed(session_factory, [("r1", datetime.utcnow(), stored_ledger())])
    ledger.trip(tenant_ledger_key("api-acme.xdr.us.paloaltonetworks.com"),
                cooldown_seconds=600)
    out = sweep(session_factory, lambda *a: (200, "{}"))
    assert out["refused_locally"] == 1
    assert out["quota_tripped"] == 0


def test_verify_sweep_records_a_local_refusal_not_a_tenant_quota_event(session_factory):
    from connectors.service import auto_verify_once
    from integrations.xsiam.exceptions import XsiamQuotaError
    from models import Run
    from sqlalchemy import select
    from tests.connectors.test_auto_verify import _seed_run

    for i in range(3):
        _seed_run(session_factory, run_id=f"run-l{i}")
    calls = []

    async def runner(query: str) -> int:
        calls.append(query)
        raise XsiamQuotaError("own budget", local=True, retry_after_seconds=5)

    async def go():
        async with session_factory() as db:
            stats = await auto_verify_once(db, lookback_seconds=86400, runner=runner,
                                           tenant="acme-prod")
            runs = (await db.execute(select(Run))).scalars().all()
            return stats, [r.tc_verdict_detail for r in runs], [r.tc_verdict for r in runs]

    stats, details, verdicts = asyncio.run(go())
    assert len(calls) == 1                       # still stops the whole sweep
    assert stats["quota_tripped"] is False and stats["refused_locally"] is True
    for d in details:
        v = (d or {}).get("verification")
        if v:
            assert v["quota_tripped"] is False and v["refused_locally"] is True
    assert all(v not in ("pass", "fail") for v in verdicts)


def test_pull_result_to_dict_uses_local_remediation_for_a_local_refusal():
    from connectors.base import PullResult
    from integrations.xsiam.codes import LOCAL_REFUSAL_REMEDIATION

    d = PullResult(ok=False, connector="xsiam", code="XSIAM_QUOTA_ERROR", error="x",
                   detail={"refused_locally": True}).to_dict()
    assert d["remediation"] == LOCAL_REFUSAL_REMEDIATION
    t = PullResult(ok=False, connector="xsiam", code="XSIAM_QUOTA_ERROR", error="x",
                   detail={}).to_dict()
    assert "analysts share" in t["remediation"]


def test_a_tenant_refusal_keeps_the_role_remediation_even_if_a_later_read_is_local(
        monkeypatch):
    monkeypatch.setenv("CORTEXSIM_XSIAM_MAX_REQUESTS_PER_MINUTE", "1")
    client = _client(lambda r: httpx.Response(403, json={}))
    report = pf.PreflightReport(tenant="t", kind="xsiam_tenant")
    stage = asyncio.run(pf._probe_response_layer(client, report, datetime(2026, 11, 11, 12)))
    assert "role of the xsiam_tenant API key" in (stage.remediation or "")


@pytest.mark.asyncio
async def test_a_refused_poll_keeps_the_daily_count_for_the_started_query(monkeypatch):
    from integrations.xsiam.exceptions import XsiamQuotaError
    from integrations.xsiam.guards import tenant_budget

    monkeypatch.setenv("CORTEXSIM_XSIAM_MAX_REQUESTS_PER_MINUTE", "2")

    def handler(request):
        if request.url.path.endswith("get_query_results"):
            return httpx.Response(200, json={"reply": {"status": "PENDING"}})
        return httpx.Response(200, json={"reply": "q1"})

    client = _client(handler)
    with pytest.raises(XsiamQuotaError) as ei:
        async with tenant_budget(client):
            await client.run_xql("dataset = x", {"relativeTime": 1000},
                                 max_wait=5, interval=0)
    assert ei.value.query_started == "q1"
    assert _snap()["queries_last_24h"] == 1      # the start reached the tenant
    assert _snap()["trips"] == 0


# ── round 4: the verify sweep's accounting and the response-layer mix ──────


def _book(run):
    return (run.tc_verdict_detail or {}).get("verification") or {}


def _verify_rig(monkeypatch):
    """A real ``xsiam_query_runner`` over a recording transport."""
    from engine.verifier import xsiam_query_runner

    sent: list = []

    def handler(request):
        sent.append(request.url.path)
        if request.url.path.endswith("start_xql_query"):
            return httpx.Response(200, json={"reply": "q1"})
        return httpx.Response(200, json={"reply": {"status": "SUCCESS",
                                                   "results": {"data": [{"a": 1}]}}})

    return sent, xsiam_query_runner(_client(handler), {"relativeTime": 1000})


def test_a_locally_refused_verify_is_not_an_attempt_and_never_reaches_the_cap(
        session_factory, monkeypatch):
    from config import settings
    from connectors.service import auto_verify_once, verify_run_now
    from integrations.xsiam.codes import LOCAL_REFUSAL_REMEDIATION
    from models import Run
    from sqlalchemy import select
    from tests.connectors.test_auto_verify import _seed_run

    monkeypatch.setenv("CORTEXSIM_XSIAM_MAX_REQUESTS_PER_MINUTE", "1")
    monkeypatch.setattr(settings, "CORTEXSIM_AUTO_VERIFY_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(settings, "CORTEXSIM_AUTO_VERIFY_BACKOFF_SECONDS", 0)
    ledger.acquire_request(tenant_ledger_key(BASE), per_minute=1)   # bucket empty
    sent, runner = _verify_rig(monkeypatch)
    _seed_run(session_factory, run_id="run-local")

    async def go():
        sweeps, books = [], []
        async with session_factory() as db:
            for _ in range(4):
                sweeps.append(await auto_verify_once(
                    db, lookback_seconds=86400, runner=runner, tenant="acme-prod",
                    max_attempts=3, backoff_seconds=0))
                run = (await db.execute(select(Run))).scalar_one()
                books.append(_book(run))
            manual = await verify_run_now(db, run, runner=runner, tenant="acme-prod")
            return sweeps, books, manual, run.tc_verdict

    sweeps, books, manual, verdict = asyncio.run(go())
    assert sent == []                                    # nothing reached the tenant
    for stats, book in zip(sweeps, books):
        assert stats["runs_considered"] == 1             # never skipped as attempt_cap
        assert stats["queries_issued"] == 0 and stats["runs_verified"] == 0
        assert stats["refused_locally"] is True and stats["quota_tripped"] is False
        assert int(book.get("attempts") or 0) == 0
        assert not book.get("last_attempt_at")
        assert book["queries_issued"] == 0 and book["refused_locally"] is True
    assert manual.reason == "refused_locally"            # not attempt_cap
    assert manual.remediation == LOCAL_REFUSAL_REMEDIATION
    assert (manual.retry_after_seconds or 0) >= 1
    assert manual.queries_issued == 0
    assert verdict == "pending"


def test_a_spent_daily_budget_stops_the_verify_sweep_and_is_not_issued(
        session_factory, monkeypatch):
    from connectors.service import auto_verify_once, verify_run_now
    from models import Run
    from sqlalchemy import select
    from tests.connectors.test_auto_verify import _seed_run

    monkeypatch.setenv("CORTEXSIM_XSIAM_MAX_QUERIES_PER_DAY", "1")
    ledger.charge(tenant_ledger_key(BASE), limit=1)                 # day spent
    sent, real = _verify_rig(monkeypatch)
    tried: list = []

    async def runner(query: str) -> int:
        tried.append(query)
        return await real(query)

    for i in range(3):
        _seed_run(session_factory, run_id=f"run-day{i}")

    async def go():
        async with session_factory() as db:
            stats = await auto_verify_once(db, lookback_seconds=86400, runner=runner,
                                           tenant="acme-prod", backoff_seconds=0)
            runs = (await db.execute(select(Run).order_by(Run.run_id))).scalars().all()
            manual = await verify_run_now(db, runs[0], runner=runner, tenant="acme-prod")
            return stats, [_book(r) for r in runs], [r.tc_verdict for r in runs], manual

    stats, books, verdicts, manual = asyncio.run(go())
    assert sent == []
    assert len(tried) == 2                    # the sweep stopped at the refusal (+1 manual)
    assert stats["queries_issued"] == 0 and stats["runs_verified"] == 0
    assert stats["refused_locally"] is True and stats["quota_tripped"] is False
    assert all(int(b.get("attempts") or 0) == 0 for b in books)
    assert verdicts[0] == "pending"           # never fail
    assert all(v not in ("pass", "fail") for v in verdicts)
    assert manual.reason == "refused_locally" and manual.queries_issued == 0
    assert "CORTEXSIM_XSIAM_MAX_QUERIES_PER_DAY" in (manual.remediation or "")
    assert "before it was sent" in manual.remediation
    assert manual.retry_after_seconds is None   # a rolling day names no interval
    assert _snap()["queries_last_24h"] == 1     # only the pre-spent query


def test_a_refused_poll_of_a_started_query_is_issued_but_not_an_attempt(session_factory):
    """The start reached the tenant, so it counts as issued. No answer came back,
    so it is not an attempt: the attempt cap claims 'verified N times without the
    detection appearing', and this verify learned nothing."""
    from connectors.service import verify_run_now
    from integrations.xsiam.codes import LOCAL_POLL_REFUSAL_REMEDIATION
    from integrations.xsiam.exceptions import XsiamQuotaError
    from models import Run
    from sqlalchemy import select
    from tests.connectors.test_auto_verify import _seed_run

    _seed_run(session_factory, run_id="run-poll")

    async def runner(query: str) -> int:
        raise XsiamQuotaError("poll refused", local=True, query_started="q1",
                              retry_after_seconds=7)

    async def go():
        async with session_factory() as db:
            run = (await db.execute(select(Run))).scalar_one()
            out = await verify_run_now(db, run, runner=runner, tenant="acme-prod")
            return out, _book(run)

    out, book = asyncio.run(go())
    assert out.queries_issued == 1 and book["queries_issued"] == 1
    assert int(book.get("attempts") or 0) == 0 and not book.get("last_attempt_at")
    assert out.reason == "refused_locally"
    assert out.remediation == LOCAL_POLL_REFUSAL_REMEDIATION
    assert out.retry_after_seconds == 7


def test_response_layer_names_each_read_when_local_and_tenant_failures_mix(monkeypatch):
    """Reverse order of the round-3 case: packs refused by our own bucket, a token
    refills, then the tenant answers the list-names read with a 403."""
    from integrations.xsiam.codes import LOCAL_REFUSAL_REMEDIATION

    monkeypatch.setenv("CORTEXSIM_XSIAM_MAX_REQUESTS_PER_MINUTE", "1")
    ticks = iter([0.0, 0.0, 0.0])          # bucket born, pre-spend, packs refused
    monkeypatch.setattr(ledger, "_clock", lambda: next(ticks, 120.0))
    ledger.acquire_request(tenant_ledger_key(BASE), per_minute=1)   # bucket empty
    seen: list = []

    def handler(request):
        seen.append(request.url.path)
        if request.url.path.endswith("lists/names"):
            return httpx.Response(403, json={})
        return httpx.Response(200, json=[])

    report = pf.PreflightReport(tenant="t", kind="xsiam_tenant")
    stage = asyncio.run(pf._probe_response_layer(_client(handler), report,
                                                 datetime(2026, 11, 11, 12)))
    assert len(seen) == 1 and seen[0].endswith("lists/names")
    assert report.queries_issued == 1
    assert "installed packs" in stage.detail and "before it was sent" in stage.detail
    assert "list names" in stage.detail and "HTTP 403" in stage.detail
    assert LOCAL_REFUSAL_REMEDIATION in stage.remediation
    assert "list-names read" in stage.remediation
    assert "installed-packs read was refused or failed" not in stage.remediation

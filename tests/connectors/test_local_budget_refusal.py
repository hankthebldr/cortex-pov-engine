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
    assert "not contacted" in by["dns_tls"].detail or "was not contacted" in by["dns_tls"].detail


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
    assert "not contacted" in auth.detail
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

"""Every tenant request spends the per-tenant request bucket; any 429 trips the breaker once."""
from __future__ import annotations

import httpx
import pytest

from integrations.xsiam.config import XsiamTenantConfig

BASE = "https://api-bucket.xdr.us.paloaltonetworks.com"
CFG = XsiamTenantConfig(base_url=BASE, region="us", auth_mode="standard", api_key_id="1")
SEARCH = "/public_api/v1/issue/search/"


def _snap():
    from connectors import tuning
    from integrations.xsiam.ledger import ledger, tenant_ledger_key

    return ledger.snapshot(tenant_ledger_key(BASE), limit=tuning.xsiam_max_queries_per_day())


@pytest.mark.asyncio
async def test_non_xql_catalog_read_charges_the_tenant_bucket(monkeypatch):
    from integrations.xsiam.client import XsiamClient
    from integrations.xsiam.exceptions import XsiamQuotaError

    monkeypatch.setenv("CORTEXSIM_XSIAM_MAX_REQUESTS_PER_MINUTE", "2")
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json={"reply": {"DATA": []}})

    client = XsiamClient(CFG, "key", transport=httpx.MockTransport(handler))
    for _ in range(2):
        await client.request("POST", SEARCH, json={"request_data": {}})
    with pytest.raises(XsiamQuotaError) as ei:
        await client.request("POST", SEARCH, json={"request_data": {}})
    assert ei.value.retry_after_seconds >= 1
    assert len(seen) == 2


@pytest.mark.asyncio
async def test_client_429_trips_the_breaker_once_for_every_path():
    from connectors.base import ConnectorConfig
    from connectors.xsiam import XsiamConnector
    from integrations.xsiam.client import XsiamClient
    from integrations.xsiam.exceptions import XsiamQuotaError

    client = XsiamClient(CFG, "key", transport=httpx.MockTransport(
        lambda r: httpx.Response(429, json={})))
    with pytest.raises(XsiamQuotaError) as ei:
        await client.request("POST", SEARCH, json={"request_data": {}})
    assert ei.value.tripped is True
    snap = _snap()
    assert snap["breaker_open"] is True and snap["trips"] == 1

    calls = []

    def fetcher(*a):
        calls.append(a)
        return 200, "{}"

    cfg = ConnectorConfig(integration_name="x", secret="k", config={
        "base_url": BASE, "api_key_id": "1"})
    from datetime import datetime
    res = XsiamConnector(fetcher=fetcher).pull(
        cfg, since=datetime(2026, 1, 1), until=datetime(2026, 1, 2))
    assert res.ok is False and res.code == "XSIAM_QUOTA_ERROR"
    assert calls == []


@pytest.mark.asyncio
async def test_xql_start_429_through_tenant_budget_trips_once():
    from integrations.xsiam.client import XsiamClient
    from integrations.xsiam.exceptions import XsiamQuotaError
    from integrations.xsiam.guards import tenant_budget

    client = XsiamClient(CFG, "key", transport=httpx.MockTransport(
        lambda r: httpx.Response(429, json={})))
    with pytest.raises(XsiamQuotaError):
        async with tenant_budget(client):
            await client.start_xql_query("dataset = x", None)
    assert _snap()["trips"] == 1


@pytest.mark.asyncio
async def test_open_breaker_refuses_before_the_wire():
    from integrations.xsiam.client import XsiamClient
    from integrations.xsiam.exceptions import XsiamQuotaError
    from integrations.xsiam.ledger import ledger, tenant_ledger_key

    ledger.trip(tenant_ledger_key(BASE), cooldown_seconds=60)
    seen = []
    client = XsiamClient(CFG, "key", transport=httpx.MockTransport(
        lambda r: seen.append(1) or httpx.Response(200, json={})))
    with pytest.raises(XsiamQuotaError):
        await client.healthcheck()
    assert seen == []

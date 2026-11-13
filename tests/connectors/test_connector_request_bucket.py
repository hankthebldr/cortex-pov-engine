"""The connector's paged harvest spends the same per-tenant bucket as the client."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import httpx
import pytest

from connectors.base import ConnectorConfig
from connectors.xsiam import XsiamConnector

SINCE = datetime(2026, 6, 10, 12, 0, 0)
UNTIL = datetime(2026, 6, 10, 13, 0, 0)
BASE = "https://api-tenant.xdr.us.paloaltonetworks.com"


def _cfg():
    return ConnectorConfig(integration_name="xsiam-prod", secret="k", config={
        "base_url": BASE, "api_key_id": "42"})


def _alert(i):
    return {"alert_id": str(i), "name": f"a{i}",
            "detection_timestamp": int((SINCE + timedelta(seconds=i)).timestamp() * 1000),
            "mitre_technique_ids": ["T1003"]}


def test_connector_page_charges_the_bucket(monkeypatch):
    monkeypatch.setenv("CORTEXSIM_XSIAM_MAX_REQUESTS_PER_MINUTE", "1")
    calls = []

    def fetcher(method, url, headers, body, timeout):
        rd = json.loads(body)["request_data"]
        calls.append(rd)
        page = [_alert(i) for i in range(150)][rd["search_from"]:rd["search_to"]]
        return 200, json.dumps({"reply": {"total_count": 150, "result_count": len(page),
                                          "alerts": page}})

    res = XsiamConnector(fetcher=fetcher).pull(_cfg(), since=SINCE, until=UNTIL)
    assert res.ok is False and res.code == "XSIAM_QUOTA_ERROR"
    assert len(calls) == 1
    assert res.detail["pages"] == 1
    assert res.detail["retry_after_seconds"] >= 1
    assert "path" in res.detail


@pytest.mark.asyncio
async def test_connector_429_trips_the_breaker():
    from integrations.xsiam.client import XsiamClient
    from integrations.xsiam.config import XsiamTenantConfig
    from integrations.xsiam.exceptions import XsiamQuotaError

    res = XsiamConnector(fetcher=lambda *a: (429, "slow down")).pull(
        _cfg(), since=SINCE, until=UNTIL)
    assert res.ok is False

    seen = []
    client = XsiamClient(
        XsiamTenantConfig(base_url=BASE, region="us", auth_mode="standard", api_key_id="1"),
        "key", transport=httpx.MockTransport(
            lambda r: seen.append(1) or httpx.Response(200, json={})))
    with pytest.raises(XsiamQuotaError):
        await client.request("POST", "/public_api/v1/issue/search/", json={})
    assert seen == []

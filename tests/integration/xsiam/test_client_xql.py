# tests/integration/xsiam/test_client_xql.py
from __future__ import annotations

import httpx
import pytest

from integrations.xsiam.config import XsiamTenantConfig

CFG = XsiamTenantConfig(
    base_url="https://api-test.xdr.us.paloaltonetworks.com",
    region="us", auth_mode="standard", api_key_id="1",
)


@pytest.mark.asyncio
async def test_start_xql_returns_query_id():
    from integrations.xsiam.client import XsiamClient

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/public_api/v1/xql/start_xql_query"
        return httpx.Response(200, json={"reply": "query-abc-123"})

    client = XsiamClient(CFG, "key", transport=httpx.MockTransport(handler))
    qid = await client.start_xql_query("dataset = x", {"relativeTime": 1000})
    assert qid == "query-abc-123"


@pytest.mark.asyncio
async def test_run_xql_polls_until_success():
    from integrations.xsiam.client import XsiamClient
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("start_xql_query"):
            return httpx.Response(200, json={"reply": "q1"})
        calls["n"] += 1
        if calls["n"] < 2:
            return httpx.Response(200, json={"reply": {"status": "PENDING"}})
        return httpx.Response(200, json={"reply": {
            "status": "SUCCESS",
            "remaining_quota": 0.97,
            "results": {"data": [{"source": "okta", "events": 12}]},
        }})

    client = XsiamClient(CFG, "key", transport=httpx.MockTransport(handler))
    reply = await client.run_xql("dataset=x", {"relativeTime": 1000},
                                 max_wait=5, interval=0)
    assert reply["status"] == "SUCCESS"
    assert reply["results"]["data"][0]["source"] == "okta"


@pytest.mark.asyncio
async def test_run_xql_times_out_while_pending():
    from integrations.xsiam.client import XsiamClient
    from integrations.xsiam.exceptions import XsiamQueryError

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("start_xql_query"):
            return httpx.Response(200, json={"reply": "q1"})
        return httpx.Response(200, json={"reply": {"status": "PENDING"}})

    client = XsiamClient(CFG, "key", transport=httpx.MockTransport(handler))
    with pytest.raises(XsiamQueryError):
        await client.run_xql("dataset=x", {"relativeTime": 1000},
                             max_wait=0, interval=0)

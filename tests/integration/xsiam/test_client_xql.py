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


@pytest.mark.asyncio
async def test_start_xql_http200_error_dict_raises_query_error():
    """XSIAM returns HTTP 200 with an error object for app-level failures.
    The client must raise XsiamQueryError rather than returning the dict as a query_id."""
    from integrations.xsiam.client import XsiamClient
    from integrations.xsiam.exceptions import XsiamQueryError

    client = XsiamClient(CFG, "key", transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"reply": {"err_code": "XQL_1001", "err_msg": "syntax error near 'x'"}})))
    with pytest.raises(XsiamQueryError, match="XQL_1001"):
        await client.start_xql_query("invalid = x", {"relativeTime": 1000})


@pytest.mark.asyncio
async def test_start_xql_http200_quota_error_raises_quota_error():
    """XQL_0003 / 'quota' in err_msg should surface as XsiamQuotaError."""
    from integrations.xsiam.client import XsiamClient
    from integrations.xsiam.exceptions import XsiamQuotaError

    client = XsiamClient(CFG, "key", transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"reply": {"err_code": "XQL_0003", "err_msg": "Daily quota exceeded"}})))
    with pytest.raises(XsiamQuotaError):
        await client.start_xql_query("dataset=x", {"relativeTime": 1000})

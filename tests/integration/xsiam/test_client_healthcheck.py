# tests/integration/xsiam/test_client_healthcheck.py
from __future__ import annotations

import httpx
import pytest

from integrations.xsiam.config import XsiamTenantConfig

CFG = XsiamTenantConfig(
    base_url="https://api-test.xdr.us.paloaltonetworks.com",
    region="us", auth_mode="standard", api_key_id="1",
)


def _transport(handler):
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_healthcheck_ok():
    from integrations.xsiam.client import XsiamClient

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/public_api/v1/healthcheck"
        assert request.headers["x-xdr-auth-id"] == "1"
        return httpx.Response(200, json={"status": "All systems normal"})

    client = XsiamClient(CFG, "key", transport=_transport(handler))
    out = await client.healthcheck()
    assert out["status"] == "All systems normal"


@pytest.mark.asyncio
async def test_healthcheck_401_raises_auth_error():
    from integrations.xsiam.client import XsiamClient
    from integrations.xsiam.exceptions import XsiamAuthError

    client = XsiamClient(CFG, "bad", transport=_transport(
        lambda r: httpx.Response(401, json={"err_msg": "nope"})))
    with pytest.raises(XsiamAuthError):
        await client.healthcheck()


@pytest.mark.asyncio
async def test_healthcheck_403_carries_upstream_status():
    from integrations.xsiam.client import XsiamClient
    from integrations.xsiam.exceptions import XsiamApiError

    client = XsiamClient(CFG, "key", transport=_transport(
        lambda r: httpx.Response(403, json={"err_msg": "license"})))
    with pytest.raises(XsiamApiError) as ei:
        await client.healthcheck()
    assert ei.value.upstream_status == 403


@pytest.mark.asyncio
async def test_advanced_auth_rejected_in_slice1():
    from integrations.xsiam.client import XsiamClient
    from integrations.xsiam.exceptions import XsiamConfigError
    cfg = XsiamTenantConfig(base_url=CFG.base_url, region="us",
                            auth_mode="advanced", api_key_id="1")
    with pytest.raises(XsiamConfigError):
        XsiamClient(cfg, "key")


@pytest.mark.asyncio
async def test_ping_via_endpoints_returns_count():
    from integrations.xsiam.client import XsiamClient

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/public_api/v1/endpoints/get_endpoints"
        return httpx.Response(200, json={"reply": {"result_count": 1}})

    client = XsiamClient(CFG, "key", transport=_transport(handler))
    assert await client.ping_via_endpoints() == 1

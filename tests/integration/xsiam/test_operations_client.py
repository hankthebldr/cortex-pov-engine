# tests/integration/xsiam/test_operations_client.py
"""XsiamClient.request() generic path + advanced (signed) auth."""
from __future__ import annotations

import hashlib

import httpx
import pytest

from integrations.xsiam.auth import advanced_auth_headers, standard_auth_headers
from integrations.xsiam.config import XsiamTenantConfig

STD = XsiamTenantConfig(
    base_url="https://api-acme.xsiam.us.paloaltonetworks.com",
    region="us", auth_mode="standard", api_key_id="7",
)
ADV = XsiamTenantConfig(
    base_url="https://api-acme.xsiam.us.paloaltonetworks.com",
    region="us", auth_mode="advanced", api_key_id="7",
)


# ── advanced auth ────────────────────────────────────────────────────────────

def test_advanced_auth_signature_is_correct(monkeypatch):
    monkeypatch.setenv("CORTEXSIM_XSIAM_TS_MS", "1700000000000")
    h = advanced_auth_headers("SECRETKEY", "7")
    assert set(h) >= {"x-xdr-auth-id", "x-xdr-nonce", "x-xdr-timestamp", "Authorization"}
    assert h["x-xdr-auth-id"] == "7"
    assert h["x-xdr-timestamp"] == "1700000000000"
    assert len(h["x-xdr-nonce"]) == 64
    expected = hashlib.sha256(
        ("SECRETKEY" + h["x-xdr-nonce"] + h["x-xdr-timestamp"]).encode()
    ).hexdigest()
    assert h["Authorization"] == expected
    # the raw key is never sent in advanced mode
    assert h["Authorization"] != "SECRETKEY"


def test_standard_vs_advanced_differ():
    std = standard_auth_headers("SECRETKEY", "7")
    assert std["Authorization"] == "SECRETKEY"           # key verbatim
    adv = advanced_auth_headers("SECRETKEY", "7")
    assert adv["Authorization"] != "SECRETKEY"            # signed


@pytest.mark.asyncio
async def test_client_builds_with_advanced_auth():
    from integrations.xsiam.client import XsiamClient

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.headers))
        return httpx.Response(200, json={"reply": {"ok": True}})

    client = XsiamClient(ADV, "key", transport=httpx.MockTransport(handler))
    out = await client.request("POST", "/public_api/v1/bioc/get", json={"request_data": {}})
    assert out == {"ok": True}
    assert "x-xdr-nonce" in seen            # advanced headers were signed per-request
    assert seen["authorization"] != "key"


# ── generic request() ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_request_unwraps_reply_and_passes_params():
    from integrations.xsiam.client import XsiamClient

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["query"] = dict(request.url.params)
        captured["method"] = request.method
        return httpx.Response(200, json={"reply": {"assets": [1, 2, 3]}})

    client = XsiamClient(STD, "key", transport=httpx.MockTransport(handler))
    out = await client.request("GET", "/public_api/v1/assets/abc/raw_fields",
                               params={"limit": "10"})
    assert out == {"assets": [1, 2, 3]}
    assert captured["method"] == "GET"
    assert captured["path"] == "/public_api/v1/assets/abc/raw_fields"
    assert captured["query"] == {"limit": "10"}


@pytest.mark.asyncio
async def test_request_raises_on_app_level_error_envelope():
    from integrations.xsiam.client import XsiamClient
    from integrations.xsiam.exceptions import XsiamQueryError

    # HTTP 200 with an application error object → must raise, not return it.
    client = XsiamClient(STD, "key", transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"reply": {"err_code": "E1", "err_msg": "bad filter"}})))
    with pytest.raises(XsiamQueryError):
        await client.request("POST", "/public_api/v1/incidents/get_incidents",
                             json={"request_data": {}})


@pytest.mark.asyncio
async def test_request_maps_401_to_auth_error():
    from integrations.xsiam.client import XsiamClient
    from integrations.xsiam.exceptions import XsiamAuthError

    client = XsiamClient(STD, "bad", transport=httpx.MockTransport(
        lambda r: httpx.Response(401, json={"err_msg": "nope"})))
    with pytest.raises(XsiamAuthError):
        await client.request("POST", "/public_api/v1/bioc/get", json={"request_data": {}})

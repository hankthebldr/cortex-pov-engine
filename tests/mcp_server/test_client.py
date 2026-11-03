import json

import httpx
import pytest

from mcp_server.client import (
    SimCoreClient,
    SimCoreError,
    SimCoreShapeUnknown,
    SimCoreUnreachable,
    encode_segment,
)


def _client(handler):
    return SimCoreClient("http://simcore.test", transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_2xx_json_dict_is_returned_and_date_header_kept():
    def handler(request):
        assert request.url.path == "/api/health"
        return httpx.Response(200, json={"status": "ok"}, headers={"Date": "Mon, 02 Nov 2026 18:04:11 GMT"})

    c = _client(handler)
    assert c.last_date is None
    assert await c.get("/api/health") == {"status": "ok"}
    assert c.last_date == "Mon, 02 Nov 2026 18:04:11 GMT"


@pytest.mark.asyncio
async def test_post_sends_json_and_params():
    seen = {}

    def handler(request):
        seen["body"] = request.content
        seen["q"] = dict(request.url.params)
        return httpx.Response(200, json={"ok": True})

    assert await _client(handler).post("/api/x", json={"a": 1}, params={"force": "true"}) == {"ok": True}
    assert json.loads(seen["body"]) == {"a": 1} and seen["q"] == {"force": "true"}


@pytest.mark.asyncio
async def test_rest_error_body_maps_to_simcore_error_with_code():
    def handler(request):
        return httpx.Response(409, json={"error": "no", "code": "BUNDLE_TARGET_UNSATISFIABLE", "detail": "steps 2"})

    with pytest.raises(SimCoreError) as ei:
        await _client(handler).get("/api/scenarios/x/download")
    e = ei.value
    assert (e.status, e.code, e.error, e.detail) == (409, "BUNDLE_TARGET_UNSATISFIABLE", "no", "steps 2")


@pytest.mark.asyncio
async def test_non_json_error_gets_http_status_code():
    def handler(request):
        return httpx.Response(502, text="bad gateway")

    with pytest.raises(SimCoreError) as ei:
        await _client(handler).get("/api/x")
    assert ei.value.code == "SIMCORE_HTTP_502" and ei.value.status == 502


@pytest.mark.asyncio
async def test_html_200_is_shape_unknown_not_empty():
    def handler(request):
        return httpx.Response(200, text="<html>console</html>", headers={"content-type": "text/html"})

    with pytest.raises(SimCoreShapeUnknown) as ei:
        await _client(handler).get("/api/health")
    assert ei.value.code == "SIMCORE_SHAPE_UNKNOWN"
    assert "text/html" in ei.value.detail and "<html>console</html>" in ei.value.detail


@pytest.mark.asyncio
async def test_json_list_200_is_shape_unknown():
    def handler(request):
        return httpx.Response(200, json=[1, 2])

    with pytest.raises(SimCoreShapeUnknown):
        await _client(handler).get("/api/x")


@pytest.mark.asyncio
async def test_connect_error_is_simcore_unreachable():
    def handler(request):
        raise httpx.ConnectError("connection refused")

    with pytest.raises(SimCoreUnreachable) as ei:
        await _client(handler).get("/api/health")
    assert ei.value.code == "SIMCORE_UNREACHABLE"
    assert "GET" in ei.value.detail and "connection refused" in ei.value.detail


def test_tenant_name_with_separators_is_one_encoded_segment():
    assert encode_segment("a/b") == "a%2Fb"
    for bad in ("", ".", ".."):
        with pytest.raises(ValueError):
            encode_segment(bad)

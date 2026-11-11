"""PanosClient over httpx.MockTransport: request shape, budget, error mapping, secrecy."""
from __future__ import annotations

from urllib.parse import parse_qs

import httpx
import pytest
from pydantic import SecretStr

from integrations.panos import codes
from integrations.panos.client import PanosClient
from integrations.panos.exceptions import (
    PanosApiError, PanosShapeError, PanosTransportError)

TZ = "/config/devices/entry[@name='localhost.localdomain']/deviceconfig/system/timezone"
OK = b'<response status="success"><result><system><hostname>fw</hostname></system></result></response>'
SYSINFO = "<show><system><info/></system></show>"


def recorder(body: bytes = OK, status: int = 200):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, content=body)

    return httpx.MockTransport(handler), seen


def form(request: httpx.Request) -> dict[str, list[str]]:
    return parse_qs(request.content.decode())


@pytest.mark.asyncio
async def test_op_request_shape():
    transport, seen = recorder()
    async with PanosClient("192.0.2.1", "KEY123", verify_tls=False, transport=transport) as c:
        result = await c.op(SYSINFO)
    assert result.tag == "result"
    (req,) = seen
    assert req.method == "POST"
    assert str(req.url) == "https://192.0.2.1/api/"
    assert req.headers["X-PAN-KEY"] == "KEY123"
    assert "key=" not in str(req.url) and "KEY123" not in str(req.url)
    f = form(req)
    assert f["type"] == ["op"] and f["cmd"] == ["<show><system><info /></system></show>"]
    assert "key" not in f


@pytest.mark.asyncio
async def test_config_show_request_shape():
    transport, seen = recorder()
    async with PanosClient("fw.example:8443", "K", verify_tls=True, transport=transport) as c:
        await c.config_show(TZ)
    (req,) = seen
    assert str(req.url) == "https://fw.example:8443/api/"
    f = form(req)
    assert f["type"] == ["config"] and f["action"] == ["show"]
    assert f["xpath"] == [TZ]


@pytest.mark.asyncio
async def test_calls_issued_and_budget_checked_before_sending():
    transport, seen = recorder()
    c = PanosClient("192.0.2.1", "K", verify_tls=False, transport=transport, max_calls=120)
    for _ in range(120):
        await c.op(SYSINFO)
    assert c.calls_issued == 120
    with pytest.raises(PanosTransportError) as ei:
        await c.op(SYSINFO)
    assert ei.value.code == codes.PN_CALL_BUDGET
    assert len(seen) == 120 and c.calls_issued == 120
    await c.aclose()


@pytest.mark.asyncio
async def test_keygen_uses_body_not_header_and_counts():
    transport, seen = recorder(b'<response status="success"><result><key>NEWKEY</key></result></response>')
    c = PanosClient("192.0.2.1", None, verify_tls=False, transport=transport)
    key = await c.keygen("admin", SecretStr("pw-secret-1"))
    assert key == "NEWKEY"
    (req,) = seen
    assert "X-PAN-KEY" not in req.headers
    f = form(req)
    assert f["type"] == ["keygen"] and f["user"] == ["admin"] and f["password"] == ["pw-secret-1"]
    assert "pw-secret-1" not in str(req.url)
    assert c.calls_issued == 1
    await c.aclose()


@pytest.mark.asyncio
async def test_keygen_rejection_is_fixed_message_and_leaks_nothing():
    body = (b'<response status="error" code="403"><result><msg>Invalid credentials for '
            b'admin pw-secret-1</msg></result></response>')
    transport, _ = recorder(body)
    c = PanosClient("192.0.2.1", None, verify_tls=False, transport=transport)
    with pytest.raises(PanosTransportError) as ei:
        await c.keygen("alice", SecretStr("pw-secret-1"))
    assert ei.value.code == codes.PN_KEYGEN_REJECTED
    for needle in ("alice", "pw-secret-1", "Invalid credentials"):
        assert needle not in str(ei.value)
    await c.aclose()


@pytest.mark.asyncio
async def test_connect_error_maps_and_never_echoes_secrets():
    def handler(request):
        raise httpx.ConnectError("boom X-PAN-KEY: secret host", request=request)

    c = PanosClient("192.0.2.1", "secret", verify_tls=False, transport=httpx.MockTransport(handler))
    with pytest.raises(PanosTransportError) as ei:
        await c.op(SYSINFO)
    assert ei.value.code == codes.PN_UNREACHABLE
    assert "secret" not in str(ei.value) and "X-PAN-KEY" not in str(ei.value)
    await c.aclose()


@pytest.mark.asyncio
async def test_timeout_maps_to_unreachable():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    c = PanosClient("192.0.2.1", "k", verify_tls=False, transport=httpx.MockTransport(handler))
    with pytest.raises(PanosTransportError) as ei:
        await c.op(SYSINFO)
    assert ei.value.code == codes.PN_UNREACHABLE
    await c.aclose()


@pytest.mark.asyncio
async def test_tls_verification_error_maps_to_tls_failed():
    import ssl

    def handler(request):
        err = httpx.ConnectError("certificate verify failed", request=request)
        err.__cause__ = ssl.SSLCertVerificationError("bad cert")
        raise err

    c = PanosClient("192.0.2.1", "k", verify_tls=True, transport=httpx.MockTransport(handler))
    with pytest.raises(PanosTransportError) as ei:
        await c.op(SYSINFO)
    assert ei.value.code == codes.PN_TLS_FAILED
    await c.aclose()


@pytest.mark.asyncio
async def test_http_403_maps_to_auth_rejected():
    transport, _ = recorder(b"forbidden", 403)
    c = PanosClient("192.0.2.1", "k", verify_tls=False, transport=transport)
    with pytest.raises(PanosTransportError) as ei:
        await c.op(SYSINFO)
    assert ei.value.code == codes.PN_AUTH_REJECTED
    await c.aclose()


@pytest.mark.asyncio
async def test_api_error_403_maps_to_auth_rejected_transport_error():
    transport, _ = recorder(b'<response status="error" code="403"><result><msg>Forbidden</msg></result></response>')
    c = PanosClient("192.0.2.1", "k", verify_tls=False, transport=transport)
    with pytest.raises(PanosTransportError) as ei:
        await c.op(SYSINFO)
    assert ei.value.code == codes.PN_AUTH_REJECTED
    await c.aclose()


@pytest.mark.asyncio
async def test_other_api_error_propagates_as_api_error():
    transport, _ = recorder(b'<response status="error" code="13"><msg><line>Object not valid</line></msg></response>')
    c = PanosClient("192.0.2.1", "k", verify_tls=False, transport=transport)
    with pytest.raises(PanosApiError) as ei:
        await c.op(SYSINFO)
    assert ei.value.panos_code == "13"
    await c.aclose()


@pytest.mark.asyncio
async def test_other_non_2xx_maps_to_upstream_error_with_status():
    transport, _ = recorder(b"<html>secret-body</html>", 502)
    c = PanosClient("192.0.2.1", "k", verify_tls=False, transport=transport)
    with pytest.raises(PanosTransportError) as ei:
        await c.op(SYSINFO)
    assert ei.value.code == codes.PN_UPSTREAM_ERROR
    assert "502" in str(ei.value) and "secret-body" not in str(ei.value)
    await c.aclose()


@pytest.mark.asyncio
async def test_html_200_is_shape_error():
    transport, _ = recorder(b"<html><body>Login</body></html>")
    c = PanosClient("192.0.2.1", "k", verify_tls=False, transport=transport)
    with pytest.raises(PanosShapeError):
        await c.op(SYSINFO)
    await c.aclose()

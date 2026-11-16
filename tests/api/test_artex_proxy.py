"""/artex/* streaming reverse proxy: transparency, SSE non-buffering, error shapes."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import config
from api import artex

ORIGIN = "http://artex:8787"


class _Body(httpx.AsyncByteStream):
    """Unread response stream, like a real transport produces (a Response built from
    content= is pre-read and cannot be re-streamed, which a real upstream never is)."""

    def __init__(self, data: bytes):
        self._data = data

    async def __aiter__(self):
        yield self._data


def _resp(status: int, data: bytes, headers=None) -> httpx.Response:
    return httpx.Response(status, headers=headers, stream=_Body(data))


@pytest.fixture
def origin(monkeypatch):
    monkeypatch.setattr(config.settings, "CORTEXSIM_ARTEX_ORIGIN", ORIGIN)


def _install(monkeypatch, handler):
    monkeypatch.setattr(artex, "_transport", httpx.MockTransport(handler))


def _client():
    app = FastAPI()
    app.include_router(artex.proxy_router)
    return TestClient(app)


def test_proxy_forwards_get_with_prefix_intact(origin, monkeypatch):
    seen = []

    def handler(request):
        seen.append(request)
        return _resp(200, b'{"ok": true}', {"x-up": "1", "connection": "close",
                                              "content-type": "application/json"})

    _install(monkeypatch, handler)
    r = _client().get("/artex/api/health")
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert r.headers["x-up"] == "1"
    assert "connection" not in r.headers
    assert seen[0].url.path == "/artex/api/health"
    assert str(seen[0].url).startswith(ORIGIN)
    assert seen[0].headers["host"] == "artex:8787"  # inbound testserver host is not forwarded


def test_proxy_forwards_post_body_and_query(origin, monkeypatch):
    seen = []

    def handler(request):
        seen.append(request)
        return _resp(201, b"made")

    _install(monkeypatch, handler)
    payload = {"a": [1, 2], "b": "x"}
    r = _client().post("/artex/api/x?token=abc&y=1", json=payload)
    assert r.status_code == 201 and r.content == b"made"
    up = seen[0]
    assert up.method == "POST"
    assert up.url.path == "/artex/api/x"
    assert up.url.params["token"] == "abc" and up.url.params["y"] == "1"
    assert json.loads(up.content) == payload


def test_proxy_streams_event_stream_unbuffered(origin, monkeypatch):
    """The handler must return while the upstream is still open, and frames must be
    delivered before the upstream has produced the rest. A buffering proxy would
    deadlock on `gate` (it only opens after we have already received frame 1)."""
    gate = asyncio.Event()

    async def frames():
        yield b"data: 1\n\n"
        await gate.wait()
        yield b"data: 2\n\n"
        yield b"data: 3\n\n"

    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=frames())

    _install(monkeypatch, handler)

    class _Req:  # minimal stand-in for starlette.Request
        method = "GET"
        headers = httpx.Headers({"accept": "text/event-stream"})
        url = httpx.URL("http://console/artex/api/update/stream")

        async def body(self):
            return b""

    # Request needs .headers.raw and .url.query
    async def run():
        resp = await asyncio.wait_for(artex.artex_proxy("api/update/stream", _Req()), 2)
        assert isinstance(resp, artex.StreamingResponse)
        assert resp.status_code == 200
        assert dict(resp.headers)["content-type"] == "text/event-stream"
        assert "content-length" not in resp.headers
        it = resp.body_iterator.__aiter__()
        first = await asyncio.wait_for(it.__anext__(), 2)
        assert first == b"data: 1\n\n"
        assert not gate.is_set()  # upstream has NOT finished; frame 1 arrived anyway
        gate.set()
        rest = [c async for c in it]
        assert b"".join(rest) == b"data: 2\n\ndata: 3\n\n"

    asyncio.run(run())


def test_proxy_event_stream_through_asgi_end_to_end(origin, monkeypatch):
    async def frames():
        for i in range(3):
            yield f"data: {i}\n\n".encode()

    _install(monkeypatch, lambda req: httpx.Response(
        200, headers={"content-type": "text/event-stream"}, content=frames()))
    with _client().stream("GET", "/artex/api/side-questions/1/events") as r:
        assert r.headers["content-type"] == "text/event-stream"
        assert "".join(r.iter_text()) == "data: 0\n\ndata: 1\n\ndata: 2\n\n"


def test_proxy_502_when_upstream_unreachable(origin, monkeypatch):
    def handler(request):
        raise httpx.ConnectError("boom", request=request)

    _install(monkeypatch, handler)
    r = _client().get("/artex/api/health")
    assert r.status_code == 502
    body = r.json()
    assert body["code"] == "ARTEX_PROXY_UPSTREAM_ERROR"
    assert set(body) == {"error", "code", "detail"}


def test_proxy_404_when_origin_empty_makes_no_outbound(monkeypatch):
    monkeypatch.setattr(config.settings, "CORTEXSIM_ARTEX_ORIGIN", "")
    calls = []

    def handler(request):
        calls.append(request)
        raise AssertionError("no outbound call allowed when ARTEX is not enabled")

    _install(monkeypatch, handler)
    r = _client().get("/artex/anything")
    assert r.status_code == 404
    assert r.json()["code"] == "ARTEX_NOT_ENABLED"
    assert calls == []

"""GET /api/artex/status and probe_artex: reachability + first-run setup state."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest

from api.artex import probe_artex

ORIGIN = "http://artex:8787"


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _run(settings, client):
    return asyncio.run(probe_artex(settings, client=client))


def test_status_not_enabled_makes_no_outbound():
    calls = []

    def handler(request):
        calls.append(request)
        raise AssertionError("no outbound call is allowed when ARTEX is not enabled")

    out = _run(SimpleNamespace(CORTEXSIM_ARTEX_ORIGIN=""), _client(handler))
    assert out["reachability"] == "not_enabled"
    assert out["code"] == "ARTEX_NOT_ENABLED"
    assert out["initialized"] is None
    assert out["origin_configured"] is False
    assert calls == []


def test_status_not_enabled_without_injected_client_builds_none(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("httpx.AsyncClient must not be constructed when disabled")

    monkeypatch.setattr(httpx, "AsyncClient", boom)
    out = asyncio.run(probe_artex(SimpleNamespace(CORTEXSIM_ARTEX_ORIGIN="")))
    assert out["code"] == "ARTEX_NOT_ENABLED"


@pytest.mark.parametrize("flag,code", [(True, "ARTEX_READY"), (False, "ARTEX_NEEDS_SETUP")])
def test_status_reachable(flag, code):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, json={"initialized": flag})

    out = _run(SimpleNamespace(CORTEXSIM_ARTEX_ORIGIN=ORIGIN + "/"), _client(handler))
    assert seen == [f"{ORIGIN}/artex/api/auth/status"]
    assert out["reachability"] == "reachable"
    assert out["initialized"] is flag
    assert out["code"] == code
    assert out["origin_configured"] is True


def test_status_unreachable_on_connect_error():
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    out = _run(SimpleNamespace(CORTEXSIM_ARTEX_ORIGIN=ORIGIN), _client(handler))
    assert out["reachability"] == "unreachable"
    assert out["code"] == "ARTEX_UNREACHABLE"
    assert out["initialized"] is None
    assert out["origin_configured"] is True


def test_status_unreachable_on_http_error_and_bad_body():
    out = _run(SimpleNamespace(CORTEXSIM_ARTEX_ORIGIN=ORIGIN),
               _client(lambda r: httpx.Response(502)))
    assert out["code"] == "ARTEX_UNREACHABLE"
    out = _run(SimpleNamespace(CORTEXSIM_ARTEX_ORIGIN=ORIGIN),
               _client(lambda r: httpx.Response(200, json={"nope": 1})))
    assert out["code"] == "ARTEX_UNREACHABLE"
    assert out["initialized"] is None


def test_route_reports_not_enabled(make_client, monkeypatch):
    import config
    from api.artex import router

    monkeypatch.setattr(config.settings, "CORTEXSIM_ARTEX_ORIGIN", "")
    r = make_client(router).get("/api/artex/status")
    assert r.status_code == 200
    assert r.json()["code"] == "ARTEX_NOT_ENABLED"
    assert r.json()["reachability"] == "not_enabled"

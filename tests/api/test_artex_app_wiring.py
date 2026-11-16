"""The real ``main.app`` mounts the ARTEX router and proxy, in the right order.

Every other ARTEX test builds an isolated FastAPI(). The ordering is load-bearing:
the ``/artex/{path:path}`` proxy must be registered BEFORE the static ``"/"`` mount,
or StaticFiles swallows ``/artex/*`` and the SPA answers instead of the proxy.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import config
from main import app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(config.settings, "CORTEXSIM_ARTEX_ORIGIN", "")
    # The Host guard refuses TestClient's default "testserver"; localhost passes.
    with TestClient(app, base_url="http://localhost") as c:
        yield c


def test_status_route_is_mounted_and_artex_disabled_by_default(client):
    r = client.get("/api/artex/status")
    assert r.status_code == 200
    assert r.json()["reachability"] == "not_enabled"


def test_artex_prefix_is_handled_by_the_proxy_not_the_static_catch_all(client):
    r = client.get("/artex/")
    assert r.status_code == 404
    # The proxy's structured error, not StaticFiles' plain {"detail": "Not Found"}.
    assert r.json()["code"] == "ARTEX_NOT_ENABLED"


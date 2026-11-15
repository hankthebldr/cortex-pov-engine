"""GET /api/agents/install bakes only this SimCore's own URL as SERVER.

The installer downloads the beacon from SERVER, checks it against a digest it
also fetches from SERVER, and the beacon then takes SERVER's tasks. Whoever
chooses SERVER chooses the server the target enrols with. ``?server=`` is
therefore accepted only when it names this SimCore: the origin the install
request itself arrived on, or an entry in ``CORTEXSIM_PUBLIC_URLS`` (LAN / NAT
names the request cannot show). Anything else is a 400 and no script.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.agents import router as agents_router


@pytest.fixture()
def client_at(memory_db):
    """A TestClient whose base_url is the origin SimCore is reached on."""
    get_db_override, _ = memory_db

    def _build(base_url: str) -> TestClient:
        from database import get_db

        app = FastAPI()
        app.include_router(agents_router, prefix="/api")
        app.dependency_overrides[get_db] = get_db_override
        return TestClient(app, base_url=base_url)

    return _build


@pytest.fixture()
def public_urls(monkeypatch):
    import config

    def _set(value: str) -> None:
        monkeypatch.setattr(config.settings, "CORTEXSIM_PUBLIC_URLS", value)

    return _set


#: Mint-shaped (``cxs_`` + token_urlsafe(32)): the installer refuses a token of
#: any other length with BAD_TOKEN before it looks at ?server=.
_TOKEN = "cxs_" + "abc" * 14 + "d"


@pytest.mark.parametrize("os_", ["linux", "darwin", "windows"])
def test_a_link_naming_another_server_gets_no_script(client_at, os_):
    r = client_at("http://simcore.lab:8888").get("/api/agents/install", params={
        "os": os_, "server": "http://c2.attacker.example:443", "token": _TOKEN,
    })
    assert r.status_code == 400, r.text
    assert r.headers["content-type"].startswith("application/json")
    detail = r.json()["detail"]
    assert detail["code"] == "SERVER_NOT_THIS_SIMCORE"
    assert {"error", "code", "detail"} <= set(detail)
    assert "CORTEXSIM_PUBLIC_URLS" in detail["remedy"]


@pytest.mark.parametrize("server", [
    "http://simcore.lab:8888",
    "HTTP://SimCore.LAB:8888",
    "http://simcore.lab:8888/",
])
def test_the_origin_the_request_arrived_on_is_accepted(client_at, server):
    r = client_at("http://simcore.lab:8888").get(
        "/api/agents/install", params={"os": "linux", "server": server})
    assert r.status_code == 200, r.text
    assert "SERVER='http://simcore.lab:8888'" in r.text


def test_a_default_port_spelled_out_is_the_same_origin(client_at):
    r = client_at("https://simcore.lab").get(
        "/api/agents/install", params={"os": "linux", "server": "https://simcore.lab:443"})
    assert r.status_code == 200, r.text
    assert "SERVER='https://simcore.lab'" in r.text


def test_a_configured_public_url_is_accepted_and_others_are_not(client_at, public_urls, monkeypatch):
    """The console is open at localhost; the jumpbox reaches SimCore on a LAN
    address the request cannot show. The operator lists it; nothing else passes."""
    public_urls(" http://10.0.10.5:8888/ , HTTPS://SimCore.Corp.Local:443 ")
    # A listed DNS name must also pass the Host guard, or the beacon's every
    # call gets HOST_NOT_ALLOWED (SERVER_HOST_NOT_ALLOWED, RG-GUARD-05).
    import config
    monkeypatch.setattr(config.settings, "CORTEXSIM_ALLOWED_HOSTS", "simcore.corp.local")
    client = client_at("http://localhost:8888")

    for server in ("http://10.0.10.5:8888", "https://simcore.corp.local"):
        r = client.get("/api/agents/install", params={"os": "linux", "server": server})
        assert r.status_code == 200, r.text
        assert f"SERVER='{server}'" in r.text

    r = client.get("/api/agents/install", params={"os": "windows", "server": "https://simcore.corp.local"})
    assert r.status_code == 200, r.text
    assert "'https://simcore.corp.local'" in r.text

    r = client.get("/api/agents/install", params={"os": "linux", "server": "http://10.0.10.6:8888"})
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "SERVER_NOT_THIS_SIMCORE"


def test_the_console_one_liner_is_accepted(client_at):
    """ui/src/api/client.js::agentInstallUrl sends no ?server=; the console puts
    its own origin in front, so SERVER is derived from the request."""
    r = client_at("http://10.0.10.5:8888").get("/api/agents/install?os=linux&interval=10")
    assert r.status_code == 200, r.text
    assert "SERVER='http://10.0.10.5:8888'" in r.text

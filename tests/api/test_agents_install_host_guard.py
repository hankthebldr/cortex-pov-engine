"""The installer does not bake a server name SimCore's own Host guard refuses.

RG-GUARD-05, install half. GET /api/agents/install?server=<name> rendered an
installer for any well-formed URL. When <name> is a DNS name that is not
localhost, an IP literal, this host's own name or in CORTEXSIM_ALLOWED_HOSTS,
the outermost Host guard answers every request the beacon makes with
400 HOST_NOT_ALLOWED: the installer "succeeds" and the agent never appears.

Now the route refuses up front with 400 SERVER_HOST_NOT_ALLOWED and names the
setting. A server taken from the request's own Host header was admitted by
the guard to get here, so only ?server= can trip it.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def client(make_client):
    from api.agents import router
    return make_client(router)


@pytest.fixture
def allowed(monkeypatch):
    from config import settings

    def _set(value: str):
        monkeypatch.setattr(settings, "CORTEXSIM_ALLOWED_HOSTS", value, raising=False)
    _set("")
    return _set


@pytest.mark.parametrize("os_", ["linux", "windows"])
def test_a_name_the_guard_refuses_is_not_baked(client, allowed, os_):
    r = client.get("/api/agents/install", params={"os": os_, "server": "http://jumpbox-zz.lab.example:8888"})
    assert r.status_code == 400, r.text[:300]
    body = r.json()
    detail = body.get("detail", body)
    assert detail["code"] == "SERVER_HOST_NOT_ALLOWED", body
    assert "CORTEXSIM_ALLOWED_HOSTS" in detail["detail"]
    assert "jumpbox-zz.lab.example" in detail["detail"]


def test_an_allowlisted_name_is_baked(client, allowed):
    allowed("jumpbox-zz.lab.example")
    r = client.get("/api/agents/install", params={"server": "http://jumpbox-zz.lab.example:8888"})
    assert r.status_code == 200, r.text[:300]
    assert "jumpbox-zz.lab.example:8888" in r.text


def test_an_ip_literal_is_baked(client, allowed):
    r = client.get("/api/agents/install", params={"server": "http://10.0.0.5:8888"})
    assert r.status_code == 200, r.text[:300]

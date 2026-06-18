"""GAP-API-012 — the credentials router must return the structured
{error, code, detail} envelope on 404, like every other router (not a bare
string detail).
"""
from __future__ import annotations

import pytest


@pytest.fixture
def client(make_client):
    from api.credentials import router
    return make_client(router)


def test_get_unknown_secret_returns_structured_envelope(client):
    r = client.get("/api/credentials/secrets/does-not-exist")
    assert r.status_code == 404
    detail = r.json()["detail"]
    assert isinstance(detail, dict), f"expected structured detail, got {detail!r}"
    assert detail["code"] == "SECRET_NOT_FOUND"
    assert "does-not-exist" in detail["detail"]
    assert detail["error"]


def test_delete_unknown_secret_returns_structured_envelope(client):
    r = client.delete("/api/credentials/secrets/nope")
    assert r.status_code == 404
    detail = r.json()["detail"]
    assert isinstance(detail, dict)
    assert detail["code"] == "SECRET_NOT_FOUND"


def test_get_unknown_integration_returns_structured_envelope(client):
    r = client.get("/api/credentials/integrations/ghost")
    assert r.status_code == 404
    detail = r.json()["detail"]
    assert isinstance(detail, dict)
    assert detail["code"] == "INTEGRATION_NOT_FOUND"
    assert "ghost" in detail["detail"]


def test_verify_unknown_integration_returns_structured_envelope(client):
    r = client.post("/api/credentials/integrations/ghost/verify", json={"ok": True})
    assert r.status_code == 404
    detail = r.json()["detail"]
    assert isinstance(detail, dict)
    assert detail["code"] == "INTEGRATION_NOT_FOUND"

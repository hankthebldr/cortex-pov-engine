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


def test_reserved_secret_name_is_a_400_not_a_500(client):
    """The ``__cortexsim__/`` namespace holds the master-key check row. The
    store refuses it with a ValueError; the router must say so as a caller
    error with a stable code, never as a 500 INTERNAL_ERROR.

    Only PUT is reachable over HTTP: the name travels in the body. DELETE takes
    it as a path segment, and the ``/`` in the prefix never routes (404)."""
    r = client.put("/api/credentials/secrets",
                   json={"name": "__cortexsim__/keycheck", "plaintext": "x"})
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "RESERVED_SECRET_NAME"

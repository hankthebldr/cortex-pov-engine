# tests/integration/xsiam/test_auth_standard.py
from __future__ import annotations


def test_standard_auth_headers():
    from integrations.xsiam.auth import standard_auth_headers
    h = standard_auth_headers("the-api-key", "42")
    assert h["x-xdr-auth-id"] == "42"
    assert h["Authorization"] == "the-api-key"
    assert h["Content-Type"] == "application/json"


def test_standard_auth_coerces_key_id_to_str():
    from integrations.xsiam.auth import standard_auth_headers
    h = standard_auth_headers("k", 7)  # api_key_id sometimes arrives as int
    assert h["x-xdr-auth-id"] == "7"

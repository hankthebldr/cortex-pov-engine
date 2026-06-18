# core/integrations/xsiam/auth.py
"""Auth header builders for the Cortex XSIAM/XDR public API.

Slice 1 implements Standard (static-header) auth. Advanced (SHA-256 signed
nonce+timestamp) auth is a later slice and slots in here as a sibling builder
without touching the client.
"""
from __future__ import annotations


def standard_auth_headers(api_key: str, api_key_id) -> dict[str, str]:
    return {
        "x-xdr-auth-id": str(api_key_id),
        "Authorization": api_key,
        "Content-Type": "application/json",
    }

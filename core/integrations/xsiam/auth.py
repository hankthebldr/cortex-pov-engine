# core/integrations/xsiam/auth.py
"""Auth header builders for the Cortex XSIAM/XDR public API.

Two modes, selected per tenant by ``XsiamTenantConfig.auth_mode``:

* **Standard** — the API key is sent verbatim in ``Authorization``.
* **Advanced** — a per-request SHA-256 signature over ``key + nonce + timestamp``;
  the raw key never leaves the process. Ported from the read-back connector
  (``core/connectors/xsiam.py``) so both stacks sign identically.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import string
from datetime import datetime


def standard_auth_headers(api_key: str, api_key_id) -> dict[str, str]:
    return {
        "x-xdr-auth-id": str(api_key_id),
        "Authorization": api_key,
        "Content-Type": "application/json",
    }


def _now_ms() -> int:
    """Epoch milliseconds (UTC).

    Honors ``CORTEXSIM_XSIAM_TS_MS`` for deterministic tests (unused in prod),
    matching the connector's signer so signatures are reproducible.
    """
    override = os.environ.get("CORTEXSIM_XSIAM_TS_MS")
    if override:
        return int(override)
    epoch = datetime(1970, 1, 1)
    return int((datetime.utcnow() - epoch).total_seconds() * 1000)


def advanced_auth_headers(api_key: str, api_key_id) -> dict[str, str]:
    """Advanced (signed) auth: Authorization = sha256(api_key + nonce + timestamp).

    A fresh 64-char alphanumeric nonce and a millisecond timestamp are generated
    per call, so this must be invoked once per request (the client builds headers
    at request time, not at construction).
    """
    nonce = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(64))
    timestamp = str(_now_ms())
    digest = hashlib.sha256((api_key + nonce + timestamp).encode("utf-8")).hexdigest()
    return {
        "x-xdr-auth-id": str(api_key_id),
        "x-xdr-nonce": nonce,
        "x-xdr-timestamp": timestamp,
        "Authorization": digest,
        "Content-Type": "application/json",
    }

"""Collector ingestion auth: one definition for every emitter that POSTs to a collector.

The three fields are declared as ``Annotated`` aliases rather than a mixin base
class. A base class puts its fields first in the params model, and the console's
campaign builder renders ``params_schema`` in field order, so a mixin would move
the auth fields to the top of every analytics emitter's form. Each params model
declares the three names where they belong in its own form, and the default,
bounds and description live here once.

The XSIAM HTTP log collector (``https://api-<tenant>/logs/v1/event``) reads the
bare API key, ``Authorization: <api_key>``, and refuses ``Bearer <key>`` with a
401. The scheme therefore defaults to empty.
"""

from __future__ import annotations

from typing import Annotated, Any, Optional

from pydantic import Field, SecretStr

AuthToken = Annotated[Optional[SecretStr], Field(
    default=None,
    description="Optional ingestion token (the XSIAM HTTP log collector's API "
                "key, a HEC-style raw token, or the credential portion of a "
                "Bearer/Splunk header). Held as a SecretStr so it is never "
                "logged and never emitted into an event body. Comes from the "
                "operator/vault, never hardcoded.",
)]

AuthHeader = Annotated[str, Field(
    default="Authorization",
    description="Header name the auth token is presented under. The XSIAM "
                "HTTP log collector reads 'Authorization'; a forwarder may "
                "want a custom X-... header.",
)]

AuthScheme = Annotated[str, Field(
    default="",
    description="Scheme prefix prepended to the token. Empty by default, "
                "because the XSIAM HTTP log collector expects the bare API "
                "key ('Authorization: <api_key>') and refuses 'Bearer <key>' "
                "with a 401. Set 'Bearer' or 'Splunk' only for a forwarder "
                "that asks for one.",
)]


def apply_collector_auth(headers: dict[str, str], params: Any) -> dict[str, str]:
    """Add the auth header to a CLIENT-level header dict, in place, and return it.

    Call this only while building the ``httpx.AsyncClient``. Per-request header
    dicts are what end up in emitted audit events, so the secret must never be
    threaded through one.
    """
    token: Optional[SecretStr] = getattr(params, "auth_token", None)
    if token is not None:
        # Empty scheme -> raw token; .strip() drops the leading space so
        # 'Bearer <t>' and a bare '<t>' both come out clean.
        headers[params.auth_header] = f"{params.auth_scheme} {token.get_secret_value()}".strip()
    return headers

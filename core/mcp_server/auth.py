"""Bearer-token gate for the /mcp streamable-HTTP mount (tenant-reader spec §7.1).

``CORTEXSIM_MCP_TOKEN`` only lets a client into ``/mcp``. It is never a tenant
credential and it does not authenticate the rest of SimCore's ``/api``.
"""
from __future__ import annotations

import hmac
from typing import Any, Optional

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

_CODE = "MCP_UNAUTHORIZED"


def bearer_ok(header: Optional[str], token: str) -> bool:
    """True when ``header`` is ``Bearer <token>`` (scheme case-insensitive).

    Compared as bytes with ``hmac.compare_digest``. An empty configured token
    never matches anything, so a misconfigured gate fails closed.
    """
    if not header or not token:
        return False
    scheme, _, presented = header.strip().partition(" ")
    if scheme.lower() != "bearer":
        return False
    return hmac.compare_digest(presented.strip().encode("utf-8"), token.encode("utf-8"))


class McpGate:
    """ASGI wrapper that answers 401 before the wrapped MCP endpoint sees a request.

    ``loopback_only`` is reserved for the peer check; it is accepted here so the
    mount's call site does not change when that check is turned on.
    """

    def __init__(self, app: ASGIApp, *, token: str, loopback_only: bool) -> None:
        self.app = app
        self._token = token
        self.loopback_only = loopback_only

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        header = _authorization(scope)
        if not bearer_ok(header, self._token):
            # Neither the header value nor the token is logged or echoed.
            resp = JSONResponse(
                {"error": "Unauthorized", "code": _CODE,
                 "detail": "/mcp requires 'Authorization: Bearer <CORTEXSIM_MCP_TOKEN>'."},
                status_code=401, headers={"WWW-Authenticate": "Bearer"})
            await resp(scope, receive, send)
            return
        await self.app(scope, receive, send)


def _authorization(scope: Any) -> Optional[str]:
    for name, value in scope.get("headers", ()):
        if name == b"authorization":
            return value.decode("latin-1")
    return None

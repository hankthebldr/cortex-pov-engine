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


def bearer_ok(header: Optional[bytes], token: bytes) -> bool:
    """True when the raw ``Authorization`` header is ``Bearer <token>`` (scheme
    case-insensitive).

    Both sides are bytes: the header exactly as it arrived, the token encoded
    once by :class:`McpGate`, compared with ``hmac.compare_digest``. An empty
    configured token never matches anything, so a misconfigured gate fails closed.
    """
    if not header or not token:
        return False
    scheme, _, presented = header.strip().partition(b" ")
    if scheme.lower() != b"bearer":
        return False
    return hmac.compare_digest(presented.strip(), token)


class McpGate:
    """ASGI wrapper that answers 401 before the wrapped MCP endpoint sees a request.

    ``loopback_only`` is reserved for a peer check and stays False: under Docker
    a host-loopback request does not arrive from a loopback address, so /mcp is
    bearer-only (spec section 7.1, note of 2026-11-13).

    The token is encoded to UTF-8 once, here, so an unencodable token fails at
    mount time instead of on every request. Anything that is not an HTTP request
    is refused: the gate fails closed on its own.
    """

    def __init__(self, app: ASGIApp, *, token: str, loopback_only: bool) -> None:
        self.app = app
        try:
            self._token = token.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError("CORTEXSIM_MCP_TOKEN is not valid UTF-8 text") from exc
        self.loopback_only = loopback_only

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
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


def _authorization(scope: Any) -> Optional[bytes]:
    for name, value in scope.get("headers", ()):
        if name == b"authorization":
            return value
    return None

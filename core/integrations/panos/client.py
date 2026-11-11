# core/integrations/panos/client.py
"""Allowlisted, read-only async PAN-OS XML API client.

This module is the security boundary of the NGFW connection: no code path here can
write to a firewall. The only request types ever built are ``op`` (command allowlist
below), ``config`` with ``action=show`` (read), and ``keygen`` (credential exchange,
no config effect). ``op`` refuses anything not on ``OP_ALLOWLIST`` BEFORE a request
is built, so a refusal sends nothing and consumes no budget.

Secrets: the API key travels in the ``X-PAN-KEY`` header (never the URL). Request
bodies, headers, responses and credentials are never logged and never copied into an
exception message; transport errors carry fixed text only.
"""
from __future__ import annotations

import ssl
import xml.etree.ElementTree as ET
from typing import Optional

import httpx
from pydantic import SecretStr

from . import codes
from .exceptions import (
    PanosApiError, PanosShapeError, PanosTransportError, PanosWriteRefused)
from .xmlparse import parse_keygen, parse_response, safe_fromstring

# Element paths (root -> command) of the read-only operational commands we may run.
OP_ALLOWLIST: frozenset[tuple[str, ...]] = frozenset({
    ("show", "system", "info"),
    ("show", "logging-status"),
    ("show", "clock"),
    ("show", "session", "all"),
    ("show", "user", "ip-user-mapping"),
    ("show", "object", "registered-ip"),
    ("test", "security-policy-match"),
    ("test", "nat-policy-match"),
    ("test", "routing", "fib-lookup"),
    ("test", "advanced-routing", "fib-lookup"),
})

# Defence in depth: these tags may not appear ANYWHERE in an allowed command, so a
# write verb cannot ride along as an argument or a nested child.
_FORBIDDEN_TAGS = frozenset({
    "request", "set", "clear", "delete", "debug", "commit", "load", "save", "edit",
    "move", "rename", "import", "export", "revert", "override", "multi-config",
    "schedule", "validate", "install", "upgrade", "restart", "shutdown",
})

_REFUSED = "operational command is not on the read-only allowlist; refused"


def _check_op_allowed(cmd_xml: str) -> None:
    try:
        root = safe_fromstring(cmd_xml)
    except PanosShapeError:
        raise PanosWriteRefused(_REFUSED) from None
    if any(el.tag in _FORBIDDEN_TAGS for el in root.iter()):
        raise PanosWriteRefused(_REFUSED)
    # Walk the single-child chain; the command path is the prefix before the first
    # element that branches (siblings at any level) or carries text (an argument).
    path: list[str] = []
    node = root
    while True:
        path.append(node.tag)
        if (node.text or "").strip() or len(node) != 1:
            break
        node = node[0]
    for n in range(1, len(path) + 1):
        if tuple(path[:n]) in OP_ALLOWLIST:
            return
    raise PanosWriteRefused(_REFUSED)


def _is_tls_failure(exc: BaseException) -> bool:
    seen = set()
    cur: Optional[BaseException] = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, ssl.SSLError):
            return True
        cur = cur.__cause__ or cur.__context__
    return False


class PanosClient:
    def __init__(self, host: str, api_key: Optional[str], *, verify_tls: bool,
                 transport: Optional[httpx.AsyncBaseTransport] = None,
                 max_calls: int = 120, timeout: float = 10.0):
        self._url = f"https://{host}/api/"
        self._api_key = api_key
        self._max_calls = max_calls
        self.calls_issued = 0
        self._http = httpx.AsyncClient(verify=verify_tls, transport=transport,
                                       timeout=timeout, follow_redirects=False)

    async def __aenter__(self) -> "PanosClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------ public reads

    async def op(self, cmd_xml: str) -> ET.Element:
        _check_op_allowed(cmd_xml)
        return await self._send({"type": "op", "cmd": cmd_xml}, with_key=True)

    async def config_show(self, xpath: str) -> ET.Element:
        if not xpath or not xpath.startswith("/"):
            raise PanosWriteRefused("config_show requires an absolute xpath; refused")
        return await self._send({"type": "config", "action": "show", "xpath": xpath},
                                with_key=True)

    async def keygen(self, username: str, password: SecretStr) -> str:
        data = {"type": "keygen", "user": username,
                "password": password.get_secret_value()}
        try:
            result = await self._send(data, with_key=False, keygen=True)
        except PanosApiError:
            raise PanosTransportError("firewall rejected the credentials",
                                      code=codes.PN_KEYGEN_REJECTED) from None
        except PanosTransportError as exc:
            if exc.code == codes.PN_AUTH_REJECTED:
                raise PanosTransportError("firewall rejected the credentials",
                                          code=codes.PN_KEYGEN_REJECTED) from None
            raise
        return parse_keygen(result)

    # ------------------------------------------------------------ transport

    async def _send(self, data: dict, *, with_key: bool, keygen: bool = False) -> ET.Element:
        if self.calls_issued >= self._max_calls:
            raise PanosTransportError("per-run call budget exhausted",
                                      code=codes.PN_CALL_BUDGET)
        headers = {"X-PAN-KEY": self._api_key} if (with_key and self._api_key) else {}
        self.calls_issued += 1
        try:
            resp = await self._http.post(self._url, data=data, headers=headers)
        except httpx.HTTPError as exc:
            if _is_tls_failure(exc):
                raise PanosTransportError("TLS verification of the management certificate failed",
                                          code=codes.PN_TLS_FAILED) from None
            raise PanosTransportError("management interface unreachable",
                                      code=codes.PN_UNREACHABLE) from None
        if resp.status_code == 403:
            raise PanosTransportError("management API rejected the credentials (HTTP 403)",
                                      code=codes.PN_AUTH_REJECTED)
        if not 200 <= resp.status_code < 300:
            raise PanosTransportError(
                f"management API returned HTTP {resp.status_code}", code=codes.PN_UNREACHABLE)
        try:
            return parse_response(resp.content)
        except PanosApiError as exc:
            if exc.code == codes.PN_AUTH_REJECTED:
                raise PanosTransportError("management API rejected the credentials",
                                          code=codes.PN_AUTH_REJECTED) from None
            if keygen:
                raise PanosApiError("firewall rejected the request", code=exc.code,
                                    panos_code=exc.panos_code) from None
            raise

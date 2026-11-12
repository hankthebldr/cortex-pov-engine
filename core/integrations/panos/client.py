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

import re
import ssl
import xml.etree.ElementTree as ET
from typing import Optional

import httpx
from pydantic import SecretStr

from . import codes
from .exceptions import (
    PanosApiError, PanosShapeError, PanosTransportError, PanosWriteRefused)
from .host import DEVICE_NAME_PATTERN, DEVICE_NAME_RE, normalize_mgmt_host
from .xmlparse import parse_keygen, parse_response, safe_fromstring

# ---------------------------------------------------------------- op allowlist
#
# Each command declares its exact argument structure. There is no prefix matching:
# an element the schema does not name is a refusal.

# Defence in depth only: the per-command schema below (_COMMANDS) is the
# structural allowlist that actually decides; this denylist never admits anything.
_FORBIDDEN_TAGS = frozenset({
    "request", "set", "clear", "delete", "debug", "commit", "load", "save", "edit",
    "move", "rename", "import", "export", "revert", "override", "multi-config",
    "schedule", "validate", "install", "upgrade", "restart", "shutdown",
})

_REFUSED = "operational command is not on the read-only allowlist; refused"
_MAX_LEAF = 256


def _refuse() -> PanosWriteRefused:
    return PanosWriteRefused(_REFUSED)


def _leaf_ok(el: ET.Element) -> bool:
    # What is sent is what was checked: no stripping. Leading/trailing whitespace and
    # control characters (incl. newline, tab) are refused, not normalised away.
    text = el.text or ""
    return (len(el) == 0 and 0 < len(text) <= _MAX_LEAF and text == text.strip()
            and not any(ord(c) < 0x20 or 0x7F <= ord(c) <= 0x9F for c in text))


def _only_leaves(node: ET.Element, allowed: frozenset[str], *, required: frozenset[str] = frozenset()) -> bool:
    tags = [c.tag for c in node]
    if len(set(tags)) != len(tags) or not set(tags) <= allowed or not required <= set(tags):
        return False
    return all(_leaf_ok(c) for c in node)


def _no_args(node: ET.Element) -> bool:
    return len(node) == 0 and not (node.text or "").strip()


def _all_or_ip(node: ET.Element) -> bool:
    if len(node) != 1:
        return False
    child = node[0]
    if child.tag == "all":
        return len(child) == 0 and not (child.text or "").strip()
    return child.tag == "ip" and _leaf_ok(child)


def _session_all(node: ET.Element) -> bool:
    # Unfiltered "show session all" dumps every session; only the counted form is allowed.
    if len(node) != 1 or node[0].tag != "filter" or (node[0].text or "").strip():
        return False
    flt = node[0]
    if not _only_leaves(flt, frozenset({"source", "destination", "count"}),
                        required=frozenset({"count"})):
        return False
    return flt.find("count").text == "yes"


_POLICY_LEAVES = frozenset({"from", "to", "source", "destination", "destination-port",
                            "protocol", "application", "source-user"})

_COMMANDS = {
    ("show", "system", "info"): _no_args,
    ("show", "clock"): _no_args,
    ("show", "logging-status"): _no_args,
    ("show", "session", "all"): _session_all,
    ("show", "user", "ip-user-mapping"): _all_or_ip,
    ("show", "object", "registered-ip"): _all_or_ip,
    ("test", "security-policy-match"): lambda n: _only_leaves(n, _POLICY_LEAVES),
    ("test", "nat-policy-match"): lambda n: _only_leaves(n, _POLICY_LEAVES),
    ("test", "routing", "fib-lookup"):
        lambda n: _only_leaves(n, frozenset({"virtual-router", "ip"})),
    ("test", "advanced-routing", "fib-lookup"):
        lambda n: _only_leaves(n, frozenset({"logical-router", "ip"})),
}

# Kept for introspection/tests: the set of allowlisted command paths.
OP_ALLOWLIST: frozenset[tuple[str, ...]] = frozenset(_COMMANDS)


def _check_op_allowed(cmd_xml: str) -> str:
    """Validate ``cmd_xml`` and return the canonical re-serialisation that is SENT.

    The bytes we check are the bytes we send: anything a different XML reader could
    interpret differently (comments, PIs, CDATA, namespaces, attributes, stray text)
    is refused outright rather than normalised away.
    """
    low = cmd_xml.lower() if isinstance(cmd_xml, str) else ""
    if not low or "<!--" in low or "<?" in low or "<![cdata[" in low:
        raise _refuse()
    try:
        root = safe_fromstring(cmd_xml)
    except PanosShapeError:
        raise _refuse() from None
    for el in root.iter():
        tag = el.tag
        if not isinstance(tag, str) or "{" in tag or ":" in tag or el.attrib:
            raise _refuse()
        if tag.lower() in _FORBIDDEN_TAGS:
            raise _refuse()
        if len(el):  # container: no text, no tails between children
            if (el.text or "").strip() or any((c.tail or "").strip() for c in el):
                raise _refuse()
        elif (el.tail or "").strip() and el is not root:
            raise _refuse()
    if (root.tail or "").strip():
        raise _refuse()
    # Descend the single-child chain to a declared command, then apply its schema.
    path = [root.tag]
    node = root
    while tuple(path) not in _COMMANDS:
        if len(node) != 1 or (node.text or "").strip():
            raise _refuse()
        node = node[0]
        path.append(node.tag)
    if not _COMMANDS[tuple(path)](node):
        raise _refuse()
    return ET.tostring(root, encoding="unicode")


# ---------------------------------------------------------------- config xpath allowlist

_NAME = r"[A-Za-z0-9 ._:/+-]{1,63}"
_ENT = rf"entry\[@name='{_NAME}'\]"
_DEV = rf"/config/devices/entry\[@name='{DEVICE_NAME_PATTERN}'\]"
_VSYS = _DEV + r"/vsys/entry\[@name='vsys[0-9]+'\]"
_PVSYS = r"/config/panorama/vsys/entry\[@name='vsys[0-9]+'\]"
_SHARED = "/config/shared"
_PSHARED = "/config/panorama/shared"
_PTYPES = "(?:virus|spyware|vulnerability|url-filtering|wildfire-analysis)"
_PROFILES = rf"/(?:profile-group/{_ENT}|profiles/{_PTYPES}/{_ENT})"

CONFIG_XPATH_ALLOWLIST: tuple[re.Pattern, ...] = tuple(re.compile(p) for p in (
    _DEV + "/deviceconfig/setting/logging",
    _DEV + "/deviceconfig/system/timezone",
    _VSYS + "/log-settings/profiles",
    _VSYS + "/log-settings/syslog",
    _SHARED + "/log-settings/syslog",
    _SHARED + "/log-settings/profiles",
    _VSYS + "/rulebase/decryption/rules",
    _PVSYS + "/log-settings/profiles",
    _PSHARED + "/log-settings/profiles",
    _PVSYS + "/log-settings/syslog",
    _PSHARED + "/log-settings/syslog",
    _PVSYS + "/(?:pre|post)-rulebase/decryption/rules",
    _VSYS + rf"/rulebase/security/rules/{_ENT}",
    _VSYS + "/zone",
    _VSYS + rf"/zone/{_ENT}",
    _VSYS + _PROFILES,
    _SHARED + _PROFILES,
    _PVSYS + rf"/(?:pre|post)-rulebase/security/rules/{_ENT}",
    _PVSYS + _PROFILES,
    _PSHARED + _PROFILES,
))


def allowed_xpath(xpath: str) -> bool:
    if not isinstance(xpath, str) or "|" in xpath or "//" in xpath or ".." in xpath:
        return False
    return any(p.fullmatch(xpath) for p in CONFIG_XPATH_ALLOWLIST)


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
        self._url = f"https://{normalize_mgmt_host(host)}/api/"
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
        canonical = _check_op_allowed(cmd_xml)
        return await self._send({"type": "op", "cmd": canonical}, with_key=True)

    async def config_show(self, xpath: str) -> ET.Element:
        if not allowed_xpath(xpath):
            raise PanosWriteRefused("xpath not in read allowlist")
        return await self._send({"type": "config", "action": "show", "xpath": xpath},
                                with_key=True)

    async def keygen(self, username: str, password: SecretStr) -> str:
        try:
            result = await self._send({"type": "keygen", "user": username},
                                      with_key=False, secret=password)
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

    async def _send(self, data: dict, *, with_key: bool,
                    secret: Optional[SecretStr] = None) -> ET.Element:
        if self.calls_issued >= self._max_calls:
            raise PanosTransportError("per-run call budget exhausted",
                                      code=codes.PN_CALL_BUDGET)
        headers = {"X-PAN-KEY": self._api_key} if (with_key and self._api_key) else {}
        self.calls_issued += 1
        try:
            resp = await self._http.post(
                self._url, headers=headers,
                data={**data, **({"password": secret.get_secret_value()} if secret else {})})
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
            # The host answered: an upstream error on this request, not unreachability.
            raise PanosTransportError(
                f"management API returned HTTP {resp.status_code}", code=codes.PN_UPSTREAM_ERROR)
        try:
            return parse_response(resp.content)
        except PanosApiError as exc:
            if exc.code == codes.PN_AUTH_REJECTED:
                raise PanosTransportError("management API rejected the credentials",
                                          code=codes.PN_AUTH_REJECTED) from None
            msg = exc.message
            if self._api_key and self._api_key in msg:
                msg = msg.replace(self._api_key, "[redacted]")
            # keygen() re-maps every PanosApiError to a fixed PN_KEYGEN_REJECTED message,
            # so this message never surfaces from a keygen call.
            raise PanosApiError(msg, code=exc.code, panos_code=exc.panos_code) from None

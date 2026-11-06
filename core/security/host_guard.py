"""Host-header guard — DNS rebinding cannot reach the unauthenticated API.

Why this exists. SimCore's operator and agent APIs carry no authentication. CORS
used to be ``allow_origins=["*"]`` (any web page could READ every response);
that is now opt-in and off by default. But CORS never stopped DNS rebinding: a
page served from ``rebind.attacker.example`` re-points that name at the DC's
SimCore (127.0.0.1, or the jumpbox's lab address) and the browser then treats
SimCore as *same-origin* with the attacker's page. No cross-origin request ever
happens, so no CORS rule applies. The one thing such a page cannot forge is the
``Host`` header — it carries the attacker's name.

So this pure-ASGI middleware answers ``400 HOST_NOT_ALLOWED`` unless ``Host`` is

* ``localhost`` / ``127.0.0.1`` / ``::1`` (any port),
* any IP literal — an address cannot be rebound,
* this machine's own hostname or FQDN,
* a name in ``CORTEXSIM_ALLOWED_HOSTS`` (comma list; ``*.lab.example`` matches
  subdomains; ``*`` disables the check entirely).

What it does NOT close: a cross-site *simple* request to an IP literal (a form
POST) is delivered by the browser without a preflight, so body-less POST routes
remain forgeable until real authentication exists (SEC-01). ``/api/health``
reports that as ``api_auth: API_UNAUTHENTICATED``.

Portability: stdlib only, no POSIX-only calls, so SimCore keeps booting natively
on Windows. ``socket.getfqdn()`` is a reverse-DNS lookup — on a host with broken
DNS it blocks for seconds, and on macOS it initialises Network.framework — so it
is consulted only for a Host nothing cheaper explains, off the event loop, with
a timeout, and the answer is cached for the life of the process.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import socket
from typing import Any, Awaitable, Callable, Iterable, Optional

logger = logging.getLogger("cortexsim.security.host_guard")

#: Always allowed, whatever the configuration.
LOOPBACK_NAMES = frozenset({"localhost", "127.0.0.1", "::1"})

#: How long the one-time FQDN lookup may take before it is abandoned.
FQDN_TIMEOUT_SECONDS = 2.0

ASGIApp = Callable[[dict, Callable[[], Awaitable[dict]], Callable[[dict], Awaitable[None]]],
                   Awaitable[None]]


def parse_allowed_hosts(raw: Optional[str]) -> list[str]:
    """``"A.example, *.lab.example,,"`` -> ``["a.example", "*.lab.example"]``."""
    out: list[str] = []
    for part in (raw or "").split(","):
        name = part.strip().lower().rstrip(".")
        if name and name not in out:
            out.append(name)
    return out


def _default_hostname() -> str:
    """``socket.gethostname()`` — a local syscall, no DNS."""
    return socket.gethostname()


def _default_fqdn() -> str:
    """``socket.getfqdn()`` — may perform a reverse-DNS lookup."""
    return socket.getfqdn()


def split_host(header: str) -> Optional[str]:
    """Return the lower-cased host part of a Host header, or None if malformed.

    ``[v6]:port`` / ``[v6]`` / ``v4:port`` / ``name:port`` / ``name``. A bare
    unbracketed IPv6 literal is accepted when it parses as one.
    """
    value = (header or "").strip()
    if not value or any(c.isspace() for c in value):
        return None
    if value.startswith("["):
        end = value.find("]")
        if end == -1:
            return None
        host, rest = value[1:end], value[end + 1:]
        if rest and not (rest.startswith(":") and rest[1:].isdigit()):
            return None
        try:
            ipaddress.ip_address(host.replace("%25", "%"))
        except ValueError:
            return None
        return host.lower()
    if value.count(":") > 1:
        try:
            ipaddress.ip_address(value)
        except ValueError:
            return None
        return value.lower()
    if ":" in value:
        host, port = value.rsplit(":", 1)
        if not port.isdigit() or not host:
            return None
    else:
        host = value
    return host.lower().rstrip(".") or None


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host.replace("%25", "%"))
    except ValueError:
        return False
    return True


def _matches(host: str, patterns: Iterable[str]) -> bool:
    for pattern in patterns:
        if pattern.startswith("*."):
            if host.endswith(pattern[1:]) and host != pattern[2:]:
                return True
        elif host == pattern:
            return True
    return False


class HostGuardMiddleware:
    """Pure ASGI middleware enforcing the Host allowlist described above."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        allowed_hosts: Iterable[str] = (),
        hostname_fn: Optional[Callable[[], str]] = None,
        fqdn_fn: Optional[Callable[[], str]] = None,
    ) -> None:
        self.app = app
        self.allowed_hosts = [h.lower().rstrip(".") for h in allowed_hosts if h]
        self.disabled = "*" in self.allowed_hosts
        self._hostname_fn = hostname_fn
        self._fqdn_fn = fqdn_fn
        self._hostname: Optional[set[str]] = None
        self._fqdn_names: Optional[set[str]] = None

    # -- local names ---------------------------------------------------------

    def _hostnames(self) -> set[str]:
        if self._hostname is None:
            fn = self._hostname_fn or _default_hostname
            try:
                name = (fn() or "").lower().rstrip(".")
            except Exception:  # noqa: BLE001 — an unknown hostname admits nothing extra
                name = ""
            self._hostname = {name, name.split(".", 1)[0]} - {""}
        return self._hostname

    async def _fqdns(self) -> set[str]:
        if self._fqdn_names is None:
            # Looked up at call time, not bound at construction, so a test can
            # replace the resolver on an already-built middleware stack.
            fn = self._fqdn_fn or _default_fqdn
            try:
                name = await asyncio.wait_for(asyncio.to_thread(fn), FQDN_TIMEOUT_SECONDS)
                name = (name or "").lower().rstrip(".")
            except Exception as exc:  # noqa: BLE001 — timeout or resolver failure
                logger.warning("host guard: FQDN lookup failed (%s); only the "
                               "hostname and CORTEXSIM_ALLOWED_HOSTS admit names", exc)
                name = ""
            self._fqdn_names = {name, name.split(".", 1)[0]} - {""}
        return self._fqdn_names

    async def is_allowed(self, raw_host: Optional[str]) -> bool:
        if self.disabled:
            return True
        host = split_host(raw_host or "")
        if host is None:
            return False
        if host in LOOPBACK_NAMES or _is_ip_literal(host):
            return True
        if _matches(host, self.allowed_hosts):
            return True
        if host in self._hostnames():
            return True
        return host in await self._fqdns()

    # -- ASGI ----------------------------------------------------------------

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        raw_host: Optional[str] = None
        for key, value in scope.get("headers") or []:
            if key == b"host":
                raw_host = value.decode("latin-1")
                break

        if await self.is_allowed(raw_host):
            await self.app(scope, receive, send)
            return

        shown = (raw_host or "")[:255]
        logger.warning("host guard: refused %s %s with Host=%r",
                       scope.get("method", scope.get("type")), scope.get("path"), shown)
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return

        body = json.dumps({
            "error": "Host not allowed",
            "code": "HOST_NOT_ALLOWED",
            "detail": (
                f"Host {shown!r} is not one SimCore answers to. Requests are accepted "
                f"for localhost, any IP address, this machine's own hostname, and the "
                f"names in CORTEXSIM_ALLOWED_HOSTS — this guard stops DNS-rebinding "
                f"pages from reaching an API that has no authentication. Fix: if this "
                f"name is yours (the console URL or a beacon's --server), add it to "
                f"CORTEXSIM_ALLOWED_HOSTS (comma list, '*.example' for subdomains) and "
                f"restart SimCore, or use the host's IP address."
            ),
        }).encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": 400,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        })
        await send({"type": "http.response.body", "body": body})

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
import re
import socket
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Iterable, Optional

logger = logging.getLogger("cortexsim.security.host_guard")

#: Always allowed, whatever the configuration.
LOOPBACK_NAMES = frozenset({"localhost", "127.0.0.1", "::1"})

#: How long the one-time FQDN lookup may take before it is abandoned.
FQDN_TIMEOUT_SECONDS = 2.0

ASGIApp = Callable[[dict, Callable[[], Awaitable[dict]], Callable[[dict], Awaitable[None]]],
                   Awaitable[None]]


#: One DNS label: letters, digits, ``-`` and ``_`` (seen in lab names), not
#: starting or ending with ``-``.
_LABEL = r"[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?"
_HOSTNAME_RE = re.compile(rf"^{_LABEL}(?:\.{_LABEL})*$")


def normalise_allowed_host(entry: str) -> Optional[str]:
    """One ``CORTEXSIM_ALLOWED_HOSTS`` entry -> the bare pattern it means.

    The guard compares the request's host WITHOUT its port, so an entry is
    reduced to the same shape. What a DC pastes after a refusal is usually the
    beacon's ``--server`` or the address bar, so all of these mean
    ``jumpbox.lab``::

        jumpbox.lab   jumpbox.lab:8888   http://jumpbox.lab:8888/   JUMPBOX.LAB.

    ``.lab.example`` is read as ``*.lab.example``; ``[v6]:port`` as the bare
    address; ``*`` stays ``*`` (the guard is off).

    Returns ``None`` for an entry that could never equal a Host — a wildcard
    anywhere but a leading ``*.``, whitespace, a non-numeric port, an empty
    name. Such an entry is REPORTED (:func:`unusable_allowed_hosts`), never
    kept: an inert entry echoed back as "allowed" is how a refusal turns into
    ``CORTEXSIM_ALLOWED_HOSTS=*``.

    Normalising never widens what is admitted: only the scheme, port, path and
    trailing dot are dropped, and the name is still matched exactly.
    """
    value = (entry or "").strip().lower()
    if not value:
        return None
    if value == "*":
        return "*"
    if "://" in value:
        value = value.split("://", 1)[1]
    for sep in ("/", "?", "#"):
        value = value.split(sep, 1)[0]
    if not value or any(c.isspace() for c in value) or "@" in value:
        return None

    wildcard = False
    if value.startswith("*."):
        wildcard, value = True, value[2:]
    elif value.startswith("."):
        wildcard, value = True, value[1:]

    if value.startswith("["):                      # [v6] or [v6]:port
        end = value.find("]")
        rest = value[end + 1:] if end != -1 else "x"
        if end == -1 or (rest and not (rest.startswith(":") and rest[1:].isdigit())):
            return None
        value = value[1:end]
    elif value.count(":") == 1:                    # name:port / v4:port
        value, port = value.rsplit(":", 1)
        if not port.isdigit():
            return None
    value = value.rstrip(".")
    if not value:
        return None

    if _is_ip_literal(value):
        return None if wildcard else value
    if not _HOSTNAME_RE.match(value):
        return None
    return f"*.{value}" if wildcard else value


def _allowed_host_entries(raw: Optional[str]) -> tuple[list[str], list[str]]:
    """``(usable patterns, unusable entries as written)``, both de-duplicated."""
    usable: list[str] = []
    unusable: list[str] = []
    for part in (raw or "").split(","):
        entry = part.strip()
        if not entry:
            continue
        pattern = normalise_allowed_host(entry)
        if pattern is None:
            if entry not in unusable:
                unusable.append(entry)
        elif pattern not in usable:
            usable.append(pattern)
    return usable, unusable


def parse_allowed_hosts(raw: Optional[str]) -> list[str]:
    """``"A.example, http://b.lab:8888, .c.lab,,"`` ->
    ``["a.example", "b.lab", "*.c.lab"]`` — the patterns the guard matches.

    Entries that can never match are dropped here and listed by
    :func:`unusable_allowed_hosts`.
    """
    return _allowed_host_entries(raw)[0]


def unusable_allowed_hosts(raw: Optional[str]) -> list[str]:
    """The ``CORTEXSIM_ALLOWED_HOSTS`` entries, as written, that match no Host."""
    return _allowed_host_entries(raw)[1]


#: Longest refused Host kept in the record, and how many distinct ones.
_REFUSED_HOST_MAX_CHARS = 255
_REFUSED_HOSTS_KEPT = 10


class RefusalLog:
    """What the guard has turned away since boot — bounded, in memory.

    A refusal used to leave one line in the server log and nothing else: the
    installer's own telemetry POST is refused by the same guard, so
    ``/api/agents/install/attempts`` stayed empty, and ``/api/health`` could
    not say which name was being refused. This is the record ``/api/health``
    reads (``api_auth.host_guard_refusals`` / ``last_refused_hosts``).

    The Host header is attacker-controlled, so both dimensions are capped: at
    most ``max_hosts`` distinct names (the least recently refused is dropped)
    and :data:`_REFUSED_HOST_MAX_CHARS` characters each. Reset on restart, like
    the install-attempt record.
    """

    def __init__(self, max_hosts: int = _REFUSED_HOSTS_KEPT) -> None:
        self.max_hosts = max(1, int(max_hosts))
        self.total = 0
        self._hosts: "OrderedDict[str, dict[str, Any]]" = OrderedDict()

    def record(self, host: str, *, method: str, path: str) -> None:
        self.total += 1
        key = (host or "")[:_REFUSED_HOST_MAX_CHARS]
        entry = self._hosts.pop(key, None) or {"host": key, "count": 0}
        entry["count"] += 1
        entry["last_method"] = (method or "")[:16]
        entry["last_path"] = (path or "")[:200]
        entry["last_at"] = datetime.now(timezone.utc).isoformat()
        self._hosts[key] = entry               # most recent last
        while len(self._hosts) > self.max_hosts:
            self._hosts.popitem(last=False)

    def snapshot(self) -> dict[str, Any]:
        """``{"total": n, "hosts": [most recent first]}`` — a copy."""
        return {"total": self.total,
                "hosts": [dict(e) for e in reversed(self._hosts.values())]}

    def clear(self) -> None:
        self.total = 0
        self._hosts.clear()


#: The process-wide record every guard writes to unless given its own.
refusals = RefusalLog()


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


def host_of(server: str) -> str:
    """``http://jumpbox.lab:8888/x`` -> ``jumpbox.lab:8888`` (a Host header's
    shape); a bare ``host`` or ``host:port`` is returned unchanged."""
    value = (server or "").strip()
    if "://" in value:
        value = value.split("://", 1)[1]
    for sep in ("/", "?", "#"):
        value = value.split(sep, 1)[0]
    return value.rsplit("@", 1)[-1]


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


_route_guard: Optional[tuple[str, "HostGuardMiddleware"]] = None


async def _no_app(scope: dict, receive: Any, send: Any) -> None:  # pragma: no cover
    return None


async def would_admit(server: str) -> bool:
    """Would THIS deployment's guard admit a request addressed to ``server``?

    For route code that renders a ``--server`` into something an endpoint will
    run (the installer, a bundle, a manifest) and does not hold the mounted
    middleware. The answer is computed from the same setting the mounted guard
    was built from (``CORTEXSIM_ALLOWED_HOSTS``) and the same local names, so
    the two agree. Records nothing: asking is not a refusal.
    """
    global _route_guard
    from config import settings  # noqa: PLC0415 — keep this module import-light

    raw = getattr(settings, "CORTEXSIM_ALLOWED_HOSTS", "") or ""
    if _route_guard is None or _route_guard[0] != raw:
        _route_guard = (raw, HostGuardMiddleware(
            _no_app, allowed_hosts=parse_allowed_hosts(raw)))
    return await _route_guard[1].would_admit(server)


class HostGuardMiddleware:
    """Pure ASGI middleware enforcing the Host allowlist described above."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        allowed_hosts: Iterable[str] = (),
        hostname_fn: Optional[Callable[[], str]] = None,
        fqdn_fn: Optional[Callable[[], str]] = None,
        refusals: Optional[RefusalLog] = None,
    ) -> None:
        self.app = app
        # Normalised here too, so a caller that passes entries as written (not
        # through parse_allowed_hosts) gets the same guard.
        patterns: list[str] = []
        for entry in allowed_hosts:
            if not entry:
                continue
            pattern = normalise_allowed_host(entry)
            if pattern is None:
                logger.warning("host guard: allowlist entry %r can never match a "
                               "Host header and is ignored", entry)
            elif pattern not in patterns:
                patterns.append(pattern)
        self.allowed_hosts = patterns
        self.disabled = "*" in self.allowed_hosts
        self._hostname_fn = hostname_fn
        self._fqdn_fn = fqdn_fn
        self._hostname: Optional[set[str]] = None
        self._fqdn_names: Optional[set[str]] = None
        # None -> the module-level record, looked up at refusal time so a test
        # (or a reload) that swaps it is honoured.
        self._refusals = refusals

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

    async def would_admit(self, server: str) -> bool:
        """Would a request addressed to ``server`` pass this guard?

        ``server`` is a URL or a bare host (``http://jumpbox.lab:8888``,
        ``jumpbox.lab``). For whatever bakes a ``--server`` into an installer,
        a bundle or a manifest: SimCore used to render those for a name its own
        guard then refused, and the endpoint failed with a bare 400.
        """
        if self.disabled:
            return True
        return await self.is_allowed(host_of(server))

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

        shown = (raw_host or "")[:_REFUSED_HOST_MAX_CHARS]
        # The name WITHOUT its port: that is what the guard compared, and what
        # an allowlist entry has to be. Quoting `host:port` and saying "add it"
        # sent operators to an entry that could never match (RG-GUARD-03).
        bare = split_host(raw_host or "")
        method = str(scope.get("method", scope.get("type")) or "")
        path = str(scope.get("path") or "")
        logger.warning("host guard: refused %s %s with Host=%r", method, path, shown)
        (self._refusals if self._refusals is not None else refusals).record(
            bare if bare is not None else shown, method=method, path=path)
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return

        accepted = (
            "Requests are accepted for localhost, any IP address, this machine's "
            "own hostname, and the names in CORTEXSIM_ALLOWED_HOSTS — this guard "
            "stops DNS-rebinding pages from reaching an API that has no "
            "authentication."
        )
        if bare is None:
            detail = (
                f"The Host header {shown!r} is malformed, so it names no host "
                f"SimCore can answer to. {accepted} Fix: send a plain "
                f"'name' or 'name:port' Host, or use the host's IP address."
            )
        else:
            detail = (
                f"Host {bare!r} is not one SimCore answers to. {accepted} Fix: if "
                f"this name is yours (the console URL or a beacon's --server), set "
                f"CORTEXSIM_ALLOWED_HOSTS={bare} (the name only — a scheme or port "
                f"is ignored; comma list; '*.example' for subdomains) and restart "
                f"SimCore, or use the host's IP address. GET /api/health, reached "
                f"by IP, lists the names refused since boot under api_auth."
            )
        body = json.dumps({
            "error": "Host not allowed",
            "code": "HOST_NOT_ALLOWED",
            "detail": detail,
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

"""Server builder and the two transports (spec section 4).

:func:`build` is the one place a ``FastMCP`` is made: every tool goes through
:func:`registry.register`, which enforces each input model's ``extra="forbid"``
and asserts the registered set equals ``naming.CANONICAL_IDS``. The two
transports differ only in the ``httpx`` transport handed to ``SimCoreClient``:

* stdio (``python -m mcp_server``) — real HTTP to ``CORTEXSIM_URL``;
* streamable HTTP (:func:`mount`, opt-in ``CORTEXSIM_MCP_HTTP``) — a route at
  ``/mcp`` on SimCore's own app, reaching ``/api/...`` through
  ``httpx.ASGITransport(app)``: in-process, zero sockets.
"""
from __future__ import annotations

import dataclasses
import logging
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Iterable, Optional, Sequence

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel

from .client import SimCoreClient, SimCoreUnreachable
from .errors import McpToolError
from .naming import canonical_id
from .ratelimit import TokenBucket
from .registry import ToolContext, ToolSpec, register
from .tools import cortexsim, xsiam

logger = logging.getLogger("cortexsim.mcp")

MCP_PATH = "/mcp"
_LOOPBACK = ("localhost", "127.0.0.1", "[::1]")


def all_specs() -> list[ToolSpec]:
    return cortexsim.specs() + xsiam.specs()


def parse_hosts(raw: str) -> list[str]:
    """``CORTEXSIM_MCP_ALLOWED_HOSTS``: comma list, stripped, empties dropped."""
    return [h.strip() for h in (raw or "").split(",") if h.strip()]


def allowed_hosts_for(extra: Sequence[str] = ()) -> list[str]:
    """Loopback plus each configured host, bare and with any port (``:*``)."""
    hosts: list[str] = []
    for h in (*_LOOPBACK, *extra):
        for v in (h, f"{h}:*"):
            if v not in hosts:
                hosts.append(v)
    return hosts


def _allowed_origins(hosts: Sequence[str]) -> list[str]:
    return [f"{scheme}://{h}" for h in hosts for scheme in ("http", "https")]


def _logged(spec: ToolSpec) -> ToolSpec:
    """One log line per tenant-class call: tool id, tenant, queries_issued,
    duration. Never a body, a query result or a credential. A count that was
    not reported is ``unknown``, never 0."""

    impl = spec.impl

    async def timed(ctx: ToolContext, args: BaseModel) -> Any:
        started = time.monotonic()
        tenant: Optional[str] = getattr(args, "tenant", None)
        queries: Optional[int] = None
        outcome = "ok"
        try:
            env = await impl(ctx, args)
            tenant, queries = env.evidence.tenant or tenant, env.evidence.queries_issued
            return env
        except McpToolError as err:
            # Same reading as registry.wrap's error evidence.
            tenant, queries, outcome = err.tenant or tenant, getattr(err, "queries_issued", 0), err.code
            raise
        except SimCoreUnreachable as err:
            queries, outcome = 0, err.code  # the request never left
            raise
        except Exception as exc:
            outcome = getattr(exc, "code", type(exc).__name__)
            raise
        finally:
            logger.info(
                "mcp tenant-call tool=%s tenant=%s queries_issued=%s duration_ms=%d outcome=%s",
                spec.id, tenant or "-", "unknown" if queries is None else queries,
                int((time.monotonic() - started) * 1000), outcome)

    return dataclasses.replace(spec, impl=timed)


def build(ctx: ToolContext, *, stateless: bool = True, allowed_hosts: Sequence[str] = (),
          specs: Optional[Iterable[ToolSpec]] = None) -> FastMCP:
    hosts = allowed_hosts_for(allowed_hosts)
    mcp = FastMCP(
        "cortexsim",
        json_response=True,
        stateless_http=stateless,
        streamable_http_path=MCP_PATH,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=hosts,
            allowed_origins=_allowed_origins(hosts),
        ),
    )
    chosen = list(all_specs() if specs is None else specs)
    register(mcp, [_logged(s) if s.network == "tenant" else s for s in chosen], ctx)
    return mcp


def registered_ids(mcp: FastMCP) -> list[str]:
    """Canonical ids of what is actually registered on ``mcp``, read back from it."""
    return [canonical_id(t.name) for t in mcp._tool_manager.list_tools()]


def mount(app: Any, *, settings: Any) -> Optional[FastMCP]:
    """Add the streamable-HTTP endpoint at exactly ``/mcp`` to ``app``.

    ``None`` (and nothing touched) when ``CORTEXSIM_MCP_HTTP`` is false. Must be
    called before the static catch-all at ``/``, which would otherwise answer
    ``/mcp``. The SDK's own ``Route`` is added to ``app`` rather than mounting
    its Starlette sub-app: a ``Mount("/mcp")`` matches ``/mcp/...`` only, so the
    bare ``/mcp`` a client is configured with would fall through to the console.
    The session manager runs in SimCore's lifespan (:func:`lifespan_for`).
    """
    if not settings.CORTEXSIM_MCP_HTTP:
        return None
    client = SimCoreClient(settings.CORTEXSIM_URL, transport=httpx.ASGITransport(app=app))
    ctx = ToolContext(client=client, bucket=TokenBucket(settings.CORTEXSIM_MCP_MAX_TENANT_CALLS_PER_MINUTE))
    mcp = build(ctx, stateless=True, allowed_hosts=parse_hosts(settings.CORTEXSIM_MCP_ALLOWED_HOSTS))
    sub = mcp.streamable_http_app()
    routes = [r for r in sub.routes if getattr(r, "path", None) == MCP_PATH]
    if len(routes) != 1 or len(sub.routes) != 1:
        raise RuntimeError(f"unexpected streamable-HTTP route table from the MCP SDK: "
                           f"{[getattr(r, 'path', r) for r in sub.routes]}")
    app.router.routes.append(routes[0])
    app.state.mcp = mcp
    logger.info("MCP streamable-HTTP mounted at %s with %d tools", MCP_PATH, len(registered_ids(mcp)))
    return mcp


@asynccontextmanager
async def lifespan_for(app: Any) -> AsyncIterator[None]:
    """Run the mounted session manager for the app's lifetime; a no-op when unmounted."""
    mcp = getattr(app.state, "mcp", None)
    if mcp is None:
        yield
        return
    async with mcp.session_manager.run():
        yield

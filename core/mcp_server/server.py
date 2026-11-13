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
import json
import logging
import shlex
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Iterable, Optional, Sequence

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.routing import Route
from pydantic import BaseModel

from .auth import McpGate
from .client import SimCoreClient, SimCoreUnreachable, encode_segment
from .errors import MCP_HOST_ACTION_DISABLED, REMEDIATION, McpToolError
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


# Where a human does what a refused host action would have done (spec section 7.2).
_HOST_ACTION_CONSOLE = {"cortexsim.run.launch": "#/library", "cortexsim.run.abort": "#/runs"}


def _human_curl(spec: ToolSpec, args: BaseModel, base_url: str) -> Optional[str]:
    """The request a human runs, or ``None`` when it cannot be built (an unusable
    run id): the refusal code never depends on this."""
    try:
        return _human_curl_unsafe(spec, args, base_url)
    except Exception:  # noqa: BLE001
        return None


def _human_curl_unsafe(spec: ToolSpec, args: BaseModel, base_url: str) -> Optional[str]:
    if spec.id == "cortexsim.run.launch":
        body = shlex.quote(json.dumps(args.to_launch_body(), sort_keys=True))  # type: ignore[attr-defined]
        return f"curl -X POST {base_url}/api/runs -H 'content-type: application/json' -d {body}"
    if spec.id == "cortexsim.run.abort":
        return f"curl -X POST {base_url}/api/runs/{encode_segment(args.run_id)}/abort"  # type: ignore[attr-defined]
    return None


def gate_host_actions(specs: list[ToolSpec], *, allow: bool, base_url: str) -> list[ToolSpec]:
    """With ``allow`` false, every tool that acts on a customer host
    (``network == "host"`` and class write or destructive) keeps its place in
    the registry but refuses with ``MCP_HOST_ACTION_DISABLED``, naming the
    console page and the request a human would run. The launch/abort request is never sent.
    """
    if allow:
        return list(specs)

    def refusing(spec: ToolSpec) -> ToolSpec:
        async def refuse(ctx: ToolContext, args: BaseModel) -> Any:
            raise McpToolError(
                MCP_HOST_ACTION_DISABLED,
                "Host actions are disabled on this MCP server",
                f"{spec.id} would act on a customer host; it was not sent. A human runs it from the "
                "console, or the operator sets CORTEXSIM_MCP_ALLOW_HOST_ACTIONS=1 and restarts.",
                remediation=REMEDIATION[MCP_HOST_ACTION_DISABLED],
                data={"console": _HOST_ACTION_CONSOLE.get(spec.id, "#/runs"),
                      "execute_as_human": _human_curl(spec, args, base_url)})

        return dataclasses.replace(spec, impl=refuse)

    return [refusing(s) if s.network == "host" and s.klass in ("write", "destructive") else s
            for s in specs]


def build(ctx: ToolContext, *, stateless: bool = True, allowed_hosts: Sequence[str] = (),
          specs: Optional[Iterable[ToolSpec]] = None, allow_host_actions: bool = False) -> FastMCP:
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
    chosen = gate_host_actions(list(all_specs() if specs is None else specs),
                               allow=allow_host_actions, base_url=ctx.client.base_url)
    register(mcp, [_logged(s) if s.network == "tenant" else s for s in chosen], ctx)
    return mcp


def registered_ids(mcp: FastMCP) -> list[str]:
    """Canonical ids of what is actually registered on ``mcp``, read back from it."""
    return [canonical_id(t.name) for t in mcp._tool_manager.list_tools()]


def mount(app: Any, *, settings: Any) -> Optional[FastMCP]:
    """Add the streamable-HTTP endpoint at exactly ``/mcp`` to ``app``.

    ``None`` (and nothing touched) when ``CORTEXSIM_MCP_HTTP`` is false, or when
    ``CORTEXSIM_MCP_TOKEN`` is empty: the mount is refused rather than opened
    without a bearer token (:mod:`mcp_server.auth`). Must be
    called before the static catch-all at ``/``, which would otherwise answer
    ``/mcp``. The SDK's own ``Route`` is added to ``app`` rather than mounting
    its Starlette sub-app: a ``Mount("/mcp")`` matches ``/mcp/...`` only, so the
    bare ``/mcp`` a client is configured with would fall through to the console.
    The session manager runs in SimCore's lifespan (:func:`lifespan_for`).
    """
    if not settings.CORTEXSIM_MCP_HTTP:
        return None
    token = settings.CORTEXSIM_MCP_TOKEN.get_secret_value().strip()
    if not token:
        logger.error("CORTEXSIM_MCP_TOKEN is not set — /mcp is NOT mounted")
        return None
    client = SimCoreClient(settings.CORTEXSIM_URL, transport=httpx.ASGITransport(app=app))
    ctx = ToolContext(client=client, bucket=TokenBucket(settings.CORTEXSIM_MCP_MAX_TENANT_CALLS_PER_MINUTE))
    # Settings reads CORTEXSIM_MCP_ALLOW_HOST_ACTIONS from the environment first,
    # so this is the same rule the stdio entry applies (__main__).
    mcp = build(ctx, stateless=True, allowed_hosts=parse_hosts(settings.CORTEXSIM_MCP_ALLOWED_HOSTS),
                allow_host_actions=bool(settings.CORTEXSIM_MCP_ALLOW_HOST_ACTIONS))
    sub = mcp.streamable_http_app()
    routes = [r for r in sub.routes if getattr(r, "path", None) == MCP_PATH]
    if len(routes) != 1 or len(sub.routes) != 1:
        raise RuntimeError(f"unexpected streamable-HTTP route table from the MCP SDK: "
                           f"{[getattr(r, 'path', r) for r in sub.routes]}")
    route = routes[0]
    app.router.routes.append(Route(MCP_PATH, endpoint=McpGate(route.endpoint, token=token, loopback_only=False),
                                   name=route.name))
    app.state.mcp = mcp
    app.state.mcp_client = client
    logger.info("MCP streamable-HTTP mounted at %s with %d tools", MCP_PATH, len(registered_ids(mcp)))
    return mcp


@asynccontextmanager
async def lifespan_for(app: Any) -> AsyncIterator[None]:
    """Run the mounted session manager for the app's lifetime and close the
    in-process SimCore client on the way out; a no-op when unmounted."""
    mcp = getattr(app.state, "mcp", None)
    if mcp is None:
        yield
        return
    try:
        async with mcp.session_manager.run():
            yield
    finally:
        client = getattr(app.state, "mcp_client", None)
        if client is not None:
            await client.aclose()

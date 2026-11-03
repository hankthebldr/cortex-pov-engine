"""Tool registration: the one place where results and errors are shaped.

A tool is a :class:`ToolSpec` (canonical id, input model, async ``impl``).
:func:`wrap` turns it into the function FastMCP registers: it validates the raw
arguments against the input model (extra fields forbidden), awaits ``impl``
and returns a ``CallToolResult`` whose first text line is the evidence
statement. Every failure becomes ``isError`` with a code; no bare exception
escapes, because the SDK's generic wrapper would drop the code.

The helpers tools share (:func:`take_token`, :func:`resolve_tenant`, the two
evidence builders and the caps) live here, once.
"""
from __future__ import annotations

import inspect
import json
import time
from dataclasses import dataclass, field
from typing import Annotated, Any, Awaitable, Callable, Iterable, Literal, Optional

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.utilities.func_metadata import ArgModelBase
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BaseModel, ConfigDict

from .client import SimCoreClient, SimCoreError, SimCoreShapeUnknown, SimCoreUnreachable
from .envelope import Envelope, Evidence, as_of_from_date_header, ledger_from_snapshot, statement
from .errors import (
    MCP_RATE_LIMITED,
    NO_TENANT_CONFIGURED,
    REMEDIATION,
    TENANT_AMBIGUOUS,
    McpToolError,
)
from .naming import CANONICAL_IDS, group_of, wire_name
from .ratelimit import TokenBucket

MCP_INTERNAL_ERROR = "MCP_INTERNAL_ERROR"
TEXT_CAP_BYTES = 262144


@dataclass
class ToolContext:
    client: SimCoreClient
    bucket: TokenBucket
    tenant_verified_cache_seconds: float = 5.0
    _readiness: Optional[dict] = field(default=None, init=False, repr=False)
    _readiness_at: float = field(default=0.0, init=False, repr=False)


@dataclass(frozen=True)
class ToolSpec:
    id: str
    input_model: type[BaseModel]
    impl: Callable[[ToolContext, BaseModel], Awaitable[Envelope]]
    klass: Literal["read", "write", "destructive"]
    network: Literal["none", "simcore", "tenant", "host"]
    scopes: tuple[str, ...]
    description: str
    idempotent: bool = True


class TenantCallError(McpToolError):
    """An error from a call that may have reached a tenant: keeps its count.

    ``statement_override`` exists for the one case the section 7 table cannot
    phrase: a tenant was called but SimCore did not report how many operations
    it issued, which is neither zero nor a number.
    """

    def __init__(
        self,
        code: str,
        error: str,
        detail: str,
        *,
        tenant: Optional[str],
        queries_issued: int = 0,
        ledger_snapshot: Optional[dict] = None,
        remediation: Optional[str] = None,
        data: Any = None,
        statement_override: Optional[str] = None,
    ) -> None:
        super().__init__(code, error, detail, remediation=remediation, basis="simcore",
                         tenant=tenant, data=data)
        self.queries_issued = queries_issued
        self.ledger_snapshot = ledger_snapshot
        self.statement_override = statement_override


# --- readiness (SimCore-local, zero outbound) ---------------------------------
async def readiness(ctx: ToolContext, *, fresh: bool = False) -> dict:
    """``GET /api/readiness``, cached ``tenant_verified_cache_seconds``."""
    now = time.monotonic()
    if (not fresh and ctx._readiness is not None
            and now - ctx._readiness_at < ctx.tenant_verified_cache_seconds):
        return ctx._readiness
    body = await ctx.client.get("/api/readiness")
    tv = body.get("tenant_verified")
    if isinstance(tv, bool) or not isinstance(tv, int) or not isinstance(body.get("integrations"), list):
        raise SimCoreShapeUnknown(200, "GET /api/readiness: missing int tenant_verified or list integrations")
    ctx._readiness, ctx._readiness_at = body, now
    return body


async def _tenant_verified(ctx: ToolContext) -> int:
    return int((await readiness(ctx))["tenant_verified"])


def _as_of(ctx: ToolContext) -> str:
    return as_of_from_date_header(ctx.client.last_date)


async def ledger_for(ctx: ToolContext, tenant: Optional[str]) -> Optional[dict]:
    """The readiness ledger snapshot for vault name ``tenant``; ``None`` when no match."""
    if tenant is None:
        return None
    for integ in (await readiness(ctx, fresh=True))["integrations"]:
        if isinstance(integ, dict) and integ.get("name") == tenant:
            return integ.get("ledger")
    return None


async def evidence_for_simcore(ctx: ToolContext, *, basis: str = "authored", truncated: bool = False) -> Evidence:
    tv = await _tenant_verified(ctx)
    return Evidence(
        basis=basis, queries_issued=0, http_round_trips=None, tenant=None, ledger=None,
        tenant_verified=tv,
        statement=statement(basis=basis, queries_issued=0, tenant=None, tenant_verified=tv),
        as_of=_as_of(ctx), truncated=truncated,
    )


async def evidence_for_tenant(
    ctx: ToolContext,
    *,
    tenant: Optional[str],
    queries_issued: int,
    ledger_snapshot: Optional[dict],
    http_round_trips: Optional[int] = None,
    basis: Optional[str] = None,
) -> Evidence:
    """``reachable`` when the route reports ``queries_issued >= 1``; otherwise
    ``configured`` (a credential was named, nothing answered) or ``simcore``."""
    if basis is None:
        basis = "reachable" if queries_issued >= 1 else ("configured" if tenant else "simcore")
    tv = await _tenant_verified(ctx)
    return Evidence(
        basis=basis, queries_issued=queries_issued, http_round_trips=http_round_trips,
        tenant=tenant, ledger=ledger_from_snapshot(ledger_snapshot), tenant_verified=tv,
        statement=statement(basis=basis, queries_issued=queries_issued, tenant=tenant, tenant_verified=tv),
        as_of=_as_of(ctx), truncated=False,
    )


# --- shared guards ------------------------------------------------------------
def take_token(ctx: ToolContext) -> None:
    """Spend one tenant-class token; ``MCP_RATE_LIMITED`` when the bucket is empty."""
    wait = ctx.bucket.acquire()
    if wait:
        raise McpToolError(
            MCP_RATE_LIMITED, "tenant-class call rate exceeded for this MCP process",
            f"retry_after_seconds={int(wait)}", remediation=REMEDIATION[MCP_RATE_LIMITED],
            basis="authored", data={"retry_after_seconds": int(wait)},
        )


async def resolve_tenant(ctx: ToolContext, tenant: Optional[str], *, kind: str,
                         required: bool = True) -> Optional[str]:
    """Spec section 5.4: by vault name, never by URL.

    A name the caller gives is returned as-is (the route is the authority on
    whether it exists). Otherwise one credential of ``kind`` is the default,
    more than one is ``TENANT_AMBIGUOUS``, none is ``NO_TENANT_CONFIGURED`` —
    or ``None`` when ``required`` is false, for routes that answer "no tenant"
    themselves (verify's ``pending`` + ``no_tenant_integration``).
    """
    if tenant is not None:
        return tenant
    names = [i["name"] for i in (await readiness(ctx))["integrations"]
             if isinstance(i, dict) and i.get("kind") == kind]
    if len(names) == 1:
        return names[0]
    if len(names) > 1:
        raise McpToolError(
            TENANT_AMBIGUOUS, f"{len(names)} {kind} credentials are configured",
            f"pass tenant= one of: {', '.join(sorted(names))}",
            remediation=REMEDIATION[TENANT_AMBIGUOUS], basis="configured",
        )
    if not required:
        return None
    raise McpToolError(
        NO_TENANT_CONFIGURED, f"no {kind} credential is configured",
        "nothing was queried", remediation=REMEDIATION[NO_TENANT_CONFIGURED], basis="authored",
    )


# --- caps (spec section 5.5) ---------------------------------------------------
def cap_list(items: list, *, limit: int, offset: int) -> tuple[list, bool]:
    page = items[offset:offset + limit]
    return page, offset + len(page) < len(items)


def cap_text(text: str, *, max_bytes: int = TEXT_CAP_BYTES) -> tuple[str, bool, int]:
    """``(text, truncated, total_bytes)``, cut on a UTF-8 character boundary."""
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text, False, len(raw)
    return raw[:max_bytes].decode("utf-8", errors="ignore"), True, len(raw)


# --- registration -------------------------------------------------------------
def annotations_for(spec: ToolSpec) -> ToolAnnotations:
    return ToolAnnotations(
        readOnlyHint=spec.klass == "read",
        destructiveHint=spec.klass == "destructive",
        idempotentHint=spec.idempotent,
        openWorldHint=spec.network in {"tenant", "host"},
    )


def flat_signature(input_model: type[BaseModel]) -> inspect.Signature:
    params = []
    for name, f in input_model.model_fields.items():
        ann = Annotated[(f.annotation, *f.metadata)] if f.metadata else f.annotation
        default = (inspect.Parameter.empty if f.is_required()
                   else f.get_default(call_default_factory=True))
        params.append(inspect.Parameter(name, inspect.Parameter.KEYWORD_ONLY, default=default, annotation=ann))
    return inspect.Signature(params, return_annotation=Envelope)


class _RawArguments(ArgModelBase):
    """Hands every argument to :func:`wrap` unvalidated, so the tool's own
    input model (``extra="forbid"``) is the one validator and a refused field
    becomes a coded error result instead of being silently dropped."""

    model_config = ConfigDict(extra="allow", arbitrary_types_allowed=True)

    def model_dump_one_level(self) -> dict[str, Any]:
        return dict(self.model_extra or {})


def _error_result(code: str, error: str, detail: str, structured: dict) -> CallToolResult:
    return CallToolResult(
        isError=True,
        content=[TextContent(type="text", text=f"{code}: {error} — {detail}")],
        structuredContent=structured,
    )


async def _safe_tenant_verified(ctx: ToolContext) -> int:
    try:
        return await _tenant_verified(ctx)
    except Exception:  # noqa: BLE001 — an error result must still be built
        return int(ctx._readiness["tenant_verified"]) if ctx._readiness else 0


async def _error_evidence(ctx: ToolContext, *, basis: str, tenant: Optional[str] = None,
                          queries_issued: int = 0, ledger_snapshot: Optional[dict] = None,
                          unreachable: bool = False, statement_override: Optional[str] = None) -> Evidence:
    tv = await _safe_tenant_verified(ctx)
    text = statement_override.replace("{tenant_verified}", str(tv)) if statement_override else statement(
        basis=basis, queries_issued=queries_issued, tenant=tenant, tenant_verified=tv,
        simcore_unreachable=unreachable)
    return Evidence(
        basis=basis, queries_issued=queries_issued, tenant=tenant,
        ledger=ledger_from_snapshot(ledger_snapshot), tenant_verified=tv, statement=text,
        as_of=None if unreachable else _as_of(ctx),
    )


def wrap(spec: ToolSpec, ctx: ToolContext) -> Callable[..., Awaitable[CallToolResult]]:
    async def tool(**kwargs: Any) -> CallToolResult:
        try:
            args = spec.input_model(**kwargs)
            env = await spec.impl(ctx, args)
            dumped = env.model_dump(mode="json")
            text = f"{env.evidence.statement}\n{json.dumps(dumped, sort_keys=True)}"
            return CallToolResult(content=[TextContent(type="text", text=text)], structuredContent=dumped)
        except McpToolError as err:
            ev = await _error_evidence(
                ctx, basis=err.basis, tenant=err.tenant,
                queries_issued=getattr(err, "queries_issued", 0),
                ledger_snapshot=getattr(err, "ledger_snapshot", None),
                statement_override=getattr(err, "statement_override", None))
            return _error_result(err.code, err.error, err.detail, err.as_structured(ev))
        except SimCoreUnreachable as err:
            ev = await _error_evidence(ctx, basis="simcore", unreachable=True)
            return _error_result(err.code, err.error, err.detail, _structured(err, ev))
        except SimCoreError as err:
            ev = await _error_evidence(ctx, basis="simcore")
            return _error_result(err.code, err.error, err.detail, _structured(err, ev))
        except Exception as exc:  # noqa: BLE001 — never let a bare exception escape
            ev = await _error_evidence(ctx, basis="authored")
            name = type(exc).__name__
            detail = str(exc)[:2000]
            return _error_result(MCP_INTERNAL_ERROR, name, detail, {
                "error": name, "code": MCP_INTERNAL_ERROR, "detail": detail,
                "remediation": None, "evidence": ev.model_dump(),
            })

    sig = flat_signature(spec.input_model)
    tool.__signature__ = sig  # type: ignore[attr-defined]
    tool.__annotations__ = {**{p.name: p.annotation for p in sig.parameters.values()}, "return": Envelope}
    tool.__name__ = wire_name(spec.id)
    tool.__qualname__ = tool.__name__
    tool.__doc__ = spec.description
    return tool


def _structured(err: SimCoreError, ev: Evidence) -> dict:
    return {"error": err.error, "code": err.code, "detail": err.detail,
            "remediation": REMEDIATION.get(err.code), "evidence": ev.model_dump()}


def register(mcp: FastMCP, specs: Iterable[ToolSpec], ctx: ToolContext) -> list[ToolSpec]:
    specs = list(specs)
    ids = [s.id for s in specs]
    groups = {group_of(i) for i in ids}
    expected = {c for c in CANONICAL_IDS if group_of(c) in groups}
    assert len(ids) == len(set(ids)) and set(ids) == expected, (
        f"registered ids differ from the canonical set for {sorted(groups)}: "
        f"missing {sorted(expected - set(ids))}, unexpected {sorted(set(ids) - expected)}")
    for spec in specs:
        name = wire_name(spec.id)
        mcp.tool(name=name, title=spec.id, description=spec.description,
                 annotations=annotations_for(spec))(wrap(spec, ctx))
        # FastMCP's argument model ignores unknown fields; route every argument
        # to wrap() so the input model's extra="forbid" is enforced.
        mcp._tool_manager.get_tool(name).fn_metadata.arg_model = _RawArguments
    return specs

# core/api/xsiam.py
"""XSIAM live-tenant operations router (decision B).

Tenant CRUD lives in the generic /api/credentials/integrations endpoints
(kind="xsiam_tenant"). This router only does things that require talking to the
live tenant: liveness, health, ingestion metrics, XQL, the operation catalog,
and the general issue, case and endpoint reads.
"""
from __future__ import annotations

import contextlib
from datetime import datetime
from typing import Any, Callable, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import get_db
from security import CredentialStore
from integrations.xsiam.client import (
    inspect_operation_body, is_xql_start_path, refuse_xql_write_stages,
)
from integrations.xsiam.loader import XSIAM_KIND, load_xsiam_client
from integrations.xsiam.queries import (
    INGESTION_HEALTH_XQL, case_search_body, endpoint_get_body, issue_search_body,
    shape_ingestion_results,
)
from integrations.xsiam.exceptions import (
    XsiamApiError, XsiamConfigError, XsiamError, XsiamParseError, XsiamQuotaError,
)
from integrations.xsiam import ledger as ledger_mod
from integrations.xsiam.guards import (
    XQL_OPERATION_IDS, XQL_POLL_OPERATION_IDS, ledger_key_for,
    refuse_xql_write_stage, require_xql_capability, tenant_budget, xql_query_text,
)
from integrations.xsiam.operations.catalog import catalog as op_catalog

router = APIRouter(prefix="/xsiam", tags=["xsiam"])

_DEFAULT_TIMEFRAME = {"relativeTime": 24 * 60 * 60 * 1000}  # last 24h


_START_XQL_OP_ID = "OP-XQL-START-XQL-QUERY"


@contextlib.asynccontextmanager
async def _budgeted(client: Any):
    """guards.tenant_budget, with ledger refusals rendered as a structured 429.

    ``ledger.charge`` raises RuntimeError subclasses that the global XsiamError
    handler does not know; without this they would surface as a 500. Raised
    before the body runs, so a refused budget never reaches the tenant.
    """
    try:
        async with tenant_budget(client):
            yield
    except (ledger_mod.QuotaBreakerOpen, ledger_mod.DailyBudgetExhausted) as exc:
        raise XsiamQuotaError(
            f"CortexSim's own budget refused this request before it was sent to "
            f"the tenant: {exc}", local=True,
            retry_after_seconds=(max(1, int(exc.retry_after_seconds))
                                 if hasattr(exc, "retry_after_seconds") else None),
        ) from exc


def _ledger_snapshot(client: Any) -> dict[str, Any]:
    from connectors import tuning  # noqa: PLC0415
    return ledger_mod.ledger.snapshot(
        ledger_key_for(client), limit=tuning.xsiam_max_queries_per_day())


class XqlRequest(BaseModel):
    query: str = Field(..., min_length=1)
    timeframe: dict[str, Any] = Field(default_factory=lambda: dict(_DEFAULT_TIMEFRAME))


@router.post("/tenants/{name}/test")
async def test_tenant(name: str, session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    store = CredentialStore(session)
    client = await load_xsiam_client(session, name)
    try:
        try:
            status = await client.healthcheck()
        except XsiamApiError as exc:
            if exc.upstream_status == 403:  # healthcheck is license-gated
                count = await client.ping_via_endpoints()
                status = {"status": "reachable (healthcheck license-gated)",
                          "endpoint_sample": count}
            else:
                raise
    except XsiamError as exc:
        # A refusal by CortexSim's own budget says nothing about the credential.
        if not getattr(exc, "local", False):
            await store.mark_integration_verified(name, ok=False, error=exc.detail)
            await session.commit()
        raise
    await store.mark_integration_verified(name, ok=True)
    await session.commit()
    return {"ok": True, "status": status}


@router.get("/tenants/{name}/health")
async def tenant_health(name: str, session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    store = CredentialStore(session)
    row = await store.get_integration(name)
    if row is None or row.kind != XSIAM_KIND:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "XSIAM tenant not found",
                "code": "TENANT_NOT_FOUND",
                "detail": f"name='{name}'",
            },
        )
    return {
        "name": name,
        "last_verified_ok": row.last_verified_ok,
        "last_verified_at": row.last_verified_at.isoformat() if row.last_verified_at else None,
        "last_verified_error": row.last_verified_error,
    }


@router.get("/tenants/{name}/metrics")
async def tenant_metrics(name: str, session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    client = await load_xsiam_client(session, name)
    require_xql_capability(getattr(client, "capabilities", None), tenant=name)
    async with _budgeted(client):
        reply = await client.run_xql(INGESTION_HEALTH_XQL, dict(_DEFAULT_TIMEFRAME))
    return {"sources": shape_ingestion_results(reply),
            "remaining_quota": reply.get("remaining_quota"),
            "queries_issued": 1,
            "ledger": _ledger_snapshot(client)}


@router.post("/tenants/{name}/xql")
async def start_xql(name: str, body: XqlRequest,
                    session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    # SEC-05: refuse a write stage BEFORE the credential is decrypted. The
    # client enforces the same guard again at send time — this is the same
    # function, called early, not a second implementation.
    refuse_xql_write_stages(body.query)
    client = await load_xsiam_client(session, name)
    require_xql_capability(getattr(client, "capabilities", None), tenant=name)
    refuse_xql_write_stage(body.query)
    async with _budgeted(client):
        query_id = await client.start_xql_query(body.query, body.timeframe)
    return {"query_id": query_id, "queries_issued": 1,
            "ledger": _ledger_snapshot(client)}


@router.get("/tenants/{name}/xql/{query_id}")
async def get_xql(name: str, query_id: str,
                  limit: int = Query(100, ge=1, le=1000),
                  session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    client = await load_xsiam_client(session, name)
    require_xql_capability(getattr(client, "capabilities", None), tenant=name)
    reply = await client.get_query_results(query_id, limit=limit)
    return {"status": reply.get("status"),
            "results": reply.get("results"),
            "remaining_quota": reply.get("remaining_quota"),
            "queries_issued": 0,  # polling a started query is not metered
            "ledger": _ledger_snapshot(client)}


# ── API operation catalog + gated executor ──────────────────────────────────
# The harness turns the declarative operation catalog into callable endpoints.
# Reads run against a configured tenant; write/destructive ops are gated by a
# global off-switch (settings) AND per-request consent, and default to dry-run.


class OperationExecuteRequest(BaseModel):
    path_params: dict[str, Any] = Field(default_factory=dict)
    query: dict[str, Any] = Field(default_factory=dict)
    body: Optional[dict[str, Any]] = None
    # dry_run None → live for reads, preview for write/destructive (safe default).
    dry_run: Optional[bool] = None
    consent: dict[str, bool] = Field(default_factory=dict)


def _op_or_404(op_id: str):
    op = op_catalog.find(op_id)
    if op is None:
        raise HTTPException(
            status_code=404,
            detail={"error": f"XSIAM operation '{op_id}' not found",
                    "code": "XSIAM_OP_NOT_FOUND", "detail": op_id},
        )
    return op


def _resolve_path(op, path_params: dict[str, Any]) -> str:
    """Substitute path params, each as exactly ONE encoded path segment.

    The catalog path is what selects the operation — and therefore which gate
    (read / write / destructive) applies. A raw substitution let the param
    rewrite it: httpx resolves dot-segments, so ``group_id="../delete/123"`` on
    the write-gated ``asset-groups/update/{group_id}`` sent
    ``POST asset-groups/delete/123`` on write consent alone, skipping the
    destructive gate; ``?``/``#`` could smuggle a query. A value that is a
    dot-segment or contains a path separator is refused (an ID never is one);
    everything else is percent-encoded so it cannot leave its segment.

    A param the catalog declares in ``path_param_allowlist`` must be one of the
    listed values exactly. This runs before the tenant client is built, for a
    dry run too, so a refused value never reaches the tenant.
    """
    path = op.path
    for p in op.path_params:
        val = path_params.get(p)
        if val in (None, ""):
            raise XsiamConfigError(f"operation {op.op_id} requires path param '{p}'")
        raw = str(val)
        if raw in (".", "..") or "/" in raw or "\\" in raw:
            raise XsiamConfigError(
                f"operation {op.op_id} path param '{p}' must be a single path "
                f"segment (no '/', '\\', '.' or '..'); got {raw!r}")
        allowed = op.path_param_allowlist.get(p)
        if allowed is not None and raw not in allowed:
            raise XsiamConfigError(
                f"operation {op.op_id} path param '{p}' must be one of "
                f"{', '.join(allowed)}; got {raw!r}")
        path = path.replace("{" + p + "}", quote(raw, safe=""))
    return path


def _effective_dry_run(op, dry_run: Optional[bool], consent: dict[str, bool]) -> bool:
    """Decide whether this call is a preview, enforcing the write/destructive gate.

    Reads: live unless dry_run is explicitly True.
    Write/destructive: preview (dry-run) unless explicitly dry_run=False AND both
    the global flag and the matching consent key are set — otherwise blocked.
    """
    if op.access_class == "read":
        return bool(dry_run)
    if dry_run is None or dry_run is True:
        return True  # never mutate implicitly
    if op.access_class == "write":
        if not (settings.CORTEXSIM_XSIAM_ALLOW_WRITE and consent.get("write_authorized")):
            raise XsiamConfigError(
                "write operation blocked: set CORTEXSIM_XSIAM_ALLOW_WRITE=1 and "
                "consent.write_authorized=true, or keep dry_run to preview")
    else:  # destructive
        if not (settings.CORTEXSIM_XSIAM_ALLOW_DESTRUCTIVE and consent.get("destructive_authorized")):
            raise XsiamConfigError(
                "destructive operation blocked: set CORTEXSIM_XSIAM_ALLOW_DESTRUCTIVE=1 "
                "and consent.destructive_authorized=true, or keep dry_run to preview")
    return False


@router.get("/operations")
async def list_operations(
    category: Optional[str] = Query(None, description="filter by category"),
    access_class: Optional[str] = Query(None, description="read | write | destructive"),
) -> dict[str, Any]:
    ops = op_catalog.all()
    if category:
        ops = [o for o in ops if o.category == category]
    if access_class:
        ops = [o for o in ops if o.access_class == access_class]
    return {
        "count": len(ops),
        "categories": op_catalog.categories(),
        "operations": [o.summary() for o in ops],
    }


@router.get("/operations/{op_id}")
async def get_operation(op_id: str) -> dict[str, Any]:
    return _op_or_404(op_id).model_dump()


@router.post("/tenants/{name}/operations/{op_id}")
async def execute_operation(
    name: str, op_id: str, body: OperationExecuteRequest,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    op = _op_or_404(op_id)
    path = _resolve_path(op, body.path_params)
    dry = _effective_dry_run(op, body.dry_run, body.consent)

    payload = body.body
    if op.method == "POST" and payload is None:
        # Minimal envelope most POST ops expect. A few (BYOS ingest) reject the
        # wrapper; they say so with a tag, so the preview shows what would be sent.
        payload = {} if "no-request-data-wrapper" in op.tags else {"request_data": {}}
    if "body-inspected" in op.tags or is_xql_start_path(path):
        # SEC-05: an op whose effect depends on its BODY (XQL `target` writes)
        # cannot be gated by access_class alone. Inspected for dry-run too: a
        # preview of a request SimCore would refuse is a misleading preview.
        inspect_operation_body(path, payload)
    request_echo = {
        "method": op.method, "path": path,
        "body": payload, "query": body.query or None,
    }
    if dry:
        return {"dry_run": True, "op_id": op.op_id,
                "access_class": op.access_class, "request": request_echo,
                "queries_issued": 0, "ledger": None}

    client = await load_xsiam_client(session, name)
    queries_issued = 1
    if op.op_id in XQL_OPERATION_IDS:
        require_xql_capability(getattr(client, "capabilities", None), tenant=name)
        if op.op_id == _START_XQL_OP_ID:
            refuse_xql_write_stage(xql_query_text(payload))
            async with _budgeted(client):
                reply = await client.request(
                    op.method, path, json=payload, params=(body.query or None))
        else:
            if op.op_id in XQL_POLL_OPERATION_IDS:
                queries_issued = 0  # polling a started query is not metered
            reply = await client.request(
                op.method, path, json=payload, params=(body.query or None))
    else:
        reply = await client.request(
            op.method, path, json=payload, params=(body.query or None))
    return {"dry_run": False, "op_id": op.op_id,
            "access_class": op.access_class, "reply": reply,
            "queries_issued": queries_issued,
            "ledger": _ledger_snapshot(client)}


# ── General tenant reads: issues, cases, endpoints (tenant-reader spec §7.3, §7.4)
#
# Three per-tenant reads, for MCP parity. They run the catalog ops
# (OP-ISSUE-SEARCH, OP-CASE-SEARCH, OP-ENDPOINTS-GET-ENDPOINT) on the catalog
# client, whose `request` charges the per-tenant request bucket and turns a local
# refusal into a 429 that names CortexSim's own budget. Only an `xsiam_tenant`
# credential serves them: configuring reconcile's `xsiam` kind does not authorise
# tenant-wide search, as it does not authorise XQL.
#
# One substrate with the run-scoped reader: request bodies come from the shared
# builders in integrations/xsiam/queries.py, replies are read by the connector's
# own envelope reader (connectors.xsiam.parse_reply, strict `reply.DATA` for
# issues and cases), and rows are projected by connectors.tenant_reader, so tenant
# text reaches a caller only under `tenant_text`, beside `data_notice`.

_READ_PAGE = 100
#: Newest first. An explicit sort is what gives `offset` a meaning.
_ISSUE_SORT = {"field": "observation_time", "keyword": "desc"}


class IssueSearchRequest(BaseModel):
    since: Optional[datetime] = Field(None, description="_insert_time lower bound")
    until: Optional[datetime] = Field(None, description="_insert_time upper bound")
    ids: list[str] = Field(default_factory=list)
    methods: list[str] = Field(default_factory=list, description=(
        "detection.method filter display names (e.g. 'PAN NGFW'), sent verbatim"))
    severities: list[str] = Field(default_factory=list)
    limit: int = Field(50, ge=1, le=200)
    offset: int = Field(0, ge=0)


class CaseSearchRequest(BaseModel):
    ids: list[str] = Field(default_factory=list)
    since: Optional[datetime] = Field(None, description="creation_time lower bound")
    until: Optional[datetime] = Field(None, description="creation_time upper bound")
    limit: int = Field(50, ge=1, le=200)
    offset: int = Field(0, ge=0)


class EndpointGetRequest(BaseModel):
    hostnames: list[str] = Field(..., min_length=1, max_length=100)


async def _no_tenant_credential(session: AsyncSession, name: str,
                                rows_key: str) -> Optional[dict[str, Any]]:
    """The ``pending`` reply when ``name`` is not an ``xsiam_tenant`` credential,
    else None. Read off the vault row, so no client is loaded and no request is
    sent; a reconcile-only ``xsiam`` credential is named as not enough."""
    from connectors.tenant_reader import DATA_NOTICE  # noqa: PLC0415

    row = await CredentialStore(session).get_integration(name)
    if row is not None and row.kind == XSIAM_KIND:
        return None
    if row is None:
        why = f"no credential is named '{name}'"
    elif row.kind == "xsiam":
        why = (f"'{name}' is an 'xsiam' credential (reconcile's alert read-back), which "
               "does not authorise tenant-wide reads")
    else:
        why = f"'{name}' is a '{row.kind}' credential"
    return {"status": "pending", "code": "NO_TENANT_CONFIGURED",
            "detail": (f"{why}; this read needs an {XSIAM_KIND} credential named '{name}' "
                       "(configure one under /api/credentials/integrations). Nothing was "
                       "read from a tenant"),
            "tenant": name, rows_key: None, "total_count": None, "filter_count": None,
            "truncated": None, "unrecognised_values": None, "excluded_non_security": None,
            "queries_issued": 0, "data_notice": DATA_NOTICE, "ledger": None}


def _window_filters(field: str, since: Optional[datetime],
                    until: Optional[datetime]) -> list[dict]:
    from connectors.xsiam import _epoch_ms_utc  # noqa: PLC0415

    out = []
    if since is not None:
        out.append({"field": field, "operator": "gte", "value": _epoch_ms_utc(since)})
    if until is not None:
        out.append({"field": field, "operator": "lte", "value": _epoch_ms_utc(until)})
    return out


def _id_values(ids: list[str]) -> list[Any]:
    """Stripped, de-duplicated ids; ASCII-digit ids go out as ints, as the
    connector's by-id issue read and the run reader's case read send them."""
    clean = dict.fromkeys(s.strip() for s in ids if s.strip())
    return [int(i) if i.isascii() and i.isdigit() else i for i in clean]


def _read_envelope(reply: Any, list_key: str, *,
                   strict: bool) -> tuple[list[dict], dict[str, Any]]:
    """Rows and counts from one reply, through the connector's envelope reader.
    The catalog client has already removed the outer ``reply`` key, so it is put
    back for the reader. An envelope it does not recognise is
    :class:`XsiamParseError` (502), never zero rows."""
    from connectors.base import ConnectorError  # noqa: PLC0415
    from connectors.xsiam import parse_reply  # noqa: PLC0415

    try:
        rows, _dropped, meta = parse_reply({"reply": reply}, list_key, strict=strict)
    except ConnectorError as exc:
        raise XsiamParseError(str(exc)) from exc
    return rows, meta


async def _paged_read(client: Any, op: Any, body_for: Callable[[int, int], dict], *,
                      limit: int, offset: int, what: str,
                      ) -> tuple[list[dict], dict[str, Any], int]:
    """``(raw rows, counts, requests sent)``: up to ``limit`` rows from
    ``offset``, in pages of 100, strict ``reply.DATA``.

    Stops at ``limit``, at a short page, or once ``FILTER_COUNT`` is reached.
    Counts are the last value the tenant sent (a capped one stays capped). A
    local budget refusal after the first page is re-raised as a 429 that says
    which page was refused and that the rows already read are not returned: a
    cut read is never handed back as a complete result.
    """
    rows: list[dict] = []
    counts: dict[str, Any] = {"total_count": None, "total_count_capped": False,
                              "filter_count": None, "filter_count_capped": False}
    pages = 0
    search_from = offset
    while len(rows) < limit:
        size = min(_READ_PAGE, limit - len(rows))
        try:
            reply = await client.request(op.method, op.path,
                                         json=body_for(search_from, search_from + size))
        except XsiamQuotaError as exc:
            if pages and exc.local:
                n = max(1, int(exc.retry_after_seconds or 1))
                raise XsiamQuotaError(
                    f"CortexSim's own per-tenant request budget refused page {pages + 1} of "
                    f"this {what} before it was sent to the tenant. The {pages} page(s) "
                    "before it were read, and their rows are not returned, because a cut "
                    f"read is not a complete result. This says nothing about the tenant; "
                    f"retry in {n}s", local=True, retry_after_seconds=n) from exc
            raise
        pages += 1
        page_rows, meta = _read_envelope(reply, "DATA", strict=True)
        for key in ("total_count", "filter_count"):
            if meta.get(key) is not None:
                counts[key] = meta[key]
            counts[f"{key}_capped"] = counts[f"{key}_capped"] or bool(meta.get(f"{key}_capped"))
        rows.extend(page_rows)
        search_from += len(page_rows)
        if len(page_rows) < size:
            break                                       # short page: the end
        if (counts["filter_count"] is not None and not counts["filter_count_capped"]
                and search_from >= counts["filter_count"]):
            break                                       # everything accounted for
    return rows[:limit], counts, pages


def _count(counts: dict[str, Any], key: str) -> Optional[int]:
    """A tenant count, or null when it was not sent or was capped (``9,999+``):
    a capped count is a floor, not a number."""
    return None if counts.get(f"{key}_capped") else counts.get(key)


def _truncated(counts: dict[str, Any], read_to: int) -> Optional[bool]:
    """Whether the tenant holds matching rows past the last one returned
    (``read_to`` = offset + rows read). Null when ``FILTER_COUNT`` is unknown,
    never False; true when it is capped."""
    if counts.get("filter_count_capped"):
        return True
    fc = counts.get("filter_count")
    return None if fc is None else fc > read_to


def _rows_reply(name: str, rows_key: str, rows: list[dict], client: Any, *,
                total_count: Optional[int], filter_count: Optional[int],
                truncated: Optional[bool], unrecognised: Optional[dict],
                excluded: Optional[int], queries: int) -> dict[str, Any]:
    from connectors.tenant_reader import DATA_NOTICE  # noqa: PLC0415

    return {"tenant": name, rows_key: rows, "total_count": total_count,
            "filter_count": filter_count, "truncated": truncated,
            "unrecognised_values": unrecognised, "excluded_non_security": excluded,
            "queries_issued": queries, "data_notice": DATA_NOTICE,
            "ledger": _ledger_snapshot(client)}


@router.post("/tenants/{name}/issues/search")
async def search_tenant_issues(name: str, body: IssueSearchRequest,
                               session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Security-domain issues on one tenant (``OP-ISSUE-SEARCH``), newest first.

    Filters: ``_insert_time`` window, ids, ``detection.method`` display names and
    severities, plus ``issue_domain`` in ``["Security"]`` always; the host fields
    are always asked for. A row outside the Security domain is excluded and
    counted. ``xsiam_tenant`` credential only.
    """
    from connectors.tenant_evidence import ISSUE_DOMAIN, _project_window  # noqa: PLC0415
    from connectors.tenant_reader import (  # noqa: PLC0415
        ISSUE_INCLUDE_FIELDS, IssueShapeError, unrecognised_values,
    )

    pending = await _no_tenant_credential(session, name, "issues")
    if pending is not None:
        return pending
    filters = _window_filters("_insert_time", body.since, body.until)
    if body.ids:
        filters.append({"field": "id", "operator": "in", "value": _id_values(body.ids)})
    if body.methods:
        filters.append({"field": "detection.method", "operator": "in",
                        "value": list(body.methods)})
    if body.severities:
        filters.append({"field": "severity", "operator": "in", "value": list(body.severities)})
    filters.append({"field": "issue_domain", "operator": "in", "value": [ISSUE_DOMAIN]})

    def body_for(search_from: int, search_to: int) -> dict:
        return issue_search_body(filters=filters, search_from=search_from,
                                 search_to=search_to, sort=_ISSUE_SORT,
                                 include_fields=list(ISSUE_INCLUDE_FIELDS))

    op = _op_or_404("OP-ISSUE-SEARCH")
    client = await load_xsiam_client(session, name)
    rows, counts, pages = await _paged_read(client, op, body_for,
                                            limit=body.limit, offset=body.offset,
                                            what="issue search")
    try:
        issues, excluded, _duplicates = _project_window(rows)
    except IssueShapeError as exc:
        raise XsiamParseError(str(exc)) from exc
    return _rows_reply(name, "issues", issues, client,
                       total_count=_count(counts, "total_count"),
                       filter_count=_count(counts, "filter_count"),
                       truncated=_truncated(counts, body.offset + len(rows)),
                       unrecognised=unrecognised_values(issues), excluded=excluded,
                       queries=pages)


@router.post("/tenants/{name}/cases/search")
async def search_tenant_cases(name: str, body: CaseSearchRequest,
                              session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Cases on one tenant (``OP-CASE-SEARCH``), by id and/or ``creation_time``
    window. ``xsiam_tenant`` credential only."""
    from connectors.tenant_reader import IssueShapeError, project_case  # noqa: PLC0415

    pending = await _no_tenant_credential(session, name, "cases")
    if pending is not None:
        return pending
    filters = _window_filters("creation_time", body.since, body.until)
    if body.ids:
        filters.append({"field": "case_id", "operator": "in", "value": _id_values(body.ids)})

    def body_for(search_from: int, search_to: int) -> dict:
        return case_search_body(filters=filters, search_from=search_from, search_to=search_to)

    op = _op_or_404("OP-CASE-SEARCH")
    client = await load_xsiam_client(session, name)
    rows, counts, pages = await _paged_read(client, op, body_for,
                                            limit=body.limit, offset=body.offset,
                                            what="case search")
    try:
        cases = [project_case(r) for r in rows]
    except IssueShapeError as exc:
        raise XsiamParseError(str(exc)) from exc
    return _rows_reply(name, "cases", cases, client,
                       total_count=_count(counts, "total_count"),
                       filter_count=_count(counts, "filter_count"),
                       truncated=_truncated(counts, body.offset + len(rows)),
                       unrecognised=None, excluded=None, queries=pages)


@router.post("/tenants/{name}/endpoints/get")
async def get_tenant_endpoints(name: str, body: EndpointGetRequest,
                               session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Endpoint records by hostname on one tenant (``OP-ENDPOINTS-GET-ENDPOINT``),
    one request, at most 100 records, as of now. ``xsiam_tenant`` credential only."""
    from connectors.tenant_reader import IssueShapeError, project_endpoint  # noqa: PLC0415

    pending = await _no_tenant_credential(session, name, "endpoints")
    if pending is not None:
        return pending
    op = _op_or_404("OP-ENDPOINTS-GET-ENDPOINT")
    client = await load_xsiam_client(session, name)
    reply = await client.request(op.method, op.path, json=endpoint_get_body(body.hostnames))
    rows, meta = _read_envelope(reply, "endpoints", strict=False)
    try:
        records = [project_endpoint(r) for r in rows]
    except IssueShapeError as exc:
        raise XsiamParseError(str(exc)) from exc
    total = meta.get("total_count")
    if meta.get("total_count_capped"):
        truncated: Optional[bool] = True
    elif total is not None:
        truncated = total > len(rows)
    else:
        # No count: a full page may have left records behind; a short one did not.
        truncated = None if len(rows) >= _READ_PAGE else False
    return _rows_reply(name, "endpoints", records, client,
                       total_count=_count(meta, "total_count"), filter_count=None,
                       truncated=truncated, unrecognised=None, excluded=None, queries=1)

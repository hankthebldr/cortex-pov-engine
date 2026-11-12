"""The ``xsiam.*`` group: 16 tools projecting SimCore's XSIAM integration (spec 5.3).

Every tenant call goes through SimCore's own routes (``/api/xsiam/...``); this
module never talks to a tenant. Three rules hold everywhere:

* **Writes only preview** (spec 6). :func:`execute` looks the operation's
  ``access_class`` up in SimCore's catalog and, for ``write``/``destructive``,
  always posts ``dry_run: true`` with an empty ``consent``. ``dry_run: false``
  on such an op fetches the preview and raises ``MCP_WRITE_NOT_EXECUTABLE``
  with it attached; nothing is sent to the tenant. An access class the gate
  does not know is refused, never read as ``read``.
* **Counts are SimCore's.** ``queries_issued`` is read off each route's body
  and summed (an XQL query is its start's 1 plus each poll's 0); a missing
  field is ``SIMCORE_SHAPE_UNKNOWN``, an unknown count is ``None``.
* **Named tools return allowlisted fields only.** A reply shape a shaper does
  not recognise raises ``XSIAM_PARSE_ERROR``; it is never read as empty. Only
  ``xsiam.operation.execute`` returns the upstream ``reply``, as capped JSON
  text with ``truncated`` + ``bytes``.

The REST routes own every gate (capability, XQL write stage, traversal,
budget); their codes pass through unchanged (D-1).
"""
from __future__ import annotations

import asyncio
import json
import shlex
import time
from typing import Any, NamedTuple, Optional

from integrations.xsiam.codes import XSIAM_PARSE_ERROR, XSIAM_QUERY_ERROR, remediation_for
from integrations.xsiam.exceptions import XsiamError
from integrations.xsiam.queries import result_rows, row_count

from ..client import SimCoreShapeUnknown, encode_segment
from ..envelope import Envelope, _iso_utc, ledger_from_snapshot
from ..errors import MCP_WRITE_NOT_EXECUTABLE, REMEDIATION, TOOL_NOT_BACKED, McpToolError
from ..registry import (
    TenantCallError,
    ToolContext,
    ToolSpec,
    cap_list,
    cap_text,
    evidence_for_simcore,
    evidence_for_tenant,
    readiness,
    resolve_tenant,
    take_token,
    tenant_call,
)
from ..schemas import (
    DatasetSchemaIn,
    EmptyInput,
    ListIn,
    OperationExecuteIn,
    OperationListIn,
    TenantIn,
    XqlQueryIn,
)
from .cortexsim import _int, _params, _require

TENANT_KIND = "xsiam_tenant"
XQL_RESULT_PENDING = "XQL_RESULT_PENDING"
POPULATION_EMPTY = "POPULATION_EMPTY"

_ACCESS = frozenset({"read", "write", "destructive"})
#: access_class -> (the global flag a human sets, the consent key a human sets)
_CONSENT = {
    "write": ("CORTEXSIM_XSIAM_ALLOW_WRITE=1", "write_authorized"),
    "destructive": ("CORTEXSIM_XSIAM_ALLOW_DESTRUCTIVE=1", "destructive_authorized"),
}
_XQL_DONE = frozenset({"SUCCESS", "PARTIAL_SUCCESS"})
_LICENSE_GATED_STATUS = "reachable (healthcheck license-gated)"

#: Poll pacing; module attributes so tests can swap in a fake clock.
_sleep = asyncio.sleep
_clock = time.monotonic


class OpIdIn(EmptyInput):
    op_id: str


class TenantListIn(TenantIn, ListIn):
    pass


# --- the write gate -----------------------------------------------------------
class Executed(NamedTuple):
    tenant: str
    op: dict
    body: dict  # the route body (live) or the preview_with_consent dict (preview)
    queries_issued: int
    ledger: Optional[dict]
    preview: bool
    #: True when the tenant's reply came back (the live path); a preview is False.
    answered: bool = False


async def _operation(ctx: ToolContext, op_id: str) -> dict:
    """The catalog entry; an access class outside read|write|destructive is refused."""
    where = f"GET /api/xsiam/operations/{op_id}"
    op = _require(await ctx.client.get(f"/api/xsiam/operations/{encode_segment(op_id)}"),
                  where, "op_id", "access_class", "method", "path")
    if op["access_class"] not in _ACCESS:
        raise SimCoreShapeUnknown(
            200, f"{where}: access_class {op['access_class']!r} is not read|write|destructive; "
            "refused rather than treated as read")
    return op


def _op_path(tenant: str, op_id: str) -> str:
    return f"/api/xsiam/tenants/{encode_segment(tenant)}/operations/{encode_segment(op_id)}"


def preview_with_consent(preview: dict, op: dict, tenant: str, *, sent: dict, base_url: str) -> dict:
    """The section 6 preview: what would be sent, what a human must set, and the
    exact request a human runs. ``sent`` is the JSON this layer posted."""
    access = op["access_class"]
    data = {k: preview[k] for k in ("dry_run", "op_id", "access_class", "request")}
    consent: dict[str, bool] = {}
    if access in _CONSENT:
        flag, key = _CONSENT[access]
        consent = {key: True}
        data["consent_required"] = {"flag": flag, "consent": consent, "dry_run": False}
    else:
        data["consent_required"] = None  # a read previews by choice; it needs no consent
    human = {**sent, "dry_run": False, "consent": consent}
    data["execute_as_human"] = (
        f"curl -X POST {base_url}{_op_path(tenant, op['op_id'])} "
        f"-H 'content-type: application/json' -d {shlex.quote(json.dumps(human, sort_keys=True))}")
    return data


async def execute(ctx: ToolContext, *, tenant: Optional[str], op_id: str, path_params: dict,
                  query: Optional[dict], body: Any, dry_run: Optional[bool]) -> Executed:
    """``POST /api/xsiam/tenants/{t}/operations/{op_id}`` — always ``consent: {}``.

    write/destructive (or any ``dry_run: true``) → one preview request, never a
    tenant call; ``dry_run: false`` on write/destructive → that preview, then
    ``MCP_WRITE_NOT_EXECUTABLE``. A read with ``dry_run`` unset or false → live,
    rate-limited, through :func:`tenant_call`.
    """
    tenant = await resolve_tenant(ctx, tenant, kind=TENANT_KIND, must_exist=True)
    op = await _operation(ctx, op_id)
    access = op["access_class"]
    path = _op_path(tenant, op["op_id"])
    where = f"POST {path}"
    sent = {"path_params": path_params, "query": query or {}, "body": body}

    if access != "read" or dry_run is True:
        # Not a tenant call: the route answers a preview from the catalog alone.
        route = _require(await ctx.client.post(path, json={**sent, "dry_run": True, "consent": {}}),
                         where, "dry_run", "op_id", "access_class", "request")
        if route["dry_run"] is not True or route["access_class"] != access:
            raise SimCoreShapeUnknown(
                200, f"{where}: asked for a preview of a {access} op, got dry_run={route['dry_run']!r} "
                f"access_class={route['access_class']!r}")
        queries = _int(route, where, "queries_issued")
        preview = preview_with_consent(route, op, tenant, sent=sent, base_url=ctx.client.base_url)
        if dry_run is False:
            err = McpToolError(
                MCP_WRITE_NOT_EXECUTABLE, f"{op['op_id']} is a {access} operation; MCP only previews it",
                "dry_run: false was refused before any request for the tenant was built; "
                "the preview is attached and nothing was sent",
                remediation=REMEDIATION[MCP_WRITE_NOT_EXECUTABLE], basis="preview", tenant=tenant,
                data=preview)
            err.queries_issued = queries  # read off the preview: 0
            raise err
        return Executed(tenant, op, preview, queries, route.get("ledger"), True)

    take_token(ctx)

    def read(b: dict) -> int:
        _require(b, where, "dry_run", "reply", "ledger")
        if b["dry_run"] is not False:
            raise SimCoreShapeUnknown(200, f"{where}: a live read came back dry_run={b['dry_run']!r}")
        return _int(b, where, "queries_issued")

    # dry_run is sent unset: the route reads it as live for a read and as a
    # preview for anything else, so a catalog that changed under us cannot write.
    route, queries = await tenant_call(
        ctx, tenant=tenant, read=read,
        coro=ctx.client.post(path, json={**sent, "dry_run": None, "consent": {}}))
    return Executed(tenant, op, route, queries, route["ledger"], False, answered=True)


def _parse_error(ex: Executed, where: str, detail: str) -> TenantCallError:
    return TenantCallError(
        XSIAM_PARSE_ERROR, f"{where}: unrecognised reply shape", detail, tenant=ex.tenant,
        queries_issued=ex.queries_issued, ledger_snapshot=ex.ledger,
        remediation=remediation_for(XSIAM_PARSE_ERROR))


async def _tenant_env(ctx: ToolContext, data: Any, *, tenant: str, queries: Optional[int],
                      ledger: Optional[dict], truncated: bool = False,
                      basis: Optional[str] = None, answered: bool = False) -> Envelope:
    ev = await evidence_for_tenant(ctx, tenant=tenant, queries_issued=queries,
                                   ledger_snapshot=ledger, basis=basis, tenant_answered=answered)
    return Envelope(data=data, evidence=ev.model_copy(update={"truncated": truncated}))


# --- (a) the generic projection -----------------------------------------------
async def operation_list(ctx: ToolContext, args: OperationListIn) -> Envelope:
    where = "GET /api/xsiam/operations"
    body = _require(await ctx.client.get("/api/xsiam/operations", params=_params(
        category=args.category, access_class=args.access_class)), where, "operations", "categories")
    for op in body["operations"]:
        if not isinstance(op, dict) or op.get("access_class") not in _ACCESS:
            raise SimCoreShapeUnknown(200, f"{where}: operation {op!r} has no read|write|destructive access_class")
    page, truncated = cap_list(body["operations"], limit=args.limit, offset=args.offset)
    data = {"categories": body["categories"], "operations": page,
            "total": len(body["operations"]), "truncated": truncated}
    return Envelope(data=data, evidence=await evidence_for_simcore(ctx, basis="authored", truncated=truncated))


async def operation_get(ctx: ToolContext, args: OpIdIn) -> Envelope:
    return Envelope(data=await _operation(ctx, args.op_id),
                    evidence=await evidence_for_simcore(ctx, basis="authored"))


async def operation_execute(ctx: ToolContext, args: OperationExecuteIn) -> Envelope:
    ex = await execute(ctx, tenant=args.tenant, op_id=args.op_id, path_params=args.path_params,
                       query=args.query, body=args.body, dry_run=args.dry_run)
    if ex.preview:
        return await _tenant_env(ctx, ex.body, tenant=ex.tenant, queries=ex.queries_issued,
                                 ledger=None, basis="preview")
    text, truncated, total = cap_text(json.dumps(ex.body["reply"], sort_keys=True))
    data = {"op_id": ex.op["op_id"], "access_class": ex.op["access_class"], "dry_run": False,
            "reply": text, "truncated": truncated, "bytes": total}
    return await _tenant_env(ctx, data, tenant=ex.tenant, queries=ex.queries_issued,
                             ledger=ex.ledger, truncated=truncated, answered=ex.answered)


# --- (b) named tools: allowlisted shapers over one catalog op -------------------
#: (output field, upstream key, conversion): "ts" → UTC ISO-8601 via coerce_utc, "lower" → lowercase.
_DATASET_FIELDS = (("name", "Dataset Name", None), ("kind", "Type", "lower"),
                   ("log_update_type", "Log Update Type", None), ("last_updated", "Last Updated", "ts"),
                   ("total_days_stored", "Total Days Stored", None),
                   ("total_size_stored", "Total Size Stored", None), ("total_events", "Total Events", None),
                   ("default_query_target", "Default Query Target", None))
_ALERT_FIELDS = (("alert_id", "alert_id", None), ("name", "name", None), ("severity", "severity", None),
                 ("category", "category", None), ("source", "source", None),
                 ("detection_timestamp", "detection_timestamp", "ts"),
                 ("local_insert_ts", "local_insert_ts", "ts"),
                 ("resolution_status", "resolution_status", None), ("case_id", "case_id", None),
                 ("host_name", "host_name", None), ("action", "action", None),
                 ("mitre_tactic_id_and_name", "mitre_tactic_id_and_name", None),
                 ("mitre_technique_id_and_name", "mitre_technique_id_and_name", None))
_INCIDENT_FIELDS = (("incident_id", "incident_id", None), ("incident_name", "incident_name", None),
                    ("status", "status", None), ("severity", "severity", None),
                    ("creation_time", "creation_time", "ts"), ("modification_time", "modification_time", "ts"),
                    ("alert_count", "alert_count", None), ("description", "description", None))
_CORRELATION_FIELDS = (("rule_id", "rule_id", None), ("name", "name", None), ("severity", "severity", None),
                       ("is_enabled", "is_enabled", None), ("execution_mode", "execution_mode", None),
                       ("search_window", "search_window", None), ("dataset", "dataset", None),
                       ("alert_category", "alert_category", None), ("mitre_defs", "mitre_defs", None),
                       ("xql_query", "xql_query", None), ("description", "description", None))
_PLAYBOOK_FIELDS = (("id", "id", None), ("name", "name", None), ("version", "version", None),
                    ("description", "description", None))


class _Shape(NamedTuple):
    op_id: str
    out_key: str              # the list's name in data
    rows_key: Optional[str]   # where the list sits in the reply; None = the reply is the list
    total_key: Optional[str]  # the tenant's own total, when the reply carries one
    required: str             # an upstream key every row must carry
    fields: tuple


_SHAPES = {
    "datasets": _Shape("OP-XQL-GET-DATASETS", "datasets", None, None, "Dataset Name", _DATASET_FIELDS),
    "alerts": _Shape("OP-ALERTS-GET-ALERTS-MULTI-EVENTS", "alerts", "alerts", "total_count", "alert_id",
                     _ALERT_FIELDS),
    "correlation_rules": _Shape("OP-CORRELATIONS-GET", "correlation_rules", "objects", "objects_count",
                                "name", _CORRELATION_FIELDS),
    "incidents": _Shape("OP-INCIDENTS-GET-INCIDENTS", "incidents", "incidents", "total_count",
                        "incident_id", _INCIDENT_FIELDS),
    "playbooks": _Shape("OP-PLAYBOOKS-GET", "playbooks", "objects", "objects_count", "name",
                        _PLAYBOOK_FIELDS),
}


def _project(row: Any, shape: _Shape, ex: Executed, where: str) -> dict:
    if not isinstance(row, dict) or shape.required not in row:
        raise _parse_error(ex, where, f"a {shape.out_key} row has no {shape.required!r}: {str(row)[:200]}")
    out = {}
    for name, key, conv in shape.fields:
        value = row.get(key)
        if conv == "ts" and value is not None:
            value = _iso_utc(value)
        elif conv == "lower" and isinstance(value, str):
            value = value.lower()
        out[name] = value
    return out


async def _named_list(ctx: ToolContext, args: TenantListIn, shape: _Shape) -> Envelope:
    ex = await execute(ctx, tenant=args.tenant, op_id=shape.op_id, path_params={}, query=None,
                       body=None, dry_run=None)
    where, reply = f"{shape.op_id} reply", ex.body["reply"]
    if shape.rows_key is None:
        rows, total = reply, None
    elif isinstance(reply, dict):
        rows, total = reply.get(shape.rows_key), reply.get(shape.total_key)
    else:
        rows, total = None, None
    if not isinstance(rows, list):
        raise _parse_error(ex, where, f"expected a list at {shape.rows_key or 'reply'}, got {str(reply)[:200]}")
    if shape.total_key is None:
        total = len(rows)
    elif isinstance(total, bool) or not isinstance(total, int):
        raise _parse_error(ex, where, f"{shape.total_key} is missing or not an integer "
                                      f"({total!r}); the list's size is unknown, not {len(rows)}")
    page, _ = cap_list(rows, limit=args.limit, offset=args.offset)
    truncated = args.offset + len(page) < total
    data = {shape.out_key: [_project(r, shape, ex, where) for r in page], "total": total,
            "truncated": truncated}
    return await _tenant_env(ctx, data, tenant=ex.tenant, queries=ex.queries_issued,
                             ledger=ex.ledger, truncated=truncated, answered=ex.answered)


async def dataset_list(ctx, args):
    return await _named_list(ctx, args, _SHAPES["datasets"])


async def alert_list(ctx, args):
    return await _named_list(ctx, args, _SHAPES["alerts"])


async def correlation_rule_list(ctx, args):
    return await _named_list(ctx, args, _SHAPES["correlation_rules"])


async def incident_list(ctx, args):
    return await _named_list(ctx, args, _SHAPES["incidents"])


async def playbook_list(ctx, args):
    return await _named_list(ctx, args, _SHAPES["playbooks"])


async def healthcheck(ctx: ToolContext, args: TenantIn) -> Envelope:
    try:
        ex = await execute(ctx, tenant=args.tenant, op_id="OP-HEALTHCHECK", path_params={},
                           query=None, body=None, dry_run=None)
    except TenantCallError as err:
        # The healthcheck endpoint is license-gated: a 403 means the tenant
        # answered and refused this one endpoint, as POST /tenants/{name}/test reads it.
        # Keyed on the route's upstream_status, never on the message text.
        upstream = err.data.get("upstream_status") if isinstance(err.data, dict) else None
        if err.code == "XSIAM_API_ERROR" and upstream == 403 and not isinstance(upstream, bool):
            return await _tenant_env(
                ctx, {"license_gated": True, "status": _LICENSE_GATED_STATUS}, tenant=err.tenant,
                queries=err.queries_issued, ledger=err.ledger_snapshot, basis="reachable",
                answered=True)
        raise
    reply = ex.body["reply"]
    status = reply.get("status") if isinstance(reply, dict) else reply
    if not isinstance(status, str):
        raise _parse_error(ex, "OP-HEALTHCHECK reply", f"no string status in {str(reply)[:200]}")
    return await _tenant_env(ctx, {"license_gated": False, "status": status}, tenant=ex.tenant,
                             queries=ex.queries_issued, ledger=ex.ledger, answered=ex.answered)


# --- XQL: start + poll ---------------------------------------------------------
async def _xql(ctx: ToolContext, *, tenant: Optional[str], query: str, timeframe: dict, limit: int,
               max_wait: float, interval: float) -> tuple[dict, int, Optional[dict], str]:
    """``(data, queries_issued, ledger, tenant)``; the count is the start's plus each poll's."""
    tenant = await resolve_tenant(ctx, tenant, kind=TENANT_KIND, must_exist=True)
    take_token(ctx)
    base = f"/api/xsiam/tenants/{encode_segment(tenant)}/xql"

    def read_start(b: dict) -> tuple[str, int]:
        return (str(_require(b, f"POST {base}", "query_id", "ledger")["query_id"]),
                _int(b, f"POST {base}", "queries_issued"))

    started, (query_id, queries) = await tenant_call(
        ctx, tenant=tenant, read=read_start,
        coro=ctx.client.post(base, json={"query": query, "timeframe": timeframe}))
    ledger = started["ledger"]
    poll_path = f"{base}/{encode_segment(query_id)}"
    out = {"query": query, "timeframe": timeframe, "limit": limit, "query_id": query_id}
    deadline = _clock() + max_wait
    while True:
        def read_poll(b: dict) -> int:
            _require(b, f"GET {poll_path}", "status", "results", "ledger")
            return _int(b, f"GET {poll_path}", "queries_issued")

        poll, polled = await tenant_call(ctx, tenant=tenant, read=read_poll,
                                         coro=ctx.client.get(poll_path, params={"limit": limit}))
        queries += polled
        ledger = poll["ledger"]
        status = poll["status"]
        if status in _XQL_DONE:
            break
        if status != "PENDING":
            raise TenantCallError(
                XSIAM_QUERY_ERROR, f"XQL query {query_id} ended {status!r}",
                f"GET {poll_path} answered status {status!r}; no rows are reported",
                tenant=tenant, queries_issued=queries, ledger_snapshot=ledger,
                remediation=remediation_for(XSIAM_QUERY_ERROR))
        if _clock() >= deadline:
            return ({**out, "status": "PENDING", "reason": XQL_RESULT_PENDING, "rows": None,
                     "row_count": None, "number_of_results": None, "truncated": False,
                     "remaining_quota": poll.get("remaining_quota")}, queries, ledger, tenant)
        await _sleep(interval)
    try:
        rows, matched = result_rows(poll), row_count(poll)
    except XsiamError as exc:
        raise TenantCallError(exc.code, "unrecognised XQL result envelope", str(exc), tenant=tenant,
                              queries_issued=queries, ledger_snapshot=ledger,
                              remediation=remediation_for(exc.code)) from exc
    return ({**out, "status": status, "reason": None, "rows": rows, "row_count": len(rows),
             "number_of_results": matched, "truncated": matched > len(rows),
             "remaining_quota": poll.get("remaining_quota")}, queries, ledger, tenant)


async def xql_query(ctx: ToolContext, args: XqlQueryIn) -> Envelope:
    data, queries, ledger, tenant = await _xql(
        ctx, tenant=args.tenant, query=args.query, timeframe=args.timeframe, limit=args.limit,
        max_wait=args.max_wait_seconds, interval=args.poll_interval_seconds)
    # _xql returns only after the start came back with a query_id: the tenant answered.
    return await _tenant_env(ctx, data, tenant=tenant, queries=queries, ledger=ledger,
                             truncated=data["truncated"], answered=True)


async def dataset_schema(ctx: ToolContext, args: DatasetSchemaIn) -> Envelope:
    # args.dataset is pattern-validated (^[A-Za-z0-9_]{1,128}$) before this line runs.
    query = f"dataset = {args.dataset} | limit 1"
    defaults = XqlQueryIn(query=query)
    xql, queries, ledger, tenant = await _xql(
        ctx, tenant=args.tenant, query=query, timeframe=defaults.timeframe, limit=1,
        max_wait=defaults.max_wait_seconds, interval=defaults.poll_interval_seconds)
    rows = xql["rows"]
    if rows is None:
        fields, reason = None, XQL_RESULT_PENDING
    elif not rows:
        fields, reason = [], POPULATION_EMPTY
    else:
        fields, reason = sorted(rows[0].keys()), None
    data = {"dataset": args.dataset, "fields": fields, "reason": reason, "query_id": xql["query_id"],
            "derivation": f"the column names of the first row returned by `{query}` over "
                          f"{json.dumps(defaults.timeframe)}; a column absent from that row is not listed"}
    return await _tenant_env(ctx, data, tenant=tenant, queries=queries, ledger=ledger, basis="derived",
                             answered=True)


# --- session index (readiness only, no tenant call) ----------------------------
_CAPABILITY_SCOPES = {"read_alerts": "alert.read"}  # "xql" is read off xql_capability_granted
_ROW_KEYS = ("name", "kind", "capabilities", "xql_capability_granted", "ledger",
             "last_verified_ok", "last_verified_at")


def _tenant_row(integ: dict, degradations: list[str]) -> dict:
    integ = _require(integ, "GET /api/readiness integrations[]", *_ROW_KEYS)
    name, caps = integ["name"], integ["capabilities"]
    if not isinstance(caps, list):
        # Absent is the legacy default, as GET /api/connectors reads it.
        degradations.append(f"tenant {name}: config.capabilities is not set; the legacy default "
                            "[\"read_alerts\"] applies")
        caps = ["read_alerts"]
    scopes = set()
    for cap in caps:
        cap = str(cap).lower()
        if cap in _CAPABILITY_SCOPES:
            scopes.add(_CAPABILITY_SCOPES[cap])
        elif cap != "xql":
            degradations.append(f"tenant {name}: capability {cap!r} maps to no MCP scope")
    if integ["xql_capability_granted"] is True:
        scopes.add("xql.execute")
    else:
        degradations.append(f"tenant {name}: \"xql\" is not in config.capabilities, so xsiam.xql.query "
                            "and xsiam.dataset.schema are refused with XSIAM_CAPABILITY_MISSING")
    if integ["last_verified_ok"] is not True:
        degradations.append(f"tenant {name}: configured, never verified reachable "
                            f"(last_verified_ok: {integ['last_verified_ok']!r})")
    ledger = ledger_from_snapshot(integ["ledger"])
    return {"name": name, "kind": integ["kind"], "capabilities": integ["capabilities"],
            "scopes_held": sorted(scopes), "last_verified_ok": integ["last_verified_ok"],
            "last_verified_at": integ["last_verified_at"],
            "ledger": None if ledger is None else ledger.model_dump()}


async def session_index(ctx: ToolContext, args: EmptyInput) -> Envelope:
    body = _require(await readiness(ctx, fresh=True), "GET /api/readiness", "server_time")
    degradations: list[str] = []
    rows = body["integrations"]
    for i in rows:
        if not isinstance(i, dict):
            raise SimCoreShapeUnknown(200, f"GET /api/readiness: integration row {i!r} is not an object")
    tenants = [_tenant_row(i, degradations) for i in rows if i.get("kind") == TENANT_KIND]
    others = [{"name": i.get("name"), "kind": i.get("kind")} for i in rows if i.get("kind") != TENANT_KIND]
    if not tenants:
        degradations.insert(0, f"no {TENANT_KIND} credential is configured: every tenant-class xsiam.* "
                               "tool refuses with NO_TENANT_CONFIGURED")
    elif len(tenants) > 1:
        degradations.insert(0, f"{len(tenants)} {TENANT_KIND} credentials: scopes are per tenant in "
                               "tenants[]; tenant tools need tenant= (TENANT_AMBIGUOUS otherwise)")
    degradations.append("dataset.read and content.read are not represented in config.capabilities, "
                        "so they are not claimed")
    single = tenants[0] if len(tenants) == 1 else None
    data = {"tenant_bound": bool(tenants), "tenant": single["name"] if single else None,
            "scopes_held": single["scopes_held"] if single else [], "tenants": tenants,
            "other_credentials": others, "degradations": degradations,
            "tenant_verified": body["tenant_verified"], "server_time": body["server_time"]}
    return Envelope(data=data, evidence=await evidence_for_simcore(ctx, basis="configured"))


# --- registered, not backed ---------------------------------------------------
_NOT_BACKED = {
    "xsiam.modeling_rule.list": (
        "the XSIAM public API exposes no modeling-rule listing; nothing was queried", None),
    "xsiam.parser.list": ("the XSIAM public API exposes no parser listing; nothing was queried", None),
    "xsiam.content.list": ("the XSIAM public API exposes no content listing; nothing was queried", None),
    "xsiam.ingest.status": (
        "the only candidate query (INGESTION_HEALTH_XQL behind GET /api/xsiam/tenants/{name}/metrics) "
        "is a placeholder that has never been run against a tenant; backing it is tenant-verified "
        "work listed under Henry's decisions; nothing was queried",
        "finalise INGESTION_HEALTH_XQL against a live tenant, then back this tool (spec section 14)"),
}


def _not_backed(tool_id: str):
    detail, remediation = _NOT_BACKED[tool_id]

    async def impl(ctx: ToolContext, args: TenantIn) -> Envelope:
        raise McpToolError(TOOL_NOT_BACKED, f"{tool_id} has no backing operation", detail,
                           remediation=remediation or REMEDIATION[TOOL_NOT_BACKED], basis="authored")
    return impl


# --- the table ----------------------------------------------------------------
_NO_TENANT = "Catalog only; makes no tenant call."
_TENANT = ("Tenant by vault name (kind xsiam_tenant; the only one is the default). Rate-limited. "
           "evidence.queries_issued is SimCore's count.")


def _fields(shape: _Shape) -> str:
    return (f"Returns {shape.out_key}[] capped by limit/offset (default 50, max 200) with total + "
            f"truncated; allowlisted fields only: {', '.join(f[0] for f in shape.fields)}. An "
            f"unrecognised reply is XSIAM_PARSE_ERROR, never an empty list.")


def specs() -> list[ToolSpec]:
    def s(id_, model, impl, klass, network, desc, *, scopes=(), idempotent=True, backed=True):
        return ToolSpec(id=id_, input_model=model, impl=impl, klass=klass, network=network,
                        scopes=tuple(scopes), description=desc, idempotent=idempotent,
                        backed=backed)

    def named(id_, key, scopes):
        shape = _SHAPES[key]
        return s(id_, TenantListIn, _LIST_IMPLS[key], "read", "tenant",
                 f"{shape.op_id} through SimCore's catalog executor. {_fields(shape)} {_TENANT}",
                 scopes=scopes)

    return [
        s("xsiam.operation.list", OperationListIn, operation_list, "read", "none",
          "GET /api/xsiam/operations[?category&access_class]: summaries capped (default 50, max 200) "
          f"with total + truncated, plus categories. {_NO_TENANT}"),
        s("xsiam.operation.get", OpIdIn, operation_get, "read", "none",
          "GET /api/xsiam/operations/{op_id}: the catalog entry (op_id, category, name, method, path, "
          f"access_class, consent, path_params, path_param_allowlist, doc_link, tags). "
          f"{_NO_TENANT}"),
        s("xsiam.operation.execute", OperationExecuteIn, operation_execute, "write", "tenant",
          "POST /api/xsiam/tenants/{tenant}/operations/{op_id}. Takes no consent. A read op runs live "
          "(queries_issued 1; 0 for the OP-XQL-GET-QUERY-RESULTS poll, which still sets "
          "evidence.tenant_answered) and returns the upstream reply as JSON text capped at 256 KiB with "
          "truncated + bytes; dry_run: true previews it. A write or destructive op ALWAYS previews "
          "(queries_issued 0): dry_run, op_id, access_class, request, consent_required, "
          "execute_as_human — a human runs it; dry_run: false on one is MCP_WRITE_NOT_EXECUTABLE "
          "with the preview attached. Path params are single segments (the executor's guard).",
          idempotent=False),
        s("xsiam.session.index", EmptyInput, session_index, "read", "none",
          "From GET /api/readiness only: tenant_bound, tenant, scopes_held (from config.capabilities: "
          "alert.read from read_alerts, xql.execute only when xql is granted — never from kind), "
          "tenants[] (name, kind, capabilities, scopes_held, last_verified_ok, last_verified_at, "
          "ledger), other_credentials[] (name, kind), degradations[], tenant_verified, server_time. "
          "Never credential values. Makes no tenant call."),
        named("xsiam.dataset.list", "datasets", ("dataset.read",)),
        s("xsiam.dataset.schema", DatasetSchemaIn, dataset_schema, "read", "tenant",
          "Derived: one XQL `dataset = <name> | limit 1` (24 h) through xsiam.xql.query's path; fields "
          "= the first row's column names, sorted. Returns dataset, fields, reason, query_id, "
          "derivation. An empty dataset is fields: [] with reason POPULATION_EMPTY, never 'no fields'; "
          "still pending is fields: null with XQL_RESULT_PENDING. The name must match "
          f"^[A-Za-z0-9_]{{1,128}}$. Needs the xql capability. {_TENANT}",
          scopes=("dataset.read", "xql.execute")),
        named("xsiam.alert.list", "alerts", ("alert.read",)),
        named("xsiam.correlation_rule.list", "correlation_rules", ("content.read",)),
        s("xsiam.xql.query", XqlQueryIn, xql_query, "read", "tenant",
          "POST /api/xsiam/tenants/{tenant}/xql, then GET .../xql/{query_id}?limit=N every "
          "poll_interval_seconds until terminal or max_wait_seconds. Returns query, timeframe, limit, "
          "query_id, status, reason, rows, row_count, number_of_results, truncated, remaining_quota; "
          "still running is status PENDING with reason XQL_RESULT_PENDING. queries_issued is 1 (the "
          "polls ride on it); ledger-charged. A `target type = dataset|lookup` stage is refused "
          "(XQL_WRITE_STAGE_REFUSED); rows are capped by limit (<= 1000), never by rewriting the "
          "query; refused with XSIAM_CAPABILITY_MISSING when the credential lacks xql. "
          f"{_TENANT}", scopes=("xql.execute",)),
        named("xsiam.incident.list", "incidents", ("alert.read",)),
        named("xsiam.playbook.list", "playbooks", ("content.read",)),
        s("xsiam.healthcheck", TenantIn, healthcheck, "read", "tenant",
          "OP-HEALTHCHECK through SimCore's catalog executor. Returns license_gated and status only. "
          "A 403 is license_gated: true (reachable, healthcheck license-gated), not unreachable. "
          f"{_TENANT}"),
        *(s(tool, TenantIn, _not_backed(tool), "read", "none",
            f"Registered, not backed: always TOOL_NOT_BACKED ({_NOT_BACKED[tool][0]}), never an "
            "empty list. Makes no call.", backed=False) for tool in _NOT_BACKED),
    ]


_LIST_IMPLS = {"datasets": dataset_list, "alerts": alert_list, "correlation_rules": correlation_rule_list,
               "incidents": incident_list, "playbooks": playbook_list}

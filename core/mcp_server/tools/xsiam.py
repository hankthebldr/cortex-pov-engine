"""The ``xsiam.*`` group: 27 tools projecting SimCore's XSIAM integration (spec 5.3).

Every tenant call goes through SimCore's own routes (``/api/xsiam/...``); this
module never talks to a tenant. Four rules hold everywhere:

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
* **Tenant text is data** (tenant-reader spec §7.7). A result carrying tenant
  rows has ``data["data_notice"] = DATA_NOTICE`` and the tool's description
  repeats it. The tenant-reader tools (``issue.search``, ``case.search``,
  ``endpoint.get``) pass SimCore's projection through, which puts free text
  only under ``tenant_text``; nothing from a row reaches ``statement``,
  ``error`` or ``remediation``. The six parity reads (tenant-reader spec §8.1:
  ``tenant_info.get``, ``audit_log.list``, ``asset.list``, ``asset.get``,
  ``vulnerability.list``, ``assessment_result.list``) project here, the same
  way: allowlisted fields, free text only under ``tenant_text``, every string
  neutralised and capped as the reader does, and a parse error that names reply
  keys, never quotes a tenant value. The two ZIP reads (``playbook.get``,
  ``script.get``) put each archive member's name and text under ``tenant_text``
  only; the text keeps its lines (``neutralise_document``) and is capped by bytes.

The REST routes own every gate (capability, XQL write stage, traversal,
budget); their codes pass through unchanged (D-1).
"""
from __future__ import annotations

import asyncio
import json
import shlex
import time
from typing import Any, NamedTuple, Optional

from connectors.tenant_reader import DATA_NOTICE, LIST_CAP, neutralise_document, neutralise_text
from connectors.xsiam import _key_names  # the envelope rule: identifier-shaped key names only
from integrations.xsiam.codes import XSIAM_PARSE_ERROR, XSIAM_QUERY_ERROR, remediation_for
from integrations.xsiam.exceptions import XsiamError
from integrations.xsiam.queries import result_rows, row_count

from ..client import SimCoreShapeUnknown, encode_segment
from ..envelope import Envelope, _iso_utc, ledger_from_snapshot
from ..errors import (
    MCP_WRITE_NOT_EXECUTABLE,
    NO_TENANT_CONFIGURED,
    REMEDIATION,
    SIMCORE_SHAPE_UNKNOWN,
    TOOL_NOT_BACKED,
    McpToolError,
)
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
    ArchiveGetIn,
    AssetGetIn,
    CaseSearchIn,
    DatasetSchemaIn,
    EmptyInput,
    EndpointGetIn,
    IssueSearchIn,
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
#: On ``xsiam.alert.list`` / ``xsiam.incident.list``: in the description and as
#: ``data["degradations"]``. Both keep working, because the matcher still reads
#: the alerts API (tenant-reader spec §7.6).
LEGACY_LINE = ("LEGACY: PANW's deprecation map lists get_alerts → issue/search and get_incidents → "
               "case/search; prefer xsiam.issue.search / xsiam.case.search.")

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

# The six parity reads (tenant-reader spec §8.1). Field tables and envelopes are
# PANW's public API pages, recorded in docs/reference/xsiam-platform-apis/15-parity-reads.md.
#: get_tenant_info: every documented licence key, kept verbatim. The page gives
#: no units, and a 0 expiration means "not licensed", so none is read as a date.
_TENANT_INFO_FIELDS = tuple((key, key, None) for key in (
    "xsiam_premium_expiration", "purchased_xsiam_premium",
    "pro_per_endpoint_expiration", "purchased_pro_per_endpoint", "data_enabled_pro_per_endpoint",
    "prevent_expiration", "purchased_prevent", "installed_prevent",
    "pro_gb_expiration", "purchased_pro_gb", "installed_pro_gb",
    "compute_unit_expiration", "purchased_compute_unit",
    "host_insights_expiration", "enabled_host_insights", "purchased_host_insights",
    "forensics_expiration", "enabled_forensics",
    "pro_cloud_expiration", "purchased_pro_cloud", "installed_pro_cloud", "data_enabled_pro_cloud",
    "identity_threat_expiration", "xth_expiration", "purchased_xth",
    "threat_intelligence_management_expiration", "purchased_threat_intelligence_management",
    "attack_surface_management_expiration", "purchased_attack_surface_management",
    "xsiam_ep_hot_expiration", "purchased_xsiam_ep_hot",
    "xsiam_ep_cold_expiration", "purchased_xsiam_ep_cold",
    "xsiam_gb_hot_expiration", "purchased_xsiam_gb_hot",
    "xsiam_gb_cold_expiration", "purchased_xsiam_gb_cold"))
_AUDIT_FIELDS = (("audit_id", "AUDIT_ID", None), ("owner_email", "AUDIT_OWNER_EMAIL", None),
                 ("entity", "AUDIT_ENTITY", None), ("entity_subtype", "AUDIT_ENTITY_SUBTYPE", None),
                 ("result", "AUDIT_RESULT", None), ("severity", "AUDIT_SEVERITY", None),
                 ("hostname", "AUDIT_HOSTNAME", None), ("case_id", "AUDIT_CASE_ID", None),
                 ("insert_time", "AUDIT_INSERT_TIME", "ts"))
_AUDIT_TEXT = (("owner_name", "AUDIT_OWNER_NAME"), ("description", "AUDIT_DESCRIPTION"),
               ("reason", "AUDIT_REASON"), ("asset_names", "AUDIT_ASSET_NAMES"))
#: Asset rows carry flat dotted XDM keys ("xdm.asset.id" is one key, not a path).
#: The asset's name is a display name whoever creates the resource chooses, so
#: it is tenant text (spec §7.7), not an allowlisted field.
_ASSET_FIELDS = (("asset_id", "xdm.asset.id", None), ("strong_id", "xdm.asset.strong_id", None),
                 ("type_name", "xdm.asset.type.name", None),
                 ("type_category", "xdm.asset.type.category", None),
                 ("type_class", "xdm.asset.type.class", None),
                 ("provider", "xdm.asset.provider", None), ("realm", "xdm.asset.realm", None),
                 ("cloud_region", "xdm.asset.cloud.region", None),
                 ("first_observed", "xdm.asset.first_observed", "ts"),
                 ("last_observed", "xdm.asset.last_observed", "ts"))
_ASSET_TEXT = (("name", "xdm.asset.name"), ("resource_group", "xdm.asset.resource_group"),
               ("hierarchy_path", "xdm.asset.hierarchy.path"))
_VULNERABILITY_FIELDS = (("vulnerability_id", "vulnerability_id", None),
                         ("cvss_severity", "cvss_severity", None), ("cvss_score", "cvss_score", None),
                         ("cvss_version", "cvss_version", None), ("epss_score", "epss_score", None),
                         ("cisa_kev", "cisa_kev", None), ("attack_vector", "attack_vector", None),
                         ("first_published", "first_published", "ts"),
                         ("last_modified", "last_modified", "ts"))
#: PANW documents no field of an assessment result row; these are the
#: assessment-profile model's, provisional until a live reply is recorded. The
#: profile name and a custom standard's name are user-named: tenant text.
_ASSESSMENT_FIELDS = (("id", "id", None), ("enabled", "enabled", None),
                      ("standard_id", "standard_id", None),
                      ("asset_group_id", "asset_group_id", None), ("report_type", "report_type", None),
                      ("creation_time", "creation_time", "ts"),
                      ("modification_time", "modification_time", "ts"))
_ASSESSMENT_TEXT = (("name", "name"), ("standard_name", "standard_name"),
                    ("description", "description"))


class _Shape(NamedTuple):
    op_id: str
    out_key: str              # the list's (or the one object's) name in data
    rows_key: Optional[str]   # where the list sits in the reply; None = the reply is the list
    #: The tenant's own total, when the reply carries one: a key, or a tuple
    #: path into the reply (``("metadata", "total_count")``).
    total_key: Any
    #: An upstream key every row must carry; None (an object with no identifier)
    #: = at least one allowlisted key must be present.
    required: Optional[str]
    fields: tuple
    #: One object, not a list: ``data[out_key]`` is that object. With a
    #: ``rows_key`` the reply holds it as a one-record list (a by-id read).
    object_reply: bool = False
    #: The catalog path param an object read fills from its input.
    path_param: Optional[str] = None
    #: ``(output name, upstream key)``: free text a tenant user wrote, projected
    #: under ``row["tenant_text"]``.
    tenant_text: tuple = ()


_SHAPES = {
    "datasets": _Shape("OP-XQL-GET-DATASETS", "datasets", None, None, "Dataset Name", _DATASET_FIELDS),
    "alerts": _Shape("OP-ALERTS-GET-ALERTS-MULTI-EVENTS", "alerts", "alerts", "total_count", "alert_id",
                     _ALERT_FIELDS),
    "correlation_rules": _Shape("OP-CORRELATIONS-GET", "correlation_rules", "objects", "objects_count",
                                "name", _CORRELATION_FIELDS),
    "incidents": _Shape("OP-INCIDENTS-GET-INCIDENTS", "incidents", "incidents", "total_count",
                        "incident_id", _INCIDENT_FIELDS),
    "tenant_info": _Shape("OP-SYSTEM-GET-TENANT-INFO", "tenant_info", None, None, None,
                          _TENANT_INFO_FIELDS, object_reply=True),
    "audit_logs": _Shape("OP-AUDITS-MANAGEMENT-LOGS", "audit_logs", "data", "total_count",
                         "AUDIT_ID", _AUDIT_FIELDS, tenant_text=_AUDIT_TEXT),
    # OP-ASSETS (POST /assets), the documented successor of the /assets/ POST the
    # retired server sent; OP-ASSETS-GET-ASSETS is the legacy search. The live
    # check confirms the tenant accepts it (15-parity-reads.md).
    "assets": _Shape("OP-ASSETS", "assets", "data", ("metadata", "total_count"), "xdm.asset.id",
                     _ASSET_FIELDS, tenant_text=_ASSET_TEXT),
    "asset": _Shape("OP-ASSETS-ID", "asset", "data", None, "xdm.asset.id", _ASSET_FIELDS,
                    object_reply=True, path_param="id", tenant_text=_ASSET_TEXT),
    "vulnerabilities": _Shape("OP-UVEM-GET-VULNERABILITIES", "vulnerabilities", "vulnerabilities",
                              "total_count", "vulnerability_id", _VULNERABILITY_FIELDS),
    "assessment_results": _Shape("OP-COMPLIANCE-GET-ASSESSMENT-RESULTS", "assessment_results",
                                 "data", "total_count", "id", _ASSESSMENT_FIELDS,
                                 tenant_text=_ASSESSMENT_TEXT),
}
#: Lists whose rows a tenant user wrote: they carry the data framing (PD7), with
#: their field layout unchanged.
_TENANT_ROWS = frozenset({"alerts", "incidents", "correlation_rules"})
#: Lists over an API that PANW's deprecation map retires.
_LEGACY = frozenset({"alerts", "incidents"})
#: The parity reads (spec §8.1): every string neutralised and capped as the
#: tenant reader does, per-row ``text_truncated`` / ``list_truncated``, the data
#: framing, and parse errors that name reply keys, never quote a tenant value.
_PARITY = frozenset({"tenant_info", "audit_logs", "assets", "asset", "vulnerabilities",
                     "assessment_results"})


class _Clean:
    """Neutralises every string it is handed (``neutralise_text``: invisible and
    control characters stripped, capped at 200) and caps lists and objects at
    ``LIST_CAP`` entries, remembering whether it cut anything."""

    def __init__(self) -> None:
        self.text_truncated = False
        self.list_truncated = False

    def __call__(self, value: Any) -> Any:
        if isinstance(value, str):
            text, cut = neutralise_text(value)
            self.text_truncated |= cut
            return text
        if isinstance(value, list):
            if len(value) > LIST_CAP:
                self.list_truncated, value = True, value[:LIST_CAP]
            return [self(v) for v in value]
        if isinstance(value, dict):
            items = list(value.items())
            if len(items) > LIST_CAP:
                self.list_truncated, items = True, items[:LIST_CAP]
            return {self(str(k)): self(v) for k, v in items}
        return value


def _seen(value: Any, parity: bool) -> str:
    """What came back, for a parse error's detail. The older shapes quote up to
    200 characters of it; a parity read names only its type and identifier-shaped
    key names (the connector's envelope rule), so a planted tenant value never
    rides in an error."""
    if not parity:
        return str(value)[:200]
    if isinstance(value, dict):
        return f"an object with keys {_key_names(value)}"
    if isinstance(value, list):
        return f"a list of {len(value)}"
    return f"a {type(value).__name__}"


def _dig(reply: dict, key: Any) -> Any:
    """``reply[key]``, or along a tuple path; None where a step is missing."""
    if isinstance(key, str):
        return reply.get(key)
    value: Any = reply
    for step in key:
        if not isinstance(value, dict):
            return None
        value = value.get(step)
    return value


def _key_label(key: Any) -> str:
    return key if isinstance(key, str) else ".".join(key)


def _convert(value: Any, conv: Optional[str]) -> Any:
    if conv == "ts" and value is not None:
        return _iso_utc(value)
    if conv == "lower" and isinstance(value, str):
        return value.lower()
    return value


def _project(row: Any, shape: _Shape, ex: Executed, where: str) -> dict:
    parity = shape.out_key in _PARITY
    if shape.required is None:
        if not isinstance(row, dict) or not any(key in row for _, key, _ in shape.fields):
            raise _parse_error(ex, where, f"the {shape.out_key} reply is not an object carrying "
                                          f"a documented key: {_seen(row, parity)}")
    elif not isinstance(row, dict) or shape.required not in row:
        raise _parse_error(ex, where, f"a {shape.out_key} row has no {shape.required!r}: "
                                      f"{_seen(row, parity)}")
    if not parity:
        return {name: _convert(row.get(key), conv) for name, key, conv in shape.fields}
    clean = _Clean()
    out = {name: clean(_convert(row.get(key), conv)) for name, key, conv in shape.fields}
    if shape.tenant_text:
        out["tenant_text"] = {name: clean(row.get(key)) for name, key in shape.tenant_text}
    out["text_truncated"] = clean.text_truncated
    out["list_truncated"] = clean.list_truncated
    return out


async def _named_list(ctx: ToolContext, args: TenantListIn, shape: _Shape) -> Envelope:
    ex = await execute(ctx, tenant=args.tenant, op_id=shape.op_id, path_params={}, query=None,
                       body=None, dry_run=None)
    parity = shape.out_key in _PARITY
    where, reply = f"{shape.op_id} reply", ex.body["reply"]
    if shape.rows_key is None:
        rows, total = reply, None
    elif isinstance(reply, dict):
        rows = reply.get(shape.rows_key)
        total = _dig(reply, shape.total_key) if shape.total_key is not None else None
    else:
        rows, total = None, None
    if not isinstance(rows, list):
        raise _parse_error(ex, where, f"expected a list at {shape.rows_key or 'reply'}, "
                                      f"got {_seen(reply, parity)}")
    if shape.total_key is None:
        total = len(rows)
    elif isinstance(total, bool) or not isinstance(total, int):
        got = _seen(total, True) if parity else repr(total)
        raise _parse_error(ex, where, f"{_key_label(shape.total_key)} is missing or not an integer "
                                      f"({got}); the list's size is unknown, not {len(rows)}")
    page, _ = cap_list(rows, limit=args.limit, offset=args.offset)
    truncated = args.offset + len(page) < total
    data = {shape.out_key: [_project(r, shape, ex, where) for r in page], "total": total,
            "truncated": truncated}
    if shape.out_key in _TENANT_ROWS or parity:
        data["data_notice"] = DATA_NOTICE
    if shape.out_key in _LEGACY:
        data["degradations"] = [LEGACY_LINE]
    return await _tenant_env(ctx, data, tenant=ex.tenant, queries=ex.queries_issued,
                             ledger=ex.ledger, truncated=truncated, answered=ex.answered)


async def _named_object(ctx: ToolContext, shape: _Shape, *, tenant: Optional[str],
                        path_value: Optional[str] = None) -> Envelope:
    """One object over one catalog op: ``data[out_key]``, never an empty object.

    With a ``rows_key`` (a by-id read) the reply must hold exactly one record
    there: none or several is not this shape (an unknown id is the tenant's
    404, which the route answers as ``XSIAM_API_ERROR``). ``path_value`` fills
    ``shape.path_param``; the executor route refuses a value that is not one
    path segment with ``XSIAM_CONFIG_ERROR`` before any tenant call.
    """
    params = {shape.path_param: path_value} if shape.path_param else {}
    ex = await execute(ctx, tenant=tenant, op_id=shape.op_id, path_params=params, query=None,
                       body=None, dry_run=None)
    where, reply = f"{shape.op_id} reply", ex.body["reply"]
    obj = reply
    if shape.rows_key is not None:
        rows = reply.get(shape.rows_key) if isinstance(reply, dict) else None
        if not isinstance(rows, list) or len(rows) != 1:
            got = _seen(reply if rows is None else rows, True)
            raise _parse_error(ex, where, f"expected exactly one record in a list at "
                                          f"{shape.rows_key}, got {got}")
        obj = rows[0]
    data = {shape.out_key: _project(obj, shape, ex, where), "data_notice": DATA_NOTICE}
    return await _tenant_env(ctx, data, tenant=ex.tenant, queries=ex.queries_issued,
                             ledger=ex.ledger, answered=ex.answered)


async def dataset_list(ctx, args):
    return await _named_list(ctx, args, _SHAPES["datasets"])


async def alert_list(ctx, args):
    return await _named_list(ctx, args, _SHAPES["alerts"])


async def correlation_rule_list(ctx, args):
    return await _named_list(ctx, args, _SHAPES["correlation_rules"])


async def incident_list(ctx, args):
    return await _named_list(ctx, args, _SHAPES["incidents"])


async def tenant_info_get(ctx: ToolContext, args: TenantIn) -> Envelope:
    return await _named_object(ctx, _SHAPES["tenant_info"], tenant=args.tenant)


async def audit_log_list(ctx, args):
    return await _named_list(ctx, args, _SHAPES["audit_logs"])


async def asset_list(ctx, args):
    return await _named_list(ctx, args, _SHAPES["assets"])


async def asset_get(ctx: ToolContext, args: AssetGetIn) -> Envelope:
    return await _named_object(ctx, _SHAPES["asset"], tenant=args.tenant, path_value=args.asset_id)


async def vulnerability_list(ctx, args):
    return await _named_list(ctx, args, _SHAPES["vulnerabilities"])


async def assessment_result_list(ctx, args):
    return await _named_list(ctx, args, _SHAPES["assessment_results"])


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


# --- the ZIP reads: playbooks/get and scripts/get (tenant-reader spec §8.1) ------
#: The keys of one member of SimCore's archive listing (archives.zip_members).
_MEMBER_KEYS = ("name", "text", "bytes", "text_truncated", "binary", "refused")


def _not_a_listing(ex: Executed, where: str, detail: str) -> TenantCallError:
    """The route's reply is not SimCore's archive listing: a SimCore that does not
    read this op as a ZIP. The tenant was read, so the count stays the route's."""
    return TenantCallError(
        SIMCORE_SHAPE_UNKNOWN, f"{where}: not an archive listing", detail, tenant=ex.tenant,
        queries_issued=ex.queries_issued, ledger_snapshot=ex.ledger,
        remediation=REMEDIATION[SIMCORE_SHAPE_UNKNOWN])


def _archive_member(m: dict) -> dict:
    """One member; its name and text sit only under ``tenant_text``."""
    name, _ = neutralise_text(m["name"])
    text, cut = m["text"], bool(m["text_truncated"])
    if isinstance(text, str):
        text, capped, _ = cap_text(neutralise_document(text))
        cut = cut or capped
    else:
        text = None
    return {"bytes": m["bytes"], "binary": m["binary"], "refused": m["refused"],
            "text_truncated": cut, "tenant_text": {"name": name, "text": text}}


async def _archive_get(ctx: ToolContext, args: ArchiveGetIn, op_id: str) -> Envelope:
    """One ``zip-reply`` op through the executor route, filtered by id or name."""
    flt = {"field": args.by, "value": args.value}
    ex = await execute(ctx, tenant=args.tenant, op_id=op_id, path_params={}, query=None,
                       body={"request_data": {"filter": flt}}, dry_run=None)
    where, reply = f"{op_id} reply", ex.body["reply"]
    expected = "an object with members[], an integer member_count and a boolean members_truncated"
    if not isinstance(reply, dict):
        raise _not_a_listing(ex, where, f"got {_seen(reply, True)}; expected {expected}")
    members, count, cut = (reply.get("members"), reply.get("member_count"),
                           reply.get("members_truncated"))
    if (not isinstance(members, list) or isinstance(count, bool) or not isinstance(count, int)
            or not isinstance(cut, bool)):
        raise _not_a_listing(ex, where, f"got {_seen(reply, True)}; expected {expected}")
    for m in members:
        if not isinstance(m, dict) or any(k not in m for k in _MEMBER_KEYS):
            raise _not_a_listing(ex, where, f"a member is {_seen(m, True)}; expected the keys "
                                            f"{', '.join(_MEMBER_KEYS)}")
    out = [_archive_member(m) for m in members]
    data = {"op_id": op_id, "filter": flt, "members": out, "member_count": count,
            "members_truncated": cut, "data_notice": DATA_NOTICE}
    # Truncated when the result does not hold the whole archive: a member past
    # the 50 listed, a text cut by a cap, or a member refused for any reason.
    left_out = any(m["text_truncated"] or m["refused"] is not None for m in out)
    return await _tenant_env(ctx, data, tenant=ex.tenant, queries=ex.queries_issued,
                             ledger=ex.ledger, answered=ex.answered, truncated=cut or left_out)


async def playbook_get(ctx: ToolContext, args: ArchiveGetIn) -> Envelope:
    return await _archive_get(ctx, args, "OP-PLAYBOOKS-GET")


async def script_get(ctx: ToolContext, args: ArchiveGetIn) -> Envelope:
    return await _archive_get(ctx, args, "OP-SCRIPTS-GET")


# --- the tenant reader: issues, cases, endpoints (tenant-reader spec §7.4) -------
#: The reply keys this layer reads; the rest of the route's reply passes through.
_READER_KEYS = ("truncated", "queries_issued", "ledger")
_RECONCILE_KIND = "xsiam"


async def _reader_tenant(ctx: ToolContext, tenant: Optional[str]) -> str:
    """The ``xsiam_tenant`` credential to read through. When none qualifies and
    reconcile's ``xsiam`` kind is what is configured, the refusal says so and
    names the kind these reads need, instead of an empty list of names."""
    try:
        return await resolve_tenant(ctx, tenant, kind=TENANT_KIND, must_exist=True)
    except McpToolError as err:
        if err.code != NO_TENANT_CONFIGURED:
            raise
        reconcile = sorted(i["name"] for i in (await readiness(ctx))["integrations"]
                           if isinstance(i, dict) and i.get("kind") == _RECONCILE_KIND
                           and (tenant is None or i.get("name") == tenant))
        if not reconcile:
            raise
        names = ", ".join(f"'{n}'" for n in reconcile)
        noun = (f"is an '{_RECONCILE_KIND}' (reconcile) credential" if len(reconcile) == 1
                else f"are '{_RECONCILE_KIND}' (reconcile) credentials")
        raise McpToolError(
            NO_TENANT_CONFIGURED, f"{names} {noun}, not '{TENANT_KIND}'",
            f"{names} {noun}, which serve alert read-back only; xsiam.issue.search, "
            f"xsiam.case.search and xsiam.endpoint.get need an '{TENANT_KIND}' credential. "
            "Nothing was queried",
            remediation=REMEDIATION[NO_TENANT_CONFIGURED], basis="authored") from err


async def _reader_read(ctx: ToolContext, args: Any, *, segment: str, rows_key: str) -> Envelope:
    """``POST /api/xsiam/tenants/{t}/{segment}``: SimCore's projection, passed through.

    The tenant must name an ``xsiam_tenant`` credential; a reconcile-only
    ``xsiam`` one is ``NO_TENANT_CONFIGURED`` before any request. The count is
    the route's. Its own ``pending`` reply (the credential changed between the
    readiness read and the call) passes through at the count it reports, 0.
    ``data.truncated`` is the route's true/false/null verbatim. When the reply
    carries rows, the boolean ``evidence.truncated`` errs safe and is false only
    when the route says false; a reply with no rows (``pending``) has nothing a
    cut could leave behind, and this layer caps nothing of its own, so it is false.
    """
    tenant = await _reader_tenant(ctx, args.tenant)
    take_token(ctx)
    path = f"/api/xsiam/tenants/{encode_segment(tenant)}/{segment}"
    where = f"POST {path}"

    def read(b: dict) -> int:
        _require(b, where, rows_key, *_READER_KEYS)
        return _int(b, where, "queries_issued")

    body, queries = await tenant_call(ctx, tenant=tenant, read=read,
                                      coro=ctx.client.post(path, json=args.to_body()))
    data = {**body, "data_notice": DATA_NOTICE}
    has_rows = isinstance(body[rows_key], list)
    return await _tenant_env(ctx, data, tenant=tenant, queries=queries, ledger=body["ledger"],
                             truncated=has_rows and body["truncated"] is not False,
                             answered=body.get("status") != "pending")


async def issue_search(ctx: ToolContext, args: IssueSearchIn) -> Envelope:
    return await _reader_read(ctx, args, segment="issues/search", rows_key="issues")


async def case_search(ctx: ToolContext, args: CaseSearchIn) -> Envelope:
    return await _reader_read(ctx, args, segment="cases/search", rows_key="cases")


async def endpoint_get(ctx: ToolContext, args: EndpointGetIn) -> Envelope:
    return await _reader_read(ctx, args, segment="endpoints/get", rows_key="endpoints")


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
    # PD6: playbooks/get answers one playbook as a ZIP; the API has no listing.
    "xsiam.playbook.list": (
        "the XSIAM public API exposes no playbook listing: POST /public_api/v1/playbooks/get "
        "returns one playbook, by id or name, as YAML in a ZIP, which xsiam.playbook.get reads; "
        "nothing was queried",
        "read one playbook with xsiam.playbook.get (by: id or name; value: the playbook's id or "
        "name)"),
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
_NO_KEY = ("Holds no tenant key: SimCore reads with the xsiam_tenant credential stored at tenant "
           "onboarding, under the per-tenant request budget run evidence and reconcile share.")
_READER = ("Returns the route's reply: {rows}[] (allowlisted; free text only under tenant_text), "
           "tenant, total_count, filter_count, truncated, unrecognised_values, "
           "excluded_non_security, duplicate_rows, queries_issued, ledger and data_notice. "
           "data.truncated is the route's true/false/null verbatim (null: the tenant sent no "
           "count, so whether rows were left behind is unknown); when rows came back, "
           "evidence.truncated is true unless the route says false (a pending reply with no "
           "rows is false). A reconcile-only xsiam credential is "
           "NO_TENANT_CONFIGURED with no call; an unrecognised reply is XSIAM_PARSE_ERROR, never "
           "an empty list.")


def _fields(shape: _Shape) -> str:
    return (f"Returns {shape.out_key}[] capped by limit/offset (default 50, max 200) with total + "
            f"truncated; allowlisted fields only: {', '.join(f[0] for f in shape.fields)}. An "
            f"unrecognised reply is XSIAM_PARSE_ERROR, never an empty list.")


def _parity_fields(shape: _Shape) -> str:
    what = (f"Returns {shape.out_key}, one object" if shape.object_reply else
            f"Returns {shape.out_key}[] capped by limit/offset (default 50, max 200) with total + "
            f"truncated. The tenant is read in ONE request and limit/offset page over that one "
            f"reply, so offset cannot reach past the tenant's first page; truncated: true says "
            f"the tenant holds more than was returned")
    text = (f"; free text only under tenant_text: {', '.join(t[0] for t in shape.tenant_text)}"
            if shape.tenant_text else "")
    return (f"{what}; allowlisted fields only: {', '.join(f[0] for f in shape.fields)}{text}. "
            "Every string is neutralised and capped at 200 characters, lists at 50 "
            "(text_truncated / list_truncated say when). An unrecognised reply is "
            f"XSIAM_PARSE_ERROR, never an empty {'object' if shape.object_reply else 'list'}.")


#: The ZIP reads (tenant-reader spec §8.1); PANW's page for each is recorded in
#: docs/reference/xsiam-platform-apis/15-parity-reads.md.
_ARCHIVE = ("{op_id} (POST {path}) through SimCore's catalog executor: one {noun}, chosen by "
            "by: id | name and value, which the tenant returns as YAML inside a ZIP. SimCore "
            "reads the archive in memory and never extracts it: at most 50 members are listed; "
            "a member over 256 KiB, or past 1 MiB in all, is refused unread (refused: "
            "MEMBER_OVER_CAP / TOTAL_OVER_CAP; UNREADABLE for a corrupt or encrypted one); a "
            "member that is not UTF-8 is binary: true with no text. Returns op_id, filter, "
            "members[] (bytes, binary, refused, text_truncated, tenant_text {{name, text}}), "
            "member_count, members_truncated and data_notice. A member's name and text are only "
            "under tenant_text; the text keeps its lines, with invisible, control and bidi "
            "characters stripped. evidence.truncated is true when a member or its text was left "
            "out. A tenant error keeps the route's code; a reply that is neither a ZIP nor a "
            "tenant error is XSIAM_PARSE_ERROR, never an empty archive.")

#: What one parity read adds to its description, after the shared text.
_PARITY_NOTES = {
    "tenant_info": ("The tenant's licence record: every documented key, verbatim (an expiration "
                    "of 0 means not licensed; units are not documented, so none is read as a "
                    "date). PANW's page documents no platform-version field in it."),
    "audit_logs": ("Console and API management actions, newest first as the tenant sorts them; "
                   "the tenant's page is its default 100 rows."),
    "assets": ("The asset inventory (POST /public_api/v1/assets); total is metadata.total_count; "
               "the tenant's page is its default (at most 1,000 assets)."),
    "asset": ("One asset by asset_id (GET /public_api/v1/assets/{id}). asset_id is one path "
              "segment: '/', '\\', '.' and '..' are refused with XSIAM_CONFIG_ERROR before any "
              "tenant call; an unknown id is the tenant's 404, XSIAM_API_ERROR."),
    "vulnerabilities": ("Vulnerability intelligence (UVEM): CVE id, CVSS, EPSS, CISA KEV, dates; "
                        "the tenant's page is its default 500 rows (its page token is not "
                        "followed)."),
    "assessment_results": ("Compliance assessment-profile results; the API does not paginate. "
                           "PANW documents no field of a result row: the allowlist follows the "
                           "assessment-profile model, and a row without id is XSIAM_PARSE_ERROR."),
}


def specs() -> list[ToolSpec]:
    def s(id_, model, impl, klass, network, desc, *, scopes=(), idempotent=True, backed=True):
        return ToolSpec(id=id_, input_model=model, impl=impl, klass=klass, network=network,
                        scopes=tuple(scopes), description=desc, idempotent=idempotent,
                        backed=backed)

    def named(id_, key, scopes):
        shape = _SHAPES[key]
        desc = f"{shape.op_id} through SimCore's catalog executor. {_fields(shape)} {_TENANT}"
        if key in _LEGACY:
            desc = f"{LEGACY_LINE} {desc} data.degradations carries the same line."
        if key in _TENANT_ROWS:
            desc += f" data.data_notice: {DATA_NOTICE}"
        return s(id_, TenantListIn, _LIST_IMPLS[key], "read", "tenant", desc, scopes=scopes)

    def reader(id_, model, impl, route, rows, what):
        return s(id_, model, impl, "read", "tenant",
                 f"POST /api/xsiam/tenants/{{tenant}}/{route}: {what} {_READER.format(rows=rows)} "
                 f"{_TENANT} {_NO_KEY} data_notice: {DATA_NOTICE}", scopes=("alert.read",))

    def archive(id_, impl, op_id, path, noun):
        return s(id_, ArchiveGetIn, impl, "read", "tenant",
                 f"{_ARCHIVE.format(op_id=op_id, path=path, noun=noun)} {_TENANT} {_NO_KEY} "
                 f"data_notice: {DATA_NOTICE}", scopes=("content.read",))

    def not_backed(tool):
        return s(tool, TenantIn, _not_backed(tool), "read", "none",
                 f"Registered, not backed: always TOOL_NOT_BACKED ({_NOT_BACKED[tool][0]}), never an "
                 "empty list. Makes no call.", backed=False)

    def parity(id_, key, model, impl):
        shape = _SHAPES[key]
        return s(id_, model, impl, "read", "tenant",
                 f"{shape.op_id} through SimCore's catalog executor. {_PARITY_NOTES[key]} "
                 f"{_parity_fields(shape)} {_TENANT} {_NO_KEY} data_notice: {DATA_NOTICE}")

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
          "with the preview attached. Path params are single segments (the executor's guard). A "
          "zip-reply op (OP-PLAYBOOKS-GET, OP-SCRIPTS-GET) replies with SimCore's in-memory "
          "reading of the archive (members[], member_count, members_truncated), its text not "
          "framed; xsiam.playbook.get / xsiam.script.get frame it.",
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
        not_backed("xsiam.playbook.list"),
        s("xsiam.healthcheck", TenantIn, healthcheck, "read", "tenant",
          "OP-HEALTHCHECK through SimCore's catalog executor. Returns license_gated and status only. "
          "A 403 is license_gated: true (reachable, healthcheck license-gated), not unreachable. "
          f"{_TENANT}"),
        *(not_backed(tool) for tool in _NOT_BACKED if tool != "xsiam.playbook.list"),
        reader("xsiam.issue.search", IssueSearchIn, issue_search, "issues/search", "issues",
               "Security-domain issues (OP-ISSUE-SEARCH), newest first, filtered by an "
               "_insert_time window (since/until), ids, detection.method display names and "
               "severities; limit <= 200 with offset."),
        reader("xsiam.case.search", CaseSearchIn, case_search, "cases/search", "cases",
               "cases (OP-CASE-SEARCH) by id and/or a creation_time window; limit <= 200 with "
               "offset."),
        reader("xsiam.endpoint.get", EndpointGetIn, endpoint_get, "endpoints/get", "endpoints",
               "endpoint records (OP-ENDPOINTS-GET-ENDPOINT) for 1..100 hostnames, as of now, in "
               "one request."),
        parity("xsiam.tenant_info.get", "tenant_info", TenantIn, tenant_info_get),
        parity("xsiam.audit_log.list", "audit_logs", TenantListIn, audit_log_list),
        parity("xsiam.asset.list", "assets", TenantListIn, asset_list),
        parity("xsiam.asset.get", "asset", AssetGetIn, asset_get),
        parity("xsiam.vulnerability.list", "vulnerabilities", TenantListIn, vulnerability_list),
        parity("xsiam.assessment_result.list", "assessment_results", TenantListIn,
               assessment_result_list),
        archive("xsiam.playbook.get", playbook_get, "OP-PLAYBOOKS-GET",
                "/public_api/v1/playbooks/get", "playbook"),
        archive("xsiam.script.get", script_get, "OP-SCRIPTS-GET", "/public_api/v1/scripts/get",
                "script"),
    ]


_LIST_IMPLS = {"datasets": dataset_list, "alerts": alert_list, "correlation_rules": correlation_rule_list,
               "incidents": incident_list}

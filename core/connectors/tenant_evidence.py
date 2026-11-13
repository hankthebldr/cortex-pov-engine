"""Run tenant evidence: one explicit, metered, read-only tenant read for a run.

``POST /api/runs/{id}/tenant-evidence`` calls :func:`read_run_evidence`;
``GET`` calls :func:`latest_tenant_evidence`, which reads only the database.
Spec §7.3 (the reader) and §7.4 (the snapshot and its routes).

What one read does, in order:

1. A run with no ``Result.executed_at`` has no window: ``NO_RUN_WINDOW``, no
   tenant request, nothing stored.
2. The credential resolves as reconcile's does (``xsiam``, or the equivalent
   ``xsiam_tenant``). None configured: ``pending`` / ``NO_TENANT_CONFIGURED``,
   ``issues: null``.
3. A read of the same run and integration inside
   ``CORTEXSIM_TENANT_EVIDENCE_MIN_INTERVAL_SECONDS`` is returned, not repeated
   (``TENANT_EVIDENCE_READ_RECENTLY``), unless ``force``.
4. The window is ``[earliest executed_at − 300 s, min(latest matcher window
   end, now)]`` — the matcher's own windows, so both readers look at one span.
5. ``issue/search`` over the window and the Security domain, sorted by
   ``observation_time``, up to ``CORTEXSIM_TENANT_EVIDENCE_MAX_PAGES`` pages;
   rows de-duplicated by id; a non-Security row is excluded and counted.
6. Attribution to the run's host (``tenant_reader.attribute``).
7. The id probe: the matcher's alert ids read back by id, with the host fields.
8. Cases: ``case/search`` for the attributed issues' case ids (at most 100).
9. The run host's endpoint record, labelled as of ``read_at``.

Every connector call runs in ``asyncio.to_thread``: the fetcher is
synchronous and SimCore's loop also serves beacon polls, SSE and the abort
poll. ``queries_issued`` sums the requests each sub-read actually sent (a local
budget refusal sends nothing). The first quota refusal stops the read: later
sections are ``QUOTA_REFUSED`` without a request, rows already read are kept,
and the snapshot is ``complete: false``.

Read-only toward the tenant, and context only here: this never calls
``mark_integration_verified`` and writes nothing a scorer reads — only a row in
``tenant_evidence_reads``. ``tenant_verified`` in the payload is copied from
:func:`connectors.provenance.tenant_verified`, never computed here.
"""
from __future__ import annotations

import asyncio
import math
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from integrations.xsiam import codes
from integrations.xsiam.codes import LOCAL_REFUSAL_REMEDIATION, remediation_for
from integrations.xsiam.queries import case_search_body, issue_search_body
from models import Observation, Result, Run, TenantEvidenceRead

from . import service, tuning
from .base import HttpFetcher, PullResult, coerce_utc
from .matcher import DEFAULT_WINDOW_SECONDS, _short_host
from .provenance import tenant_verified
from .tenant_reader import (
    DATA_NOTICE,
    ISSUE_INCLUDE_FIELDS,
    STATEMENT,
    IssueShapeError,
    agreement,
    attribute,
    group_cases,
    lag_seconds,
    project_endpoint,
    project_issue,
    run_issue_filters,
    unrecognised_values,
)
from .xsiam import _epoch_ms_utc, alert_host

READER_VERSION = "1"
#: The window opens this long before the run's first step.
WINDOW_LEAD_SECONDS = 300
#: Case ids sent in the one ``case/search`` request.
CASE_IDS_MAX = 100
ISSUE_DOMAIN = "Security"
CONNECTOR_KIND = "xsiam"
HISTORY_LIMIT = 10

OK = "OK"
NO_ATTRIBUTED_ISSUES = "NO_ATTRIBUTED_ISSUES"
UNSCOPED = "UNSCOPED"
QUOTA_REFUSED = "QUOTA_REFUSED"
NO_TENANT_CONFIGURED = "NO_TENANT_CONFIGURED"
NO_RUN_WINDOW = "NO_RUN_WINDOW"
TENANT_EVIDENCE_READ_RECENTLY = "TENANT_EVIDENCE_READ_RECENTLY"
TENANT_EVIDENCE_NOT_READ = "TENANT_EVIDENCE_NOT_READ"
ENDPOINT_NOT_FOUND = "ENDPOINT_NOT_FOUND"
AMBIGUOUS_ENDPOINT_RECORDS = "AMBIGUOUS_ENDPOINT_RECORDS"

_PENDING = frozenset({NO_TENANT_CONFIGURED, TENANT_EVIDENCE_NOT_READ})
#: Section codes that are a complete answer, even when degraded.
_ANSWERED = frozenset({OK, UNSCOPED, ENDPOINT_NOT_FOUND, AMBIGUOUS_ENDPOINT_RECORDS})


def status_for(code: str) -> str:
    """``pending`` (nothing read yet), ``ok``, or ``degraded``: a zero is degraded."""
    if code in _PENDING:
        return "pending"
    return "ok" if code == OK else "degraded"


def _naive_utc(value: Optional[datetime]) -> datetime:
    return coerce_utc(value) if value is not None else datetime.utcnow()


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


# ── one read's accounting ────────────────────────────────────────────────────

class _Read:
    """Requests sent, whether the tenant answered, and the first quota refusal.

    Every connector call goes through :meth:`call`, in a worker thread. After
    the first ``XSIAM_QUOTA_ERROR`` no further call is made.
    """

    def __init__(self) -> None:
        self.queries = 0
        self.last_sent = 0
        self.answered: Optional[bool] = None
        self.quota: Optional[dict[str, Any]] = None

    async def call(self, fn: Callable[..., PullResult], *args: Any, **kwargs: Any
                   ) -> Optional[PullResult]:
        if self.quota is not None:
            return None
        res = await asyncio.to_thread(fn, *args, **kwargs)
        detail = res.detail or {}
        local = bool(detail.get("refused_locally"))
        sent = int(detail.get("pages") or 0) if local else service._pages_issued(detail)
        self.queries += sent
        self.last_sent = sent
        if res.ok or detail.get("rows_kept"):
            if sent:
                self.answered = True
        elif sent and self.answered is None:
            self.answered = False
        if not res.ok and res.code == codes.XSIAM_QUOTA_ERROR:
            retry = (detail.get("retry_after_seconds") if local
                     else tuning.xsiam_breaker_cooldown())
            self.quota = {"refused_locally": local,
                          "retry_after_seconds": max(1, int(retry or 1))}
        return res


def _quota_section(quota: dict[str, Any], *, attempted: bool) -> dict[str, Any]:
    """A section the quota stopped. ``attempted`` is false for a section that
    was never tried because an earlier request was refused."""
    n = quota["retry_after_seconds"]
    if not attempted:
        detail = ("not read: an earlier request in this read was refused for quota, "
                  f"so no further request was sent; retry in {n}s")
    elif quota["refused_locally"]:
        detail = ("CortexSim's own per-tenant request budget refused this read before "
                  f"it was sent to the tenant; this says nothing about the tenant. "
                  f"Retry in {n}s")
    else:
        detail = ("the tenant answered HTTP 429 (its API rate limit, shared with the "
                  f"customer's own analysts); CortexSim stopped reading. Retry in {n}s")
    return {"code": QUOTA_REFUSED,
            "refused_locally": quota["refused_locally"] if attempted else None,
            "attempted": attempted, "retry_after_seconds": n, "detail": detail,
            "remediation": (LOCAL_REFUSAL_REMEDIATION
                            if attempted and quota["refused_locally"]
                            else remediation_for(codes.XSIAM_QUOTA_ERROR))}


def _failed_section(res: PullResult, what: str) -> dict[str, Any]:
    """A section whose read failed. Only SimCore's words: the tenant's own error
    text (``res.error`` can quote it) never enters a response."""
    return {"code": res.code or codes.XSIAM_API_ERROR,
            "detail": f"the {what} read failed ({res.code}); nothing was measured from it",
            "remediation": remediation_for(res.code)}


def _parse_section(e: IssueShapeError, what: str) -> dict[str, Any]:
    return {"code": codes.XSIAM_PARSE_ERROR,
            "detail": f"the {what} read returned a row this reader does not recognise: {e}",
            "remediation": remediation_for(codes.XSIAM_PARSE_ERROR)}


def _section_for(res: Optional[PullResult], read: _Read, what: str) -> Optional[dict]:
    """The section for a failed or skipped read, or None when ``res`` succeeded."""
    if res is None:
        return _quota_section(read.quota, attempted=False)
    if res.ok:
        return None
    if res.code == codes.XSIAM_QUOTA_ERROR:
        return _quota_section(read.quota, attempted=True)
    return _failed_section(res, what)


# ── the database side ────────────────────────────────────────────────────────

async def _results(db: AsyncSession, run_id: str) -> list[Result]:
    return list((await db.execute(
        select(Result).where(Result.run_id == run_id).order_by(Result.id))).scalars().all())


async def _matcher_rows(db: AsyncSession, run_id: str, results: list[Result],
                        run_host: Optional[str]) -> list[dict]:
    """One row per ``Observation`` linked to a ``Result``: the matcher's side of
    the agreement join. ``host`` is the alert record's own host
    (``alert_host``, the connector's reader); only when the record names none is
    it the run host, which the reconcile that linked it was scoped to."""
    executed = {r.id: r.executed_at for r in results}
    rows = (await db.execute(
        select(Observation).where(Observation.run_id == run_id,
                                  Observation.result_id.is_not(None))
        .order_by(Observation.id))).scalars().all()
    out = []
    for o in rows:
        raw = o.raw if isinstance(o.raw, dict) else {}
        out.append({"result_id": o.result_id, "external_id": o.external_id, "name": o.name,
                    "observed_at": o.observed_at, "host": alert_host(raw) or run_host,
                    "executed_at": executed.get(o.result_id)})
    return out


async def _snapshots(db: AsyncSession, run_id: str, *, integration: Optional[str] = None,
                     limit: int = HISTORY_LIMIT) -> list[TenantEvidenceRead]:
    q = select(TenantEvidenceRead).where(TenantEvidenceRead.run_id == run_id)
    if integration is not None:
        q = q.where(TenantEvidenceRead.integration == integration)
    q = q.order_by(TenantEvidenceRead.read_at.desc(), TenantEvidenceRead.id.desc()).limit(limit)
    return list((await db.execute(q)).scalars().all())


def _response(run: Run, *, read: bool, code: str, detail: str,
              status: Optional[str] = None, retry_after: Optional[int] = None,
              snapshot: Optional[TenantEvidenceRead] = None) -> dict:
    snap = snapshot.to_dict() if snapshot is not None else None
    payload = (snap or {}).get("payload") or {}
    return {"run_id": run.run_id, "read": read, "status": status or status_for(code),
            "code": code, "detail": detail, "retry_after_seconds": retry_after,
            "snapshot": snap, "issues": payload.get("issues") if snap else None}


# ── the read ─────────────────────────────────────────────────────────────────

def _project_window(raw_rows: list[dict]) -> tuple[list[dict], int, int]:
    """``(issues, excluded_non_security, duplicate_rows)``: projected, one per id
    (first kept), Security domain only. Raises :class:`IssueShapeError`."""
    by_id: dict[str, dict] = {}
    duplicates = 0
    for raw in raw_rows:
        issue = project_issue(raw)
        if issue["id"] in by_id:
            duplicates += 1
            continue
        by_id[issue["id"]] = issue
    kept, excluded = [], 0
    for issue in by_id.values():
        if (issue.get("domain") or "").casefold() == ISSUE_DOMAIN.casefold():
            kept.append(issue)
        else:
            excluded += 1
    return kept, excluded, duplicates


def _with_lag(issue: dict, pairs: list[dict], executed: dict[Any, Optional[datetime]]) -> dict:
    """PD1: "observed after step" only for an issue joined to a matcher row,
    timed from that row's ``Result.executed_at`` (the earliest, when the issue
    joined several). Never MTTD."""
    joined = sorted((executed[p["result_id"]], p["result_id"]) for p in pairs
                    if p["issue_id"] == issue["id"] and executed.get(p["result_id"]))
    if not joined:
        return {**issue, "observed_after_step_seconds": None,
                "observed_after_step_result_id": None}
    at, rid = joined[0]
    return {**issue, "observed_after_step_seconds": lag_seconds(issue, at),
            "observed_after_step_result_id": rid}


def _case_value(cid: str) -> Any:
    return int(cid) if cid.isascii() and cid.isdigit() else cid


async def read_run_evidence(
    db: AsyncSession, run: Run, *, integration_name: Optional[str] = None,
    force: bool = False, fetcher: Optional[HttpFetcher] = None,
    now: Optional[datetime] = None,
) -> dict:
    """Read the run's tenant evidence and store it as a snapshot (§7.3, §7.4).

    Returns ``{"run_id", "read", "status", "code", "detail",
    "retry_after_seconds", "snapshot", "issues"}``; ``issues`` is the stored
    payload's issues section, or null when nothing was read. Raises
    :class:`connectors.service.ReconcileError` for a named integration that
    does not exist or a credential that cannot be loaded (the API maps them as
    reconcile does).
    """
    now = _naive_utc(now)
    results = await _results(db, run.run_id)
    executed = [r.executed_at for r in results if r.executed_at is not None]
    if not executed:
        return _response(run, read=False, code=NO_RUN_WINDOW, detail=(
            "no step of this run has an execution time, so there is no window to read; "
            "no tenant request was made"))

    try:
        conn, cfg, _store, integ = await service._resolve_connector(
            db, CONNECTOR_KIND, integration_name, fetcher)
    except service.ReconcileError as e:
        if e.code != "NO_INTEGRATION":
            raise
        kinds = " or ".join(f"'{k}'" for k in service.credential_kinds_for(CONNECTOR_KIND))
        return _response(run, read=False, code=NO_TENANT_CONFIGURED, detail=(
            f"no integration of kind {kinds} is configured, so nothing was read from a "
            "tenant; configure one under /api/credentials/integrations"))

    interval = timedelta(seconds=settings.CORTEXSIM_TENANT_EVIDENCE_MIN_INTERVAL_SECONDS)
    if not force:
        recent = await _snapshots(db, run.run_id, integration=integ.name, limit=1)
        if recent and now - recent[0].read_at < interval:
            last = recent[0]
            wait = math.ceil((interval - (now - last.read_at)).total_seconds())
            return _response(
                run, read=False, code=TENANT_EVIDENCE_READ_RECENTLY,
                status=(last.payload or {}).get("status") or status_for(last.code),
                retry_after=max(1, wait), snapshot=last, detail=(
                    f"this run was read from integration '{integ.name}' at "
                    f"{last.read_at.isoformat()} UTC, inside the "
                    f"{settings.CORTEXSIM_TENANT_EVIDENCE_MIN_INTERVAL_SECONDS}-second minimum "
                    "interval (CORTEXSIM_TENANT_EVIDENCE_MIN_INTERVAL_SECONDS); the stored "
                    "snapshot is returned. Pass force=true to read now"))

    since = min(executed) - timedelta(seconds=WINDOW_LEAD_SECONDS)
    ends = await service.match_window_ends(db, run.run_id, results,
                                           window_seconds=DEFAULT_WINDOW_SECONDS)
    until = max(min(max(ends.values()), now) if ends else now, since)
    run_host = await service.resolve_run_host(db, run)
    tenant = service.case_ledger_tenant(cfg)
    read = _Read()

    # 5. The window issue read.
    since_ms, until_ms = _epoch_ms_utc(since), _epoch_ms_utc(until)

    def window_body(search_from: int, page_size: int) -> dict:
        return issue_search_body(
            filters=run_issue_filters(since_ms, until_ms),
            search_from=search_from, search_to=search_from + page_size,
            sort={"field": "observation_time", "keyword": "asc"},
            include_fields=list(ISSUE_INCLUDE_FIELDS))

    wres = await read.call(conn.search_issues, cfg, body_for=window_body,
                           max_pages=settings.CORTEXSIM_TENANT_EVIDENCE_MAX_PAGES,
                           keep_partial=True)
    window_pages = read.last_sent
    issues_section = _section_for(wres, read, "issue")
    issues_failed = issues_section is not None and issues_section["code"] != QUOTA_REFUSED
    window: list[dict] = []
    excluded = duplicates = 0
    if not issues_failed:
        try:
            window, excluded, duplicates = _project_window(list(wres.observations))
        except IssueShapeError as e:
            issues_section, issues_failed = _parse_section(e, "issue"), True

    # 6. Attribution.
    att = attribute(window, run_host)
    unscoped = att["code"] == UNSCOPED
    attributed = att["attributed"]

    # 7. The id probe, over the matcher's alert ids.
    agreement_out: Optional[dict] = None
    pairs: list[dict] = []
    matcher_rows = await _matcher_rows(db, run.run_id, results, run_host)
    if not issues_failed:
        ids = list(dict.fromkeys(str(r["external_id"]).strip() for r in matcher_rows
                                 if r["external_id"] is not None and str(r["external_id"]).strip()))
        pres = await read.call(conn.issues_by_id, cfg, ids,
                               include_fields=ISSUE_INCLUDE_FIELDS) if ids else None
        probe_rows: Optional[list[dict]] = []
        probe_code = OK
        if ids:
            failed = _section_for(pres, read, "issue id")
            if failed is not None:
                probe_rows, probe_code = None, failed["code"]
            else:
                try:
                    probe_rows = [project_issue(r) for r in pres.observations]
                except IssueShapeError:
                    probe_rows, probe_code = None, codes.XSIAM_PARSE_ERROR
        agreement_out = agreement(
            matcher_rows, attributed, probe_rows, run_host,
            tolerance_seconds=settings.CORTEXSIM_TENANT_EVIDENCE_JOIN_TOLERANCE_SECONDS)
        agreement_out["id_space"]["code"] = probe_code
        pairs = agreement_out["pairs"]

    # 8. Cases for the attributed issues.
    cases_section: Optional[dict] = None
    if not issues_failed:
        if unscoped:
            cases_section = {"code": UNSCOPED, "rows": None, "requested": 0,
                             "detail": "no run host, so no issue is attributed and no case is read"}
        else:
            all_ids = list(dict.fromkeys(c for i in attributed for c in i.get("case_ids") or []))
            case_ids = all_ids[:CASE_IDS_MAX]
            base = {"requested": len(case_ids), "ids_capped": len(all_ids) > CASE_IDS_MAX}
            if not case_ids:
                cases_section = {"code": OK, "rows": [], **base}
            else:
                values = [_case_value(c) for c in case_ids]

                def case_body(search_from: int, page_size: int) -> dict:
                    return case_search_body(
                        filters=[{"field": "case_id", "operator": "in", "value": values}],
                        search_from=0, search_to=page_size)

                cres = await read.call(conn.search_cases, cfg, body_for=case_body,
                                       limit=len(case_ids))
                failed = _section_for(cres, read, "case")
                if failed is None:
                    try:
                        rows = group_cases(attributed, dict(enumerate(cres.incidents)))
                        cases_section = {"code": OK, "rows": rows, **base}
                    except IssueShapeError as e:
                        failed = _parse_section(e, "case")
                if failed is not None:
                    # Grouping by issue needs no case read; issue_count is then null.
                    cases_section = {**failed, "rows": group_cases(attributed, None), **base}

    # 9. The run host's endpoint record, as of read_at.
    as_of = now.isoformat()
    if run_host is None:
        endpoint = {"code": UNSCOPED, "as_of": as_of, "record": None,
                    "detail": "no run host, so no endpoint record is read"}
    else:
        eres = await read.call(conn.get_endpoints, cfg, [run_host])
        failed = _section_for(eres, read, "endpoint")
        endpoint = {"as_of": as_of, "record": None}
        if failed is not None:
            endpoint.update(failed)
        else:
            want = _short_host(run_host)
            try:
                records = [project_endpoint(r) for r in eres.observations
                           if _short_host(r.get("endpoint_name")) == want]
            except IssueShapeError as e:
                endpoint.update(_parse_section(e, "endpoint"))
                records = None
            if records is not None:
                endpoint["truncated"] = eres.detail.get("truncated")
                if not records:
                    endpoint.update(code=ENDPOINT_NOT_FOUND, detail=(
                        f"the tenant returned no endpoint record for host '{run_host}' "
                        f"as of {as_of} UTC"))
                elif len(records) > 1:
                    endpoint.update(
                        code=AMBIGUOUS_ENDPOINT_RECORDS,
                        records=[{"endpoint_id": r["endpoint_id"], "last_seen": r["last_seen"]}
                                 for r in records],
                        detail=(f"the tenant holds {len(records)} endpoint records for host "
                                f"'{run_host}'; none is claimed as this run's endpoint"))
                else:
                    endpoint.update(code=OK, record=records[0])

    # The issues section, measured from the rows read (a floor when refused).
    if not issues_failed:
        executed_by_id = {r.id: r.executed_at for r in results}
        detail = (wres.detail if wres is not None else {}) or {}
        issues_section = {
            **(issues_section or {"code": OK}),
            "total_count": detail.get("total_count"),
            "filter_count": detail.get("filter_count"),
            "truncated": detail.get("truncated") if wres is not None and wres.ok else None,
            "pages": window_pages,
            "host_scope": {"host": att["host"],
                           "attributed": None if unscoped else len(attributed),
                           "other_host": att["other_host"],
                           "without_host": len(att["without_host"])},
            "excluded_non_security": excluded,
            "duplicate_rows": duplicates,
            "unrecognised_values": unrecognised_values(window),
            "rows": [_with_lag(i, pairs, executed_by_id) for i in attributed],
            "unattributed_rows": att["unattributed"],
        }
    else:
        issues_section = {**issues_section, "total_count": None, "filter_count": None,
                          "truncated": None, "host_scope": None,
                          "excluded_non_security": None, "unrecognised_values": None,
                          "rows": None, "unattributed_rows": None}

    window_text = f"between {since.isoformat()} and {until.isoformat()} UTC"
    if issues_failed:
        code = issues_section["code"]
        top_detail = (f"the issue read failed ({code}); nothing was measured from it, so this "
                      "says nothing about the run's detections")
    elif read.quota is not None:
        code = QUOTA_REFUSED
        top_detail = ((("CortexSim's own per-tenant request budget" if read.quota["refused_locally"]
                        else "the tenant's rate limit (HTTP 429)")
                       + " refused a request in this read. Rows read before the refusal are "
                       "kept; the read is incomplete and its counts are floors. Retry in "
                       f"{read.quota['retry_after_seconds']}s"))
    elif unscoped:
        code = UNSCOPED
        top_detail = (
            "this run has no resolvable target host (a push run, or its agent record is gone), "
            f"so no issue can be attributed to it; the {len(window)} issue(s) read "
            f"{window_text} are listed as unattributed, not credited")
    elif not attributed:
        code = NO_ATTRIBUTED_ISSUES
        top_detail = (
            f"the tenant returned no Security-domain issue attributed to host '{run_host}' "
            f"{window_text}. Check these first: ingestion latency (issues can take minutes "
            "to appear; read again later), the hostname (the tenant may record this endpoint "
            f"under another name than '{run_host}'), the window (from "
            f"{WINDOW_LEAD_SECONDS} s before the first step to the end of the matcher's "
            "windows) and the Security domain filter (issues in other domains are not read)")
    else:
        code = OK
        top_detail = (f"{len(attributed)} Security-domain issue(s) attributed to host "
                      f"'{run_host}' {window_text}")

    sections = [issues_section, endpoint] + ([cases_section] if cases_section else [])
    section_codes = [s["code"] for s in sections]
    if agreement_out is not None:
        section_codes.append(agreement_out["id_space"]["code"])
    complete = all(c in _ANSWERED for c in section_codes)
    status = "ok" if code == OK and all(c == OK for c in section_codes) else "degraded"
    if code == OK and status != "ok":
        top_detail += "; some sections are degraded, and each carries its own code"

    payload = {
        "reader": conn.reader_paths(cfg),
        "tenant": tenant,
        "integration": integ.name,
        "read_at": now.isoformat(),
        "window": [since.isoformat(), until.isoformat()],
        "queries_issued": read.queries,
        "tenant_answered": read.answered,
        "complete": complete,
        "code": code,
        "status": status,
        "detail": top_detail,
        "issues": issues_section,
        "cases": cases_section,
        "endpoint": endpoint,
        "agreement": agreement_out,
        "tenant_verified": (await tenant_verified(db)).count,
        "statement": STATEMENT,
        "data_notice": DATA_NOTICE,
    }
    row = TenantEvidenceRead(
        run_id=run.run_id, integration=integ.name, tenant=tenant, read_at=now,
        since=since, until=until, code=code, queries_issued=read.queries,
        reader_version=READER_VERSION, payload=payload)
    db.add(row)
    try:
        await db.flush()
        snapshot = row.to_dict()
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return {"run_id": run.run_id, "read": True, "status": status, "code": code,
            "detail": top_detail,
            "retry_after_seconds": read.quota["retry_after_seconds"] if read.quota else None,
            "snapshot": snapshot, "issues": issues_section}


async def latest_tenant_evidence(db: AsyncSession, run_id: str) -> dict:
    """The stored evidence for a run, with no outbound call.

    ``{"run_id", "status", "code", "snapshot", "history", "case_ledger"}``:
    the latest snapshot (any integration), the last ten reads newest first
    (``id``, ``read_at``, ``code``) and Part B's stored case ledger. A run never
    read is ``snapshot: null`` / ``TENANT_EVIDENCE_NOT_READ``.
    """
    rows = await _snapshots(db, run_id)
    run = (await db.execute(select(Run).where(Run.run_id == run_id))).scalar_one_or_none()
    latest = rows[0] if rows else None
    code = latest.code if latest else TENANT_EVIDENCE_NOT_READ
    status = ((latest.payload or {}).get("status") or status_for(code)) if latest else status_for(code)
    return {
        "run_id": run_id,
        "status": status,
        "code": code,
        "snapshot": latest.to_dict() if latest else None,
        "history": [{"id": r.id, "read_at": _iso(r.read_at), "code": r.code} for r in rows],
        "case_ledger": run.case_ledger if run is not None else None,
    }

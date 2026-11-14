"""XSIAM / Cortex XDR results connector.

Pulls alerts from a Cortex XSIAM / XDR tenant via the Public API and normalizes
them to :class:`ObservedAlert` so the matcher can auto-validate a run's expected
detections. This is the connector that closes CortexSim's measurement loop: the
engine puts signal IN (agent/EAL), this reads the resulting alerts OUT.

Auth — the connector supports both Cortex API key modes, selected by
``config["auth_mode"]``:

  * ``"standard"`` (default): the API key is sent verbatim in the ``Authorization``
    header alongside the ``x-xdr-auth-id`` key id.
  * ``"advanced"``: per Cortex's advanced-auth scheme, the request sends a random
    nonce, a millisecond timestamp, and an ``Authorization`` value of
    ``sha256(api_key + nonce + timestamp)`` — the api_key itself never leaves the
    process.

Required integration metadata (``config``): ``fqdn`` (api-<tenant>.xdr.<region>.
paloaltonetworks.com) and ``api_key_id``. The secret is the API key.

Offline-safe: any transport/auth/parse failure returns ``PullResult(ok=False)``.
The HTTP transport is injected, so unit tests drive it with a canned tenant
response and never reach the network.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Sequence

from integrations.xsiam import codes
from integrations.xsiam.auth import advanced_auth_headers, standard_auth_headers
from integrations.xsiam.exceptions import XsiamConfigError
from integrations.xsiam.ledger import (
    QuotaBreakerOpen, RequestBudgetExhausted, ledger, tenant_ledger_key,
)
from integrations.xsiam.queries import endpoint_get_body, issue_search_body
from integrations.xsiam.tenant_url import normalize_tenant_base_url, tenant_endpoint_url

from . import tuning
from .base import (
    Connector,
    ConnectorConfig,
    ConnectorError,
    ObservedAlert,
    ObservedIncident,
    PullResult,
    coerce_utc,
    register_connector,
    _host_only,
)

logger = logging.getLogger("cortexsim.connectors.xsiam")

# Cortex maps its alert severity strings to these; normalize to lowercase.
_SEVERITY_MAP = {
    "SEV_010_INFO": "informational",
    "SEV_020_LOW": "low",
    "SEV_030_MEDIUM": "medium",
    "SEV_040_HIGH": "high",
    "SEV_050_CRITICAL": "critical",
}


class IssueStatusMap(dict):
    """``pull_issue_status`` result: a plain dict plus the read's accounting.

    ``.pages`` is the number of POSTs issued (the caller adds it to
    ``queries_issued`` and the per-sweep caps); ``.detail`` is the merged detail.
    """

    pages: int = 0
    detail: dict

    def __init__(self, *a: Any, pages: int = 0, detail: Optional[dict] = None) -> None:
        super().__init__(*a)
        self.pages = pages
        self.detail = detail or {}


def _clean_ids(raw: Sequence[Any]) -> list[str]:
    """Stripped, non-empty, order-preserving de-duplicated ids."""
    seen: dict[str, None] = {}
    for i in raw:
        t = str(i).strip()
        if t:
            seen.setdefault(t, None)
    return list(seen)


class IssueReadError(ConnectorError):
    """A by-id issue read failed (transport, auth, HTTP, or unrecognised shape).

    ``.code`` is a ``integrations.xsiam.codes`` code; ``.detail`` is the harvest
    detail. Raised instead of returning ``{}``, which would read as "no issues".
    """

    def __init__(self, code: str, message: str, detail: Optional[dict] = None) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.detail = detail or {}


@register_connector
class XsiamConnector(Connector):
    """Read alerts from a Cortex XSIAM / XDR tenant Public API."""

    kind = "xsiam"
    description = "Cortex XSIAM / XDR — pull alerts to auto-validate detections (read-only)."

    # Default Public API paths. Both are overridable per credential
    # (``config["alerts_path"]`` / ``config["incidents_path"]``) because the
    # vendor lists the v1 alerts call as legacy; a tenant on the newer path is
    # a setting, not a patch. Whichever path answered is recorded in
    # ``PullResult.detail["path"]`` and the preflight ``alert_shape`` rung.
    _ALERTS_PATH = "/public_api/v1/alerts/get_alerts_multi_events"
    _INCIDENTS_PATH = "/public_api/v1/incidents/get_incidents"
    #: Issue search (read-only POST). Observed on the lab tenant 2026-11-10: the
    #: envelope is ``reply.DATA`` and the status fields are flat dotted keys.
    _ISSUES_SEARCH_PATH = "/public_api/v1/issue/search/"
    #: Case search (read-only POST), the ``reply.DATA`` envelope; overridable via
    #: ``config["cases_path"]``.
    _CASES_SEARCH_PATH = "/public_api/v1/case/search/"
    #: Endpoint detail (read-only POST); overridable via ``config["endpoints_path"]``.
    _ENDPOINT_PATH = "/public_api/v1/endpoints/get_endpoint"
    #: Tenant licence record (read-only POST): one object under ``reply``.
    _TENANT_INFO_PATH = "/public_api/v1/system/get_tenant_info"
    #: Ids per request body for the by-id reads (the tenant's page ceiling).
    _ID_CHUNK = 100

    #: The tenant's hard page ceiling (search_to - search_from <= 100).
    _PAGE_SIZE_MAX = 100
    #: Default harvest cap per pull: 20 pages x 100 = 2 000 alerts. A window
    #: holding more than that is reported as truncated, never silently cut.
    _DEFAULT_MAX_PAGES = 20

    def pull(
        self,
        cfg: ConnectorConfig,
        *,
        since: datetime,
        until: datetime,
        filters: Optional[dict[str, Any]] = None,
    ) -> PullResult:
        """Alerts in ``[since, until]``, paged, with truncation accounted for."""
        conf = cfg.config or {}
        path = str(conf.get("alerts_path") or self._ALERTS_PATH)
        return self._harvest(cfg, path, since=since, until=until, filters=filters or {},
                             list_key="alerts", normalize=self._normalize_alert,
                             kind="alerts")

    def pull_incidents(
        self,
        cfg: ConnectorConfig,
        *,
        since: datetime,
        until: datetime,
        filters: Optional[dict[str, Any]] = None,
    ) -> PullResult:
        """Incidents created in ``[since, until]``, on ``PullResult.incidents``.

        The fallback basis for the correlation rate when the tenant does not
        stamp ``incident_id`` on alert objects. Same paging, same truncation
        accounting, same error vocabulary as the alert pull.
        """
        conf = cfg.config or {}
        path = str(conf.get("incidents_path") or self._INCIDENTS_PATH)
        return self._harvest(cfg, path, since=since, until=until, filters=filters or {},
                             list_key="incidents", normalize=self._normalize_incident,
                             kind="incidents")

    # ── the one paged harvest both reads share ─────────────────────────

    def _harvest(
        self, cfg: ConnectorConfig, path: str, *, since: Optional[datetime],
        until: Optional[datetime], filters: dict[str, Any], list_key: str,
        normalize: Any, kind: str,
        request_for: Optional[Callable[[int, int], dict]] = None,
        strict: bool = False,
        keep_partial: bool = False,
        parse: Optional[Callable[[str], "tuple[list[Any], int, dict[str, Any]]"]] = None,
    ) -> PullResult:
        # `parse(text)` replaces the list-envelope reader for a reply that is not
        # a list (get_tenant_info's one object); a raise is XSIAM_PARSE_ERROR.
        #
        # `request_for(search_from, page_size)` replaces the alert time-window body
        # for the by-id and tenant-reader reads; everything else (auth, URL
        # validation, the request bucket, error mapping, envelope parsing) is
        # this one path.
        #
        # `strict` is the issue/case `reply.DATA` envelope (spec §7.3): no `data`
        # fallback, and the window is measured by FILTER_COUNT (the filter's
        # match count). TOTAL_COUNT there is tenant-wide, so it never decides
        # truncation; a missing FILTER_COUNT leaves `truncated` null, never False.
        #
        # `keep_partial` (the tenant reader's window read only): a failure after
        # one or more pages still fails the read, but the failed PullResult
        # carries the rows already fetched and `detail["rows_kept"]`, so a caller
        # that reports a floor (spec §7.9) can keep them. Every other caller
        # gets the old contract: a failed read carries no rows.
        conf = cfg.config or {}
        # `base_url` first so ONE registration (the xsiam_tenant spelling) can
        # serve both call paths; `fqdn`/`tenant_url` stay for back-compat with
        # every reconcile credential already in the field.
        fqdn = conf.get("base_url") or conf.get("fqdn") or conf.get("tenant_url")
        api_key_id = conf.get("api_key_id") or conf.get("auth_id")
        if not fqdn or not api_key_id:
            return PullResult(
                ok=False, connector=self.kind, code=codes.XSIAM_CONFIG_ERROR,
                error="integration config missing 'fqdn'/'base_url' and/or 'api_key_id'",
            )

        try:
            base = normalize_tenant_base_url(str(fqdn))
            # The path can be a credential override; it must not move the host.
            url = tenant_endpoint_url(base, path)
        except XsiamConfigError as e:
            # Refusing here is the whole point: without it this connector would
            # POST `Authorization: <raw api key>` in cleartext to whatever host
            # the config named. The tenant client has always validated its URL;
            # this one did not, and both send the same key to the same tenant.
            logger.warning("xsiam pull refused: %s", e)
            return PullResult(ok=False, connector=self.kind,
                              code=codes.XSIAM_CONFIG_ERROR, error=str(e))

        mode = str(conf.get("auth_mode", "standard")).lower()
        # One signer, shared with the tenant client. Two byte-identical copies of
        # a signature scheme is two edits when PANW changes it, and one of them
        # gets missed.
        headers = (advanced_auth_headers(cfg.secret, str(api_key_id))
                   if mode == "advanced"
                   else standard_auth_headers(cfg.secret, str(api_key_id)))

        # An explicit ``limit`` (the preflight probes with 1) is a single page
        # and never a harvest; otherwise page up to ``max_pages`` x 100.
        explicit_limit = "limit" in filters
        page_size = max(1, min(int(filters.get("limit", self._PAGE_SIZE_MAX)),
                               self._PAGE_SIZE_MAX))
        max_pages = 1 if explicit_limit else max(
            1, int(filters.get("max_pages", conf.get("max_pages", self._DEFAULT_MAX_PAGES))))

        items: list[Any] = []
        dropped = 0
        pages = 0

        def failed(res: PullResult) -> PullResult:
            if keep_partial and items:
                (res.incidents if kind in ("incidents", "cases")
                 else res.observations).extend(items)
                res.detail = {**res.detail, "rows_kept": len(items)}
            return res

        total_count: Optional[int] = None
        total_capped = False
        filter_count: Optional[int] = None
        filter_capped = False
        search_from = 0
        while pages < max_pages:
            payload = (request_for(search_from, page_size) if request_for
                       else self._build_request(since, until, filters,
                                                search_from=search_from,
                                                page_size=page_size))
            body = json.dumps(payload).encode("utf-8")
            # One token per page, from the same per-tenant bucket (and the same
            # key) the tenant client spends: two egress points, one budget.
            try:
                ledger.acquire_request(
                    tenant_ledger_key(base),
                    per_minute=tuning.xsiam_max_requests_per_minute())
            except (QuotaBreakerOpen, RequestBudgetExhausted) as e:
                n = max(1, int(e.retry_after_seconds))
                return failed(PullResult(
                    ok=False, connector=self.kind, code=codes.XSIAM_QUOTA_ERROR,
                    error=("CortexSim's own per-tenant request budget refused "
                           + (f"page {pages + 1}; pages 1..{pages} were already "
                              f"fetched from the tenant" if pages else
                              "this request before it was sent to the tenant")
                           + f"; retry in {n}s"),
                    detail={"pages": pages, "path": path, "refused_locally": True,
                            "retry_after_seconds": n}))
            try:
                status, text = self._fetch("POST", url, headers, body, 30.0)
            except Exception as e:  # noqa: BLE001 — offline-safe boundary
                logger.warning("xsiam %s pull transport error: %s", kind, e)
                return failed(PullResult(ok=False, connector=self.kind,
                                         code=codes.XSIAM_TRANSPORT_ERROR, error=str(e),
                                         detail={"page": pages + 1, "path": path}))

            if status != 200:
                # The tenant's response body is attacker-influenceable and
                # unbounded, and this error string is persisted to
                # IntegrationCredential.last_verified_error, which
                # /api/credentials and the console both return. Keep the body in
                # the server log; ship only a status and a digest the operator
                # can correlate with it. A failure on page N>1 fails the WHOLE
                # pull: page 1 alone would read as "the tenant held this many".
                if status == 429:
                    ledger.trip(tenant_ledger_key(base),
                                cooldown_seconds=tuning.xsiam_breaker_cooldown())
                logger.warning("xsiam %s pull HTTP %s from %s (page %d); body=%s",
                               kind, status, _host_only(url), pages + 1, text[:500])
                return failed(PullResult(
                    ok=False, connector=self.kind, code=codes.code_for_status(status),
                    error=f"tenant returned HTTP {status}" + (
                        f" on page {pages + 1}" if pages else ""),
                    detail={"status": status, "page": pages + 1, "path": path,
                            "body_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]},
                ))

            try:
                page_items, page_dropped, meta = (
                    parse(text) if parse is not None
                    else self._parse_page(text, list_key, normalize, strict=strict))
            except Exception as e:  # noqa: BLE001
                # `page`: the page that was sent and could not be read, so a
                # caller counting requests counts it (and every page before it).
                return failed(PullResult(ok=False, connector=self.kind,
                                         code=codes.XSIAM_PARSE_ERROR,
                                         error=f"failed to parse tenant response: {e}",
                                         detail={"page": pages + 1, "path": path}))

            pages += 1
            items.extend(page_items)
            dropped += page_dropped
            if meta.get("total_count") is not None:
                total_count = meta["total_count"]
            total_capped = total_capped or bool(meta.get("total_count_capped"))
            if meta.get("filter_count") is not None:
                filter_count = meta["filter_count"]
            filter_capped = filter_capped or bool(meta.get("filter_count_capped"))
            page_rows = meta.get("result_count") or (len(page_items) + page_dropped)
            search_from += page_rows
            if page_rows < page_size:
                break                                   # short page: the end
            # The count that speaks for this read: FILTER_COUNT on the strict
            # envelope (TOTAL_COUNT there is the whole tenant), else total_count.
            known, capped = ((filter_count, filter_capped) if strict
                             else (total_count, total_capped))
            if known is not None and not capped and search_from >= known:
                break                                   # everything accounted for

        returned = len(items) + dropped
        detail: dict[str, Any] = {
            "queried_host": _host_only(url),
            "path": path,
            "window": ([since.isoformat(), until.isoformat()]
                       if since is not None and until is not None else None),
            "pages": pages,
            "page_size": page_size,
            "returned": returned,
            "total_count": total_count,
        }
        # Truncated means: the tenant told us it holds more than we fetched
        # (or capped its own count, which is the same admission). The number
        # downstream is then a FLOOR on coverage, and every consumer says so.
        truncated: Optional[bool]
        if strict:
            # FILTER_COUNT is the only count that describes this read. Without
            # it nobody knows whether rows were left behind: null, never False.
            detail["filter_count"] = filter_count
            if filter_capped:
                detail["filter_count_capped"] = True
            truncated = (True if filter_capped
                         else None if filter_count is None
                         else filter_count > returned)
        else:
            truncated = bool(total_capped or (total_count is not None
                                              and total_count > returned))
        detail["truncated"] = truncated
        if total_capped:
            detail["total_count_capped"] = True
        if truncated:
            count_key, count, capped = (("filter_count", filter_count, filter_capped)
                                        if strict else
                                        ("total_count", total_count, total_capped))
            detail["truncation"] = {
                count_key: "9,999+" if capped and count is None else count,
                "returned": returned,
                "pages": pages,
                "max_pages": max_pages,
                "consequence": (
                    f"the tenant holds more {kind} in this window than were fetched, "
                    "so any coverage/MTTD derived from this pull is a floor, not a "
                    "measurement — rows sorted after the cut were never seen"),
            }
        if dropped:
            # Visible, not absorbed: a dropped row is one the DC's coverage
            # number will not include, and they must be told why.
            detail["unparseable_timestamps"] = dropped
        if kind in ("incidents", "cases"):
            return PullResult(ok=True, connector=self.kind, incidents=items,
                              dropped=dropped, detail=detail)
        return PullResult(ok=True, connector=self.kind, observations=items,
                          dropped=dropped, detail=detail)


    # ── by-id reads for the run case ledger (read-only) ─────────────────

    def pull_cases(self, cfg: ConnectorConfig, case_ids: Sequence[str]) -> PullResult:
        """Read specific cases by id via ``get_incidents`` ``incident_id_list``.

        Rows land on ``.incidents`` (``_normalize_incident``, ``raw`` kept).
        ``detail["pages"]`` is the number of POSTs issued, one per chunk of 100
        ids. A failed chunk fails the whole read (``ok=False`` + code): a
        partial id set would read as "those cases do not exist". An id the
        tenant does not return is simply absent; the caller decides what that
        means.
        """
        conf = cfg.config or {}
        path = str(conf.get("incidents_path") or self._INCIDENTS_PATH)
        ids = _clean_ids(case_ids)

        def _one(chunk: list[str]) -> PullResult:
            # `search_from` is ignored on purpose: the explicit `limit` below makes
            # this ONE page per chunk, so the window is always [0, len(chunk)).
            def body(search_from: int, page_size: int) -> dict:
                return {"request_data": {
                    "filters": [{"field": "incident_id_list", "operator": "in",
                                 "value": chunk}],
                    "search_from": 0, "search_to": len(chunk)}}
            # explicit `limit` => exactly one page per chunk.
            return self._harvest(cfg, path, since=None, until=None,
                                 filters={"limit": len(chunk)}, list_key="incidents",
                                 normalize=self._normalize_incident, kind="cases",
                                 request_for=body)

        return self._chunked(ids, _one, path)

    def pull_issue_status(
        self, cfg: ConnectorConfig, issue_ids: Sequence[str],
    ) -> "IssueStatusMap":
        """Issue id (string) -> ``{"status", "reason", "comment"}``.

        Read from ``status.progress`` / ``status.resolution_reason`` /
        ``status.resolution_comment`` of :meth:`issues_by_id` (``issue/search``
        filtered by ``id``, ints when the id is numeric, chunks of 100). An id
        the tenant does not return is ABSENT from the map.

        Failure contract: a failed read RAISES :class:`IssueReadError` carrying
        the ``codes.py`` code (``.code``), the message and the harvest detail. It
        never returns ``{}`` for a failure, because ``{}`` reads as "the tenant
        returned no issues". A reply without ``reply.DATA`` (a lowercase
        ``data`` included) and a row whose id or ``status.progress`` is missing
        are unrecognised shapes and also raise.
        """
        res = self.issues_by_id(cfg, issue_ids, normalize=self._normalize_issue_status)
        if not res.ok:
            raise IssueReadError(res.code or codes.XSIAM_API_ERROR,
                                 res.error or "issue read failed", res.detail)
        if res.dropped:
            raise IssueReadError(
                codes.XSIAM_PARSE_ERROR,
                f"{res.dropped} issue row(s) lacked an id or status.progress; the "
                "issue shape is not the one this connector reads. Refusing to "
                "treat them as absent.", res.detail)
        return IssueStatusMap({iid: row for iid, row in res.observations},
                              pages=int(res.detail.get("pages", 0)), detail=res.detail)

    def pull_endpoint_ips(self, cfg: ConnectorConfig, hostname: str) -> Optional[list[str]]:
        """IPs on the tenant's endpoint record for ``hostname``, or None.

        :meth:`get_endpoints` for ``[hostname]``; the reply is
        ``reply.endpoints[]`` and each record's ``ip`` is a list of strings
        (field table and sample reply in
        ``docs/reference/xsiam-platform-apis/05-endpoints-api.md``, itself taken
        from the vendor's Get Endpoint page,
        https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/nxq2mf3ia6wrd-get-endpoint
        -- unverified against a live tenant).

        Exactly ONE request, so the caller counts one query per call whatever
        the outcome. ``None`` means "do not claim anything": the read failed, no
        record's ``endpoint_name`` matched the host, more than one record matched
        it (a stale reinstall record cannot be told from the live one, so the two
        are never unioned into a BRIDGED claim), the result was truncated, or a
        matching record had no ``ip`` list. It is never ``[]`` for a failure.
        """
        want = (hostname or "").strip().lower()
        if not want:
            return None
        res = self.get_endpoints(cfg, [hostname])
        if not res.ok or res.detail.get("truncated"):
            return None
        records = [rec for rec in res.observations
                   if (_s(rec.get("endpoint_name")) or "").lower() == want]
        if len(records) != 1:
            return None                             # none, or ambiguous
        raw = records[0].get("ip")
        if not isinstance(raw, list) or not all(isinstance(i, str) for i in raw):
            return None                             # unrecognised record shape
        return list(dict.fromkeys(raw))

    # ── tenant reader reads (read-only; spec §7.3) ──────────────────────
    #
    # Each is a `request_for` body over `_harvest`, so auth, URL validation, the
    # per-tenant request bucket (one token per page), error mapping and envelope
    # parsing are the one path every other read uses. Rows are the tenant's raw
    # dicts unless a `normalize` is given; projection is `connectors.tenant_reader`'s
    # job, not this one's.

    def search_issues(
        self,
        cfg: ConnectorConfig,
        *,
        body_for: Callable[[int, int], dict],
        limit: Optional[int] = None,
        max_pages: int = 1,
        normalize: Optional[Callable[[dict], Any]] = None,
        keep_partial: bool = False,
    ) -> PullResult:
        """``POST /public_api/v1/issue/search/``, strict ``reply.DATA``.

        ``body_for(search_from, page_size)`` returns each page's body (build it
        with :func:`integrations.xsiam.queries.issue_search_body`, with
        ``search_to = search_from + page_size``). Without ``limit`` the read pages
        by 100 up to ``max_pages``; an explicit ``limit`` is exactly one page of
        that size (1..100), as everywhere else on this connector.

        Rows land on ``.observations``. ``detail`` carries ``total_count``
        (``TOTAL_COUNT``, tenant-wide), ``filter_count`` (``FILTER_COUNT``) and
        ``truncated`` (``filter_count > returned``, or None when ``FILTER_COUNT``
        is missing). A reply without ``DATA`` is ``XSIAM_PARSE_ERROR`` naming
        ``XSIAM_ENVELOPE_UNRECOGNISED``, never zero rows. ``keep_partial``: a
        failure after some pages still fails, and carries the rows already
        fetched (``detail["rows_kept"]``); see :meth:`_harvest`.
        """
        return self._harvest(cfg, self._issues_path(cfg), since=None, until=None,
                             filters=_paging(limit, max_pages), list_key="DATA",
                             normalize=normalize or _raw_row, kind="issues",
                             request_for=body_for, strict=True, keep_partial=keep_partial)

    def issues_by_id(
        self,
        cfg: ConnectorConfig,
        issue_ids: Sequence[str],
        *,
        normalize: Optional[Callable[[dict], Any]] = None,
        include_fields: Optional[Sequence[str]] = None,
    ) -> PullResult:
        """``issue/search`` filtered by ``id`` ``in``, strict ``reply.DATA``.

        Ids are stripped and de-duplicated; ids that are ASCII digits are sent
        as ints. One request per chunk of 100 (``detail["pages"]`` sums them);
        a failed chunk fails the whole read, since a partial id set would read
        as "those issues do not exist". Rows land on ``.observations``. An id the
        tenant does not return is simply absent. No ids, no request.
        ``include_fields`` (the tenant reader's id probe passes
        ``["normalized_fields"]``) adds fields to each row; left None, the body
        is exactly the filters and the page window.
        """
        path = self._issues_path(cfg)
        ids = _clean_ids(issue_ids)
        extra = list(include_fields) if include_fields is not None else None

        def _one(chunk: list[str]) -> PullResult:
            values: list[Any] = [int(i) if i.isascii() and i.isdigit() else i for i in chunk]

            # One page per chunk (explicit `limit`), so `search_from` is ignored.
            def body(search_from: int, page_size: int) -> dict:
                return issue_search_body(
                    filters=[{"field": "id", "operator": "in", "value": values}],
                    search_from=0, search_to=len(chunk), include_fields=extra)
            return self._harvest(cfg, path, since=None, until=None,
                                 filters={"limit": len(chunk)}, list_key="DATA",
                                 normalize=normalize or _raw_row, kind="issues",
                                 request_for=body, strict=True)

        return self._chunked(ids, _one, path)

    def search_cases(
        self,
        cfg: ConnectorConfig,
        *,
        body_for: Callable[[int, int], dict],
        limit: Optional[int] = None,
        max_pages: int = 1,
    ) -> PullResult:
        """``POST /public_api/v1/case/search/``, strict ``reply.DATA``.

        ``body_for`` and the paging rules are :meth:`search_issues`'; build the
        body with :func:`integrations.xsiam.queries.case_search_body`. Rows are
        the raw case dicts and land on ``.incidents``, the case slot every case
        read on this connector uses. ``detail`` carries ``total_count``,
        ``filter_count`` and ``truncated`` as :meth:`search_issues` does.
        """
        return self._harvest(cfg, self._cases_path(cfg), since=None, until=None,
                             filters=_paging(limit, max_pages), list_key="DATA",
                             normalize=_raw_row, kind="cases",
                             request_for=body_for, strict=True)

    def get_endpoints(self, cfg: ConnectorConfig, hostnames: Sequence[str]) -> PullResult:
        """``POST /public_api/v1/endpoints/get_endpoint`` by ``hostname`` ``in``.

        Exactly one request (the tenant returns at most 100 records), in the
        lowercase ``reply.endpoints`` / ``total_count`` / ``result_count``
        envelope; ``truncated`` is ``total_count > returned``, and null (unknown,
        never False) when the tenant sent no ``total_count`` and the page came
        back full. Raw endpoint records land on ``.observations``. No
        hostnames, no request.
        """
        path = self._endpoints_path(cfg)
        names = list(hostnames)
        if not names:
            return PullResult(ok=True, connector=self.kind,
                              detail={"path": path, "pages": 0, "requested": 0})

        # Exactly one page (explicit `limit`), so `search_from` is ignored; a
        # truncated reply is reported in `detail`, never as a full record set.
        def body(search_from: int, page_size: int) -> dict:
            return endpoint_get_body(names, search_from=0, search_to=page_size)

        res = self._harvest(cfg, path, since=None, until=None,
                            filters={"limit": self._PAGE_SIZE_MAX}, list_key="endpoints",
                            normalize=_raw_row, kind="endpoints", request_for=body)
        if (res.ok and res.detail.get("total_count") is None
                and not res.detail.get("total_count_capped")
                and res.detail.get("returned", 0) >= res.detail.get("page_size", self._PAGE_SIZE_MAX)):
            # A full page with no count: records may have been left behind.
            res.detail["truncated"] = None
        return res

    def get_tenant_info(self, cfg: ConnectorConfig) -> PullResult:
        """``POST /public_api/v1/system/get_tenant_info``: the tenant's licence record.

        Exactly one request, through :meth:`_harvest`'s path (auth, URL
        validation, one token from the per-tenant request bucket, error mapping;
        a local refusal is ``XSIAM_QUOTA_ERROR`` with ``refused_locally`` and
        sends nothing). The reply is one object (:func:`parse_object_reply`), not a
        list: it lands on ``detail["tenant_info"]`` as the tenant sent it, with
        ``detail["pages"]`` = 1. Anything but an object under ``reply`` is
        ``XSIAM_PARSE_ERROR``, never an empty record.
        """
        path = self._TENANT_INFO_PATH

        def body(search_from: int, page_size: int) -> dict:
            return {"request_data": {}}

        def parse(text: str) -> "tuple[list[Any], int, dict[str, Any]]":
            return [parse_object_reply(json.loads(text))], 0, {"result_count": 1}

        res = self._harvest(cfg, path, since=None, until=None, filters={"limit": 1},
                            list_key="reply", normalize=None, kind="tenant_info",
                            request_for=body, parse=parse)
        if not res.ok:
            return res
        return PullResult(ok=True, connector=self.kind, detail={
            "queried_host": res.detail.get("queried_host"), "path": path,
            "pages": res.detail.get("pages"), "tenant_info": res.observations[0]})

    # ── the reader's paths, per credential ────────────────────────────────

    def _issues_path(self, cfg: ConnectorConfig) -> str:
        return str((cfg.config or {}).get("issues_path") or self._ISSUES_SEARCH_PATH)

    def _cases_path(self, cfg: ConnectorConfig) -> str:
        return str((cfg.config or {}).get("cases_path") or self._CASES_SEARCH_PATH)

    def _endpoints_path(self, cfg: ConnectorConfig) -> str:
        return str((cfg.config or {}).get("endpoints_path") or self._ENDPOINT_PATH)

    def reader_paths(self, cfg: ConnectorConfig) -> dict[str, str]:
        """The vendor routes the tenant reader reads for this credential: the
        class defaults unless the integration overrides ``issues_path`` /
        ``cases_path`` / ``endpoints_path`` (spec §7.4 ``reader``)."""
        return {"issues": f"POST {self._issues_path(cfg)}",
                "cases": f"POST {self._cases_path(cfg)}",
                "endpoint": f"POST {self._endpoints_path(cfg)}"}

    def _chunked(self, ids: list[str], one: Callable[[list[str]], PullResult],
                 path: str) -> PullResult:
        """Run ``one`` per chunk of ids; first failure wins; pages are summed."""
        merged = PullResult(ok=True, connector=self.kind,
                            detail={"path": path, "pages": 0, "requested": len(ids)})
        for start in range(0, len(ids), self._ID_CHUNK):
            r = one(ids[start:start + self._ID_CHUNK])
            if not r.ok:
                # A locally refused chunk sent no request; a failed one sent one.
                sent = 0 if r.detail.get("refused_locally") else 1
                r.detail = {**r.detail, "pages": merged.detail["pages"] + sent}
                return r
            merged.incidents.extend(r.incidents)
            merged.observations.extend(r.observations)
            merged.dropped += r.dropped
            merged.detail["pages"] += int(r.detail.get("pages", 0))
        return merged

    def _normalize_issue_status(self, i: dict[str, Any]) -> Optional[tuple]:
        """One issue row -> ``(id_str, {status, reason, comment})`` or None."""
        iid = _s(i.get("id"))
        status = alert_key(i, "status.progress")
        if iid is None or status is None:
            return None
        return iid, {
            "status": _s(status),
            "reason": _s(alert_key(i, "status.resolution_reason")),
            "comment": _s(alert_key(i, "status.resolution_comment")),
        }

    # ── request / response shaping ──────────────────────────────────────

    def _build_request(self, since: datetime, until: datetime, filters: dict, *,
                       search_from: int = 0, page_size: Optional[int] = None) -> dict:
        """Cortex get_alerts request: time-window filter + one page window."""
        if page_size is None:
            page_size = max(1, min(int(filters.get("limit", self._PAGE_SIZE_MAX)),
                                   self._PAGE_SIZE_MAX))
        request_data: dict[str, Any] = {
            "filters": [
                {
                    "field": "creation_time",
                    "operator": "gte",
                    "value": _epoch_ms_utc(since),
                },
                {
                    "field": "creation_time",
                    "operator": "lte",
                    "value": _epoch_ms_utc(until),
                },
            ],
            "search_from": int(search_from),
            "search_to": int(search_from) + int(page_size),
            "sort": {"field": "creation_time", "keyword": "asc"},
        }
        return {"request_data": request_data}

    def _parse_alerts(self, text: str) -> "tuple[list[ObservedAlert], int, dict[str, Any]]":
        """Alert-page parser; see :meth:`_parse_page`."""
        return self._parse_page(text, "alerts", self._normalize_alert)

    def _parse_page(self, text: str, list_key: str, normalize: Any, *,
                    strict: bool = False,
                    ) -> "tuple[list[Any], int, dict[str, Any]]":
        """Return ``(rows, dropped, meta)`` for one page of ``list_key``.

        ``dropped`` counts unreadable timestamps. The envelope rules (counts,
        ``strict``) are :func:`parse_reply`'s, which this calls on the parsed
        page.
        """
        return parse_reply(json.loads(text), list_key, normalize, strict=strict)

    def _normalize_alert(self, a: dict[str, Any]) -> Optional[ObservedAlert]:
        """One alert → :class:`ObservedAlert`, or None if its time is unreadable.

        A ``None`` here used to be ``utcnow()``, which produced an MTTD of
        "however long ago the run executed" and failed the customer's 300 s
        threshold on the strength of a timestamp format CortexSim could not read.
        """
        # `_insert_time` (Issues API: "Issue creation timestamp in epoch
        # milliseconds") is LAST, so it only dates a record that carries none
        # of the other three — such a record used to be dropped. Which of these
        # is the time MTTD should use is open (PM-ALR-02); the order of the
        # first three is unchanged on purpose.
        ts = (a.get("detection_timestamp") or a.get("creation_time")
              or a.get("local_insert_ts") or a.get("_insert_time"))
        observed_at = coerce_utc(ts)
        if observed_at is None:
            logger.warning("xsiam alert %s has an unreadable timestamp %r — dropped, "
                           "not dated to now", a.get("alert_id", "?"), ts)
            return None
        techs = self._extract_techniques(a)
        sev_raw = str(a.get("severity", ""))
        severity = _SEVERITY_MAP.get(sev_raw, sev_raw.lower() or None)
        return ObservedAlert(
            source=self.kind,
            observed_at=observed_at,
            external_id=_s(a.get("alert_id") or a.get("internal_id") or a.get("event_id")),
            name=_s(a.get("name") or a.get("alert_name") or a.get("description")),
            severity=severity,
            techniques=techs,
            host=alert_host(a),
            # `matching_service_rule_id` is the field Cortex documents on the
            # alert object; the other three were guesses that never matched.
            # `detection.rule_id` / `detection.method` / `case_ids` are the
            # Issues API's names, read after the alerts-API keys so an alert
            # carrying both keeps the value it had.
            detection_id=_s(a.get("matching_service_rule_id") or a.get("detector_id")
                            or a.get("bioc_id") or a.get("rule_id")
                            or alert_key(a, "detection.rule_id")),
            alert_source=_s(a.get("source") or a.get("alert_source")
                            or alert_key(a, "detection.method")),
            category=_s(a.get("category")),
            incident_id=_s(a.get("incident_id") or a.get("case_id")
                           or alert_key(a, "case_ids")),
            action=_s(a.get("action")),
            action_pretty=_s(a.get("action_pretty")),
            raw=a,
        )

    def _normalize_incident(self, i: dict[str, Any]) -> Optional[ObservedIncident]:
        """One ``get_incidents`` row → :class:`ObservedIncident`.

        ``hosts`` arrive as ``"web-01:aef3..."`` (hostname, colon, endpoint id);
        only the hostname is kept so host scoping compares like with like.
        """
        iid = _s(i.get("incident_id") or i.get("case_id") or i.get("id"))
        if not iid:
            return None
        hosts_raw = i.get("hosts")
        hosts: list[str] = []
        if isinstance(hosts_raw, list):
            for h in hosts_raw:
                name = str(h).split(":", 1)[0].strip()
                if name:
                    hosts.append(name)
        count = i.get("alert_count")
        try:
            alert_count = int(count) if count is not None and not isinstance(count, bool) else 0
        except (TypeError, ValueError):
            alert_count = 0
        return ObservedIncident(
            incident_id=iid,
            created_at=coerce_utc(i.get("creation_time") or i.get("detection_time")),
            name=_s(i.get("incident_name") or i.get("description")),
            severity=_s(i.get("severity")),
            status=_s(i.get("status")),
            alert_count=alert_count,
            hosts=hosts,
            raw=i,
        )

    @staticmethod
    def _extract_techniques(a: dict[str, Any]) -> list[str]:
        """Pull MITRE technique ids from the several shapes Cortex uses."""
        out: list[str] = []
        for key in ("mitre_technique_ids", "mitre_technique_id_and_name", "mitre_techniques"):
            val = a.get(key)
            if isinstance(val, list):
                for item in val:
                    out.extend(_technique_ids(item))
            elif isinstance(val, str):
                out.extend(_technique_ids(val))
        # de-dupe, preserve order
        seen: set[str] = set()
        deduped = []
        for t in out:
            if t not in seen:
                seen.add(t)
                deduped.append(t)
        return deduped


def _epoch_ms_utc(dt: datetime) -> int:
    """Epoch milliseconds for a request bound, read in UTC — never host-local.

    Naive is the storage contract (``Result.executed_at`` / ``utcnow()`` are
    naive UTC), but ``naive.timestamp()`` interprets a naive value as LOCAL
    time. On a host-run SimCore in UTC-7 that moved the alert window seven
    hours into the future and the run's own alerts fell before ``since``. The
    parse side of this leak was closed in ``coerce_utc``; this is the request
    side. Aware values are converted, never stripped.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.replace(microsecond=0).timestamp() * 1000)


def _s(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def alert_host(a: dict[str, Any]) -> Optional[str]:
    """The host the connector reads on one alert record, or None.

    ``host_name``, then ``endpoint_name``, then the first of ``hosts``. The one
    reader :meth:`XsiamConnector._normalize_alert` and the tenant reader's
    agreement join both use, so the two cannot disagree on an alert's host.
    """
    hosts = a.get("hosts")
    return _s(a.get("host_name") or a.get("endpoint_name")
              or (hosts[0] if isinstance(hosts, list) and hosts else None))


def _raise_on_app_error(doc: Any, tail: str) -> None:
    """Raise ``XSIAM_APPLICATION_ERROR`` when ``doc`` marks an application error.

    Checks the top level and the object under ``reply``: a document carrying an
    error in either half is not a success. The tenant's text is neutralised and
    capped by the catalog client's formatter (the one definition) and nothing
    else of the reply is echoed.
    """
    if not isinstance(doc, dict):
        return
    from integrations.xsiam.client import _app_error_text, has_app_error  # noqa: PLC0415

    inner = doc.get("reply") if isinstance(doc.get("reply"), dict) else None
    for err in (inner, doc):
        if has_app_error(err):
            text, _quota = _app_error_text(err)
            raise ConnectorError(
                f"XSIAM_APPLICATION_ERROR: tenant returned an error inside a "
                f"200 response ({text}). {tail}"
            )


def parse_reply(doc: Any, list_key: str,
                normalize: Optional[Callable[[dict], Any]] = None, *,
                strict: bool = False) -> "tuple[list[Any], int, dict[str, Any]]":
    """``(rows, dropped, meta)`` from one parsed tenant document. THE envelope reader.

    :meth:`XsiamConnector._parse_page` (the connector's paged reads) and the
    general tenant routes in ``api/xsiam.py`` (which read through the catalog
    client and wrap its unwrapped reply back as ``{"reply": ...}``) both call
    this, so the two XSIAM call paths read one envelope one way.

    ``normalize`` maps each dict row (default: the raw row); a ``None`` from it
    counts in ``dropped``. ``meta`` carries the tenant's own ``total_count``
    (or the uppercase ``TOTAL_COUNT``), ``FILTER_COUNT`` as ``filter_count``,
    and ``result_count``, so the caller can tell a complete read from a
    truncated one. Cortex caps a count at the string ``"9,999+"``, which is
    read as *capped* — an admission of more, not a number. A missing count is
    None, never 0.

    ``strict`` (the issue/case ``reply.DATA`` reads): a reply without
    ``list_key`` raises ``XSIAM_ENVELOPE_UNRECOGNISED``; there is no fallback
    to a lowercase ``data`` list. An error names only identifier-shaped reply
    keys (:func:`_key_names`), so no tenant free text enters a message SimCore
    returns.
    """
    normalize = normalize or _raw_row
    # XSIAM signals application errors INSIDE a 200 — {"reply": {"err_code":
    # .., "err_msg": ..}} — so a permissions problem, a bad tenant or an
    # expired key arrives looking exactly like a successful query that
    # observed nothing. Reading that as zero alerts turns an auth failure
    # into "the customer's stack detected nothing", which is the manufactured
    # false negative in its most damaging form: it is the number that goes in
    # the report. Raise instead, so the caller degrades to pending.
    _raise_on_app_error(
        doc,
        "This is NOT an absence of alerts — no observation was made, so the "
        "run stays pending rather than being scored.",
    )
    if not isinstance(doc, dict):
        raise ConnectorError(
            f"XSIAM_ENVELOPE_UNRECOGNISED: expected a JSON object, got "
            f"{type(doc).__name__}. Refusing to read it as zero alerts."
        )
    if strict and "reply" not in doc:
        # The issue/case envelope is `{"reply": {"DATA": ...}}`. A bare
        # document is some other envelope (a proxy, a changed contract);
        # reading it as these rows is a guess.
        raise ConnectorError(
            f"XSIAM_ENVELOPE_UNRECOGNISED: the reply carries no 'reply' wrapper "
            f"(keys: {_key_names(doc)}). This is NOT an absence "
            f"of rows — no observation was made."
        )
    reply = doc.get("reply", doc)
    if not isinstance(reply, dict):
        raise ConnectorError(
            f"XSIAM_ENVELOPE_UNRECOGNISED: 'reply' is a "
            f"{type(reply).__name__}, not an object. Refusing to read it as "
            f"zero {list_key}."
        )
    # A reply that names NEITHER list key is not "no rows" — the tenant
    # always sends `alerts` / `incidents` (possibly empty). Defaulting the
    # missing key to [] turned a login wall, a proxy's JSON, or an API
    # change into a successful pull that observed nothing and reported
    # 0 % coverage.
    if list_key in reply:
        alerts = reply[list_key]
    elif strict:
        # The issue/case envelope is uppercase `DATA`. A lowercase `data`
        # here is some other envelope (an older API, a proxy, a changed
        # contract), and reading it as these rows is a guess.
        raise ConnectorError(
            f"XSIAM_ENVELOPE_UNRECOGNISED: the reply carries no '{list_key}' "
            f"(keys: {_key_names(reply)}); a lowercase 'data' "
            f"is not read in its place. This is NOT an absence of rows — no "
            f"observation was made."
        )
    elif "data" in reply:
        alerts = reply["data"]
    else:
        raise ConnectorError(
            f"XSIAM_ENVELOPE_UNRECOGNISED: the reply carries neither '{list_key}' "
            f"nor 'data' (keys: {_key_names(reply)}). This is "
            f"NOT an absence of {list_key} — no observation was made."
        )
    if not isinstance(alerts, list):
        # A dict where a list belongs means the envelope is not what we
        # think it is. Guessing is how `len({"data": []}) == 1` happened.
        raise ConnectorError(
            f"XSIAM_ENVELOPE_UNRECOGNISED: expected a list of {list_key}, got "
            f"{type(alerts).__name__}. Refusing to infer a count from "
            f"an envelope this connector does not recognise."
        )
    out: list[Any] = []
    dropped = 0
    for a in alerts:
        if not isinstance(a, dict):
            continue
        alert = normalize(a)
        if alert is None:
            dropped += 1
            continue
        out.append(alert)
    meta: dict[str, Any] = {}
    total, capped = _count_or_capped(reply["total_count"] if "total_count" in reply
                                     else reply.get("TOTAL_COUNT"))
    meta["total_count"] = total
    meta["total_count_capped"] = capped
    fcount, fcapped = _count_or_capped(reply.get("FILTER_COUNT"))
    meta["filter_count"] = fcount
    meta["filter_count_capped"] = fcapped
    rc = reply.get("result_count")
    meta["result_count"] = rc if isinstance(rc, int) and not isinstance(rc, bool) else None
    return out, dropped, meta


def parse_object_reply(doc: Any) -> dict[str, Any]:
    """The one object under ``reply`` (``get_tenant_info``), or raise.

    The rules :func:`parse_reply` holds for a list hold for an object: an error
    inside a 200 is not a record, and a document that is not ``{"reply": {...}}``
    is ``XSIAM_ENVELOPE_UNRECOGNISED``, never an empty one. A message names the
    error code and identifier-shaped keys only, never tenant text.
    """
    if not isinstance(doc, dict):
        raise ConnectorError(
            f"XSIAM_ENVELOPE_UNRECOGNISED: expected a JSON object, got {type(doc).__name__}")
    reply = doc.get("reply")
    _raise_on_app_error(doc, "This is not a record.")
    if not isinstance(reply, dict):
        raise ConnectorError(
            "XSIAM_ENVELOPE_UNRECOGNISED: expected an object under 'reply', got "
            + (f"a {type(reply).__name__}" if "reply" in doc
               else f"no 'reply' (keys: {_key_names(doc)})"))
    return reply


#: How many reply keys an envelope error names. Only key names shaped like an
#: identifier (``DATA``, ``total_count``, ``detection.method``) are named, at
#: most ``_KEY_CHARS`` long; any other key is counted, never quoted.
_KEYS_SHOWN = 10
_KEY_CHARS = 40
_KEY_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.\-]{0,%d}" % (_KEY_CHARS - 1), re.ASCII)


def _key_names(mapping: dict) -> str:
    """The reply's keys for an error message, bounded. Key names come from the
    tenant (or whatever answered in its place) and the message is SimCore's, so
    only identifier-shaped names are quoted, at most ``_KEYS_SHOWN`` of them;
    the rest (free text, over-long or non-ASCII names, the overflow) are a
    count."""
    names = sorted(k for k in (str(k) for k in mapping) if _KEY_NAME.fullmatch(k))
    shown = names[:_KEYS_SHOWN]
    other = len(mapping) - len(shown)
    text = "[" + ", ".join(f"'{k}'" for k in shown) + "]"
    return text + (f" and {other} other key(s)" if other > 0 else "")


def _raw_row(row: dict[str, Any]) -> dict[str, Any]:
    """Identity normaliser: the tenant reader's reads keep the raw row."""
    return row


def _paging(limit: Optional[int], max_pages: int) -> dict[str, Any]:
    """``_harvest`` paging filters: an explicit ``limit`` is one page of that
    size; otherwise pages of 100, up to ``max_pages``."""
    return {"limit": limit} if limit is not None else {"max_pages": max_pages}


def alert_key(a: dict[str, Any], key: str) -> Any:
    """The value the connector reads for ``key`` on one alert/issue record.

    The one reader both :meth:`XsiamConnector._normalize_alert` and the
    preflight ``alert_shape`` rung use, so preflight cannot credit a key the
    connector reads differently.

    * A dotted key is read as the FLAT property first — the Issues API schema
      names them ``"detection.rule_id"`` / ``"detection.method"`` — then as a
      path into a nested object (``{"detection": {"rule_id": ...}}``).
    * ``case_ids`` (Issues API: "List of case IDs this issue is associated
      with") reads as its element only when it lists exactly one. Several cases
      return None: picking the first would credit the alert to an arbitrary
      case and feed that into the correlation-rate basis.
    """
    if key in a:
        value = a[key]
    elif "." in key:
        head, _, rest = key.partition(".")
        sub = a.get(head)
        return alert_key(sub, rest) if isinstance(sub, dict) else None
    else:
        return None
    if key == "case_ids":
        return value[0] if isinstance(value, list) and len(value) == 1 else None
    return value


# `_ms_to_dt` lived here and disagreed with connectors.base._coerce_dt by up to
# nine hours on the same input. There is now exactly one coercer
# (``connectors.base.coerce_utc``) and this module imports it.

_TECH_RE = __import__("re").compile(r"T\d{4}(?:\.\d{3})?")


def _technique_ids(item: Any) -> list[str]:
    """Every `Txxxx[.yyy]` id in a string like 'T1059.001 - PowerShell, T1003 - ...'.

    ``search`` (first match only) used to lose every technique after the first
    in a comma-joined value — and the second one was the one the step declared.
    """
    return _TECH_RE.findall(str(item))


def _technique_id(item: Any) -> Optional[str]:
    """First `Txxxx[.yyy]` id, kept for callers that want exactly one."""
    ids = _technique_ids(item)
    return ids[0] if ids else None


def _count_or_capped(value: Any) -> "tuple[Optional[int], bool]":
    """``(count, capped)`` from a tenant ``total_count``.

    Ints pass through. Cortex caps the field at ``"9,999+"``; a string ending in
    ``+`` is *capped* — the tenant admits to more than it will count — and its
    digits are the floor. Anything else is unknown (None, not 0).
    """
    if isinstance(value, bool):
        return None, False
    if isinstance(value, int):
        return value, False
    if isinstance(value, str):
        s = value.strip()
        capped = s.endswith("+")
        digits = s.rstrip("+").replace(",", "").strip()
        if digits.isdigit():
            return int(digits), capped
        return None, capped
    return None, False

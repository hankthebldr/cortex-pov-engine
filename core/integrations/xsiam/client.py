# core/integrations/xsiam/client.py
"""Thin async httpx client for the Cortex XSIAM/XDR public API (Standard auth).

The `transport` kwarg lets tests inject httpx.MockTransport so the real client
code runs against a faked network (no live tenant, no new deps).
"""
from __future__ import annotations

import asyncio
import logging
import posixpath
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Optional
from urllib.parse import unquote, urlsplit

import httpx

from .auth import advanced_auth_headers, standard_auth_headers
from .config import AuthMode, XsiamTenantConfig
from .exceptions import (
    XqlRequestShapeInvalid, XqlWriteStageRefused,
    XsiamApiError, XsiamAuthError, XsiamConfigError,
    XsiamQueryError, XsiamQuotaError,
)

logger = logging.getLogger("cortexsim.xsiam")

#: Rows asked for per ``get_query_results`` page. A caller that measures from a
#: page passes this explicitly and checks the page against the same number
#: (``queries.complete_rows``): a page that comes back this full may be a slice
#: of a larger answer. The largest limit the API accepts is open (PM-XQL-03).
XQL_PAGE_LIMIT = 100


# ---------------------------------------------------------------------------
# XQL write-stage refusal (SEC-05) — the ONE guard
# ---------------------------------------------------------------------------
#
# XQL is not read-only. Its `target` stage writes the query's result into a
# dataset or a lookup — `| target type=lookup append=false <name>` REPLACES an
# existing lookup, possibly one the customer's own correlation rules read. The
# API docs this repo carries called XQL "never writes", the catalog labelled
# start_xql_query `read`, and operator free text, the generic operation
# executor, scenario verification_xql and assertion probes all reached it
# uninspected. That is a write path to Cortex, which this product promises not
# to have.
#
# `refuse_xql_write_stages` is the single guard. It is enforced inside
# `XsiamClient.start_xql_query` — the only method that puts a query string on
# the wire (tests/integrations/test_xql_write_stage.py holds that by AST) — and
# `request()` routes the start path through that method. The API routes, the
# operation executor and the verifier runners call it EARLY as well, so a
# refused query costs no credential decrypt and no quota; they are the same
# function, not a second implementation.
#
# Fail-closed lexing: stages are split on `|` outside quoted spans under every
# reading of {comments `//` and `/* */` honoured or not} x {backslash escapes
# honoured or not} x {which characters delimit a quoted span: `"` alone, or `"`
# plus `'` and/or a backtick}. If ANY reading sees a `target` stage, the query
# is refused. A single reading CAN remove a refusal (one that treats a
# character as a delimiter when XQL does not lets a stray `"` inside a backtick
# field name or a '...' literal swallow a real `| target` stage), which is why
# the quote-set dimension exists: every plausible set is tried, and only a
# query that NO reading sees a `target` stage in is sent. An unterminated quote
# or block comment is NOT treated as one.

#: The one spelling of the start path in core/ (drift guard: test_xql_write_stage).
XQL_START_PATH = "/public_api/v1/xql/start_xql_query"

#: Stage keywords that write to the tenant.
XQL_WRITE_STAGES = frozenset({"target"})

_STAGE_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# XqlWriteStageRefused (an XsiamConfigError) is defined in .exceptions and
# imported above, so `from integrations.xsiam.client import XqlWriteStageRefused`
# and the guards module name the same class.


#: The quote-character sets tried; `"` is a string delimiter in every one.
_XQL_QUOTE_SETS: tuple[frozenset[str], ...] = (
    frozenset('"'), frozenset("\"'"), frozenset('"`'), frozenset("\"'`"),
)


def _xql_stages(query: str, *, comments: bool, escapes: bool,
                quotes: frozenset[str] = frozenset('"')) -> list[str]:
    """Split ``query`` into pipe stages under one lexing reading.

    Quoted spans (any character in ``quotes``, closed by the same character)
    and, when ``comments``, comments are replaced by a single space so they can
    neither hide a ``|`` nor glue two tokens together.
    """
    stages: list[str] = []
    cur: list[str] = []
    i, n = 0, len(query)
    while i < n:
        c = query[i]
        if c in quotes:
            j = i + 1
            closed = False
            while j < n:
                if escapes and query[j] == "\\":
                    j += 2
                    continue
                if query[j] == c:
                    closed = True
                    break
                j += 1
            if closed:
                cur.append(" ")
                i = j + 1
            else:
                # An unterminated quote is not a string: everything after it is
                # still code. Treating it as a string would let `"abc | target`
                # hide a stage behind a syntax error XQL might not raise.
                cur.append(" ")
                i += 1
            continue
        if comments and query.startswith("//", i):
            j = query.find("\n", i)
            i = n if j == -1 else j
            cur.append(" ")
            continue
        if comments and query.startswith("/*", i):
            j = query.find("*/", i + 2)
            if j != -1:
                cur.append(" ")
                i = j + 2
                continue
            # Unterminated: not a comment (fail closed) — fall through.
        if c == "|":
            stages.append("".join(cur))
            cur = []
            i += 1
            continue
        cur.append(c)
        i += 1
    stages.append("".join(cur))
    return stages


def refuse_xql_write_stages(query: Any) -> str:
    """Return ``query`` unchanged, or raise :class:`XqlWriteStageRefused`.

    A non-string query is an unrecognised shape and is refused too
    (:class:`XsiamConfigError`) — never read as "nothing to inspect".
    """
    if not isinstance(query, str):
        raise XsiamConfigError(
            f"XQL query must be a string, got {type(query).__name__}; refusing to "
            f"send what cannot be inspected"
        )
    readings = ((comments, escapes, quotes)
                for comments in (True, False)
                for escapes in (True, False)
                for quotes in _XQL_QUOTE_SETS)
    for comments, escapes, quotes in readings:
        for stage in _xql_stages(query, comments=comments, escapes=escapes, quotes=quotes):
            m = _STAGE_WORD_RE.match(stage.lstrip())
            if m and m.group(0).lower() in XQL_WRITE_STAGES:
                raise XqlWriteStageRefused(
                    f"XQL query refused: it contains a `{m.group(0)}` stage, which "
                    f"writes the result into a tenant dataset or lookup (`| target "
                    f"type=lookup append=false <name>` replaces an existing lookup). "
                    f"CortexSim only reads from Cortex. Remove the `{m.group(0)}` "
                    f"stage; a query that merely mentions the word inside a "
                    f"\"string\" is accepted."
                )
    return query


def is_xql_start_path(path: Any) -> bool:
    """True when ``path`` (however spelled) addresses start_xql_query."""
    if not isinstance(path, str):
        return False
    raw = unquote(urlsplit(path.strip()).path or "")
    norm = posixpath.normpath("/" + raw.lstrip("/")).lower()
    return norm == XQL_START_PATH


def _xql_start_body(body: Any) -> tuple[str, Optional[dict[str, Any]], dict[str, Any]]:
    """Unpack a start_xql_query body: ``{"request_data": {"query": str, ...}}``.

    Any other shape is refused — an unrecognised shape must raise, not read as
    a body with no query in it. The refusal is XqlRequestShapeInvalid (an
    XsiamConfigError), the same code guards.xql_query_text raises.
    """
    if not isinstance(body, dict) or set(body) != {"request_data"}:
        raise XqlRequestShapeInvalid(
            "start_xql_query body must be exactly {\"request_data\": {\"query\": ...}}; "
            f"got {type(body).__name__}"
            + (f" with keys {sorted(body)}" if isinstance(body, dict) else "")
        )
    rd = body["request_data"]
    if not isinstance(rd, dict) or not isinstance(rd.get("query"), str):
        raise XqlRequestShapeInvalid(
            "start_xql_query needs request_data.query as a string"
        )
    timeframe = rd.get("timeframe")
    if timeframe is not None and not isinstance(timeframe, dict):
        raise XqlRequestShapeInvalid("start_xql_query request_data.timeframe must be an object")
    extra = {k: v for k, v in rd.items() if k not in ("query", "timeframe")}
    return rd["query"], timeframe, extra


def inspect_operation_body(path: str, body: Any) -> None:
    """Gate for catalog operations tagged ``body-inspected``.

    The operation's ``access_class`` cannot express "read unless the body says
    otherwise", so these ops are inspected here, before dry-run OR live. A
    tagged op whose path this function does not know how to inspect is refused:
    a new tagged op must teach this function its body, not slip through it.
    """
    if is_xql_start_path(path):
        query, _tf, _extra = _xql_start_body(body)
        refuse_xql_write_stages(query)
        return
    raise XsiamConfigError(
        f"operation path {path!r} is tagged body-inspected but no inspector is "
        f"defined for it; refusing rather than sending an uninspected body"
    )


#: Characters of a tenant's ``err_code`` quoted in an error; ``err_msg`` is
#: capped at the tenant reader's ``TEXT_CAP`` (200).
_ERR_CODE_CAP = 64


def _app_error_text(reply: dict) -> tuple[str, bool]:
    """``(text, is_quota)`` for an application error a tenant sent inside a 200.

    ``err_code`` and ``err_msg`` come from the tenant (or whatever answered in
    its place), and the text becomes an ``XsiamError.detail`` that SimCore
    returns. So each is neutralised as tenant text is
    (``connectors.tenant_reader.neutralise_text``: control, bidi, zero-width
    and other invisible characters stripped; the message capped at 200
    characters, the code at 64), and nothing else in the reply is ever echoed:
    a reply with no ``err_msg`` is named by its code alone, because the rest of
    it can carry whole rows. ``is_quota`` reads the raw values: code
    ``XQL_0003``, or "quota" in the message.
    """
    from connectors.tenant_reader import neutralise_text  # noqa: PLC0415

    raw_code, raw_msg = reply.get("err_code"), reply.get("err_msg")
    code = neutralise_text(raw_code)[0] if raw_code not in (None, "") else None
    if code is not None:
        code = code[:_ERR_CODE_CAP]
    msg, cut = (neutralise_text(raw_msg) if raw_msg not in (None, "")
                else (None, False))
    if msg is not None and cut:
        msg += " [cut at 200 characters]"
    if code and msg:
        text = f"{code}: {msg}"
    elif code:
        text = f"{code} (the tenant sent no error message)"
    elif msg:
        text = msg
    else:
        text = "the tenant replied with an error object carrying no err_code and no err_msg"
    quota = raw_code == "XQL_0003" or "quota" in str(raw_msg or "").lower()
    return text, quota


class XsiamClient:
    def __init__(
        self,
        config: XsiamTenantConfig,
        api_key: str,
        *,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        timeout: float = 30.0,
        capabilities: Optional[list[str]] = None,
    ):
        if config.auth_mode not in (AuthMode.standard, AuthMode.advanced):
            raise XsiamConfigError(f"unsupported auth_mode '{config.auth_mode}'")
        self._base = config.base_url.rstrip("/")
        self._api_key = api_key
        self._api_key_id = config.api_key_id
        self._auth_mode = config.auth_mode
        self._transport = transport
        self._timeout = timeout
        self.capabilities = capabilities

    @property
    def base_url(self) -> str:
        return self._base

    def _build_headers(self) -> dict[str, str]:
        # Rebuilt per request: advanced auth signs a fresh nonce + timestamp
        # every call, so headers cannot be cached at construction.
        # `==` not `is`: pydantic may coerce the str-Enum to a plain str.
        if self._auth_mode == AuthMode.advanced:
            return advanced_auth_headers(self._api_key, self._api_key_id)
        return standard_auth_headers(self._api_key, self._api_key_id)

    def _client(self, *, query_id: Optional[str] = None) -> httpx.AsyncClient:
        # Every use of this client is ONE HTTP request (`async with
        # self._client() as c: resp = await c.<verb>(...)`), so charging here is
        # one token per request to the tenant, XQL polls included.
        self._charge_request(query_id=query_id)
        return httpx.AsyncClient(
            base_url=self._base,
            headers=self._build_headers(),
            transport=self._transport,
            timeout=self._timeout,
        )

    def _charge_request(self, *, query_id: Optional[str] = None) -> None:
        from connectors import tuning  # noqa: PLC0415

        from .ledger import (  # noqa: PLC0415
            QuotaBreakerOpen, RequestBudgetExhausted, ledger, tenant_ledger_key,
        )
        try:
            ledger.acquire_request(
                tenant_ledger_key(self._base),
                per_minute=tuning.xsiam_max_requests_per_minute())
        except (QuotaBreakerOpen, RequestBudgetExhausted) as exc:
            n = max(1, int(exc.retry_after_seconds))
            if query_id:
                raise XsiamQuotaError(
                    "CortexSim's own per-tenant request budget refused fetching the "
                    f"results of XQL query {query_id}, which WAS started on the "
                    f"tenant; retry in {n}s ({exc})",
                    retry_after_seconds=n, local=True, query_started=query_id,
                ) from exc
            raise XsiamQuotaError(
                "CortexSim's own per-tenant request budget refused this request "
                f"before it was sent to the tenant; retry in {n}s ({exc})",
                retry_after_seconds=n, local=True,
            ) from exc

    def _tenant_429(self) -> XsiamQuotaError:
        """Open the shared breaker for a tenant 429; the error is marked tripped."""
        from connectors import tuning  # noqa: PLC0415

        from .ledger import ledger, tenant_ledger_key  # noqa: PLC0415
        ledger.trip(tenant_ledger_key(self._base),
                    cooldown_seconds=tuning.xsiam_breaker_cooldown())
        err = XsiamQuotaError("XSIAM rate/quota limit hit (HTTP 429)",
                              upstream_status=429)
        err.tripped = True
        return err

    async def healthcheck(self) -> dict[str, Any]:
        async with self._client() as c:
            resp = await c.get("/public_api/v1/healthcheck")
        data = self._unwrap(resp)
        # Some tenants wrap in {reply: {...}}, others return the object directly.
        return data.get("reply", data) if isinstance(data, dict) else {"status": data}

    async def server_time(self) -> Optional[datetime]:
        """The tenant's CURRENT clock, read from the HTTP ``Date`` response header.

        True clock skew for MTTD needs the tenant's *now*, not the timestamp of
        some ingested event: an old-but-perfectly-synced tenant (one whose most
        recent event is 14 min old) would otherwise read as 14 min of skew. The
        ``Date`` header is on every HTTP response — including a 403 — so this
        needs no XQL and no ingested data, and it does not go through
        ``_unwrap`` (a non-2xx still carries its clock). Returns ``None`` only
        when the response carries no ``Date`` header.
        """
        async with self._client() as c:
            resp = await c.get("/public_api/v1/healthcheck")
        if resp.status_code == 429:
            raise self._tenant_429()
        raw = resp.headers.get("date")
        if not raw:
            return None
        try:
            dt = parsedate_to_datetime(raw)
        except (ValueError, TypeError):
            # A malformed Date (a proxy quirk, a truncated header) is an
            # UNMEASURED clock, not a preflight-aborting error: treat it exactly
            # like a missing header so _probe_clock reports `skipped`.
            return None
        if dt is None:  # older stdlib returned None instead of raising
            return None
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)

    async def ping_via_endpoints(self) -> int:
        """Liveness fallback when /healthcheck is license-gated (403).

        A single get_endpoints call asking for one row — used only to prove the
        key authenticates. NOT the Slice-2 agent-fleet-health feature.
        """
        body = {"request_data": {"search_from": 0, "search_to": 1}}
        async with self._client() as c:
            resp = await c.post("/public_api/v1/endpoints/get_endpoints", json=body)
        data = self._unwrap(resp)
        reply = data.get("reply", data) if isinstance(data, dict) else {}
        return int(reply.get("result_count", 0) or 0)

    def _unwrap(self, resp: httpx.Response) -> Any:
        if resp.status_code == 401:
            raise XsiamAuthError("XSIAM rejected the API key (HTTP 401)")
        if resp.status_code == 429:
            raise self._tenant_429()
        if resp.status_code >= 400:
            raise XsiamApiError(
                f"XSIAM API error (HTTP {resp.status_code})",
                upstream_status=resp.status_code,
            )
        try:
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            raise XsiamApiError("XSIAM returned a non-JSON response") from exc

    @staticmethod
    def _check_app_error(reply: Any) -> Any:
        """Raise on XSIAM's HTTP-200 application-level error envelope.

        XSIAM returns 200 with ``{"err_code","err_msg"}`` for app-level failures
        (bad filter, quota, syntax). A normal read reply is also a dict, so only
        an envelope carrying ``err_code``/``err_msg`` (and not a wrapping ``reply``)
        is treated as an error — ordinary result dicts pass through untouched.
        """
        if (
            isinstance(reply, dict)
            and ("err_code" in reply or "err_msg" in reply)
            and "reply" not in reply
        ):
            err, quota = _app_error_text(reply)
            if quota:
                raise XsiamQuotaError(f"XSIAM quota exceeded: {err}")
            raise XsiamQueryError(f"XSIAM API error: {err}")
        return reply

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Optional[Any] = None,
        params: Optional[dict[str, Any]] = None,
    ) -> Any:
        """Generic Cortex Public API call used by the operation-catalog executor.

        Sends ``json`` verbatim (the caller/operation supplies the
        ``request_data``-shaped body — the client never invents a payload),
        unwraps ``reply``, and surfaces HTTP-200 app-errors via ``_check_app_error``.

        The XQL start path is NOT sent from here: it is delegated to
        :meth:`start_xql_query`, the one sender of a query string, so the
        executor is not a second, unguarded door onto XQL (SEC-05).
        """
        if is_xql_start_path(path):
            if method.upper() != "POST" or params:
                raise XsiamConfigError(
                    "start_xql_query is POST-only and takes no query-string params"
                )
            query, timeframe, extra = _xql_start_body(json)
            return await self.start_xql_query(query, timeframe, extra=extra)
        async with self._client() as c:
            resp = await c.request(method.upper(), path, json=json, params=params)
        data = self._unwrap(resp)
        reply = data.get("reply", data) if isinstance(data, dict) else data
        return self._check_app_error(reply)

    async def start_xql_query(
        self, query: str, timeframe: Optional[dict[str, Any]] = None,
        *, extra: Optional[dict[str, Any]] = None,
    ) -> str:
        """Start an XQL query — the ONLY place a query string reaches the wire.

        ``refuse_xql_write_stages`` runs first, before the request is built, so
        a write stage costs zero transport calls. ``timeframe`` is omitted from
        the body when None (the tenant's default); ``extra`` carries any other
        ``request_data`` keys a catalog-operation caller supplied.
        """
        refuse_xql_write_stages(query)
        request_data: dict[str, Any] = dict(extra or {})
        request_data["query"] = query
        if timeframe is not None:
            request_data["timeframe"] = timeframe
        body = {"request_data": request_data}
        async with self._client() as c:
            resp = await c.post(XQL_START_PATH, json=body)
        data = self._unwrap(resp)
        reply = data.get("reply", data) if isinstance(data, dict) else data
        # XSIAM returns HTTP 200 with an error object ({"err_code": ..., "err_msg": ...})
        # for application-level failures (quota exceeded, bad syntax, etc.).
        # A real query id is always a plain string — a dict reply is always an error.
        if isinstance(reply, dict):
            err, quota = _app_error_text(reply)
            if quota:
                raise XsiamQuotaError(f"XQL quota exceeded: {err}")
            raise XsiamQueryError(f"XSIAM XQL error: {err}")
        if not reply:
            raise XsiamQueryError("XSIAM did not return a query id")
        return str(reply)

    async def get_query_results(self, query_id: str, *,
                                limit: int = XQL_PAGE_LIMIT) -> dict[str, Any]:
        # Public (operator-supplied id): CortexSim cannot know the query was
        # started on this tenant, so a local refusal never claims it was.
        return await self._get_query_results(query_id, limit=limit, started=False)

    async def _get_query_results(self, query_id: str, *, limit: int,
                                 started: bool) -> dict[str, Any]:
        body = {"request_data": {
            "query_id": query_id, "pending_flag": True,
            "limit": limit, "format": "json",
        }}
        async with self._client(query_id=query_id if started else None) as c:
            resp = await c.post("/public_api/v1/xql/get_query_results", json=body)
        data = self._unwrap(resp)
        reply = data.get("reply", data) if isinstance(data, dict) else {}
        # `request()` has always done this; this method did not. A 200 carrying
        # {"err_code": ...} has no `status`, so run_xql's poll loop saw
        # `status is None`, treated it as still-pending, and burned the full
        # max_wait before raising "still None after 30s" — a misleading timeout
        # in place of the syntax/quota error the tenant actually reported.
        return self._check_app_error(reply)

    async def run_xql(
        self, query: str, timeframe: dict[str, Any],
        *, max_wait: float = 30.0, interval: float = 1.5, limit: int = XQL_PAGE_LIMIT,
    ) -> dict[str, Any]:
        """Start a query and poll until SUCCESS or timeout.

        max_wait/interval are deliberately conservative defaults — health
        metrics queries are cheap. Tune if your tenant's XQL latency differs.
        """
        query_id = await self.start_xql_query(query, timeframe)
        # A monotonic deadline, not an accumulator. `waited += interval` with
        # interval=0 (which tests pass, and which a future caller might) never
        # advances, so a permanently-PENDING tenant spun forever.
        deadline = asyncio.get_running_loop().time() + max(0.0, max_wait)
        while True:
            reply = await self._get_query_results(query_id, limit=limit, started=True)
            status = (reply or {}).get("status")
            if status == "SUCCESS":
                return reply
            if status not in ("PENDING", None):
                raise XsiamQueryError(f"XQL query {query_id} returned status {status!r}")
            if asyncio.get_running_loop().time() >= deadline:
                raise XsiamQueryError(
                    f"XQL query {query_id} still {status!r} after {max_wait}s"
                )
            await asyncio.sleep(max(interval, 0.05))

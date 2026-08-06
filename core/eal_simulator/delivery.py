"""
Delivery accounting + error taxonomy for the collector-POST plugin families.

A POV lives or dies on one question: *did the signal actually land in the
collector?* Before this module the emitters answered "yes" for any POST that
did not raise — a 401 from a Broker VM with the wrong ingestion token, a 404
from a mistyped ``/logs/v1/event`` path, or a 302 to a login page (the clients
run with ``follow_redirects=False``) all counted as delivered records and the
step returned ``status="success"``. A DC would demo a green campaign against a
collector that ingested nothing.

So delivery is now accounted explicitly:

  * ``classify_status`` / ``classify_exception`` map an HTTP status or a
    transport exception onto a **stable taxonomy code**. The codes separate
    the three failure classes an operator remediates differently — the
    collector rejected the payload (fix the request/credential/path), the
    network never got there (fix routing/DNS/firewall), or the plugin was
    pointed somewhere wrong (fix the campaign).
  * ``DeliveryLedger`` accumulates attempted-vs-delivered records and bytes
    per step and derives the honest outcome: ``success`` only when every
    attempted record was accepted, ``partial`` when some were, ``error`` when
    none were.
  * ``summarise_step_results`` rolls the per-step ledgers up for the run API,
    so ``GET /api/eal/runs/{id}`` states in one block how much of the campaign
    actually reached a collector.

The taxonomy is duplicated (deliberately, as a small literal table) inside the
offline bundle's ``send.py`` — that script must run on a clean host with no
SimCore import. ``BUNDLE_TAXONOMY_SOURCE`` below is the shared reference and a
test asserts the two stay in step.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Iterable, Optional


# ---------------------------------------------------------------------------
# Taxonomy — stable codes. Never renumber; the UI and the offline bundle's
# results.json both key on these strings.
# ---------------------------------------------------------------------------

DELIVERED = "delivered"

#: Collector answered, but refused the payload. Operator fixes the request.
COLLECTOR_REJECTED = "collector_rejected"
COLLECTOR_UNAUTHORIZED = "collector_unauthorized"
COLLECTOR_THROTTLED = "collector_throttled"
COLLECTOR_REDIRECTED = "collector_redirected"

#: Something answered 200 — but with an HTML document, not an ingest
#: acknowledgement. A log collector never answers a JSON/NDJSON POST with
#: `<html>`. This is the modern enterprise egress-interception shape (ZTNA
#: portals, GlobalProtect/Zscaler/Netskope login interstitials, corporate SSO
#: walls): they do NOT 302, they return 200 with a login page. It is the MORE
#: common interception shape and, before this code, the only one that passed —
#: a campaign against a captive portal reported `delivered, 6/6 records`.
#: `POST /api/shelf/stage` already refuses exactly this condition for staged
#: tool artifacts (an HTML error page saved as linpeas.sh has a perfect digest
#: and runs as a no-op). One codebase, one rule.
COLLECTOR_INTERCEPTED = "collector_intercepted"

#: Collector answered but is itself broken/overloaded. Retry or escalate.
COLLECTOR_ERROR = "collector_error"
COLLECTOR_UNAVAILABLE = "collector_unavailable"

#: The bytes never reached a collector at all. Operator fixes the network.
COLLECTOR_UNREACHABLE = "collector_unreachable"
COLLECTOR_TIMEOUT = "collector_timeout"
COLLECTOR_TLS_ERROR = "collector_tls_error"
TRANSPORT_ERROR = "transport_error"

#: The campaign/plugin itself is wrong — nothing was even attempted.
PLUGIN_MISCONFIGURED = "plugin_misconfigured"
TARGET_NOT_AUTHORISED = "target_not_authorised"

FAILURE_CODES = frozenset({
    COLLECTOR_REJECTED,
    COLLECTOR_UNAUTHORIZED,
    COLLECTOR_THROTTLED,
    COLLECTOR_REDIRECTED,
    COLLECTOR_INTERCEPTED,
    COLLECTOR_ERROR,
    COLLECTOR_UNAVAILABLE,
    COLLECTOR_UNREACHABLE,
    COLLECTOR_TIMEOUT,
    COLLECTOR_TLS_ERROR,
    TRANSPORT_ERROR,
    PLUGIN_MISCONFIGURED,
    TARGET_NOT_AUTHORISED,
})

#: One line of operator-actionable remediation per code. Surfaced in the step
#: detail and in the offline bundle's console summary — a DC in a customer
#: meeting should not have to read Python to know what to fix.
REMEDIATION: dict[str, str] = {
    COLLECTOR_REJECTED: (
        "Collector answered but refused the payload — check the collector URL "
        "path (XSIAM native collector expects /logs/v1/event), the content-type, "
        "and whether the target dataset/HTTP-collector instance exists."
    ),
    COLLECTOR_UNAUTHORIZED: (
        "Collector rejected the credential — check auth_token, auth_header and "
        "auth_scheme against the collector's ingestion contract (XSIAM native "
        "collector wants the raw API key under 'Authorization')."
    ),
    COLLECTOR_THROTTLED: (
        "Collector is rate-limiting — lower burst_count/iterations, raise "
        "sleep_seconds, or enable batch mode to cut POST count."
    ),
    COLLECTOR_REDIRECTED: (
        "Collector answered with a redirect and the simulator does not follow "
        "them (a redirect usually means a proxy/captive login, not ingestion). "
        "Point collector_url at the final ingestion endpoint."
    ),
    COLLECTOR_INTERCEPTED: (
        "The collector answered 200 but returned an HTML page, not a log-ingest "
        "acknowledgement. This is almost always a captive portal, SSO wall or "
        "inspecting proxy standing between SimCore and the collector — the "
        "records were accepted by the interceptor and never reached Cortex. "
        "Verify the collector URL from this host with `curl -i` and confirm the "
        "response is JSON."
    ),
    COLLECTOR_ERROR: (
        "Collector returned a server error — inspect the Broker VM / collector "
        "logs; the records were not ingested."
    ),
    COLLECTOR_UNAVAILABLE: (
        "Collector reported itself unavailable — confirm the Broker VM HTTP "
        "applet is running and accepting the configured port."
    ),
    COLLECTOR_UNREACHABLE: (
        "Nothing answered at the collector address — check DNS, routing and "
        "any firewall between this host and the collector. Run the emitter "
        "from inside the customer network (see the offline bundle)."
    ),
    COLLECTOR_TIMEOUT: (
        "Connection or read timed out — raise request_timeout, or confirm the "
        "path to the collector is not being silently dropped by a firewall."
    ),
    COLLECTOR_TLS_ERROR: (
        "TLS handshake failed — if the customer NGFW MitMs outbound traffic, "
        "leave verify_tls off (default); otherwise trust the collector's CA."
    ),
    TRANSPORT_ERROR: (
        "Transport-level failure before a response was read — see the sample "
        "error for the underlying exception."
    ),
    PLUGIN_MISCONFIGURED: (
        "The step never ran — its params failed validation or the plugin is "
        "not registered. Fix the campaign spec."
    ),
    TARGET_NOT_AUTHORISED: (
        "The collector host is not in the campaign target_allowlist, so the "
        "safety policy refused the step before any bytes were sent."
    ),
}


#: Bytes of the response body worth sniffing for an HTML document. An
#: interceptor's login page announces itself in the first line; anything past
#: this is a doctype hidden behind half a kilobyte of whitespace, which no real
#: portal emits and which is not worth buffering for.
_BODY_SNIFF_BYTES = 512

_HTML_MARKERS = (b"<html", b"<!doctype html", b"<head", b"<body")


def response_evidence(resp: Any) -> tuple[str, bytes]:
    """Extract ``(content_type, body_prefix)`` from an HTTP response.

    THE single place the collector-POST plugins get delivery evidence, so the
    three call sites cannot drift into passing different things — a real
    ``httpx.Response`` always carries both, so in production every 2xx is
    inspected and a captive-portal login page can never be counted as ingested
    records.

    Tolerant of objects lacking either attribute purely so a test double is not
    required to impersonate the whole of httpx. That tolerance is SAFE because
    it is visible: :class:`DeliveryLedger` counts such a POST under
    ``delivered_uninspected`` rather than ``delivered_inspected``, so "we never
    looked" is reported instead of being silently rendered as "we checked and it
    was fine".
    """
    content_type = ""
    headers = getattr(resp, "headers", None)
    if headers is not None:
        try:
            content_type = headers.get("content-type", "") or ""
        except AttributeError:  # pragma: no cover - exotic double
            content_type = ""
    body = getattr(resp, "content", b"") or b""
    if not isinstance(body, bytes):
        body = str(body).encode("utf-8", "replace")
    return content_type, body[:512]


def looks_like_html(content_type: str = "", body_prefix: bytes = b"") -> bool:
    """Is this response an HTML document rather than an ingest acknowledgement?

    Two independent signals, because an interceptor may set either without the
    other: the declared content-type, and the body itself. A log collector never
    answers a JSON/NDJSON POST with an HTML document, so either signal is
    sufficient.
    """
    ctype = (content_type or "").strip().lower()
    if ctype.startswith("text/html") or ctype.startswith("application/xhtml"):
        return True
    if body_prefix:
        head = bytes(body_prefix[:_BODY_SNIFF_BYTES]).lstrip().lower()
        return any(head.startswith(m) or m in head for m in _HTML_MARKERS)
    return False


def classify_status(
    status_code: int,
    *,
    content_type: str = "",
    body_prefix: bytes = b"",
) -> Optional[str]:
    """Map an HTTP status onto a taxonomy code; ``None`` means delivered.

    Only 2xx counts as delivered — and only when the 2xx body is not an HTML
    document. 3xx is explicitly a failure because every collector-POST client in
    this package runs with ``follow_redirects=False``: a redirect is a black
    hole, not an ingest.

    ``content_type`` / ``body_prefix`` are keyword-only and default to empty so
    a caller that has not been wired up yet keeps its previous behaviour rather
    than crashing — but a caller that passes NEITHER is not proving delivery,
    only proving a status code, and ``DeliveryLedger`` records that distinction
    explicitly (see ``content_type_evidence``).
    """
    if 200 <= status_code < 300:
        if looks_like_html(content_type, body_prefix):
            return COLLECTOR_INTERCEPTED
        return None
    if 300 <= status_code < 400:
        return COLLECTOR_REDIRECTED
    if status_code in (401, 403, 407):
        return COLLECTOR_UNAUTHORIZED
    if status_code == 429:
        return COLLECTOR_THROTTLED
    if 400 <= status_code < 500:
        return COLLECTOR_REJECTED
    if status_code == 503:
        return COLLECTOR_UNAVAILABLE
    return COLLECTOR_ERROR


def classify_exception(exc: BaseException) -> str:
    """Map a transport exception onto a taxonomy code.

    Matched on class *name* rather than isinstance so this stays usable when
    the caller's exception came from httpx, urllib or ssl — the offline bundle
    classifies urllib errors with the same vocabulary.
    """
    names = {cls.__name__ for cls in type(exc).__mro__}
    text = str(exc).lower()

    if names & {"TimeoutException", "ConnectTimeout", "ReadTimeout",
                "WriteTimeout", "PoolTimeout", "socket.timeout", "timeout"}:
        return COLLECTOR_TIMEOUT
    if names & {"SSLError", "SSLCertVerificationError", "ConnectError"} and (
        "ssl" in text or "certificate" in text or "tls" in text
    ):
        return COLLECTOR_TLS_ERROR
    if names & {"SSLError", "SSLCertVerificationError"}:
        return COLLECTOR_TLS_ERROR
    if names & {"ConnectError", "ConnectionRefusedError", "URLError",
                "gaierror", "ConnectionError"}:
        return COLLECTOR_UNREACHABLE
    if "timed out" in text:
        return COLLECTOR_TIMEOUT
    return TRANSPORT_ERROR


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class _FailureBucket:
    code: str
    count: int = 0            # failed POSTs
    records: int = 0          # records lost with them
    status_code: Optional[int] = None
    sample_error: Optional[str] = None


@dataclasses.dataclass
class DeliveryLedger:
    """Per-step accounting of what was attempted vs what the collector took."""

    records_attempted: int = 0
    records_delivered: int = 0
    requests_attempted: int = 0
    requests_delivered: int = 0
    bytes_attempted: int = 0
    bytes_delivered: int = 0
    status_counts: dict[int, int] = dataclasses.field(default_factory=dict)
    #: 2xx responses whose content-type/body WAS inspected for the captive-portal
    #: shape, and those where the caller supplied neither signal. A campaign that
    #: reports `delivered` on responses nobody inspected has proved a status
    #: code, not an ingest — and the rollup says so rather than implying the
    #: interception check ran.
    delivered_inspected: int = 0
    delivered_uninspected: int = 0
    _failures: dict[str, _FailureBucket] = dataclasses.field(default_factory=dict)

    # -- recording -------------------------------------------------------

    def record_response(
        self,
        status_code: int,
        *,
        records: int,
        wire_bytes: int,
        content_type: str = "",
        body_prefix: bytes = b"",
    ) -> Optional[str]:
        """Account one POST that got an HTTP response. Returns the failure code
        (``None`` when the collector accepted it).

        Pass ``content_type`` (and ideally the first bytes of the body) so a 200
        carrying a captive-portal login page is classified as
        ``collector_intercepted`` rather than counted as a delivered record.
        """
        self.records_attempted += records
        self.requests_attempted += 1
        self.bytes_attempted += wire_bytes
        self.status_counts[status_code] = self.status_counts.get(status_code, 0) + 1

        code = classify_status(
            status_code, content_type=content_type, body_prefix=body_prefix,
        )
        if code is None:
            if content_type or body_prefix:
                self.delivered_inspected += 1
            else:
                self.delivered_uninspected += 1
            self.records_delivered += records
            self.requests_delivered += 1
            self.bytes_delivered += wire_bytes
            return None
        bucket = self._bucket(code)
        bucket.count += 1
        bucket.records += records
        bucket.status_code = status_code
        return code

    def record_exception(
        self, exc: BaseException, *, records: int, wire_bytes: int,
    ) -> str:
        """Account one POST that never produced a response. Returns the code."""
        self.records_attempted += records
        self.requests_attempted += 1
        self.bytes_attempted += wire_bytes
        # Status 0 is the long-standing "no HTTP response" sentinel in
        # response_status_counts; keep emitting it so existing dashboards and
        # tests that key on it still work.
        self.status_counts[0] = self.status_counts.get(0, 0) + 1

        code = classify_exception(exc)
        bucket = self._bucket(code)
        bucket.count += 1
        bucket.records += records
        if bucket.sample_error is None:
            bucket.sample_error = f"{type(exc).__name__}: {exc}"
        return code

    def _bucket(self, code: str) -> _FailureBucket:
        if code not in self._failures:
            self._failures[code] = _FailureBucket(code=code)
        return self._failures[code]

    # -- reporting -------------------------------------------------------

    @property
    def outcome(self) -> str:
        """``success`` | ``partial`` | ``error`` — the honest step outcome.

        Nothing attempted is ``success`` (a zero-iteration step is not a
        failure); anything attempted and nothing accepted is ``error``.
        """
        if self.records_attempted == 0:
            return "success"
        if self.records_delivered == 0:
            return "error"
        if self.records_delivered < self.records_attempted:
            return "partial"
        return "success"

    @property
    def dominant_failure(self) -> Optional[str]:
        """Code of the failure bucket that lost the most records."""
        if not self._failures:
            return None
        return max(
            self._failures.values(), key=lambda b: (b.records, b.count)
        ).code

    def failure_codes(self) -> list[str]:
        return sorted(self._failures)

    def error_summary(self) -> Optional[str]:
        """One-line ``SimulationResult.error`` string, or None when all landed."""
        code = self.dominant_failure
        if code is None:
            return None
        bucket = self._failures[code]
        parts = [
            f"{code}: {self.records_delivered}/{self.records_attempted} records "
            f"accepted by the collector"
        ]
        if bucket.status_code is not None:
            parts.append(f"last status={bucket.status_code}")
        if bucket.sample_error:
            parts.append(bucket.sample_error)
        return " — ".join(parts)

    @property
    def content_type_evidence(self) -> str:
        """``full`` | ``partial`` | ``absent`` | ``not_applicable``.

        States whether the captive-portal check actually ran on the responses
        this step counted as delivered. ``absent`` means the step's verdict
        rests on the status code alone — an interceptor answering 200 with an
        HTML login page would have been counted as a delivery, so the verdict
        must not be read as proof of ingest.
        """
        inspected = self.delivered_inspected
        uninspected = self.delivered_uninspected
        if inspected + uninspected == 0:
            return "not_applicable"
        if uninspected == 0:
            return "full"
        if inspected == 0:
            return "absent"
        return "partial"

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "content_type_evidence": self.content_type_evidence,
            "delivered_inspected": self.delivered_inspected,
            "delivered_uninspected": self.delivered_uninspected,
            "records_attempted": self.records_attempted,
            "records_delivered": self.records_delivered,
            "requests_attempted": self.requests_attempted,
            "requests_delivered": self.requests_delivered,
            "bytes_attempted": self.bytes_attempted,
            "bytes_delivered": self.bytes_delivered,
            "status_counts": dict(self.status_counts),
            "failures": [
                {
                    "code": b.code,
                    "count": b.count,
                    "records": b.records,
                    "status_code": b.status_code,
                    "sample_error": b.sample_error,
                    "remediation": REMEDIATION.get(b.code, ""),
                }
                for b in sorted(
                    self._failures.values(),
                    key=lambda b: (-b.records, -b.count, b.code),
                )
            ],
        }


# ---------------------------------------------------------------------------
# Run-level rollup — consumed by GET /api/eal/runs/{id}.
# ---------------------------------------------------------------------------


def summarise_step_results(step_results: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Roll per-step ``detail.delivery`` blocks into one campaign verdict.

    Steps from non-collector plugins carry no ``delivery`` block; they are
    counted in ``steps_total`` but never make the campaign look like it
    delivered bytes it did not. ``delivery_verdict`` is the field an operator
    reads: ``delivered`` / ``partial`` / ``not_delivered`` / ``not_applicable``.
    """
    totals = {
        "records_attempted": 0,
        "records_delivered": 0,
        "bytes_attempted": 0,
        "bytes_delivered": 0,
    }
    steps_total = 0
    steps_with_delivery = 0
    failure_codes: dict[str, int] = {}
    inspected = 0
    uninspected = 0

    for result in step_results:
        steps_total += 1
        delivery = (result.get("detail") or {}).get("delivery")
        if not isinstance(delivery, dict):
            continue
        steps_with_delivery += 1
        for key in totals:
            totals[key] += int(delivery.get(key) or 0)
        inspected += int(delivery.get("delivered_inspected") or 0)
        uninspected += int(delivery.get("delivered_uninspected") or 0)
        for failure in delivery.get("failures") or []:
            code = failure.get("code")
            if code:
                failure_codes[code] = failure_codes.get(code, 0) + int(
                    failure.get("records") or 0
                )

    if steps_with_delivery == 0:
        verdict = "not_applicable"
    elif totals["records_attempted"] == 0:
        verdict = "not_applicable"
    elif totals["records_delivered"] == 0:
        verdict = "not_delivered"
    elif totals["records_delivered"] < totals["records_attempted"]:
        verdict = "partial"
    else:
        verdict = "delivered"

    dominant = max(failure_codes, key=lambda c: failure_codes[c]) if failure_codes else None

    # How much of a 'delivered'/'partial' verdict was actually inspected for the
    # captive-portal shape. Reported alongside the verdict so nobody reads a
    # green campaign as proof of ingest when the only evidence was a 2xx.
    if inspected + uninspected == 0:
        evidence = "not_applicable"
    elif uninspected == 0:
        evidence = "full"
    elif inspected == 0:
        evidence = "absent"
    else:
        evidence = "partial"
    evidence_caveat = ""
    if evidence in ("absent", "partial") and verdict in ("delivered", "partial"):
        evidence_caveat = (
            f"{uninspected} accepted response(s) were classified on the HTTP "
            f"status alone — their content-type was not inspected, so a captive "
            f"portal or SSO wall answering 200 with an HTML login page would "
            f"have been counted as a delivery. Confirm the collector answers "
            f"JSON with `curl -i` from this host before reporting ingest."
        )

    return {
        "delivery_verdict": verdict,
        "steps_total": steps_total,
        "steps_with_delivery": steps_with_delivery,
        **totals,
        "failure_codes": dict(sorted(failure_codes.items())),
        "dominant_failure": dominant,
        "remediation": REMEDIATION.get(dominant, "") if dominant else "",
        "content_type_evidence": evidence,
        "evidence_caveat": evidence_caveat,
    }


#: The subset of the taxonomy the offline bundle's ``send.py`` re-implements.
#: ``tests/eal_simulator/test_bundle.py`` asserts the bundle's inline table
#: covers exactly these codes so the two never drift.
BUNDLE_TAXONOMY_SOURCE = frozenset({
    COLLECTOR_REJECTED,
    COLLECTOR_UNAUTHORIZED,
    COLLECTOR_THROTTLED,
    COLLECTOR_REDIRECTED,
    COLLECTOR_ERROR,
    COLLECTOR_UNAVAILABLE,
    COLLECTOR_UNREACHABLE,
    COLLECTOR_TIMEOUT,
    COLLECTOR_TLS_ERROR,
    TRANSPORT_ERROR,
})

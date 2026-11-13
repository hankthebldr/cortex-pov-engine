"""Tenant preflight — "is my connection working?" answered BEFORE the POV.

The gap this closes, stated plainly: a DC could register a Cortex tenant and had
no way to learn whether it worked until a run was already executing in front of
the customer. ``POST /api/xsiam/tenants/{n}/test`` proves the key authenticates
and nothing else; the reconcile credential had no test at all, and its first
exercise was a live ``/reconcile`` mid-POV. Nothing checked alert-read scope,
XQL scope, dataset presence, or clock skew — and role-scoping is the single most
common first-POV failure.

Design rules, all load-bearing:

* **Staged, and every stage runs.** One failed stage does not abort the rest;
  the DC gets the whole picture in one pass. Only an unreachable host short-
  circuits, because every later stage would just repeat the same error.
* **Read-only. Never mutates a run, a result or a verdict.** Preflight is
  diagnosis, not measurement.
* **Quota-cheap.** The default costs at most two tenant calls. Dataset probing
  is opt-in and priced per dataset, because 12 datasets is 12 XQL queries
  against a resource the customer's own analysts share.
* **Every non-ok stage carries a remediation that names the CONSEQUENCE in
  verdict terms.** "403" is a status, not remediation. "Tier-2 verification and
  all assertions will resolve `pending`; reconcile and MTTD still work" is.
* **A green result against an injected transport proves the STAGE LOGIC, not
  the tenant.** ``queries_issued`` is reported so nobody can quote a mocked
  preflight as evidence a tenant was reached: a real run has a non-zero count
  and a mocked one is explicit about where its answers came from.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import httpx

from connectors import socfw as _socfw

logger = logging.getLogger("cortexsim.connectors.preflight")

OK = "ok"
DEGRADED = "degraded"
BLOCKED = "blocked"
SKIPPED = "skipped"

READY = "ready"
NOT_READY = "blocked"

# ── Stage codes. Stable, documented, and the only vocabulary the console shows.
PF_OK = "PF_OK"
PF_CONFIG_INVALID = "PF_CONFIG_INVALID"
PF_NO_INTEGRATION = "PF_NO_INTEGRATION"
PF_CREDENTIAL_UNDECRYPTABLE = "PF_CREDENTIAL_UNDECRYPTABLE"
PF_UNREACHABLE = "PF_UNREACHABLE"
PF_SKIPPED_UNREACHABLE = "PF_SKIPPED_UNREACHABLE"
PF_AUTH_REJECTED = "PF_AUTH_REJECTED"
PF_ALERTS_FORBIDDEN = "PF_ALERTS_FORBIDDEN"
PF_XQL_FORBIDDEN = "PF_XQL_FORBIDDEN"
PF_XQL_NOT_AUTHORISED = "PF_XQL_NOT_AUTHORISED"
PF_DATASET_MISSING = "PF_DATASET_MISSING"
PF_QUOTA_EXHAUSTED = "PF_QUOTA_EXHAUSTED"
PF_CLOCK_SKEW = "PF_CLOCK_SKEW"
PF_UPSTREAM_ERROR = "PF_UPSTREAM_ERROR"
PF_NOT_PROBED = "PF_NOT_PROBED"
PF_ALERT_SHAPE_PARTIAL = "PF_ALERT_SHAPE_PARTIAL"
PF_ALERT_SHAPE_UNMATCHABLE = "PF_ALERT_SHAPE_UNMATCHABLE"
PF_ALERT_SHAPE_UNKNOWN = "PF_ALERT_SHAPE_UNKNOWN"
PF_ALERTS_PATH_MISMATCH = "PF_ALERTS_PATH_MISMATCH"
PF_RESPONSE_LAYER_ABSENT = "PF_RESPONSE_LAYER_ABSENT"
PF_RESPONSE_LAYER_UNREAD = "PF_RESPONSE_LAYER_UNREAD"
PF_RESPONSE_LAYER_SHAPE_UNRECOGNIZED = "PF_RESPONSE_LAYER_SHAPE_UNRECOGNIZED"

#: The one XQL the response_layer stage runs: production closes by the framework
#: over the last day. ``action_actor`` is needed to tell an AI close from an
#: analyst's. The window is the ``timeframe`` argument, never XQL time
#: arithmetic (``current_time()`` maths returns HTTP 500 on deathstar).
RESPONSE_LAYER_XQL = (
    f"dataset = {_socfw.EXECUTION_DATASET} | comp count() as n "
    "by action_taken, execution_mode, action_actor"
)
RESPONSE_LAYER_TIMEFRAME_MS = 86_400_000

#: The two published versions of the alert read. The connector defaults to v1
#: (the vendor lists it as legacy); a field-built MCP server reads v2 against a
#: live tenant. When the configured one errors with a path-shaped status, or is
#: empty for the whole lookback, preflight spends ONE 1-row query on the other.
ALERTS_PATH_V1 = "/public_api/v1/alerts/get_alerts_multi_events"
ALERTS_PATH_V2 = "/public_api/v2/alerts/get_alerts_multi_events"
_ALERTS_PATH_ALTERNATE = {ALERTS_PATH_V1: ALERTS_PATH_V2, ALERTS_PATH_V2: ALERTS_PATH_V1}
#: Statuses that say "no such path here". 401/403/429/5xx say something else
#: (key, role, quota, server) and never trigger the alternate probe.
_PATH_SHAPED_STATUSES = frozenset({400, 404, 405, 410})

#: How far back the alert-shape probe looks for ONE alert to inspect. A tenant
#: with nothing in seven days is reported as *unverified*, not as ready.
ALERT_SHAPE_LOOKBACK_DAYS = 7

#: The keys the reconcile connector reads off a tenant alert, by role. The
#: shape probe reports which one (if any) this tenant's alerts carry, so
#: "will matching work?" is a rung with a code instead of a POV with zero
#: matches. Keep in lock-step with ``connectors/xsiam.py::_normalize_alert``;
#: ``tests/connectors/test_issues_api_fields.py`` asserts every key listed here
#: is one the connector actually reads. Values are read through the same
#: ``xsiam.alert_key`` (flat-then-nested dotted keys; ``case_ids`` only when it
#: lists one case). The Issues API names — ``_insert_time``,
#: ``detection.method``, ``detection.rule_id``, ``case_ids`` — come last.
ALERT_SHAPE_KEYS: dict[str, tuple[str, ...]] = {
    "timestamp": ("detection_timestamp", "creation_time", "local_insert_ts", "_insert_time"),
    "technique": ("mitre_technique_ids", "mitre_technique_id_and_name", "mitre_techniques"),
    "name": ("name", "alert_name", "description"),
    "host": ("host_name", "endpoint_name", "hosts"),
    "source": ("source", "alert_source", "detection.method"),
    "rule_id": ("matching_service_rule_id", "detector_id", "bioc_id", "rule_id",
                "detection.rule_id"),
    # what the agent did about the alert: the prevention outcome cannot be
    # read without it. Not a matching key — but its absence is surfaced.
    "action": ("action_pretty", "action"),
    # informational — decides the correlation-rate basis, never the grade
    "incident": ("incident_id", "case_id", "case_ids"),
}

#: Clock skew beyond this makes MTTD untrustworthy. MTTD is the only KPI the
#: engine measures natively, and it is computed as
#: (tenant alert time) - (SimCore executed_at) — two different clocks. A 30 s
#: skew against a 300 s threshold is a 10 % error in the headline number.
CLOCK_SKEW_WARN_SECONDS = 30


@dataclass
class Stage:
    id: str
    status: str
    code: str
    detail: str = ""
    remediation: Optional[str] = None
    extra: dict[str, Any] = field(default_factory=dict)
    ms: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id, "status": self.status,
                               "code": self.code, "detail": self.detail}
        if self.remediation:
            out["remediation"] = self.remediation
        if self.ms is not None:
            out["ms"] = self.ms
        out.update(self.extra)
        return out


@dataclass
class PreflightReport:
    tenant: Optional[str]
    kind: str
    base_url_host: Optional[str] = None
    stages: list[Stage] = field(default_factory=list)
    queries_issued: int = 0
    capabilities_confirmed: list[str] = field(default_factory=list)
    capabilities_denied: list[str] = field(default_factory=list)

    @property
    def overall(self) -> str:
        if any(s.status == BLOCKED for s in self.stages):
            return BLOCKED
        if any(s.status in (DEGRADED, SKIPPED) for s in self.stages):
            return DEGRADED
        return READY

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant": self.tenant,
            "kind": self.kind,
            # Host only. The full base_url is not a secret, but this response is
            # rendered in a console a customer may be looking at, and there is
            # no reason to widen what it carries.
            "base_url_host": self.base_url_host,
            "overall": self.overall,
            "stages": [s.to_dict() for s in self.stages],
            "queries_issued": self.queries_issued,
            "capabilities_confirmed": list(self.capabilities_confirmed),
            "capabilities_denied": list(self.capabilities_denied),
            # Said in the payload, not only in the docs: a preflight run against
            # an injected transport is a test of this module, not of a tenant.
            "proves": (
                "Stage logic and this tenant's reachability/scope as observed by "
                f"{self.queries_issued} live call(s). A report with "
                "queries_issued=0 was not answered by a tenant."
            ),
        }


# ---------------------------------------------------------------------------
# Reconcile-credential preflight (kind="xsiam") — no XQL, one cheap POST
# ---------------------------------------------------------------------------


def preflight_reconcile(
    config: dict[str, Any], secret: str, *,
    integration_name: Optional[str] = None,
    fetcher: Optional[Callable[..., Any]] = None,
    now: Optional[datetime] = None,
) -> PreflightReport:
    """config -> dns/tls -> auth -> alert-read scope, using ONE tenant call.

    Deliberately mirrors the reconcile connector's own code path rather than
    reimplementing it: a preflight that green-lights a request shape the real
    pull does not use would be worse than no preflight. It asks for
    ``search_to: 1`` so the answer costs the tenant essentially nothing.
    """
    from datetime import timedelta  # noqa: PLC0415

    from integrations.xsiam import codes  # noqa: PLC0415
    from integrations.xsiam.exceptions import XsiamConfigError  # noqa: PLC0415
    from integrations.xsiam.tenant_url import (  # noqa: PLC0415
        EXPECTED_SHAPE, normalize_tenant_base_url,
    )

    from .base import ConnectorConfig, get_connector  # noqa: PLC0415

    report = PreflightReport(tenant=integration_name, kind="xsiam")
    now = now or datetime.utcnow()

    raw_url = config.get("base_url") or config.get("fqdn") or config.get("tenant_url")
    api_key_id = config.get("api_key_id") or config.get("auth_id")

    # ── config ──────────────────────────────────────────────────────────
    if not raw_url or not api_key_id:
        report.stages.append(Stage(
            "config", BLOCKED, PF_CONFIG_INVALID,
            "integration config is missing 'fqdn'/'base_url' and/or 'api_key_id'",
            remediation=("Re-register this credential with the tenant FQDN and the "
                         "API key id (x-xdr-auth-id). Until then every reconcile "
                         "fails and coverage stays at whatever was marked by hand."),
        ))
        _skip_rest(report, ["dns_tls", "auth", "scope_alerts", "alert_shape"])
        return report
    try:
        base = normalize_tenant_base_url(str(raw_url))
    except XsiamConfigError as exc:
        report.stages.append(Stage(
            "config", BLOCKED, PF_CONFIG_INVALID, str(exc),
            remediation=(f"The tenant URL must be {EXPECTED_SHAPE}. This is refused "
                         f"before any request, so the API key was NOT sent anywhere."),
        ))
        _skip_rest(report, ["dns_tls", "auth", "scope_alerts", "alert_shape"])
        return report

    host = base.split("://", 1)[1]
    report.base_url_host = host
    mode = str(config.get("auth_mode", "standard")).lower()
    report.stages.append(Stage(
        "config", OK, PF_OK,
        f"tenant URL matches the Cortex pattern; auth_mode={mode}",
    ))

    # ── one live call: dns + tls + auth + alert-read scope ──────────────
    conn = get_connector("xsiam", fetcher=fetcher)
    if conn is None:  # pragma: no cover — the connector is registered at import
        report.stages.append(Stage("auth", BLOCKED, PF_UPSTREAM_ERROR,
                                   "the xsiam connector is not registered"))
        return report

    ccfg = ConnectorConfig(integration_name=integration_name or "preflight",
                           config=dict(config), secret=secret)
    started = time.monotonic()
    pull = conn.pull(
        ccfg,
        # A one-minute window ending now: we are probing scope, not harvesting.
        since=now - timedelta(minutes=1), until=now,
        filters={"limit": 1},
    )
    elapsed_ms = int((time.monotonic() - started) * 1000)
    if (pull.detail or {}).get("refused_locally"):
        # Raised before any byte left: the tenant answered nothing, so no stage
        # may read ok and nothing is counted as a live call.
        report.stages.append(_local_refusal_stage(
            "dns_tls", pull.error or "refused by CortexSim's own request budget"))
        _skip_rest(report, ["auth", "scope_alerts", "alert_shape"], local=True)
        return report
    report.queries_issued += 1

    if pull.ok:
        report.stages.append(Stage("dns_tls", OK, PF_OK,
                                   f"resolved and TLS-verified {host}", ms=elapsed_ms))
        report.stages.append(Stage("auth", OK, PF_OK,
                                   "the tenant accepted this API key"))
        report.stages.append(Stage(
            "scope_alerts", OK, PF_OK,
            f"get_alerts_multi_events returned {len(pull.observations)} row(s) "
            f"for a 1-row window",
        ))
        report.capabilities_confirmed.append("read_alerts")

        # ── alert_shape: will matching work on THIS tenant's alert objects? ──
        sample = pull.observations[0] if pull.observations else None
        total_count = (pull.detail or {}).get("total_count")
        lookback_refused = False
        if sample is None:
            # Nothing in the last minute (normal). One more 1-row call over a
            # bounded lookback — still not a harvest, and priced as one query.
            probe = conn.pull(ccfg, since=now - timedelta(days=ALERT_SHAPE_LOOKBACK_DAYS),
                              until=now, filters={"limit": 1})
            if (probe.detail or {}).get("refused_locally"):
                lookback_refused = True
            else:
                report.queries_issued += 1
            if probe.ok and probe.observations:
                sample = probe.observations[0]
                total_count = (probe.detail or {}).get("total_count")
        if sample is None:
            configured = _configured_alerts_path(config, conn)
            alt = _ALERTS_PATH_ALTERNATE.get(configured)
            if alt:
                alt_pull = _pull_alternate(conn, ccfg, alt, report,
                                           since=now - timedelta(days=ALERT_SHAPE_LOOKBACK_DAYS),
                                           until=now)
                if (alt_pull.detail or {}).get("refused_locally"):
                    lookback_refused = True
                if alt_pull.ok and alt_pull.observations and not lookback_refused:
                    # The quiet failure: reconcile would "work" and match
                    # nothing. Withdraw the capability claim; the key reads
                    # alerts, just not where reconcile looks.
                    report.capabilities_confirmed.remove("read_alerts")
                    report.stages.append(_path_mismatch_stage(
                        "alert_shape", configured, alt,
                        f"{configured} returned no alert in {ALERT_SHAPE_LOOKBACK_DAYS} days "
                        f"while {alt} returned some",
                        consequence=("Reconcile would read zero alerts and score every "
                                     "seeded detection `missed`: a false negative "
                                     "manufactured by the path, not by the customer's "
                                     "detections.")))
                    return report
        if sample is None and lookback_refused:
            # The window was never (fully) queried: do not claim there is no alert.
            report.stages.append(_local_refusal_stage(
                "alert_shape",
                f"the {ALERT_SHAPE_LOOKBACK_DAYS}-day lookback was not fully read: "
                f"CortexSim's own request budget refused it, so whether the tenant "
                f"holds an alert to inspect is unknown"))
            return report
        report.stages.append(_alert_shape_stage(
            sample, total_count,
            path=(pull.detail or {}).get("path") or config.get("alerts_path")))
        return report

    code = pull.code
    if code == codes.XSIAM_TRANSPORT_ERROR:
        report.stages.append(Stage(
            "dns_tls", BLOCKED, PF_UNREACHABLE, pull.error or "unreachable",
            remediation=codes.remediation_for(codes.XSIAM_TRANSPORT_ERROR), ms=elapsed_ms))
        _skip_rest(report, ["auth", "scope_alerts", "alert_shape"])
        return report

    report.stages.append(Stage("dns_tls", OK, PF_OK,
                               f"reached {host} (the tenant answered)", ms=elapsed_ms))
    if code == codes.XSIAM_AUTH_ERROR:
        report.stages.append(Stage(
            "auth", BLOCKED, PF_AUTH_REJECTED, pull.error or "HTTP 401",
            remediation=codes.remediation_for(codes.XSIAM_AUTH_ERROR)))
        _skip_rest(report, ["scope_alerts", "alert_shape"])
        report.capabilities_denied.append("read_alerts")
        return report

    report.stages.append(Stage("auth", OK, PF_OK,
                               "the tenant did not reject the key itself"))
    status = (pull.detail or {}).get("status")
    if code == codes.XSIAM_QUOTA_ERROR:
        report.stages.append(Stage(
            "scope_alerts", DEGRADED, PF_QUOTA_EXHAUSTED, pull.error or "HTTP 429",
            remediation=codes.remediation_for(codes.XSIAM_QUOTA_ERROR)))
        _skip_rest(report, ["alert_shape"])
        return report
    configured = _configured_alerts_path(config, conn)
    alt = _ALERTS_PATH_ALTERNATE.get(configured)
    if status in _PATH_SHAPED_STATUSES and alt:
        alt_pull = _pull_alternate(conn, ccfg, alt, report,
                                   since=now - timedelta(minutes=1), until=now)
        if alt_pull.ok:
            report.stages.append(_path_mismatch_stage(
                "scope_alerts", configured, alt,
                f"{configured} returned HTTP {status}; {alt} answered",
                consequence=("Every reconcile fails until this is set, so MTTD stays "
                             "unmeasured and verdicts resolve `pending`.")))
            _skip_rest(report, ["alert_shape"])
            return report
    report.stages.append(Stage(
        "scope_alerts", BLOCKED,
        PF_ALERTS_FORBIDDEN if status == 403 else PF_UPSTREAM_ERROR,
        pull.error or "the alert query failed",
        remediation=(
            "This API key's role cannot read alerts. Reconcile will find nothing, "
            "so MTTD stays unmeasured and every affected verdict resolves "
            "`pending` — not `fail`."
            if status == 403 else codes.remediation_for(code)),
    ))
    report.capabilities_denied.append("read_alerts")
    _skip_rest(report, ["alert_shape"])
    return report


# ---------------------------------------------------------------------------
# Tenant-credential preflight (kind="xsiam_tenant") — XQL, datasets, clock
# ---------------------------------------------------------------------------


async def preflight_tenant(
    client: Any, *,
    integration_name: Optional[str] = None,
    base_url_host: Optional[str] = None,
    datasets: Optional[list[str]] = None,
    xql_authorised: bool = True,
    now: Optional[datetime] = None,
    response_layer: bool = True,
) -> PreflightReport:
    """auth -> XQL scope -> (opt-in) dataset presence -> clock skew -> response layer.

    ``response_layer`` adds the read-only SOC Framework detection stage (four
    kinds of tenant read, counted in ``queries_issued``); pass False to omit it.

    ``client`` is injected — an ``XsiamClient`` built over ``httpx.MockTransport``
    in tests, a real one in the field — so every stage's logic is provable
    without a tenant while the code path is the same one production runs.

    ``datasets`` is **opt-in and defaults to none**: each entry costs one XQL
    query. Pass only the datasets the DC's selected scenarios actually name.
    """
    from integrations.xsiam.exceptions import (  # noqa: PLC0415
        XsiamApiError, XsiamAuthError, XsiamError, XsiamQuotaError,
    )

    report = PreflightReport(tenant=integration_name, kind="xsiam_tenant",
                             base_url_host=base_url_host)
    now = now or datetime.utcnow()
    rl_ids = ["response_layer"] if response_layer else []

    # ── auth (also proves dns/tls) ──────────────────────────────────────
    started = time.monotonic()
    try:
        await client.healthcheck()
        report.queries_issued += 1
        report.stages.append(Stage("auth", OK, PF_OK, "/healthcheck returned 200",
                                   ms=int((time.monotonic() - started) * 1000)))
    except XsiamAuthError as exc:
        report.queries_issued += 1
        report.stages.append(Stage(
            "auth", BLOCKED, PF_AUTH_REJECTED, str(exc),
            remediation=("The tenant rejected this API key. Nothing was measured, so "
                         "every verification resolves `pending` — this is not a "
                         "detection failure.")))
        _skip_rest(report, ["scope_xql", "datasets", "clock"] + rl_ids)
        report.capabilities_denied.append("xql")
        return report
    except XsiamQuotaError as exc:
        if _is_local(exc):
            # Our own budget refused before the tenant was contacted: not a
            # reachability finding and not a live call.
            report.stages.append(_local_refusal_stage("auth", str(exc)))
            _skip_rest(report, ["scope_xql", "datasets", "clock"] + rl_ids, local=True)
            return report
        report.queries_issued += 1
        report.stages.append(Stage(
            "auth", DEGRADED, PF_QUOTA_EXHAUSTED, str(exc),
            remediation=("The tenant reported its rate/quota limit on /healthcheck. "
                         "Nothing further was probed.")))
        _skip_rest(report, ["scope_xql", "datasets", "clock"] + rl_ids)
        return report
    except XsiamApiError as exc:
        # 403 on /healthcheck is commonly a licence gate, not a dead tenant. Say
        # so rather than reporting the whole tenant blocked on one endpoint.
        report.queries_issued += 1
        report.stages.append(Stage(
            "auth", DEGRADED, PF_UPSTREAM_ERROR, str(exc),
            remediation=("/healthcheck did not return 200. It is licence-gated on "
                         "some tenants, so this alone is not fatal — the XQL stage "
                         "below is the authoritative scope check.")))
    except Exception as exc:  # noqa: BLE001 — transport
        report.queries_issued += 1
        report.stages.append(Stage(
            "auth", BLOCKED, PF_UNREACHABLE, str(exc),
            remediation=("CortexSim could not reach the tenant (DNS, TLS, or egress "
                         "policy on this host). Every verification resolves `pending`.")))
        _skip_rest(report, ["scope_xql", "datasets", "clock"] + rl_ids)
        return report

    # ── XQL scope ───────────────────────────────────────────────────────
    if not xql_authorised:
        # An authorisation CortexSim itself withholds, not a tenant refusal.
        # Reported as blocked-with-a-cause so nobody reads it as a 403.
        report.stages.append(Stage(
            "scope_xql", BLOCKED, PF_XQL_NOT_AUTHORISED,
            "this tenant is registered for alert read-back only",
            remediation=("Add 'xql' to the tenant's capabilities to authorise "
                         "verification queries. Until then Tier-2 verification and "
                         "all assertions resolve `pending`; reconcile and MTTD "
                         "still work.")))
        _skip_rest(report, ["datasets", "clock"] + rl_ids)
        report.capabilities_denied.append("xql")
        return report

    xql_ok, xql_stage = await _probe_xql(client, report)
    report.stages.append(xql_stage)
    if not xql_ok:
        local_stage = xql_stage.code == PF_NOT_PROBED
        _skip_rest(report, ["datasets", "clock"] + rl_ids, local=local_stage)
        if not local_stage:  # a local refusal says nothing about the key's role
            report.capabilities_denied.append("xql")
        return report
    report.capabilities_confirmed.append("xql")

    # ── dataset presence (opt-in, one query each) ───────────────────────
    if datasets:
        present, missing = [], []
        for name in datasets:
            try:
                await client.run_xql(f"dataset = {name} | limit 1",
                                     {"relativeTime": 3600 * 1000})
                report.queries_issued += 1
                present.append(name)
            except XsiamQuotaError as qexc:
                if _is_local(qexc):
                    st = _local_xql_stage("datasets", qexc, report)
                    st.detail = (f"stopped after {len(present)} of {len(datasets)} "
                                 f"datasets: {qexc}")
                    report.stages.append(st)
                    break
                report.queries_issued += 1
                report.stages.append(Stage(
                    "datasets", DEGRADED, PF_QUOTA_EXHAUSTED,
                    f"stopped after {len(present)} of {len(datasets)} datasets",
                    remediation=("The tenant's query quota is exhausted. The "
                                 "remaining datasets were not probed.")))
                break
            except XsiamError:
                report.queries_issued += 1
                missing.append(name)
        else:
            report.stages.append(_dataset_stage(present, missing))
    else:
        report.stages.append(Stage(
            "datasets", SKIPPED, PF_NOT_PROBED,
            "no datasets requested",
            remediation=("Pass the datasets your selected scenarios name to learn "
                         "whether this tenant ingests them. A verification query "
                         "against a dataset the tenant does not ingest returns zero "
                         "rows, which is indistinguishable from a detection that "
                         "did not fire.")))

    # ── clock skew ──────────────────────────────────────────────────────
    report.stages.append(await _probe_clock(client, report, now))

    # ── response layer (SOC Framework) ──────────────────────────────────
    if response_layer:
        report.stages.append(await _probe_response_layer(client, report, now))
    return report


async def _probe_xql(client: Any, report: PreflightReport) -> "tuple[bool, Stage]":
    """One trivial XQL to prove the key's role carries XQL at all."""
    from integrations.xsiam.exceptions import (  # noqa: PLC0415
        XsiamApiError, XsiamAuthError, XsiamError, XsiamQuotaError,
    )

    started = time.monotonic()
    try:
        await client.run_xql("dataset = xdr_data | limit 1",
                             {"relativeTime": 60 * 1000})
        report.queries_issued += 1
        return True, Stage("scope_xql", OK, PF_OK, "start_xql_query returned a result",
                           ms=int((time.monotonic() - started) * 1000))
    except XsiamQuotaError as exc:
        if _is_local(exc):
            return False, _local_xql_stage("scope_xql", exc, report)
        report.queries_issued += 1
        return False, Stage(
            "scope_xql", DEGRADED, PF_QUOTA_EXHAUSTED, str(exc),
            remediation=("The tenant's XQL quota is exhausted. Verification resolves "
                         "`pending` and retries after the backoff — it is never "
                         "downgraded to `fail` because CortexSim ran out of budget."))
    except (XsiamAuthError, XsiamApiError) as exc:
        report.queries_issued += 1
        return False, Stage(
            "scope_xql", BLOCKED, PF_XQL_FORBIDDEN, str(exc),
            remediation=("This API key's role lacks XQL. Tier-2 verification and all "
                         "assertions will resolve `pending`. Reconcile and MTTD still "
                         "work — you can still measure detection latency."))
    except XsiamError as exc:
        # A query error here is CortexSim's probe, not the customer's content.
        report.queries_issued += 1
        return False, Stage(
            "scope_xql", DEGRADED, PF_UPSTREAM_ERROR, str(exc),
            remediation=("The tenant accepted the request but the probe query failed. "
                         "Verification will resolve `pending`, not `fail`."))
    except Exception as exc:  # noqa: BLE001
        report.queries_issued += 1
        return False, Stage("scope_xql", BLOCKED, PF_UNREACHABLE, str(exc))


def _dataset_stage(present: list[str], missing: list[str]) -> Stage:
    total = len(present) + len(missing)
    if not missing:
        return Stage("datasets", OK, PF_OK, f"present {total}/{total}",
                     extra={"present": present, "missing": []})
    return Stage(
        "datasets", DEGRADED, PF_DATASET_MISSING,
        f"present {len(present)}/{total}",
        remediation=(
            "This tenant does not ingest the dataset(s) listed. Scenarios binding "
            "them CANNOT be proven here — their verification queries resolve "
            "`pending`, not `fail`. Zero rows from an absent dataset is not a "
            "failed detection."),
        extra={"present": present, "missing": missing},
    )


async def _probe_clock(client: Any, report: PreflightReport, now: datetime) -> Stage:
    """Compare the tenant's own clock to SimCore's.

    Not optional. MTTD is ``(tenant alert time) - (SimCore executed_at)`` — two
    clocks — and it is the only KPI the engine measures natively. This codebase
    has already lost nine hours to timestamp handling once (see
    ``connectors.base.coerce_utc``); silent skew is the same failure wearing a
    different hat.
    """
    from integrations.xsiam.exceptions import XsiamError  # noqa: PLC0415

    # TRUE clock skew is (tenant's current time) - (SimCore's current time). It
    # is read from the tenant's HTTP Date header (client.server_time), NOT from
    # an ingested event's _time: a healthy tenant whose most recent event is 14
    # min old is not 14 min skewed, and the old event-age probe reported exactly
    # that false degradation. The Date header needs no XQL and no data, so it is
    # a free liveness call (still counted — it is a real tenant round-trip).
    # Both clocks must be read at the SAME instant. SimCore's local time is
    # captured bracketing the tenant read (NOT the preflight-start `now`, which
    # is stale by however long the earlier XQL-scope poll took — reusing it
    # turns elapsed wall-time into fake skew), and compared against the RTT
    # midpoint so the network round-trip nets out.
    try:
        # Count the live round-trip BEFORE awaiting it: the auth and XQL stages
        # count their attempt whether it succeeds or fails, and a failed clock
        # probe that silently undercounts queries_issued would understate the
        # live-call evidence the operator reads.
        report.queries_issued += 1
        before = datetime.now(timezone.utc)
        tenant_time = await client.server_time()
        after = datetime.now(timezone.utc)
    except (XsiamError, httpx.HTTPError) as exc:
        if _is_local(exc):
            _uncount_local(report, exc)
            return _local_refusal_stage("clock", str(exc))
        return Stage("clock", SKIPPED, PF_NOT_PROBED, str(exc),
                     remediation=("Clock skew could not be measured, so MTTD from this "
                                  "tenant is unverified. It is still reported — treat "
                                  "it as approximate."))
    if tenant_time is None:
        return Stage("clock", SKIPPED, PF_NOT_PROBED,
                     "the tenant's response carried no Date header to read its clock from",
                     remediation=("Clock skew is unmeasured (no Date header), so MTTD "
                                  "from this tenant is approximate. This is rare — any "
                                  "HTTP response normally carries one."))
    local_mid = before + (after - before) / 2
    skew = abs((tenant_time - local_mid).total_seconds())
    if skew <= CLOCK_SKEW_WARN_SECONDS:
        return Stage("clock", OK, PF_OK,
                     f"tenant clock within {skew:.1f}s of SimCore; MTTD is trustworthy",
                     extra={"skew_seconds": round(skew, 1)})
    return Stage(
        "clock", DEGRADED, PF_CLOCK_SKEW,
        f"tenant clock differs from SimCore by {skew:.1f}s",
        remediation=(
            f"MTTD is measured across these two clocks, so every latency figure "
            f"carries up to {skew:.0f}s of error. Sync SimCore's host to NTP (and "
            f"run it in UTC) before quoting MTTD against a threshold."),
        extra={"skew_seconds": round(skew, 1)},
    )


def _alert_shape_stage(sample: Any, total_count: Any, path: Optional[str] = None) -> Stage:
    """Grade ONE sampled alert against the keys the connector reads.

    * no alert to inspect → ``degraded`` / ``PF_ALERT_SHAPE_UNKNOWN``: the
      tenant shape is unverified, which is not the same as verified;
    * neither a technique key nor a name key → ``blocked`` /
      ``PF_ALERT_SHAPE_UNMATCHABLE``: nothing the matcher keys on exists, so a
      run against this tenant can only ever read 0 % — and that 0 % would be
      reported as the customer's coverage;
    * no rule-id key → ``degraded`` / ``PF_ALERT_SHAPE_PARTIAL``: matching rests
      on technique and name only;
    * otherwise ``ok``.
    The rung never touches the earlier ones: auth and scope stay green when
    they were green — this is about the OBJECT, not the connection.
    """
    if sample is None:
        return Stage(
            "alert_shape", DEGRADED, PF_ALERT_SHAPE_UNKNOWN,
            f"no alert in the last {ALERT_SHAPE_LOOKBACK_DAYS} days to inspect — "
            f"the tenant's alert shape is unverified",
            remediation=("Raise one alert on the tenant (any BIOC firing will do) and "
                         "re-run preflight. Until an alert has been inspected, whether "
                         "reconcile can match anything is an assumption, not a check."),
            extra={"keys": {role: None for role in ALERT_SHAPE_KEYS},
                   "source": None, "total_count": total_count, "alerts_path": path},
        )
    from .xsiam import alert_key  # noqa: PLC0415 — the connector's own reader

    raw = sample.raw if isinstance(getattr(sample, "raw", None), dict) else {}
    keys: dict[str, Optional[str]] = {}
    for role, candidates in ALERT_SHAPE_KEYS.items():
        keys[role] = next((k for k in candidates
                           if alert_key(raw, k) not in (None, "", [])), None)
    extra: dict[str, Any] = {
        "keys": keys,
        "source": getattr(sample, "alert_source", None),
        "total_count": total_count,
        "alerts_path": path,
        # Which correlation-rate basis this tenant will get: exact (incident id
        # on the alert) or the host-scoped incident fallback.
        "correlation_basis": ("alert_incident_ids" if keys.get("incident") else "host_incidents"),
        "keys_seen": sorted(str(k) for k in raw)[:60],
    }
    found = ", ".join(f"{r}={k}" for r, k in keys.items() if k) or "none of the expected keys"
    if keys["technique"] is None and keys["name"] is None:
        return Stage(
            "alert_shape", BLOCKED, PF_ALERT_SHAPE_UNMATCHABLE,
            f"the sampled alert carries neither a MITRE technique key nor a name "
            f"key (found: {found})",
            remediation=("The matcher correlates on technique, rule id or name; this "
                         "tenant's alert objects expose none of them under the keys "
                         "CortexSim reads. Reconcile would report 0 % coverage and "
                         "that number would be wrong. Compare `keys_seen` with "
                         "ALERT_SHAPE_KEYS and add the tenant's field names before "
                         "running a POV against it."),
            extra=extra,
        )
    if keys["action"] is None:
        return Stage(
            "alert_shape", DEGRADED, PF_ALERT_SHAPE_PARTIAL,
            f"the sampled alert carries no action key (action_pretty / action); "
            f"detection matching is unaffected but a prevention outcome cannot be "
            f"read (found: {found})",
            remediation=("Without the alert's action, a prevent expectation can never "
                         "be credited as prevented. Confirm the tenant's alert objects "
                         "expose `action_pretty` or `action` before running a "
                         "prevention POV."),
            extra=extra,
        )
    if keys["rule_id"] is None:
        return Stage(
            "alert_shape", DEGRADED, PF_ALERT_SHAPE_PARTIAL,
            f"the sampled alert carries no rule-id key; matching will rest on "
            f"technique and name only (found: {found})",
            remediation=("Without a rule id, a step's expected detections can only be "
                         "matched on MITRE technique or on a two-token name overlap, "
                         "so distinct rules on one technique are indistinguishable. "
                         "Acceptable for a POV; read every `matched_on` before quoting "
                         "per-detection coverage."),
            extra=extra,
        )
    return Stage("alert_shape", OK, PF_OK,
                 f"the sampled alert carries every key the matcher reads ({found})",
                 extra=extra)


def _configured_alerts_path(config: dict[str, Any], conn: Any) -> str:
    """The path the reconcile pull actually uses: the credential's override, else the default."""
    return str(config.get("alerts_path") or getattr(conn, "_ALERTS_PATH", ALERTS_PATH_V1))


def _pull_alternate(conn: Any, ccfg: Any, path: str, report: PreflightReport, *,
                    since: datetime, until: datetime) -> Any:
    """ONE 1-row read on the other alerts version. Counted, never retried."""
    from .base import ConnectorConfig  # noqa: PLC0415

    alt_cfg = ConnectorConfig(integration_name=ccfg.integration_name,
                              config={**(ccfg.config or {}), "alerts_path": path},
                              secret=ccfg.secret)
    res = conn.pull(alt_cfg, since=since, until=until, filters={"limit": 1})
    if not (res.detail or {}).get("refused_locally"):
        report.queries_issued += 1
    return res


def _path_mismatch_stage(stage_id: str, configured: str, working: str,
                         detail: str, *, consequence: str) -> Stage:
    return Stage(
        stage_id, BLOCKED, PF_ALERTS_PATH_MISMATCH, detail,
        remediation=(f"Set `alerts_path` to `{working}` on this integration credential "
                     f"and re-run preflight. {consequence} Preflight does not change "
                     f"the path itself."),
        extra={"configured_path": configured, "working_path": working},
    )


# ---------------------------------------------------------------------------
# response_layer — is the Palo-Cortex SOC Framework on this tenant?
# ---------------------------------------------------------------------------

# List-content path. povkit (internal/tenant/api.go) only ever reads list NAMES
# and deletes lists; it has no list-content read, and the public XSOAR REST docs
# (xsoar.pan.dev "Demisto REST API") show only /lists/save. The path below is
# the XSOAR server's list download route (GET /lists/download/{name}) behind the
# same /xsoar gateway prefix povkit's other reads use. It is UNVERIFIED against a
# live tenant. A 404/405 here is therefore read as "path not available", which
# yields PRESENT_UNREAD, never ABSENT.
_LIST_CONTENT_PATH = "/xsoar/lists/download/{name}"
_PACKS_PATH = "/xsoar/contentpacks/metadata/installed"
_LIST_NAMES_PATH = "/xsoar/lists/names"


def _http_status(exc: Exception) -> Optional[int]:
    """The tenant's HTTP status behind an XsiamError, when there was one."""
    from integrations.xsiam.exceptions import XsiamAuthError, XsiamQuotaError  # noqa: PLC0415

    if _is_local(exc):
        return None  # CortexSim's own refusal: the tenant sent no status
    status = getattr(exc, "upstream_status", None)
    if isinstance(status, int):
        return status
    if isinstance(exc, XsiamAuthError):
        return 401
    if isinstance(exc, XsiamQuotaError):
        return 429
    return None


def _list_refusal(name: str, exc: Exception) -> str:
    """Why one list read failed, with the HTTP status (spec §8: 403 vs 404 matter)."""
    status = _http_status(exc)
    if status is None:
        return f"list {name}: {exc}"
    if status in (401, 403):
        why = "the xsiam_tenant key's role may not read lists"
    elif status in (404, 405):
        why = "the list-content path is not available on this tenant (path unverified)"
    elif status == 429:
        why = "rate or quota limit"
    else:
        why = "tenant refused the read"
    return f"list {name}: HTTP {status} ({why})"


async def _read_list(client: Any, name: str) -> tuple[Optional[Any], Optional[str]]:
    """Read one tenant list's content as a JSON document.

    Returns ``(document, None)``, or ``(None, reason)`` when the tenant refused the
    read; the reason names the HTTP status, because a 403 (role) and a 404/405
    (path not available) need different fixes and both read as PRESENT_UNREAD,
    never ABSENT. A reply that arrives but is not a JSON object raises
    ``ResponseLayerShapeError`` (tolerance hides bugs).
    """
    import json  # noqa: PLC0415

    from integrations.xsiam.exceptions import XsiamError  # noqa: PLC0415

    try:
        reply = await client.request("GET", _LIST_CONTENT_PATH.format(name=name))
    except XsiamError as exc:
        if _is_local(exc):
            raise  # never read as the tenant's refusal; the caller accounts for it
        reason = _list_refusal(name, exc)
        logger.info("response_layer: %s", reason)
        return None, reason
    if isinstance(reply, (str, bytes)):
        try:
            reply = json.loads(reply)
        except ValueError as exc:
            raise _socfw.ResponseLayerShapeError(
                f"list {name}: content is not JSON") from exc
    if not isinstance(reply, dict):
        raise _socfw.ResponseLayerShapeError(
            f"list {name}: expected a JSON object, got {type(reply).__name__}")
    return reply, None


async def _probe_response_layer(client: Any, report: PreflightReport,
                                now: datetime) -> Stage:
    """Detect the SOC Framework with read-only calls. Every call is counted.

    Order: installed packs, list names, one read per ``CONFIG_LISTS`` entry (only
    when the core pack and both lists are present), one XQL (only when the core
    pack is present). A shape this module does not recognise yields state
    UNKNOWN with ``PF_RESPONSE_LAYER_SHAPE_UNRECOGNIZED``; it is never read as
    ABSENT or as defaults.
    """
    from integrations.xsiam.exceptions import XsiamError, XsiamQuotaError  # noqa: PLC0415
    from integrations.xsiam.queries import result_rows  # noqa: PLC0415

    started = time.monotonic()
    sources: list[str] = []
    packs = names = tenant_config = evidence = None
    shape_error: Optional[str] = None
    # One entry per read that did not complete, each naming the read and its own
    # outcome: a local refusal and a tenant refusal in one stage are two facts.
    problems: list[str] = []
    # The local remediation for each read CortexSim's own request budget refused.
    local_fixes: list[str] = []
    tenant_reads: list[str] = []  # packs / list-names reads the tenant refused or failed

    # 1. installed packs
    try:
        report.queries_issued += 1
        packs = _socfw.parse_installed_packs(await client.request("GET", _PACKS_PATH))
        sources.append("installed_packs")
    except _socfw.ResponseLayerShapeError as exc:
        shape_error = str(exc)
    except XsiamError as exc:
        _uncount_local(report, exc)
        _note_local(local_fixes, exc)
        if not _is_local(exc):
            tenant_reads.append("installed-packs")
        problems.append(f"installed packs: {exc}")
    except Exception as exc:  # noqa: BLE001 — transport
        tenant_reads.append("installed-packs")
        problems.append(f"installed packs: {exc}")

    # 2. list names (a leftover-lists signal, so read even if packs failed)
    try:
        report.queries_issued += 1
        names = _socfw.parse_list_names(await client.request("GET", _LIST_NAMES_PATH))
        sources.append("list_names")
    except _socfw.ResponseLayerShapeError as exc:
        shape_error = shape_error or str(exc)
        packs = None  # a shape we cannot read: neither present nor absent
    except Exception as exc:  # noqa: BLE001 — XsiamError or transport
        _uncount_local(report, exc)
        _note_local(local_fixes, exc)
        if not _is_local(exc):
            tenant_reads.append("list-names")
        problems.append(f"list names: {exc}")

    core = packs is not None and _socfw.CORE_PACK_ID in packs
    if core and names is not None and all(n in names for n in _socfw.CONFIG_LISTS) \
            and shape_error is None:
        # 3. list content, both documents or neither
        docs = []
        refusals: list[str] = []
        try:
            for list_name in _socfw.CONFIG_LISTS:
                report.queries_issued += 1
                doc, refusal = await _read_list(client, list_name)
                docs.append(doc)
                if refusal:
                    refusals.append(refusal)
            if all(d is not None for d in docs):
                tenant_config = _socfw.parse_tenant_config(docs[0], docs[1])
                sources.append("list_content")
            if tenant_config is None and shape_error is None:
                problems.append(
                    "list content (SOCFWTenantConfig / SOCFWConfigModes) was not readable"
                    + (": " + "; ".join(refusals) if refusals else ""))
        except _socfw.ResponseLayerShapeError as exc:
            shape_error = str(exc)
        except Exception as exc:  # noqa: BLE001 — transport mid-read
            _uncount_local(report, exc)
            _note_local(local_fixes, exc)
            problems.append(f"list content read failed: {exc}")

    if core and shape_error is None:
        # 4. execution evidence
        reply = None
        try:
            report.queries_issued += 1
            reply = await client.run_xql(RESPONSE_LAYER_XQL,
                                         {"relativeTime": RESPONSE_LAYER_TIMEFRAME_MS})
        except XsiamQuotaError as qexc:
            _uncount_local(report, qexc)
            _note_local(local_fixes, qexc)
            problems.append(
                f"XQL read refused by CortexSim's own request budget: {qexc}"
                if _is_local(qexc) else "XQL read: quota exhausted")
        except Exception as exc:  # noqa: BLE001 — XsiamError or transport
            problems.append(f"XQL read failed: {exc}")
            logger.info("response_layer: execution evidence unreadable: %s", exc)
        if reply is not None:
            # Any failure unwrapping or summarising a reply that ARRIVED is a
            # shape problem, whatever its message (no substring matching).
            try:
                evidence = _socfw.summarize_execution_rows(result_rows(reply))
                sources.append("xql_evidence")
            except Exception as exc:  # noqa: BLE001
                shape_error = f"XQL read: {exc}"

    if shape_error is not None:
        packs = None
    read_problem = "; ".join(problems) or None
    layer = _socfw.assemble_response_layer(
        packs=packs, list_names=names, tenant_config=tenant_config, evidence=evidence,
        sources_used=sources, read_at=now, shape_error=shape_error)
    ms = int((time.monotonic() - started) * 1000)
    extra = {"response_layer": layer.to_dict()}

    if layer.state == "ABSENT":
        return Stage(
            "response_layer", DEGRADED, PF_RESPONSE_LAYER_ABSENT,
            "the SOC Framework core pack is not installed on this tenant"
            + (" (its config lists remain)" if layer.leftover_lists else ""),
            remediation=("Every POV tenant is expected to run the SOC Framework. Without "
                         "it, tenant disposition cannot be explained, and runs are not "
                         "comparable to a tenant that has it. Install it, or record why "
                         "this tenant is the exception."),
            ms=ms, extra=extra)
    if shape_error is not None:
        return Stage(
            "response_layer", DEGRADED, PF_RESPONSE_LAYER_SHAPE_UNRECOGNIZED, shape_error,
            remediation=(f"A reply did not match the shape pinned for framework "
                         f"{_socfw.FRAMEWORK_PINNED_VERSION}. CortexSim will not guess: "
                         f"the response layer is reported UNKNOWN."),
            ms=ms, extra=extra)
    if layer.state == "UNKNOWN":
        # Only a failed installed-packs read leaves the layer UNKNOWN; list names
        # is read regardless, so either read may be the one the tenant refused.
        tenant_fix = None
        if "installed-packs" in tenant_reads or not local_fixes:
            tenant_fix = ("The installed-packs read was refused or failed, so neither "
                          "present nor absent can be claimed. Check the role of the "
                          "xsiam_tenant API key: it needs Marketplace / installed-packs "
                          "read.")
        elif "list-names" in tenant_reads:
            tenant_fix = ("The tenant refused or failed the list-names read (see "
                          "detail). Check the role of the xsiam_tenant API key: it "
                          "needs list read.")
        return Stage(
            "response_layer", DEGRADED, PF_RESPONSE_LAYER_UNREAD,
            read_problem or "the installed-packs read failed",
            remediation=_combined_remediation(tenant_fix, local_fixes),
            ms=ms, extra=extra)
    if layer.state == "PRESENT" and not read_problem:
        # Name every Auto Triage value that was assumed or inferred, not read.
        return Stage("response_layer", OK, PF_OK,
                     f"SOC Framework {layer.framework_version} present; Auto Triage "
                     f"{_socfw.auto_triage_summary(extra['response_layer']['auto_triage'])}",
                     ms=ms, extra=extra)
    return Stage(
        "response_layer", DEGRADED, PF_RESPONSE_LAYER_UNREAD,
        read_problem or "framework present, its config could not be read",
        remediation=_combined_remediation(
            "A read against the SOC Framework did not complete (see detail), so "
            "part of its state is unknown. Disposition is shown for context only.",
            local_fixes),
        ms=ms, extra=extra)


def _note_local(local_fixes: list[str], exc: BaseException) -> None:
    """Record the local remediation for a read CortexSim's own budget refused.

    A refused result poll of a started XQL query gets the poll text: that query
    WAS sent, so "refused before it was sent" would be false for it.
    """
    from integrations.xsiam.codes import LOCAL_POLL_REFUSAL_REMEDIATION  # noqa: PLC0415

    if not _is_local(exc):
        return
    fix = (LOCAL_POLL_REFUSAL_REMEDIATION if getattr(exc, "query_started", None)
           else _LOCAL_REMEDIATION())
    if fix not in local_fixes:
        local_fixes.append(fix)


def _combined_remediation(tenant_fix: Optional[str],
                          local_fixes: list[str]) -> Optional[str]:
    """The tenant's fix and the local-refusal remediation(s), each when it applies.

    A stage can hold both: one read refused by CortexSim's own budget, another
    refused by the tenant. Each needs its own fix, so neither text replaces the
    other.
    """
    parts = [p for p in (tenant_fix, *local_fixes) if p]
    return " ".join(parts) or None


def _is_local(exc: BaseException) -> bool:
    """True when CortexSim's own budget refused before the tenant was contacted."""
    return bool(getattr(exc, "local", False))


def _local_unsent(exc: BaseException) -> bool:
    """A local refusal of a call that never left (no query was started)."""
    return _is_local(exc) and not getattr(exc, "query_started", None)


def _uncount_local(report: "PreflightReport", exc: BaseException) -> None:
    """Take back a ``queries_issued`` charged before a call that never left.

    A result poll refused after its query was started on the tenant DID reach the
    tenant (the start), so that one stays counted.
    """
    if _local_unsent(exc) and report.queries_issued > 0:
        report.queries_issued -= 1


def _local_refusal_stage(stage_id: str, detail: str) -> "Stage":
    """A stage the tenant never saw: CortexSim's own budget refused the call."""
    from integrations.xsiam.codes import LOCAL_REFUSAL_REMEDIATION  # noqa: PLC0415

    return Stage(stage_id, DEGRADED, PF_NOT_PROBED, detail,
                 remediation=LOCAL_REFUSAL_REMEDIATION)


def _LOCAL_REMEDIATION() -> str:
    from integrations.xsiam.codes import LOCAL_REFUSAL_REMEDIATION  # noqa: PLC0415

    return LOCAL_REFUSAL_REMEDIATION


def _local_xql_stage(stage_id: str, exc: BaseException,
                     report: "PreflightReport") -> "Stage":
    """The stage for a locally refused XQL call, counting only what left.

    Refused before the start: nothing left, nothing counted. Refused on a result
    poll: the start reached the tenant and is counted, and the stage says the
    query was started and only the fetch of its results was refused.
    """
    from integrations.xsiam.codes import LOCAL_POLL_REFUSAL_REMEDIATION  # noqa: PLC0415

    if getattr(exc, "query_started", None):
        report.queries_issued += 1
        return Stage(stage_id, DEGRADED, PF_NOT_PROBED, str(exc),
                     remediation=LOCAL_POLL_REFUSAL_REMEDIATION)
    return _local_refusal_stage(stage_id, str(exc))


def _skip_rest(report: PreflightReport, stage_ids: list[str], *,
               local: bool = False) -> None:
    """Record the stages that were not attempted, rather than omitting them.

    An absent stage reads as "fine"; an explicit ``skipped`` reads as "unknown".
    Those are different answers and the DC needs the second one.
    """
    for sid in stage_ids:
        if local:
            # The earlier stage was not probed because CortexSim's own request
            # budget refused it, not because the tenant blocked anything.
            report.stages.append(Stage(
                sid, SKIPPED, PF_NOT_PROBED,
                "not attempted — an earlier stage was not probed (CortexSim's own "
                "request budget refused it)"))
        else:
            report.stages.append(Stage(
                sid, SKIPPED, PF_SKIPPED_UNREACHABLE,
                "not attempted — an earlier stage blocked"))

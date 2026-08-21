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
from datetime import datetime
from typing import Any, Callable, Optional

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
        _skip_rest(report, ["dns_tls", "auth", "scope_alerts"])
        return report
    try:
        base = normalize_tenant_base_url(str(raw_url))
    except XsiamConfigError as exc:
        report.stages.append(Stage(
            "config", BLOCKED, PF_CONFIG_INVALID, str(exc),
            remediation=(f"The tenant URL must be {EXPECTED_SHAPE}. This is refused "
                         f"before any request, so the API key was NOT sent anywhere."),
        ))
        _skip_rest(report, ["dns_tls", "auth", "scope_alerts"])
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

    started = time.monotonic()
    pull = conn.pull(
        ConnectorConfig(integration_name=integration_name or "preflight",
                        config=dict(config), secret=secret),
        # A one-minute window ending now: we are probing scope, not harvesting.
        since=now - timedelta(minutes=1), until=now,
        filters={"limit": 1},
    )
    elapsed_ms = int((time.monotonic() - started) * 1000)
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
        return report

    code = pull.code
    if code == codes.XSIAM_TRANSPORT_ERROR:
        report.stages.append(Stage(
            "dns_tls", BLOCKED, PF_UNREACHABLE, pull.error or "unreachable",
            remediation=codes.remediation_for(codes.XSIAM_TRANSPORT_ERROR), ms=elapsed_ms))
        _skip_rest(report, ["auth", "scope_alerts"])
        return report

    report.stages.append(Stage("dns_tls", OK, PF_OK,
                               f"reached {host} (the tenant answered)", ms=elapsed_ms))
    if code == codes.XSIAM_AUTH_ERROR:
        report.stages.append(Stage(
            "auth", BLOCKED, PF_AUTH_REJECTED, pull.error or "HTTP 401",
            remediation=codes.remediation_for(codes.XSIAM_AUTH_ERROR)))
        _skip_rest(report, ["scope_alerts"])
        report.capabilities_denied.append("read_alerts")
        return report

    report.stages.append(Stage("auth", OK, PF_OK,
                               "the tenant did not reject the key itself"))
    status = (pull.detail or {}).get("status")
    if code == codes.XSIAM_QUOTA_ERROR:
        report.stages.append(Stage(
            "scope_alerts", DEGRADED, PF_QUOTA_EXHAUSTED, pull.error or "HTTP 429",
            remediation=codes.remediation_for(codes.XSIAM_QUOTA_ERROR)))
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
) -> PreflightReport:
    """auth -> XQL scope -> (opt-in) dataset presence -> clock skew.

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
        _skip_rest(report, ["scope_xql", "datasets", "clock"])
        report.capabilities_denied.append("xql")
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
        _skip_rest(report, ["scope_xql", "datasets", "clock"])
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
        _skip_rest(report, ["datasets", "clock"])
        report.capabilities_denied.append("xql")
        return report

    xql_ok, xql_stage = await _probe_xql(client, report)
    report.stages.append(xql_stage)
    if not xql_ok:
        _skip_rest(report, ["datasets", "clock"])
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
            except XsiamQuotaError:
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
    from integrations.xsiam.queries import result_rows  # noqa: PLC0415

    from .base import coerce_utc  # noqa: PLC0415

    try:
        reply = await client.run_xql(
            "dataset = xdr_data | fields _time | limit 1", {"relativeTime": 3600 * 1000})
        report.queries_issued += 1
    except XsiamError as exc:
        return Stage("clock", SKIPPED, PF_NOT_PROBED, str(exc),
                     remediation=("Clock skew could not be measured, so MTTD from this "
                                  "tenant is unverified. It is still reported — treat "
                                  "it as approximate."))
    rows = result_rows(reply)
    tenant_time = coerce_utc(rows[0].get("_time")) if rows else None
    if tenant_time is None:
        return Stage("clock", SKIPPED, PF_NOT_PROBED,
                     "the tenant returned no timestamped row to compare against",
                     remediation=("No event in the last hour, so skew is unmeasured. "
                                  "Re-run preflight after the first scenario executes."))
    skew = abs((tenant_time - now).total_seconds())
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


def _skip_rest(report: PreflightReport, stage_ids: list[str]) -> None:
    """Record the stages that were not attempted, rather than omitting them.

    An absent stage reads as "fine"; an explicit ``skipped`` reads as "unknown".
    Those are different answers and the DC needs the second one.
    """
    for sid in stage_ids:
        report.stages.append(Stage(
            sid, SKIPPED, PF_SKIPPED_UNREACHABLE,
            "not attempted — an earlier stage blocked"))

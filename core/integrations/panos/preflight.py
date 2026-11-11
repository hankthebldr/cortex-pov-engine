# core/integrations/panos/preflight.py
"""Staged, read-only, firewall-wide NGFW preflight (spec §5.1, §5.2).

A DC shows this report to a customer, so a green stage must mean something:

* Every stage id appears exactly once, in order, in every report. A stage that did not
  run says why (``skipped`` + a ``PN_SKIPPED_*`` code); it is never left out.
* Only an unreachable management interface or a rejected key short-circuits the rest.
  A parser failure blocks that one stage (``PN_SHAPE_UNRECOGNISED``, with the command or
  xpath in ``detail``) and every other stage still runs.
* A zero is degraded, never ok: no log forwarding profile is blocked, no syslog server
  on the selected path is blocked, no decryption rule is degraded, an unknown timezone
  is degraded.
* ``queries_issued`` is the client's own count of requests sent. A report built from an
  injected transport proves stage logic, not a firewall; ``tenant_verified`` is 0.
* The API key never appears in a report: the client keeps it out of exception text and
  this module scrubs every string in the report on the way out as well.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from connectors.preflight import BLOCKED, DEGRADED, OK, READY, SKIPPED, Stage

from . import PANOS_KIND, codes
from .client import PanosClient
from .context import PreflightContext, xp, xp_device
from .exceptions import (
    PanosConfigError, PanosShapeError, PanosTransportError, PanosWriteRefused)
from .host import validate_panos_config
from .sources import AgentRef, check_sources
from .xmlparse import (
    clock_to_utc, parse_clock, parse_decryption_rules, parse_eal_device,
    parse_log_forwarding_profiles, parse_logging_status, parse_lsf_config,
    parse_registered_ips, parse_syslog_profiles, parse_system_info, parse_timezone)

__all__ = ["AgentRef", "FIREWALL_STAGE_IDS", "FirewallPreflightReport", "PROVES_TEMPLATE",
           "REMEDIATIONS", "run_panos_preflight"]

FIREWALL_STAGE_IDS: tuple[str, ...] = (
    "config", "dns_tls", "auth", "system", "clock", "forwarding_sls",
    "forwarding_syslog", "eal_device", "log_forwarding_profiles", "decryption",
    "registered_ip")

PROVES_TEMPLATE = (
    "Firewall configuration as read by {n} live call(s). It does not show that the "
    "tenant ingested or stitched anything. A report with queries_issued=0 was not "
    "answered by a firewall.")

CLOCK_SKEW_THRESHOLD_SECONDS = 30
LOG_STALE_SECONDS = 15 * 60
#: A last-forwarded time this far past the firewall's own clock is not a fresh log,
#: it is a clock or parse problem: the age is reported as unknown, never as ok.
LOG_FUTURE_TOLERANCE_SECONDS = 60
ALL_LOGS_FILTER = "All Logs"
#: Spec §5.2: URL/WildFire/data are subtypes of threat in ``show logging-status``,
#: so stage 6a checks these two; stage 8 checks all five in the match-lists.
FORWARDED_LOG_TYPES = ("traffic", "threat")
REQUIRED_LFP_TYPES = ("traffic", "threat", "url", "wildfire", "data")

CMD_SYSTEM_INFO = "<show><system><info/></system></show>"
CMD_CLOCK = "<show><clock/></show>"
CMD_LOGGING_STATUS = "<show><logging-status/></show>"
CMD_REGISTERED_IP_ALL = "<show><object><registered-ip><all/></registered-ip></object></show>"

# ---------------------------------------------------------------- remediations
#
# Each names the consequence in POV terms, not just the status.

REM_CONFIG_INVALID = (
    "Fix the firewall registration (management host, vsys, device name, API key) and "
    "run preflight again. Nothing was read from the firewall, so no NGFW part of this "
    "POV has been checked.")
REM_SKIPPED_CONFIG = "Not checked: the registration is invalid. Fix the config stage first."
REM_UNREACHABLE = (
    "SimCore cannot reach the firewall management interface (DNS, routing, the "
    "interface's permitted-IP list, or HTTPS not enabled on the management profile). "
    "Nothing about this firewall's logging is known: every NGFW-dataset detection in "
    "this POV is unverified until it answers.")
REM_TLS_FAILED = (
    "The management certificate did not verify. Install a certificate SimCore trusts, "
    "or set verify_tls=false for a lab (the connection is then reported as "
    "unverified). Nothing was read from the firewall.")
REM_TLS_UNVERIFIED = (
    "TLS verification is off, so SimCore cannot prove it is talking to this firewall "
    "and not to something in between. Acceptable in a lab with a self-signed "
    "certificate; every preflight of this firewall stays degraded until a trusted "
    "certificate is installed and verify_tls is on.")
REM_AUTH_REJECTED = (
    "The firewall rejected the API key (expired, revoked, or the admin role lacks XML "
    "API read access). Generate a new key with a read-only API admin role. No firewall "
    "check in this POV can run until it authenticates.")
REM_SKIPPED_UNREACHABLE = (
    "Not checked: the firewall management interface did not answer. Fix dns_tls first.")
REM_SKIPPED_AUTH = "Not checked: the firewall rejected the API key. Fix auth first."
REM_SHAPE_UNRECOGNISED = (
    "The firewall answered in a shape this version of CortexSim does not recognise "
    "(the detail names the command or xpath; panos_version is in the report). This "
    "stage's claim is unknown, not ok: treat the NGFW checks it covers as unverified "
    "and raise the response shape as a CortexSim gap.")
REM_MULTI_VSYS = (
    "Multi-vsys is on and this preflight checked only the configured vsys. If the lab "
    "traffic crosses another vsys, the profile and policy checks describe a vsys the "
    "test traffic never uses: set vsys to the one that carries it.")
REM_NOT_NORMAL_MODE = (
    "The firewall is not in normal operational mode (for example FIPS-CC). API reads "
    "and logging can behave differently in that mode, and these checks are not proven "
    "against it; treat this report as unverified for that reason.")
REM_CLOCK_SKEW = (
    "The firewall clock differs from SimCore's by more than 30 s. MTTD is measured "
    "across the two clocks, so every MTTD from this firewall's logs carries that "
    "error. Correct NTP on the firewall before the run.")
REM_CLOCK_TZ_UNKNOWN = (
    "The firewall clock could not be converted to UTC (no timezone configured, a zone "
    "name SimCore does not know, or no tzdata on the SimCore host). Clock skew is "
    "unknown, so MTTD from this firewall's logs cannot be trusted to the second. Set "
    "the device timezone, or install tzdata on the SimCore host.")
REM_LSF_DISABLED = (
    "Logging Service forwarding is not enabled, but the selected forwarding path needs "
    "it. Threat and traffic logs from this firewall will not reach the tenant over "
    "SLS; every NGFW-dataset detection on this run reads as missed.")
REM_COLLECTOR_INACTIVE = (
    "Logging Service forwarding is enabled but no log collector connection is Active. "
    "Logs queue on the firewall instead of reaching the tenant: every NGFW-dataset "
    "detection on this run reads as missed until the connection is up (check the "
    "device certificate, the logging-service licence and outbound access).")
REM_LOG_TYPE_STALE = (
    "A required log type has not been forwarded in the last 15 minutes. Either nothing "
    "produced it or forwarding has stalled; if it has stalled, detections on that log "
    "type read as missed. Send traffic through the firewall and run preflight again.")
REM_LOG_TYPE_NEVER = (
    "A required log type has never been forwarded to the collector. Detections that "
    "need it read as missed: check that the security rules carrying lab traffic use a "
    "log forwarding profile that sends this type.")
REM_LOG_TYPE_AGE_UNKNOWN = (
    "The firewall's own clock could not be read, so the age of the last forwarded "
    "traffic/threat log is unknown. Forwarding freshness is unverified.")
REM_SYSLOG_DELIVERY_UNVERIFIED = (
    "Syslog is configured, but the firewall keeps no forwarded/acknowledged counters "
    "for syslog the way it does for Logging Service forwarding. Delivery to the Broker "
    "VM must be confirmed on the tenant side (query the NGFW dataset for this "
    "firewall) before the run; until then a missing NGFW detection may be a delivery "
    "failure, not a detection gap.")
REM_SYSLOG_UDP = (
    "UDP syslog drops records silently under load or while the Broker VM restarts, and "
    "a dropped record reads as a missed detection. Prefer TCP or SSL for the POV.")
REM_SYSLOG_NO_SERVERS = (
    "The selected forwarding path is syslog, but no syslog server profile has a "
    "server. No firewall log can reach the tenant over syslog; every NGFW-dataset "
    "detection on this run reads as missed.")
REM_NO_FORWARDING_PATH = (
    "Neither Logging Service forwarding nor a syslog server is configured, so no "
    "firewall log can reach the tenant. Every NGFW-dataset detection in this POV reads "
    "as missed; configure one path before the run.")
REM_PATH_NOT_SELECTED = (
    "Not required by the selected forwarding path; reported for information only. "
    "Change forwarding_path if this is the path the tenant actually ingests from.")
REM_EAL_DEVICE_OFF = (
    "Enhanced Application Logging is off on the device, so the tenant receives no EAL "
    "records from this firewall. Detections and stitches that use EAL fields (DNS, "
    "HTTP, file metadata) cannot fire.")
REM_NO_LFP = (
    "There is no log forwarding profile, so no security rule can forward a log to the "
    "tenant. Every NGFW-dataset detection on this run reads as missed. Create a profile "
    "that forwards traffic, threat, url, wildfire and data with Enhanced Application "
    "Logging on, and attach it to the rules that carry lab traffic.")
REM_LFP_MISSING_TYPE = (
    "No profile forwards all of traffic, threat, url, wildfire and data. A rule using "
    "it sends none of the missing types to the tenant, so detections on those log types "
    "read as missed.")
REM_LFP_EAL_OFF = (
    "The profile has Enhanced Application Logging off, so traffic through rules that "
    "use it produces no EAL records; EAL-based detections and stitches cannot fire for "
    "that traffic.")
REM_LFP_TAG_ACTION = (
    "A log forwarding profile has a tagging action. It can register a tag on a test "
    "host mid-run and move it into a different security rule (for example a bypass "
    "rule), changing what the rest of the POV measures. Remove the action for the POV "
    "or account for it.")
REM_LFP_WRONG_TARGET = (
    "A log type is forwarded only to a path that is not the selected forwarding path, "
    "so it never reaches the tenant and detections on that type read as missed. Point "
    "the match-list at the selected path.")
REM_DECRYPTION_OFF = (
    "No enabled decryption rule. HTTP body/URI/header, TLS 1.3 certificate and "
    "WildFire-over-HTTPS detections cannot fire on encrypted test traffic; scenarios "
    "that rely on them read as missed.")
REM_REGISTERED_TAGS_PRESENT = (
    "Registered IP tags exist. A tag can put a test host into a dynamic address group "
    "and so into a different security rule than this POV assumes; check the per-source "
    "registered_tags stage for the agents under test.")
REM_LSF_DISABLED_BOTH = (
    "Logging Service forwarding is not enabled, but forwarding_path=both requires it. "
    "No log reaches the tenant over this path; detections that depend on SLS delivery "
    "read as missed even if syslog delivers.")
REM_COLLECTOR_INACTIVE_BOTH = (
    "No log collector connection is Active, so no log reaches the tenant over this path "
    "(forwarding_path=both). Detections that depend on SLS delivery read as missed until "
    "the connection is up.")
REM_SYSLOG_NO_SERVERS_BOTH = (
    "forwarding_path=both requires syslog, but no syslog server profile has a server. "
    "No log reaches the tenant over this path; detections that depend on the Broker VM "
    "ingest read as missed even if SLS delivers.")
REM_UPSTREAM_ERROR = (
    "The firewall answered this request with an HTTP error, so this stage was not "
    "checked; the other stages still ran. Its NGFW claim is unverified until the request "
    "succeeds (check the management plane's load and the admin role's XML API rights).")
REM_INTERNAL_ERROR = (
    "CortexSim failed while evaluating this stage, so its claim is unknown, not ok. "
    "Treat the NGFW checks it covers as unverified and report it as a CortexSim defect.")
REM_LFP_FILTERED = (
    "A log type is forwarded only through a match-list filter narrower than All Logs. "
    "Logs outside the filter never reach the tenant, so detections on them read as "
    "missed. Use All Logs for the POV, or confirm the filter covers the test traffic.")
REM_CALL_BUDGET = (
    "The preflight reached its call budget (CORTEXSIM_PANOS_MAX_CALLS_PER_PREFLIGHT) "
    "before this stage, so it was not checked. Raise the budget or select fewer agents.")
REM_WRITE_REFUSED = (
    "Internal guard: the read-only client refused a request this stage built, and "
    "nothing was sent. This stage was not checked; report it as a CortexSim defect.")

REMEDIATIONS: dict[str, str] = {
    codes.PN_CONFIG_INVALID: REM_CONFIG_INVALID,
    codes.PN_SKIPPED_CONFIG: REM_SKIPPED_CONFIG,
    codes.PN_UNREACHABLE: REM_UNREACHABLE,
    codes.PN_TLS_FAILED: REM_TLS_FAILED,
    codes.PN_TLS_UNVERIFIED: REM_TLS_UNVERIFIED,
    codes.PN_AUTH_REJECTED: REM_AUTH_REJECTED,
    codes.PN_SKIPPED_UNREACHABLE: REM_SKIPPED_UNREACHABLE,
    codes.PN_SKIPPED_AUTH: REM_SKIPPED_AUTH,
    codes.PN_SHAPE_UNRECOGNISED: REM_SHAPE_UNRECOGNISED,
    codes.PN_MULTI_VSYS: REM_MULTI_VSYS,
    codes.PN_NOT_NORMAL_MODE: REM_NOT_NORMAL_MODE,
    codes.PN_CLOCK_SKEW: REM_CLOCK_SKEW,
    codes.PN_CLOCK_TZ_UNKNOWN: REM_CLOCK_TZ_UNKNOWN,
    codes.PN_LSF_DISABLED: REM_LSF_DISABLED,
    codes.PN_COLLECTOR_INACTIVE: REM_COLLECTOR_INACTIVE,
    codes.PN_LOG_TYPE_STALE: REM_LOG_TYPE_STALE,
    codes.PN_LOG_TYPE_NEVER: REM_LOG_TYPE_NEVER,
    codes.PN_LOG_TYPE_AGE_UNKNOWN: REM_LOG_TYPE_AGE_UNKNOWN,
    codes.PN_SYSLOG_DELIVERY_UNVERIFIED: REM_SYSLOG_DELIVERY_UNVERIFIED,
    codes.PN_SYSLOG_UDP: REM_SYSLOG_UDP,
    codes.PN_SYSLOG_NO_SERVERS: REM_SYSLOG_NO_SERVERS,
    codes.PN_NO_FORWARDING_PATH: REM_NO_FORWARDING_PATH,
    codes.PN_PATH_NOT_SELECTED: REM_PATH_NOT_SELECTED,
    codes.PN_EAL_DEVICE_OFF: REM_EAL_DEVICE_OFF,
    codes.PN_NO_LFP: REM_NO_LFP,
    codes.PN_LFP_MISSING_TYPE: REM_LFP_MISSING_TYPE,
    codes.PN_LFP_EAL_OFF: REM_LFP_EAL_OFF,
    codes.PN_LFP_TAG_ACTION: REM_LFP_TAG_ACTION,
    codes.PN_LFP_WRONG_TARGET: REM_LFP_WRONG_TARGET,
    codes.PN_DECRYPTION_OFF: REM_DECRYPTION_OFF,
    codes.PN_REGISTERED_TAGS_PRESENT: REM_REGISTERED_TAGS_PRESENT,
    codes.PN_CALL_BUDGET: REM_CALL_BUDGET,
    codes.PN_UPSTREAM_ERROR: REM_UPSTREAM_ERROR,
    codes.PN_INTERNAL_ERROR: REM_INTERNAL_ERROR,
    codes.PN_LFP_FILTERED: REM_LFP_FILTERED,
    codes.PN_WRITE_REFUSED: REM_WRITE_REFUSED,
}

#: forwarding_path=both: one path failing does not mean no log reaches the tenant.
REMEDIATIONS_BOTH: dict[str, str] = {
    codes.PN_LSF_DISABLED: REM_LSF_DISABLED_BOTH,
    codes.PN_COLLECTOR_INACTIVE: REM_COLLECTOR_INACTIVE_BOTH,
    codes.PN_SYSLOG_NO_SERVERS: REM_SYSLOG_NO_SERVERS_BOTH,
}

_SHORT_CIRCUIT = {
    codes.PN_UNREACHABLE: codes.PN_SKIPPED_UNREACHABLE,
    codes.PN_TLS_FAILED: codes.PN_SKIPPED_UNREACHABLE,
    codes.PN_AUTH_REJECTED: codes.PN_SKIPPED_AUTH,
}


# ---------------------------------------------------------------- report


def _status_of(entry: dict[str, Any]) -> list[str]:
    out = [entry["status"]] if isinstance(entry.get("status"), str) else []
    for st in entry.get("stages") or []:
        status = st.status if isinstance(st, Stage) else (st or {}).get("status")
        if isinstance(status, str):
            out.append(status)
    return out


@dataclass
class FirewallPreflightReport:
    firewall: Optional[str]
    mgmt_host: Optional[str]
    stages: list[Stage] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)
    queries_issued: int = 0
    panos_version: Optional[str] = None
    config_source: str = "running"

    @property
    def overall(self) -> str:
        statuses = [s.status for s in self.stages]
        for src in self.sources:
            statuses.extend(_status_of(src))
        if BLOCKED in statuses:
            return BLOCKED
        if DEGRADED in statuses or SKIPPED in statuses:
            return DEGRADED
        return READY

    def to_dict(self) -> dict[str, Any]:
        return {
            "firewall": self.firewall,
            "kind": PANOS_KIND,
            "mgmt_host": self.mgmt_host,
            "overall": self.overall,
            "stages": [s.to_dict() for s in self.stages],
            "sources": [dict(s) for s in self.sources],
            "queries_issued": self.queries_issued,
            "panos_version": self.panos_version,
            "config_source": self.config_source,
            "proves": PROVES_TEMPLATE.format(n=self.queries_issued),
            "tenant_verified": 0,
        }


# ---------------------------------------------------------------- run state


class _Run:
    """Per-run scratch the stages share: the first response, cached config reads,
    the firewall's own clock text, and which forwarding path(s) are in use."""

    def __init__(self) -> None:
        self.short: Optional[str] = None
        self.cache: dict[str, Any] = {}
        self.sys_result: Any = None
        self.sys_exc: Optional[BaseException] = None
        self.fw_clock_text: Optional[str] = None
        self.selected_paths: set[str] = set()
        self.proven_paths: set[str] = set()
        self.fw_tz: Optional[str] = None
        self.syslog_profile_names: Optional[set[str]] = None


def _now(ctx: PreflightContext) -> datetime:
    return ctx.now if ctx.now is not None else datetime.now(timezone.utc)


def _shape(where: str, exc: PanosShapeError) -> PanosShapeError:
    return PanosShapeError(f"{where}: {exc}", code=exc.code)


async def _config(ctx: PreflightContext, run: _Run, xpath: str, parser: Callable[[Any], Any]) -> Any:
    """One running-config read, cached by xpath, parsed; a shape error names the xpath."""
    where = f"config {xpath}"
    cached = run.cache.get(xpath)
    if isinstance(cached, PanosShapeError):
        raise _shape(where, cached)
    if cached is None:
        try:
            cached = await ctx.client.config_show(xpath)
        except PanosShapeError as exc:
            run.cache[xpath] = exc
            raise _shape(where, exc) from None
        run.cache[xpath] = cached
    try:
        return parser(cached)
    except PanosShapeError as exc:
        raise _shape(where, exc) from None


async def _op(ctx: PreflightContext, cmd: str, parser: Callable[[Any], Any]) -> Any:
    where = f"op {cmd}"
    try:
        return parser(await ctx.client.op(cmd))
    except PanosShapeError as exc:
        raise _shape(where, exc) from None


def _rem(*codes_: str) -> str:
    seen: list[str] = []
    for c in codes_:
        r = REMEDIATIONS.get(c)
        if r and r not in seen:
            seen.append(r)
    return " ".join(seen)


def _issues_stage(sid: str, status: str, issues: list[tuple[str, str]], extra: dict) -> Stage:
    """A stage carrying one or more issues: the first is its code, all are listed."""
    code = issues[0][0]
    return Stage(sid, status, code, "; ".join(d for _, d in issues),
                 remediation=_rem(*(c for c, _ in issues)),
                 extra={**extra, "issues": [{"code": c, "detail": d} for c, d in issues]})


def _failure_stage(sid: str, exc: BaseException, run: _Run) -> Stage:
    if isinstance(exc, PanosTransportError):
        if exc.code in _SHORT_CIRCUIT:
            run.short = _SHORT_CIRCUIT[exc.code]
        return Stage(sid, BLOCKED, exc.code, str(exc), remediation=_rem(exc.code))
    if isinstance(exc, PanosWriteRefused):
        return Stage(sid, BLOCKED, codes.PN_WRITE_REFUSED, str(exc), remediation=REM_WRITE_REFUSED)
    # PanosShapeError (incl. PanosApiError): this stage only; the rest still run.
    return Stage(sid, BLOCKED, codes.PN_SHAPE_UNRECOGNISED, str(exc),
                 remediation=REM_SHAPE_UNRECOGNISED)


async def _attempt(sid: str, fn: Callable[..., Awaitable[Any]], ctx: PreflightContext,
                   run: _Run) -> tuple[Any, Optional[Stage], int]:
    t0 = time.monotonic()
    try:
        value = await fn(ctx, run)
        return value, None, int((time.monotonic() - t0) * 1000)
    except (PanosTransportError, PanosShapeError, PanosWriteRefused) as exc:
        return None, _failure_stage(sid, exc, run), int((time.monotonic() - t0) * 1000)
    except Exception as exc:  # noqa: BLE001 - one stage's defect must not lose the report
        # Type name only: an arbitrary exception message may carry request content.
        return None, Stage(sid, BLOCKED, codes.PN_INTERNAL_ERROR,
                           f"internal error ({type(exc).__name__})",
                           remediation=REM_INTERNAL_ERROR), int((time.monotonic() - t0) * 1000)


async def _guarded(sid: str, fn: Callable[..., Awaitable[Stage]], ctx: PreflightContext,
                   run: _Run) -> Stage:
    value, failure, ms = await _attempt(sid, fn, ctx, run)
    st = failure or value
    st.ms = ms
    return st


def _skip(sid: str, code: str, why: str = "") -> Stage:
    return Stage(sid, SKIPPED, code, why or "not checked", remediation=_rem(code))


# ---------------------------------------------------------------- stages 2-4: one call


async def _stage_dns_tls(ctx: PreflightContext, run: _Run) -> Stage:
    # auth and system read the same response; any failure here is carried to them so
    # neither can report ok for a call that did not succeed.
    try:
        run.sys_result = await ctx.client.op(CMD_SYSTEM_INFO)
    except PanosTransportError as exc:
        run.sys_exc = exc
        if exc.code in (codes.PN_UNREACHABLE, codes.PN_TLS_FAILED, codes.PN_CALL_BUDGET):
            raise  # this stage's own failure
        # it answered (a rejection or an HTTP error): DNS and TLS worked
    except (PanosShapeError, PanosWriteRefused) as exc:
        run.sys_exc = exc  # a 2xx with a body we do not recognise: reached, not authed
    if not ctx.config["verify_tls"]:
        return Stage("dns_tls", DEGRADED, codes.PN_TLS_UNVERIFIED,
                     f"{ctx.config['host']} answered over HTTPS without certificate "
                     "verification (verify_tls=false)", remediation=REM_TLS_UNVERIFIED)
    return Stage("dns_tls", OK, codes.PN_OK,
                 f"{ctx.config['host']} answered over HTTPS with a verified certificate")


async def _stage_auth(ctx: PreflightContext, run: _Run) -> Stage:
    if isinstance(run.sys_exc, PanosShapeError):
        raise PanosShapeError(
            f"op {CMD_SYSTEM_INFO}: the management interface did not answer as the "
            f"PAN-OS XML API ({run.sys_exc}); authentication is not established")
    if run.sys_exc is not None:
        raise run.sys_exc
    if run.sys_result is None:  # defensive: never ok without a successful reply
        raise PanosShapeError(f"op {CMD_SYSTEM_INFO}: no reply was recorded")
    return Stage("auth", OK, codes.PN_OK, "API key accepted (show system info returned success)")


async def _stage_system(ctx: PreflightContext, run: _Run) -> Stage:
    if isinstance(run.sys_exc, PanosShapeError):
        raise _shape(f"op {CMD_SYSTEM_INFO}", run.sys_exc)
    if run.sys_exc is not None:
        raise run.sys_exc
    if run.sys_result is None:
        raise PanosShapeError(f"op {CMD_SYSTEM_INFO}: no reply was recorded")
    try:
        info = parse_system_info(run.sys_result)
    except PanosShapeError as exc:
        raise _shape(f"op {CMD_SYSTEM_INFO}", exc) from None
    ctx.system = info
    ctx.report.panos_version = info.sw_version
    extra = {"hostname": info.hostname, "model": info.model, "serial": info.serial,
             "sw_version": info.sw_version, "multi_vsys": info.multi_vsys,
             "operational_mode": info.operational_mode,
             "advanced_routing": info.advanced_routing, "vsys": ctx.config["vsys"]}
    issues: list[tuple[str, str]] = []
    if info.multi_vsys:
        issues.append((codes.PN_MULTI_VSYS,
                       f"multi-vsys is on; checked {ctx.config['vsys']} only, other vsys "
                       "were not checked"))
    if info.operational_mode != "normal":
        issues.append((codes.PN_NOT_NORMAL_MODE,
                       f"operational mode is {info.operational_mode!r}, not 'normal'"))
    if issues:
        return _issues_stage("system", DEGRADED, issues, extra)
    return Stage("system", OK, codes.PN_OK,
                 f"{info.model} {info.hostname}, PAN-OS {info.sw_version}", extra=extra)


# ---------------------------------------------------------------- stage 5: clock


async def _stage_clock(ctx: PreflightContext, run: _Run) -> Stage:
    text = await _op(ctx, CMD_CLOCK, parse_clock)
    run.fw_clock_text = text
    tz = await _config(ctx, run, xp_device(ctx, "deviceconfig/system/timezone"), parse_timezone)
    extra: dict[str, Any] = {"firewall_clock": text, "timezone": tz}
    run.fw_tz = tz
    if tz is None:
        return Stage("clock", DEGRADED, codes.PN_CLOCK_TZ_UNKNOWN,
                     "the firewall reports no configured timezone, so its clock cannot be "
                     "converted to UTC", remediation=REM_CLOCK_TZ_UNKNOWN, extra=extra)
    try:
        fw_utc = clock_to_utc(text, tz)
    except PanosShapeError as exc:
        raise _shape(f"op {CMD_CLOCK}", exc) from None
    if fw_utc is None:
        return Stage("clock", DEGRADED, codes.PN_CLOCK_TZ_UNKNOWN,
                     f"timezone {tz!r} is unknown to this host (or tzdata is missing)",
                     remediation=REM_CLOCK_TZ_UNKNOWN, extra=extra)
    skew = int(round(abs((fw_utc - _now(ctx)).total_seconds())))
    extra.update({"firewall_utc": fw_utc.isoformat(), "skew_seconds": skew,
                  "threshold_seconds": CLOCK_SKEW_THRESHOLD_SECONDS})
    if skew > CLOCK_SKEW_THRESHOLD_SECONDS:
        return Stage("clock", DEGRADED, codes.PN_CLOCK_SKEW,
                     f"firewall clock is {skew} s from SimCore UTC (threshold "
                     f"{CLOCK_SKEW_THRESHOLD_SECONDS} s)", remediation=REM_CLOCK_SKEW, extra=extra)
    return Stage("clock", OK, codes.PN_OK, f"firewall clock within {skew} s of SimCore UTC",
                 extra=extra)


# ---------------------------------------------------------------- stage 6: forwarding


def _logging_xpath(ctx: PreflightContext) -> str:
    return xp_device(ctx, "deviceconfig/setting/logging")


def _local_to_utc(naive: datetime, tz: str) -> Optional[datetime]:
    """Firewall-local naive time -> aware UTC through the firewall's zone. None when the
    zone is unknown, or the local time is ambiguous (DST fall-back) or non-existent
    (spring-forward): a guess there could be off by an hour."""
    try:
        zone = ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return None
    first, second = naive.replace(tzinfo=zone, fold=0), naive.replace(tzinfo=zone, fold=1)
    if first.utcoffset() != second.utcoffset():
        return None
    return first.astimezone(timezone.utc)


def _fw_now_utc(run: _Run) -> tuple[Optional[datetime], str]:
    """The firewall's own clock in UTC, or (None, why not)."""
    if not run.fw_tz:
        return None, "the firewall reports no usable timezone"
    local = _fw_local_now(run)
    if local is None:
        return None, "the firewall clock could not be read"
    utc = _local_to_utc(local, run.fw_tz)
    if utc is None:
        return None, (f"the firewall clock falls in an ambiguous or non-existent hour of "
                      f"{run.fw_tz} (or the zone is unknown here)")
    return utc, ""


def _fw_local_now(run: _Run) -> Optional[datetime]:
    """The firewall's wall clock as a naive firewall-local datetime."""
    if not run.fw_clock_text:
        return None
    try:
        as_utc = clock_to_utc(run.fw_clock_text, "UTC")
    except PanosShapeError:
        return None
    return as_utc.replace(tzinfo=None) if as_utc is not None else None


def _fmt_age(seconds: int) -> str:
    if seconds < 3600:
        return f"{seconds // 60} min"
    return f"{seconds // 3600} h {seconds % 3600 // 60} min"


async def _gather_sls(ctx: PreflightContext, run: _Run) -> tuple[bool, Stage]:
    """(configured, the stage as it reads when this path is required)."""
    lsf = await _config(ctx, run, _logging_xpath(ctx), parse_lsf_config)
    extra: dict[str, Any] = {"enabled": lsf.enabled, "region": lsf.region}
    if not lsf.enabled:
        return False, Stage("forwarding_sls", BLOCKED, codes.PN_LSF_DISABLED,
                            "logging-service-forwarding is not enabled",
                            remediation=REM_LSF_DISABLED, extra=extra)
    collectors = await _op(ctx, CMD_LOGGING_STATUS, parse_logging_status)
    extra["collectors"] = [{"name": c.name, "connection_ip": c.connection_ip, "active": c.active}
                           for c in collectors]
    active = [c for c in collectors if c.active]
    if not active:
        return True, Stage("forwarding_sls", BLOCKED, codes.PN_COLLECTOR_INACTIVE,
                           f"none of {len(collectors)} log collector connection(s) is Active",
                           remediation=REM_COLLECTOR_INACTIVE, extra=extra)
    fw_now, why = _fw_now_utc(run)
    issues: list[tuple[str, str]] = []
    log_types: dict[str, dict[str, Any]] = {}
    for kind in FORWARDED_LOG_TYPES:
        fwded = max((c.types[kind].forwarded for c in active
                     if kind in c.types and c.types[kind].forwarded is not None), default=None)
        row: dict[str, Any] = {"last_forwarded": fwded.isoformat(sep=" ") if fwded else None,
                               "age_seconds": None}
        log_types[kind] = row
        if fwded is None:
            issues.append((codes.PN_LOG_TYPE_NEVER, f"{kind} logs have never been forwarded"))
            continue
        if fw_now is None:
            issues.append((codes.PN_LOG_TYPE_AGE_UNKNOWN,
                           f"{kind} last forwarded {row['last_forwarded']} (firewall time); "
                           f"age unknown: {why}"))
            continue
        fwded_utc = _local_to_utc(fwded, run.fw_tz)
        if fwded_utc is None:
            issues.append((codes.PN_LOG_TYPE_AGE_UNKNOWN,
                           f"{kind} last forwarded {row['last_forwarded']} (firewall time), "
                           f"an ambiguous or non-existent hour of {run.fw_tz}; age unknown"))
            continue
        age = int((fw_now - fwded_utc).total_seconds())
        if age < -LOG_FUTURE_TOLERANCE_SECONDS:
            issues.append((codes.PN_LOG_TYPE_AGE_UNKNOWN,
                           f"{kind} last forwarded {-age} s in the future of the firewall's "
                           "own clock; age unknown"))
            continue
        age = max(age, 0)
        row["age_seconds"] = age
        if age > LOG_STALE_SECONDS:
            issues.append((codes.PN_LOG_TYPE_STALE,
                           f"{kind} last forwarded {_fmt_age(age)} ago (threshold 15 min)"))
    extra["log_types"] = log_types
    if issues:
        return True, _issues_stage("forwarding_sls", DEGRADED, issues, extra)
    return True, Stage("forwarding_sls", OK, codes.PN_OK,
                       "collector Active; traffic and threat forwarded within 15 min", extra=extra)


async def _gather_syslog(ctx: PreflightContext, run: _Run) -> tuple[bool, Stage]:
    found = []
    for scope, xpath in (("vsys", xp(ctx, "log-settings/syslog")),
                         ("shared", "/config/shared/log-settings/syslog")):
        for p in await _config(ctx, run, xpath, parse_syslog_profiles):
            found.append((scope, p))
    run.syslog_profile_names = {p.name for _, p in found if p.servers}
    extra = {"profiles": [{"name": p.name, "scope": scope,
                           "servers": [asdict(s) for s in p.servers]} for scope, p in found]}
    servers = [s for _, p in found for s in p.servers]
    if not servers:
        return False, Stage("forwarding_syslog", BLOCKED, codes.PN_SYSLOG_NO_SERVERS,
                            f"{len(found)} syslog server profile(s), none with a server",
                            remediation=REM_SYSLOG_NO_SERVERS, extra=extra)
    issues = [(codes.PN_SYSLOG_DELIVERY_UNVERIFIED,
               f"{len(servers)} syslog server(s) configured; the firewall keeps no delivery "
               "counters for syslog, so delivery is unverified")]
    udp = [s.name for s in servers if s.transport.upper() == "UDP"]
    if udp:
        issues.append((codes.PN_SYSLOG_UDP, f"UDP transport (loss is silent): {', '.join(udp)}"))
    return True, _issues_stage("forwarding_syslog", DEGRADED, issues, extra)


def _not_selected(st: Stage, path: str) -> Stage:
    return Stage(st.id, SKIPPED, codes.PN_PATH_NOT_SELECTED,
                 f"forwarding_path={path}: not the path in use; reported for information "
                 f"({st.detail})", remediation=REM_PATH_NOT_SELECTED,
                 extra={**st.extra, "would_report": {"status": st.status, "code": st.code}},
                 ms=st.ms)


def _required_reading(st: Stage, path: str) -> Stage:
    if path == "both" and st.code in REMEDIATIONS_BOTH:
        st.remediation = REMEDIATIONS_BOTH[st.code]
    return st


def _apply_path(path: str, sls: tuple[Optional[bool], Stage],
                syslog: tuple[Optional[bool], Stage], run: _Run) -> tuple[Stage, Stage]:
    sides = {"sls": sls, "syslog": syslog}
    proven = {name for name, (conf, _) in sides.items() if conf is True}
    run.proven_paths = proven
    if path == "auto":
        if sls[0] is False and syslog[0] is False:
            out = []
            for _, st in (sls, syslog):
                out.append(Stage(st.id, BLOCKED, codes.PN_NO_FORWARDING_PATH,
                                 f"neither Logging Service forwarding nor a syslog server is "
                                 f"configured ({st.detail})",
                                 remediation=REM_NO_FORWARDING_PATH, extra=st.extra, ms=st.ms))
            return out[0], out[1]
        required = set(proven)
    else:
        required = {"sls", "syslog"} if path == "both" else {path}
    run.selected_paths = set(required)
    final = []
    for name, (conf, st) in sides.items():
        if conf is None or name in required:
            # a read failure stays blocked; a required path as it reads
            final.append(_required_reading(st, path))
        elif conf is False and proven - {name}:
            other = ", ".join(sorted(proven - {name}))
            final.append(Stage(st.id, OK, codes.PN_PATH_NOT_SELECTED,
                               f"forwarding_path={path}: not configured and not required; "
                               f"{other} is the proven forwarding path",
                               extra=st.extra, ms=st.ms))
        elif conf is False:
            # Unconfigured, and no other path is proven: it is not "not selected", it is
            # one more reason no log is known to reach the tenant.
            final.append(Stage(st.id, st.status, st.code,
                               f"{st.detail}; no other forwarding path is proven configured",
                               remediation=st.remediation, extra=st.extra, ms=st.ms))
        else:
            final.append(_not_selected(st, path))  # configured but not the selected path
    return final[0], final[1]


# ---------------------------------------------------------------- stages 7-10


async def _stage_eal_device(ctx: PreflightContext, run: _Run) -> Stage:
    on = await _config(ctx, run, _logging_xpath(ctx), parse_eal_device)
    if on:
        return Stage("eal_device", OK, codes.PN_OK, "enhanced-application-logging enabled")
    return Stage("eal_device", DEGRADED, codes.PN_EAL_DEVICE_OFF,
                 "enhanced-application-logging is not enabled on the device",
                 remediation=REM_EAL_DEVICE_OFF)


_LFP_ORDER = (codes.PN_NO_FORWARDING_PATH, codes.PN_LFP_WRONG_TARGET, codes.PN_LFP_FILTERED,
              codes.PN_LFP_MISSING_TYPE, codes.PN_LFP_EAL_OFF, codes.PN_LFP_TAG_ACTION)


def _pvsys(ctx: PreflightContext) -> str:
    return f"/config/panorama/vsys/entry[@name='{ctx.config['vsys']}']"


async def _read_scopes(ctx: PreflightContext, run: _Run, scopes, parser) -> list[tuple[str, Any]]:
    """Union of one config list across scopes, each item tagged with where it came from."""
    out = []
    for scope, xpath in scopes:
        for item in await _config(ctx, run, xpath, parser):
            out.append((scope, item))
    return out


def _scope_counts(found: list[tuple[str, Any]], scopes) -> str:
    return ", ".join(f"{scope} {sum(1 for s, _ in found if s == scope)}" for scope, _ in scopes)


async def _stage_log_forwarding_profiles(ctx: PreflightContext, run: _Run) -> Stage:
    scopes = (("vsys", xp(ctx, "log-settings/profiles")),
              ("shared", "/config/shared/log-settings/profiles"),
              ("panorama-vsys", _pvsys(ctx) + "/log-settings/profiles"),
              ("panorama-shared", "/config/panorama/shared/log-settings/profiles"))
    found = await _read_scopes(ctx, run, scopes, parse_log_forwarding_profiles)
    selected = run.selected_paths
    usable = selected & run.proven_paths
    if not found:
        return Stage("log_forwarding_profiles", BLOCKED, codes.PN_NO_LFP,
                     "no log forwarding profile in vsys, shared or Panorama-pushed config",
                     remediation=REM_NO_LFP,
                     extra={"profiles": [], "selected_paths": sorted(selected)})
    known_syslog = run.syslog_profile_names
    summaries, issues, complete = [], [], []
    for scope, p in found:
        label = f"profile {p.name} ({scope})"
        targets: dict[str, set[str]] = {}
        filters: dict[str, list[str]] = {}
        filtered: dict[str, list[str]] = {}
        for m in p.matches:
            flt = m.filter or ALL_LOGS_FILTER  # PAN-OS default when omitted
            filters.setdefault(m.log_type, []).append(flt)
            t = targets.setdefault(m.log_type, set())
            if flt != ALL_LOGS_FILTER:
                filtered.setdefault(m.log_type, []).append(flt)
                continue  # a narrowed match never counts as forwarding the whole type
            if m.send_to_panorama:
                t.add("sls")
            if m.syslog_profiles and (known_syslog is None
                                      or any(n in known_syslog for n in m.syslog_profiles)):
                t.add("syslog")
        on_path = {k for k, tg in targets.items() if (tg & selected if selected else tg)}
        missing = [k for k in REQUIRED_LFP_TYPES if k not in on_path]
        wrong = [k for k in missing if targets.get(k)]
        narrowed = [k for k in missing if k not in wrong and k in filtered]
        absent = [k for k in missing if k not in wrong and k not in narrowed]
        mine: list[tuple[str, str]] = []
        if wrong:
            mine.append((codes.PN_LFP_WRONG_TARGET,
                         f"{label} forwards {', '.join(wrong)} only to a path not in use "
                         f"(selected: {', '.join(sorted(selected)) or 'none'})"))
        if narrowed:
            mine.append((codes.PN_LFP_FILTERED, f"{label} forwards " + ", ".join(
                f"{k} only through filter {' / '.join(filtered[k])}" for k in narrowed)))
        if absent:
            mine.append((codes.PN_LFP_MISSING_TYPE, f"{label} does not forward {', '.join(absent)}"))
        if not p.eal:
            mine.append((codes.PN_LFP_EAL_OFF, f"{label} has enhanced-application-logging off"))
        if p.has_tag_action:
            mine.append((codes.PN_LFP_TAG_ACTION, f"{label} has a tagging action"))
        ok_profile = not missing and p.eal
        if ok_profile:
            complete.append(label)
        issues.extend(mine)
        summaries.append({"name": p.name, "scope": scope, "eal": p.eal,
                          "forwards": {k: sorted(v) for k, v in targets.items()},
                          "filters": filters, "has_tag_action": p.has_tag_action,
                          "complete": ok_profile, "issues": [c for c, _ in mine]})
    extra = {"profiles": summaries, "selected_paths": sorted(selected),
             "proven_paths": sorted(run.proven_paths), "complete": complete,
             "read_from": _scope_counts(found, scopes)}
    if complete:
        issues = [i for i in issues if i[0] == codes.PN_LFP_TAG_ACTION]
    if not usable:
        # A profile can only be judged against a path that is proven to deliver.
        issues.insert(0, (codes.PN_NO_FORWARDING_PATH,
                          "no forwarding path proven, so no profile can be shown to deliver "
                          "logs to the tenant"))
    if complete and not issues:
        return Stage("log_forwarding_profiles", OK, codes.PN_OK,
                     f"{', '.join(complete)} forward(s) traffic, threat, url, wildfire and "
                     f"data with enhanced-application-logging (read from: "
                     f"{extra['read_from']})", extra=extra)
    issues.sort(key=lambda i: _LFP_ORDER.index(i[0]))
    return _issues_stage("log_forwarding_profiles", DEGRADED, issues, extra)


async def _stage_decryption(ctx: PreflightContext, run: _Run) -> Stage:
    scopes = (("vsys", xp(ctx, "rulebase/decryption/rules")),
              ("panorama-pre", _pvsys(ctx) + "/pre-rulebase/decryption/rules"),
              ("panorama-post", _pvsys(ctx) + "/post-rulebase/decryption/rules"))
    rules = await _read_scopes(ctx, run, scopes, parse_decryption_rules)
    enabled = [f"{name} ({scope})" for scope, (name, on) in rules if on]
    extra = {"rules": len(rules), "enabled": enabled, "read_from": _scope_counts(rules, scopes)}
    if enabled:
        return Stage("decryption", OK, codes.PN_OK,
                     f"{len(enabled)} enabled decryption rule(s): {', '.join(enabled)}",
                     extra=extra)
    return Stage("decryption", DEGRADED, codes.PN_DECRYPTION_OFF,
                 f"no enabled decryption rule ({len(rules)} rule(s) in "
                 f"{extra['read_from']})", remediation=REM_DECRYPTION_OFF, extra=extra)


async def _stage_registered_ip(ctx: PreflightContext, run: _Run) -> Stage:
    mapping = await _op(ctx, CMD_REGISTERED_IP_ALL, parse_registered_ips)
    tags = sorted({t for ts in mapping.values() for t in ts})
    extra = {"count": len(mapping), "tags": tags, "entries": mapping}
    if tags:
        return Stage("registered_ip", DEGRADED, codes.PN_REGISTERED_TAGS_PRESENT,
                     f"{len(mapping)} registered IP(s) carrying tag(s): {', '.join(tags)}",
                     remediation=REM_REGISTERED_TAGS_PRESENT, extra=extra)
    return Stage("registered_ip", OK, codes.PN_OK,
                 f"{len(mapping)} registered IP(s), no tags", extra=extra)


# ---------------------------------------------------------------- scrubbing


def _scrub(value: Any, key: str) -> Any:
    if isinstance(value, str):
        return value.replace(key, "[redacted]")
    if isinstance(value, list):
        return [_scrub(v, key) for v in value]
    if isinstance(value, tuple):
        return tuple(_scrub(v, key) for v in value)
    if isinstance(value, dict):
        return {_scrub(k, key): _scrub(v, key) for k, v in value.items()}
    return value


def _scrub_report(report: FirewallPreflightReport, key: Optional[str]) -> None:
    # Very short strings would mangle unrelated text; real PAN-OS keys are long.
    if not key or len(key) < 6:
        return
    for st in report.stages:
        st.detail = _scrub(st.detail, key)
        st.remediation = _scrub(st.remediation, key)
        st.extra = _scrub(st.extra, key)
    report.sources = _scrub(report.sources, key)


# ---------------------------------------------------------------- runner


async def run_panos_preflight(config: dict, api_key: str, *, firewall: Optional[str],
                              agents: list[AgentRef],
                              transport: Optional[httpx.AsyncBaseTransport] = None,
                              now: Optional[datetime] = None,
                              max_calls: int = 120) -> FirewallPreflightReport:
    if now is not None and now.tzinfo is None:
        raise ValueError("now must be timezone-aware (UTC)")
    report = FirewallPreflightReport(firewall=firewall, mgmt_host=None)
    try:
        cfg = validate_panos_config(config)
        if not api_key:
            raise PanosConfigError("no API key is stored for this firewall")
    except PanosConfigError as exc:
        report.stages.append(Stage("config", BLOCKED, codes.PN_CONFIG_INVALID, str(exc),
                                   remediation=REM_CONFIG_INVALID))
        for sid in FIREWALL_STAGE_IDS[1:]:
            report.stages.append(_skip(sid, codes.PN_SKIPPED_CONFIG))
        report.sources = [{"code": codes.PN_SKIPPED_CONFIG, "status": SKIPPED,
                           "detail": "source checks not run: the registration is invalid"}]
        _scrub_report(report, api_key)
        return report

    report.mgmt_host = cfg["host"]
    report.stages.append(Stage(
        "config", OK, codes.PN_OK,
        f"host {cfg['host']}, vsys {cfg['vsys']}, forwarding_path {cfg['forwarding_path']}, "
        f"verify_tls {str(cfg['verify_tls']).lower()}"))

    client = PanosClient(cfg["host"], api_key, verify_tls=cfg["verify_tls"],
                         transport=transport, max_calls=max_calls)
    ctx = PreflightContext(client=client, config=cfg, report=report, now=now)
    run = _Run()
    stages = report.stages  # appended in order so later checks can read earlier ones

    async def step(sid: str, fn: Callable[..., Awaitable[Stage]]) -> None:
        if run.short:
            stages.append(_skip(sid, run.short))
        else:
            stages.append(await _guarded(sid, fn, ctx, run))

    try:
        for sid, fn in (("dns_tls", _stage_dns_tls), ("auth", _stage_auth),
                        ("system", _stage_system), ("clock", _stage_clock)):
            await step(sid, fn)

        # 6a/6b always both run and report; the path rule only decides what is required.
        if run.short:
            stages.append(_skip("forwarding_sls", run.short))
            stages.append(_skip("forwarding_syslog", run.short))
        else:
            facts = []
            for sid, gather in (("forwarding_sls", _gather_sls),
                                ("forwarding_syslog", _gather_syslog)):
                if run.short:
                    facts.append((None, _skip(sid, run.short)))
                    continue
                value, failure, ms = await _attempt(sid, gather, ctx, run)
                conf, st = (None, failure) if failure else value
                st.ms = ms
                facts.append((conf, st))
            if run.short:
                stages.extend(st for _, st in facts)
            else:
                stages.extend(_apply_path(cfg["forwarding_path"], facts[0], facts[1], run))

        for sid, fn in (("eal_device", _stage_eal_device),
                        ("log_forwarding_profiles", _stage_log_forwarding_profiles),
                        ("decryption", _stage_decryption),
                        ("registered_ip", _stage_registered_ip)):
            await step(sid, fn)

        if run.short:
            report.sources = [{"code": run.short, "status": SKIPPED,
                               "detail": "source checks not run: the firewall-wide checks "
                                         "could not reach or authenticate to the firewall"}]
        else:
            report.sources = await check_sources(ctx, agents)
    finally:
        report.queries_issued = client.calls_issued
        await client.aclose()

    _scrub_report(report, api_key)
    return report

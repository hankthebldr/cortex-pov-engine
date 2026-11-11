# core/integrations/panos/sources.py
"""Per-agent source checks: address, NAT, collisions and policy path (spec §5.3, §5.4).

The stitch contract: endpoint and NGFW records stitch only when the 5-tuple agrees,
with the source as the agent recorded it and the destination as the firewall recorded
it. NAT between agent and firewall, or two agents behind one address, breaks the
stitch for that agent however healthy the firewall-wide stages are. So every enrolled
agent is checked on its own, and the honesty rules of the firewall-wide preflight
hold here too:

* every source stage id appears once, in order, for every agent; a stage that did not
  run says why (``skipped`` + a code), it is never left out;
* an unknown is degraded, never ok: an unknown firewall-observed source, an unknown
  agent-side address, an unresolved zone, a profile name found nowhere;
* no network command is ever built from an unknown source: with no observed address
  the source's network stages are skipped and nothing is sent;
* one stage's failure (a shape the parser does not know, an HTTP error, a CortexSim
  defect) blocks that stage only, and the report stays whole and key-free;
* when the call budget runs out, the rest of that source and every later source are
  ``skipped / PN_CALL_BUDGET``, and work done before the cap is kept.

Operator-supplied values (addresses, zone and router names, applications) reach the
firewall only as element text built with ``xml.etree.ElementTree``, which escapes
``&``, ``<`` and ``>`` on serialisation. They are not pre-escaped as well: that would
double-escape and send ``&amp;lt;`` for ``<``.
"""
from __future__ import annotations

import ipaddress
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from connectors.preflight import BLOCKED, DEGRADED, OK, SKIPPED, Stage

from . import codes
from .client import allowed_xpath
from .context import PreflightContext, find_object, xp
from .exceptions import PanosShapeError, PanosTransportError, PanosWriteRefused
from .xmlparse import (
    ProfileActions, parse_fib, parse_interface_zone, parse_nat_match, parse_policy_match,
    parse_profile_actions, parse_profile_group, parse_registered_ips, parse_rule,
    parse_session_count, parse_user_mapping, parse_zone_userid)

SOURCE_STAGE_IDS: tuple[str, ...] = (
    "address", "source_seen", "policy_match", "rule_profiles", "nat", "user_id",
    "registered_tags")

PROFILE_TYPES = ("virus", "spyware", "vulnerability", "url-filtering", "wildfire-analysis")
SEVERITIES = ("critical", "high", "medium", "low", "informational")
_PROTO_NUMBERS = {"tcp": "6", "udp": "17"}
_ALLOW_ACTIONS = frozenset({"allow"})

NO_AGENTS_DETAIL = (
    "No enrolled agent was selected, so no source path through this firewall was "
    "checked. A firewall-wide green does not show that any agent's traffic is "
    "inspected or logged.")

#: Predefined profiles, which never appear in running config. Classified from their
#: documented semantics (spec §5.1 ruling): ``default`` defers to each signature's own
#: action; ``strict`` blocks critical/high/medium and defers below that.
_SIG = "signature-default"
PREDEFINED_PROFILES: dict[tuple[str, str], dict[str, str]] = {
    ("spyware", "default"): {s: _SIG for s in SEVERITIES},
    ("vulnerability", "default"): {s: _SIG for s in SEVERITIES},
    ("virus", "default"): {"*": _SIG},
    ("spyware", "strict"): {"critical": "block", "high": "block", "medium": "block",
                            "low": _SIG, "informational": _SIG},
    ("vulnerability", "strict"): {"critical": "block", "high": "block", "medium": "block",
                                  "low": _SIG, "informational": _SIG},
}
#: url-filtering / wildfire-analysis have predefined ``default`` profiles too; their
#: actions are not classified by this version (no parser), so they are reported as such.
PREDEFINED_UNCLASSIFIED = frozenset({("url-filtering", "default"),
                                     ("wildfire-analysis", "default")})

# ---------------------------------------------------------------- remediations
#
# Each names the consequence in POV terms.

SOURCE_REMEDIATIONS: dict[str, str] = {
    codes.PN_NO_AGENTS: NO_AGENTS_DETAIL,
    codes.PN_SOURCE_COLLISION: (
        "Two or more enrolled agents reach this firewall from the same address. The "
        "firewall attributes every session from it to one host, so src-IP / 5-tuple "
        "stitches for these agents either fail or land on the wrong endpoint. Give each "
        "agent its own address on the firewall's side (no shared NAT), or run them in "
        "separate POV passes."),
    codes.PN_NAT_BETWEEN_AGENT_AND_FW: (
        "The address the firewall sees for this agent is not one the agent itself "
        "records, so there is NAT between them. No src-IP / 5-tuple / container_id "
        "stitch can form for this agent; the firewall attributes its sessions to "
        "whichever host owns the observed address. Remove the NAT for the lab path, or "
        "correct the entered addresses if they are wrong."),
    codes.PN_AGENT_ADDRESS_UNKNOWN: (
        "The agent-side address (what the XDR agent records) is unknown, so whether NAT "
        "sits between the agent and the firewall is unknown, and so is whether this "
        "agent's endpoint and NGFW records can stitch. Enter the agent's own interface "
        "address(es) for this firewall."),
    codes.PN_SOURCE_UNKNOWN: (
        "The address the firewall sees for this agent is unknown (no override entered, "
        "and SimCore has no usable last-seen address for it), so no policy path was "
        "checked and nothing was sent for it. Enter the firewall-observed source for "
        "this agent."),
    codes.PN_SOURCE_NOT_SEEN: (
        "The firewall has no current session from this source. Either the agent is idle "
        "or this is not the address the firewall sees it as (NAT, a second interface, "
        "an inferred last_ip that is wrong); if it is the wrong address, every check "
        "for this source describes a host the test traffic never comes from. Generate "
        "traffic from the agent or correct the observed source, then re-run."),
    codes.PN_ZONE_UNRESOLVED: (
        "The source or destination zone could not be resolved (no route in the FIB, an "
        "interface in no zone, or the routing mode is unknown), so the security policy "
        "for this path was not looked up and its inspection and logging are unknown. "
        "Set inside_zone / outside_zone for this firewall."),
    codes.PN_NO_RULE_MATCH: (
        "No configured security rule matches this probe, so the traffic falls to the "
        "default intrazone/interzone rule, which does not log or forward by default. "
        "NGFW detections on this path read as missed; add an explicit rule with a log "
        "forwarding profile for the lab traffic."),
    codes.PN_RULE_DENIES: (
        "The matching rule does not allow this traffic, so the test's network leg never "
        "completes: no threat inspection happens and the endpoint never receives the "
        "artefact. Scenarios on this path cannot produce the NGFW-plus-XDR case they "
        "expect; allow the lab traffic through a rule with alerting profiles."),
    codes.PN_NO_PROFILE: (
        "The matching rule (or one of its profiles) does not inspect or log some threat "
        "types or severities. Threats there pass without a threat log, so NGFW threat "
        "detections for them read as missed. Attach virus, spyware, vulnerability, "
        "URL and WildFire profiles that alert on every severity."),
    codes.PN_PROFILE_BLOCKS: (
        "A profile on this path blocks some threats. A block still writes a threat log, "
        "but it stops the artefact before the endpoint, so the XDR half of the case "
        "never fires and the case holds only the NGFW alert. Use an alert-only profile "
        "on a lab-scoped rule if the POV needs both halves."),
    codes.PN_PROFILE_SIGNATURE_DEFAULT: (
        "A profile action is 'default', which defers to each signature's own action and "
        "likely blocks critical/high (the firewall cannot say which without the threat "
        "database). Set an explicit alert on a lab-scoped profile to be sure the "
        "artefact reaches the endpoint."),
    codes.PN_DNS_SEC_SINKHOLE: (
        "DNS Security sinkholes some domain categories on this path. The lookup is "
        "answered with the sinkhole address, so a C2 test never reaches its real "
        "destination and later steps that need it read as not reached. Set the lab "
        "categories to alert for the POV, or account for the sinkhole."),
    codes.PN_RULE_NO_LOG_FORWARDING: (
        "The matching rule has no log forwarding profile, so its traffic and threat logs "
        "stay on the firewall and never reach the tenant. Every NGFW-dataset detection "
        "on this path reads as missed; attach a forwarding profile."),
    codes.PN_RULE_LOG_PROFILE_INCOMPLETE: (
        "The matching rule forwards through a log forwarding profile that the "
        "log_forwarding_profiles stage did not show complete, so some log types from "
        "this path do not reach the tenant. See that stage for the missing types."),
    codes.PN_OBJECT_NOT_FOUND: (
        "A referenced object (rule, profile group, profile or zone) was found in no "
        "configuration scope this preflight reads and is not a predefined name, so what "
        "it does is unknown, not ok. Check the name, or whether it lives in a scope "
        "(another vsys, a Panorama device group) this firewall does not run."),
    codes.PN_NO_USER_MAPPING: (
        "The firewall has no user mapping for this source, so its logs carry no user. "
        "User-principal stitches cannot resolve on the firewall side and detections "
        "that key on the user column read as missed."),
    codes.PN_ZONE_USERID_OFF: (
        "User-ID is not enabled on this source's zone, so the firewall writes no user "
        "into its logs. User-principal stitches cannot resolve on the firewall side; "
        "enable user identification on the zone."),
    codes.PN_SOURCE_TAGGED: (
        "This source carries registered IP tags. A tag can place it in a dynamic "
        "address group and so in a different security rule than this check assumes "
        "(for example a bypass rule), changing what the POV measures. Remove the tags "
        "for the POV or account for them."),
}

_BLOCKING_CODES = frozenset({codes.PN_SOURCE_COLLISION, codes.PN_NAT_BETWEEN_AGENT_AND_FW,
                             codes.PN_RULE_DENIES})
_HALT = {codes.PN_UNREACHABLE: codes.PN_SKIPPED_UNREACHABLE,
         codes.PN_TLS_FAILED: codes.PN_SKIPPED_UNREACHABLE,
         codes.PN_AUTH_REJECTED: codes.PN_SKIPPED_AUTH}


def _rem(*codes_: str) -> str:
    from . import preflight as _pf  # lazy: preflight imports this module
    seen: list[str] = []
    for c in codes_:
        r = SOURCE_REMEDIATIONS.get(c) or _pf.REMEDIATIONS.get(c)
        if r and r not in seen:
            seen.append(r)
    return " ".join(seen)


# ---------------------------------------------------------------- address resolution


@dataclass(frozen=True)
class AgentRef:
    agent_id: str
    hostname: str
    last_ip: Optional[str]


@dataclass
class ResolvedSource:
    agent_id: str
    hostname: str
    fw_observed_src: Optional[str]
    fw_src_provenance: str          # "entered" | "inferred:last_ip" | "unknown"
    agent_addresses: list[str]
    agent_addr_provenance: str      # "entered" | "unknown"
    nat_verdict: str                # "tuple_intact" | "nat_between" | "unknown"
    collision_with: list[str] = field(default_factory=list)
    note: Optional[str] = None      # why the source is unknown, when it is


def _ip_key(value: str):
    """Comparison key: an IPv4-mapped IPv6 address equals its IPv4 form. The original
    string is what is sent; this is only used to compare."""
    ip = ipaddress.ip_address(value)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def _valid_ip(value: Any) -> bool:
    if not isinstance(value, str) or not value or value != value.strip():
        return False
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def resolve_addresses(agents: list[AgentRef], config: dict) -> list[ResolvedSource]:
    """Pure: no I/O. Firewall-observed source, agent-side addresses, NAT verdict and
    collisions for each agent (spec §5.4)."""
    observed = config.get("observed_sources") or {}
    entered = config.get("agent_addresses") or {}
    out: list[ResolvedSource] = []
    for a in agents:
        note = None
        if a.agent_id in observed:
            src, prov = observed[a.agent_id], "entered"
        elif _valid_ip(a.last_ip):
            src, prov = a.last_ip, "inferred:last_ip"
        else:
            src, prov = None, "unknown"
            note = ("SimCore has no last-seen address for this agent" if not a.last_ip
                    else "the agent's last-seen address is not an IP address")
        addrs = list(entered.get(a.agent_id) or [])
        aprov = "entered" if addrs else "unknown"
        if src is None or not addrs:
            verdict = "unknown"
        else:
            verdict = ("tuple_intact" if _ip_key(src) in {_ip_key(x) for x in addrs}
                       else "nat_between")
        out.append(ResolvedSource(a.agent_id, a.hostname, src, prov, addrs, aprov, verdict,
                                  note=note))
    for r in out:
        if r.fw_observed_src is None:
            continue
        key = _ip_key(r.fw_observed_src)
        r.collision_with = [o.hostname or o.agent_id for o in out
                            if o is not r and o.fw_observed_src is not None
                            and _ip_key(o.fw_observed_src) == key]
    return out


# ---------------------------------------------------------------- command builders


def _el(tag: str, *children: ET.Element, text: Optional[str] = None) -> ET.Element:
    e = ET.Element(tag)
    for c in children:
        e.append(c)
    if text is not None:
        e.text = text  # ET escapes on serialisation
    return e


def _xml(root: ET.Element) -> str:
    return ET.tostring(root, encoding="unicode")


def cmd_session_count(ip: str) -> str:
    return _xml(_el("show", _el("session", _el("all", _el(
        "filter", _el("source", text=ip), _el("count", text="yes"))))))


def cmd_user_mapping(ip: str) -> str:
    return _xml(_el("show", _el("user", _el("ip-user-mapping", _el("ip", text=ip)))))


def cmd_registered_ip(ip: str) -> str:
    return _xml(_el("show", _el("object", _el("registered-ip", _el("ip", text=ip)))))


def cmd_fib(ip: str, router: str, advanced: bool) -> str:
    if advanced:
        return _xml(_el("test", _el("advanced-routing", _el(
            "fib-lookup", _el("logical-router", text=router), _el("ip", text=ip)))))
    return _xml(_el("test", _el("routing", _el(
        "fib-lookup", _el("virtual-router", text=router), _el("ip", text=ip)))))


def cmd_match(kind: str, frm: str, to: str, src: str, probe: dict) -> str:
    assert kind in ("security-policy-match", "nat-policy-match")
    return _xml(_el("test", _el(
        kind, _el("from", text=frm), _el("to", text=to), _el("source", text=src),
        _el("destination", text=probe["ip"]),
        _el("destination-port", text=str(probe["port"])),
        _el("protocol", text=_PROTO_NUMBERS[probe["protocol"]]),
        _el("application", text=probe["application"]))))


# ---------------------------------------------------------------- run state


class _Halt(Exception):
    """Raised inside a source to stop it: the budget ran out, or the firewall stopped
    answering. Carries the code the remaining stages are skipped with."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass
class SourcesRun:
    """Shared across all sources in one preflight: the halt reason, and caches so a
    rule or profile used by many probes and agents is read once."""
    halt: Optional[str] = None
    objects: dict[tuple[str, str], Any] = field(default_factory=dict)
    rules: dict[str, tuple[dict, list]] = field(default_factory=dict)
    fib: dict[str, Optional[str]] = field(default_factory=dict)
    zones: Any = None


@dataclass
class _Src:
    """Per-source scratch the stages share."""
    src: ResolvedSource
    probes: list[dict] = field(default_factory=list)
    src_zone: Optional[str] = None
    src_zone_why: str = ""


def _issue_stage(sid: str, issues: list[tuple[str, str, str]], extra: dict,
                 ok_detail: str) -> Stage:
    """issues: (status, code, detail). Blocked first, then degraded; deduplicated."""
    seen, uniq = set(), []
    for st, c, d in issues:
        if (c, d) not in seen:
            seen.add((c, d))
            uniq.append((st, c, d))
    if not uniq:
        return Stage(sid, OK, codes.PN_OK, ok_detail, extra=extra)
    order = {BLOCKED: 0, SKIPPED: 1, DEGRADED: 2}
    uniq.sort(key=lambda i: order[i[0]])
    status = uniq[0][0]
    return Stage(sid, status, uniq[0][1], "; ".join(d for _, _, d in uniq),
                 remediation=_rem(*(c for _, c, _ in uniq)),
                 extra={**extra, "issues": [{"status": s, "code": c, "detail": d}
                                            for s, c, d in uniq]})


def _where(where: str, exc: PanosShapeError) -> PanosShapeError:
    return PanosShapeError(f"{where}: {exc}", code=exc.code)


def _classify(exc: BaseException) -> tuple[str, str, str]:
    """(status, code, detail) for a failure that does not halt the source."""
    if isinstance(exc, PanosTransportError):
        return BLOCKED, exc.code, str(exc)
    if isinstance(exc, PanosWriteRefused):
        return BLOCKED, codes.PN_WRITE_REFUSED, str(exc)
    if isinstance(exc, PanosShapeError):
        if exc.code == codes.PN_OBJECT_NOT_FOUND:
            return DEGRADED, codes.PN_OBJECT_NOT_FOUND, str(exc)
        return BLOCKED, codes.PN_SHAPE_UNRECOGNISED, str(exc)
    # Type name only: an arbitrary exception message may carry request content.
    return BLOCKED, codes.PN_INTERNAL_ERROR, f"internal error ({type(exc).__name__})"


def _maybe_halt(exc: BaseException, state: SourcesRun) -> None:
    if isinstance(exc, PanosTransportError):
        if exc.code == codes.PN_CALL_BUDGET:
            state.halt = codes.PN_CALL_BUDGET
            raise _Halt(codes.PN_CALL_BUDGET) from None
        if exc.code in _HALT:
            state.halt = _HALT[exc.code]


async def _op(ctx: PreflightContext, cmd: str, parser: Callable[[Any], Any]) -> Any:
    try:
        return parser(await ctx.client.op(cmd))
    except PanosShapeError as exc:
        raise _where(f"op {cmd}", exc) from None


async def _config(ctx: PreflightContext, xpath: str, parser: Callable[[Any], Any]) -> Any:
    try:
        return parser(await ctx.client.config_show(xpath))
    except PanosShapeError as exc:
        raise _where(f"config {xpath}", exc) from None


async def _find(ctx: PreflightContext, state: SourcesRun, kind: str, name: str):
    """find_object, cached across probes and sources. A shape error names the object."""
    key = (kind, name)
    if key not in state.objects:
        try:
            state.objects[key] = await find_object(ctx, kind, name)
        except PanosShapeError as exc:
            raise _where(f"{kind} {name!r}", exc) from None
    return state.objects[key]


# ---------------------------------------------------------------- stage: address


def _stage_address(s: _Src) -> Stage:
    r = s.src
    issues: list[tuple[str, str, str]] = []
    if r.fw_observed_src is None:
        issues.append((DEGRADED, codes.PN_SOURCE_UNKNOWN,
                       f"firewall-observed source unknown: no observed_sources entry and "
                       f"{r.note or 'no usable last-seen address'}"))
    if r.collision_with:
        issues.append((BLOCKED, codes.PN_SOURCE_COLLISION,
                       f"{r.hostname} shares firewall-observed source {r.fw_observed_src} "
                       f"with {', '.join(r.collision_with)}"))
    if r.nat_verdict == "nat_between":
        issues.append((BLOCKED, codes.PN_NAT_BETWEEN_AGENT_AND_FW,
                       f"the firewall sees {r.fw_observed_src}; the agent's own addresses "
                       f"are {', '.join(r.agent_addresses)}: NAT sits between them"))
    if r.agent_addr_provenance == "unknown":
        issues.append((DEGRADED, codes.PN_AGENT_ADDRESS_UNKNOWN,
                       "agent-side address unknown, so the NAT verdict is unknown"))
    prov = "inferred from last_ip" if r.fw_src_provenance == "inferred:last_ip" else "entered"
    return _issue_stage("address", issues, {},
                        f"firewall sees {r.fw_observed_src} ({prov}); the agent records it "
                        "too (tuple intact)")


# ---------------------------------------------------------------- stage: source_seen


async def _stage_source_seen(ctx: PreflightContext, state: SourcesRun, s: _Src) -> Stage:
    ip = s.src.fw_observed_src
    n = await _op(ctx, cmd_session_count(ip), parse_session_count)
    extra = {"sessions": n}
    if n == 0:
        return _issue_stage("source_seen", [(DEGRADED, codes.PN_SOURCE_NOT_SEEN,
                                             f"no current session from {ip}")], extra, "")
    return Stage("source_seen", OK, codes.PN_OK, f"{n} current session(s) from {ip}", extra=extra)


# ---------------------------------------------------------------- zones


async def _zone_of(ctx: PreflightContext, state: SourcesRun, ip: str) -> tuple[Optional[str], str]:
    """(zone, why-not) through a FIB lookup and the interface's zone."""
    if ctx.system is None:
        return None, "the routing mode is unknown (system info was not read)"
    if ip not in state.fib:
        state.fib[ip] = await _op(ctx, cmd_fib(ip, ctx.config["routing_instance"],
                                               ctx.system.advanced_routing), parse_fib)
    iface = state.fib[ip]
    if iface is None:
        return None, f"no route to {ip} in {ctx.config['routing_instance']}"
    if state.zones is None:
        try:
            state.zones = await ctx.client.config_show(xp(ctx, "zone"))
        except PanosShapeError as exc:
            raise _where(f"config {xp(ctx, 'zone')}", exc) from None
    try:
        zone = parse_interface_zone(state.zones, iface)
    except PanosShapeError as exc:
        raise _where(f"config {xp(ctx, 'zone')}", exc) from None
    if zone is None:
        return None, f"interface {iface} (route to {ip}) is in no zone of {ctx.config['vsys']}"
    return zone, ""


async def _src_zone(ctx: PreflightContext, state: SourcesRun, s: _Src) -> None:
    if ctx.config.get("inside_zone"):
        s.src_zone = ctx.config["inside_zone"]
        return
    s.src_zone, s.src_zone_why = await _zone_of(ctx, state, s.src.fw_observed_src)


# ---------------------------------------------------------------- stage: policy_match


def _probe_row(probe: dict) -> dict:
    return {"destination": probe["ip"], "port": probe["port"], "protocol": probe["protocol"],
            "application": probe["application"], "from_zone": None, "to_zone": None,
            "rule": None, "rule_index": None, "action": None, "profiles": {},
            "dns_security": {}, "log_setting": None, "nat_rule": None, "checked": False}


def _label(row: dict) -> str:
    return f"{row['destination']} {row['protocol']}/{row['port']} {row['application']}"


async def _stage_policy_match(ctx: PreflightContext, state: SourcesRun, s: _Src) -> Stage:
    issues: list[tuple[str, str, str]] = []
    try:
        await _src_zone(ctx, state, s)
    except (PanosShapeError, PanosWriteRefused) as exc:
        s.src_zone_why = "the source zone lookup failed"
        issues.append(_classify(exc))
    for probe in ctx.config["probe_destinations"]:
        row = _probe_row(probe)
        s.probes.append(row)
        try:
            row["from_zone"] = s.src_zone
            dst_zone = ctx.config.get("outside_zone")
            why = ""
            if not dst_zone:
                dst_zone, why = await _zone_of(ctx, state, probe["ip"])
            row["to_zone"] = dst_zone
            if not s.src_zone or not dst_zone:
                issues.append((DEGRADED, codes.PN_ZONE_UNRESOLVED,
                               f"{_label(row)}: zone unresolved "
                               f"({s.src_zone_why or why}); policy not looked up"))
                continue
            m = await _op(ctx, cmd_match("security-policy-match", s.src_zone, dst_zone,
                                         s.src.fw_observed_src, probe), parse_policy_match)
            row["checked"] = True
            if m is None:
                issues.append((DEGRADED, codes.PN_NO_RULE_MATCH,
                               f"{_label(row)}: no configured rule matches"))
                continue
            row.update(rule=m.name, rule_index=m.index, action=m.action)
        except PanosTransportError:
            raise
        except Exception as exc:  # noqa: BLE001 - one probe's failure is that probe's
            st, c, d = _classify(exc)
            issues.append((st, c, f"{_label(row)}: {d}"))
    matched = [r for r in s.probes if r["rule"]]
    return _issue_stage("policy_match", issues, {},
                        "; ".join(f"{_label(r)} -> rule {r['rule']} (#{r['rule_index']})"
                                  for r in matched))


# ---------------------------------------------------------------- stage: rule_profiles


def _bucket(values: list[str]) -> str:
    vals = set(values)
    if not vals:
        return "none"
    return vals.pop() if len(vals) == 1 else "mixed"


def _scoped(actions: ProfileActions) -> list[dict]:
    return [{"name": r.name, "threat_name": r.threat_name, "category": r.category,
             "severities": list(r.severities), "bucket": r.bucket} for r in actions.scoped_rules]


async def _profile(ctx: PreflightContext, state: SourcesRun, ptype: str,
                   name: Optional[str]) -> tuple[dict, dict, list]:
    """(profile summary, dns_security summary or {}, issues) for one profile slot."""
    issues: list[tuple[str, str, str]] = []
    dns: dict = {}
    if not name:
        return {"profile": None, "source": "absent", "bucket": "none"}, dns, issues
    found = await _find(ctx, state, ptype, name)
    summary: dict[str, Any] = {"profile": name}
    if found is None:
        if (ptype, name) in PREDEFINED_PROFILES:
            table = PREDEFINED_PROFILES[(ptype, name)]
            summary["source"] = "predefined"
            if ptype == "virus":
                summary.update(by_decoder=dict(table), bucket=_bucket(list(table.values())))
            else:
                summary.update(by_severity=dict(table), scoped_rules=[],
                               bucket=_bucket(list(table.values())))
            if ptype == "spyware":
                dns = {"profile": name, "source": "predefined", "by_category": {},
                       "bucket": "unknown",
                       "note": "a predefined profile's DNS policy is not in running config"}
        elif (ptype, name) in PREDEFINED_UNCLASSIFIED:
            summary.update(source="predefined", bucket="not-classified")
            return summary, dns, issues
        else:
            summary.update(source="not-found", bucket="unknown")
            issues.append((DEGRADED, codes.PN_OBJECT_NOT_FOUND,
                           f"{ptype} profile {name!r} not found in vsys, shared or "
                           "Panorama-pushed config and not a predefined profile"))
            return summary, dns, issues
    else:
        summary["source"] = "config"
        if ptype not in ("virus", "spyware", "vulnerability"):
            summary["bucket"] = "not-classified"
            return summary, dns, issues
        try:
            acts = parse_profile_actions(found, ptype)
        except PanosShapeError as exc:
            raise _where(f"{ptype} profile {name!r}", exc) from None
        if ptype == "virus":
            summary.update(by_decoder=dict(acts.by_decoder),
                           bucket=_bucket(list(acts.by_decoder.values())))
        else:
            by_sev = {sv: acts.by_severity.get(sv, "none") for sv in SEVERITIES}
            summary.update(by_severity=by_sev, scoped_rules=_scoped(acts),
                           bucket=_bucket(list(by_sev.values())))
        if ptype == "spyware":
            dns = {"profile": name, "source": "config", "by_category": dict(acts.dns_security),
                   "bucket": _bucket(list(acts.dns_security.values()))}
    # issues from the classified actions
    where = f"{ptype} profile {name}"
    values = summary.get("by_severity") or summary.get("by_decoder") or {}
    blocks = sorted(k for k, v in values.items() if v in ("block", "sinkhole"))
    sigdef = sorted(k for k, v in values.items() if v == _SIG)
    silent = [k for k, v in values.items() if v in ("allow", "none")]
    if blocks:
        issues.append((DEGRADED, codes.PN_PROFILE_BLOCKS, f"{where} blocks at {', '.join(blocks)}"))
    if sigdef:
        issues.append((DEGRADED, codes.PN_PROFILE_SIGNATURE_DEFAULT,
                       f"{where} defers to signature default at {', '.join(sigdef)}"))
    if silent:
        issues.append((DEGRADED, codes.PN_NO_PROFILE,
                       f"{where} writes no threat log at " + ", ".join(
                           f"{k} ({values[k]})" for k in silent)))
    if summary.get("scoped_rules"):
        summary["note"] = (f"{len(summary['scoped_rules'])} rule(s) scoped to a threat name "
                           "or category are reported apart and not counted per severity")
    cats = dns.get("by_category") or {}
    sink = sorted(k for k, v in cats.items() if v == "sinkhole")
    dblock = sorted(k for k, v in cats.items() if v == "block")
    dsig = sorted(k for k, v in cats.items() if v == _SIG)
    if sink:
        issues.append((DEGRADED, codes.PN_DNS_SEC_SINKHOLE,
                       f"DNS Security in {name} sinkholes {', '.join(sink)}"))
    if dblock:
        issues.append((DEGRADED, codes.PN_PROFILE_BLOCKS,
                       f"DNS Security in {name} blocks {', '.join(dblock)}"))
    if dsig:
        issues.append((DEGRADED, codes.PN_PROFILE_SIGNATURE_DEFAULT,
                       f"DNS Security in {name} defers to the default action for {', '.join(dsig)}"))
    if dns.get("bucket") == "unknown":
        issues.append((DEGRADED, codes.PN_PROFILE_SIGNATURE_DEFAULT,
                       f"DNS Security in predefined {name} is not readable; its actions are unknown"))
    return summary, dns, issues


def _lfp_view(ctx: PreflightContext, name: str) -> Optional[tuple[str, str, str]]:
    """The issue (if any) stage 8 implies for a rule's log-setting."""
    st = next((x for x in (getattr(ctx.report, "stages", None) or [])
               if getattr(x, "id", None) == "log_forwarding_profiles"), None)
    profiles = (st.extra or {}).get("profiles") if st is not None else None
    if profiles is None:
        return (DEGRADED, codes.PN_RULE_LOG_PROFILE_INCOMPLETE,
                f"log forwarding profile {name} could not be confirmed complete: the "
                "log_forwarding_profiles stage did not read the profiles")
    mine = [p for p in profiles if p.get("name") == name]
    if not mine:
        return (DEGRADED, codes.PN_OBJECT_NOT_FOUND,
                f"log forwarding profile {name} is not among the profiles read in "
                "vsys, shared or Panorama-pushed config")
    if not any(p.get("complete") for p in mine):
        flagged = sorted({c for p in mine for c in p.get("issues", [])})
        return (DEGRADED, codes.PN_RULE_LOG_PROFILE_INCOMPLETE,
                f"log forwarding profile {name} is flagged by log_forwarding_profiles"
                + (f" ({', '.join(flagged)})" if flagged else ""))
    return None


async def _evaluate_rule(ctx: PreflightContext, state: SourcesRun, name: str,
                         match_action: Optional[str]) -> tuple[dict, list]:
    if name in state.rules:
        return state.rules[name]
    issues: list[tuple[str, str, str]] = []
    out: dict[str, Any] = {"profiles": {}, "dns_security": {}, "log_setting": None}
    found = await _find(ctx, state, "security-rule", name)
    if found is None:
        issues.append((DEGRADED, codes.PN_OBJECT_NOT_FOUND,
                       f"rule {name} matched but was not found in the local rulebase or "
                       "the Panorama pre/post rulebases"))
        if match_action and match_action not in _ALLOW_ACTIONS:
            issues.append((BLOCKED, codes.PN_RULE_DENIES,
                           f"rule {name} action is {match_action}"))
        state.rules[name] = (out, issues)
        return state.rules[name]
    try:
        rule = parse_rule(found)
    except PanosShapeError as exc:
        raise _where(f"security rule {name!r}", exc) from None
    out.update(log_setting=rule.log_setting, rule_disabled=rule.disabled)
    if rule.action not in _ALLOW_ACTIONS:
        issues.append((BLOCKED, codes.PN_RULE_DENIES, f"rule {name} action is {rule.action}"))
    slots: dict[str, Optional[str]] = {t: None for t in PROFILE_TYPES}
    if rule.group:
        out["profile_group"] = rule.group
        g = await _find(ctx, state, "profile-group", rule.group)
        if g is None:
            issues.append((DEGRADED, codes.PN_OBJECT_NOT_FOUND,
                           f"profile group {rule.group!r} (rule {name}) not found in any scope"))
            for t in PROFILE_TYPES:
                out["profiles"][t] = {"profile": None, "source": "group-not-found",
                                      "bucket": "unknown"}
            slots = {}
        else:
            try:
                members = parse_profile_group(g)
            except PanosShapeError as exc:
                raise _where(f"profile group {rule.group!r}", exc) from None
            slots.update({t: members.get(t) for t in PROFILE_TYPES})
    else:
        slots.update({t: rule.profiles.get(t) for t in PROFILE_TYPES})
    if slots and rule.action in _ALLOW_ACTIONS:
        absent = [t for t, n in slots.items() if not n]
        if len(absent) == len(PROFILE_TYPES):
            issues.append((DEGRADED, codes.PN_NO_PROFILE,
                           f"rule {name} allows with no security profile: no threat "
                           "inspection, no threat log"))
        elif absent:
            issues.append((DEGRADED, codes.PN_NO_PROFILE,
                           f"rule {name} attaches no {', '.join(absent)} profile"))
    for t, pname in slots.items():
        summary, dns, p_issues = await _profile(ctx, state, t, pname)
        out["profiles"][t] = summary
        if dns:
            out["dns_security"] = dns
        if rule.action in _ALLOW_ACTIONS:
            issues.extend(p_issues)
    if not out["dns_security"]:
        out["dns_security"] = {"profile": None, "by_category": {}, "bucket": "none"}
    if rule.action in _ALLOW_ACTIONS:
        if not rule.log_setting:
            issues.append((DEGRADED, codes.PN_RULE_NO_LOG_FORWARDING,
                           f"rule {name} has no log forwarding profile"))
        else:
            lf = _lfp_view(ctx, rule.log_setting)
            if lf:
                issues.append(lf)
    state.rules[name] = (out, issues)
    return state.rules[name]


async def _stage_rule_profiles(ctx: PreflightContext, state: SourcesRun, s: _Src) -> Stage:
    matched = [r for r in s.probes if r["rule"]]
    if not matched:
        why = ("no probe matched a configured rule" if any(r["checked"] for r in s.probes)
               else "no probe's policy was looked up")
        code = (codes.PN_NO_RULE_MATCH if any(r["checked"] for r in s.probes)
                else codes.PN_ZONE_UNRESOLVED)
        if not s.probes:
            code, why = codes.PN_SHAPE_UNRECOGNISED, "policy_match did not complete"
        return Stage("rule_profiles", SKIPPED, code, f"not checked: {why}",
                     remediation=_rem(code))
    issues: list[tuple[str, str, str]] = []
    for row in matched:
        try:
            out, r_issues = await _evaluate_rule(ctx, state, row["rule"], row["action"])
        except PanosTransportError:
            raise
        except Exception as exc:  # noqa: BLE001
            st, c, d = _classify(exc)
            issues.append((st, c, f"rule {row['rule']}: {d}"))
            continue
        row.update(profiles=out["profiles"], dns_security=out["dns_security"],
                   log_setting=out["log_setting"])
        if out.get("profile_group"):
            row["profile_group"] = out["profile_group"]
        issues.extend(r_issues)
    rules = sorted({r["rule"] for r in matched})
    return _issue_stage("rule_profiles", issues, {},
                        f"rule(s) {', '.join(rules)} allow with alerting virus, spyware and "
                        "vulnerability profiles on every severity and a complete log "
                        "forwarding profile (url-filtering and wildfire-analysis actions are "
                        "reported by name, not classified)")


# ---------------------------------------------------------------- stage: nat


async def _stage_nat(ctx: PreflightContext, state: SourcesRun, s: _Src) -> Stage:
    ready = [r for r in s.probes if r["from_zone"] and r["to_zone"]]
    if not ready:
        return Stage("nat", SKIPPED, codes.PN_ZONE_UNRESOLVED,
                     "not checked: no probe has resolved zones", remediation=_rem(codes.PN_ZONE_UNRESOLVED))
    issues: list[tuple[str, str, str]] = []
    notes = []
    probes = {(p["ip"], p["port"], p["protocol"], p["application"]): p
              for p in ctx.config["probe_destinations"]}
    for row in ready:
        probe = probes[(row["destination"], row["port"], row["protocol"], row["application"])]
        try:
            m = await _op(ctx, cmd_match("nat-policy-match", row["from_zone"], row["to_zone"],
                                         s.src.fw_observed_src, probe), parse_nat_match)
        except PanosTransportError:
            raise
        except Exception as exc:  # noqa: BLE001
            st, c, d = _classify(exc)
            issues.append((st, c, f"{_label(row)}: {d}"))
            continue
        row["nat_rule"] = m.name if m else None
        notes.append(f"{_label(row)}: " + (f"egress NAT rule {m.name} (the pre-NAT source "
                                            "is what the firewall logs)" if m else "no NAT rule"))
    skipped = len(s.probes) - len(ready)
    if skipped:
        issues.append((SKIPPED, codes.PN_ZONE_UNRESOLVED,
                       f"{skipped} probe(s) not checked: zones unresolved"))
    return _issue_stage("nat", issues, {}, "; ".join(notes))


# ---------------------------------------------------------------- stage: user_id


async def _stage_user_id(ctx: PreflightContext, state: SourcesRun, s: _Src) -> Stage:
    ip = s.src.fw_observed_src
    issues: list[tuple[str, str, str]] = []
    users = await _op(ctx, cmd_user_mapping(ip), parse_user_mapping)
    extra: dict[str, Any] = {"users": users, "zone": s.src_zone, "zone_user_id": None}
    if not users:
        issues.append((DEGRADED, codes.PN_NO_USER_MAPPING, f"no user mapping for {ip}"))
    if not s.src_zone:
        issues.append((DEGRADED, codes.PN_ZONE_UNRESOLVED,
                       f"source zone unresolved ({s.src_zone_why or 'not looked up'}), so "
                       "whether User-ID is on for it is unknown"))
    else:
        xpath = xp(ctx, f"zone/entry[@name='{s.src_zone}']")
        if not allowed_xpath(xpath):
            issues.append((DEGRADED, codes.PN_OBJECT_NOT_FOUND,
                           f"zone {s.src_zone!r} cannot be read (name outside the readable set)"))
        else:
            try:
                on = await _config(ctx, xpath, parse_zone_userid)
            except PanosShapeError as exc:
                if exc.code != codes.PN_OBJECT_NOT_FOUND:
                    raise
                issues.append((DEGRADED, codes.PN_OBJECT_NOT_FOUND,
                               f"zone {s.src_zone!r} not found in {ctx.config['vsys']}"))
            else:
                extra["zone_user_id"] = on
                if not on:
                    issues.append((DEGRADED, codes.PN_ZONE_USERID_OFF,
                                   f"User-ID is off on zone {s.src_zone}"))
    return _issue_stage("user_id", issues, extra,
                        f"{ip} maps to {', '.join(users)}; User-ID on in zone {s.src_zone}")


# ---------------------------------------------------------------- stage: registered_tags


async def _stage_registered_tags(ctx: PreflightContext, state: SourcesRun, s: _Src) -> Stage:
    ip = s.src.fw_observed_src
    mapping = await _op(ctx, cmd_registered_ip(ip), parse_registered_ips)
    key = _ip_key(ip)
    tags = sorted({t for addr, ts in mapping.items() if _valid_ip(addr) and _ip_key(addr) == key
                   for t in ts})
    extra = {"tags": tags}
    if tags:
        return _issue_stage("registered_tags", [(DEGRADED, codes.PN_SOURCE_TAGGED,
                                                 f"{ip} carries tag(s): {', '.join(tags)}")],
                            extra, "")
    return Stage("registered_tags", OK, codes.PN_OK, f"{ip} carries no registered tag", extra=extra)


# ---------------------------------------------------------------- runner

_NETWORK_STAGES: tuple[tuple[str, Callable[..., Awaitable[Stage]]], ...] = (
    ("source_seen", _stage_source_seen),
    ("policy_match", _stage_policy_match),
    ("rule_profiles", _stage_rule_profiles),
    ("nat", _stage_nat),
    ("user_id", _stage_user_id),
    ("registered_tags", _stage_registered_tags),
)

_HALT_DETAIL = {
    codes.PN_CALL_BUDGET: "not checked: the preflight call budget ran out",
    codes.PN_SKIPPED_UNREACHABLE: "not checked: the firewall stopped answering",
    codes.PN_SKIPPED_AUTH: "not checked: the firewall rejected the API key",
}


def _skip(sid: str, code: str, detail: str) -> Stage:
    return Stage(sid, SKIPPED, code, detail, remediation=_rem(code))


async def _run_stage(sid: str, fn, ctx: PreflightContext, state: SourcesRun, s: _Src) -> Stage:
    try:
        return await fn(ctx, state, s)
    except _Halt as h:
        return _skip(sid, h.code, _HALT_DETAIL[h.code])
    except Exception as exc:  # noqa: BLE001 - one stage's defect must not lose the report
        try:
            _maybe_halt(exc, state)
        except _Halt as h:
            return _skip(sid, h.code, _HALT_DETAIL[h.code])
        st, c, d = _classify(exc)
        return Stage(sid, st, c, d, remediation=_rem(c))


async def check_source(ctx: PreflightContext, src: ResolvedSource,
                       state: Optional[SourcesRun] = None) -> dict[str, Any]:
    state = state if state is not None else SourcesRun()
    s = _Src(src)
    stages = [_stage_address(s)]
    for sid, fn in _NETWORK_STAGES:
        if src.fw_observed_src is None:
            stages.append(_skip(sid, codes.PN_SOURCE_UNKNOWN,
                                "not checked: the firewall-observed source is unknown, so "
                                "no command was sent for it"))
        elif state.halt:
            stages.append(_skip(sid, state.halt, _HALT_DETAIL[state.halt]))
        else:
            stages.append(await _run_stage(sid, fn, ctx, state, s))
    statuses = [st.status for st in stages]
    status = (BLOCKED if BLOCKED in statuses else
              DEGRADED if (DEGRADED in statuses or SKIPPED in statuses) else OK)
    for row in s.probes:
        row.pop("checked", None)
    return {
        "agent_id": src.agent_id, "hostname": src.hostname,
        "fw_observed_src": src.fw_observed_src, "fw_src_provenance": src.fw_src_provenance,
        "agent_addresses": list(src.agent_addresses),
        "agent_addr_provenance": src.agent_addr_provenance,
        "nat_verdict": src.nat_verdict, "collision_with": list(src.collision_with),
        "status": status, "stages": [st.to_dict() for st in stages], "probes": s.probes,
    }


async def check_sources(ctx: PreflightContext, agents: list[AgentRef]) -> list[dict[str, Any]]:
    if not agents:
        return [{"code": codes.PN_NO_AGENTS, "status": DEGRADED, "detail": NO_AGENTS_DETAIL}]
    state = SourcesRun()
    return [await check_source(ctx, r, state) for r in resolve_addresses(agents, ctx.config)]

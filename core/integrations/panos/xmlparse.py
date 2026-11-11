# core/integrations/panos/xmlparse.py
"""Strict parsers for PAN-OS XML API replies (proven against 10.2.8).

Every parser raises ``PanosShapeError`` for a shape it does not recognise. None returns
an empty value for an unrecognised shape: the only empty returns are for shapes the
firewall really emits for "nothing there" (an empty ``<result/>`` or ``count="0"``), and
each is named at its parser. Stdlib ElementTree only; DOCTYPE/ENTITY is refused up front.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import codes
from .exceptions import PanosApiError, PanosShapeError

_SEVERITIES = ("critical", "high", "medium", "low", "informational")
_BLOCK_ACTIONS = frozenset({"drop", "reset-client", "reset-server", "reset-both", "block-ip", "block"})


# ---------------------------------------------------------------- transport envelope

def safe_fromstring(data: "bytes | str") -> ET.Element:
    """Parse XML, refusing DTDs and entities outright (billion-laughs / XXE)."""
    raw = data.encode("utf-8") if isinstance(data, str) else bytes(data)
    # Also check with NULs removed so a UTF-16/32 body cannot hide the marker.
    for candidate in (raw, raw.replace(b"\x00", b"")):
        low = candidate.lower()
        if b"<!doctype" in low or b"<!entity" in low:
            raise PanosShapeError("response contains a DOCTYPE/ENTITY declaration; refused")
    try:
        return ET.fromstring(raw)
    except (ET.ParseError, ValueError) as exc:
        raise PanosShapeError(f"response is not well-formed XML ({exc})") from None


def _error_message(root: ET.Element) -> str:
    msg = root.find("result/msg")
    if msg is None:
        msg = root.find("msg")
    if msg is None:
        return "PAN-OS returned status=error"
    lines = [(ln.text or "").strip() for ln in msg.findall("line")]
    lines = [ln for ln in lines if ln]
    if lines:
        return "; ".join(lines)
    return (msg.text or "").strip() or "PAN-OS returned status=error"


def parse_response(body: bytes) -> ET.Element:
    """Return ``<result>`` (or the root when there is none) of a successful reply."""
    root = safe_fromstring(body)
    if root.tag != "response":
        raise PanosShapeError(f"expected a <response> root, got <{root.tag}>")
    status = root.get("status")
    if status == "success":
        result = root.find("result")
        return result if result is not None else root
    if status == "error":
        panos_code = root.get("code")
        raise PanosApiError(
            _error_message(root),
            code=codes.PN_AUTH_REJECTED if panos_code == "403" else None,
            panos_code=panos_code,
        )
    raise PanosShapeError(f"response status is {status!r}, expected success or error")


# ---------------------------------------------------------------- helpers

def _text(el: Optional[ET.Element], path: str) -> Optional[str]:
    if el is None:
        return None
    found = el.find(path)
    if found is None:
        return None
    t = (found.text or "").strip()
    return t or None


def _req_text(el: ET.Element, path: str, what: str) -> str:
    t = _text(el, path)
    if t is None:
        raise PanosShapeError(f"{what}: missing <{path}>")
    return t


def _yesno(text: Optional[str], what: str, *, default: Optional[bool] = None) -> bool:
    if text is None:
        if default is None:
            raise PanosShapeError(f"{what}: missing yes/no value")
        return default
    if text == "yes":
        return True
    if text == "no":
        return False
    raise PanosShapeError(f"{what}: expected yes or no, got {text!r}")


def _members(el: Optional[ET.Element]) -> list[str]:
    if el is None:
        return []
    return [(m.text or "").strip() for m in el.findall("member") if (m.text or "").strip()]


def _is_empty(result: ET.Element) -> bool:
    return len(result) == 0 and not (result.text or "").strip()


def _config_child(result: ET.Element, tag: str) -> Optional[ET.Element]:
    """The requested config element, or None for the firewall's "nothing configured"
    reply (an empty result). Any other result without it is a shape error."""
    child = result.find(tag)
    if child is not None:
        return child
    if _is_empty(result):
        return None
    raise PanosShapeError(f"expected <{tag}> under <{result.tag}>")


def _entry(result: ET.Element, what: str) -> ET.Element:
    """The single ``<entry>`` of an object lookup. Empty result = object absent."""
    entry = result.find("entry")
    if entry is not None:
        return entry
    if _is_empty(result):
        raise PanosShapeError(f"{what} not found in this configuration scope",
                              code=codes.PN_OBJECT_NOT_FOUND)
    raise PanosShapeError(f"{what}: expected <entry> under <{result.tag}>")


def classify_action(raw: str) -> str:
    raw = raw.strip()
    if raw == "default":
        return "signature-default"
    if raw in _BLOCK_ACTIONS:
        return "block"
    if raw in ("alert", "allow", "sinkhole"):
        return raw
    raise PanosShapeError(f"unrecognised profile action {raw!r}")


def _action_value(el: Optional[ET.Element], what: str) -> str:
    """An action is a child tag (``<action><alert/></action>``) or text (``<action>default</action>``)."""
    if el is None:
        raise PanosShapeError(f"{what}: missing <action>")
    kids = list(el)
    if kids:
        return kids[0].tag
    t = (el.text or "").strip()
    if not t:
        raise PanosShapeError(f"{what}: empty <action>")
    return t


# ---------------------------------------------------------------- keygen / system

def parse_keygen(result: ET.Element) -> str:
    key = result.find("key")
    if key is None:
        key = result.find("result/key")
    if key is None or not (key.text or "").strip():
        raise PanosShapeError("keygen reply carries no <key>")
    return key.text.strip()


@dataclass(frozen=True)
class SystemInfo:
    hostname: str
    model: str
    serial: str
    sw_version: str
    multi_vsys: bool
    operational_mode: str
    advanced_routing: bool
    cloud_mode: Optional[str]


def _onoff(text: str, what: str) -> bool:
    if text == "on":
        return True
    if text == "off":
        return False
    raise PanosShapeError(f"{what}: expected on or off, got {text!r}")


def parse_system_info(result: ET.Element) -> SystemInfo:
    s = result.find("system")
    if s is None:
        raise PanosShapeError("expected <system> under <result>")
    return SystemInfo(
        hostname=_req_text(s, "hostname", "system info"),
        model=_req_text(s, "model", "system info"),
        serial=_req_text(s, "serial", "system info"),
        sw_version=_req_text(s, "sw-version", "system info"),
        multi_vsys=_onoff(_req_text(s, "multi-vsys", "system info"), "multi-vsys"),
        operational_mode=_req_text(s, "operational-mode", "system info"),
        advanced_routing=_onoff(_req_text(s, "advanced-routing", "system info"), "advanced-routing"),
        cloud_mode=_text(s, "cloud-mode"),
    )


# ---------------------------------------------------------------- clock

def parse_clock(result: ET.Element) -> str:
    if len(result):
        raise PanosShapeError("clock reply carries child elements; expected text")
    text = (result.text or "").strip()
    if not text:
        raise PanosShapeError("clock reply is empty")
    return text


def parse_timezone(result: ET.Element) -> Optional[str]:
    """Configured timezone, or None when the firewall reports nothing configured."""
    tz = _config_child(result, "timezone")
    if tz is None:
        return None
    t = (tz.text or "").strip()
    if not t:
        raise PanosShapeError("<timezone> is empty")
    return t


_CLOCK_RE = re.compile(
    r"^[A-Za-z]{3} ([A-Za-z]{3}) (\d{1,2}) (\d{2}:\d{2}:\d{2})(?: [A-Z]{2,5})? (\d{4})$")


def clock_to_utc(text: str, tz: Optional[str]) -> Optional[datetime]:
    """Firewall wall-clock text -> aware UTC. None when the zone is unknown."""
    m = _CLOCK_RE.match(" ".join(text.split()))
    if not m:
        raise PanosShapeError(f"unrecognised clock text {text!r}")
    try:
        naive = datetime.strptime(" ".join(m.groups()), "%b %d %H:%M:%S %Y")
    except ValueError:
        raise PanosShapeError(f"unrecognised clock text {text!r}") from None
    if not tz:
        return None
    try:
        zone = ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return None
    return naive.replace(tzinfo=zone).astimezone(timezone.utc)


# ---------------------------------------------------------------- logging status

@dataclass(frozen=True)
class LogTypeRow:
    created: Optional[datetime]
    forwarded: Optional[datetime]
    total: int


@dataclass(frozen=True)
class CollectorStatus:
    name: str
    connection_ip: Optional[str]
    active: bool
    types: dict[str, LogTypeRow]


_TS = r"(?:Not Available|\d{4}/\d\d/\d\d \d\d:\d\d:\d\d)"
_ROW_RE = re.compile(rf"^\s*(\S+)\s+({_TS})\s+({_TS})\s+(\d+)\s+(\d+)\s+(\d+)\s*$")
_LABEL_RE = re.compile(
    r"^\s*(Log Collector|Connection IP|Conn Source IP|High speed mode|Connection Status|Rate)\s*:\s*(.*?)\s*$")


def _ts(s: str) -> Optional[datetime]:
    return None if s == "Not Available" else datetime.strptime(s, "%Y/%m/%d %H:%M:%S")


def parse_logging_status(result: ET.Element) -> list[CollectorStatus]:
    conn = result.find("show-logging-status/Conn-Info")
    if conn is None:
        raise PanosShapeError("expected <show-logging-status>/<Conn-Info>")
    # The labelled text trails the <ConnStatus> child, so collect Conn-Info's own text
    # and each child's tail; the child's content is the API status, not collector text.
    text = (conn.text or "") + "".join(child.tail or "" for child in conn)

    collectors: list[CollectorStatus] = []
    cur: Optional[dict] = None

    def finish() -> None:
        if cur is None:
            return
        if cur["status"] is None:
            raise PanosShapeError(f"collector {cur['name']!r} has no Connection Status line")
        collectors.append(CollectorStatus(
            name=cur["name"], connection_ip=cur["ip"],
            active=re.search(r"\bActive\b", cur["status"]) is not None, types=cur["types"]))

    for line in text.splitlines():
        if not line.strip():
            continue
        lm = _LABEL_RE.match(line)
        if lm:
            label, value = lm.groups()
            if label == "Log Collector":
                finish()
                cur = {"name": value, "ip": None, "status": None, "types": {}}
                continue
            if cur is None:
                raise PanosShapeError(f"{label!r} line before any Log Collector line")
            if label == "Connection IP":
                cur["ip"] = value or None
            elif label == "Connection Status":
                cur["status"] = value
            continue  # Conn Source IP / High speed mode / Rate: recognised, unused
        rm = _ROW_RE.match(line)
        if rm and cur is not None:
            kind, created, fwded, _seq, _ack, total = rm.groups()
            cur["types"][kind] = LogTypeRow(_ts(created), _ts(fwded), int(total))
            continue
        raise PanosShapeError(f"unrecognised logging-status line: {line.strip()[:80]!r}")
    finish()
    if not collectors:
        raise PanosShapeError("logging-status lists no Log Collector")
    return collectors


# ---------------------------------------------------------------- device logging config

@dataclass(frozen=True)
class LsfConfig:
    enabled: bool
    region: Optional[str]


def parse_lsf_config(result: ET.Element) -> LsfConfig:
    """Absent config (empty result, or no logging-service-forwarding element) = disabled."""
    logging = _config_child(result, "logging")
    lsf = logging.find("logging-service-forwarding") if logging is not None else None
    if lsf is None:
        return LsfConfig(enabled=False, region=None)
    return LsfConfig(enabled=_yesno(_text(lsf, "enable"), "logging-service-forwarding/enable"),
                     region=_text(lsf, "logging-service-regions"))


def parse_eal_device(result: ET.Element) -> bool:
    logging = _config_child(result, "logging")
    eal = logging.find("enhanced-application-logging") if logging is not None else None
    if eal is None:
        return False
    return _yesno(_text(eal, "enable"), "enhanced-application-logging/enable")


# ---------------------------------------------------------------- log forwarding profiles

@dataclass(frozen=True)
class LfMatch:
    log_type: str
    filter: Optional[str]
    send_to_panorama: bool
    syslog_profiles: list[str]


@dataclass(frozen=True)
class LfProfile:
    name: str
    eal: bool
    matches: list[LfMatch]
    has_tag_action: bool


def parse_log_forwarding_profiles(result: ET.Element) -> list[LfProfile]:
    profiles = _config_child(result, "profiles")
    if profiles is None:
        return []
    out: list[LfProfile] = []
    for e in profiles.findall("entry"):
        name = e.get("name")
        if not name:
            raise PanosShapeError("log-forwarding profile entry has no name")
        matches: list[LfMatch] = []
        tag_action = False
        for m in e.findall("match-list/entry"):
            matches.append(LfMatch(
                log_type=_req_text(m, "log-type", f"log-forwarding profile {name}"),
                filter=_text(m, "filter"),
                send_to_panorama=_yesno(_text(m, "send-to-panorama"), "send-to-panorama", default=False),
                syslog_profiles=_members(m.find("send-syslog")),
            ))
            if m.find("actions/entry/type/tagging") is not None:
                tag_action = True
        out.append(LfProfile(
            name=name,
            eal=_yesno(_text(e, "enhanced-application-logging"),
                       "enhanced-application-logging", default=False),
            matches=matches, has_tag_action=tag_action))
    return out


# ---------------------------------------------------------------- syslog

@dataclass(frozen=True)
class SyslogServer:
    name: str
    server: str
    transport: str
    port: int
    format: str
    facility: str


@dataclass(frozen=True)
class SyslogProfile:
    name: str
    servers: list[SyslogServer]


def parse_syslog_profiles(result: ET.Element) -> list[SyslogProfile]:
    """PAN-OS defaults (UDP, 514, BSD, LOG_USER) apply to omitted optional fields."""
    syslog = _config_child(result, "syslog")
    if syslog is None:
        return []
    out: list[SyslogProfile] = []
    for e in syslog.findall("entry"):
        name = e.get("name")
        if not name:
            raise PanosShapeError("syslog profile entry has no name")
        servers: list[SyslogServer] = []
        for s in e.findall("server/entry"):
            sname = s.get("name")
            if not sname:
                raise PanosShapeError(f"syslog profile {name}: server entry has no name")
            port_text = _text(s, "port") or "514"
            if not port_text.isdigit():
                raise PanosShapeError(f"syslog profile {name}/{sname}: port {port_text!r} is not a number")
            servers.append(SyslogServer(
                name=sname, server=_req_text(s, "server", f"syslog profile {name}/{sname}"),
                transport=_text(s, "transport") or "UDP", port=int(port_text),
                format=_text(s, "format") or "BSD", facility=_text(s, "facility") or "LOG_USER"))
        out.append(SyslogProfile(name=name, servers=servers))
    return out


# ---------------------------------------------------------------- decryption / tags

def parse_decryption_rules(result: ET.Element) -> list[tuple[str, bool]]:
    rules = _config_child(result, "rules")
    if rules is None:
        return []
    out = []
    for e in rules.findall("entry"):
        name = e.get("name")
        if not name:
            raise PanosShapeError("decryption rule entry has no name")
        out.append((name, not _yesno(_text(e, "disabled"), "decryption rule disabled", default=False)))
    return out


def parse_registered_ips(result: ET.Element) -> dict[str, list[str]]:
    if _is_empty(result):
        return {}
    if not len(result):
        raise PanosShapeError("registered-ip reply is text, expected <entry> elements")
    out: dict[str, list[str]] = {}
    for child in result:
        if child.tag != "entry":
            raise PanosShapeError(f"unexpected <{child.tag}> in registered-ip reply")
        ip = child.get("ip")
        if not ip:
            raise PanosShapeError("registered-ip <entry> has no ip attribute")
        out[ip] = _members(child.find("tag"))
    return out


# ---------------------------------------------------------------- policy / NAT match

@dataclass(frozen=True)
class PolicyMatch:
    name: str
    index: int
    action: str


@dataclass(frozen=True)
class NatMatch:
    name: str
    index: Optional[int]


def _first_rule_entry(result: ET.Element) -> Optional[ET.Element]:
    rules = _config_child(result, "rules")
    if rules is None:
        return None
    entry = rules.find("entry")
    if entry is None:
        return None
    if not entry.get("name"):
        raise PanosShapeError("matched rule <entry> has no name")
    return entry


def parse_policy_match(result: ET.Element) -> Optional[PolicyMatch]:
    entry = _first_rule_entry(result)
    if entry is None:
        return None
    idx = _req_text(entry, "index", "policy match")
    if not idx.isdigit():
        raise PanosShapeError(f"policy match index {idx!r} is not a number")
    return PolicyMatch(name=entry.get("name"), index=int(idx),
                       action=_req_text(entry, "action", "policy match"))


def parse_nat_match(result: ET.Element) -> Optional[NatMatch]:
    entry = _first_rule_entry(result)
    if entry is None:
        return None
    idx = _text(entry, "index")
    if idx is not None and not idx.isdigit():
        raise PanosShapeError(f"NAT match index {idx!r} is not a number")
    return NatMatch(name=entry.get("name"), index=int(idx) if idx is not None else None)


# ---------------------------------------------------------------- rule / profiles

@dataclass(frozen=True)
class RuleConfig:
    name: str
    action: str
    disabled: bool
    group: Optional[str]
    profiles: dict[str, str]
    log_setting: Optional[str]


def parse_rule(result: ET.Element) -> RuleConfig:
    e = _entry(result, "security rule")
    name = e.get("name")
    if not name:
        raise PanosShapeError("security rule <entry> has no name")
    group_members = _members(e.find("profile-setting/group"))
    profiles: dict[str, str] = {}
    ps = e.find("profile-setting/profiles")
    if ps is not None:
        for child in ps:
            members = _members(child)
            if members:
                profiles[child.tag] = members[0]
    return RuleConfig(
        name=name, action=_req_text(e, "action", f"security rule {name}"),
        disabled=_yesno(_text(e, "disabled"), "rule disabled", default=False),
        group=group_members[0] if group_members else None,
        profiles=profiles, log_setting=_text(e, "log-setting"))


def parse_profile_group(result: ET.Element) -> dict[str, str]:
    e = _entry(result, "profile group")
    out: dict[str, str] = {}
    for child in e:
        members = _members(child)
        if members:
            out[child.tag] = members[0]
    return out


@dataclass(frozen=True)
class ProfileActions:
    by_severity: dict[str, str] = field(default_factory=dict)
    dns_security: dict[str, str] = field(default_factory=dict)
    by_decoder: dict[str, str] = field(default_factory=dict)


def parse_profile_actions(result: ET.Element, ptype: str) -> ProfileActions:
    """Per-severity action buckets for spyware/vulnerability (first matching rule wins,
    as the firewall evaluates top-down); per-decoder for virus; DNS Security separately."""
    if ptype not in ("spyware", "vulnerability", "virus"):
        raise ValueError(f"unsupported profile type {ptype!r}")
    e = _entry(result, f"{ptype} profile")
    by_sev: dict[str, str] = {}
    dns: dict[str, str] = {}
    by_dec: dict[str, str] = {}
    if ptype == "virus":
        for d in e.findall("decoder/entry"):
            dname = d.get("name")
            if not dname:
                raise PanosShapeError("virus decoder entry has no name")
            by_dec[dname] = classify_action(_action_value(d.find("action"), f"decoder {dname}"))
        return ProfileActions(by_decoder=by_dec)
    for r in e.findall("rules/entry"):
        rname = r.get("name") or "?"
        bucket = classify_action(_action_value(r.find("action"), f"{ptype} rule {rname}"))
        sevs = _members(r.find("severity"))
        if not sevs:
            raise PanosShapeError(f"{ptype} rule {rname}: no severity")
        expanded: list[str] = []
        for s in sevs:
            if s == "any":
                expanded.extend(_SEVERITIES)
            elif s in _SEVERITIES:
                expanded.append(s)
            else:
                raise PanosShapeError(f"{ptype} rule {rname}: unknown severity {s!r}")
        for s in expanded:
            by_sev.setdefault(s, bucket)
    if ptype == "spyware":
        for path in ("botnet-domains/lists/entry", "botnet-domains/dns-security-categories/entry"):
            for d in e.findall(path):
                dname = d.get("name")
                if not dname:
                    raise PanosShapeError("DNS security entry has no name")
                dns[dname] = classify_action(_action_value(d.find("action"), f"DNS security {dname}"))
    return ProfileActions(by_severity=by_sev, dns_security=dns)


# ---------------------------------------------------------------- user / session / routing / zones

def parse_user_mapping(result: ET.Element) -> list[str]:
    if _is_empty(result):
        return []
    if not len(result):
        raise PanosShapeError("user mapping reply is text, expected <entry> elements")
    users = []
    for child in result:
        if child.tag != "entry":
            raise PanosShapeError(f"unexpected <{child.tag}> in user mapping reply")
        users.append(_req_text(child, "user", "user mapping entry"))
    return users


def parse_session_count(result: ET.Element) -> int:
    if len(result):
        raise PanosShapeError("session count reply carries child elements; expected a number")
    t = (result.text or "").strip()
    if not t.isdigit():
        raise PanosShapeError(f"session count {t!r} is not a number")
    return int(t)


def parse_fib(result: ET.Element) -> Optional[str]:
    """Egress interface, or None for the firewall's empty "no route" reply."""
    if _is_empty(result):
        return None
    iface = _text(result, "interface")
    if iface is None:
        raise PanosShapeError("fib-lookup reply has no <interface>")
    return iface


def parse_zone_userid(result: ET.Element) -> bool:
    e = _entry(result, "zone")
    return _yesno(_text(e, "enable-user-identification"), "enable-user-identification", default=False)


def parse_interface_zone(result: ET.Element, iface: str) -> Optional[str]:
    zones = _config_child(result, "zone")
    if zones is None:
        return None
    for z in zones.findall("entry"):
        network = z.find("network")
        if network is None:
            continue
        for mode in network:
            if iface in _members(mode):
                name = z.get("name")
                if not name:
                    raise PanosShapeError("zone <entry> has no name")
                return name
    return None

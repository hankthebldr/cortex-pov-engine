# core/integrations/panos/host.py
"""THE management-host validator and panos_ngfw config validator. One definition.

The API key is sent to whatever host the config names, so a malformed host
(scheme, path, userinfo, query) is rejected before any request is built.
"""
from __future__ import annotations

import copy
import ipaddress
import re
from typing import Any

from .exceptions import PanosConfigError

_LABEL = re.compile(r"(?!-)[a-z0-9-]{1,63}(?<!-)")
_PORT = re.compile(r"[0-9]{1,5}")
VSYS_RE = re.compile(r"vsys[0-9]+")
# THE device-name rule: config validation and the client's xpath allowlist both use it,
# so a value that validates is never refused at read time. ASCII only.
DEVICE_NAME_PATTERN = r"[A-Za-z0-9._-]+"
DEVICE_NAME_RE = re.compile(DEVICE_NAME_PATTERN)
FORWARDING_PATHS = ("auto", "sls", "syslog", "both")

DEFAULT_PROBES: list[dict[str, Any]] = [
    {"ip": "1.1.1.1", "port": 53, "protocol": "udp", "application": "dns-base"},
    {"ip": "198.51.100.10", "port": 80, "protocol": "tcp", "application": "web-browsing"},
    {"ip": "198.51.100.10", "port": 443, "protocol": "tcp", "application": "ssl"},
]


def _port(text: str, raw: str) -> int:
    if not _PORT.fullmatch(text) or not (1 <= int(text) <= 65535):
        raise PanosConfigError(f"management host port must be 1-65535 (got {_safe(raw)})")
    return int(text)


def normalize_mgmt_host(raw: str) -> str:
    """Return ``host`` or ``host:port`` (lowercased, IPv6 bracketed), or raise."""
    value = (raw or "").strip()
    if not value:
        raise PanosConfigError("management host is empty; expected an IP or DNS name[:port]")
    if any(ch in value for ch in "/?#@\\ \t\r\n") or "://" in value:
        raise PanosConfigError(
            "management host must be an IP or DNS name with an optional :port — "
            f"no scheme, path, query or userinfo (got {_safe(value)})")
    port = None
    if value.startswith("["):
        end = value.find("]")
        if end < 0:
            raise PanosConfigError(f"unterminated IPv6 bracket (got {_safe(value)})")
        host, rest = value[1:end], value[end + 1:]
        if rest:
            if not rest.startswith(":"):
                raise PanosConfigError(f"unexpected text after IPv6 literal (got {_safe(value)})")
            port = _port(rest[1:], value)
        try:
            ip = ipaddress.IPv6Address(host)
        except ValueError as exc:
            raise PanosConfigError(f"invalid IPv6 literal (got {_safe(value)})") from exc
        out = f"[{ip.compressed}]"
    else:
        try:
            out = f"[{ipaddress.IPv6Address(value).compressed}]"
        except ValueError:
            host = value
            if value.count(":") == 1:
                host, ptxt = value.split(":", 1)
                port = _port(ptxt, value)
            elif ":" in value:
                raise PanosConfigError(f"invalid management host (got {_safe(value)})")
            try:
                out = str(ipaddress.IPv4Address(host))
            except ValueError:
                host = host.lower().rstrip(".")
                if (not host or len(host) > 253
                        or not all(_LABEL.fullmatch(lbl) for lbl in host.split("."))):
                    raise PanosConfigError(
                        f"invalid management host name (got {_safe(value)})") from None
                out = host
    return f"{out}:{port}" if port is not None else out


def _safe(value: str) -> str:
    flat = " ".join(str(value).split())
    return flat if len(flat) <= 120 else flat[:117] + "..."


def _ip(value: Any, what: str) -> str:
    try:
        return str(ipaddress.ip_address(str(value)))
    except ValueError as exc:
        raise PanosConfigError(f"{what} must be an IP address (got {_safe(str(value))})") from exc


def _probe(p: Any) -> dict[str, Any]:
    if not isinstance(p, dict):
        raise PanosConfigError("each probe destination must be an object")
    proto = p.get("protocol")
    if proto not in ("tcp", "udp"):
        raise PanosConfigError(f"probe protocol must be tcp or udp (got {_safe(str(proto))})")
    port = p.get("port")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise PanosConfigError("probe port must be an integer 1-65535")
    app = p.get("application")
    if not isinstance(app, str) or not app.strip():
        raise PanosConfigError("probe application must be a non-empty string")
    return {"ip": _ip(p.get("ip"), "probe ip"), "port": port, "protocol": proto,
            "application": app.strip()}


def validate_panos_config(config: dict) -> dict:
    """Validate a ``panos_ngfw`` config; return it with defaults filled."""
    if not isinstance(config, dict):
        raise PanosConfigError("config must be an object")
    cfg = copy.deepcopy(config)
    cfg["host"] = normalize_mgmt_host(str(cfg.get("host") or ""))

    verify = cfg.get("verify_tls", True)
    if not isinstance(verify, bool):
        raise PanosConfigError("verify_tls must be a boolean")
    cfg["verify_tls"] = verify

    vsys = cfg.get("vsys", "vsys1")
    if not isinstance(vsys, str) or not VSYS_RE.fullmatch(vsys):
        raise PanosConfigError("vsys must be 'vsys' followed by ASCII digits (e.g. vsys1)")
    cfg["vsys"] = vsys

    for key, default in (("device_name", "localhost.localdomain"), ("routing_instance", "default")):
        v = cfg.get(key, default)
        if not isinstance(v, str) or not v.strip():
            raise PanosConfigError(f"{key} must be a non-empty string")
        v = v.strip()
        if key == "device_name" and not DEVICE_NAME_RE.fullmatch(v):
            raise PanosConfigError(
                f"device_name must match {DEVICE_NAME_PATTERN} (ASCII letters, digits, . _ -)")
        cfg[key] = v

    fp = cfg.get("forwarding_path", "auto")
    if fp not in FORWARDING_PATHS:
        raise PanosConfigError(f"forwarding_path must be one of {', '.join(FORWARDING_PATHS)}")
    cfg["forwarding_path"] = fp

    probes = cfg.get("probe_destinations")
    if probes is None:
        cfg["probe_destinations"] = copy.deepcopy(DEFAULT_PROBES)
    elif isinstance(probes, list):
        cfg["probe_destinations"] = [_probe(p) for p in probes]
    else:
        raise PanosConfigError("probe_destinations must be a list")

    for key in ("inside_zone", "outside_zone"):
        v = cfg.get(key)
        if v is not None and (not isinstance(v, str) or not v.strip()):
            raise PanosConfigError(f"{key} must be a non-empty string or null")
        if v is not None:
            cfg[key] = v.strip()

    obs = cfg.get("observed_sources", {})
    if not isinstance(obs, dict):
        raise PanosConfigError("observed_sources must be an object {agent_id: ip}")
    cfg["observed_sources"] = {str(k): _ip(v, f"observed_sources[{k}]") for k, v in obs.items()}

    addrs = cfg.get("agent_addresses", {})
    if not isinstance(addrs, dict):
        raise PanosConfigError("agent_addresses must be an object {agent_id: [ip, ...]}")
    out: dict[str, list[str]] = {}
    for k, v in addrs.items():
        if not isinstance(v, list):
            raise PanosConfigError(f"agent_addresses[{k}] must be a list of IPs")
        out[str(k)] = [_ip(a, f"agent_addresses[{k}]") for a in v]
    cfg["agent_addresses"] = out
    return cfg


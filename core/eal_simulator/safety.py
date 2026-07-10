"""
Safety policy for the EAL Traffic Simulator.

The simulator emits real network traffic that can be indistinguishable from
malicious activity at the wire. Without guardrails, a typo'd campaign could
beacon to a third-party host or DDoS an internal service. This module provides
the single chokepoint every plugin must clear before sending real packets.

Policy contract (enforced by ``SafetyPolicy.authorise``):

  1. ``simulation_authorized`` must be ``True`` on the parent campaign.
  2. The campaign must declare an ``authorized_by`` operator string.
  3. Every target host or CIDR a plugin touches must appear in the campaign's
     ``target_allowlist``. Hostnames match by suffix (``.testmynids.org``
     accepts ``foo.testmynids.org``); CIDRs match by IP-in-network membership.
  4. Plugins running in dry-run mode skip the network checks but still need
     authorisation context. ``CampaignExecutor`` always sets ``dry_run=True``
     when the campaign omits the authorisation block.
  5. A campaign that includes a C2-shaped plugin (e.g. ``c2_http_beacon``)
     additionally requires ``c2_authorized=True``. This mirrors the scenario /
     tool-adapter launch gate: the orchestrator refuses a ``safety_class:
     c2-framework`` adapter unless launch consent carries
     ``consent.c2_authorized`` (see ``core/engine/orchestrator.py``). Both
     paths now share ONE consent vocabulary (``simulation_authorized`` +
     ``c2_authorized``) so a C2-shaped EAL campaign can never run with weaker
     consent than the equivalent scenario adapter.

C2-shaped plugin classification
-------------------------------

EAL plugins do not carry an explicit ``safety_class`` in their ``Meta`` (the
classification mirrors the tool-adapter ``safety_class: c2-framework`` concept
without coupling the plugin contract to it). ``classify_c2_plugins`` decides
whether a plugin is C2-shaped from metadata available on every plugin:

  * an explicit name allowlist (``c2_http_beacon`` and any future C2 plugin
    added to ``_C2_PLUGIN_NAMES``), and
  * the plugin's declared MITRE techniques — the ATT&CK Command-and-Control
    tactic (TA0011) techniques in ``_C2_MITRE_TECHNIQUES`` (Application-Layer
    Protocol / Web Protocols / Protocol Tunneling / DGA, etc.).

This keeps the gate robust as new C2 plugins land: a new beacon plugin that
declares ``T1071`` is gated automatically even before it is name-listed.

Port-level authorisation
------------------------

Allowlist entries may pin a specific port (``host:port`` or ``ip:port`` /
``[v6]:port``). A *host-level* entry (no port) authorises **any** port on
that host — this is the backwards-compatible default. When a plugin knows the
port it is about to touch (stratum, ftp, ssh, smb), it calls
``authorise(host, port=N)``; if the allowlist pins one or more ports for that
host the call only succeeds for those ports. This lets an operator scope a
campaign to, e.g., ``pool.lab.invalid:3333`` without implicitly permitting an
SSH egress to the same host.
"""

from __future__ import annotations

import ipaddress
import re
from typing import Iterable


class SafetyError(Exception):
    """Raised whenever a campaign or plugin call violates safety policy."""


_HOSTNAME_RE = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9\-\.]*[a-zA-Z0-9])?$")


# ---------------------------------------------------------------------------
# C2-shaped plugin classification (mirrors tool-adapter safety_class:c2-framework)
# ---------------------------------------------------------------------------

# Plugins explicitly classed as command-and-control by name. This is the
# authoritative list for plugins whose C2 nature is not fully captured by their
# MITRE mapping alone.
_C2_PLUGIN_NAMES: frozenset[str] = frozenset({
    "c2_http_beacon",
})

# ATT&CK Command-and-Control (TA0011) techniques. A plugin declaring any of
# these in ``Meta.mitre_techniques`` is treated as C2-shaped even if it is not
# yet on the name allowlist. Matching is prefix-aware so ``T1071`` also catches
# ``T1071.001`` / ``T1071.004`` etc.
_C2_MITRE_TECHNIQUES: frozenset[str] = frozenset({
    "T1071",   # Application Layer Protocol (web, DNS, mail, ...)
    "T1568",   # Dynamic Resolution (DGA / fast-flux)
    "T1572",   # Protocol Tunneling
    "T1090",   # Proxy
    "T1095",   # Non-Application Layer Protocol
    "T1102",   # Web Service (dead-drop resolver)
    "T1571",   # Non-Standard Port
    "T1573",   # Encrypted Channel
    "T1008",   # Fallback Channels
    "T1104",   # Multi-Stage Channels
    "T1105",   # Ingress Tool Transfer
    "T1219",   # Remote Access Software
})


def _technique_is_c2(technique: str) -> bool:
    """True if a MITRE technique id belongs to the C2 tactic.

    Matches a base technique id and any of its sub-techniques
    (``T1071`` matches ``T1071.001``); a sub-technique id is normalised to its
    base before comparison so ``T1071.004`` matches too.
    """
    if not technique:
        return False
    base = technique.strip().upper().split(".", 1)[0]
    return base in _C2_MITRE_TECHNIQUES


def is_c2_plugin(name: str | None, mitre_techniques: Iterable[str] | None = None) -> bool:
    """Classify a single plugin as C2-shaped from its name and MITRE mapping.

    ``name`` is matched case-insensitively against the explicit C2 name
    allowlist; ``mitre_techniques`` (the plugin's ``Meta.mitre_techniques``)
    is matched against the ATT&CK Command-and-Control tactic. Either signal is
    sufficient — this is a deliberately conservative gate (false positives only
    cost an extra consent flag).
    """
    if name and name.strip().lower() in _C2_PLUGIN_NAMES:
        return True
    for tech in mitre_techniques or ():
        if _technique_is_c2(tech):
            return True
    return False


def classify_c2_plugins(plugins: Iterable[tuple[str, Iterable[str] | None]]) -> list[str]:
    """Return the names of the C2-shaped plugins in an iterable.

    ``plugins`` is an iterable of ``(name, mitre_techniques)`` pairs — typically
    built from a campaign's steps joined to the plugin registry metadata. The
    returned list is order-preserving and de-duplicated so callers can name the
    offending plugins in a refusal message.
    """
    out: list[str] = []
    seen: set[str] = set()
    for name, techniques in plugins:
        if is_c2_plugin(name, techniques) and name not in seen:
            out.append(name)
            seen.add(name)
    return out


def _looks_like_cidr(token: str) -> bool:
    if "/" not in token:
        return False
    try:
        ipaddress.ip_network(token, strict=False)
        return True
    except ValueError:
        return False


def _looks_like_ip(token: str) -> bool:
    try:
        ipaddress.ip_address(token)
        return True
    except ValueError:
        return False


def _split_host_port(token: str) -> tuple[str, int | None]:
    """Split an allowlist / target token into (host, port|None).

    Accepts ``host:port``, ``ip:port`` and ``[v6]:port`` forms. A bare host,
    a bare IP, an IPv6 literal without brackets, or a CIDR returns
    ``port=None``. Only a single, fully-numeric trailing ``:port`` is peeled
    off so IPv6 literals (which contain many colons) are not mangled.
    """
    token = token.strip()
    if not token:
        return token, None

    # Bracketed IPv6 literal — optionally with a trailing :port.
    if token.startswith("["):
        close = token.find("]")
        if close == -1:
            return token, None
        host = token[1:close]
        rest = token[close + 1:]
        if rest.startswith(":") and rest[1:].isdigit():
            return host, int(rest[1:])
        return host, None

    # CIDR — never carries a port.
    if _looks_like_cidr(token):
        return token, None

    # Unbracketed IPv6 literal (>=2 colons and parses as an IP) — no port.
    if token.count(":") >= 2 and _looks_like_ip(token):
        return token, None

    # host:port or ipv4:port — exactly one colon with a numeric tail.
    if token.count(":") == 1:
        host, _, maybe_port = token.partition(":")
        if maybe_port.isdigit():
            return host, int(maybe_port)
        return host, None

    return token, None


class SafetyPolicy:
    """Stateless evaluator that decides whether a target is allowed.

    Construct once per campaign with the campaign's metadata, then call
    ``authorise(target)`` from inside each plugin before emitting traffic.
    """

    def __init__(
        self,
        *,
        simulation_authorized: bool,
        authorized_by: str | None,
        target_allowlist: Iterable[str],
        dry_run: bool = False,
        c2_authorized: bool = False,
    ) -> None:
        self.simulation_authorized = simulation_authorized
        self.authorized_by = authorized_by or ""
        self.target_allowlist = [t.strip() for t in target_allowlist if t.strip()]
        self.dry_run = dry_run
        # Mirrors the scenario / tool-adapter ``consent.c2_authorized`` gate.
        # Enforced by ``assert_c2_authorized`` when a campaign carries a
        # C2-shaped plugin. Defaults False so existing (non-C2) campaigns and
        # the back-compat ``assert_campaign_authorized`` path are unaffected.
        self.c2_authorized = c2_authorized

        # Pre-parse CIDRs once so authorise() is hot-path cheap. Each entry
        # carries the optional set of ports it pins (``None`` member ⇒ a
        # host-level entry that authorises any port).
        self._cidrs: list[tuple[ipaddress._BaseNetwork, set[int | None]]] = []
        self._hostnames: list[tuple[str, set[int | None]]] = []
        # Index by canonical key so multiple allowlist entries pinning
        # different ports on the same host merge their port sets.
        cidr_ports: dict[str, set[int | None]] = {}
        cidr_net: dict[str, ipaddress._BaseNetwork] = {}
        host_ports: dict[str, set[int | None]] = {}
        for raw in self.target_allowlist:
            host_part, port = _split_host_port(raw)
            if _looks_like_cidr(host_part):
                net = ipaddress.ip_network(host_part, strict=False)
                key = str(net)
                cidr_net[key] = net
                cidr_ports.setdefault(key, set()).add(port)
            elif _looks_like_ip(host_part):
                # Use the address family's full host length: /32 for IPv4,
                # /128 for IPv6. Hard-coding /32 would silently broaden an
                # IPv6 literal into an entire /32 network.
                ip = ipaddress.ip_address(host_part)
                prefix = 32 if isinstance(ip, ipaddress.IPv4Address) else 128
                net = ipaddress.ip_network(f"{host_part}/{prefix}", strict=False)
                key = str(net)
                cidr_net[key] = net
                cidr_ports.setdefault(key, set()).add(port)
            else:
                hn = host_part.lower().lstrip(".")
                host_ports.setdefault(hn, set()).add(port)

        self._cidrs = [(cidr_net[k], ports) for k, ports in cidr_ports.items()]
        self._hostnames = [(hn, ports) for hn, ports in host_ports.items()]

    # ------------------------------------------------------------------
    # Campaign-level gate (called once at executor start).
    # ------------------------------------------------------------------

    def assert_campaign_authorized(self) -> None:
        if self.dry_run:
            return
        if not self.simulation_authorized:
            raise SafetyError(
                "Campaign refused: simulation_authorized must be true for "
                "live execution. Re-run in dry_run mode or have the customer "
                "Domain Consultant explicitly authorise the campaign."
            )
        if not self.authorized_by.strip():
            raise SafetyError(
                "Campaign refused: authorized_by must name the operator who "
                "approved this simulation."
            )
        if not self.target_allowlist:
            raise SafetyError(
                "Campaign refused: target_allowlist is empty. Live execution "
                "requires at least one allowlisted host or CIDR."
            )

    def assert_c2_authorized(self, c2_plugins: Iterable[str]) -> None:
        """Refuse a live campaign that runs a C2-shaped plugin without consent.

        ``c2_plugins`` is the list of C2-classified plugin names in the
        campaign (see ``classify_c2_plugins``). When non-empty and the campaign
        is live (not dry-run), ``c2_authorized`` must be True — mirroring the
        scenario gate's ``consent.c2_authorized`` requirement for
        ``safety_class: c2-framework`` adapters. A dry-run never emits packets,
        so the gate is skipped in planning mode.
        """
        if self.dry_run:
            return
        c2_plugins = [p for p in c2_plugins if p]
        if c2_plugins and not self.c2_authorized:
            raise SafetyError(
                "Campaign refused: it includes C2-shaped plugin(s) "
                f"{sorted(set(c2_plugins))} but consent c2_authorized is not "
                "set. Re-launch with c2_authorized=true to proceed. (This "
                "mirrors the scenario / tool-adapter launch gate, which refuses "
                "a safety_class:c2-framework adapter without "
                "consent.c2_authorized.)"
            )

    # ------------------------------------------------------------------
    # Per-target gate (called by plugins before each network call).
    # ------------------------------------------------------------------

    def authorise(self, target: str, port: int | None = None) -> None:
        """Raise SafetyError if ``target`` (optionally ``:port``) is not allowed.

        ``target`` may itself carry a ``:port`` suffix (``host:port`` /
        ``[v6]:port``); an explicit ``port`` argument takes precedence over a
        suffix. When a port is known the allowlist is consulted at port
        granularity: a host-level allowlist entry (no port) authorises any
        port, while a port-pinned entry only authorises that port.

        Dry-runs are allowed to call any target — they don't emit packets.
        """
        if self.dry_run:
            return
        if not target or not isinstance(target, str):
            raise SafetyError(f"Invalid target: {target!r}")

        host, suffix_port = _split_host_port(target)
        host = host.strip().lower()
        if port is None:
            port = suffix_port

        if _looks_like_ip(host):
            ip = ipaddress.ip_address(host)
            for net, ports in self._cidrs:
                try:
                    if ip in net and self._port_ok(port, ports):
                        return
                except TypeError:
                    continue
            raise SafetyError(
                f"Target {target!r}"
                f"{f' port {port}' if port is not None else ''} not in "
                f"allowlist (IP/port not within any authorised CIDR). "
                f"Allowlist={self.target_allowlist}"
            )

        if not _HOSTNAME_RE.match(host):
            raise SafetyError(f"Invalid hostname: {target!r}")

        # Hostname suffix match (testmynids.org allows foo.testmynids.org).
        for entry, ports in self._hostnames:
            if (host == entry or host.endswith("." + entry)) and \
                    self._port_ok(port, ports):
                return

        raise SafetyError(
            f"Target {target!r}"
            f"{f' port {port}' if port is not None else ''} not in allowlist. "
            f"Allowed hostnames: {[h for h, _ in self._hostnames]}"
        )

    @staticmethod
    def _port_ok(port: int | None, allowed_ports: set[int | None]) -> bool:
        """True if ``port`` is permitted by an entry's pinned port set.

        ``None`` in ``allowed_ports`` means the entry was host-level and so
        authorises any port. When the caller does not know the port
        (``port is None``) the entry matches on host alone.
        """
        if port is None:
            return True
        if None in allowed_ports:
            return True
        return port in allowed_ports

    # ------------------------------------------------------------------
    # Convenience: compute a human summary for audit logs.
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, object]:
        return {
            "simulation_authorized": self.simulation_authorized,
            "authorized_by": self.authorized_by,
            "target_allowlist": self.target_allowlist,
            "dry_run": self.dry_run,
            "c2_authorized": self.c2_authorized,
        }


class BudgetError(SafetyError):
    """Raised when a campaign exceeds its cumulative byte / request budget."""


# Default campaign-level ceilings. Per-plugin params still cap a single run
# (e.g. bulk_https permits up to 16 GiB for one step); these aggregate ceilings
# bound the WHOLE campaign so a campaign chaining several large steps cannot
# quietly emit terabytes. ``0`` / ``None`` disables a given ceiling.
DEFAULT_MAX_TOTAL_BYTES = 32 * 1024 * 1024 * 1024   # 32 GiB across the campaign
DEFAULT_MAX_TOTAL_REQUESTS = 1_000_000              # cumulative request/event ceiling


class CampaignBudget:
    """Cumulative byte + request ceiling shared across every step of a run.

    The executor constructs one ``CampaignBudget`` per campaign execution and
    threads it into each step's ``SimulationContext``. Plugins charge the
    budget through ``ctx`` helpers (``charge_bytes`` / ``charge_request``)
    before emitting traffic; once a ceiling is crossed the next charge raises
    ``BudgetError`` and the executor records the step as a safety violation.

    Dry-runs construct a budget but plugins skip charging (they emit no real
    bytes), so the ceiling never fires in planning mode.
    """

    def __init__(
        self,
        *,
        max_total_bytes: int | None = DEFAULT_MAX_TOTAL_BYTES,
        max_total_requests: int | None = DEFAULT_MAX_TOTAL_REQUESTS,
    ) -> None:
        # Normalise non-positive to "disabled" so a campaign can opt out.
        self.max_total_bytes = max_total_bytes if (max_total_bytes or 0) > 0 else None
        self.max_total_requests = (
            max_total_requests if (max_total_requests or 0) > 0 else None
        )
        self.bytes_charged = 0
        self.requests_charged = 0

    def charge_bytes(self, n: int) -> None:
        """Account ``n`` bytes against the campaign ceiling; raise if exceeded."""
        if n <= 0:
            return
        self.bytes_charged += n
        if self.max_total_bytes is not None and self.bytes_charged > self.max_total_bytes:
            raise BudgetError(
                f"Campaign byte budget exceeded: {self.bytes_charged} > "
                f"{self.max_total_bytes} (cumulative across all steps). Lower "
                f"the per-step volume or raise the campaign max_total_bytes."
            )

    def charge_request(self, n: int = 1) -> None:
        """Account ``n`` requests/events against the ceiling; raise if exceeded."""
        if n <= 0:
            return
        self.requests_charged += n
        if (
            self.max_total_requests is not None
            and self.requests_charged > self.max_total_requests
        ):
            raise BudgetError(
                f"Campaign request budget exceeded: {self.requests_charged} > "
                f"{self.max_total_requests} (cumulative across all steps)."
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "max_total_bytes": self.max_total_bytes,
            "max_total_requests": self.max_total_requests,
            "bytes_charged": self.bytes_charged,
            "requests_charged": self.requests_charged,
        }

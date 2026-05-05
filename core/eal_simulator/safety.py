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
"""

from __future__ import annotations

import ipaddress
import re
from typing import Iterable


class SafetyError(Exception):
    """Raised whenever a campaign or plugin call violates safety policy."""


_HOSTNAME_RE = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9\-\.]*[a-zA-Z0-9])?$")


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
    ) -> None:
        self.simulation_authorized = simulation_authorized
        self.authorized_by = authorized_by or ""
        self.target_allowlist = [t.strip() for t in target_allowlist if t.strip()]
        self.dry_run = dry_run

        # Pre-parse CIDRs once so authorise() is hot-path cheap.
        self._cidrs: list[ipaddress._BaseNetwork] = []
        self._hostnames: list[str] = []
        for token in self.target_allowlist:
            if _looks_like_cidr(token):
                self._cidrs.append(ipaddress.ip_network(token, strict=False))
            elif _looks_like_ip(token):
                self._cidrs.append(ipaddress.ip_network(f"{token}/32", strict=False))
            else:
                self._hostnames.append(token.lower().lstrip("."))

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

    # ------------------------------------------------------------------
    # Per-target gate (called by plugins before each network call).
    # ------------------------------------------------------------------

    def authorise(self, target: str) -> None:
        """Raise SafetyError if ``target`` is not in the allowlist.

        Dry-runs are allowed to call any target — they don't emit packets.
        """
        if self.dry_run:
            return
        if not target or not isinstance(target, str):
            raise SafetyError(f"Invalid target: {target!r}")

        host = target.strip().lower()

        # Strip port suffix if present (host:port or [v6]:port).
        if host.startswith("["):
            # IPv6 literal in brackets
            close = host.find("]")
            if close == -1:
                raise SafetyError(f"Malformed IPv6 target: {target!r}")
            host = host[1:close]
        elif host.count(":") == 1:
            host = host.split(":", 1)[0]

        if _looks_like_ip(host):
            ip = ipaddress.ip_address(host)
            for net in self._cidrs:
                try:
                    if ip in net:
                        return
                except TypeError:
                    continue
            raise SafetyError(
                f"Target {target!r} not in allowlist (IP not within any "
                f"authorised CIDR). Allowlist={self.target_allowlist}"
            )

        if not _HOSTNAME_RE.match(host):
            raise SafetyError(f"Invalid hostname: {target!r}")

        # Hostname suffix match (testmynids.org allows foo.testmynids.org).
        for entry in self._hostnames:
            if host == entry or host.endswith("." + entry):
                return

        raise SafetyError(
            f"Target {target!r} not in allowlist. "
            f"Allowed hostnames: {self._hostnames}"
        )

    # ------------------------------------------------------------------
    # Convenience: compute a human summary for audit logs.
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, object]:
        return {
            "simulation_authorized": self.simulation_authorized,
            "authorized_by": self.authorized_by,
            "target_allowlist": self.target_allowlist,
            "dry_run": self.dry_run,
        }

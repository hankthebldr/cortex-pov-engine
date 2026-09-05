"""
third_party_firewall_emitter — analytics log-streamer for the **Third-Party
Firewalls** data source (a non-PAN firewall's normalized traffic/threat feed ->
``third_party_firewall_raw``).

Built on the ``analytics_emitter`` spine. It POSTs shape-true normalized
firewall log records (the CEF/syslog traffic + threat events a Fortinet /
Check Point / generic NGFW forwards) to an operator-supplied collector so a
customer can validate that their Cortex XSIAM **Analytics** network-behaviour
detections fire on a THIRD-PARTY firewall feed — the generic bucket most
customers who do not run PAN-OS everywhere actually land in. No real scan is
run and nothing resolves: RFC 5737 documentation IP ranges only.

The whole point, per the brief, is that the record satisfies the detector's
actual predicate, not merely that it "looks like" a firewall log. Each event
pattern is authored against the field/value the analytics alert keys on, and
EVERY pattern ships a **negative control** — a record from this same emitter,
in the same dataset, that must NOT fire the detector — so "the detector fired"
can be told apart from "the detector fires on anything".

Event patterns (parameter ``event_pattern``):

  ===================== ==============================================================
  preset                analytics alert it exercises (and the predicate)
  ===================== ==============================================================
  port_scan             "Port scan detected" — one src_ip → one dst_ip across many
                        DISTINCT dst_ports, connections denied/reset. Predicate:
                        distinct(dst_port) over one (src_ip,dst_ip) >= _PORT_SCAN_MIN
                        within the window. T1046. NEGATIVE: same pair, 2 allowed
                        ports (web-browsing) — nowhere near the distinct-port floor.
  host_sweep            "Network host sweep" — one src_ip → many DISTINCT dst_ips on
                        ONE dst_port. Predicate: distinct(dst_ip) over one
                        (src_ip,dst_port) >= _HOST_SWEEP_MIN. T1046. NEGATIVE: same
                        src to 2 dst_ips — not a sweep.
  denied_conn_spike     "Unusual volume of denied connections" — a burst of
                        action=deny from one src_ip. Predicate: count(action=deny)
                        from one src_ip >= _DENIED_SPIKE_MIN in the window. T1046 /
                        firewall-evasion recon. NEGATIVE: 2 denied events — ordinary
                        background noise.
  ===================== ==============================================================
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import Field, field_validator

from ..analytics_emitter import AnalyticsLogEmitter, NegativeControlEmitterParams


logger = logging.getLogger("cortexsim.eal.plugins.third_party_firewall_emitter")


# The XSIAM dataset a normalized third-party firewall feed lands in.
_DATASET = "third_party_firewall_raw"

# Synthetic, non-resolving endpoints. RFC 5737 TEST-NET documentation ranges.
_ATTACKER_IP = "198.51.100.23"
_VICTIM_IP = "203.0.113.10"
_SWEEP_SUBNET = "203.0.113."  # /24 the sweep walks

# Detector predicate floors. A live tenant's exact thresholds are tenant-tuned
# and unproven here (tenant-verified is 0); these are the documented behavioural
# floors the positive burst is guaranteed to clear so the record is
# detector-true, not merely shape-true. The negative control stays well below.
_PORT_SCAN_MIN = 20        # distinct dst_ports over one (src,dst)
_HOST_SWEEP_MIN = 20       # distinct dst_ips over one (src,dst_port)
_DENIED_SPIKE_MIN = 25     # denied connections from one src

_VENDOR = "GenericFW"
_PRODUCT = "NGFW"


_PATTERN_MARKER = {
    "port_scan": "port_scan_marker",
    "host_sweep": "host_sweep_marker",
    "denied_conn_spike": "denied_conn_spike_marker",
}
_EVENT_PATTERNS = tuple(_PATTERN_MARKER)


def _list_event_patterns() -> list[str]:
    return sorted(_EVENT_PATTERNS)


def _fw_event(
    *,
    marker: str,
    sim_run_id: str,
    src_ip: str,
    dst_ip: str,
    dst_port: int,
    action: str,
    app: str = "unknown-tcp",
    protocol: str = "tcp",
    event_type: str = "traffic",
    bytes_sent: int = 0,
    bytes_received: int = 0,
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Shape one normalized third-party firewall log record.

    The field set is the vendor-neutral intersection an XSIAM parser normalizes
    from a CEF/syslog firewall feed: the 5-tuple, the verdict (``action``), the
    App-ID-equivalent (``app``) and byte counts.
    """
    now = datetime.now(timezone.utc).isoformat()
    body: dict[str, Any] = {
        "dataset": _DATASET,
        "vendor": _VENDOR,
        "product": _PRODUCT,
        "event_type": event_type,
        "action": action,
        "src_ip": src_ip,
        "src_port": secrets.randbelow(20000) + 40000,
        "dst_ip": dst_ip,
        "dst_port": dst_port,
        "protocol": protocol,
        "app": app,
        "bytes_sent": bytes_sent,
        "bytes_received": bytes_received,
        "rule_name": "default-deny" if action != "allow" else "allow-web",
        "session_id": secrets.randbelow(9_000_000) + 1_000_000,
        "timestamp": now,
        marker: True,
        "cortexsim_run_id": sim_run_id,
    }
    if extra:
        body.update(extra)
    return body


class ThirdPartyFirewallParams(NegativeControlEmitterParams):
    event_pattern: str = Field(
        default="port_scan",
        description="Third-party firewall analytics alert to exercise: "
                    "port_scan | host_sweep | denied_conn_spike.",
    )
    src_ip: str = Field(
        default=_ATTACKER_IP,
        description="Synthetic source IP the scan/sweep is attributed to "
                    "(RFC 5737 documentation range).",
    )

    @property
    def dataset(self) -> str:
        return _DATASET

    @field_validator("event_pattern")
    @classmethod
    def _pattern_known(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in _EVENT_PATTERNS:
            raise ValueError(
                f"event_pattern must be one of {sorted(_EVENT_PATTERNS)}, got '{v}'"
            )
        return v


class ThirdPartyFirewallEmitter(AnalyticsLogEmitter):
    supports_negative_control = True

    class Meta:
        name = "third_party_firewall_emitter"
        version = "1.0.0"
        # Vendor analytics catalogue join (see analytics_catalogue.py).
        data_sources = ["third_party_firewalls"]
        datasets = [_DATASET]
        detectors = [
            {
                "alert": "Port scan detected",
                "dataset": _DATASET,
                "key_fields": ["src_ip", "dst_ip", "dst_port", "action"],
                "predicate": (
                    f"distinct(dst_port) over one (src_ip,dst_ip) >= "
                    f"{_PORT_SCAN_MIN} with denied/reset verdicts in the window"
                ),
                "mitre": "T1046",
                "negative_control": "same src/dst, 2 allowed web-browsing ports",
            },
            {
                "alert": "Network host sweep",
                "dataset": _DATASET,
                "key_fields": ["src_ip", "dst_ip", "dst_port"],
                "predicate": (
                    f"distinct(dst_ip) over one (src_ip,dst_port) >= "
                    f"{_HOST_SWEEP_MIN} in the window"
                ),
                "mitre": "T1046",
                "negative_control": "same src to 2 dst_ips",
            },
            {
                "alert": "Unusual volume of denied connections",
                "dataset": _DATASET,
                "key_fields": ["src_ip", "action"],
                "predicate": (
                    f"count(action=deny) from one src_ip >= {_DENIED_SPIKE_MIN} "
                    f"in the window"
                ),
                "mitre": "T1046",
                "negative_control": "2 denied connections (background noise)",
            },
        ]
        description = (
            "Emits shape-true normalized third-party firewall log records "
            "(third_party_firewall_raw) into an operator-supplied collector so "
            "Cortex XSIAM exercises its Analytics network-behaviour detections "
            "(port scan, host sweep, denied-connection spike) on a NON-PAN "
            "firewall feed — the generic bucket most customers land in. Ships a "
            "negative control per detector."
        )
        mitre_techniques = ["T1046"]  # Network Service Discovery
        eal_targets = [
            "Analytics — Port scan detected (third-party firewall)",
            "Analytics — Network host sweep (third-party firewall)",
            "Analytics — Unusual volume of denied connections (third-party firewall)",
            "NGFW EAL — outbound POST to XSIAM log-collector App-ID match",
        ]
        ecs_category = "network"
        params_model = ThirdPartyFirewallParams

    # ------------------------------------------------------------------
    # Positive builders — records that MUST fire the detector.
    # ------------------------------------------------------------------

    def build_events(
        self, params: ThirdPartyFirewallParams, *, sim_run_id: str, iteration: int,
    ) -> list[dict[str, Any]]:
        marker = _PATTERN_MARKER[params.event_pattern]
        events: list[dict[str, Any]] = []

        if params.event_pattern == "port_scan":
            # One src -> one dst across many DISTINCT ports, denied/reset. We
            # emit at least the distinct-port floor so the record is
            # detector-true regardless of the operator's burst_count.
            n = max(params.burst_count, _PORT_SCAN_MIN + 4)
            for i in range(n):
                events.append(_fw_event(
                    marker=marker, sim_run_id=sim_run_id,
                    src_ip=params.src_ip, dst_ip=_VICTIM_IP,
                    # Distinct, sequential ports — the port-scan signature.
                    dst_port=1024 + i,
                    action="reset-both",
                    app="unknown-tcp",
                    event_type="traffic",
                    extra={"distinct_ports": n},
                ))
            return events

        if params.event_pattern == "host_sweep":
            # One src -> many DISTINCT dst_ips on one port (445 / SMB sweep).
            n = max(params.burst_count, _HOST_SWEEP_MIN + 4)
            for i in range(n):
                events.append(_fw_event(
                    marker=marker, sim_run_id=sim_run_id,
                    src_ip=params.src_ip,
                    dst_ip=f"{_SWEEP_SUBNET}{10 + i}",
                    dst_port=445,
                    action="deny",
                    app="ms-ds-smb",
                    event_type="traffic",
                    extra={"distinct_hosts": n},
                ))
            return events

        if params.event_pattern == "denied_conn_spike":
            # A burst of denied connections from one src across varied dsts.
            n = max(params.burst_count, _DENIED_SPIKE_MIN + 4)
            for i in range(n):
                events.append(_fw_event(
                    marker=marker, sim_run_id=sim_run_id,
                    src_ip=params.src_ip,
                    dst_ip=f"{_SWEEP_SUBNET}{20 + (i % 8)}",
                    dst_port=[22, 23, 3389, 5900][i % 4],
                    action="deny",
                    app="unknown-tcp",
                    event_type="traffic",
                    extra={"denied_count": n},
                ))
            return events

        raise ValueError(  # pragma: no cover — gated by the pydantic validator
            f"unknown event_pattern {params.event_pattern!r}"
        )

    # ------------------------------------------------------------------
    # Negative controls — records that MUST NOT fire the detector.
    # ------------------------------------------------------------------

    def build_negative_control(
        self, params: ThirdPartyFirewallParams, *, sim_run_id: str, iteration: int,
    ) -> list[dict[str, Any]]:
        marker = _PATTERN_MARKER[params.event_pattern]

        if params.event_pattern == "port_scan":
            # Two allowed web-browsing sessions to the same host — nowhere near
            # the distinct-port floor, and allowed rather than reset.
            return [
                _fw_event(
                    marker=marker, sim_run_id=sim_run_id,
                    src_ip=params.src_ip, dst_ip=_VICTIM_IP,
                    dst_port=port, action="allow", app="web-browsing",
                    bytes_sent=1200, bytes_received=48000,
                    extra={"negative_control": True, "distinct_ports": 2},
                )
                for port in (443, 80)
            ]

        if params.event_pattern == "host_sweep":
            # Same src to just two hosts on one port — not a sweep.
            return [
                _fw_event(
                    marker=marker, sim_run_id=sim_run_id,
                    src_ip=params.src_ip, dst_ip=f"{_SWEEP_SUBNET}{10 + i}",
                    dst_port=445, action="allow", app="ms-ds-smb",
                    bytes_sent=800, bytes_received=1600,
                    extra={"negative_control": True, "distinct_hosts": 2},
                )
                for i in range(2)
            ]

        if params.event_pattern == "denied_conn_spike":
            # Two denied connections — ordinary background noise, below the floor.
            return [
                _fw_event(
                    marker=marker, sim_run_id=sim_run_id,
                    src_ip=params.src_ip, dst_ip=f"{_SWEEP_SUBNET}{20 + i}",
                    dst_port=22, action="deny", app="ssh",
                    extra={"negative_control": True, "denied_count": 2},
                )
                for i in range(2)
            ]

        raise ValueError(  # pragma: no cover — gated by the pydantic validator
            f"unknown event_pattern {params.event_pattern!r}"
        )

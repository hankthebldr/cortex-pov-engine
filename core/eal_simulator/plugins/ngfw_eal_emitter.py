"""
ngfw_eal_emitter — analytics log-streamer for the Firewall / NGFW Enhanced
Application (EAL) Logs data source. PAN-OS NGFW EAL traffic normalises into the
``panw_ngfw_traffic_raw`` dataset discriminated by ``log_source_type = "eal"`` —
the SAME dataset + discriminator the NGFW analytics cards query, so the emitted
stream actually fires the detection (previously mis-keyed to panw_ngfw_eal_raw,
which no card queries).

Built on the ``analytics_emitter`` spine. It POSTs shape-true PAN-OS NGFW
Enhanced-Application-Log traffic records to an operator-supplied collector so a
customer can validate that their Cortex XSIAM **Analytics / ABIOC** NGFW
detections fire — without generating a single real outbound session to a rare
domain, storage endpoint or external SSH host. Every record is a JSON blob
shaped *like* the firewall's EAL traffic schema (the enhanced-application
traffic log that carries per-session application, byte-volume, URL/domain and
endpoint-process context).

The plugin implements only ``build_events`` — the shared driver
(``AnalyticsLogEmitter``) owns the transport, the ``ctx.authorise``-before-emit
gate, the dry-run short-circuit, budget charging, batching/gzip and the
``SimulationResult`` aggregation.

Event-shape presets (parameter ``event_pattern``) — each maps to a real XSIAM
NGFW EAL analytics alert:

  ============================== ======================================================
  preset                         analytics / ABIOC alert it exercises
  ============================== ======================================================
  abnormal_recurring_comms       Analytics/ABIOC — "Abnormal Recurring Communications
                                 to a Rare Domain" (regular-interval beacon burst of
                                 HTTPS sessions to a rarely-seen domain) — T1071.001
  abnormal_comms                 Analytics — "Abnormal Communication to a Rare Domain"
                                 (a single anomalous HTTPS session to a rare domain)
                                 — T1071.001
  large_upload_https             Analytics — "Large Upload (HTTPS)" (one session with an
                                 anomalously large bytes_sent over 443) — T1048
  massive_upload_rare_domain     Analytics — "Massive upload to a rare storage or mail
                                 domain" (massive bytes_sent to a rare storage/mail
                                 domain) — T1567.002
  rare_ssh_session               ABIOC — "Rare process created an SSH session to an
                                 uncommon external host" (port-22 session whose
                                 initiating endpoint process rarely opens SSH) —
                                 T1021.004
  recurring_rare_domain_access   Analytics/ABIOC — "Recurring access to rare domain"
                                 (recurring web-browsing sessions to a rare domain)
                                 — T1071.001
  ============================== ======================================================

All destination domains use the reserved ``.invalid`` TLD and all destination
IPs use the RFC 5737 documentation ranges, so nothing here resolves or routes
on a real network. Several presets fire ATT&CK Command-and-Control (TA0011)
techniques (``T1071``), so a live campaign is classified C2-shaped and — per
``SafetyPolicy`` — should carry ``c2_authorized=true`` in addition to
``simulation_authorized``.
"""

from __future__ import annotations

import dataclasses
import logging
import secrets
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import Field, field_validator

from ..analytics_emitter import AnalyticsEmitterParams, AnalyticsLogEmitter


logger = logging.getLogger("cortexsim.eal.plugins.ngfw_eal_emitter")


# The XSIAM dataset the PAN-OS NGFW Enhanced Application Logs normalise to.
# EAL records land in the normalized traffic dataset, discriminated by
# log_source_type="eal" (which is how the NGFW analytics cards query them).
_DATASET = "panw_ngfw_traffic_raw"

# Synthetic, non-resolving identifiers. The reserved .invalid TLD and the
# RFC 5737 documentation IP ranges guarantee nothing here touches a real host
# or resolves on a real network.
_FIREWALL_SERIAL = "007051000012345"
_FIREWALL_HOSTNAME = "cortexsim-ngfw-canary"
_SRC_HOST = "10.0.12.34"
_SRC_USER = "corp\\cortexsim-canary"
_ENDPOINT_DEVICE = "WKS-CORTEXSIM-0007"

# Rare destinations per alert family.
_RARE_DOMAIN = "rare-cdn-7f3a91.invalid"
_RARE_DOMAIN_IP = "203.0.113.201"
_RARE_STORAGE_DOMAIN = "cortexsim-object-store-rare.invalid"
_RARE_STORAGE_IP = "198.51.100.77"
_UNCOMMON_SSH_HOST = "jump-uncommon-ext.invalid"
_UNCOMMON_SSH_IP = "192.0.2.66"


# --------------------------------------------------------------------------
# Session descriptor + the shape-true NGFW EAL record builder
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class _Session:
    """One firewall EAL traffic session, expressed as shape-true fields."""
    app: str
    proto: str
    dst_port: int
    dst_ip: str
    dst_domain: str
    bytes_sent: int
    bytes_received: int
    process_name: str
    action: str = "allow"
    session_end_reason: str = "tcp-fin"


def _ngfw_eal_event(
    *,
    session: _Session,
    marker: str,
    sim_run_id: str,
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Shape a PAN-OS NGFW Enhanced-Application-Log traffic record.

    A subset of the PAN-OS traffic/EAL log fields: 5-tuple + application,
    per-session byte/packet volumes, zones/interfaces, URL/domain, and the
    enhanced endpoint-process context that distinguishes an EAL record from a
    plain traffic log.
    """
    now = datetime.now(timezone.utc).isoformat()
    body: dict[str, Any] = {
        "dataset": _DATASET,
        "type": "TRAFFIC",
        "subtype": "end",
        "log_source_type": "eal",
        "time_generated": now,
        "receive_time": now,
        "serial": _FIREWALL_SERIAL,
        "device_name": _FIREWALL_HOSTNAME,
        "vsys": "vsys1",
        "session_id": secrets.randbelow(9_000_000) + 1_000_000,
        "src": _SRC_HOST,
        "dst": session.dst_ip,
        "src_port": secrets.randbelow(16383) + 49152,
        "dst_port": session.dst_port,
        "proto": session.proto,
        "app": session.app,
        "rule": "allow-outbound",
        "action": session.action,
        "src_zone": "trust",
        "dst_zone": "untrust",
        "inbound_if": "ethernet1/2",
        "outbound_if": "ethernet1/1",
        "bytes_sent": session.bytes_sent,
        "bytes_received": session.bytes_received,
        "packets_sent": max(1, session.bytes_sent // 1400),
        "packets_received": max(1, session.bytes_received // 1400),
        "session_end_reason": session.session_end_reason,
        "url_domain": session.dst_domain,
        "dst_hostname": session.dst_domain,
        "user": _SRC_USER,
        # -- EAL enhanced endpoint-process context --
        "endpoint_device_name": _ENDPOINT_DEVICE,
        "process_name": session.process_name,
        "process_hash": secrets.token_hex(32),
        marker: True,
        "cortexsim_run_id": sim_run_id,
    }
    if extra:
        body.update(extra)
    return body


# --------------------------------------------------------------------------
# Event patterns — the session template each preset emits + its marker
# --------------------------------------------------------------------------


# ~5 GiB and ~20 GiB — these are JSON byte-count fields only; the wire body the
# transport actually POSTs is a few hundred bytes (charged against the budget).
_LARGE_UPLOAD_BYTES = 5 * 1024 * 1024 * 1024
_MASSIVE_UPLOAD_BYTES = 20 * 1024 * 1024 * 1024


# Each pattern -> (marker, human alert name, session template). "Burst"
# patterns fan out ``burst_count`` copies of the template (handled in
# build_events); the rest emit a single session.
_PATTERN_SESSION: dict[str, tuple[str, str, _Session]] = {
    "abnormal_recurring_comms": (
        "abnormal_recurring_comms_marker",
        "Abnormal Recurring Communications to a Rare Domain",
        _Session(
            app="ssl", proto="tcp", dst_port=443,
            dst_ip=_RARE_DOMAIN_IP, dst_domain=_RARE_DOMAIN,
            bytes_sent=2048, bytes_received=1536, process_name="svchost.exe",
        ),
    ),
    "abnormal_comms": (
        "abnormal_comms_marker",
        "Abnormal Communication to a Rare Domain",
        _Session(
            app="ssl", proto="tcp", dst_port=443,
            dst_ip=_RARE_DOMAIN_IP, dst_domain=_RARE_DOMAIN,
            bytes_sent=4096, bytes_received=3072, process_name="rundll32.exe",
        ),
    ),
    "large_upload_https": (
        "large_upload_https_marker",
        "Large Upload (HTTPS)",
        _Session(
            app="ssl", proto="tcp", dst_port=443,
            dst_ip=_RARE_DOMAIN_IP, dst_domain=_RARE_DOMAIN,
            bytes_sent=_LARGE_UPLOAD_BYTES, bytes_received=8192,
            process_name="curl.exe",
        ),
    ),
    "massive_upload_rare_domain": (
        "massive_upload_rare_domain_marker",
        "Massive upload to a rare storage or mail domain",
        _Session(
            app="web-browsing", proto="tcp", dst_port=443,
            dst_ip=_RARE_STORAGE_IP, dst_domain=_RARE_STORAGE_DOMAIN,
            bytes_sent=_MASSIVE_UPLOAD_BYTES, bytes_received=16384,
            process_name="rclone.exe",
        ),
    ),
    "rare_ssh_session": (
        "rare_ssh_session_marker",
        "Rare process created an SSH session to an uncommon external host",
        _Session(
            app="ssh", proto="tcp", dst_port=22,
            dst_ip=_UNCOMMON_SSH_IP, dst_domain=_UNCOMMON_SSH_HOST,
            bytes_sent=6144, bytes_received=9216, process_name="python.exe",
        ),
    ),
    "recurring_rare_domain_access": (
        "recurring_rare_domain_access_marker",
        "Recurring access to rare domain",
        _Session(
            app="web-browsing", proto="tcp", dst_port=443,
            dst_ip=_RARE_DOMAIN_IP, dst_domain=_RARE_DOMAIN,
            bytes_sent=3072, bytes_received=20480, process_name="chrome.exe",
        ),
    ),
}

# Presets that fan out ``burst_count`` recurring sessions (beacon / recurring
# access shapes) rather than a single anomalous session.
_BURST_PATTERNS = frozenset({
    "abnormal_recurring_comms",
    "recurring_rare_domain_access",
})

_EVENT_PATTERNS = tuple(_PATTERN_SESSION)


def _list_event_patterns() -> list[str]:
    return sorted(_EVENT_PATTERNS)


def _pattern_alert(pattern: str) -> str:
    """The human analytics-alert name a preset exercises."""
    return _PATTERN_SESSION[pattern][1]


# --------------------------------------------------------------------------
# Params — subclass the shared base, add only event_pattern.
# --------------------------------------------------------------------------


class NgfwEalEmitterParams(AnalyticsEmitterParams):
    event_pattern: str = Field(
        default="abnormal_recurring_comms",
        description="NGFW EAL analytics alert to exercise: "
                    "abnormal_recurring_comms | abnormal_comms | "
                    "large_upload_https | massive_upload_rare_domain | "
                    "rare_ssh_session | recurring_rare_domain_access.",
    )

    @property
    def dataset(self) -> str:
        """The XSIAM dataset PAN-OS NGFW EAL logs normalise to."""
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


# --------------------------------------------------------------------------
# Plugin
# --------------------------------------------------------------------------


class NgfwEalEmitter(AnalyticsLogEmitter):
    # All three are flat PAN-OS traffic-log columns, and all three are SUFFIXED
    # rather than replaced so the record keeps its shape-true domain\user,
    # firewall-hostname and endpoint-hostname forms.
    CANARY_FIELDS = {
        "user": "{value}-{account}",
        "device_name": "{value}-{account}",
        "endpoint_device_name": "{value}-{account}",
    }

    class Meta:
        name = "ngfw_eal_emitter"
        # Vendor analytics catalogue join (see analytics_catalogue.py). The PAN
        # EAL feed carries both the EAL-logs and traffic-logs catalogue sources.
        data_sources = ["pan_firewall_eal_logs", "pan_firewall_traffic_logs"]
        datasets = ["panw_ngfw_traffic_raw"]
        version = "1.0.0"
        description = (
            "Emits shape-true PAN-OS NGFW Enhanced Application Log (EAL) traffic "
            "records (panw_ngfw_traffic_raw, log_source_type=eal) into an operator-supplied "
            "collector so Cortex XSIAM exercises its NGFW Analytics / ABIOC "
            "detections (abnormal/recurring communication to a rare domain, "
            "large & massive HTTPS uploads, rare-process SSH to an uncommon "
            "external host) without generating a single real outbound session."
        )
        # Drawn from the analytics alerts this plugin fires. Several are ATT&CK
        # Command-and-Control (TA0011) techniques (T1071), so the SafetyPolicy
        # C2 classifier treats a live campaign as C2-shaped -> c2_authorized
        # should accompany simulation_authorized.
        mitre_techniques = ["T1071.001", "T1048", "T1567.002", "T1021.004"]
        eal_targets = [
            "Analytics/ABIOC — Abnormal Recurring Communications to a Rare Domain",
            "Analytics — Abnormal Communication to a Rare Domain",
            "Analytics — Large Upload (HTTPS)",
            "Analytics — Massive upload to a rare storage or mail domain",
            "ABIOC — Rare process created an SSH session to an uncommon external host",
            "Analytics/ABIOC — Recurring access to rare domain",
            "NGFW EAL — outbound POST to XSIAM log-collector App-ID match",
        ]
        ecs_category = "network"
        params_model = NgfwEalEmitterParams

    def build_events(
        self, params: NgfwEalEmitterParams, *, sim_run_id: str, iteration: int,
    ) -> list[dict[str, Any]]:
        marker, _alert, session = _PATTERN_SESSION[params.event_pattern]

        events: list[dict[str, Any]] = []

        if params.event_pattern in _BURST_PATTERNS:
            # A recurring, regular-interval burst of sessions to the rare
            # domain — the beacon / recurring-access behavioural shape.
            for seq in range(params.burst_count):
                events.append(_ngfw_eal_event(
                    session=session,
                    marker=marker,
                    sim_run_id=sim_run_id,
                    extra={"beacon_sequence": seq, "recurring_marker": True},
                ))
            return events

        events.append(_ngfw_eal_event(
            session=session,
            marker=marker,
            sim_run_id=sim_run_id,
        ))
        return events

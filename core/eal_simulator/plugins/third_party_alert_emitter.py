"""
third_party_alert_emitter — analytics log-streamer for the **Third-Party
Alerts** data source (a non-PAN security tool's alert feed ->
``third_party_alerts_raw``).

Built on the ``analytics_emitter`` spine. It POSTs shape-true third-party
security-alert records (the alerts a third-party EDR / IDS / AV / email-security
tool forwards into XSIAM) to an operator-supplied collector so a customer can
validate that XSIAM ingests, surfaces and correlates third-party alerts —
the "bring your existing detections into Cortex" bucket. Nothing is executed;
synthetic ``.invalid`` hosts and users only.

Every event pattern is authored against the field/value XSIAM keys on when it
ingests a third-party alert, and EVERY pattern ships a **negative control** — a
benign third-party record in the same dataset that must NOT raise.

Event patterns (parameter ``event_pattern``):

  ===================== ==============================================================
  preset                behaviour it exercises (and the predicate)
  ===================== ==============================================================
  high_severity_alert   XSIAM surfaces an ingested third-party alert whose severity
                        is high/critical. Predicate: severity in {high, critical}.
                        NEGATIVE: severity=informational (an audit/info event that
                        must not raise).
  malware_verdict       XSIAM raises on a third-party malware/backdoor verdict.
                        Predicate: category in the malware family AND action in
                        {blocked, detected, quarantined}. T1204/T1059. NEGATIVE: a
                        policy/audit category with action=allowed.
  repeated_host_alerts  XSIAM escalates when many third-party alerts hit ONE host in
                        the window. Predicate: count(alerts) for one src_host >=
                        _REPEAT_MIN. NEGATIVE: a single alert on the host.
  ===================== ==============================================================
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import Field, field_validator

from ..analytics_emitter import AnalyticsLogEmitter, NegativeControlEmitterParams


logger = logging.getLogger("cortexsim.eal.plugins.third_party_alert_emitter")


_DATASET = "third_party_alerts_raw"

_SRC_HOST = "WKSTN-14.cortexsim-canary.invalid"
_USER = "alice.doe@cortexsim-canary.invalid"
_VENDOR = "GenericEDR"
_PRODUCT = "Endpoint Protection"

# The severities XSIAM should surface an ingested third-party alert on.
_SURFACING_SEVERITIES = frozenset({"high", "critical"})
# Verdict categories that read as a real malware detection, not policy noise.
_MALWARE_CATEGORIES = frozenset({"malware", "backdoor", "ransomware", "trojan"})
_MALWARE_ACTIONS = frozenset({"blocked", "detected", "quarantined"})
# Escalation floor — third-party alerts on one host in the window.
_REPEAT_MIN = 5


_PATTERN_MARKER = {
    "high_severity_alert": "high_severity_alert_marker",
    "malware_verdict": "malware_verdict_marker",
    "repeated_host_alerts": "repeated_host_alerts_marker",
}
_EVENT_PATTERNS = tuple(_PATTERN_MARKER)


def _list_event_patterns() -> list[str]:
    return sorted(_EVENT_PATTERNS)


def _alert_event(
    *,
    marker: str,
    sim_run_id: str,
    alert_name: str,
    severity: str,
    category: str,
    action: str,
    src_host: str = _SRC_HOST,
    user: str = _USER,
    signature_id: Optional[str] = None,
    description: str = "",
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Shape one normalized third-party security-alert record."""
    now = datetime.now(timezone.utc).isoformat()
    body: dict[str, Any] = {
        "dataset": _DATASET,
        "vendor": _VENDOR,
        "product": _PRODUCT,
        "event_type": "alert",
        "alert_name": alert_name,
        "severity": severity,
        "category": category,
        "action": action,
        "src_host": src_host,
        "user": user,
        "signature_id": signature_id or f"SIG-{secrets.randbelow(90000) + 10000}",
        "description": description or alert_name,
        "timestamp": now,
        marker: True,
        "cortexsim_run_id": sim_run_id,
    }
    if extra:
        body.update(extra)
    return body


class ThirdPartyAlertParams(NegativeControlEmitterParams):
    event_pattern: str = Field(
        default="high_severity_alert",
        description="Third-party alert behaviour to exercise: "
                    "high_severity_alert | malware_verdict | repeated_host_alerts.",
    )
    src_host: str = Field(
        default=_SRC_HOST,
        description="Synthetic host the third-party alert is attributed to.",
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


class ThirdPartyAlertEmitter(AnalyticsLogEmitter):
    supports_negative_control = True

    class Meta:
        name = "third_party_alert_emitter"
        version = "1.0.0"
        # Vendor analytics catalogue join (see analytics_catalogue.py).
        data_sources = ["third_party_alerts"]
        datasets = [_DATASET]
        detectors = [
            {
                "alert": "Third-party alert surfaced (high/critical)",
                "dataset": _DATASET,
                "key_fields": ["severity"],
                "predicate": f"severity in {sorted(_SURFACING_SEVERITIES)}",
                "mitre": "T1584",
                "negative_control": "severity=informational (audit/info event)",
            },
            {
                "alert": "Third-party malware verdict",
                "dataset": _DATASET,
                "key_fields": ["category", "action"],
                "predicate": (
                    f"category in {sorted(_MALWARE_CATEGORIES)} AND action in "
                    f"{sorted(_MALWARE_ACTIONS)}"
                ),
                "mitre": "T1204",
                "negative_control": "policy/audit category with action=allowed",
            },
            {
                "alert": "Repeated third-party alerts on one host",
                "dataset": _DATASET,
                "key_fields": ["src_host"],
                "predicate": f"count(alert) for one src_host >= {_REPEAT_MIN}",
                "mitre": "T1584",
                "negative_control": "a single alert on the host",
            },
        ]
        description = (
            "Emits shape-true normalized third-party security-alert records "
            "(third_party_alerts_raw) into an operator-supplied collector so "
            "Cortex XSIAM exercises ingestion, surfacing and escalation of "
            "third-party EDR/IDS/AV alerts (high-severity surfacing, malware "
            "verdict, repeated-host escalation). Ships a negative control per "
            "detector."
        )
        mitre_techniques = ["T1584", "T1204"]
        eal_targets = [
            "Analytics — third-party high/critical alert surfaced in XSIAM",
            "Analytics — third-party malware verdict ingested",
            "Analytics — repeated third-party alerts escalated on one host",
            "NGFW EAL — outbound POST to XSIAM log-collector App-ID match",
        ]
        ecs_category = "intrusion_detection"
        params_model = ThirdPartyAlertParams

    def build_events(
        self, params: ThirdPartyAlertParams, *, sim_run_id: str, iteration: int,
    ) -> list[dict[str, Any]]:
        marker = _PATTERN_MARKER[params.event_pattern]
        host = params.src_host

        if params.event_pattern == "high_severity_alert":
            return [_alert_event(
                marker=marker, sim_run_id=sim_run_id,
                alert_name="Credential dumping tool detected",
                severity="critical", category="credential_access",
                action="detected", src_host=host,
                description="Third-party EDR flagged an LSASS access tool",
                extra={"surfacing_severity": True},
            )]

        if params.event_pattern == "malware_verdict":
            return [_alert_event(
                marker=marker, sim_run_id=sim_run_id,
                alert_name="Ransomware payload quarantined",
                severity="high", category="ransomware",
                action="quarantined", src_host=host,
                description="Third-party AV quarantined a ransomware binary",
                extra={"malware_verdict": True},
            )]

        if params.event_pattern == "repeated_host_alerts":
            n = max(params.burst_count, _REPEAT_MIN + 2)
            names = [
                "Suspicious PowerShell", "Defense evasion attempt",
                "Persistence via run key", "Lateral movement attempt",
                "Exfiltration over HTTPS",
            ]
            return [
                _alert_event(
                    marker=marker, sim_run_id=sim_run_id,
                    alert_name=names[i % len(names)],
                    severity="high", category="behavior",
                    action="detected", src_host=host,
                    extra={"repeat_index": i + 1, "repeat_count": n},
                )
                for i in range(n)
            ]

        raise ValueError(  # pragma: no cover — gated by the pydantic validator
            f"unknown event_pattern {params.event_pattern!r}"
        )

    def build_negative_control(
        self, params: ThirdPartyAlertParams, *, sim_run_id: str, iteration: int,
    ) -> list[dict[str, Any]]:
        marker = _PATTERN_MARKER[params.event_pattern]
        host = params.src_host

        if params.event_pattern == "high_severity_alert":
            # Informational audit event — below any surfacing severity.
            return [_alert_event(
                marker=marker, sim_run_id=sim_run_id,
                alert_name="Agent policy synced",
                severity="informational", category="audit",
                action="logged", src_host=host,
                description="Routine third-party agent policy sync",
                extra={"negative_control": True},
            )]

        if params.event_pattern == "malware_verdict":
            # A policy/audit category that was allowed — not a malware verdict.
            return [_alert_event(
                marker=marker, sim_run_id=sim_run_id,
                alert_name="USB device connected",
                severity="low", category="device_control",
                action="allowed", src_host=host,
                description="A USB mass-storage device was permitted by policy",
                extra={"negative_control": True},
            )]

        if params.event_pattern == "repeated_host_alerts":
            # One lonely alert — below the escalation floor.
            return [_alert_event(
                marker=marker, sim_run_id=sim_run_id,
                alert_name="Suspicious PowerShell",
                severity="high", category="behavior",
                action="detected", src_host=host,
                extra={"negative_control": True, "repeat_count": 1},
            )]

        raise ValueError(  # pragma: no cover — gated by the pydantic validator
            f"unknown event_pattern {params.event_pattern!r}"
        )

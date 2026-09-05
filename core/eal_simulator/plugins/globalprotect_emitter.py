"""
globalprotect_emitter — analytics log-streamer for the **Palo Alto Networks
Global Protect** data source (GlobalProtect gateway/portal auth log ->
``panw_ngfw_globalprotect_raw``).

Built on the ``analytics_emitter`` spine. It POSTs shape-true GlobalProtect
authentication log records to an operator-supplied collector so a customer can
validate their Cortex XSIAM **Analytics** remote-access detections fire on the
first-party PAN GlobalProtect feed. No real gateway is touched; RFC 5737
documentation IPs + a synthetic ``.invalid`` user only.

Every event pattern is authored against the field/value the analytics alert
keys on, and EVERY pattern ships a **negative control**.

Event patterns (parameter ``event_pattern``):

  ===================== ==============================================================
  preset                analytics alert it exercises (and the predicate)
  ===================== ==============================================================
  portal_brute_force    "GlobalProtect brute-force authentication" — a burst of
                        status=failure for one user then a success. Predicate:
                        count(status=failure) for one srcuser >= _BRUTE_FORCE_MIN
                        then a success. T1110. NEGATIVE: a single clean success.
  impossible_travel     "GlobalProtect impossible travel" — two status=success for
                        one user from geographically distant public IPs within a
                        window too short to travel. Predicate: two successes, same
                        srcuser, distinct public_ip_country, close timestamps. T1133.
                        NEGATIVE: two successes from the same country.
  anomalous_geo         "GlobalProtect login from an anomalous country" — a success
                        from a country the user does not normally connect from.
                        Predicate: status=success with an anomalous public_ip_country.
                        T1078.004. NEGATIVE: success from the usual country.
  ===================== ==============================================================
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from pydantic import Field, field_validator

from ..analytics_emitter import AnalyticsLogEmitter, NegativeControlEmitterParams


logger = logging.getLogger("cortexsim.eal.plugins.globalprotect_emitter")


_DATASET = "panw_ngfw_globalprotect_raw"

_USER = "gp.user@cortexsim-canary.invalid"
_HOME_IP = "203.0.113.70"
_HOME_COUNTRY = "US"
_FAR_IP = "198.51.100.120"
_FAR_COUNTRY = "CN"
_PORTAL = "gp-portal.cortexsim-canary.invalid"
_GATEWAY = "gp-gw-us-west.cortexsim-canary.invalid"

_BRUTE_FORCE_MIN = 10
_IMPOSSIBLE_TRAVEL_SECONDS = 600


_PATTERN_MARKER = {
    "portal_brute_force": "portal_brute_force_marker",
    "impossible_travel": "impossible_travel_marker",
    "anomalous_geo": "anomalous_geo_marker",
}
_EVENT_PATTERNS = tuple(_PATTERN_MARKER)


def _list_event_patterns() -> list[str]:
    return sorted(_EVENT_PATTERNS)


def _gp_event(
    *,
    marker: str,
    sim_run_id: str,
    srcuser: str,
    public_ip: str,
    country: str,
    status: str,
    when: datetime,
    stage: str = "gateway-auth",
    reason: str = "",
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Shape one GlobalProtect auth log record (PAN-OS GLOBALPROTECT subtype)."""
    body: dict[str, Any] = {
        "dataset": _DATASET,
        "log_type": "GLOBALPROTECT",
        "event_type": "globalprotect",
        "eventid": stage,
        "stage": stage,
        "status": status,
        "srcuser": srcuser,
        "public_ip": public_ip,
        "public_ip_country": country,
        "private_ip": f"10.20.0.{secrets.randbelow(200) + 10}" if status == "success" else "",
        "portal": _PORTAL,
        "gateway": _GATEWAY,
        "client_version": "GlobalProtect 6.2.0",
        "reason": reason,
        "timestamp": when.isoformat(),
        marker: True,
        "cortexsim_run_id": sim_run_id,
    }
    if extra:
        body.update(extra)
    return body


class GlobalProtectParams(NegativeControlEmitterParams):
    event_pattern: str = Field(
        default="portal_brute_force",
        description="GlobalProtect analytics alert to exercise: "
                    "portal_brute_force | impossible_travel | anomalous_geo.",
    )
    target_user: str = Field(
        default=_USER,
        description="Synthetic GlobalProtect user the events are attributed to.",
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

    @field_validator("target_user")
    @classmethod
    def _user_principal(cls, v: str) -> str:
        if "@" not in v:
            raise ValueError("target_user must look like a user principal (user@host)")
        return v


class GlobalProtectEmitter(AnalyticsLogEmitter):
    supports_negative_control = True

    class Meta:
        name = "globalprotect_emitter"
        version = "1.0.0"
        data_sources = ["pan_global_protect"]
        datasets = [_DATASET]
        detectors = [
            {
                "alert": "GlobalProtect brute-force authentication",
                "dataset": _DATASET,
                "key_fields": ["srcuser", "status"],
                "predicate": (
                    f"count(status=failure) for one srcuser >= {_BRUTE_FORCE_MIN} "
                    f"then a success"
                ),
                "mitre": "T1110",
                "negative_control": "a single clean success",
            },
            {
                "alert": "GlobalProtect impossible travel",
                "dataset": _DATASET,
                "key_fields": ["srcuser", "public_ip_country", "status", "timestamp"],
                "predicate": (
                    "two status=success for one srcuser from distinct "
                    f"public_ip_country within {_IMPOSSIBLE_TRAVEL_SECONDS}s"
                ),
                "mitre": "T1133",
                "negative_control": "two successes from the same country",
            },
            {
                "alert": "GlobalProtect login from an anomalous country",
                "dataset": _DATASET,
                "key_fields": ["srcuser", "public_ip_country", "status"],
                "predicate": "status=success from an anomalous public_ip_country",
                "mitre": "T1078.004",
                "negative_control": "success from the user's usual country",
            },
        ]
        description = (
            "Emits shape-true PAN GlobalProtect authentication log records "
            "(panw_ngfw_globalprotect_raw) into an operator-supplied collector so "
            "Cortex XSIAM exercises its remote-access Analytics detections "
            "(portal brute-force, impossible travel, anomalous-country login) on "
            "the first-party GlobalProtect feed. Ships a negative control per "
            "detector."
        )
        mitre_techniques = ["T1110", "T1133", "T1078.004"]
        eal_targets = [
            "Analytics — GlobalProtect brute-force authentication",
            "Analytics — GlobalProtect impossible travel",
            "Analytics — GlobalProtect login from an anomalous country",
            "NGFW EAL — outbound POST to XSIAM log-collector App-ID match",
        ]
        ecs_category = "authentication"
        params_model = GlobalProtectParams

    def build_events(
        self, params: GlobalProtectParams, *, sim_run_id: str, iteration: int,
    ) -> list[dict[str, Any]]:
        marker = _PATTERN_MARKER[params.event_pattern]
        user = params.target_user
        now = datetime.now(timezone.utc)

        if params.event_pattern == "portal_brute_force":
            n = max(params.burst_count, _BRUTE_FORCE_MIN + 2)
            events = [
                _gp_event(
                    marker=marker, sim_run_id=sim_run_id, srcuser=user,
                    public_ip=_FAR_IP, country=_FAR_COUNTRY, status="failure",
                    when=now + timedelta(seconds=i * 2), reason="auth-failed",
                    extra={"attempt": i + 1, "failure_count": n},
                )
                for i in range(n)
            ]
            events.append(_gp_event(
                marker=marker, sim_run_id=sim_run_id, srcuser=user,
                public_ip=_FAR_IP, country=_FAR_COUNTRY, status="success",
                when=now + timedelta(seconds=n * 2 + 2),
                extra={"after_failures": n, "brute_force_success": True},
            ))
            return events

        if params.event_pattern == "impossible_travel":
            return [
                _gp_event(
                    marker=marker, sim_run_id=sim_run_id, srcuser=user,
                    public_ip=_HOME_IP, country=_HOME_COUNTRY, status="success",
                    when=now, extra={"leg": 1},
                ),
                _gp_event(
                    marker=marker, sim_run_id=sim_run_id, srcuser=user,
                    public_ip=_FAR_IP, country=_FAR_COUNTRY, status="success",
                    when=now + timedelta(seconds=_IMPOSSIBLE_TRAVEL_SECONDS // 2),
                    extra={"leg": 2, "impossible_travel": True},
                ),
            ]

        if params.event_pattern == "anomalous_geo":
            return [_gp_event(
                marker=marker, sim_run_id=sim_run_id, srcuser=user,
                public_ip=_FAR_IP, country=_FAR_COUNTRY, status="success",
                when=now,
                extra={"anomalous_country": True, "usual_country": _HOME_COUNTRY},
            )]

        raise ValueError(  # pragma: no cover — gated by the pydantic validator
            f"unknown event_pattern {params.event_pattern!r}"
        )

    def build_negative_control(
        self, params: GlobalProtectParams, *, sim_run_id: str, iteration: int,
    ) -> list[dict[str, Any]]:
        marker = _PATTERN_MARKER[params.event_pattern]
        user = params.target_user
        now = datetime.now(timezone.utc)

        if params.event_pattern == "portal_brute_force":
            return [_gp_event(
                marker=marker, sim_run_id=sim_run_id, srcuser=user,
                public_ip=_HOME_IP, country=_HOME_COUNTRY, status="success",
                when=now, extra={"negative_control": True, "failure_count": 0},
            )]

        if params.event_pattern == "impossible_travel":
            return [
                _gp_event(
                    marker=marker, sim_run_id=sim_run_id, srcuser=user,
                    public_ip=_HOME_IP, country=_HOME_COUNTRY, status="success",
                    when=now, extra={"negative_control": True, "leg": 1},
                ),
                _gp_event(
                    marker=marker, sim_run_id=sim_run_id, srcuser=user,
                    public_ip=_HOME_IP, country=_HOME_COUNTRY, status="success",
                    when=now + timedelta(seconds=_IMPOSSIBLE_TRAVEL_SECONDS // 2),
                    extra={"negative_control": True, "leg": 2},
                ),
            ]

        if params.event_pattern == "anomalous_geo":
            return [_gp_event(
                marker=marker, sim_run_id=sim_run_id, srcuser=user,
                public_ip=_HOME_IP, country=_HOME_COUNTRY, status="success",
                when=now,
                extra={"negative_control": True, "usual_country": _HOME_COUNTRY},
            )]

        raise ValueError(  # pragma: no cover — gated by the pydantic validator
            f"unknown event_pattern {params.event_pattern!r}"
        )

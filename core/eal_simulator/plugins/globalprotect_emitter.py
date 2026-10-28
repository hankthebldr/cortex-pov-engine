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
  gateway_connected_without_auth
                        "GlobalProtect gateway session with no preceding auth" — one
                        gateway-connected success carrying an auth cookie and no
                        portal-auth event. T1556. NEGATIVE: the same session
                        preceded by a portal-auth success.
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
    "gateway_connected_without_auth": "gateway_connected_without_auth_marker",
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
    private_ip: Optional[str] = None,
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
        "private_ip": (
            private_ip if private_ip is not None
            else f"10.20.0.{secrets.randbelow(200) + 10}" if status == "success" else ""
        ),
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
                    "portal_brute_force | impossible_travel | anomalous_geo | "
                    "gateway_connected_without_auth.",
    )
    target_user: str = Field(
        default=_USER,
        description="Synthetic GlobalProtect user the events are attributed to.",
    )
    public_ip: Optional[str] = Field(
        default=None,
        description="Client public IP for gateway_connected_without_auth "
                    "(dotted quad; default the far documentation IP).",
    )
    private_ip: Optional[str] = Field(
        default=None,
        description="Tunnel-assigned private IP for gateway_connected_without_auth "
                    "(dotted quad; default a random 10.20.0.x).",
    )
    machine_name: str = Field(
        default="DESKTOP-CORTEXSIM", min_length=1,
        description="Endpoint machine name on gateway_connected_without_auth.",
    )
    endpoint_mac: str = Field(
        default="02:00:00:00:00:07", min_length=1,
        description="Endpoint MAC (locally administered) on "
                    "gateway_connected_without_auth.",
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

    @field_validator("machine_name", "endpoint_mac")
    @classmethod
    def _non_blank(cls, v: str, info) -> str:
        if not v.strip():
            raise ValueError(f"{info.field_name} must be non-empty")
        return v

    @field_validator("public_ip", "private_ip")
    @classmethod
    def _ip_quad(cls, v: Optional[str], info) -> Optional[str]:
        if v is None:
            return v
        parts = v.split(".")
        if len(parts) != 4 or not all(
            p.isascii() and p.isdigit() and 0 <= int(p) <= 255 for p in parts
        ):
            raise ValueError(
                f"{info.field_name} must be a dotted-quad IPv4 address, got '{v}'"
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
                "alert_ref": "vpn-login-brute-force-attempt",
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
                "alert_ref": "impossible-traveler-vpn",
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
                "alert_ref": "a-user-connected-to-a-vpn-from-a-new-country",
                "dataset": _DATASET,
                "key_fields": ["srcuser", "public_ip_country", "status"],
                "predicate": "status=success from an anomalous public_ip_country",
                "mitre": "T1078.004",
                "negative_control": "success from the user's usual country",
            },
            {
                "alert": "GlobalProtect gateway session without a preceding "
                         "authentication",
                "undocumented_reason": (
                    "The vendor analytics catalogue lists no alert for a "
                    "GlobalProtect gateway session lacking a prior portal-auth; "
                    "this emits the record a custom BIOC / correlation rule on "
                    "eventid=gateway-connected would key on, not a documented "
                    "Analytics detector."
                ),
                "dataset": _DATASET,
                "key_fields": ["srcuser", "eventid", "preceding_auth_event"],
                "predicate": (
                    "eventid=gateway-connected with no portal-auth success for "
                    "the same srcuser and public_ip beforehand"
                ),
                "mitre": "T1556",
                "negative_control": "the same session preceded by a portal-auth success",
            },
        ]
        description = (
            "Emits shape-true PAN GlobalProtect authentication log records "
            "(panw_ngfw_globalprotect_raw) into an operator-supplied collector so "
            "Cortex XSIAM exercises its remote-access Analytics detections "
            "(portal brute-force, impossible travel, anomalous-country login, "
            "gateway session without authentication) on "
            "the first-party GlobalProtect feed. Ships a negative control per "
            "detector."
        )
        mitre_techniques = ["T1110", "T1133", "T1078.004", "T1556"]
        eal_targets = [
            "Analytics — GlobalProtect brute-force authentication",
            "Analytics — GlobalProtect impossible travel",
            "Analytics — GlobalProtect login from an anomalous country",
            "Analytics — GlobalProtect gateway session without a preceding authentication",
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

        if params.event_pattern == "gateway_connected_without_auth":
            return [self._gateway_connected(
                params, marker, sim_run_id, now, preceding="NONE",
            )]

        raise ValueError(  # pragma: no cover — gated by the pydantic validator
            f"unknown event_pattern {params.event_pattern!r}"
        )

    @staticmethod
    def _gateway_connected(
        params: GlobalProtectParams, marker: str, sim_run_id: str,
        when: datetime, *, preceding: str, private_ip: Optional[str] = None,
    ) -> dict[str, Any]:
        return _gp_event(
            marker=marker, sim_run_id=sim_run_id, srcuser=params.target_user,
            public_ip=params.public_ip or _FAR_IP, country=_FAR_COUNTRY,
            status="success", when=when, stage="gateway-connected",
            private_ip=private_ip if private_ip is not None else params.private_ip,
            extra={
                "machine_name": params.machine_name,
                "endpoint_mac": params.endpoint_mac,
                "auth_method": "auth-cookie",
                "preceding_auth_event": preceding,
            },
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

        if params.event_pattern == "gateway_connected_without_auth":
            return [
                _gp_event(
                    marker=marker, sim_run_id=sim_run_id, srcuser=user,
                    public_ip=params.public_ip or _FAR_IP, country=_FAR_COUNTRY,
                    status="success", when=now - timedelta(seconds=60),
                    stage="portal-auth", extra={"negative_control": True},
                ),
                self._gateway_connected(
                    params, marker, sim_run_id, now, preceding="portal-auth",
                ),
            ]

        raise ValueError(  # pragma: no cover — gated by the pydantic validator
            f"unknown event_pattern {params.event_pattern!r}"
        )

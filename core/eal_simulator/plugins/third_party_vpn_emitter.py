"""
third_party_vpn_emitter — analytics log-streamer for the **Third-Party VPNs**
data source (a non-PAN VPN gateway's auth/session feed -> ``third_party_vpn_raw``).

Built on the ``analytics_emitter`` spine. It POSTs shape-true VPN
authentication / session records (the events a Cisco AnyConnect / Fortinet
SSL-VPN / generic IPsec concentrator forwards) to an operator-supplied
collector so a customer can validate that their Cortex XSIAM **Analytics**
identity/remote-access detections fire on a THIRD-PARTY VPN feed. No real VPN is
touched; RFC 5737 documentation IPs and a synthetic ``.invalid`` user only.

Every event pattern is authored against the field/value the analytics alert
keys on, and EVERY pattern ships a **negative control** — a benign login in the
same dataset that must NOT fire the detector.

Event patterns (parameter ``event_pattern``):

  ===================== ==============================================================
  preset                analytics alert it exercises (and the predicate)
  ===================== ==============================================================
  impossible_travel     "Impossible travel" — two auth_result=success for one user
                        from geographically distant src_country within a window too
                        short to physically travel. Predicate: two successes, same
                        user, distinct src_country, delta_seconds below the travel
                        floor. T1078 / T1133. NEGATIVE: two successes from the SAME
                        country/city.
  brute_force_success   "Brute-force VPN authentication" — a burst of
                        auth_result=failure for one user, then a success. Predicate:
                        count(failure) for one user >= _BRUTE_FORCE_MIN then a
                        success. T1110. NEGATIVE: a single clean success, no
                        failures.
  anomalous_geo         "VPN login from an anomalous country" — a success from a
                        country the user does not normally log in from. Predicate:
                        auth_result=success with src_country flagged first-seen /
                        anomalous. T1078.004. NEGATIVE: a success from the user's
                        usual country.
  ===================== ==============================================================
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from pydantic import Field, field_validator

from ..analytics_emitter import AnalyticsLogEmitter, NegativeControlEmitterParams


logger = logging.getLogger("cortexsim.eal.plugins.third_party_vpn_emitter")


_DATASET = "third_party_vpn_raw"

# Synthetic principal + endpoints — the reserved .invalid TLD and RFC 5737
# documentation ranges guarantee nothing resolves.
_USER = "vpn.user@cortexsim-canary.invalid"
_HOME_IP = "203.0.113.44"
_HOME_COUNTRY = "US"
_HOME_CITY = "Santa Clara"
_FAR_IP = "198.51.100.77"
_FAR_COUNTRY = "SG"
_FAR_CITY = "Singapore"
_VPN_GATEWAY = "vpn-gw01.cortexsim-canary.invalid"
_VENDOR = "GenericVPN"
_PRODUCT = "SSL-VPN"

# Detector predicate floors — behavioural, tenant-tuned in production and
# unproven here (tenant-verified is 0). The positive burst clears them; the
# negative control stays below.
_BRUTE_FORCE_MIN = 10            # failures before the success
_IMPOSSIBLE_TRAVEL_SECONDS = 600  # 10 min between two distant successes


_PATTERN_MARKER = {
    "impossible_travel": "impossible_travel_marker",
    "brute_force_success": "brute_force_success_marker",
    "anomalous_geo": "anomalous_geo_marker",
}
_EVENT_PATTERNS = tuple(_PATTERN_MARKER)


def _list_event_patterns() -> list[str]:
    return sorted(_EVENT_PATTERNS)


def _vpn_event(
    *,
    marker: str,
    sim_run_id: str,
    user: str,
    src_ip: str,
    src_country: str,
    src_city: str,
    auth_result: str,
    when: datetime,
    reason: str = "",
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Shape one normalized third-party VPN auth/session record."""
    body: dict[str, Any] = {
        "dataset": _DATASET,
        "vendor": _VENDOR,
        "product": _PRODUCT,
        "event_type": "vpn_auth",
        "user": user,
        "src_ip": src_ip,
        "src_country": src_country,
        "src_city": src_city,
        "auth_result": auth_result,
        "vpn_gateway": _VPN_GATEWAY,
        "vpn_client": "GenericVPN Client 4.10",
        "assigned_ip": f"10.8.0.{secrets.randbelow(200) + 10}" if auth_result == "success" else "",
        "session_id": secrets.randbelow(9_000_000) + 1_000_000,
        "reason": reason,
        "timestamp": when.isoformat(),
        marker: True,
        "cortexsim_run_id": sim_run_id,
    }
    if extra:
        body.update(extra)
    return body


class ThirdPartyVpnParams(NegativeControlEmitterParams):
    event_pattern: str = Field(
        default="impossible_travel",
        description="Third-party VPN analytics alert to exercise: "
                    "impossible_travel | brute_force_success | anomalous_geo.",
    )
    target_user: str = Field(
        default=_USER,
        description="Synthetic VPN user principal the events are attributed to.",
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


class ThirdPartyVpnEmitter(AnalyticsLogEmitter):
    supports_negative_control = True

    class Meta:
        name = "third_party_vpn_emitter"
        version = "1.0.0"
        # Vendor analytics catalogue join (see analytics_catalogue.py).
        data_sources = ["third_party_vpns"]
        datasets = [_DATASET]
        detectors = [
            {
                "alert": "Impossible travel (VPN)",
                "dataset": _DATASET,
                "key_fields": ["user", "src_country", "auth_result", "timestamp"],
                "predicate": (
                    "two auth_result=success for one user from distinct "
                    f"src_country within {_IMPOSSIBLE_TRAVEL_SECONDS}s"
                ),
                "mitre": "T1133",
                "negative_control": "two successes from the same country/city",
            },
            {
                "alert": "Brute-force VPN authentication",
                "dataset": _DATASET,
                "key_fields": ["user", "auth_result"],
                "predicate": (
                    f"count(auth_result=failure) for one user >= {_BRUTE_FORCE_MIN} "
                    f"then a success"
                ),
                "mitre": "T1110",
                "negative_control": "a single clean success, no failures",
            },
            {
                "alert": "VPN login from an anomalous country",
                "dataset": _DATASET,
                "key_fields": ["user", "src_country", "auth_result"],
                "predicate": "success from a first-seen / anomalous src_country",
                "mitre": "T1078.004",
                "negative_control": "success from the user's usual country",
            },
        ]
        description = (
            "Emits shape-true normalized third-party VPN auth/session records "
            "(third_party_vpn_raw) into an operator-supplied collector so Cortex "
            "XSIAM exercises its Analytics remote-access detections (impossible "
            "travel, brute-force, anomalous-country login) on a NON-PAN VPN feed. "
            "Ships a negative control per detector."
        )
        mitre_techniques = ["T1133", "T1110", "T1078.004"]
        eal_targets = [
            "Analytics — Impossible travel via third-party VPN",
            "Analytics — Brute-force VPN authentication",
            "Analytics — VPN login from an anomalous country",
            "NGFW EAL — outbound POST to XSIAM log-collector App-ID match",
        ]
        ecs_category = "authentication"
        params_model = ThirdPartyVpnParams

    def build_events(
        self, params: ThirdPartyVpnParams, *, sim_run_id: str, iteration: int,
    ) -> list[dict[str, Any]]:
        marker = _PATTERN_MARKER[params.event_pattern]
        user = params.target_user
        now = datetime.now(timezone.utc)

        if params.event_pattern == "impossible_travel":
            # Two successes, distinct countries, minutes apart — unflyable.
            return [
                _vpn_event(
                    marker=marker, sim_run_id=sim_run_id, user=user,
                    src_ip=_HOME_IP, src_country=_HOME_COUNTRY, src_city=_HOME_CITY,
                    auth_result="success", when=now,
                    extra={"leg": 1},
                ),
                _vpn_event(
                    marker=marker, sim_run_id=sim_run_id, user=user,
                    src_ip=_FAR_IP, src_country=_FAR_COUNTRY, src_city=_FAR_CITY,
                    auth_result="success",
                    when=now + timedelta(seconds=_IMPOSSIBLE_TRAVEL_SECONDS // 2),
                    extra={"leg": 2, "impossible_travel": True},
                ),
            ]

        if params.event_pattern == "brute_force_success":
            # A burst of failures then a success — the classic guess-then-in.
            n = max(params.burst_count, _BRUTE_FORCE_MIN + 2)
            events = [
                _vpn_event(
                    marker=marker, sim_run_id=sim_run_id, user=user,
                    src_ip=_FAR_IP, src_country=_FAR_COUNTRY, src_city=_FAR_CITY,
                    auth_result="failure",
                    when=now + timedelta(seconds=i * 3),
                    reason="invalid_credentials",
                    extra={"attempt": i + 1, "failure_count": n},
                )
                for i in range(n)
            ]
            events.append(_vpn_event(
                marker=marker, sim_run_id=sim_run_id, user=user,
                src_ip=_FAR_IP, src_country=_FAR_COUNTRY, src_city=_FAR_CITY,
                auth_result="success",
                when=now + timedelta(seconds=n * 3 + 2),
                extra={"after_failures": n, "brute_force_success": True},
            ))
            return events

        if params.event_pattern == "anomalous_geo":
            # A single success from a country the user never logs in from.
            return [_vpn_event(
                marker=marker, sim_run_id=sim_run_id, user=user,
                src_ip=_FAR_IP, src_country=_FAR_COUNTRY, src_city=_FAR_CITY,
                auth_result="success", when=now,
                extra={"anomalous_country": True, "usual_country": _HOME_COUNTRY},
            )]

        raise ValueError(  # pragma: no cover — gated by the pydantic validator
            f"unknown event_pattern {params.event_pattern!r}"
        )

    def build_negative_control(
        self, params: ThirdPartyVpnParams, *, sim_run_id: str, iteration: int,
    ) -> list[dict[str, Any]]:
        marker = _PATTERN_MARKER[params.event_pattern]
        user = params.target_user
        now = datetime.now(timezone.utc)

        if params.event_pattern == "impossible_travel":
            # Two successes from the SAME city — travel is zero, cannot fire.
            return [
                _vpn_event(
                    marker=marker, sim_run_id=sim_run_id, user=user,
                    src_ip=_HOME_IP, src_country=_HOME_COUNTRY, src_city=_HOME_CITY,
                    auth_result="success", when=now,
                    extra={"negative_control": True, "leg": 1},
                ),
                _vpn_event(
                    marker=marker, sim_run_id=sim_run_id, user=user,
                    src_ip=_HOME_IP, src_country=_HOME_COUNTRY, src_city=_HOME_CITY,
                    auth_result="success",
                    when=now + timedelta(seconds=_IMPOSSIBLE_TRAVEL_SECONDS // 2),
                    extra={"negative_control": True, "leg": 2},
                ),
            ]

        if params.event_pattern == "brute_force_success":
            # One clean success, no failures — nothing to brute-force.
            return [_vpn_event(
                marker=marker, sim_run_id=sim_run_id, user=user,
                src_ip=_HOME_IP, src_country=_HOME_COUNTRY, src_city=_HOME_CITY,
                auth_result="success", when=now,
                extra={"negative_control": True, "failure_count": 0},
            )]

        if params.event_pattern == "anomalous_geo":
            # A success from the user's usual country — expected, benign.
            return [_vpn_event(
                marker=marker, sim_run_id=sim_run_id, user=user,
                src_ip=_HOME_IP, src_country=_HOME_COUNTRY, src_city=_HOME_CITY,
                auth_result="success", when=now,
                extra={"negative_control": True, "usual_country": _HOME_COUNTRY},
            )]

        raise ValueError(  # pragma: no cover — gated by the pydantic validator
            f"unknown event_pattern {params.event_pattern!r}"
        )

"""
duo_auth_emitter — analytics log-streamer for the **Duo** data source (Cisco
Duo authentication log -> ``duo_auth_raw``).

Built on the ``analytics_emitter`` spine. It POSTs shape-true Duo Auth Log
records to an operator-supplied collector so a customer can validate their
Cortex XSIAM **Analytics / ABIOC** MFA detections fire on the Duo feed. No
real Duo tenant is touched; a synthetic ``.invalid`` user only.

Every event pattern is authored against the field/value the analytics alert
keys on, and EVERY pattern ships a **negative control**.

Event patterns (parameter ``event_pattern``):

  ===================== ==============================================================
  preset                analytics alert it exercises (and the predicate)
  ===================== ==============================================================
  mfa_fatigue           "MFA push-bombing / fatigue" — a burst of Duo push requests
                        the user denies, then an approval. Predicate:
                        count(factor=duo_push AND result in {denied,fraud}) >=
                        _FATIGUE_MIN then a result=success. T1621. NEGATIVE: a single
                        approved push.
  fraud_reported        "User reported a fraudulent push" — a Duo auth with
                        result=fraud. Predicate: result=fraud. T1621. NEGATIVE:
                        result=success (a normal approval).
  anomalous_geo         "Duo login from an anomalous country" — a success from a
                        country the user does not normally authenticate from.
                        Predicate: result=success with an anomalous access-device
                        country. T1078.004. NEGATIVE: success from the usual country.
  ===================== ==============================================================
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import Field, field_validator

from ..analytics_emitter import AnalyticsLogEmitter, NegativeControlEmitterParams


logger = logging.getLogger("cortexsim.eal.plugins.duo_auth_emitter")


_DATASET = "duo_auth_raw"

_USER = "mfa.user@cortexsim-canary.invalid"
_HOME_IP = "203.0.113.60"
_HOME_COUNTRY = "US"
_FAR_IP = "198.51.100.90"
_FAR_COUNTRY = "RU"
_APPLICATION = "Corp VPN (Duo)"

_FATIGUE_MIN = 8  # denied/fraud pushes before the eventual approval


_PATTERN_MARKER = {
    "mfa_fatigue": "mfa_fatigue_marker",
    "fraud_reported": "fraud_reported_marker",
    "anomalous_geo": "anomalous_geo_marker",
}
_EVENT_PATTERNS = tuple(_PATTERN_MARKER)


def _list_event_patterns() -> list[str]:
    return sorted(_EVENT_PATTERNS)


def _duo_event(
    *,
    marker: str,
    sim_run_id: str,
    user: str,
    ip: str,
    country: str,
    factor: str,
    result: str,
    reason: str = "",
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Shape one Duo Auth Log record (the subset XSIAM normalises)."""
    now = datetime.now(timezone.utc).isoformat()
    body: dict[str, Any] = {
        "dataset": _DATASET,
        "event_type": "authentication",
        "timestamp": now,
        "user": {"name": user},
        "access_device": {"ip": ip, "location": {"country": country}},
        "auth_device": {"ip": ip},
        "application": {"name": _APPLICATION},
        "factor": factor,
        "result": result,
        "reason": reason,
        "txid": f"{secrets.token_hex(8)}",
        marker: True,
        "cortexsim_run_id": sim_run_id,
    }
    if extra:
        body.update(extra)
    return body


class DuoAuthParams(NegativeControlEmitterParams):
    event_pattern: str = Field(
        default="mfa_fatigue",
        description="Duo analytics alert to exercise: mfa_fatigue | "
                    "fraud_reported | anomalous_geo.",
    )
    target_user: str = Field(
        default=_USER,
        description="Synthetic Duo user the events are attributed to.",
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


class DuoAuthEmitter(AnalyticsLogEmitter):
    supports_negative_control = True

    class Meta:
        name = "duo_auth_emitter"
        version = "1.0.0"
        data_sources = ["duo"]
        datasets = [_DATASET]
        detectors = [
            {
                "alert": "MFA push-bombing / fatigue",
                "dataset": _DATASET,
                "key_fields": ["user.name", "factor", "result"],
                "predicate": (
                    f"count(factor=duo_push AND result in {{denied,fraud}}) >= "
                    f"{_FATIGUE_MIN} then result=success"
                ),
                "mitre": "T1621",
                "negative_control": "a single approved push",
            },
            {
                "alert": "User reported a fraudulent push",
                "dataset": _DATASET,
                "key_fields": ["result"],
                "predicate": "result=fraud",
                "mitre": "T1621",
                "negative_control": "result=success (normal approval)",
            },
            {
                "alert": "Duo login from an anomalous country",
                "dataset": _DATASET,
                "key_fields": ["user.name", "access_device.location.country", "result"],
                "predicate": "result=success from an anomalous access-device country",
                "mitre": "T1078.004",
                "negative_control": "success from the user's usual country",
            },
        ]
        description = (
            "Emits shape-true Cisco Duo authentication log records (duo_auth_raw) "
            "into an operator-supplied collector so Cortex XSIAM exercises its MFA "
            "Analytics / ABIOC detections (push-bombing/fatigue, fraud-reported, "
            "anomalous-country login) without touching a real Duo tenant. Ships a "
            "negative control per detector."
        )
        mitre_techniques = ["T1621", "T1078.004"]
        eal_targets = [
            "ABIOC — MFA push-bombing / fatigue burst then approval (Duo)",
            "Analytics — user reported a fraudulent Duo push",
            "Analytics — Duo login from an anomalous country",
            "NGFW EAL — outbound POST to identity log-collector App-ID match",
        ]
        ecs_category = "authentication"
        params_model = DuoAuthParams

    def build_events(
        self, params: DuoAuthParams, *, sim_run_id: str, iteration: int,
    ) -> list[dict[str, Any]]:
        marker = _PATTERN_MARKER[params.event_pattern]
        user = params.target_user

        if params.event_pattern == "mfa_fatigue":
            n = max(params.burst_count, _FATIGUE_MIN + 2)
            events = [
                _duo_event(
                    marker=marker, sim_run_id=sim_run_id, user=user,
                    ip=_FAR_IP, country=_FAR_COUNTRY, factor="duo_push",
                    result="denied", reason="user_marked_fraud" if i % 2 else "no_response",
                    extra={"push_index": i + 1, "denied_count": n},
                )
                for i in range(n)
            ]
            events.append(_duo_event(
                marker=marker, sim_run_id=sim_run_id, user=user,
                ip=_FAR_IP, country=_FAR_COUNTRY, factor="duo_push",
                result="success", reason="user_approved",
                extra={"after_denied": n, "mfa_fatigue": True},
            ))
            return events

        if params.event_pattern == "fraud_reported":
            return [_duo_event(
                marker=marker, sim_run_id=sim_run_id, user=user,
                ip=_FAR_IP, country=_FAR_COUNTRY, factor="duo_push",
                result="fraud", reason="user_marked_fraud",
                extra={"fraud_reported": True},
            )]

        if params.event_pattern == "anomalous_geo":
            return [_duo_event(
                marker=marker, sim_run_id=sim_run_id, user=user,
                ip=_FAR_IP, country=_FAR_COUNTRY, factor="duo_push",
                result="success", reason="user_approved",
                extra={"anomalous_country": True, "usual_country": _HOME_COUNTRY},
            )]

        raise ValueError(  # pragma: no cover — gated by the pydantic validator
            f"unknown event_pattern {params.event_pattern!r}"
        )

    def build_negative_control(
        self, params: DuoAuthParams, *, sim_run_id: str, iteration: int,
    ) -> list[dict[str, Any]]:
        marker = _PATTERN_MARKER[params.event_pattern]
        user = params.target_user

        if params.event_pattern == "mfa_fatigue":
            # One approved push from home — no denied burst, cannot be fatigue.
            return [_duo_event(
                marker=marker, sim_run_id=sim_run_id, user=user,
                ip=_HOME_IP, country=_HOME_COUNTRY, factor="duo_push",
                result="success", reason="user_approved",
                extra={"negative_control": True, "denied_count": 0},
            )]

        if params.event_pattern == "fraud_reported":
            # A normal successful approval — not a fraud report.
            return [_duo_event(
                marker=marker, sim_run_id=sim_run_id, user=user,
                ip=_HOME_IP, country=_HOME_COUNTRY, factor="duo_push",
                result="success", reason="user_approved",
                extra={"negative_control": True},
            )]

        if params.event_pattern == "anomalous_geo":
            # Success from the usual country — expected, benign.
            return [_duo_event(
                marker=marker, sim_run_id=sim_run_id, user=user,
                ip=_HOME_IP, country=_HOME_COUNTRY, factor="duo_push",
                result="success", reason="user_approved",
                extra={"negative_control": True, "usual_country": _HOME_COUNTRY},
            )]

        raise ValueError(  # pragma: no cover — gated by the pydantic validator
            f"unknown event_pattern {params.event_pattern!r}"
        )

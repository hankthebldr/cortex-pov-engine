"""
idp_signin_emulator — synthetic IdP sign-in event emitter for ITDR.

Generates authentic-shape sign-in / authentication events against an
**operator-supplied collector endpoint** (typically an HTTP log
collector that the customer has already wired into Cortex ITDR / XSIAM
as a third-party log source). The plugin never talks to a real Okta /
Microsoft / Google identity tenant — every event is a JSON blob shaped
*like* the IdP's audit-event schema and POSTed to the collector URL.

Why this approach: ITDR detection looks at the raw IdP audit logs. By
posting shape-true events into a collector the customer already trusts,
we exercise the same parsing + behavioural rules without touching the
real tenant or burning a real account lockout. The customer's NGFW
EAL stack also sees the outbound POST and may correlate.

Event-shape presets (parameter ``event_pattern``):

  ============================ ========================================================
  preset                       what it simulates
  ============================ ========================================================
  impossible_travel            two successful sign-ins from geographically distant IPs
                                within an impossible interval
  mfa_fatigue                  N MFA challenges in a short window followed by an
                                approval (mfa-bombing pattern)
  credential_stuffing          N failed password attempts across N user identifiers
                                from the same source IP
  token_replay                 reuse of the same session token from a different IP /
                                user-agent than the original issuance
  brute_force_lockout          repeated failures against a single account causing
                                lockout state transition
  ============================ ========================================================

Each event carries ``cortexsim_run_id`` in its body and an
``X-Simulation-Run-ID`` HTTP header so SOC analysts can filter the
simulator traffic.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field, field_validator

from ..audit import ecs_event
from ..base import BaseSimulation, SimulationContext, SimulationResult


logger = logging.getLogger("cortexsim.eal.plugins.idp_signin_emulator")


# --------------------------------------------------------------------------
# Provider event-shape adapters
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class _SourceLocation:
    label: str
    city: str
    country: str
    ip: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "city": self.city,
            "country": self.country,
            "ip": self.ip,
        }


_LOCATIONS: dict[str, _SourceLocation] = {
    "us-west":      _SourceLocation("us-west",      "San Francisco", "US", "203.0.113.10"),
    "eu-central":   _SourceLocation("eu-central",   "Frankfurt",     "DE", "198.51.100.42"),
    "apac-east":    _SourceLocation("apac-east",    "Singapore",     "SG", "192.0.2.77"),
    "africa-south": _SourceLocation("africa-south", "Cape Town",     "ZA", "203.0.113.200"),
    "sa-east":      _SourceLocation("sa-east",      "Sao Paulo",     "BR", "198.51.100.150"),
}


def _list_locations() -> list[str]:
    return sorted(_LOCATIONS)


def _okta_event(
    *,
    event_type: str,
    outcome: str,
    user_principal: str,
    source: _SourceLocation,
    user_agent: str,
    sim_run_id: str,
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Shape an Okta system-log event (subset of /api/v1/logs schema)."""
    body = {
        "eventType": event_type,
        "published": datetime.now(timezone.utc).isoformat(),
        "outcome": {"result": outcome.upper()},
        "actor": {
            "alternateId": user_principal,
            "type": "User",
            "displayName": user_principal.split("@")[0],
        },
        "client": {
            "userAgent": {"rawUserAgent": user_agent},
            "ipAddress": source.ip,
            "geographicalContext": source.to_dict(),
        },
        "request": {"ipChain": [{"ip": source.ip}]},
        "transaction": {"id": secrets.token_hex(8)},
        "cortexsim_run_id": sim_run_id,
    }
    if extra:
        body.update(extra)
    return body


def _microsoft_event(
    *,
    event_type: str,
    outcome: str,
    user_principal: str,
    source: _SourceLocation,
    user_agent: str,
    sim_run_id: str,
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Shape a Microsoft Entra ID / AAD signInLogs event (subset)."""
    body = {
        "id": secrets.token_hex(8),
        "createdDateTime": datetime.now(timezone.utc).isoformat(),
        "userPrincipalName": user_principal,
        "userDisplayName": user_principal.split("@")[0],
        "appDisplayName": "CortexSim Canary App",
        "appId": "00000000-cortexsim-canary",
        "ipAddress": source.ip,
        "userAgent": user_agent,
        "location": {
            "city": source.city,
            "countryOrRegion": source.country,
        },
        "status": {
            "errorCode": 0 if outcome == "success" else 50053,
            "failureReason": None if outcome == "success" else event_type,
            "additionalDetails": event_type,
        },
        "cortexsim_run_id": sim_run_id,
    }
    if extra:
        body.update(extra)
    return body


def _google_event(
    *,
    event_type: str,
    outcome: str,
    user_principal: str,
    source: _SourceLocation,
    user_agent: str,
    sim_run_id: str,
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Shape a Google Workspace login activity event (subset)."""
    body = {
        "kind": "admin#reports#activity",
        "id": {
            "time": datetime.now(timezone.utc).isoformat(),
            "uniqueQualifier": secrets.token_hex(8),
            "applicationName": "login",
        },
        "actor": {"email": user_principal},
        "ipAddress": source.ip,
        "events": [{
            "type": "login",
            "name": event_type,
            "parameters": [
                {"name": "login_type", "value": "google_password"},
                {"name": "is_suspicious", "boolValue": outcome != "success"},
                {"name": "user_agent", "value": user_agent},
                {"name": "country", "value": source.country},
            ],
        }],
        "cortexsim_run_id": sim_run_id,
    }
    if extra:
        body.update(extra)
    return body


_PROVIDER_BUILDERS = {
    "okta": _okta_event,
    "microsoft": _microsoft_event,
    "google": _google_event,
}


def _list_providers() -> list[str]:
    return sorted(_PROVIDER_BUILDERS)


# --------------------------------------------------------------------------
# Event patterns — what each preset emits
# --------------------------------------------------------------------------


_EVENT_PATTERNS = (
    "impossible_travel",
    "mfa_fatigue",
    "credential_stuffing",
    "token_replay",
    "brute_force_lockout",
)


def _list_event_patterns() -> list[str]:
    return sorted(_EVENT_PATTERNS)


# --------------------------------------------------------------------------
# Pydantic params
# --------------------------------------------------------------------------


class IdpSigninEmulatorParams(BaseModel):
    collector_url: str = Field(
        ...,
        description="HTTP collector endpoint to POST synthetic IdP events to. "
                    "Typically an in-customer log forwarder already wired into "
                    "Cortex ITDR / XSIAM as a third-party source.",
    )
    provider: str = Field(
        default="okta",
        description="IdP audit-event shape: okta | microsoft | google.",
    )
    event_pattern: str = Field(
        default="impossible_travel",
        description="Behavioural pattern: impossible_travel | mfa_fatigue | "
                    "credential_stuffing | token_replay | brute_force_lockout.",
    )
    target_user: str = Field(
        default="ada.lovelace@cortexsim-canary.invalid",
        description="User principal whose audit log gets the synthetic events.",
    )
    iterations: int = Field(default=1, ge=1, le=200)
    sleep_seconds: float = Field(default=0.0, ge=0.0, le=600.0)
    request_timeout: float = Field(default=15.0, ge=1.0, le=300.0)
    burst_count: int = Field(
        default=8, ge=2, le=200,
        description="Burst size for mfa_fatigue / credential_stuffing / "
                    "brute_force_lockout patterns (events per iteration).",
    )
    user_agent: Optional[str] = Field(
        default=None,
        description="Override the outbound User-Agent header on POSTs to the collector.",
    )

    @field_validator("collector_url")
    @classmethod
    def _collector_url_safe(cls, v: str) -> str:
        parsed = urlparse(v)
        if parsed.scheme not in ("http", "https"):
            raise ValueError("collector_url must use http or https")
        if not parsed.hostname:
            raise ValueError("collector_url must include a hostname")
        return v

    @field_validator("provider")
    @classmethod
    def _provider_known(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in _PROVIDER_BUILDERS:
            raise ValueError(
                f"provider must be one of {sorted(_PROVIDER_BUILDERS)}, got '{v}'"
            )
        return v

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
    def _target_user_canary(cls, v: str) -> str:
        if "@" not in v:
            raise ValueError("target_user must look like a user-principal (user@host)")
        return v


# --------------------------------------------------------------------------
# Plugin
# --------------------------------------------------------------------------


class IdpSigninEmulator(BaseSimulation):
    class Meta:
        name = "idp_signin_emulator"
        version = "1.0.0"
        description = (
            "Emits synthetic IdP sign-in audit events (Okta / Microsoft Entra / "
            "Google Workspace shape) into an operator-supplied collector so "
            "Cortex ITDR / XSIAM exercises its identity-behavioural detection "
            "rules without touching the real tenant."
        )
        mitre_techniques = ["T1110.003", "T1110.004", "T1078.004", "T1556.006", "T1539"]
        eal_targets = [
            "ITDR — impossible-travel detection",
            "ITDR — MFA fatigue / push-bombing detection",
            "ITDR — credential-stuffing detection (failed-login burst)",
            "ITDR — session-token replay across geo / user-agent",
            "ITDR — account-lockout state transition",
            "NGFW EAL — outbound POST to log-collector App-ID match",
        ]
        params_model = IdpSigninEmulatorParams

    async def run(self, ctx: SimulationContext) -> SimulationResult:
        params: IdpSigninEmulatorParams = ctx.params  # type: ignore[assignment]
        started_at = self.utcnow()

        host = urlparse(params.collector_url).hostname or ""
        getattr(ctx, "authorise")(host)

        if ctx.dry_run:
            await ctx.emit_event(ecs_event(
                action="idp_signin_emulator_dry_run",
                outcome="success",
                category="iam",
                type_="info",
                message=(
                    f"DRY-RUN — would POST {params.iterations} {params.event_pattern} "
                    f"event burst(s) for {params.target_user} to {host}"
                ),
                campaign_id=ctx.campaign_id,
                run_id=ctx.run_id,
                step_id=ctx.step_id,
                plugin=self.Meta.name,
                target=host,
                extra={
                    "provider": params.provider,
                    "event_pattern": params.event_pattern,
                    "iterations": params.iterations,
                    "burst_count": params.burst_count,
                    "target_user": params.target_user,
                },
            ))
            return SimulationResult(
                plugin=self.Meta.name,
                step_id=ctx.step_id,
                status="success",
                started_at=started_at,
                completed_at=self.utcnow(),
                events_emitted=1,
                bytes_sent=0,
                detail={
                    "dry_run": True,
                    "provider": params.provider,
                    "event_pattern": params.event_pattern,
                    "iterations_planned": params.iterations,
                },
            )

        events_emitted = 0
        bytes_sent = 0
        responses_seen: dict[int, int] = {}

        client = self._build_client(params)
        try:
            for i in range(params.iterations):
                events_in_iter, iter_bytes, iter_status = await self._emit_pattern(
                    client, params, ctx, iteration=i + 1,
                )
                events_emitted += events_in_iter
                bytes_sent += iter_bytes
                for code, n in iter_status.items():
                    responses_seen[code] = responses_seen.get(code, 0) + n
                if i < params.iterations - 1 and params.sleep_seconds > 0:
                    await asyncio.sleep(params.sleep_seconds)
        finally:
            await client.aclose()

        return SimulationResult(
            plugin=self.Meta.name,
            step_id=ctx.step_id,
            status="success",
            started_at=started_at,
            completed_at=self.utcnow(),
            events_emitted=events_emitted,
            bytes_sent=bytes_sent,
            detail={
                "provider": params.provider,
                "event_pattern": params.event_pattern,
                "iterations_completed": params.iterations,
                "events_posted": events_emitted,
                "response_status_counts": responses_seen,
                "target": host,
                "target_user": params.target_user,
            },
        )

    # ----------------------------------------------------------------------
    # Internals
    # ----------------------------------------------------------------------

    def _build_client(self, params: IdpSigninEmulatorParams) -> httpx.AsyncClient:
        headers: dict[str, str] = {"content-type": "application/json"}
        if params.user_agent:
            headers["user-agent"] = params.user_agent
        return httpx.AsyncClient(
            timeout=params.request_timeout,
            verify=False,
            follow_redirects=False,
            headers=headers,
        )

    def _build_events_for_pattern(
        self, params: IdpSigninEmulatorParams, *, sim_run_id: str,
    ) -> list[dict[str, Any]]:
        """Return the list of audit-event bodies the pattern should POST."""
        builder = _PROVIDER_BUILDERS[params.provider]
        ua_default = params.user_agent or \
            "Mozilla/5.0 (X11; Linux x86_64) CortexSim/1.0"

        if params.event_pattern == "impossible_travel":
            return [
                builder(
                    event_type="user.session.start",
                    outcome="success",
                    user_principal=params.target_user,
                    source=_LOCATIONS["us-west"],
                    user_agent=ua_default,
                    sim_run_id=sim_run_id,
                ),
                builder(
                    event_type="user.session.start",
                    outcome="success",
                    user_principal=params.target_user,
                    source=_LOCATIONS["apac-east"],
                    user_agent=ua_default,
                    sim_run_id=sim_run_id,
                    extra={"impossible_travel_marker": True},
                ),
            ]

        if params.event_pattern == "mfa_fatigue":
            evs: list[dict[str, Any]] = []
            for _ in range(max(2, params.burst_count - 1)):
                evs.append(builder(
                    event_type="user.mfa.attempt",
                    outcome="failure",
                    user_principal=params.target_user,
                    source=_LOCATIONS["africa-south"],
                    user_agent=ua_default,
                    sim_run_id=sim_run_id,
                    extra={"mfa_factor": "push", "denied": True},
                ))
            evs.append(builder(
                event_type="user.mfa.attempt",
                outcome="success",
                user_principal=params.target_user,
                source=_LOCATIONS["africa-south"],
                user_agent=ua_default,
                sim_run_id=sim_run_id,
                extra={"mfa_factor": "push", "denied": False, "fatigue_marker": True},
            ))
            return evs

        if params.event_pattern == "credential_stuffing":
            evs = []
            for i in range(params.burst_count):
                evs.append(builder(
                    event_type="user.session.start",
                    outcome="failure",
                    user_principal=f"user{i:03d}@cortexsim-canary.invalid",
                    source=_LOCATIONS["sa-east"],
                    user_agent=ua_default,
                    sim_run_id=sim_run_id,
                    extra={"failure_reason": "INVALID_CREDENTIALS"},
                ))
            return evs

        if params.event_pattern == "token_replay":
            tx_id = secrets.token_hex(8)
            return [
                builder(
                    event_type="user.session.start",
                    outcome="success",
                    user_principal=params.target_user,
                    source=_LOCATIONS["us-west"],
                    user_agent="Chrome/124 (Windows NT 10.0)",
                    sim_run_id=sim_run_id,
                    extra={"session_token_id": tx_id},
                ),
                builder(
                    event_type="user.session.access_token",
                    outcome="success",
                    user_principal=params.target_user,
                    source=_LOCATIONS["eu-central"],
                    user_agent="curl/7.85.0",
                    sim_run_id=sim_run_id,
                    extra={
                        "session_token_id": tx_id,
                        "replay_marker": True,
                    },
                ),
            ]

        if params.event_pattern == "brute_force_lockout":
            evs = []
            for _ in range(params.burst_count):
                evs.append(builder(
                    event_type="user.session.start",
                    outcome="failure",
                    user_principal=params.target_user,
                    source=_LOCATIONS["sa-east"],
                    user_agent=ua_default,
                    sim_run_id=sim_run_id,
                    extra={"failure_reason": "INVALID_CREDENTIALS"},
                ))
            evs.append(builder(
                event_type="user.account.lock",
                outcome="success",
                user_principal=params.target_user,
                source=_LOCATIONS["sa-east"],
                user_agent=ua_default,
                sim_run_id=sim_run_id,
                extra={"lockout_marker": True},
            ))
            return evs

        raise ValueError(  # pragma: no cover — gated by the pydantic validator
            f"unknown event_pattern {params.event_pattern!r}"
        )

    async def _emit_pattern(
        self,
        client: httpx.AsyncClient,
        params: IdpSigninEmulatorParams,
        ctx: SimulationContext,
        *,
        iteration: int,
    ) -> tuple[int, int, dict[int, int]]:
        per_request_sim_id = f"{ctx.simulation_run_id}-i{iteration}-{secrets.token_hex(2)}"
        events = self._build_events_for_pattern(
            params, sim_run_id=per_request_sim_id,
        )

        headers = {
            **{k.lower(): v for k, v in ctx.telemetry_headers.items()},
            "x-simulation-run-id": per_request_sim_id,
            "content-type": "application/json",
        }

        events_posted = 0
        bytes_sent = 0
        status_counts: dict[int, int] = {}
        host = urlparse(params.collector_url).hostname or ""

        for evt in events:
            body_bytes = json.dumps(evt).encode("utf-8")
            bytes_sent += len(body_bytes)
            try:
                resp = await client.post(
                    params.collector_url, headers=headers, content=body_bytes,
                )
                events_posted += 1
                status_counts[resp.status_code] = status_counts.get(resp.status_code, 0) + 1
                await ctx.emit_event(ecs_event(
                    action="idp_signin_emulator_event",
                    outcome="success",
                    category="iam",
                    type_="user",
                    message=(
                        f"idp signin event provider={params.provider} "
                        f"pattern={params.event_pattern} "
                        f"user={params.target_user} -> {host} "
                        f"status={resp.status_code}"
                    ),
                    campaign_id=ctx.campaign_id,
                    run_id=ctx.run_id,
                    step_id=ctx.step_id,
                    plugin=self.Meta.name,
                    target=host,
                    bytes_sent=len(body_bytes),
                    extra={
                        "iteration": iteration,
                        "provider": params.provider,
                        "event_pattern": params.event_pattern,
                        "event_type": evt.get("eventType")
                                      or evt.get("status", {}).get("additionalDetails")
                                      or (evt.get("events", [{}])[0].get("name")
                                          if evt.get("events") else None),
                        "status_code": resp.status_code,
                        "simulation_request_id": per_request_sim_id,
                    },
                ))
            except httpx.HTTPError as exc:
                status_counts[0] = status_counts.get(0, 0) + 1
                await ctx.emit_event(ecs_event(
                    action="idp_signin_emulator_event",
                    outcome="failure",
                    category="iam",
                    type_="error",
                    message=f"idp signin event POST failed: {exc}",
                    campaign_id=ctx.campaign_id,
                    run_id=ctx.run_id,
                    step_id=ctx.step_id,
                    plugin=self.Meta.name,
                    target=host,
                    bytes_sent=len(body_bytes),
                    extra={
                        "iteration": iteration,
                        "provider": params.provider,
                        "error": str(exc),
                        "simulation_request_id": per_request_sim_id,
                    },
                ))

        return events_posted, bytes_sent, status_counts

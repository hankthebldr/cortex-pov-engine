"""
idp_signin_emulator — Identity-Provider (Okta / Microsoft Entra) sign-in
analytics log-streamer, built on the ``analytics_emitter`` spine.

Generates authentic-shape sign-in / authentication audit records against an
**operator-supplied collector endpoint** (typically an HTTP log collector the
customer has already wired into Cortex XSIAM / ITDR as a third-party IdP
source) so a customer can validate that their Cortex **Analytics / ABIOC**
identity detections fire. The plugin never talks to a real Okta / Microsoft
Entra / Google tenant — every record is a JSON blob shaped *like* the IdP's
audit schema and POSTed to the collector URL.

Why this approach: ITDR / Analytics detection reads the raw IdP sign-in feed
(``okta_sso`` for Okta, ``msft_azure_ad_signin`` for Entra ID,
``google_workspace_audit`` for Google Workspace). By posting shape-true events
into a collector the customer already trusts, we exercise the same parsing +
behavioural / ML rules without touching the real tenant or burning a real
account lockout. The customer's NGFW EAL stack also sees the outbound POST and
may correlate.

The plugin implements only ``build_events`` — the shared driver
(``AnalyticsLogEmitter``) owns the injectable httpx client, the
``ctx.authorise``-before-emit gate, the dry-run short-circuit, budget charging,
per-event / NDJSON batch POST, gzip, ingestion-auth header and the
``SimulationResult`` aggregation.

Event-shape presets (parameter ``event_pattern``) — each maps to a real
XSIAM identity analytics / ABIOC alert:

  ================================ =====================================================
  preset                           analytics / ABIOC alert it exercises
  ================================ =====================================================
  impossible_travel                Analytics — "Impossible travel by a cloud identity"
                                    (two successful sign-ins from geographically distant
                                    IPs within an impossible interval) — T1078.004
  mfa_fatigue                      ABIOC — MFA push-bombing burst then an approval — T1621
  credential_stuffing              Analytics — "Possible Brute-Force attempt" (failed
                                    logins across many identities, one source IP) — T1110.004
  token_replay                     Analytics — session-token reuse across geo / UA — T1539
  brute_force_lockout              Analytics — "Possible Brute-Force attempt" against a
                                    single account causing lockout — T1110.003
  azure_risky_login                Analytics — "A possible risky login to Azure"
                                    (Entra sign-in flagged atRisk / high risk from an
                                    anonymised IP) — T1078.004
  azure_ad_powershell_first_use    ABIOC — "First Azure AD PowerShell operation for a
                                    user" (first sign-in via the Azure AD PowerShell app
                                    for that principal) — T1526
  entra_mfa_reported_suspicious    Analytics — "Suspicious MFA request reported by user
                                    in Entra ID" (push challenges then a user fraud
                                    report) — T1621
  ================================ =====================================================

Providers (parameter ``provider``): ``okta`` (System Log -> ``okta_sso``) |
``microsoft`` (Entra ID signInLogs -> ``msft_azure_ad_signin``) | ``google``
(Workspace login activity -> ``google_workspace_audit``). The Azure/Entra
presets (``azure_risky_login`` / ``azure_ad_powershell_first_use`` /
``entra_mfa_reported_suspicious``) are Entra-native and are normally driven
with ``provider: microsoft``, but every preset is expressible against any
provider's schema.

Each record carries ``cortexsim_run_id`` in its body and a top-level
``dataset`` hint (the provider's real XSIAM dataset), plus an
``X-Simulation-Run-ID`` HTTP header so SOC analysts can filter simulator
traffic.
"""

from __future__ import annotations

import dataclasses
import logging
import secrets
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import Field, field_validator

from ..analytics_emitter import AnalyticsEmitterParams, AnalyticsLogEmitter


logger = logging.getLogger("cortexsim.eal.plugins.idp_signin_emulator")


# --------------------------------------------------------------------------
# Per-provider XSIAM dataset the sign-in feed normalises to.
# --------------------------------------------------------------------------

_PROVIDER_DATASET: dict[str, str] = {
    "okta": "okta_sso",
    "microsoft": "msft_azure_ad_signin",
    "google": "google_workspace_audit",
}

# The canonical dataset for this data source (the plugin's assigned feed).
_DATASET = "okta_sso"


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


# Documentation IP ranges (RFC 5737) so nothing here resolves on a real network.
_LOCATIONS: dict[str, _SourceLocation] = {
    "us-west":      _SourceLocation("us-west",      "San Francisco", "US", "203.0.113.10"),
    "eu-central":   _SourceLocation("eu-central",   "Frankfurt",     "DE", "198.51.100.42"),
    "apac-east":    _SourceLocation("apac-east",    "Singapore",     "SG", "192.0.2.77"),
    "africa-south": _SourceLocation("africa-south", "Cape Town",     "ZA", "203.0.113.200"),
    "sa-east":      _SourceLocation("sa-east",      "Sao Paulo",     "BR", "198.51.100.150"),
    # An anonymised / hosting-provider IP used by the Entra risky-login preset.
    "anon-vpn":     _SourceLocation("anon-vpn",     "Unknown",       "XX", "192.0.2.202"),
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
    dataset: str = "okta_sso",
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Shape an Okta System-Log event (subset of the /api/v1/logs schema)."""
    body: dict[str, Any] = {
        "dataset": dataset,
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
    dataset: str = "msft_azure_ad_signin",
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Shape a Microsoft Entra ID (AAD) signInLogs event (subset)."""
    body: dict[str, Any] = {
        "dataset": dataset,
        "id": secrets.token_hex(8),
        "createdDateTime": datetime.now(timezone.utc).isoformat(),
        "userPrincipalName": user_principal,
        "userDisplayName": user_principal.split("@")[0],
        "appDisplayName": "CortexSim Canary App",
        "appId": "00000000-cortexsim-canary",
        "ipAddress": source.ip,
        "userAgent": user_agent,
        "clientAppUsed": "Browser",
        "location": {
            "city": source.city,
            "countryOrRegion": source.country,
        },
        # Entra ID Protection risk fields — benign baseline unless a preset
        # overrides them via ``extra``.
        "riskLevelDuringSignIn": "none",
        "riskState": "none",
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
    dataset: str = "google_workspace_audit",
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Shape a Google Workspace login activity event (subset)."""
    body: dict[str, Any] = {
        "dataset": dataset,
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
    "azure_risky_login",
    "azure_ad_powershell_first_use",
    "entra_mfa_reported_suspicious",
)


def _list_event_patterns() -> list[str]:
    return sorted(_EVENT_PATTERNS)


# The Azure AD PowerShell first-party app (the real Microsoft app id).
_AZURE_AD_POWERSHELL_APP_ID = "1b730954-1685-4b74-9bfd-dac224a7b894"


# --------------------------------------------------------------------------
# Params — subclass the shared base, add only provider + event_pattern +
# target_user. The full transport field-set (collector_url + validator,
# iterations, sleep_seconds, request_timeout, burst_count, user_agent,
# auth_token, auth_header, auth_scheme, compress, batch) is inherited verbatim.
# --------------------------------------------------------------------------


class IdpSigninEmulatorParams(AnalyticsEmitterParams):
    provider: str = Field(
        default="okta",
        description="IdP audit-event shape: okta (okta_sso) | microsoft "
                    "(msft_azure_ad_signin) | google (google_workspace_audit).",
    )
    event_pattern: str = Field(
        default="impossible_travel",
        description="Analytics / ABIOC alert to exercise: impossible_travel | "
                    "mfa_fatigue | credential_stuffing | token_replay | "
                    "brute_force_lockout | azure_risky_login | "
                    "azure_ad_powershell_first_use | entra_mfa_reported_suspicious.",
    )
    target_user: str = Field(
        default="ada.lovelace@cortexsim-canary.invalid",
        description="User principal whose audit log gets the synthetic events.",
    )

    @property
    def dataset(self) -> str:
        """The real XSIAM dataset this provider's sign-in feed normalises to."""
        return _PROVIDER_DATASET.get(self.provider, _DATASET)

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


class IdpSigninEmulator(AnalyticsLogEmitter):
    # Three provider shapes, one map — Okta nests the principal under actor,
    # Entra carries it flat, Google nests it under actor.email. Each record
    # only has one of them, and the rest are skipped.
    CANARY_FIELDS = {
        "actor.alternateId": "{principal}",
        "actor.displayName": "{account}",
        "actor.email": "{principal}",
        "userPrincipalName": "{principal}",
        "userDisplayName": "{account}",
    }

    class Meta:
        name = "idp_signin_emulator"
        version = "2.0.0"
        description = (
            "Emits shape-true IdP sign-in audit records (Okta System Log / "
            "Microsoft Entra ID signInLogs / Google Workspace shape) into an "
            "operator-supplied collector so Cortex XSIAM / ITDR exercises its "
            "identity Analytics / ABIOC detections (impossible travel, "
            "brute-force, MFA push-bombing, risky Azure login, first Azure AD "
            "PowerShell use, user-reported suspicious MFA) without touching the "
            "real identity tenant."
        )
        # Drawn from the analytics / ABIOC alerts this plugin fires. None are
        # TA0011 (Command-and-Control) techniques, so live campaigns need only
        # simulation_authorized (not c2_authorized) per the SafetyPolicy
        # classifier.
        mitre_techniques = [
            "T1110.003", "T1110.004", "T1078.004", "T1556.006", "T1539",
            "T1621", "T1526",
        ]
        eal_targets = [
            "Analytics — impossible travel by a cloud identity (Okta / Entra sign-in)",
            "Analytics — possible brute-force attempt (failed-login burst / lockout)",
            "ABIOC — MFA push-bombing / fatigue burst then approval",
            "Analytics — session-token replay across geo / user-agent",
            "Analytics — a possible risky login to Azure (Entra atRisk sign-in)",
            "ABIOC — first Azure AD PowerShell operation for a user",
            "Analytics — suspicious MFA request reported by user in Entra ID",
            "NGFW EAL — outbound POST to identity log-collector App-ID match",
        ]
        ecs_category = "iam"
        params_model = IdpSigninEmulatorParams

    # ------------------------------------------------------------------
    # The ONLY method a source plugin implements.
    # ------------------------------------------------------------------

    def build_events(
        self, params: IdpSigninEmulatorParams, *, sim_run_id: str, iteration: int,
    ) -> list[dict[str, Any]]:
        """Return the shape-true sign-in records this iteration should POST."""
        builder = _PROVIDER_BUILDERS[params.provider]
        dataset = _PROVIDER_DATASET[params.provider]
        ua_default = params.user_agent or \
            "Mozilla/5.0 (X11; Linux x86_64) CortexSim/2.0"

        def _emit(**kwargs: Any) -> dict[str, Any]:
            return builder(sim_run_id=sim_run_id, dataset=dataset, **kwargs)

        if params.event_pattern == "impossible_travel":
            return [
                _emit(
                    event_type="user.session.start",
                    outcome="success",
                    user_principal=params.target_user,
                    source=_LOCATIONS["us-west"],
                    user_agent=ua_default,
                ),
                _emit(
                    event_type="user.session.start",
                    outcome="success",
                    user_principal=params.target_user,
                    source=_LOCATIONS["apac-east"],
                    user_agent=ua_default,
                    extra={"impossible_travel_marker": True},
                ),
            ]

        if params.event_pattern == "mfa_fatigue":
            evs: list[dict[str, Any]] = []
            for _ in range(max(2, params.burst_count - 1)):
                evs.append(_emit(
                    event_type="user.mfa.attempt",
                    outcome="failure",
                    user_principal=params.target_user,
                    source=_LOCATIONS["africa-south"],
                    user_agent=ua_default,
                    extra={"mfa_factor": "push", "denied": True},
                ))
            evs.append(_emit(
                event_type="user.mfa.attempt",
                outcome="success",
                user_principal=params.target_user,
                source=_LOCATIONS["africa-south"],
                user_agent=ua_default,
                extra={"mfa_factor": "push", "denied": False, "fatigue_marker": True},
            ))
            return evs

        if params.event_pattern == "credential_stuffing":
            evs = []
            for i in range(params.burst_count):
                evs.append(_emit(
                    event_type="user.session.start",
                    outcome="failure",
                    user_principal=f"user{i:03d}@cortexsim-canary.invalid",
                    source=_LOCATIONS["sa-east"],
                    user_agent=ua_default,
                    extra={"failure_reason": "INVALID_CREDENTIALS"},
                ))
            return evs

        if params.event_pattern == "token_replay":
            tx_id = secrets.token_hex(8)
            return [
                _emit(
                    event_type="user.session.start",
                    outcome="success",
                    user_principal=params.target_user,
                    source=_LOCATIONS["us-west"],
                    user_agent="Chrome/124 (Windows NT 10.0)",
                    extra={"session_token_id": tx_id},
                ),
                _emit(
                    event_type="user.session.access_token",
                    outcome="success",
                    user_principal=params.target_user,
                    source=_LOCATIONS["eu-central"],
                    user_agent="curl/7.85.0",
                    extra={
                        "session_token_id": tx_id,
                        "replay_marker": True,
                    },
                ),
            ]

        if params.event_pattern == "brute_force_lockout":
            evs = []
            for _ in range(params.burst_count):
                evs.append(_emit(
                    event_type="user.session.start",
                    outcome="failure",
                    user_principal=params.target_user,
                    source=_LOCATIONS["sa-east"],
                    user_agent=ua_default,
                    extra={"failure_reason": "INVALID_CREDENTIALS"},
                ))
            evs.append(_emit(
                event_type="user.account.lock",
                outcome="success",
                user_principal=params.target_user,
                source=_LOCATIONS["sa-east"],
                user_agent=ua_default,
                extra={"lockout_marker": True},
            ))
            return evs

        if params.event_pattern == "azure_risky_login":
            # A benign baseline sign-in, then the Entra-flagged risky sign-in
            # from an anonymised IP that trips "A possible risky login to Azure".
            return [
                _emit(
                    event_type="user.session.start",
                    outcome="success",
                    user_principal=params.target_user,
                    source=_LOCATIONS["us-west"],
                    user_agent=ua_default,
                ),
                _emit(
                    event_type="user.session.start",
                    outcome="success",
                    user_principal=params.target_user,
                    source=_LOCATIONS["anon-vpn"],
                    user_agent=ua_default,
                    extra={
                        "risky_login_marker": True,
                        "riskLevelDuringSignIn": "high",
                        "riskState": "atRisk",
                        "riskDetail": "none",
                        "riskEventTypes": [
                            "unfamiliarFeatures", "anonymizedIPAddress",
                        ],
                    },
                ),
            ]

        if params.event_pattern == "azure_ad_powershell_first_use":
            # First-ever sign-in for the principal via the Azure AD PowerShell
            # first-party app — the ABIOC "first Azure AD PowerShell operation".
            return [
                _emit(
                    event_type="user.session.start",
                    outcome="success",
                    user_principal=params.target_user,
                    source=_LOCATIONS["eu-central"],
                    user_agent="Mozilla/5.0 AzureADPowerShell/2.0",
                    extra={
                        "azure_ad_powershell_marker": True,
                        "first_operation": True,
                        "appDisplayName": "Azure Active Directory PowerShell",
                        "appId": _AZURE_AD_POWERSHELL_APP_ID,
                        "clientAppUsed": "Mobile Apps and Desktop clients",
                    },
                ),
            ]

        if params.event_pattern == "entra_mfa_reported_suspicious":
            # A short MFA push burst, then a user "report suspicious" event —
            # the "Suspicious MFA request reported by user in Entra ID" alert.
            evs = []
            for _ in range(max(2, params.burst_count - 1)):
                evs.append(_emit(
                    event_type="user.mfa.attempt",
                    outcome="failure",
                    user_principal=params.target_user,
                    source=_LOCATIONS["anon-vpn"],
                    user_agent=ua_default,
                    extra={"mfa_factor": "push", "denied": True},
                ))
            evs.append(_emit(
                event_type="user.mfa.reported_fraud",
                outcome="failure",
                user_principal=params.target_user,
                source=_LOCATIONS["anon-vpn"],
                user_agent=ua_default,
                extra={
                    "mfa_reported_suspicious_marker": True,
                    "fraudReported": True,
                    "authenticationRequirement": "multiFactorAuthentication",
                },
            ))
            return evs

        raise ValueError(  # pragma: no cover — gated by the pydantic validator
            f"unknown event_pattern {params.event_pattern!r}"
        )

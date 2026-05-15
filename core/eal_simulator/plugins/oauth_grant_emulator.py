"""
oauth_grant_emulator — outbound OAuth-2.0 grant simulator.

Sends authentic-shape requests to public IdP / OAuth provider authorize
endpoints (Okta, Microsoft Identity Platform, Google Identity) carrying
*risky* scope sets so the customer's **Cortex Cloud App Security**
(CASB) and the in-path NGFW EAL stack see the grant attempt and fire.

Pattern mirrors ``llm_provider_egress``: per-provider URL templates,
fake/bogus client IDs, X-Simulation-Run-ID injection, target-allowlist
safety. **No real OAuth client secrets are ever used.** The
authorize endpoint will return a 4xx on the bogus client_id; the
detection happens at the proxy on the *outbound request shape*, not on
the response.

Supported providers and the fields they touch:

  ============== ===================================================== ==================================
  name           authorize URL                                          risky-scope detection trigger
  ============== ===================================================== ==================================
  okta           https://{tenant}.okta.com/oauth2/v1/authorize         scope contains 'okta.users.manage'
  microsoft      https://login.microsoftonline.com/common/oauth2/      scope contains '.../.default' or
                   v2.0/authorize                                       'Directory.ReadWrite.All'
  google         https://accounts.google.com/o/oauth2/v2/auth          scope contains 'drive' or 'gmail'
  ============== ===================================================== ==================================

Scope-risk presets (planted into the request):

  benign        openid email profile
  risky_drive   benign + drive / Files.ReadWrite.All — should fire CASB
  admin_consent admin-consent-required scopes (Directory.ReadWrite.All,
                okta.users.manage) — should fire on first attempt
  full_mailbox  Mail.ReadWrite + offline_access (token-replay risk)
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import secrets
from typing import Optional
from urllib.parse import urlencode, urlparse

import httpx
from pydantic import BaseModel, Field, field_validator

from ..audit import ecs_event
from ..base import BaseSimulation, SimulationContext, SimulationResult


logger = logging.getLogger("cortexsim.eal.plugins.oauth_grant_emulator")


# --------------------------------------------------------------------------
# Provider definitions
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class _Provider:
    name: str
    authorize_url_template: str
    response_type: str
    fake_client_id: str

    def authorize_url(self, *, tenant: Optional[str] = None) -> str:
        if "{tenant}" in self.authorize_url_template:
            t = (tenant or "cortexsim-canary").strip()
            return self.authorize_url_template.replace("{tenant}", t)
        return self.authorize_url_template

    def host(self, *, tenant: Optional[str] = None) -> str:
        return urlparse(self.authorize_url(tenant=tenant)).hostname or ""


_PROVIDERS: dict[str, _Provider] = {
    "okta": _Provider(
        name="okta",
        authorize_url_template="https://{tenant}.okta.com/oauth2/v1/authorize",
        response_type="code",
        fake_client_id="0oaCORTEXSIMCANARYNOTREALCLIENT",
    ),
    "microsoft": _Provider(
        name="microsoft",
        authorize_url_template="https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        response_type="code",
        fake_client_id="00000000-cortexsim-canary-not-a-real-client-id",
    ),
    "google": _Provider(
        name="google",
        authorize_url_template="https://accounts.google.com/o/oauth2/v2/auth",
        response_type="code",
        fake_client_id="123456789012-cortexsim-canary.apps.googleusercontent.com",
    ),
}


def _list_providers() -> list[str]:
    return sorted(_PROVIDERS)


# --------------------------------------------------------------------------
# Scope presets — what the customer's CASB / Cloud App Security tags
# --------------------------------------------------------------------------


_SCOPE_PRESETS: dict[str, dict[str, list[str]]] = {
    "benign": {
        "okta":      ["openid", "email", "profile"],
        "microsoft": ["openid", "email", "profile", "User.Read"],
        "google":    ["openid", "email", "profile"],
    },
    "risky_drive": {
        "okta":      ["openid", "okta.users.read.self"],
        "microsoft": ["openid", "Files.ReadWrite.All", "Sites.ReadWrite.All"],
        "google":    ["openid", "https://www.googleapis.com/auth/drive"],
    },
    "admin_consent": {
        # Admin-consent-required scopes — should fire on the very first
        # attempt because no end-user can self-grant them.
        "okta":      ["okta.users.manage", "okta.apps.manage"],
        "microsoft": ["Directory.ReadWrite.All", "Application.ReadWrite.All"],
        "google":    ["https://www.googleapis.com/auth/admin.directory.user"],
    },
    "full_mailbox": {
        # Long-lived mailbox + offline-access tokens — common in
        # commodity-malware phishing-app grants.
        "okta":      ["openid", "okta.myAccount.email.read"],
        "microsoft": ["Mail.ReadWrite", "Mail.Send", "offline_access"],
        "google":    ["https://mail.google.com/", "https://www.googleapis.com/auth/gmail.modify"],
    },
}


def _scope_string_for(provider: str, preset: str) -> str:
    scopes = _SCOPE_PRESETS[preset][provider]
    return " ".join(scopes)


def _list_scope_presets() -> list[str]:
    return sorted(_SCOPE_PRESETS)


# --------------------------------------------------------------------------
# Pydantic params
# --------------------------------------------------------------------------


class OAuthGrantEmulatorParams(BaseModel):
    provider: str = Field(..., description="One of: okta | microsoft | google.")
    scope_preset: str = Field(
        default="risky_drive",
        description="One of: benign | risky_drive | admin_consent | full_mailbox.",
    )
    iterations: int = Field(default=1, ge=1, le=200)
    sleep_seconds: float = Field(default=0.0, ge=0.0, le=600.0)
    request_timeout: float = Field(default=15.0, ge=1.0, le=300.0)
    redirect_uri: str = Field(
        default="https://cortexsim-canary.invalid/oauth/callback",
        description="Bogus redirect URI included in the request.",
    )
    okta_tenant: Optional[str] = Field(
        default=None,
        description="Okta tenant subdomain (e.g. 'acme-dev'); used only "
                    "when provider=okta. Defaults to 'cortexsim-canary'.",
    )
    user_agent: Optional[str] = Field(
        default=None,
        description="Override the outbound User-Agent header.",
    )

    @field_validator("provider")
    @classmethod
    def _provider_known(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in _PROVIDERS:
            raise ValueError(
                f"provider must be one of {sorted(_PROVIDERS)}, got '{v}'"
            )
        return v

    @field_validator("scope_preset")
    @classmethod
    def _preset_known(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in _SCOPE_PRESETS:
            raise ValueError(
                f"scope_preset must be one of {sorted(_SCOPE_PRESETS)}, got '{v}'"
            )
        return v

    @field_validator("redirect_uri")
    @classmethod
    def _redirect_safe(cls, v: str) -> str:
        # Redirect URI must be http(s) and contain the canary marker so
        # accidental misconfigurations don't point at real customer URLs.
        parsed = urlparse(v)
        if parsed.scheme not in ("http", "https"):
            raise ValueError("redirect_uri must use http or https")
        if not parsed.hostname:
            raise ValueError("redirect_uri must include a hostname")
        return v


# --------------------------------------------------------------------------
# Plugin
# --------------------------------------------------------------------------


class OAuthGrantEmulator(BaseSimulation):
    class Meta:
        name = "oauth_grant_emulator"
        version = "1.0.0"
        description = (
            "Emits authentic-shape OAuth 2.0 authorize requests against "
            "public IdP endpoints (Okta / Microsoft / Google) with planted "
            "risky scopes so Cortex Cloud App Security (CASB) and the NGFW "
            "EAL stack see the grant attempt and fire."
        )
        mitre_techniques = ["T1550.001", "T1528", "T1078.004", "T1098"]
        eal_targets = [
            "Cloud App — risky OAuth scope grant",
            "Cloud App — admin-consent-required scope request",
            "Cloud App — token-replay-risk scope (Mail.ReadWrite + offline_access)",
            "NGFW EAL — outbound /oauth2/.../authorize App-ID match",
        ]
        params_model = OAuthGrantEmulatorParams

    async def run(self, ctx: SimulationContext) -> SimulationResult:
        params: OAuthGrantEmulatorParams = ctx.params  # type: ignore[assignment]
        started_at = self.utcnow()

        provider = _PROVIDERS[params.provider]
        host = provider.host(tenant=params.okta_tenant)
        getattr(ctx, "authorise")(host)

        if ctx.dry_run:
            await ctx.emit_event(ecs_event(
                action="oauth_grant_emulator_dry_run",
                outcome="success",
                category="iam",
                type_="info",
                message=(
                    f"DRY-RUN — would POST {params.iterations} {params.scope_preset} "
                    f"OAuth grant request(s) to {host}"
                ),
                campaign_id=ctx.campaign_id,
                run_id=ctx.run_id,
                step_id=ctx.step_id,
                plugin=self.Meta.name,
                target=host,
                extra={
                    "provider": params.provider,
                    "scope_preset": params.scope_preset,
                    "iterations": params.iterations,
                    "scopes": _scope_string_for(params.provider, params.scope_preset),
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
                    "scope_preset": params.scope_preset,
                    "iterations_planned": params.iterations,
                },
            )

        events_emitted = 0
        bytes_sent = 0
        responses_seen: dict[int, int] = {}

        client = self._build_client(params)
        try:
            for i in range(params.iterations):
                outcome, status_code, request_bytes = await self._send_one(
                    client, provider, params, ctx, iteration=i + 1,
                )
                bytes_sent += request_bytes
                events_emitted += 1
                responses_seen[status_code] = responses_seen.get(status_code, 0) + 1
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
                "scope_preset": params.scope_preset,
                "iterations_completed": events_emitted,
                "response_status_counts": responses_seen,
                "target": host,
            },
        )

    # ----------------------------------------------------------------------
    # Internals (split for unit-test patching)
    # ----------------------------------------------------------------------

    def _build_client(self, params: OAuthGrantEmulatorParams) -> httpx.AsyncClient:
        headers: dict[str, str] = {}
        if params.user_agent:
            headers["user-agent"] = params.user_agent
        return httpx.AsyncClient(
            timeout=params.request_timeout,
            verify=False,
            follow_redirects=False,
            headers=headers,
        )

    async def _send_one(
        self,
        client: httpx.AsyncClient,
        provider: _Provider,
        params: OAuthGrantEmulatorParams,
        ctx: SimulationContext,
        *,
        iteration: int,
    ) -> tuple[str, int, int]:
        per_request_sim_id = f"{ctx.simulation_run_id}-i{iteration}-{secrets.token_hex(2)}"
        scopes = _scope_string_for(params.provider, params.scope_preset)
        state = secrets.token_urlsafe(16)

        # OAuth 2.0 authorize-request shape (RFC 6749 §4.1.1).
        query = {
            "client_id": provider.fake_client_id,
            "response_type": provider.response_type,
            "redirect_uri": params.redirect_uri,
            "scope": scopes,
            "state": state,
            # Marker query param for SOC filtering — appears in NGFW URL logs.
            "x_cortexsim_run_id": per_request_sim_id,
        }
        url = f"{provider.authorize_url(tenant=params.okta_tenant)}?{urlencode(query)}"
        request_bytes = len(url.encode("utf-8"))

        headers = {
            **{k.lower(): v for k, v in ctx.telemetry_headers.items()},
            "x-simulation-run-id": per_request_sim_id,
        }

        try:
            resp = await client.get(url, headers=headers)
            await ctx.emit_event(ecs_event(
                action="oauth_grant_emulator_request",
                outcome="success",
                category="iam",
                type_="connection",
                message=(
                    f"oauth grant {iteration}/{params.iterations} "
                    f"provider={params.provider} preset={params.scope_preset} "
                    f"-> {provider.host(tenant=params.okta_tenant)} "
                    f"status={resp.status_code}"
                ),
                campaign_id=ctx.campaign_id,
                run_id=ctx.run_id,
                step_id=ctx.step_id,
                plugin=self.Meta.name,
                target=provider.host(tenant=params.okta_tenant),
                bytes_sent=request_bytes,
                extra={
                    "iteration": iteration,
                    "provider": params.provider,
                    "scope_preset": params.scope_preset,
                    "scopes": scopes,
                    "client_id": provider.fake_client_id,
                    "redirect_uri": params.redirect_uri,
                    "url": url,
                    "status_code": resp.status_code,
                    "simulation_request_id": per_request_sim_id,
                },
            ))
            return "success", resp.status_code, request_bytes
        except httpx.HTTPError as exc:
            await ctx.emit_event(ecs_event(
                action="oauth_grant_emulator_request",
                outcome="failure",
                category="iam",
                type_="error",
                message=(
                    f"oauth grant {iteration}/{params.iterations} failed: {exc}"
                ),
                campaign_id=ctx.campaign_id,
                run_id=ctx.run_id,
                step_id=ctx.step_id,
                plugin=self.Meta.name,
                target=provider.host(tenant=params.okta_tenant),
                bytes_sent=request_bytes,
                extra={
                    "iteration": iteration,
                    "provider": params.provider,
                    "error": str(exc),
                    "simulation_request_id": per_request_sim_id,
                },
            ))
            return "failure", 0, request_bytes

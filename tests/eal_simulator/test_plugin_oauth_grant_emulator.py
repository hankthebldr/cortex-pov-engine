"""Tests for the oauth_grant_emulator EAL plugin.

We mock ``httpx.AsyncClient`` so no real outbound traffic is generated;
each test asserts the URL/header shape the plugin would send and the
audit events it emits. Safety-policy enforcement (host allowlist
authorisation) is exercised through the executor end-to-end so the
integration between plugin and policy is covered.
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from eal_simulator import AuditLogger, Campaign, CampaignExecutor
from eal_simulator.plugins.oauth_grant_emulator import (
    OAuthGrantEmulator,
    OAuthGrantEmulatorParams,
    _PROVIDERS,
    _SCOPE_PRESETS,
    _list_providers,
    _list_scope_presets,
    _scope_string_for,
)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class _RecordingClient:
    """Stub ``httpx.AsyncClient`` for the OAuth plugin's GET-based shape."""

    def __init__(self, status_code: int = 400, raise_exc: Exception | None = None):
        self.requests: list[dict[str, Any]] = []
        self.status_code = status_code
        self.raise_exc = raise_exc
        self.closed = False

    async def get(self, url: str, *, headers=None):
        self.requests.append({
            "url": url,
            "headers": dict(headers or {}),
        })
        if self.raise_exc is not None:
            raise self.raise_exc

        class _R:
            def __init__(self, status_code: int) -> None:
                self.status_code = status_code

        return _R(self.status_code)

    async def aclose(self) -> None:
        self.closed = True


def _campaign(
    *,
    provider: str,
    scope_preset: str = "risky_drive",
    iterations: int = 1,
    target_allowlist: list[str] | None = None,
    dry_run: bool = False,
    **extra_params,
) -> Campaign:
    spec = {
        "campaign_id": "CMP-CLOUDAPP-INTEG-001",
        "name": "oauth_grant_emulator test",
        "dry_run": dry_run,
        "steps": [{
            "step_id": "step-01",
            "plugin": "oauth_grant_emulator",
            "params": {
                "provider": provider,
                "scope_preset": scope_preset,
                "iterations": iterations,
                "sleep_seconds": 0.0,
                **extra_params,
            },
        }],
    }
    if not dry_run:
        spec.update({
            "simulation_authorized": True,
            "authorized_by": "tester",
            "target_allowlist": target_allowlist or [
                # Default allowlist covers all three providers.
                "cortexsim-canary.okta.com",
                "login.microsoftonline.com",
                "accounts.google.com",
            ],
        })
    return Campaign.model_validate(spec)


# --------------------------------------------------------------------------
# Param validation
# --------------------------------------------------------------------------


class TestParamValidation:
    def test_provider_required(self):
        with pytest.raises(Exception):
            OAuthGrantEmulatorParams.model_validate({})

    def test_unknown_provider_rejected(self):
        with pytest.raises(Exception, match="provider must be one of"):
            OAuthGrantEmulatorParams.model_validate({"provider": "ping"})

    def test_unknown_scope_preset_rejected(self):
        with pytest.raises(Exception, match="scope_preset must be one of"):
            OAuthGrantEmulatorParams.model_validate({
                "provider": "okta", "scope_preset": "unicorn",
            })

    def test_iterations_bounds(self):
        with pytest.raises(Exception):
            OAuthGrantEmulatorParams.model_validate({
                "provider": "okta", "iterations": 0,
            })
        with pytest.raises(Exception):
            OAuthGrantEmulatorParams.model_validate({
                "provider": "okta", "iterations": 999,
            })

    def test_provider_normalised_to_lowercase(self):
        p = OAuthGrantEmulatorParams.model_validate({"provider": "MICROSOFT"})
        assert p.provider == "microsoft"

    def test_scope_preset_normalised_to_lowercase(self):
        p = OAuthGrantEmulatorParams.model_validate({
            "provider": "okta", "scope_preset": "Admin_Consent",
        })
        assert p.scope_preset == "admin_consent"

    def test_default_preset_is_risky_drive(self):
        p = OAuthGrantEmulatorParams.model_validate({"provider": "okta"})
        assert p.scope_preset == "risky_drive"
        assert p.iterations == 1

    def test_redirect_uri_must_be_http_or_https(self):
        with pytest.raises(Exception, match="http or https"):
            OAuthGrantEmulatorParams.model_validate({
                "provider": "okta", "redirect_uri": "ftp://x.invalid/cb",
            })

    def test_redirect_uri_requires_hostname(self):
        with pytest.raises(Exception, match="hostname"):
            OAuthGrantEmulatorParams.model_validate({
                "provider": "okta", "redirect_uri": "https:///cb",
            })


# --------------------------------------------------------------------------
# Provider definitions
# --------------------------------------------------------------------------


class TestProviderShapes:
    def test_three_providers_registered(self):
        assert _list_providers() == sorted(["okta", "microsoft", "google"])

    def test_okta_url_substitutes_tenant(self):
        p = _PROVIDERS["okta"]
        assert p.authorize_url(tenant="acme-dev") == \
            "https://acme-dev.okta.com/oauth2/v1/authorize"
        assert p.host(tenant="acme-dev") == "acme-dev.okta.com"

    def test_okta_default_tenant_is_canary(self):
        p = _PROVIDERS["okta"]
        assert "cortexsim-canary.okta.com" in p.authorize_url()

    def test_microsoft_url_is_common_tenant(self):
        p = _PROVIDERS["microsoft"]
        assert p.authorize_url() == \
            "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
        assert p.host() == "login.microsoftonline.com"

    def test_google_url_constant(self):
        p = _PROVIDERS["google"]
        assert p.authorize_url() == \
            "https://accounts.google.com/o/oauth2/v2/auth"
        assert p.host() == "accounts.google.com"

    def test_fake_client_ids_marked_as_canary(self):
        for name, prov in _PROVIDERS.items():
            assert "cortexsim" in prov.fake_client_id.lower() or \
                "CORTEX" in prov.fake_client_id, \
                f"{name} fake_client_id should be obviously a canary"


# --------------------------------------------------------------------------
# Scope presets
# --------------------------------------------------------------------------


class TestScopePresets:
    def test_four_presets_registered(self):
        assert _list_scope_presets() == sorted(
            ["benign", "risky_drive", "admin_consent", "full_mailbox"]
        )

    def test_every_preset_covers_every_provider(self):
        for preset, by_provider in _SCOPE_PRESETS.items():
            assert set(by_provider) == {"okta", "microsoft", "google"}, \
                f"preset {preset} missing provider coverage"

    def test_benign_is_safe(self):
        # Benign covers the standard OIDC trio + at most basic profile read.
        # It must NOT carry any of the high-risk scope substrings that the
        # other presets are designed to surface.
        risky_substrings = (
            "drive", "files.readwrite", "directory.readwrite", "mail.readwrite",
            "mail.send", "okta.users.manage", "okta.apps.manage",
            "admin.directory", "offline_access", "https://mail.google.com",
        )
        for provider in ("okta", "microsoft", "google"):
            scopes = _scope_string_for(provider, "benign").lower()
            for risky in risky_substrings:
                assert risky not in scopes, \
                    f"benign preset for {provider} leaked risky scope: {risky}"

    def test_risky_drive_carries_drive_scope(self):
        # At least one provider expresses drive access in the preset.
        joined = " ".join(_scope_string_for(p, "risky_drive")
                          for p in ("okta", "microsoft", "google"))
        assert "drive" in joined.lower() or "files.readwrite" in joined.lower()

    def test_admin_consent_includes_admin_scopes(self):
        ms = _scope_string_for("microsoft", "admin_consent")
        okta = _scope_string_for("okta", "admin_consent")
        google = _scope_string_for("google", "admin_consent")
        assert "Directory.ReadWrite.All" in ms
        assert "okta.users.manage" in okta
        assert "admin.directory" in google

    def test_full_mailbox_includes_offline_access_for_microsoft(self):
        # The token-replay preset must request offline_access on Microsoft
        # so a refresh-token scenario is simulatable.
        ms = _scope_string_for("microsoft", "full_mailbox")
        assert "offline_access" in ms
        assert "Mail.ReadWrite" in ms


# --------------------------------------------------------------------------
# Plugin run path — mocked httpx
# --------------------------------------------------------------------------


class TestPluginRun:
    def _run_with_stub(self, monkeypatch, **campaign_kw):
        stub = _RecordingClient()
        monkeypatch.setattr(
            OAuthGrantEmulator, "_build_client", lambda self, params: stub,
        )
        campaign = _campaign(**campaign_kw)
        executor = CampaignExecutor(audit=AuditLogger(file_path=None))
        state = _run(executor.execute(campaign))
        return state, stub

    def test_dry_run_does_not_invoke_client(self, monkeypatch):
        def _boom(self, params):  # noqa: ARG001
            raise AssertionError("client should not be built in dry-run")
        monkeypatch.setattr(OAuthGrantEmulator, "_build_client", _boom)

        campaign = _campaign(provider="okta", dry_run=True)
        state = _run(CampaignExecutor(audit=AuditLogger(file_path=None)).execute(campaign))
        sr = state.step_results[0]
        assert sr.status == "success"
        assert sr.detail["dry_run"] is True
        assert sr.events_emitted == 1

    def test_okta_request_url_carries_authorize_path(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, provider="okta", scope_preset="risky_drive",
        )
        assert state.step_results[0].status == "success"
        assert len(stub.requests) == 1
        url = stub.requests[0]["url"]
        assert url.startswith("https://cortexsim-canary.okta.com/oauth2/v1/authorize?")
        assert "client_id=" in url
        assert "response_type=code" in url
        assert "scope=" in url

    def test_okta_tenant_override_used_in_url(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, provider="okta", okta_tenant="acme-dev",
            target_allowlist=["acme-dev.okta.com"],
        )
        assert state.step_results[0].status == "success"
        assert "acme-dev.okta.com" in stub.requests[0]["url"]

    def test_microsoft_request_url(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, provider="microsoft", scope_preset="admin_consent",
        )
        url = stub.requests[0]["url"]
        assert url.startswith(
            "https://login.microsoftonline.com/common/oauth2/v2.0/authorize?"
        )
        assert "Directory.ReadWrite.All" in url

    def test_google_request_url_drive_scope(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, provider="google", scope_preset="risky_drive",
        )
        url = stub.requests[0]["url"]
        assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
        # urlencode percent-encodes the scope value.
        assert "drive" in url

    def test_iterations_send_n_requests(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, provider="okta", iterations=4,
        )
        assert len(stub.requests) == 4
        assert state.step_results[0].detail["iterations_completed"] == 4

    def test_telemetry_headers_injected_lowercase(self, monkeypatch):
        state, stub = self._run_with_stub(monkeypatch, provider="okta")
        headers = stub.requests[0]["headers"]
        assert "x-simulation-run-id" in headers
        assert headers["x-simulation-run-id"].startswith("cortexsim-")
        assert "-i1-" in headers["x-simulation-run-id"]
        assert headers["x-simulation-source"].startswith("cortexsim-eal-simulator")

    def test_per_request_simulation_id_unique_across_iterations(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, provider="okta", iterations=3,
        )
        ids = [r["headers"]["x-simulation-run-id"] for r in stub.requests]
        assert len(set(ids)) == 3, ids

    def test_response_status_counts_recorded(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, provider="okta", iterations=2,
        )
        # Stub returns 400 by default — bogus client_id triggers a 4xx.
        assert state.step_results[0].detail["response_status_counts"] == {400: 2}

    def test_url_contains_run_marker_for_soc_filtering(self, monkeypatch):
        state, stub = self._run_with_stub(monkeypatch, provider="okta")
        url = stub.requests[0]["url"]
        assert "x_cortexsim_run_id=" in url

    def test_bytes_sent_reflects_url_length(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, provider="microsoft", scope_preset="admin_consent",
        )
        sr = state.step_results[0]
        assert sr.bytes_sent > 100

    def test_safety_violation_when_target_not_allowlisted(self, monkeypatch):
        stub = _RecordingClient()
        monkeypatch.setattr(
            OAuthGrantEmulator, "_build_client", lambda self, params: stub,
        )
        # Allowlist excludes okta.com — request must be refused.
        campaign = _campaign(
            provider="okta",
            target_allowlist=["accounts.google.com"],
        )
        state = _run(CampaignExecutor(audit=AuditLogger(file_path=None)).execute(campaign))
        sr = state.step_results[0]
        assert sr.status == "error"
        assert "safety_violation" in (sr.error or "")
        assert stub.requests == []  # no traffic emitted

    def test_http_error_surfaces_as_failure_event_not_crash(self, monkeypatch):
        stub = _RecordingClient(raise_exc=httpx.ConnectError("boom"))
        monkeypatch.setattr(
            OAuthGrantEmulator, "_build_client", lambda self, params: stub,
        )
        campaign = _campaign(provider="okta", iterations=2)
        state = _run(CampaignExecutor(audit=AuditLogger(file_path=None)).execute(campaign))
        sr = state.step_results[0]
        # The plugin tolerates per-request errors and reports overall success
        # with the failure events captured in audit.
        assert sr.status == "success"
        assert len(stub.requests) == 2

    def test_user_agent_override_passed_to_client(self, monkeypatch):
        captured: dict = {}

        def _capture(self, params):
            captured["user_agent"] = params.user_agent
            return _RecordingClient()

        monkeypatch.setattr(OAuthGrantEmulator, "_build_client", _capture)
        campaign = _campaign(
            provider="okta", user_agent="okta-browser-plugin/3.5",
        )
        _run(CampaignExecutor(audit=AuditLogger(file_path=None)).execute(campaign))
        assert captured["user_agent"] == "okta-browser-plugin/3.5"


# --------------------------------------------------------------------------
# Plugin metadata / registry integration
# --------------------------------------------------------------------------


class TestRegistration:
    def test_plugin_registered_with_default_registry(self):
        from eal_simulator import get_default_registry

        reg = get_default_registry()
        assert reg.has("oauth_grant_emulator")

    def test_metadata_lists_eal_targets(self):
        from eal_simulator import get_default_registry

        meta = get_default_registry().get("oauth_grant_emulator").metadata()
        assert any("Cloud App" in t for t in meta["eal_targets"])
        assert "T1528" in meta["mitre_techniques"]
        props = meta["params_schema"]["properties"]
        assert "provider" in props and "scope_preset" in props

"""Tests for the idp_signin_emulator EAL plugin (analytics_emitter spine).

We stub ``httpx.AsyncClient`` so no real outbound traffic is generated; each
test asserts the JSON shape posted to the collector, the transport toggles
(batch / gzip / auth), and the audit events emitted. Safety-policy enforcement
(host allowlist authorisation) is exercised through the executor end-to-end.

The plugin is a per-data-source subclass of ``AnalyticsLogEmitter``: it supplies
only ``build_events`` + a params model, so proving its record shapes here also
re-exercises the shared transport spine through the identity data source.
"""
from __future__ import annotations

import asyncio
import gzip
import json
from typing import Any

import httpx
import pytest

from eal_simulator import AuditLogger, Campaign, CampaignExecutor
from eal_simulator.analytics_emitter import AnalyticsEmitterParams
from eal_simulator.plugins.idp_signin_emulator import (
    IdpSigninEmulator,
    IdpSigninEmulatorParams,
    _DATASET,
    _EVENT_PATTERNS,
    _LOCATIONS,
    _PROVIDER_BUILDERS,
    _PROVIDER_DATASET,
    _list_event_patterns,
    _list_providers,
)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _run(coro):
    return asyncio.run(coro)


class _RecordingClient:
    """Stub ``httpx.AsyncClient`` capturing every POST the plugin makes."""

    def __init__(self, status_code: int = 202, raise_exc: Exception | None = None):
        self.requests: list[dict[str, Any]] = []
        self.status_code = status_code
        self.raise_exc = raise_exc
        self.closed = False

    async def post(self, url: str, *, headers=None, content=None):
        self.requests.append({
            "url": url,
            "headers": dict(headers or {}),
            "content": content,
        })
        if self.raise_exc is not None:
            raise self.raise_exc

        class _R:
            def __init__(self, status_code: int) -> None:
                self.status_code = status_code

        return _R(self.status_code)

    async def aclose(self) -> None:
        self.closed = True


_DEFAULT_COLLECTOR = "https://collector.cortexsim-canary.invalid/logs/v1/event"


def _campaign(
    *,
    provider: str = "okta",
    event_pattern: str = "impossible_travel",
    iterations: int = 1,
    burst_count: int = 8,
    collector_url: str = _DEFAULT_COLLECTOR,
    target_allowlist: list[str] | None = None,
    dry_run: bool = False,
    **extra_params,
) -> Campaign:
    spec = {
        "campaign_id": "CMP-ITDR-INTEG-001",
        "name": "idp_signin_emulator test",
        "dry_run": dry_run,
        "steps": [{
            "step_id": "step-01",
            "plugin": "idp_signin_emulator",
            "params": {
                "collector_url": collector_url,
                "provider": provider,
                "event_pattern": event_pattern,
                "iterations": iterations,
                "burst_count": burst_count,
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
                "collector.cortexsim-canary.invalid",
            ],
        })
    return Campaign.model_validate(spec)


def _bodies(stub: _RecordingClient, *, batch: bool = False) -> list[dict[str, Any]]:
    """Decode the JSON records posted to the stub (handles gzip + NDJSON)."""
    out: list[dict[str, Any]] = []
    for r in stub.requests:
        content = r["content"]
        if "content-encoding" in {k.lower() for k in r["headers"]} or (
            isinstance(content, (bytes, bytearray)) and content[:2] == b"\x1f\x8b"
        ):
            content = gzip.decompress(content)
        text = content.decode("utf-8") if isinstance(content, (bytes, bytearray)) else content
        if batch:
            out.extend(json.loads(line) for line in text.splitlines() if line.strip())
        else:
            out.append(json.loads(text))
    return out


# --------------------------------------------------------------------------
# Param validation (shared base fields + subclass fields)
# --------------------------------------------------------------------------


class TestParamValidation:
    def test_collector_url_required(self):
        with pytest.raises(Exception):
            IdpSigninEmulatorParams.model_validate({})

    def test_unknown_provider_rejected(self):
        with pytest.raises(Exception, match="provider must be one of"):
            IdpSigninEmulatorParams.model_validate({
                "collector_url": _DEFAULT_COLLECTOR, "provider": "ping",
            })

    def test_unknown_event_pattern_rejected(self):
        with pytest.raises(Exception, match="event_pattern must be one of"):
            IdpSigninEmulatorParams.model_validate({
                "collector_url": _DEFAULT_COLLECTOR,
                "event_pattern": "supernova",
            })

    def test_collector_url_must_be_http_or_https(self):
        with pytest.raises(Exception, match="http or https"):
            IdpSigninEmulatorParams.model_validate({
                "collector_url": "ftp://x.invalid/cb",
            })

    def test_collector_url_requires_hostname(self):
        with pytest.raises(Exception, match="hostname"):
            IdpSigninEmulatorParams.model_validate({
                "collector_url": "https:///events",
            })

    def test_target_user_must_look_like_principal(self):
        with pytest.raises(Exception, match="user-principal"):
            IdpSigninEmulatorParams.model_validate({
                "collector_url": _DEFAULT_COLLECTOR,
                "target_user": "no-at-sign",
            })

    def test_provider_normalised_to_lowercase(self):
        p = IdpSigninEmulatorParams.model_validate({
            "collector_url": _DEFAULT_COLLECTOR, "provider": "OKTA",
        })
        assert p.provider == "okta"

    def test_event_pattern_normalised_to_lowercase(self):
        p = IdpSigninEmulatorParams.model_validate({
            "collector_url": _DEFAULT_COLLECTOR,
            "event_pattern": "MFA_FATIGUE",
        })
        assert p.event_pattern == "mfa_fatigue"

    def test_iterations_bounds(self):
        with pytest.raises(Exception):
            IdpSigninEmulatorParams.model_validate({
                "collector_url": _DEFAULT_COLLECTOR, "iterations": 0,
            })
        with pytest.raises(Exception):
            IdpSigninEmulatorParams.model_validate({
                "collector_url": _DEFAULT_COLLECTOR, "iterations": 999,
            })

    def test_burst_count_bounds(self):
        with pytest.raises(Exception):
            IdpSigninEmulatorParams.model_validate({
                "collector_url": _DEFAULT_COLLECTOR, "burst_count": 1,
            })
        with pytest.raises(Exception):
            IdpSigninEmulatorParams.model_validate({
                "collector_url": _DEFAULT_COLLECTOR, "burst_count": 9999,
            })

    def test_dataset_is_provider_specific(self):
        okta = IdpSigninEmulatorParams.model_validate({
            "collector_url": _DEFAULT_COLLECTOR, "provider": "okta",
        })
        assert okta.dataset == "okta_sso" == _DATASET
        msft = IdpSigninEmulatorParams.model_validate({
            "collector_url": _DEFAULT_COLLECTOR, "provider": "microsoft",
        })
        assert msft.dataset == "msft_azure_ad_signin"

    def test_auth_defaults_and_transport_toggles(self):
        p = IdpSigninEmulatorParams.model_validate({
            "collector_url": _DEFAULT_COLLECTOR,
        })
        assert p.auth_token is None
        assert p.auth_header == "Authorization"
        assert p.auth_scheme == "Bearer"
        assert p.compress is False
        assert p.batch is False

    def test_auth_token_is_secret_and_not_reprd(self):
        p = IdpSigninEmulatorParams.model_validate({
            "collector_url": _DEFAULT_COLLECTOR,
            "auth_token": "super-secret-hec-token",
        })
        assert "super-secret-hec-token" not in repr(p)
        assert "super-secret-hec-token" not in str(p)
        assert "super-secret-hec-token" not in p.model_dump_json()
        assert p.auth_token.get_secret_value() == "super-secret-hec-token"

    def test_base_params_shared_fields_present(self):
        fields = IdpSigninEmulatorParams.model_fields
        for name in AnalyticsEmitterParams.model_fields:
            assert name in fields


# --------------------------------------------------------------------------
# Provider event-shape adapters (unit tests on the builders)
# --------------------------------------------------------------------------


class TestProviderShapes:
    def test_three_providers_registered(self):
        assert _list_providers() == sorted(["okta", "microsoft", "google"])

    def test_eight_event_patterns_registered(self):
        assert _list_event_patterns() == sorted(_EVENT_PATTERNS)
        assert len(_EVENT_PATTERNS) == 8

    def test_every_provider_has_a_dataset(self):
        for provider in _PROVIDER_BUILDERS:
            assert provider in _PROVIDER_DATASET

    def test_okta_event_carries_eventType_actor_and_dataset(self):
        evt = _PROVIDER_BUILDERS["okta"](
            event_type="user.session.start",
            outcome="success",
            user_principal="u@x.invalid",
            source=_LOCATIONS["us-west"],
            user_agent="curl/7.85",
            sim_run_id="cortexsim-x-i1-aaaa",
        )
        assert evt["dataset"] == "okta_sso"
        assert evt["eventType"] == "user.session.start"
        assert evt["outcome"]["result"] == "SUCCESS"
        assert evt["actor"]["alternateId"] == "u@x.invalid"
        assert evt["client"]["ipAddress"] == "203.0.113.10"
        assert evt["cortexsim_run_id"] == "cortexsim-x-i1-aaaa"

    def test_microsoft_event_carries_userPrincipalName_and_dataset(self):
        evt = _PROVIDER_BUILDERS["microsoft"](
            event_type="user.session.start",
            outcome="failure",
            user_principal="u@x.invalid",
            source=_LOCATIONS["eu-central"],
            user_agent="curl/7.85",
            sim_run_id="cortexsim-x-i1-aaaa",
        )
        assert evt["dataset"] == "msft_azure_ad_signin"
        assert evt["userPrincipalName"] == "u@x.invalid"
        assert evt["status"]["errorCode"] == 50053
        assert evt["location"]["countryOrRegion"] == "DE"

    def test_google_event_carries_actor_email_and_dataset(self):
        evt = _PROVIDER_BUILDERS["google"](
            event_type="login_failure",
            outcome="failure",
            user_principal="u@x.invalid",
            source=_LOCATIONS["sa-east"],
            user_agent="curl/7.85",
            sim_run_id="cortexsim-x-i1-aaaa",
        )
        assert evt["dataset"] == "google_workspace_audit"
        assert evt["actor"]["email"] == "u@x.invalid"
        assert evt["id"]["applicationName"] == "login"
        assert evt["events"][0]["name"] == "login_failure"


# --------------------------------------------------------------------------
# Plugin run path — stubbed httpx (no network)
# --------------------------------------------------------------------------


class TestPluginRun:
    def _run_with_stub(self, monkeypatch, *, status_code=202, raise_exc=None, **campaign_kw):
        stub = _RecordingClient(status_code=status_code, raise_exc=raise_exc)
        monkeypatch.setattr(
            IdpSigninEmulator, "_build_client", lambda self, params: stub,
        )
        campaign = _campaign(**campaign_kw)
        executor = CampaignExecutor(audit=AuditLogger(file_path=None))
        state = _run(executor.execute(campaign))
        return state, stub

    def test_dry_run_does_not_invoke_client(self, monkeypatch):
        def _boom(self, params):  # noqa: ARG001
            raise AssertionError("client should not be built in dry-run")
        monkeypatch.setattr(IdpSigninEmulator, "_build_client", _boom)

        campaign = _campaign(dry_run=True)
        state = _run(CampaignExecutor(audit=AuditLogger(file_path=None)).execute(campaign))
        sr = state.step_results[0]
        assert sr.status == "success"
        assert sr.detail["dry_run"] is True
        assert sr.events_emitted == 1
        assert sr.bytes_sent == 0

    def test_impossible_travel_posts_two_events(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="impossible_travel",
        )
        assert state.step_results[0].status == "success"
        assert len(stub.requests) == 2
        bodies = _bodies(stub)
        ips = {b["client"]["ipAddress"] for b in bodies}
        assert len(ips) == 2  # two distinct geo IPs
        assert any(b.get("impossible_travel_marker") for b in bodies)
        assert all(b["dataset"] == "okta_sso" for b in bodies)

    def test_mfa_fatigue_posts_burst_with_final_success(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="mfa_fatigue", burst_count=5,
        )
        bodies = _bodies(stub)
        assert len(bodies) == 5
        outcomes = [b["outcome"]["result"] for b in bodies]
        assert outcomes.count("FAILURE") == 4
        assert outcomes.count("SUCCESS") == 1
        assert outcomes[-1] == "SUCCESS"
        assert any(b.get("fatigue_marker") for b in bodies)

    def test_credential_stuffing_uses_distinct_user_ids(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="credential_stuffing", burst_count=6,
        )
        bodies = _bodies(stub)
        users = {b["actor"]["alternateId"] for b in bodies}
        assert len(users) == 6
        ips = {b["client"]["ipAddress"] for b in bodies}
        assert len(ips) == 1  # all from the same source IP

    def test_token_replay_reuses_session_token_id_across_two_geos(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="token_replay",
        )
        bodies = _bodies(stub)
        assert len(bodies) == 2
        token_ids = {b["session_token_id"] for b in bodies}
        assert len(token_ids) == 1  # same token id reused
        ips = {b["client"]["ipAddress"] for b in bodies}
        assert len(ips) == 2  # different source geos
        assert any(b.get("replay_marker") for b in bodies)

    def test_brute_force_lockout_emits_failures_then_lock(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="brute_force_lockout", burst_count=4,
        )
        bodies = _bodies(stub)
        assert len(bodies) == 5  # 4 failures + 1 lock
        events = [b["eventType"] for b in bodies]
        assert events.count("user.session.start") == 4
        assert events[-1] == "user.account.lock"
        assert any(b.get("lockout_marker") for b in bodies)

    def test_azure_risky_login_flags_atrisk_signin(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, provider="microsoft", event_pattern="azure_risky_login",
        )
        bodies = _bodies(stub)
        assert len(bodies) == 2  # benign baseline + risky sign-in
        risky = [b for b in bodies if b.get("risky_login_marker")]
        assert len(risky) == 1
        assert risky[0]["riskState"] == "atRisk"
        assert risky[0]["riskLevelDuringSignIn"] == "high"
        assert "anonymizedIPAddress" in risky[0]["riskEventTypes"]
        assert all(b["dataset"] == "msft_azure_ad_signin" for b in bodies)

    def test_azure_ad_powershell_first_use_single_flagged_event(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, provider="microsoft",
            event_pattern="azure_ad_powershell_first_use",
        )
        bodies = _bodies(stub)
        assert len(bodies) == 1
        evt = bodies[0]
        assert evt.get("azure_ad_powershell_marker") is True
        assert evt["first_operation"] is True
        assert evt["appDisplayName"] == "Azure Active Directory PowerShell"
        assert evt["appId"] == "1b730954-1685-4b74-9bfd-dac224a7b894"

    def test_entra_mfa_reported_suspicious_ends_with_fraud_report(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, provider="microsoft",
            event_pattern="entra_mfa_reported_suspicious", burst_count=4,
        )
        bodies = _bodies(stub)
        assert len(bodies) == 4  # 3 push challenges + 1 report
        assert bodies[-1].get("mfa_reported_suspicious_marker") is True
        assert bodies[-1]["fraudReported"] is True
        assert bodies[-1]["status"]["additionalDetails"] == "user.mfa.reported_fraud"

    def test_iterations_multiply_event_count(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="impossible_travel", iterations=3,
        )
        assert len(stub.requests) == 6  # 2 events per iteration * 3 iterations
        assert state.step_results[0].detail["events_posted"] == 6

    # -- transport toggles ------------------------------------------------

    def test_batch_mode_single_ndjson_post(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="impossible_travel", batch=True,
        )
        assert len(stub.requests) == 1  # two records, ONE POST
        req = stub.requests[0]
        assert req["headers"]["content-type"] == "application/x-ndjson"
        records = _bodies(stub, batch=True)
        assert len(records) == 2
        detail = state.step_results[0].detail
        assert detail["events_posted"] == 2
        assert detail["response_status_counts"] == {202: 1}
        assert detail["batch"] is True

    def test_compress_mode_gzips_body(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="impossible_travel", compress=True,
        )
        req = stub.requests[0]
        assert req["headers"]["content-encoding"] == "gzip"
        assert req["content"][:2] == b"\x1f\x8b"  # gzip magic
        body = _bodies(stub)[0]
        assert body["dataset"] == "okta_sso"

    # -- telemetry / identity --------------------------------------------

    def test_telemetry_headers_injected_lowercase(self, monkeypatch):
        state, stub = self._run_with_stub(monkeypatch)
        headers = stub.requests[0]["headers"]
        assert "x-simulation-run-id" in headers
        assert headers["x-simulation-run-id"].startswith("cortexsim-")
        assert "-i1-" in headers["x-simulation-run-id"]
        assert headers["x-simulation-source"].startswith("cortexsim-eal-simulator")
        assert headers["content-type"] == "application/json"

    def test_per_request_simulation_id_unique_across_iterations(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="impossible_travel", iterations=3,
        )
        ids = {r["headers"]["x-simulation-run-id"] for r in stub.requests}
        assert len(ids) == 3, ids

    def test_response_status_counts_recorded(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="impossible_travel", iterations=2,
        )
        assert state.step_results[0].detail["response_status_counts"] == {202: 4}

    def test_request_url_targets_collector(self, monkeypatch):
        state, stub = self._run_with_stub(monkeypatch)
        for r in stub.requests:
            assert r["url"] == _DEFAULT_COLLECTOR

    def test_cortexsim_run_id_in_every_body(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="brute_force_lockout", burst_count=3,
        )
        for b in _bodies(stub):
            assert b["cortexsim_run_id"].startswith("cortexsim-")

    # -- safety + resilience ---------------------------------------------

    def test_safety_violation_when_collector_not_allowlisted(self, monkeypatch):
        stub = _RecordingClient()
        monkeypatch.setattr(
            IdpSigninEmulator, "_build_client", lambda self, params: stub,
        )
        campaign = _campaign(target_allowlist=["other.invalid"])
        state = _run(CampaignExecutor(audit=AuditLogger(file_path=None)).execute(campaign))
        sr = state.step_results[0]
        assert sr.status == "error"
        assert "safety_violation" in (sr.error or "")
        assert stub.requests == []

    def test_http_error_does_not_crash_iteration(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="impossible_travel", iterations=2,
            raise_exc=httpx.ConnectError("boom"),
        )
        sr = state.step_results[0]
        # A transport failure is NOT a green step — nothing reached the
        # collector, so the ledger must say so with a taxonomy code.
        assert sr.status == "error"
        assert sr.detail["delivery"]["outcome"] == "error"
        assert sr.detail["delivery"]["records_delivered"] == 0
        assert sr.detail["delivery"]["failures"][0]["code"] == "collector_unreachable"
        assert sr.detail["delivery"]["failures"][0]["remediation"]
        assert "collector_unreachable" in (sr.error or "")
        assert len(stub.requests) == 4  # all attempted despite errors
        assert sr.detail["response_status_counts"].get(0, 0) == 4
        assert sr.detail["events_posted"] == 0

    def test_microsoft_provider_event_shape_in_request_body(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, provider="microsoft", event_pattern="credential_stuffing",
            burst_count=3,
        )
        bodies = _bodies(stub)
        for b in bodies:
            assert "userPrincipalName" in b
            assert "appId" in b
            assert b["status"]["errorCode"] == 50053
            assert b["dataset"] == "msft_azure_ad_signin"

    def test_google_provider_event_shape_in_request_body(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, provider="google", event_pattern="impossible_travel",
        )
        bodies = _bodies(stub)
        for b in bodies:
            assert b["kind"] == "admin#reports#activity"
            assert b["id"]["applicationName"] == "login"
            assert b["dataset"] == "google_workspace_audit"


# --------------------------------------------------------------------------
# Injectable transport — auth header (exercised on the real _build_client)
# --------------------------------------------------------------------------


class TestAuthHeader:
    def test_no_auth_header_when_token_absent(self):
        emitter = IdpSigninEmulator()
        emitter._verify_tls = False
        params = IdpSigninEmulatorParams.model_validate({
            "collector_url": _DEFAULT_COLLECTOR,
        })
        client = emitter._build_client(params)
        try:
            assert "authorization" not in {k.lower() for k in client.headers}
        finally:
            _run(client.aclose())

    def test_bearer_auth_header_set_from_token(self):
        emitter = IdpSigninEmulator()
        emitter._verify_tls = False
        params = IdpSigninEmulatorParams.model_validate({
            "collector_url": _DEFAULT_COLLECTOR,
            "auth_token": "tok-123",
        })
        client = emitter._build_client(params)
        try:
            assert client.headers["authorization"] == "Bearer tok-123"
        finally:
            _run(client.aclose())

    def test_raw_token_when_scheme_empty(self):
        emitter = IdpSigninEmulator()
        emitter._verify_tls = False
        params = IdpSigninEmulatorParams.model_validate({
            "collector_url": _DEFAULT_COLLECTOR,
            "auth_token": "raw-hec-token",
            "auth_header": "X-HEC-Token",
            "auth_scheme": "",
        })
        client = emitter._build_client(params)
        try:
            assert client.headers["x-hec-token"] == "raw-hec-token"
        finally:
            _run(client.aclose())

    def test_auth_token_never_leaks_into_event_body(self):
        # The secret is a client-level header only; it must never appear in a
        # record the plugin builds.
        emitter = IdpSigninEmulator()
        params = IdpSigninEmulatorParams.model_validate({
            "collector_url": _DEFAULT_COLLECTOR,
            "auth_token": "leak-canary-token",
        })
        events = emitter.build_events(params, sim_run_id="cortexsim-x-i1-aa", iteration=1)
        assert "leak-canary-token" not in json.dumps(events)


# --------------------------------------------------------------------------
# Registry / metadata
# --------------------------------------------------------------------------


class TestRegistration:
    def test_plugin_registered_with_default_registry(self):
        from eal_simulator import get_default_registry

        reg = get_default_registry()
        assert reg.has("idp_signin_emulator")

    def test_metadata_lists_analytics_and_abioc_targets(self):
        from eal_simulator import get_default_registry

        meta = get_default_registry().get("idp_signin_emulator").metadata()
        assert any("Analytics" in t for t in meta["eal_targets"])
        assert any("ABIOC" in t for t in meta["eal_targets"])
        assert "T1078.004" in meta["mitre_techniques"]
        props = meta["params_schema"]["properties"]
        assert "collector_url" in props
        assert "event_pattern" in props
        assert "auth_token" in props
        assert "compress" in props
        assert "batch" in props

    def test_not_c2_classified(self):
        # Identity techniques must NOT trip the C2 gate — only
        # simulation_authorized is required, not c2_authorized.
        from eal_simulator.safety import is_c2_plugin

        assert is_c2_plugin(
            "idp_signin_emulator", IdpSigninEmulator.Meta.mitre_techniques,
        ) is False

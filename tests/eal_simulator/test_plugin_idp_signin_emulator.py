"""Tests for the idp_signin_emulator EAL plugin.

We mock ``httpx.AsyncClient`` so no real outbound traffic is generated;
each test asserts the JSON shape posted to the collector and the audit
events emitted. Safety-policy enforcement (host allowlist authorisation)
is exercised through the executor end-to-end.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from eal_simulator import AuditLogger, Campaign, CampaignExecutor
from eal_simulator.plugins.idp_signin_emulator import (
    IdpSigninEmulator,
    IdpSigninEmulatorParams,
    _EVENT_PATTERNS,
    _LOCATIONS,
    _PROVIDER_BUILDERS,
    _list_event_patterns,
    _list_providers,
)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class _RecordingClient:
    """Stub ``httpx.AsyncClient`` for the IdP plugin's POST shape."""

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


_DEFAULT_COLLECTOR = "https://collector.cortexsim-canary.invalid/idp/events"


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


# --------------------------------------------------------------------------
# Param validation
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


# --------------------------------------------------------------------------
# Provider event-shape adapters (unit tests on the builders)
# --------------------------------------------------------------------------


class TestProviderShapes:
    def test_three_providers_registered(self):
        assert _list_providers() == sorted(["okta", "microsoft", "google"])

    def test_five_event_patterns_registered(self):
        assert _list_event_patterns() == sorted(_EVENT_PATTERNS)

    def test_okta_event_carries_eventType_and_actor(self):
        evt = _PROVIDER_BUILDERS["okta"](
            event_type="user.session.start",
            outcome="success",
            user_principal="u@x.invalid",
            source=_LOCATIONS["us-west"],
            user_agent="curl/7.85",
            sim_run_id="cortexsim-x-i1-aaaa",
        )
        assert evt["eventType"] == "user.session.start"
        assert evt["outcome"]["result"] == "SUCCESS"
        assert evt["actor"]["alternateId"] == "u@x.invalid"
        assert evt["client"]["ipAddress"] == "203.0.113.10"
        assert evt["cortexsim_run_id"].startswith("cortexsim-")

    def test_microsoft_event_carries_userPrincipalName(self):
        evt = _PROVIDER_BUILDERS["microsoft"](
            event_type="user.session.start",
            outcome="failure",
            user_principal="u@x.invalid",
            source=_LOCATIONS["eu-central"],
            user_agent="curl/7.85",
            sim_run_id="cortexsim-x-i1-aaaa",
        )
        assert evt["userPrincipalName"] == "u@x.invalid"
        assert evt["status"]["errorCode"] == 50053
        assert evt["location"]["countryOrRegion"] == "DE"

    def test_google_event_carries_actor_email_and_login_kind(self):
        evt = _PROVIDER_BUILDERS["google"](
            event_type="login_failure",
            outcome="failure",
            user_principal="u@x.invalid",
            source=_LOCATIONS["sa-east"],
            user_agent="curl/7.85",
            sim_run_id="cortexsim-x-i1-aaaa",
        )
        assert evt["actor"]["email"] == "u@x.invalid"
        assert evt["id"]["applicationName"] == "login"
        assert evt["events"][0]["name"] == "login_failure"


# --------------------------------------------------------------------------
# Plugin run path — mocked httpx
# --------------------------------------------------------------------------


class TestPluginRun:
    def _run_with_stub(self, monkeypatch, **campaign_kw):
        stub = _RecordingClient()
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

    def test_impossible_travel_posts_two_events(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="impossible_travel",
        )
        assert state.step_results[0].status == "success"
        assert len(stub.requests) == 2
        bodies = [json.loads(r["content"]) for r in stub.requests]
        ips = {b["client"]["ipAddress"] for b in bodies}
        assert len(ips) == 2  # two distinct geo IPs
        assert any(b.get("impossible_travel_marker") for b in bodies)

    def test_mfa_fatigue_posts_burst_with_final_success(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="mfa_fatigue", burst_count=5,
        )
        bodies = [json.loads(r["content"]) for r in stub.requests]
        assert len(bodies) == 5
        outcomes = [b["outcome"]["result"] for b in bodies]
        # n-1 failures then 1 success.
        assert outcomes.count("FAILURE") == 4
        assert outcomes.count("SUCCESS") == 1
        assert outcomes[-1] == "SUCCESS"
        assert any(b.get("fatigue_marker") for b in bodies)

    def test_credential_stuffing_uses_distinct_user_ids(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="credential_stuffing", burst_count=6,
        )
        bodies = [json.loads(r["content"]) for r in stub.requests]
        users = {b["actor"]["alternateId"] for b in bodies}
        assert len(users) == 6
        # All from the same source IP.
        ips = {b["client"]["ipAddress"] for b in bodies}
        assert len(ips) == 1

    def test_token_replay_reuses_session_token_id_across_two_geos(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="token_replay",
        )
        bodies = [json.loads(r["content"]) for r in stub.requests]
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
        bodies = [json.loads(r["content"]) for r in stub.requests]
        assert len(bodies) == 5  # 4 failures + 1 lock
        events = [b["eventType"] for b in bodies]
        assert events.count("user.session.start") == 4
        assert events[-1] == "user.account.lock"
        assert any(b.get("lockout_marker") for b in bodies)

    def test_iterations_multiply_event_count(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="impossible_travel", iterations=3,
        )
        # 2 events per iteration * 3 iterations.
        assert len(stub.requests) == 6
        assert state.step_results[0].detail["events_posted"] == 6

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
        # Each iteration has its own per-request id, but events within an
        # iteration share that id. So 3 iterations -> 3 distinct ids.
        assert len(ids) == 3, ids

    def test_response_status_counts_recorded(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="impossible_travel", iterations=2,
        )
        # Stub returns 202 by default for every request; 2 iters * 2 events = 4.
        assert state.step_results[0].detail["response_status_counts"] == {202: 4}

    def test_request_url_targets_collector(self, monkeypatch):
        state, stub = self._run_with_stub(monkeypatch)
        for r in stub.requests:
            assert r["url"] == _DEFAULT_COLLECTOR

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
        stub = _RecordingClient(raise_exc=httpx.ConnectError("boom"))
        monkeypatch.setattr(
            IdpSigninEmulator, "_build_client", lambda self, params: stub,
        )
        campaign = _campaign(event_pattern="impossible_travel", iterations=2)
        state = _run(CampaignExecutor(audit=AuditLogger(file_path=None)).execute(campaign))
        sr = state.step_results[0]
        assert sr.status == "success"
        assert len(stub.requests) == 4  # all attempted despite errors
        assert sr.detail["response_status_counts"].get(0, 0) == 4

    def test_microsoft_provider_event_shape_in_request_body(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, provider="microsoft", event_pattern="credential_stuffing",
            burst_count=3,
        )
        bodies = [json.loads(r["content"]) for r in stub.requests]
        for b in bodies:
            assert "userPrincipalName" in b
            assert "appId" in b
            assert b["status"]["errorCode"] == 50053

    def test_google_provider_event_shape_in_request_body(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, provider="google", event_pattern="impossible_travel",
        )
        bodies = [json.loads(r["content"]) for r in stub.requests]
        for b in bodies:
            assert b["kind"] == "admin#reports#activity"
            assert b["id"]["applicationName"] == "login"


# --------------------------------------------------------------------------
# Plugin metadata / registry integration
# --------------------------------------------------------------------------


class TestRegistration:
    def test_plugin_registered_with_default_registry(self):
        from eal_simulator import get_default_registry

        reg = get_default_registry()
        assert reg.has("idp_signin_emulator")

    def test_metadata_lists_eal_targets(self):
        from eal_simulator import get_default_registry

        meta = get_default_registry().get("idp_signin_emulator").metadata()
        assert any("ITDR" in t for t in meta["eal_targets"])
        assert "T1110.003" in meta["mitre_techniques"]
        props = meta["params_schema"]["properties"]
        assert "collector_url" in props
        assert "event_pattern" in props

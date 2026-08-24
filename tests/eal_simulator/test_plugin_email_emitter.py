"""Tests for the email_emitter EAL plugin.

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
from eal_simulator.plugins.email_emitter import (
    EmailEmitter,
    EmailEmitterParams,
    _EVENT_PATTERNS,
    _PATTERN_INDICATORS,
    _PROVIDER_BUILDERS,
    _list_event_patterns,
    _list_providers,
)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _run(coro):
    return asyncio.run(coro)


class _RecordingClient:
    """Stub ``httpx.AsyncClient`` for the email plugin's POST shape."""

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


_DEFAULT_COLLECTOR = "https://collector.cortexsim-canary.invalid/email/events"


def _campaign(
    *,
    provider: str = "proofpoint",
    event_pattern: str = "phishing_link",
    iterations: int = 1,
    burst_count: int = 4,
    collector_url: str = _DEFAULT_COLLECTOR,
    target_allowlist: list[str] | None = None,
    dry_run: bool = False,
    **extra_params,
) -> Campaign:
    spec = {
        "campaign_id": "CMP-EMAIL-INTEG-001",
        "name": "email_emitter test",
        "dry_run": dry_run,
        "steps": [{
            "step_id": "step-01",
            "plugin": "email_emitter",
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
            EmailEmitterParams.model_validate({})

    def test_unknown_provider_rejected(self):
        with pytest.raises(Exception, match="provider must be one of"):
            EmailEmitterParams.model_validate({
                "collector_url": _DEFAULT_COLLECTOR, "provider": "gmail",
            })

    def test_unknown_event_pattern_rejected(self):
        with pytest.raises(Exception, match="event_pattern must be one of"):
            EmailEmitterParams.model_validate({
                "collector_url": _DEFAULT_COLLECTOR,
                "event_pattern": "smishing",
            })

    def test_collector_url_must_be_http_or_https(self):
        with pytest.raises(Exception, match="http or https"):
            EmailEmitterParams.model_validate({
                "collector_url": "ftp://x.invalid/cb",
            })

    def test_collector_url_requires_hostname(self):
        with pytest.raises(Exception, match="hostname"):
            EmailEmitterParams.model_validate({
                "collector_url": "https:///events",
            })

    def test_target_user_must_look_like_mailbox(self):
        with pytest.raises(Exception, match="mailbox"):
            EmailEmitterParams.model_validate({
                "collector_url": _DEFAULT_COLLECTOR,
                "target_user": "no-at-sign",
            })

    def test_provider_normalised_to_lowercase(self):
        p = EmailEmitterParams.model_validate({
            "collector_url": _DEFAULT_COLLECTOR, "provider": "PROOFPOINT",
        })
        assert p.provider == "proofpoint"

    def test_event_pattern_normalised_to_lowercase(self):
        p = EmailEmitterParams.model_validate({
            "collector_url": _DEFAULT_COLLECTOR,
            "event_pattern": "BEC_IMPERSONATION",
        })
        assert p.event_pattern == "bec_impersonation"

    def test_iterations_bounds(self):
        with pytest.raises(Exception):
            EmailEmitterParams.model_validate({
                "collector_url": _DEFAULT_COLLECTOR, "iterations": 0,
            })
        with pytest.raises(Exception):
            EmailEmitterParams.model_validate({
                "collector_url": _DEFAULT_COLLECTOR, "iterations": 999,
            })

    def test_burst_count_bounds(self):
        with pytest.raises(Exception):
            EmailEmitterParams.model_validate({
                "collector_url": _DEFAULT_COLLECTOR, "burst_count": 1,
            })
        with pytest.raises(Exception):
            EmailEmitterParams.model_validate({
                "collector_url": _DEFAULT_COLLECTOR, "burst_count": 9999,
            })


# --------------------------------------------------------------------------
# Provider event-shape adapters (unit tests on the builders)
# --------------------------------------------------------------------------


class TestProviderShapes:
    def test_two_providers_registered(self):
        assert _list_providers() == sorted(["proofpoint", "microsoft365"])

    def test_four_event_patterns_registered(self):
        assert _list_event_patterns() == sorted(_EVENT_PATTERNS)

    def test_proofpoint_event_carries_dataset_and_threats(self):
        evt = _PROVIDER_BUILDERS["proofpoint"](
            message_type="messagesBlocked",
            classification="credphish",
            recipient="u@x.invalid",
            indicators=_PATTERN_INDICATORS["phishing_link"],
            sim_run_id="cortexsim-x-i1-aaaa",
        )
        assert evt["dataset"] == "proofpoint_tap_raw"
        assert evt["recipient"] == ["u@x.invalid"]
        assert evt["classification"] == "credphish"
        assert evt["threatsInfoMap"][0]["threatType"] == "url"
        assert evt["cortexsim_run_id"].startswith("cortexsim-")

    def test_m365_event_carries_dataset_and_recipient(self):
        evt = _PROVIDER_BUILDERS["microsoft365"](
            message_type="messagesBlocked",
            classification="malware",
            recipient="u@x.invalid",
            indicators=_PATTERN_INDICATORS["malicious_attachment"],
            sim_run_id="cortexsim-x-i1-aaaa",
        )
        assert evt["dataset"] == "msft_o365"
        assert evt["RecipientEmailAddress"] == "u@x.invalid"
        assert evt["RecordType"] == "EmailEvents"
        assert "Malware" in evt["ThreatTypes"]
        assert evt["SHA256"] == _PATTERN_INDICATORS["malicious_attachment"].attachment_sha256


# --------------------------------------------------------------------------
# Plugin run path — mocked httpx
# --------------------------------------------------------------------------


class TestPluginRun:
    def _run_with_stub(self, monkeypatch, **campaign_kw):
        stub = _RecordingClient()
        monkeypatch.setattr(
            EmailEmitter, "_build_client", lambda self, params: stub,
        )
        campaign = _campaign(**campaign_kw)
        executor = CampaignExecutor(audit=AuditLogger(file_path=None))
        state = _run(executor.execute(campaign))
        return state, stub

    def test_dry_run_does_not_invoke_client(self, monkeypatch):
        def _boom(self, params):  # noqa: ARG001
            raise AssertionError("client should not be built in dry-run")
        monkeypatch.setattr(EmailEmitter, "_build_client", _boom)

        campaign = _campaign(dry_run=True)
        state = _run(CampaignExecutor(audit=AuditLogger(file_path=None)).execute(campaign))
        sr = state.step_results[0]
        assert sr.status == "success"
        assert sr.detail["dry_run"] is True
        assert sr.events_emitted == 1

    def test_phishing_link_posts_block_then_click(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="phishing_link",
        )
        assert state.step_results[0].status == "success"
        assert len(stub.requests) == 2
        bodies = [json.loads(r["content"]) for r in stub.requests]
        types = [b["messageType"] for b in bodies]
        assert types == ["messagesBlocked", "clicksPermitted"]
        assert any(b.get("phishing_link_marker") for b in bodies)
        assert all(b["dataset"] == "proofpoint_tap_raw" for b in bodies)

    def test_malicious_attachment_posts_burst(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="malicious_attachment", burst_count=5,
        )
        bodies = [json.loads(r["content"]) for r in stub.requests]
        assert len(bodies) == 5
        assert all(b.get("malicious_attachment_marker") for b in bodies)
        assert all(b["threatsInfoMap"][0]["threatType"] == "attachment" for b in bodies)

    def test_bec_impersonation_marks_display_name_spoof(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="bec_impersonation", burst_count=3,
        )
        bodies = [json.loads(r["content"]) for r in stub.requests]
        assert len(bodies) == 3
        assert all(b.get("bec_marker") for b in bodies)
        assert all(b["classification"] == "impostor" for b in bodies)

    def test_thread_hijack_posts_delivered_then_click(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="thread_hijack",
        )
        bodies = [json.loads(r["content"]) for r in stub.requests]
        assert len(bodies) == 2
        types = [b["messageType"] for b in bodies]
        assert types == ["messagesDelivered", "clicksPermitted"]
        assert all(b.get("thread_hijack_marker") for b in bodies)

    def test_iterations_multiply_event_count(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="phishing_link", iterations=3,
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
            monkeypatch, event_pattern="phishing_link", iterations=3,
        )
        ids = {r["headers"]["x-simulation-run-id"] for r in stub.requests}
        assert len(ids) == 3, ids

    def test_response_status_counts_recorded(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="phishing_link", iterations=2,
        )
        # Stub returns 202 by default; 2 iters * 2 events = 4.
        assert state.step_results[0].detail["response_status_counts"] == {202: 4}

    def test_request_url_targets_collector(self, monkeypatch):
        state, stub = self._run_with_stub(monkeypatch)
        for r in stub.requests:
            assert r["url"] == _DEFAULT_COLLECTOR

    def test_safety_violation_when_collector_not_allowlisted(self, monkeypatch):
        stub = _RecordingClient()
        monkeypatch.setattr(
            EmailEmitter, "_build_client", lambda self, params: stub,
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
            EmailEmitter, "_build_client", lambda self, params: stub,
        )
        campaign = _campaign(event_pattern="phishing_link", iterations=2)
        state = _run(CampaignExecutor(audit=AuditLogger(file_path=None)).execute(campaign))
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

    def test_microsoft365_provider_event_shape_in_request_body(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, provider="microsoft365", event_pattern="phishing_link",
        )
        bodies = [json.loads(r["content"]) for r in stub.requests]
        for b in bodies:
            assert b["dataset"] == "msft_o365"
            assert "RecipientEmailAddress" in b
            assert b["RecordType"] == "EmailEvents"


# --------------------------------------------------------------------------
# Plugin metadata / registry integration
# --------------------------------------------------------------------------


class TestRegistration:
    def test_plugin_registered_with_default_registry(self):
        from eal_simulator import get_default_registry

        reg = get_default_registry()
        assert reg.has("email_emitter")

    def test_metadata_lists_eal_targets(self):
        from eal_simulator import get_default_registry

        meta = get_default_registry().get("email_emitter").metadata()
        assert any("EMAIL" in t for t in meta["eal_targets"])
        assert "T1566.002" in meta["mitre_techniques"]
        props = meta["params_schema"]["properties"]
        assert "collector_url" in props
        assert "event_pattern" in props

"""Tests for the llm_provider_egress EAL plugin.

We mock ``httpx.AsyncClient`` so no real outbound traffic is generated;
each test asserts the URL/body/header shape the plugin would send and
the audit events it emits. Safety-policy enforcement (host allowlist
authorisation) is tested via the executor end-to-end so the integration
between plugin and policy is covered.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from eal_simulator import AuditLogger, Campaign, CampaignExecutor
from eal_simulator.plugins.llm_provider_egress import (
    LLMProviderEgress,
    LLMProviderEgressParams,
    _PAYLOAD_TEMPLATES,
    _PROVIDERS,
    _list_payload_types,
    _list_providers,
    _render_payload,
)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class _RecordingClient:
    """Stub ``httpx.AsyncClient`` that records each request and returns 401.

    The default 401 mirrors what the real provider would return for our
    bogus auth tokens — the AI Access detection happens at the proxy on
    the *outbound* request, not on the response.
    """

    def __init__(self, status_code: int = 401, raise_exc: Exception | None = None):
        self.requests: list[dict[str, Any]] = []
        self.status_code = status_code
        self.raise_exc = raise_exc
        self.closed = False

    async def request(self, method: str, url: str, *, headers=None, content=None):
        self.requests.append({
            "method": method,
            "url": url,
            "headers": dict(headers or {}),
            "content": content,
        })
        if self.raise_exc is not None:
            raise self.raise_exc
        # Return a faux response object exposing the bits the plugin uses.
        class _R:
            def __init__(self, status_code: int) -> None:
                self.status_code = status_code
        return _R(self.status_code)

    async def aclose(self) -> None:
        self.closed = True


def _campaign(
    *,
    provider: str,
    payload_type: str = "benign",
    iterations: int = 1,
    target_allowlist: list[str] | None = None,
    dry_run: bool = False,
    **extra_params,
) -> Campaign:
    spec = {
        "campaign_id": "CMP-AIACC-INTEG-001",
        "name": "llm_provider_egress test",
        "dry_run": dry_run,
        "steps": [{
            "step_id": "step-01",
            "plugin": "llm_provider_egress",
            "params": {
                "provider": provider,
                "payload_type": payload_type,
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
                "api.openai.com",
                "api.anthropic.com",
                "generativelanguage.googleapis.com",
            ],
        })
    return Campaign.model_validate(spec)


# --------------------------------------------------------------------------
# Param validation
# --------------------------------------------------------------------------


class TestParamValidation:
    def test_provider_required(self):
        with pytest.raises(Exception):
            LLMProviderEgressParams.model_validate({})

    def test_unknown_provider_rejected(self):
        with pytest.raises(Exception, match="provider must be one of"):
            LLMProviderEgressParams.model_validate({"provider": "cohere"})

    def test_unknown_payload_type_rejected(self):
        with pytest.raises(Exception, match="payload_type must be one of"):
            LLMProviderEgressParams.model_validate({
                "provider": "openai", "payload_type": "phishing",
            })

    def test_iterations_bounds(self):
        with pytest.raises(Exception):
            LLMProviderEgressParams.model_validate({
                "provider": "openai", "iterations": 0,
            })
        with pytest.raises(Exception):
            LLMProviderEgressParams.model_validate({
                "provider": "openai", "iterations": 1000,
            })

    def test_paste_padding_kb_bounded(self):
        with pytest.raises(Exception):
            LLMProviderEgressParams.model_validate({
                "provider": "openai", "paste_padding_kb": 4096,
            })

    def test_provider_normalised_to_lowercase(self):
        p = LLMProviderEgressParams.model_validate({"provider": "OpenAI"})
        assert p.provider == "openai"

    def test_default_payload_is_benign(self):
        p = LLMProviderEgressParams.model_validate({"provider": "openai"})
        assert p.payload_type == "benign"
        assert p.iterations == 1


# --------------------------------------------------------------------------
# Provider definitions (URL/body/header shape)
# --------------------------------------------------------------------------


class TestProviderShapes:
    def test_three_providers_registered(self):
        assert _list_providers() == sorted(["openai", "anthropic", "gemini"])

    def test_openai_url_and_headers(self):
        p = _PROVIDERS["openai"]
        url = p.build_url(fake_key=p.fake_key())
        headers = p.build_headers(fake_key=p.fake_key())
        body = p.build_body(prompt="hi")
        assert url == "https://api.openai.com/v1/chat/completions"
        assert headers["authorization"].startswith("Bearer sk-cortexsim-canary")
        assert body["model"] == "gpt-4o"
        assert body["messages"][0]["content"] == "hi"

    def test_anthropic_url_and_headers(self):
        p = _PROVIDERS["anthropic"]
        url = p.build_url(fake_key=p.fake_key())
        headers = p.build_headers(fake_key=p.fake_key())
        body = p.build_body(prompt="hi")
        assert url == "https://api.anthropic.com/v1/messages"
        assert headers["x-api-key"].startswith("sk-ant-cortexsim-canary")
        assert headers["anthropic-version"] == "2023-06-01"
        assert body["model"].startswith("claude-")
        assert body["max_tokens"] == 256

    def test_gemini_key_in_query_param_not_header(self):
        p = _PROVIDERS["gemini"]
        url = p.build_url(fake_key=p.fake_key())
        headers = p.build_headers(fake_key=p.fake_key())
        body = p.build_body(prompt="hi")
        assert "?key=AIzaSy-CORTEXSIM-CANARY" in url
        assert "authorization" not in headers
        assert "x-api-key" not in headers
        assert body["contents"][0]["parts"][0]["text"] == "hi"

    def test_provider_host_extracted(self):
        assert _PROVIDERS["openai"].host() == "api.openai.com"
        assert _PROVIDERS["anthropic"].host() == "api.anthropic.com"
        assert _PROVIDERS["gemini"].host() == "generativelanguage.googleapis.com"


# --------------------------------------------------------------------------
# Payload rendering
# --------------------------------------------------------------------------


class TestPayloadRendering:
    def test_five_payload_types_registered(self):
        assert _list_payload_types() == sorted(
            ["benign", "pii", "secret", "source", "jailbreak"]
        )

    def test_benign_payload_is_safe(self):
        out = _render_payload("benign")
        assert "AKIA" not in out
        assert "DAN" not in out

    def test_pii_payload_carries_canary_marker(self):
        out = _render_payload("pii")
        assert "CORTEXSIMCANARY" in out
        assert "555-00" in out

    def test_secret_payload_includes_aws_canary_key(self):
        out = _render_payload("secret")
        assert "AKIA0000CORTEXSIMCANARY" in out
        assert "DB_CONN" in out

    def test_source_payload_marks_as_proprietary(self):
        out = _render_payload("source")
        assert "CORTEXSIM-CANARY" in out
        assert "settle_transactions" in out

    def test_jailbreak_payload_uses_dan_frame(self):
        out = _render_payload("jailbreak")
        assert "DAN" in out
        assert "Ignore all previous instructions" in out

    def test_padding_inflates_body_size(self):
        plain = _render_payload("benign", paste_padding_kb=0)
        padded = _render_payload("benign", paste_padding_kb=8)
        assert len(padded) > len(plain) + 8000
        assert "lorem ipsum" in padded

    def test_padding_zero_is_passthrough(self):
        assert _render_payload("benign", paste_padding_kb=0) == _PAYLOAD_TEMPLATES["benign"]


# --------------------------------------------------------------------------
# Plugin run path — mocked httpx
# --------------------------------------------------------------------------


class TestPluginRun:
    def _run_with_stub(self, monkeypatch, **campaign_kw):
        """Helper: build a campaign, monkeypatch the plugin's _build_client
        to return our recording stub, run the executor, return (state, stub)."""
        stub = _RecordingClient()
        monkeypatch.setattr(
            LLMProviderEgress, "_build_client", lambda self, params: stub,
        )
        campaign = _campaign(**campaign_kw)
        executor = CampaignExecutor(audit=AuditLogger(file_path=None))
        state = _run(executor.execute(campaign))
        return state, stub

    def test_dry_run_does_not_invoke_client(self, monkeypatch):
        # Make _build_client explode if called — confirms we never construct one.
        def _boom(self, params):  # noqa: ARG001
            raise AssertionError("client should not be built in dry-run")
        monkeypatch.setattr(LLMProviderEgress, "_build_client", _boom)

        campaign = _campaign(provider="openai", dry_run=True)
        state = _run(CampaignExecutor(audit=AuditLogger(file_path=None)).execute(campaign))
        sr = state.step_results[0]
        assert sr.status == "success"
        assert sr.detail["dry_run"] is True
        assert sr.events_emitted == 1

    def test_openai_request_url_and_body(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, provider="openai", payload_type="secret",
        )
        assert state.step_results[0].status == "success"
        assert len(stub.requests) == 1
        req = stub.requests[0]
        assert req["url"] == "https://api.openai.com/v1/chat/completions"
        body = json.loads(req["content"])
        assert body["model"] == "gpt-4o"
        assert "AKIA0000CORTEXSIMCANARY" in body["messages"][0]["content"]

    def test_anthropic_request_url_and_body(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, provider="anthropic", payload_type="pii",
        )
        req = stub.requests[0]
        assert req["url"] == "https://api.anthropic.com/v1/messages"
        body = json.loads(req["content"])
        assert body["model"].startswith("claude-")
        assert "555-00-CORTEXSIMCANARY" in body["messages"][0]["content"]

    def test_gemini_request_uses_query_key(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, provider="gemini", payload_type="jailbreak",
        )
        req = stub.requests[0]
        assert "?key=AIzaSy-CORTEXSIM-CANARY" in req["url"]
        body = json.loads(req["content"])
        assert "DAN" in body["contents"][0]["parts"][0]["text"]

    def test_iterations_send_n_requests(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, provider="openai", iterations=4,
        )
        assert len(stub.requests) == 4
        assert state.step_results[0].detail["iterations_completed"] == 4

    def test_telemetry_headers_injected(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, provider="openai",
        )
        headers = stub.requests[0]["headers"]
        assert "x-simulation-run-id" in headers
        assert headers["x-simulation-run-id"].startswith("cortexsim-")
        # Per-request id includes iteration suffix.
        assert "-i1-" in headers["x-simulation-run-id"]
        assert headers["x-simulation-source"].startswith("cortexsim-eal-simulator")

    def test_per_request_simulation_id_unique_across_iterations(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, provider="openai", iterations=3,
        )
        ids = [r["headers"]["x-simulation-run-id"] for r in stub.requests]
        assert len(set(ids)) == 3, ids

    def test_response_status_counts_recorded(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, provider="openai", iterations=2,
        )
        # Stub returns 401 by default for every request.
        assert state.step_results[0].detail["response_status_counts"] == {401: 2}

    def test_bytes_sent_reflects_body_size(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, provider="openai", payload_type="secret",
        )
        sr = state.step_results[0]
        assert sr.bytes_sent > 100  # secret payload is non-trivial
        assert sr.bytes_sent == len(stub.requests[0]["content"])

    def test_paste_padding_inflates_request_size(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, provider="openai", paste_padding_kb=4,
        )
        body = stub.requests[0]["content"]
        assert len(body) >= 4 * 1024

    def test_safety_violation_when_target_not_allowlisted(self, monkeypatch):
        # Allowlist excludes openai.com — request must be refused.
        stub = _RecordingClient()
        monkeypatch.setattr(
            LLMProviderEgress, "_build_client", lambda self, params: stub,
        )
        campaign = _campaign(
            provider="openai",
            target_allowlist=["api.anthropic.com"],  # only anthropic allowed
        )
        state = _run(CampaignExecutor(audit=AuditLogger(file_path=None)).execute(campaign))
        sr = state.step_results[0]
        assert sr.status == "error"
        assert "safety_violation" in (sr.error or "")
        assert stub.requests == []  # no traffic emitted

    def test_http_error_surfaces_as_failure_event_not_crash(self, monkeypatch):
        stub = _RecordingClient(raise_exc=httpx.ConnectError("boom"))
        monkeypatch.setattr(
            LLMProviderEgress, "_build_client", lambda self, params: stub,
        )
        campaign = _campaign(provider="openai", iterations=2)
        state = _run(CampaignExecutor(audit=AuditLogger(file_path=None)).execute(campaign))
        sr = state.step_results[0]
        # The plugin should still report success (errors are per-request,
        # logged as audit events) — see other plugins for the same idiom.
        assert sr.status == "success"
        assert len(stub.requests) == 2

    def test_user_agent_override_passed_to_client(self, monkeypatch):
        captured: dict = {}

        original = LLMProviderEgress._build_client

        def _capture(self, params):
            captured["user_agent"] = params.user_agent
            return _RecordingClient()

        monkeypatch.setattr(LLMProviderEgress, "_build_client", _capture)
        campaign = _campaign(
            provider="openai", user_agent="curl/7.85.0 CortexSim/1.0",
        )
        _run(CampaignExecutor(audit=AuditLogger(file_path=None)).execute(campaign))
        assert captured["user_agent"] == "curl/7.85.0 CortexSim/1.0"


# --------------------------------------------------------------------------
# Plugin metadata / registry integration
# --------------------------------------------------------------------------


class TestRegistration:
    def test_plugin_registered_with_default_registry(self):
        from eal_simulator import get_default_registry

        reg = get_default_registry()
        assert reg.has("llm_provider_egress")

    def test_metadata_lists_eal_targets(self):
        from eal_simulator import get_default_registry

        meta = get_default_registry().get("llm_provider_egress").metadata()
        assert any("AI Access" in t for t in meta["eal_targets"])
        assert "T1567" in meta["mitre_techniques"]
        # Pydantic schema must surface the configurable fields.
        props = meta["params_schema"]["properties"]
        assert "provider" in props and "payload_type" in props

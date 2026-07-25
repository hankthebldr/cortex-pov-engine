"""Tests for the azure_audit_emitter EAL plugin (built on analytics_emitter).

We stub ``httpx.AsyncClient`` so no real outbound traffic is generated; each
test asserts the JSON shape posted to the collector, the transport toggles
(batch / gzip / auth), the dry-run short-circuit and registration. Safety-policy
enforcement (host allowlist authorisation) is exercised end-to-end through the
executor. No network is ever touched.
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
from eal_simulator.plugins.azure_audit_emitter import (
    AzureAuditEmitter,
    AzureAuditEmitterParams,
    _DATASET,
    _EVENT_PATTERNS,
    _PATTERN_ACTIVITY,
    _UNKNOWN_TENANT,
    _azure_activity_event,
    _list_event_patterns,
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
    event_pattern: str = "service_principal_token_remote_use",
    iterations: int = 1,
    burst_count: int = 8,
    collector_url: str = _DEFAULT_COLLECTOR,
    target_allowlist: list[str] | None = None,
    dry_run: bool = False,
    **extra_params,
) -> Campaign:
    spec = {
        "campaign_id": "CMP-AZUREAUDIT-INTEG-001",
        "name": "azure_audit_emitter test",
        "dry_run": dry_run,
        "steps": [{
            "step_id": "step-01",
            "plugin": "azure_audit_emitter",
            "params": {
                "collector_url": collector_url,
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
# Param validation (shared base fields + subclass field)
# --------------------------------------------------------------------------


class TestParamValidation:
    def test_collector_url_required(self):
        with pytest.raises(Exception):
            AzureAuditEmitterParams.model_validate({})

    def test_collector_url_must_be_http_or_https(self):
        with pytest.raises(Exception, match="http or https"):
            AzureAuditEmitterParams.model_validate({
                "collector_url": "ftp://x.invalid/cb",
            })

    def test_unknown_event_pattern_rejected(self):
        with pytest.raises(Exception, match="event_pattern must be one of"):
            AzureAuditEmitterParams.model_validate({
                "collector_url": _DEFAULT_COLLECTOR,
                "event_pattern": "coin_mining",
            })

    def test_event_pattern_normalised_to_lowercase(self):
        p = AzureAuditEmitterParams.model_validate({
            "collector_url": _DEFAULT_COLLECTOR,
            "event_pattern": "KEY_VAULT_MODIFIED",
        })
        assert p.event_pattern == "key_vault_modified"

    def test_dataset_is_msft_azure_audit(self):
        p = AzureAuditEmitterParams.model_validate({
            "collector_url": _DEFAULT_COLLECTOR,
        })
        assert p.dataset == "msft_azure_audit"

    def test_auth_defaults_and_transport_toggles(self):
        p = AzureAuditEmitterParams.model_validate({
            "collector_url": _DEFAULT_COLLECTOR,
        })
        assert p.auth_token is None
        assert p.auth_header == "Authorization"
        assert p.auth_scheme == "Bearer"
        assert p.compress is False
        assert p.batch is False

    def test_auth_token_is_secret_and_not_reprd(self):
        p = AzureAuditEmitterParams.model_validate({
            "collector_url": _DEFAULT_COLLECTOR,
            "auth_token": "super-secret-hec-token",
        })
        assert "super-secret-hec-token" not in repr(p)
        assert "super-secret-hec-token" not in str(p)
        assert "super-secret-hec-token" not in p.model_dump_json()
        assert p.auth_token.get_secret_value() == "super-secret-hec-token"

    def test_base_params_shared_fields_present(self):
        fields = AzureAuditEmitterParams.model_fields
        for name in AnalyticsEmitterParams.model_fields:
            assert name in fields


# --------------------------------------------------------------------------
# Record shape
# --------------------------------------------------------------------------


class TestRecordShape:
    def test_six_event_patterns_registered(self):
        assert _list_event_patterns() == sorted(_EVENT_PATTERNS)
        assert len(_EVENT_PATTERNS) == 6

    def test_activity_record_shape(self):
        marker, ops = _PATTERN_ACTIVITY["key_vault_modified"]
        evt = _azure_activity_event(
            op=ops[0], marker=marker, sim_run_id="cortexsim-x-i1-aa",
        )
        assert evt["dataset"] == _DATASET
        assert evt["operationName"] == "MICROSOFT.KEYVAULT/VAULTS/WRITE"
        assert evt["category"] == "Administrative"
        assert evt["resultType"] == "Success"
        assert evt["callerIpAddress"] == "203.0.113.201"
        assert "claims" in evt["identity"]
        assert evt[marker] is True
        assert evt["cortexsim_run_id"] == "cortexsim-x-i1-aa"

    def test_service_principal_token_uses_app_only_claims(self):
        marker, ops = _PATTERN_ACTIVITY["service_principal_token_remote_use"]
        evt = _azure_activity_event(op=ops[0], marker=marker, sim_run_id="x")
        claims = evt["identity"]["claims"]
        # app-only (service principal) token indicator
        assert claims["idtyp"] == "app"
        assert claims["appidacr"] == "2"

    def test_unknown_tenant_uses_foreign_tenant_claim(self):
        marker, ops = _PATTERN_ACTIVITY["unknown_tenant_app_access"]
        evt = _azure_activity_event(op=ops[0], marker=marker, sim_run_id="x")
        claims = evt["identity"]["claims"]
        assert claims["http://schemas.microsoft.com/identity/claims/tenantid"] == _UNKNOWN_TENANT
        assert evt["resultType"] == "Failure"


# --------------------------------------------------------------------------
# Plugin run path — stubbed httpx (no network)
# --------------------------------------------------------------------------


class TestPluginRun:
    def _run_with_stub(self, monkeypatch, *, status_code=202, raise_exc=None, **campaign_kw):
        stub = _RecordingClient(status_code=status_code, raise_exc=raise_exc)
        monkeypatch.setattr(
            AzureAuditEmitter, "_build_client", lambda self, params: stub,
        )
        campaign = _campaign(**campaign_kw)
        executor = CampaignExecutor(audit=AuditLogger(file_path=None))
        state = _run(executor.execute(campaign))
        return state, stub

    def test_dry_run_does_not_invoke_client(self, monkeypatch):
        def _boom(self, params):  # noqa: ARG001
            raise AssertionError("client should not be built in dry-run")
        monkeypatch.setattr(AzureAuditEmitter, "_build_client", _boom)

        campaign = _campaign(dry_run=True)
        state = _run(CampaignExecutor(audit=AuditLogger(file_path=None)).execute(campaign))
        sr = state.step_results[0]
        assert sr.status == "success"
        assert sr.detail["dry_run"] is True
        assert sr.events_emitted == 1
        assert sr.bytes_sent == 0

    def test_service_principal_pattern_posts_two_records(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="service_principal_token_remote_use",
        )
        assert state.step_results[0].status == "success"
        assert len(stub.requests) == 2
        bodies = _bodies(stub)
        names = [b["operationName"] for b in bodies]
        assert names == [
            "MICROSOFT.RESOURCES/SUBSCRIPTIONS/RESOURCEGROUPS/READ",
            "MICROSOFT.STORAGE/STORAGEACCOUNTS/LISTKEYS/ACTION",
        ]
        assert all(b.get("sp_token_remote_use_marker") for b in bodies)
        assert all(b["dataset"] == _DATASET for b in bodies)

    def test_unknown_tenant_single_failed_record(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="unknown_tenant_app_access",
        )
        bodies = _bodies(stub)
        assert len(bodies) == 1
        assert bodies[0]["resultType"] == "Failure"
        assert bodies[0].get("unknown_tenant_access_marker") is True

    def test_unusual_app_resource_access_bursts(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="unusual_app_resource_access", burst_count=5,
        )
        bodies = _bodies(stub)
        assert len(bodies) == 5
        assert all(b.get("unusual_app_resource_access_marker") for b in bodies)
        # Each read targets a distinct resource.
        indexes = sorted(b["accessed_resource_index"] for b in bodies)
        assert indexes == [0, 1, 2, 3, 4]

    def test_key_vault_modified_posts_two_records(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="key_vault_modified",
        )
        bodies = _bodies(stub)
        names = [b["operationName"] for b in bodies]
        assert names == [
            "MICROSOFT.KEYVAULT/VAULTS/WRITE",
            "MICROSOFT.KEYVAULT/VAULTS/ACCESSPOLICIES/WRITE",
        ]
        assert all(b.get("key_vault_modified_marker") for b in bodies)

    def test_app_credentials_added_single_record(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="app_credentials_added",
        )
        bodies = _bodies(stub)
        assert len(bodies) == 1
        assert bodies[0]["operationName"] == "MICROSOFT.AADIAM/APPLICATIONS/CREDENTIALS/WRITE"
        assert bodies[0].get("app_credentials_added_marker") is True

    def test_conditional_access_change_single_record(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="conditional_access_policy_change",
        )
        bodies = _bodies(stub)
        assert len(bodies) == 1
        assert bodies[0]["operationName"] == "MICROSOFT.AADIAM/CONDITIONALACCESS/POLICIES/WRITE"
        assert bodies[0].get("conditional_access_change_marker") is True

    def test_iterations_multiply_record_count(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="key_vault_modified", iterations=3,
        )
        assert len(stub.requests) == 6  # 2 records * 3 iterations
        assert state.step_results[0].detail["events_posted"] == 6

    # -- transport toggles ------------------------------------------------

    def test_batch_mode_single_ndjson_post(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="key_vault_modified", batch=True,
        )
        assert len(stub.requests) == 1
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
            monkeypatch, event_pattern="app_credentials_added", compress=True,
        )
        req = stub.requests[0]
        assert req["headers"]["content-encoding"] == "gzip"
        assert req["content"][:2] == b"\x1f\x8b"
        body = _bodies(stub)[0]
        assert body["operationName"] == "MICROSOFT.AADIAM/APPLICATIONS/CREDENTIALS/WRITE"

    # -- telemetry / identity --------------------------------------------

    def test_telemetry_headers_injected_lowercase(self, monkeypatch):
        state, stub = self._run_with_stub(monkeypatch)
        headers = stub.requests[0]["headers"]
        assert "x-simulation-run-id" in headers
        assert headers["x-simulation-run-id"].startswith("cortexsim-")
        assert headers["x-simulation-source"].startswith("cortexsim-eal-simulator")
        assert headers["content-type"] == "application/json"

    def test_request_url_targets_collector(self, monkeypatch):
        state, stub = self._run_with_stub(monkeypatch)
        for r in stub.requests:
            assert r["url"] == _DEFAULT_COLLECTOR

    # -- safety + resilience ---------------------------------------------

    def test_safety_violation_when_collector_not_allowlisted(self, monkeypatch):
        stub = _RecordingClient()
        monkeypatch.setattr(
            AzureAuditEmitter, "_build_client", lambda self, params: stub,
        )
        campaign = _campaign(target_allowlist=["other.invalid"])
        state = _run(CampaignExecutor(audit=AuditLogger(file_path=None)).execute(campaign))
        sr = state.step_results[0]
        assert sr.status == "error"
        assert "safety_violation" in (sr.error or "")
        assert stub.requests == []

    def test_http_error_does_not_crash_iteration(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="key_vault_modified", iterations=2,
            raise_exc=httpx.ConnectError("boom"),
        )
        sr = state.step_results[0]
        assert sr.status == "success"
        assert len(stub.requests) == 4  # all attempted despite errors
        assert sr.detail["response_status_counts"].get(0, 0) == 4
        assert sr.detail["events_posted"] == 0


# --------------------------------------------------------------------------
# Injectable transport — auth header (real _build_client)
# --------------------------------------------------------------------------


class TestAuthHeader:
    def test_no_auth_header_when_token_absent(self):
        emitter = AzureAuditEmitter()
        emitter._verify_tls = False
        params = AzureAuditEmitterParams.model_validate({
            "collector_url": _DEFAULT_COLLECTOR,
        })
        client = emitter._build_client(params)
        try:
            assert "authorization" not in {k.lower() for k in client.headers}
        finally:
            _run(client.aclose())

    def test_bearer_auth_header_set_from_token(self):
        emitter = AzureAuditEmitter()
        emitter._verify_tls = False
        params = AzureAuditEmitterParams.model_validate({
            "collector_url": _DEFAULT_COLLECTOR,
            "auth_token": "tok-123",
        })
        client = emitter._build_client(params)
        try:
            assert client.headers["authorization"] == "Bearer tok-123"
        finally:
            _run(client.aclose())


# --------------------------------------------------------------------------
# Registry / metadata
# --------------------------------------------------------------------------


class TestRegistration:
    def test_plugin_registered_with_default_registry(self):
        from eal_simulator import get_default_registry

        reg = get_default_registry()
        assert reg.has("azure_audit_emitter")

    def test_metadata_lists_analytics_and_abioc_targets(self):
        from eal_simulator import get_default_registry

        meta = get_default_registry().get("azure_audit_emitter").metadata()
        assert any("Analytics" in t for t in meta["eal_targets"])
        assert any("ABIOC" in t for t in meta["eal_targets"])
        assert "T1098.001" in meta["mitre_techniques"]
        props = meta["params_schema"]["properties"]
        assert "collector_url" in props
        assert "event_pattern" in props
        assert "auth_token" in props
        assert "compress" in props
        assert "batch" in props

    def test_not_c2_classified(self):
        # Azure control-plane / identity techniques must NOT trip the C2 gate.
        from eal_simulator.safety import is_c2_plugin

        assert is_c2_plugin(
            "azure_audit_emitter", AzureAuditEmitter.Meta.mitre_techniques,
        ) is False

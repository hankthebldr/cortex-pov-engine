"""Tests for the ad_windows_emitter EAL plugin (Active Directory / Windows
Security data source, msft_windows_security dataset).

We stub ``httpx.AsyncClient`` so no real outbound traffic is generated; each
test asserts the shape-true Windows Security Event Log records posted to the
collector, the shared transport toggles (batch / gzip / auth) inherited from
the analytics_emitter spine, and the audit events emitted. Safety-policy
enforcement (host allowlist authorisation) is exercised end-to-end through the
executor. The dry-run path proves no network is touched.
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
from eal_simulator.plugins.ad_windows_emitter import (
    ADWindowsEmitter,
    ADWindowsEmitterParams,
    _DATASET,
    _DCSYNC_PROPERTY_GUIDS,
    _EVENT_PATTERNS,
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
    event_pattern: str = "kerberoast_weak_ticket",
    iterations: int = 1,
    burst_count: int = 8,
    collector_url: str = _DEFAULT_COLLECTOR,
    target_allowlist: list[str] | None = None,
    dry_run: bool = False,
    **extra_params,
) -> Campaign:
    spec = {
        "campaign_id": "CMP-ADWIN-INTEG-001",
        "name": "ad_windows_emitter test",
        "dry_run": dry_run,
        "steps": [{
            "step_id": "step-01",
            "plugin": "ad_windows_emitter",
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
# Param validation (shared base fields + subclass fields)
# --------------------------------------------------------------------------


class TestParamValidation:
    def test_collector_url_required(self):
        with pytest.raises(Exception):
            ADWindowsEmitterParams.model_validate({})

    def test_collector_url_must_be_http_or_https(self):
        with pytest.raises(Exception, match="http or https"):
            ADWindowsEmitterParams.model_validate({
                "collector_url": "ftp://x.invalid/cb",
            })

    def test_unknown_event_pattern_rejected(self):
        with pytest.raises(Exception, match="event_pattern must be one of"):
            ADWindowsEmitterParams.model_validate({
                "collector_url": _DEFAULT_COLLECTOR,
                "event_pattern": "golden_ticket",
            })

    def test_event_pattern_normalised_to_lowercase(self):
        p = ADWindowsEmitterParams.model_validate({
            "collector_url": _DEFAULT_COLLECTOR,
            "event_pattern": "DCSYNC",
        })
        assert p.event_pattern == "dcsync"

    def test_dataset_is_msft_windows_security(self):
        p = ADWindowsEmitterParams.model_validate({
            "collector_url": _DEFAULT_COLLECTOR,
        })
        assert p.dataset == "msft_windows_security"

    def test_iterations_bounds(self):
        with pytest.raises(Exception):
            ADWindowsEmitterParams.model_validate({
                "collector_url": _DEFAULT_COLLECTOR, "iterations": 0,
            })
        with pytest.raises(Exception):
            ADWindowsEmitterParams.model_validate({
                "collector_url": _DEFAULT_COLLECTOR, "iterations": 999,
            })

    def test_auth_defaults_and_transport_toggles(self):
        p = ADWindowsEmitterParams.model_validate({
            "collector_url": _DEFAULT_COLLECTOR,
        })
        assert p.auth_token is None
        assert p.auth_header == "Authorization"
        assert p.auth_scheme == "Bearer"
        assert p.compress is False
        assert p.batch is False

    def test_auth_token_is_secret_and_not_reprd(self):
        p = ADWindowsEmitterParams.model_validate({
            "collector_url": _DEFAULT_COLLECTOR,
            "auth_token": "super-secret-hec-token",
        })
        assert "super-secret-hec-token" not in repr(p)
        assert "super-secret-hec-token" not in str(p)
        assert "super-secret-hec-token" not in p.model_dump_json()
        assert p.auth_token.get_secret_value() == "super-secret-hec-token"

    def test_base_params_shared_fields_present(self):
        fields = ADWindowsEmitterParams.model_fields
        for name in AnalyticsEmitterParams.model_fields:
            assert name in fields


# --------------------------------------------------------------------------
# Event-shape correctness
# --------------------------------------------------------------------------


class TestEventShapes:
    def test_six_event_patterns_registered(self):
        assert _list_event_patterns() == sorted(_EVENT_PATTERNS)
        assert len(_EVENT_PATTERNS) == 6

    def test_build_events_carry_dataset_and_run_id(self):
        emitter = ADWindowsEmitter()
        params = ADWindowsEmitterParams.model_validate({
            "collector_url": _DEFAULT_COLLECTOR,
            "event_pattern": "kerberoast_weak_ticket",
        })
        events = emitter.build_events(params, sim_run_id="cortexsim-x-i1-aa", iteration=1)
        assert events
        for e in events:
            assert e["dataset"] == _DATASET
            assert e["cortexsim_run_id"] == "cortexsim-x-i1-aa"
            assert e["Channel"] == "Security"


# --------------------------------------------------------------------------
# Plugin run path — stubbed httpx (no network)
# --------------------------------------------------------------------------


class TestPluginRun:
    def _run_with_stub(self, monkeypatch, *, status_code=202, raise_exc=None, **campaign_kw):
        stub = _RecordingClient(status_code=status_code, raise_exc=raise_exc)
        monkeypatch.setattr(
            ADWindowsEmitter, "_build_client", lambda self, params: stub,
        )
        campaign = _campaign(**campaign_kw)
        executor = CampaignExecutor(audit=AuditLogger(file_path=None))
        state = _run(executor.execute(campaign))
        return state, stub

    def test_dry_run_does_not_invoke_client(self, monkeypatch):
        def _boom(self, params):  # noqa: ARG001
            raise AssertionError("client should not be built in dry-run")
        monkeypatch.setattr(ADWindowsEmitter, "_build_client", _boom)

        campaign = _campaign(dry_run=True)
        state = _run(CampaignExecutor(audit=AuditLogger(file_path=None)).execute(campaign))
        sr = state.step_results[0]
        assert sr.status == "success"
        assert sr.detail["dry_run"] is True
        assert sr.events_emitted == 1
        assert sr.bytes_sent == 0

    def test_kerberoast_weak_ticket_all_rc4(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="kerberoast_weak_ticket", burst_count=6,
        )
        assert state.step_results[0].status == "success"
        bodies = _bodies(stub)
        assert len(bodies) == 6
        for b in bodies:
            assert b["EventID"] == 4769
            assert b["EventData"]["TicketEncryptionType"] == "0x17"
            assert b.get("weak_service_ticket_marker") is True
            assert b["weak_encryption"] is True

    def test_service_ticket_volume_mixes_encryption(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="service_ticket_volume", burst_count=6,
        )
        bodies = _bodies(stub)
        assert len(bodies) == 6
        assert all(b["EventID"] == 4769 for b in bodies)
        assert all(b.get("service_ticket_volume_marker") for b in bodies)
        enc_types = {b["EventData"]["TicketEncryptionType"] for b in bodies}
        # Volume alert fires on request count, so a strong-crypto ticket is mixed in.
        assert "0x12" in enc_types

    def test_pass_the_hash_ntlm_network_logon(self, monkeypatch):
        state, stub = self._run_with_stub(monkeypatch, event_pattern="pass_the_hash")
        bodies = _bodies(stub)
        assert len(bodies) == 1
        b = bodies[0]
        assert b["EventID"] == 4624
        assert b["EventData"]["LogonType"] == 3
        assert b["EventData"]["AuthenticationPackageName"] == "NTLM"
        assert b["EventData"]["LogonProcessName"].strip() == "NtLmSsp"
        assert b.get("pass_the_hash_marker") is True

    def test_ldap_enumeration_bursts_4662(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="ldap_enumeration", burst_count=5,
        )
        bodies = _bodies(stub)
        assert len(bodies) == 5
        for b in bodies:
            assert b["EventID"] == 4662
            assert b["EventData"]["ObjectServer"] == "DS"
            assert b.get("ldap_enumeration_marker") is True

    def test_dcsync_carries_replication_guids_from_non_dc(self, monkeypatch):
        state, stub = self._run_with_stub(monkeypatch, event_pattern="dcsync")
        bodies = _bodies(stub)
        assert len(bodies) == 1
        b = bodies[0]
        assert b["EventID"] == 4662
        props = b["EventData"]["Properties"]
        for guid in _DCSYNC_PROPERTY_GUIDS:
            assert guid in props
        assert b["replication_from_non_dc"] is True
        assert b.get("dcsync_marker") is True

    def test_dc_login_anomaly_remote_interactive(self, monkeypatch):
        state, stub = self._run_with_stub(monkeypatch, event_pattern="dc_login_anomaly")
        bodies = _bodies(stub)
        assert len(bodies) == 1
        b = bodies[0]
        assert b["EventID"] == 4624
        assert b["EventData"]["LogonType"] == 10
        assert b["logon_to_domain_controller"] is True
        assert b.get("dc_login_anomaly_marker") is True

    def test_iterations_multiply_record_count(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="pass_the_hash", iterations=3,
        )
        assert len(stub.requests) == 3
        assert state.step_results[0].detail["events_posted"] == 3

    def test_target_account_flows_into_records(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="pass_the_hash", target_account="rogue-admin",
        )
        b = _bodies(stub)[0]
        assert b["EventData"]["TargetUserName"] == "rogue-admin"

    # -- transport toggles ------------------------------------------------

    def test_batch_mode_single_ndjson_post(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="ldap_enumeration", burst_count=4, batch=True,
        )
        assert len(stub.requests) == 1
        req = stub.requests[0]
        assert req["headers"]["content-type"] == "application/x-ndjson"
        records = _bodies(stub, batch=True)
        assert len(records) == 4
        detail = state.step_results[0].detail
        assert detail["events_posted"] == 4
        assert detail["response_status_counts"] == {202: 1}
        assert detail["batch"] is True

    def test_compress_mode_gzips_body(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="dcsync", compress=True,
        )
        req = stub.requests[0]
        assert req["headers"]["content-encoding"] == "gzip"
        assert req["content"][:2] == b"\x1f\x8b"
        body = _bodies(stub)[0]
        assert body["EventID"] == 4662

    # -- telemetry / identity --------------------------------------------

    def test_telemetry_headers_injected_lowercase(self, monkeypatch):
        state, stub = self._run_with_stub(monkeypatch, event_pattern="pass_the_hash")
        headers = stub.requests[0]["headers"]
        assert "x-simulation-run-id" in headers
        assert headers["x-simulation-run-id"].startswith("cortexsim-")
        assert headers["x-simulation-source"].startswith("cortexsim-eal-simulator")
        assert headers["content-type"] == "application/json"

    def test_request_url_targets_collector(self, monkeypatch):
        state, stub = self._run_with_stub(monkeypatch, event_pattern="pass_the_hash")
        for r in stub.requests:
            assert r["url"] == _DEFAULT_COLLECTOR

    # -- safety + resilience ---------------------------------------------

    def test_safety_violation_when_collector_not_allowlisted(self, monkeypatch):
        stub = _RecordingClient()
        monkeypatch.setattr(
            ADWindowsEmitter, "_build_client", lambda self, params: stub,
        )
        campaign = _campaign(target_allowlist=["other.invalid"])
        state = _run(CampaignExecutor(audit=AuditLogger(file_path=None)).execute(campaign))
        sr = state.step_results[0]
        assert sr.status == "error"
        assert "safety_violation" in (sr.error or "")
        assert stub.requests == []

    def test_http_error_does_not_crash_iteration(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="ldap_enumeration", burst_count=3,
            raise_exc=httpx.ConnectError("boom"),
        )
        sr = state.step_results[0]
        assert sr.status == "success"
        assert len(stub.requests) == 3  # all attempted despite errors
        assert sr.detail["events_posted"] == 0


# --------------------------------------------------------------------------
# Injectable transport — auth header (exercised on the real _build_client)
# --------------------------------------------------------------------------


class TestAuthHeader:
    def test_no_auth_header_when_token_absent(self):
        emitter = ADWindowsEmitter()
        emitter._verify_tls = False
        params = ADWindowsEmitterParams.model_validate({
            "collector_url": _DEFAULT_COLLECTOR,
        })
        client = emitter._build_client(params)
        try:
            assert "authorization" not in {k.lower() for k in client.headers}
        finally:
            _run(client.aclose())

    def test_bearer_auth_header_set_from_token(self):
        emitter = ADWindowsEmitter()
        emitter._verify_tls = False
        params = ADWindowsEmitterParams.model_validate({
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
        assert reg.has("ad_windows_emitter")

    def test_metadata_lists_analytics_and_abioc_targets(self):
        from eal_simulator import get_default_registry

        meta = get_default_registry().get("ad_windows_emitter").metadata()
        assert any("Analytics" in t for t in meta["eal_targets"])
        assert any("ABIOC" in t for t in meta["eal_targets"])
        assert "T1558.003" in meta["mitre_techniques"]
        props = meta["params_schema"]["properties"]
        assert "collector_url" in props
        assert "event_pattern" in props
        assert "auth_token" in props
        assert "compress" in props
        assert "batch" in props

    def test_not_c2_classified(self):
        # AD credential-access techniques must NOT trip the C2 gate — only
        # simulation_authorized is required, not c2_authorized.
        from eal_simulator.safety import is_c2_plugin

        assert is_c2_plugin(
            "ad_windows_emitter", ADWindowsEmitter.Meta.mitre_techniques,
        ) is False

"""Tests for the m365_activity_emitter EAL plugin (Microsoft 365 / Exchange
data-plane workloads -> ``msft_o365_audit``).

We stub ``httpx.AsyncClient`` so no real outbound traffic is generated; each
test asserts the JSON shape posted to the collector, the transport toggles
(batch / gzip / auth), the dry-run short-circuit, safety-policy enforcement,
and registration. The shared transport driver lives in ``analytics_emitter``
(proven by the cloud_audit_emitter suite); here we focus on this source's
shape-true M365 unified-audit records + its own param validation.
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
from eal_simulator.plugins.m365_activity_emitter import (
    M365ActivityEmitter,
    M365ActivityEmitterParams,
    _DATASET,
    _EVENT_PATTERNS,
    _list_event_patterns,
    _m365_audit_event,
    _FILE_DOWNLOADED,
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
    event_pattern: str = "storage_exfiltration",
    iterations: int = 1,
    burst_count: int = 8,
    collector_url: str = _DEFAULT_COLLECTOR,
    target_allowlist: list[str] | None = None,
    dry_run: bool = False,
    **extra_params,
) -> Campaign:
    spec = {
        "campaign_id": "CMP-M365-INTEG-001",
        "name": "m365_activity_emitter test",
        "dry_run": dry_run,
        "steps": [{
            "step_id": "step-01",
            "plugin": "m365_activity_emitter",
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
            M365ActivityEmitterParams.model_validate({})

    def test_collector_url_must_be_http_or_https(self):
        with pytest.raises(Exception, match="http or https"):
            M365ActivityEmitterParams.model_validate({
                "collector_url": "ftp://x.invalid/cb",
            })

    def test_unknown_event_pattern_rejected(self):
        with pytest.raises(Exception, match="event_pattern must be one of"):
            M365ActivityEmitterParams.model_validate({
                "collector_url": _DEFAULT_COLLECTOR,
                "event_pattern": "teams_call",
            })

    def test_event_pattern_normalised_to_lowercase(self):
        p = M365ActivityEmitterParams.model_validate({
            "collector_url": _DEFAULT_COLLECTOR,
            "event_pattern": "MAILBOX_RULE_CREATION",
        })
        assert p.event_pattern == "mailbox_rule_creation"

    def test_target_user_must_be_mailbox(self):
        with pytest.raises(Exception, match="mailbox"):
            M365ActivityEmitterParams.model_validate({
                "collector_url": _DEFAULT_COLLECTOR,
                "target_user": "not-an-address",
            })

    def test_dataset_is_msft_o365_audit(self):
        p = M365ActivityEmitterParams.model_validate({
            "collector_url": _DEFAULT_COLLECTOR,
        })
        assert p.dataset == "msft_o365_audit"

    def test_auth_defaults_and_transport_toggles(self):
        p = M365ActivityEmitterParams.model_validate({
            "collector_url": _DEFAULT_COLLECTOR,
        })
        assert p.auth_token is None
        assert p.auth_header == "Authorization"
        assert p.auth_scheme == "Bearer"
        assert p.compress is False
        assert p.batch is False

    def test_auth_token_is_secret_and_not_reprd(self):
        p = M365ActivityEmitterParams.model_validate({
            "collector_url": _DEFAULT_COLLECTOR,
            "auth_token": "super-secret-hec-token",
        })
        assert "super-secret-hec-token" not in repr(p)
        assert "super-secret-hec-token" not in str(p)
        assert "super-secret-hec-token" not in p.model_dump_json()
        assert p.auth_token.get_secret_value() == "super-secret-hec-token"

    def test_base_params_shared_fields_present(self):
        fields = M365ActivityEmitterParams.model_fields
        for name in AnalyticsEmitterParams.model_fields:
            assert name in fields


# --------------------------------------------------------------------------
# Event-shape adapter
# --------------------------------------------------------------------------


class TestRecordShape:
    def test_six_event_patterns_registered(self):
        assert _list_event_patterns() == sorted(_EVENT_PATTERNS)
        assert len(_EVENT_PATTERNS) == 6

    def test_audit_record_shape(self):
        evt = _m365_audit_event(
            activity=_FILE_DOWNLOADED,
            marker="storage_exfiltration_marker",
            sim_run_id="cortexsim-x-i1-aa",
            object_id="https://x-my.sharepoint.invalid/personal/ada/Documents/Q4.xlsx",
        )
        assert evt["dataset"] == _DATASET
        assert evt["Operation"] == "FileDownloaded"
        assert evt["RecordType"] == 6
        assert evt["Workload"] == "OneDrive"
        assert evt["storage_exfiltration_marker"] is True
        assert evt["cortexsim_run_id"] == "cortexsim-x-i1-aa"


# --------------------------------------------------------------------------
# Plugin run path — stubbed httpx (no network)
# --------------------------------------------------------------------------


class TestPluginRun:
    def _run_with_stub(self, monkeypatch, *, status_code=202, raise_exc=None, **campaign_kw):
        stub = _RecordingClient(status_code=status_code, raise_exc=raise_exc)
        monkeypatch.setattr(
            M365ActivityEmitter, "_build_client", lambda self, params: stub,
        )
        campaign = _campaign(**campaign_kw)
        executor = CampaignExecutor(audit=AuditLogger(file_path=None))
        state = _run(executor.execute(campaign))
        return state, stub

    def test_dry_run_does_not_invoke_client(self, monkeypatch):
        def _boom(self, params):  # noqa: ARG001
            raise AssertionError("client should not be built in dry-run")
        monkeypatch.setattr(M365ActivityEmitter, "_build_client", _boom)

        campaign = _campaign(dry_run=True)
        state = _run(CampaignExecutor(audit=AuditLogger(file_path=None)).execute(campaign))
        sr = state.step_results[0]
        assert sr.status == "success"
        assert sr.detail["dry_run"] is True
        assert sr.events_emitted == 1
        assert sr.bytes_sent == 0

    def test_storage_exfiltration_bursts_downloads(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="storage_exfiltration", burst_count=6,
        )
        assert state.step_results[0].status == "success"
        bodies = _bodies(stub)
        assert len(bodies) == 6
        assert all(b["dataset"] == _DATASET for b in bodies)
        assert all(b.get("storage_exfiltration_marker") for b in bodies)
        ops = {b["Operation"] for b in bodies}
        assert ops <= {"FileDownloaded", "FileSyncDownloadedFull"}

    def test_mailbox_enumeration_by_app_carries_appid(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="mailbox_enumeration_by_app", burst_count=4,
        )
        bodies = _bodies(stub)
        assert len(bodies) == 4
        for b in bodies:
            assert b["Operation"] == "MailItemsAccessed"
            assert b["RecordType"] == 50
            assert b["AppId"]
            assert b.get("mailbox_enumeration_marker") is True

    def test_sharepoint_enumeration_emits_search_and_page(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="sharepoint_enumeration", burst_count=3,
        )
        bodies = _bodies(stub)
        # burst_count * (1 search + 1 page)
        assert len(bodies) == 6
        ops = [b["Operation"] for b in bodies]
        assert ops.count("SearchQueryPerformed") == 3
        assert ops.count("PageViewed") == 3
        assert all(b["Workload"] == "SharePoint" for b in bodies)

    def test_mailbox_rule_creation_single_hide_forward_rule(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="mailbox_rule_creation",
        )
        bodies = _bodies(stub)
        assert len(bodies) == 1
        rule = bodies[0]
        assert rule["Operation"] == "New-InboxRule"
        assert rule["RecordType"] == 1
        assert rule.get("hide_rule_marker") is True
        assert rule.get("forwarding_rule_marker") is True
        assert any(p["Name"] == "ForwardTo" for p in rule["Parameters"])

    def test_onedrive_enumeration_workload(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="onedrive_enumeration", burst_count=5,
        )
        bodies = _bodies(stub)
        assert len(bodies) == 5
        assert all(b["Operation"] == "FileAccessed" for b in bodies)
        assert all(b["Workload"] == "OneDrive" for b in bodies)

    def test_iterations_multiply_record_count(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="mailbox_rule_creation", iterations=3,
        )
        assert len(stub.requests) == 3
        assert state.step_results[0].detail["events_posted"] == 3

    # -- transport toggles ------------------------------------------------

    def test_batch_mode_single_ndjson_post(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="onedrive_enumeration", burst_count=4, batch=True,
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
            monkeypatch, event_pattern="mailbox_rule_creation", compress=True,
        )
        req = stub.requests[0]
        assert req["headers"]["content-encoding"] == "gzip"
        assert req["content"][:2] == b"\x1f\x8b"
        assert _bodies(stub)[0]["Operation"] == "New-InboxRule"

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
            M365ActivityEmitter, "_build_client", lambda self, params: stub,
        )
        campaign = _campaign(target_allowlist=["other.invalid"])
        state = _run(CampaignExecutor(audit=AuditLogger(file_path=None)).execute(campaign))
        sr = state.step_results[0]
        assert sr.status == "error"
        assert "safety_violation" in (sr.error or "")
        assert stub.requests == []

    def test_http_error_does_not_crash_iteration(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="onedrive_enumeration", burst_count=3,
            raise_exc=httpx.ConnectError("boom"),
        )
        sr = state.step_results[0]
        assert sr.status == "success"
        assert len(stub.requests) == 3
        assert sr.detail["response_status_counts"].get(0, 0) == 3
        assert sr.detail["events_posted"] == 0


# --------------------------------------------------------------------------
# Registry / metadata
# --------------------------------------------------------------------------


class TestRegistration:
    def test_plugin_registered_with_default_registry(self):
        from eal_simulator import get_default_registry

        reg = get_default_registry()
        assert reg.has("m365_activity_emitter")

    def test_metadata_lists_analytics_and_abioc_targets(self):
        from eal_simulator import get_default_registry

        meta = get_default_registry().get("m365_activity_emitter").metadata()
        assert any("Analytics" in t for t in meta["eal_targets"])
        assert any("ABIOC" in t for t in meta["eal_targets"])
        assert "T1114" in meta["mitre_techniques"]
        props = meta["params_schema"]["properties"]
        assert "collector_url" in props
        assert "event_pattern" in props
        assert "auth_token" in props
        assert "compress" in props
        assert "batch" in props

    def test_not_c2_classified(self):
        from eal_simulator.safety import is_c2_plugin

        assert is_c2_plugin(
            "m365_activity_emitter", M365ActivityEmitter.Meta.mitre_techniques,
        ) is False

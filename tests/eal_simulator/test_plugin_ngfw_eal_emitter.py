"""Tests for the ngfw_eal_emitter EAL plugin (analytics_emitter spine).

We stub ``httpx.AsyncClient`` so no real outbound traffic is generated; each
test asserts the JSON shape posted to the collector, the transport toggles
(batch / gzip / auth) and the audit events emitted. The shared driver
(``AnalyticsLogEmitter``) is proven by the sibling cloud_audit test; this file
proves the NGFW EAL data-source builder + its params/registration.
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
from eal_simulator.plugins.ngfw_eal_emitter import (
    NgfwEalEmitter,
    NgfwEalEmitterParams,
    _DATASET,
    _EVENT_PATTERNS,
    _LARGE_UPLOAD_BYTES,
    _MASSIVE_UPLOAD_BYTES,
    _RARE_DOMAIN,
    _RARE_STORAGE_DOMAIN,
    _UNCOMMON_SSH_HOST,
    _list_event_patterns,
    _ngfw_eal_event,
    _pattern_alert,
    _PATTERN_SESSION,
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
    event_pattern: str = "abnormal_recurring_comms",
    iterations: int = 1,
    burst_count: int = 8,
    collector_url: str = _DEFAULT_COLLECTOR,
    target_allowlist: list[str] | None = None,
    dry_run: bool = False,
    **extra_params,
) -> Campaign:
    spec = {
        "campaign_id": "CMP-NGFWEAL-INTEG-001",
        "name": "ngfw_eal_emitter test",
        "dry_run": dry_run,
        "steps": [{
            "step_id": "step-01",
            "plugin": "ngfw_eal_emitter",
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
            # Several NGFW EAL alerts fire C2-tactic techniques (T1071); carry
            # c2_authorized so a live campaign is honestly consented even if the
            # plugin is later added to the C2 name allowlist.
            "c2_authorized": True,
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
            NgfwEalEmitterParams.model_validate({})

    def test_collector_url_must_be_http_or_https(self):
        with pytest.raises(Exception, match="http or https"):
            NgfwEalEmitterParams.model_validate({
                "collector_url": "ftp://x.invalid/cb",
            })

    def test_collector_url_requires_hostname(self):
        with pytest.raises(Exception, match="hostname"):
            NgfwEalEmitterParams.model_validate({
                "collector_url": "https:///event",
            })

    def test_unknown_event_pattern_rejected(self):
        with pytest.raises(Exception, match="event_pattern must be one of"):
            NgfwEalEmitterParams.model_validate({
                "collector_url": _DEFAULT_COLLECTOR,
                "event_pattern": "coin_mining",
            })

    def test_event_pattern_normalised_to_lowercase(self):
        p = NgfwEalEmitterParams.model_validate({
            "collector_url": _DEFAULT_COLLECTOR,
            "event_pattern": "RARE_SSH_SESSION",
        })
        assert p.event_pattern == "rare_ssh_session"

    def test_iterations_bounds(self):
        with pytest.raises(Exception):
            NgfwEalEmitterParams.model_validate({
                "collector_url": _DEFAULT_COLLECTOR, "iterations": 0,
            })
        with pytest.raises(Exception):
            NgfwEalEmitterParams.model_validate({
                "collector_url": _DEFAULT_COLLECTOR, "iterations": 999,
            })

    def test_auth_defaults_and_transport_toggles(self):
        p = NgfwEalEmitterParams.model_validate({
            "collector_url": _DEFAULT_COLLECTOR,
        })
        assert p.auth_token is None
        assert p.auth_header == "Authorization"
        assert p.auth_scheme == "Bearer"
        assert p.compress is False
        assert p.batch is False

    def test_dataset_is_panw_ngfw_eal_raw(self):
        p = NgfwEalEmitterParams.model_validate({
            "collector_url": _DEFAULT_COLLECTOR,
        })
        assert p.dataset == "panw_ngfw_eal_raw"

    def test_auth_token_is_secret_and_not_reprd(self):
        p = NgfwEalEmitterParams.model_validate({
            "collector_url": _DEFAULT_COLLECTOR,
            "auth_token": "super-secret-hec-token",
        })
        assert "super-secret-hec-token" not in repr(p)
        assert "super-secret-hec-token" not in str(p)
        assert "super-secret-hec-token" not in p.model_dump_json()
        assert p.auth_token.get_secret_value() == "super-secret-hec-token"


# --------------------------------------------------------------------------
# Record-shape builder
# --------------------------------------------------------------------------


class TestRecordShape:
    def test_six_event_patterns_registered(self):
        assert _list_event_patterns() == sorted(_EVENT_PATTERNS)
        assert len(_EVENT_PATTERNS) == 6

    def test_every_pattern_maps_to_a_named_alert(self):
        alerts = {_pattern_alert(p) for p in _EVENT_PATTERNS}
        assert "Large Upload (HTTPS)" in alerts
        assert "Recurring access to rare domain" in alerts
        assert any("SSH session" in a for a in alerts)

    def test_ngfw_eal_record_shape(self):
        marker, _alert, session = _PATTERN_SESSION["abnormal_comms"]
        evt = _ngfw_eal_event(
            session=session, marker=marker, sim_run_id="cortexsim-x-i1-aa",
        )
        assert evt["dataset"] == _DATASET
        assert evt["type"] == "TRAFFIC"
        assert evt["log_source_type"] == "eal"
        assert evt["app"] == "ssl"
        assert evt["dst_port"] == 443
        assert evt["url_domain"] == _RARE_DOMAIN
        # EAL enhanced endpoint-process context is present.
        assert evt["process_name"] == "rundll32.exe"
        assert "process_hash" in evt
        assert evt["endpoint_device_name"]
        assert evt[marker] is True
        assert evt["cortexsim_run_id"] == "cortexsim-x-i1-aa"


# --------------------------------------------------------------------------
# Plugin run path — stubbed httpx (no network)
# --------------------------------------------------------------------------


class TestPluginRun:
    def _run_with_stub(self, monkeypatch, *, status_code=202, raise_exc=None, **campaign_kw):
        stub = _RecordingClient(status_code=status_code, raise_exc=raise_exc)
        monkeypatch.setattr(
            NgfwEalEmitter, "_build_client", lambda self, params: stub,
        )
        campaign = _campaign(**campaign_kw)
        executor = CampaignExecutor(audit=AuditLogger(file_path=None))
        state = _run(executor.execute(campaign))
        return state, stub

    def test_dry_run_does_not_invoke_client(self, monkeypatch):
        def _boom(self, params):  # noqa: ARG001
            raise AssertionError("client should not be built in dry-run")
        monkeypatch.setattr(NgfwEalEmitter, "_build_client", _boom)

        campaign = _campaign(dry_run=True)
        state = _run(CampaignExecutor(audit=AuditLogger(file_path=None)).execute(campaign))
        sr = state.step_results[0]
        assert sr.status == "success"
        assert sr.detail["dry_run"] is True
        assert sr.events_emitted == 1
        assert sr.bytes_sent == 0

    def test_recurring_comms_bursts_sessions(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="abnormal_recurring_comms", burst_count=5,
        )
        assert state.step_results[0].status == "success"
        assert len(stub.requests) == 5
        bodies = _bodies(stub)
        assert all(b.get("abnormal_recurring_comms_marker") for b in bodies)
        assert all(b["dataset"] == _DATASET for b in bodies)
        assert all(b["url_domain"] == _RARE_DOMAIN for b in bodies)
        # Beacon-sequence ordinal present on each recurring session.
        assert [b["beacon_sequence"] for b in bodies] == [0, 1, 2, 3, 4]

    def test_abnormal_comms_single_session(self, monkeypatch):
        state, stub = self._run_with_stub(monkeypatch, event_pattern="abnormal_comms")
        bodies = _bodies(stub)
        assert len(bodies) == 1
        assert bodies[0].get("abnormal_comms_marker") is True
        assert bodies[0]["app"] == "ssl"

    def test_large_upload_https_volume(self, monkeypatch):
        state, stub = self._run_with_stub(monkeypatch, event_pattern="large_upload_https")
        bodies = _bodies(stub)
        assert len(bodies) == 1
        assert bodies[0]["bytes_sent"] == _LARGE_UPLOAD_BYTES
        assert bodies[0]["dst_port"] == 443
        assert bodies[0].get("large_upload_https_marker") is True

    def test_massive_upload_targets_rare_storage_domain(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="massive_upload_rare_domain",
        )
        body = _bodies(stub)[0]
        assert body["bytes_sent"] == _MASSIVE_UPLOAD_BYTES
        assert body["url_domain"] == _RARE_STORAGE_DOMAIN
        assert body.get("massive_upload_rare_domain_marker") is True

    def test_rare_ssh_session_shape(self, monkeypatch):
        state, stub = self._run_with_stub(monkeypatch, event_pattern="rare_ssh_session")
        body = _bodies(stub)[0]
        assert body["app"] == "ssh"
        assert body["dst_port"] == 22
        assert body["dst_hostname"] == _UNCOMMON_SSH_HOST
        assert body["process_name"] == "python.exe"
        assert body.get("rare_ssh_session_marker") is True

    def test_recurring_rare_domain_access_bursts(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="recurring_rare_domain_access", burst_count=4,
        )
        bodies = _bodies(stub)
        assert len(bodies) == 4
        assert all(b["app"] == "web-browsing" for b in bodies)
        assert all(b.get("recurring_rare_domain_access_marker") for b in bodies)

    def test_iterations_multiply_record_count(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="abnormal_comms", iterations=3,
        )
        assert len(stub.requests) == 3  # 1 record * 3 iterations
        assert state.step_results[0].detail["events_posted"] == 3

    # -- transport toggles ------------------------------------------------

    def test_batch_mode_single_ndjson_post(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="abnormal_recurring_comms",
            burst_count=6, batch=True,
        )
        assert len(stub.requests) == 1
        req = stub.requests[0]
        assert req["headers"]["content-type"] == "application/x-ndjson"
        records = _bodies(stub, batch=True)
        assert len(records) == 6
        detail = state.step_results[0].detail
        assert detail["events_posted"] == 6
        assert detail["response_status_counts"] == {202: 1}
        assert detail["batch"] is True

    def test_compress_mode_gzips_body(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="large_upload_https", compress=True,
        )
        req = stub.requests[0]
        assert req["headers"]["content-encoding"] == "gzip"
        assert req["content"][:2] == b"\x1f\x8b"  # gzip magic
        body = _bodies(stub)[0]
        assert body["bytes_sent"] == _LARGE_UPLOAD_BYTES

    # -- telemetry / identity --------------------------------------------

    def test_telemetry_headers_injected_lowercase(self, monkeypatch):
        state, stub = self._run_with_stub(monkeypatch)
        headers = stub.requests[0]["headers"]
        assert "x-simulation-run-id" in headers
        assert headers["x-simulation-run-id"].startswith("cortexsim-")
        assert "-i1-" in headers["x-simulation-run-id"]
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
            NgfwEalEmitter, "_build_client", lambda self, params: stub,
        )
        campaign = _campaign(target_allowlist=["other.invalid"])
        state = _run(CampaignExecutor(audit=AuditLogger(file_path=None)).execute(campaign))
        sr = state.step_results[0]
        assert sr.status == "error"
        assert "safety_violation" in (sr.error or "")
        assert stub.requests == []

    def test_http_error_does_not_crash_iteration(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="abnormal_recurring_comms", burst_count=4,
            raise_exc=httpx.ConnectError("boom"),
        )
        sr = state.step_results[0]
        assert sr.status == "success"
        assert len(stub.requests) == 4  # all attempted despite errors
        assert sr.detail["response_status_counts"].get(0, 0) == 4
        assert sr.detail["events_posted"] == 0


# --------------------------------------------------------------------------
# Registry / metadata
# --------------------------------------------------------------------------


class TestRegistration:
    def test_plugin_registered_with_default_registry(self):
        from eal_simulator import get_default_registry

        reg = get_default_registry()
        assert reg.has("ngfw_eal_emitter")

    def test_metadata_lists_analytics_and_abioc_targets(self):
        from eal_simulator import get_default_registry

        meta = get_default_registry().get("ngfw_eal_emitter").metadata()
        assert any("Analytics" in t for t in meta["eal_targets"])
        assert any("ABIOC" in t for t in meta["eal_targets"])
        assert "T1071.001" in meta["mitre_techniques"]
        props = meta["params_schema"]["properties"]
        assert "collector_url" in props
        assert "event_pattern" in props
        assert "auth_token" in props
        assert "compress" in props
        assert "batch" in props

    def test_base_params_shared_fields_present(self):
        fields = NgfwEalEmitterParams.model_fields
        for name in AnalyticsEmitterParams.model_fields:
            assert name in fields

    def test_c2_classified_by_technique(self):
        # NGFW beaconing alerts fire T1071 (C2 tactic) -> the plugin is
        # C2-shaped, so a live campaign should carry c2_authorized.
        from eal_simulator.safety import is_c2_plugin

        assert is_c2_plugin(
            "ngfw_eal_emitter", NgfwEalEmitter.Meta.mitre_techniques,
        ) is True

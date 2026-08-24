"""Tests for the cloud_storage_compute_emitter EAL plugin.

Sibling of test_plugin_cloud_audit_emitter — same ``analytics_emitter`` spine,
different (compute / instance-credential) data-source slice. We stub
``httpx.AsyncClient`` so no real outbound traffic is generated; each test
asserts the JSON shape posted to the collector, the transport toggles
(batch / gzip / auth), the per-pattern record fan-out, and safety-policy
enforcement (host allowlist authorisation) end-to-end through the executor.

Dry-run + stub-transport prove the plugin registers and emits its expected
events with NO network.
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
from eal_simulator.plugins.cloud_storage_compute_emitter import (
    CloudStorageComputeEmitter,
    CloudStorageComputeEmitterParams,
    _DATASET,
    _EVENT_PATTERNS,
    _MULTI_REGIONS,
    _PATTERN_ALERT,
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
    provider: str = "aws",
    event_pattern: str = "imds_token_usage",
    iterations: int = 1,
    burst_count: int = 8,
    collector_url: str = _DEFAULT_COLLECTOR,
    target_allowlist: list[str] | None = None,
    dry_run: bool = False,
    **extra_params,
) -> Campaign:
    spec = {
        "campaign_id": "CMP-CLOUDCOMPUTE-INTEG-001",
        "name": "cloud_storage_compute_emitter test",
        "dry_run": dry_run,
        "steps": [{
            "step_id": "step-01",
            "plugin": "cloud_storage_compute_emitter",
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
            CloudStorageComputeEmitterParams.model_validate({})

    def test_collector_url_must_be_http_or_https(self):
        with pytest.raises(Exception, match="http or https"):
            CloudStorageComputeEmitterParams.model_validate({
                "collector_url": "ftp://x.invalid/cb",
            })

    def test_collector_url_requires_hostname(self):
        with pytest.raises(Exception, match="hostname"):
            CloudStorageComputeEmitterParams.model_validate({
                "collector_url": "https:///event",
            })

    def test_unknown_provider_rejected(self):
        with pytest.raises(Exception, match="provider must be one of"):
            CloudStorageComputeEmitterParams.model_validate({
                "collector_url": _DEFAULT_COLLECTOR, "provider": "gcp",
            })

    def test_unknown_event_pattern_rejected(self):
        with pytest.raises(Exception, match="event_pattern must be one of"):
            CloudStorageComputeEmitterParams.model_validate({
                "collector_url": _DEFAULT_COLLECTOR,
                "event_pattern": "coin_mining",
            })

    def test_provider_normalised_to_lowercase(self):
        p = CloudStorageComputeEmitterParams.model_validate({
            "collector_url": _DEFAULT_COLLECTOR, "provider": "AWS",
        })
        assert p.provider == "aws"

    def test_event_pattern_normalised_to_lowercase(self):
        p = CloudStorageComputeEmitterParams.model_validate({
            "collector_url": _DEFAULT_COLLECTOR,
            "event_pattern": "EBS_SNAPSHOT_DOWNLOAD",
        })
        assert p.event_pattern == "ebs_snapshot_download"

    def test_iterations_bounds(self):
        with pytest.raises(Exception):
            CloudStorageComputeEmitterParams.model_validate({
                "collector_url": _DEFAULT_COLLECTOR, "iterations": 0,
            })
        with pytest.raises(Exception):
            CloudStorageComputeEmitterParams.model_validate({
                "collector_url": _DEFAULT_COLLECTOR, "iterations": 999,
            })

    def test_auth_defaults_and_transport_toggles(self):
        p = CloudStorageComputeEmitterParams.model_validate({
            "collector_url": _DEFAULT_COLLECTOR,
        })
        assert p.auth_token is None
        assert p.auth_header == "Authorization"
        assert p.auth_scheme == "Bearer"
        assert p.compress is False
        assert p.batch is False

    def test_dataset_is_cloud_audit_logs(self):
        p = CloudStorageComputeEmitterParams.model_validate({
            "collector_url": _DEFAULT_COLLECTOR,
        })
        assert p.dataset == "cloud_audit_logs"

    def test_auth_token_is_secret_and_not_reprd(self):
        p = CloudStorageComputeEmitterParams.model_validate({
            "collector_url": _DEFAULT_COLLECTOR,
            "auth_token": "super-secret-hec-token",
        })
        assert "super-secret-hec-token" not in repr(p)
        assert "super-secret-hec-token" not in str(p)
        assert "super-secret-hec-token" not in p.model_dump_json()
        assert p.auth_token.get_secret_value() == "super-secret-hec-token"


# --------------------------------------------------------------------------
# Provider event-shape adapter
# --------------------------------------------------------------------------


class TestProviderShapes:
    def test_single_aws_provider_registered(self):
        assert _list_providers() == ["aws"]

    def test_seven_event_patterns_registered(self):
        assert _list_event_patterns() == sorted(_EVENT_PATTERNS)
        assert len(_EVENT_PATTERNS) == 7

    def test_all_named_analytics_alerts_covered(self):
        alerts = {spec.alert for spec in _PATTERN_ALERT.values()}
        assert alerts == {
            "Cloud IMDS access followed by remote token usage",
            "Suspicious usage of EC2 token",
            "Remote usage of an AWS service token",
            "Compute activity in dormant cloud region",
            "Abnormal Allocation of compute resources in multiple regions",
            "Suspicious heavy allocation of compute resources - possible mining activity",
            "An EBS snapshot block was downloaded",
        }

    def test_aws_cloudtrail_shape(self):
        evt = _PROVIDER_BUILDERS["aws"](
            event_name="RunInstances",
            event_source="ec2.amazonaws.com",
            marker="dormant_region_marker",
            sim_run_id="cortexsim-x-i1-aa",
        )
        assert evt["dataset"] == _DATASET
        assert evt["eventName"] == "RunInstances"
        assert evt["eventSource"] == "ec2.amazonaws.com"
        assert evt["userIdentity"]["type"] == "IAMUser"
        assert evt["dormant_region_marker"] is True
        assert evt["cortexsim_run_id"] == "cortexsim-x-i1-aa"


# --------------------------------------------------------------------------
# Plugin run path — stubbed httpx (no network)
# --------------------------------------------------------------------------


class TestPluginRun:
    def _run_with_stub(self, monkeypatch, *, status_code=202, raise_exc=None, **campaign_kw):
        stub = _RecordingClient(status_code=status_code, raise_exc=raise_exc)
        monkeypatch.setattr(
            CloudStorageComputeEmitter, "_build_client", lambda self, params: stub,
        )
        campaign = _campaign(**campaign_kw)
        executor = CampaignExecutor(audit=AuditLogger(file_path=None))
        state = _run(executor.execute(campaign))
        return state, stub

    def test_dry_run_does_not_invoke_client(self, monkeypatch):
        def _boom(self, params):  # noqa: ARG001
            raise AssertionError("client should not be built in dry-run")
        monkeypatch.setattr(CloudStorageComputeEmitter, "_build_client", _boom)

        campaign = _campaign(dry_run=True)
        state = _run(CampaignExecutor(audit=AuditLogger(file_path=None)).execute(campaign))
        sr = state.step_results[0]
        assert sr.status == "success"
        assert sr.detail["dry_run"] is True
        assert sr.events_emitted == 1
        assert sr.bytes_sent == 0

    def test_imds_token_usage_posts_access_then_remote_use(self, monkeypatch):
        state, stub = self._run_with_stub(monkeypatch, event_pattern="imds_token_usage")
        assert state.step_results[0].status == "success"
        bodies = _bodies(stub)
        assert len(bodies) == 2
        assert bodies[0]["eventName"] == "GetInstanceMetadataCredentials"
        assert bodies[0]["sourceIPAddress"] == "169.254.169.254"
        assert bodies[0].get("imds_access_marker") is True
        assert bodies[1]["eventName"] == "GetCallerIdentity"
        assert bodies[1]["sourceIPAddress"] == "203.0.113.201"
        assert bodies[1].get("remote_token_use_marker") is True
        # Same instance role session on both sides of the causality chain.
        assert bodies[0]["userIdentity"]["type"] == "AssumedRole"
        assert bodies[1]["userIdentity"]["arn"] == bodies[0]["userIdentity"]["arn"]
        assert all(b.get("imds_token_usage_marker") for b in bodies)
        assert all(b["dataset"] == _DATASET for b in bodies)

    def test_ec2_token_usage_posts_assumed_role_offbox(self, monkeypatch):
        state, stub = self._run_with_stub(monkeypatch, event_pattern="ec2_token_usage")
        bodies = _bodies(stub)
        assert len(bodies) == 1
        assert bodies[0]["userIdentity"]["type"] == "AssumedRole"
        assert bodies[0]["sourceIPAddress"] == "203.0.113.201"
        assert bodies[0].get("credential_source") == "ec2-instance-profile"
        assert bodies[0].get("ec2_token_usage_marker") is True

    def test_service_token_remote_use_shape(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="service_token_remote_use",
        )
        bodies = _bodies(stub)
        assert len(bodies) == 1
        assert bodies[0]["eventSource"] == "secretsmanager.amazonaws.com"
        assert bodies[0].get("role_type") == "service-role"
        assert "assumed-role/cortexsim-canary-lambda-exec-role" in \
            bodies[0]["userIdentity"]["arn"]
        assert bodies[0].get("service_token_remote_use_marker") is True

    def test_dormant_region_activity_uses_offbeat_region(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="dormant_region_activity",
        )
        bodies = _bodies(stub)
        assert len(bodies) == 1
        assert bodies[0]["eventName"] == "RunInstances"
        assert bodies[0]["awsRegion"] == "ap-south-1"
        assert bodies[0].get("dormant_region_marker") is True

    def test_multi_region_allocation_fans_across_regions(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="multi_region_allocation", burst_count=5,
        )
        bodies = _bodies(stub)
        assert len(bodies) == 5
        regions = [b["awsRegion"] for b in bodies]
        assert regions == list(_MULTI_REGIONS[:5])
        assert len(set(regions)) == 5  # distinct regions
        assert all(b["eventName"] == "RunInstances" for b in bodies)
        assert all(b.get("multi_region_allocation_marker") for b in bodies)

    def test_heavy_compute_allocation_bursts_gpu_instances(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="heavy_compute_allocation", burst_count=6,
        )
        bodies = _bodies(stub)
        assert len(bodies) == 6
        assert all(b["instance_type"] == "p4d.24xlarge" for b in bodies)
        assert all(b.get("possible_mining") is True for b in bodies)
        assert all(b.get("heavy_compute_allocation_marker") for b in bodies)

    def test_ebs_snapshot_download_posts_create_then_getblock(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="ebs_snapshot_download",
        )
        bodies = _bodies(stub)
        assert len(bodies) == 2
        assert bodies[0]["eventName"] == "CreateSnapshot"
        assert bodies[1]["eventName"] == "GetSnapshotBlock"
        assert bodies[1]["eventSource"] == "ebs.amazonaws.com"
        assert bodies[1].get("block_downloaded") is True
        assert all(b.get("ebs_snapshot_download_marker") for b in bodies)

    def test_iterations_multiply_record_count(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="imds_token_usage", iterations=3,
        )
        assert len(stub.requests) == 6  # 2 records * 3 iterations
        assert state.step_results[0].detail["events_posted"] == 6

    # -- transport toggles ------------------------------------------------

    def test_batch_mode_single_ndjson_post(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="imds_token_usage", batch=True,
        )
        assert len(stub.requests) == 1
        req = stub.requests[0]
        assert req["headers"]["content-type"] == "application/x-ndjson"
        records = _bodies(stub, batch=True)
        assert len(records) == 2
        assert [r["eventName"] for r in records] == [
            "GetInstanceMetadataCredentials", "GetCallerIdentity",
        ]
        detail = state.step_results[0].detail
        assert detail["events_posted"] == 2
        assert detail["response_status_counts"] == {202: 1}
        assert detail["batch"] is True

    def test_compress_mode_gzips_body(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="dormant_region_activity", compress=True,
        )
        req = stub.requests[0]
        assert req["headers"]["content-encoding"] == "gzip"
        assert req["content"][:2] == b"\x1f\x8b"  # gzip magic
        body = _bodies(stub)[0]
        assert body["eventName"] == "RunInstances"

    def test_batch_and_compress_together(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="multi_region_allocation",
            burst_count=4, batch=True, compress=True,
        )
        assert len(stub.requests) == 1
        req = stub.requests[0]
        assert req["headers"]["content-encoding"] == "gzip"
        assert req["headers"]["content-type"] == "application/x-ndjson"
        records = _bodies(stub, batch=True)
        assert len(records) == 4

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
            monkeypatch, event_pattern="dormant_region_activity", iterations=3,
        )
        ids = {r["headers"]["x-simulation-run-id"] for r in stub.requests}
        assert len(ids) == 3, ids

    def test_auth_token_never_leaks_into_event_body(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="dormant_region_activity",
            auth_token="super-secret-hec-token",
        )
        # The secret is a client-level header (stub bypasses it) and must never
        # appear in any emitted record body.
        for r in stub.requests:
            raw = r["content"]
            text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw
            assert "super-secret-hec-token" not in text

    def test_request_url_targets_collector(self, monkeypatch):
        state, stub = self._run_with_stub(monkeypatch)
        for r in stub.requests:
            assert r["url"] == _DEFAULT_COLLECTOR

    # -- safety + resilience ---------------------------------------------

    def test_safety_violation_when_collector_not_allowlisted(self, monkeypatch):
        stub = _RecordingClient()
        monkeypatch.setattr(
            CloudStorageComputeEmitter, "_build_client", lambda self, params: stub,
        )
        campaign = _campaign(target_allowlist=["other.invalid"])
        state = _run(CampaignExecutor(audit=AuditLogger(file_path=None)).execute(campaign))
        sr = state.step_results[0]
        assert sr.status == "error"
        assert "safety_violation" in (sr.error or "")
        assert stub.requests == []

    def test_http_error_does_not_crash_iteration(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="imds_token_usage", iterations=2,
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


# --------------------------------------------------------------------------
# Injectable transport — auth header (exercised on the real _build_client)
# --------------------------------------------------------------------------


class TestAuthHeader:
    def test_no_auth_header_when_token_absent(self):
        emitter = CloudStorageComputeEmitter()
        emitter._verify_tls = False
        params = CloudStorageComputeEmitterParams.model_validate({
            "collector_url": _DEFAULT_COLLECTOR,
        })
        client = emitter._build_client(params)
        try:
            assert "authorization" not in {k.lower() for k in client.headers}
        finally:
            _run(client.aclose())

    def test_bearer_auth_header_set_from_token(self):
        emitter = CloudStorageComputeEmitter()
        emitter._verify_tls = False
        params = CloudStorageComputeEmitterParams.model_validate({
            "collector_url": _DEFAULT_COLLECTOR,
            "auth_token": "tok-123",
        })
        client = emitter._build_client(params)
        try:
            assert client.headers["authorization"] == "Bearer tok-123"
        finally:
            _run(client.aclose())

    def test_content_type_ndjson_when_batch(self):
        emitter = CloudStorageComputeEmitter()
        emitter._verify_tls = False
        params = CloudStorageComputeEmitterParams.model_validate({
            "collector_url": _DEFAULT_COLLECTOR, "batch": True,
        })
        client = emitter._build_client(params)
        try:
            assert client.headers["content-type"] == "application/x-ndjson"
        finally:
            _run(client.aclose())


# --------------------------------------------------------------------------
# Registry / metadata
# --------------------------------------------------------------------------


class TestRegistration:
    def test_plugin_registered_with_default_registry(self):
        from eal_simulator import get_default_registry

        reg = get_default_registry()
        assert reg.has("cloud_storage_compute_emitter")

    def test_metadata_lists_analytics_and_abioc_targets(self):
        from eal_simulator import get_default_registry

        meta = get_default_registry().get("cloud_storage_compute_emitter").metadata()
        assert any("Analytics" in t for t in meta["eal_targets"])
        assert any("ABIOC" in t for t in meta["eal_targets"])
        assert "T1552.005" in meta["mitre_techniques"]
        props = meta["params_schema"]["properties"]
        assert "collector_url" in props
        assert "event_pattern" in props
        assert "auth_token" in props
        assert "compress" in props
        assert "batch" in props

    def test_base_params_shared_fields_present(self):
        fields = CloudStorageComputeEmitterParams.model_fields
        for name in AnalyticsEmitterParams.model_fields:
            assert name in fields

    def test_not_c2_classified(self):
        # Cloud compute/credential techniques must NOT trip the C2 gate — only
        # simulation_authorized is required, not c2_authorized.
        from eal_simulator.safety import is_c2_plugin

        assert is_c2_plugin(
            "cloud_storage_compute_emitter",
            CloudStorageComputeEmitter.Meta.mitre_techniques,
        ) is False

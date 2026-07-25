"""Tests for the cloud_audit_emitter EAL plugin + the analytics_emitter spine.

We stub ``httpx.AsyncClient`` so no real outbound traffic is generated; each
test asserts the JSON shape posted to the collector, the transport toggles
(batch / gzip / auth), and the audit events emitted. Safety-policy enforcement
(host allowlist authorisation) is exercised through the executor end-to-end.

This test doubles as the framework proof: the shared driver
(``AnalyticsLogEmitter`` in ``analytics_emitter.py``) is exercised entirely
through this one reference source plugin — dry-run, per-event POST, NDJSON
batch, gzip, ingestion-auth header, budget charging and error swallowing all
live in the base, so proving them here proves the spine.
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
from eal_simulator.plugins.cloud_audit_emitter import (
    CloudAuditEmitter,
    CloudAuditEmitterParams,
    _DATASET,
    _EVENT_PATTERNS,
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
    event_pattern: str = "iam_privilege_escalation",
    iterations: int = 1,
    burst_count: int = 8,
    collector_url: str = _DEFAULT_COLLECTOR,
    target_allowlist: list[str] | None = None,
    dry_run: bool = False,
    **extra_params,
) -> Campaign:
    spec = {
        "campaign_id": "CMP-CLOUDAUDIT-INTEG-001",
        "name": "cloud_audit_emitter test",
        "dry_run": dry_run,
        "steps": [{
            "step_id": "step-01",
            "plugin": "cloud_audit_emitter",
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
            CloudAuditEmitterParams.model_validate({})

    def test_collector_url_must_be_http_or_https(self):
        with pytest.raises(Exception, match="http or https"):
            CloudAuditEmitterParams.model_validate({
                "collector_url": "ftp://x.invalid/cb",
            })

    def test_collector_url_requires_hostname(self):
        with pytest.raises(Exception, match="hostname"):
            CloudAuditEmitterParams.model_validate({
                "collector_url": "https:///event",
            })

    def test_unknown_provider_rejected(self):
        with pytest.raises(Exception, match="provider must be one of"):
            CloudAuditEmitterParams.model_validate({
                "collector_url": _DEFAULT_COLLECTOR, "provider": "azure",
            })

    def test_unknown_event_pattern_rejected(self):
        with pytest.raises(Exception, match="event_pattern must be one of"):
            CloudAuditEmitterParams.model_validate({
                "collector_url": _DEFAULT_COLLECTOR,
                "event_pattern": "coin_mining",
            })

    def test_provider_normalised_to_lowercase(self):
        p = CloudAuditEmitterParams.model_validate({
            "collector_url": _DEFAULT_COLLECTOR, "provider": "AWS",
        })
        assert p.provider == "aws"

    def test_event_pattern_normalised_to_lowercase(self):
        p = CloudAuditEmitterParams.model_validate({
            "collector_url": _DEFAULT_COLLECTOR,
            "event_pattern": "S3_PUBLIC_EXPOSURE",
        })
        assert p.event_pattern == "s3_public_exposure"

    def test_iterations_bounds(self):
        with pytest.raises(Exception):
            CloudAuditEmitterParams.model_validate({
                "collector_url": _DEFAULT_COLLECTOR, "iterations": 0,
            })
        with pytest.raises(Exception):
            CloudAuditEmitterParams.model_validate({
                "collector_url": _DEFAULT_COLLECTOR, "iterations": 999,
            })

    def test_auth_defaults_and_transport_toggles(self):
        p = CloudAuditEmitterParams.model_validate({
            "collector_url": _DEFAULT_COLLECTOR,
        })
        assert p.auth_token is None
        assert p.auth_header == "Authorization"
        assert p.auth_scheme == "Bearer"
        assert p.compress is False
        assert p.batch is False

    def test_dataset_is_cloud_audit_logs(self):
        p = CloudAuditEmitterParams.model_validate({
            "collector_url": _DEFAULT_COLLECTOR,
        })
        assert p.dataset == "cloud_audit_logs"

    def test_auth_token_is_secret_and_not_reprd(self):
        p = CloudAuditEmitterParams.model_validate({
            "collector_url": _DEFAULT_COLLECTOR,
            "auth_token": "super-secret-hec-token",
        })
        # SecretStr must never surface the raw value in repr/str/json dumps.
        assert "super-secret-hec-token" not in repr(p)
        assert "super-secret-hec-token" not in str(p)
        assert "super-secret-hec-token" not in p.model_dump_json()
        assert p.auth_token.get_secret_value() == "super-secret-hec-token"


# --------------------------------------------------------------------------
# Provider event-shape adapters
# --------------------------------------------------------------------------


class TestProviderShapes:
    def test_two_providers_registered(self):
        assert _list_providers() == sorted(["aws", "gcp"])

    def test_three_event_patterns_registered(self):
        assert _list_event_patterns() == sorted(_EVENT_PATTERNS)

    def test_aws_cloudtrail_shape(self):
        from eal_simulator.plugins.cloud_audit_emitter import _PATTERN_ACTIVITY
        marker, activities = _PATTERN_ACTIVITY["iam_privilege_escalation"]
        evt = _PROVIDER_BUILDERS["aws"](
            activity=activities[0], marker=marker, sim_run_id="cortexsim-x-i1-aa",
        )
        assert evt["dataset"] == _DATASET
        assert evt["eventName"] == "CreateUser"
        assert evt["eventSource"] == "iam.amazonaws.com"
        assert evt["userIdentity"]["type"] == "IAMUser"
        assert evt[marker] is True
        assert evt["cortexsim_run_id"] == "cortexsim-x-i1-aa"

    def test_gcp_audit_shape(self):
        from eal_simulator.plugins.cloud_audit_emitter import _PATTERN_ACTIVITY
        marker, activities = _PATTERN_ACTIVITY["iam_privilege_escalation"]
        evt = _PROVIDER_BUILDERS["gcp"](
            activity=activities[1], marker=marker, sim_run_id="cortexsim-x-i1-bb",
        )
        assert evt["dataset"] == _DATASET
        assert evt["protoPayload"]["methodName"] == "SetIamPolicy"
        assert evt["protoPayload"]["serviceName"] == "cloudresourcemanager.googleapis.com"
        assert evt["protoPayload"]["authenticationInfo"]["principalEmail"].endswith(".invalid")
        assert evt[marker] is True
        assert evt["cortexsim_run_id"] == "cortexsim-x-i1-bb"


# --------------------------------------------------------------------------
# Plugin run path — stubbed httpx (no network)
# --------------------------------------------------------------------------


class TestPluginRun:
    def _run_with_stub(self, monkeypatch, *, status_code=202, raise_exc=None, **campaign_kw):
        stub = _RecordingClient(status_code=status_code, raise_exc=raise_exc)
        monkeypatch.setattr(
            CloudAuditEmitter, "_build_client", lambda self, params: stub,
        )
        campaign = _campaign(**campaign_kw)
        executor = CampaignExecutor(audit=AuditLogger(file_path=None))
        state = _run(executor.execute(campaign))
        return state, stub

    def test_dry_run_does_not_invoke_client(self, monkeypatch):
        def _boom(self, params):  # noqa: ARG001
            raise AssertionError("client should not be built in dry-run")
        monkeypatch.setattr(CloudAuditEmitter, "_build_client", _boom)

        campaign = _campaign(dry_run=True)
        state = _run(CampaignExecutor(audit=AuditLogger(file_path=None)).execute(campaign))
        sr = state.step_results[0]
        assert sr.status == "success"
        assert sr.detail["dry_run"] is True
        assert sr.events_emitted == 1
        assert sr.bytes_sent == 0

    def test_iam_privilege_escalation_posts_two_records(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="iam_privilege_escalation",
        )
        assert state.step_results[0].status == "success"
        assert len(stub.requests) == 2
        bodies = _bodies(stub)
        names = [b["eventName"] for b in bodies]
        assert names == ["CreateUser", "AttachUserPolicy"]
        assert all(b.get("privilege_escalation_marker") for b in bodies)
        assert all(b["dataset"] == _DATASET for b in bodies)

    def test_s3_public_exposure_posts_single_record(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="s3_public_exposure",
        )
        bodies = _bodies(stub)
        assert len(bodies) == 1
        assert bodies[0]["eventName"] == "PutBucketPolicy"
        assert bodies[0].get("public_exposure_marker") is True

    def test_console_login_anomaly_bursts_failures_then_success(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="console_login_anomaly", burst_count=5,
        )
        bodies = _bodies(stub)
        # burst_count failures + 1 success.
        assert len(bodies) == 6
        results = [b["login_result"] for b in bodies]
        assert results == ["Failure"] * 5 + ["Success"]
        assert all(b.get("console_login_anomaly_marker") for b in bodies)
        assert all(b.get("mfa_used") is False for b in bodies)

    def test_iterations_multiply_record_count(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="iam_privilege_escalation", iterations=3,
        )
        assert len(stub.requests) == 6  # 2 records * 3 iterations
        assert state.step_results[0].detail["events_posted"] == 6

    def test_gcp_provider_shape_in_request_body(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, provider="gcp", event_pattern="iam_privilege_escalation",
        )
        bodies = _bodies(stub)
        for b in bodies:
            assert b["dataset"] == _DATASET
            assert "protoPayload" in b
            assert "methodName" in b["protoPayload"]

    # -- transport toggles ------------------------------------------------

    def test_batch_mode_single_ndjson_post(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="iam_privilege_escalation", batch=True,
        )
        # Two records, ONE POST.
        assert len(stub.requests) == 1
        req = stub.requests[0]
        assert req["headers"]["content-type"] == "application/x-ndjson"
        records = _bodies(stub, batch=True)
        assert len(records) == 2
        assert [r["eventName"] for r in records] == ["CreateUser", "AttachUserPolicy"]
        detail = state.step_results[0].detail
        assert detail["events_posted"] == 2
        assert detail["response_status_counts"] == {202: 1}
        assert detail["batch"] is True

    def test_compress_mode_gzips_body(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="s3_public_exposure", compress=True,
        )
        req = stub.requests[0]
        assert req["headers"]["content-encoding"] == "gzip"
        assert req["content"][:2] == b"\x1f\x8b"  # gzip magic
        # Still decodes to the shape-true record.
        body = _bodies(stub)[0]
        assert body["eventName"] == "PutBucketPolicy"

    def test_batch_and_compress_together(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="console_login_anomaly",
            burst_count=4, batch=True, compress=True,
        )
        assert len(stub.requests) == 1
        req = stub.requests[0]
        assert req["headers"]["content-encoding"] == "gzip"
        assert req["headers"]["content-type"] == "application/x-ndjson"
        records = _bodies(stub, batch=True)
        assert len(records) == 5  # 4 failures + 1 success

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
            monkeypatch, event_pattern="s3_public_exposure", iterations=3,
        )
        ids = {r["headers"]["x-simulation-run-id"] for r in stub.requests}
        assert len(ids) == 3, ids

    def test_response_status_counts_recorded(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="iam_privilege_escalation", iterations=2,
        )
        # 2 iters * 2 records, stub returns 202.
        assert state.step_results[0].detail["response_status_counts"] == {202: 4}

    def test_request_url_targets_collector(self, monkeypatch):
        state, stub = self._run_with_stub(monkeypatch)
        for r in stub.requests:
            assert r["url"] == _DEFAULT_COLLECTOR

    # -- safety + resilience ---------------------------------------------

    def test_safety_violation_when_collector_not_allowlisted(self, monkeypatch):
        stub = _RecordingClient()
        monkeypatch.setattr(
            CloudAuditEmitter, "_build_client", lambda self, params: stub,
        )
        campaign = _campaign(target_allowlist=["other.invalid"])
        state = _run(CampaignExecutor(audit=AuditLogger(file_path=None)).execute(campaign))
        sr = state.step_results[0]
        assert sr.status == "error"
        assert "safety_violation" in (sr.error or "")
        assert stub.requests == []

    def test_http_error_does_not_crash_iteration(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="iam_privilege_escalation", iterations=2,
            raise_exc=httpx.ConnectError("boom"),
        )
        sr = state.step_results[0]
        assert sr.status == "success"
        assert len(stub.requests) == 4  # all attempted despite errors
        assert sr.detail["response_status_counts"].get(0, 0) == 4
        assert sr.detail["events_posted"] == 0


# --------------------------------------------------------------------------
# New analytics/ABIOC alert patterns (the assigned data-source coverage)
# --------------------------------------------------------------------------


class TestAnalyticsAlertPatterns:
    """Each new event_pattern emits the shape-true records for one (or two)
    of the assigned cloud_audit_logs analytics/ABIOC alerts, with no network.
    """

    def _run_with_stub(self, monkeypatch, *, status_code=202, raise_exc=None, **campaign_kw):
        stub = _RecordingClient(status_code=status_code, raise_exc=raise_exc)
        monkeypatch.setattr(
            CloudAuditEmitter, "_build_client", lambda self, params: stub,
        )
        campaign = _campaign(**campaign_kw)
        executor = CampaignExecutor(audit=AuditLogger(file_path=None))
        state = _run(executor.execute(campaign))
        return state, stub

    def test_all_assigned_patterns_registered(self):
        for p in (
            "storage_object_download",
            "secrets_dump",
            "ssm_parameter_retrieval",
            "iam_enumeration",
            "iam_enumeration_non_user",
            "s3_bucket_enumeration",
            "bigquery_exfiltration",
            "service_account_impersonation",
        ):
            assert p in _EVENT_PATTERNS

    def test_storage_object_download_aws_burst_getobject(self, monkeypatch):
        # Alert: "Suspicious identity downloaded multiple objects from a bucket".
        state, stub = self._run_with_stub(
            monkeypatch, provider="aws",
            event_pattern="storage_object_download", burst_count=6,
        )
        bodies = _bodies(stub)
        assert len(bodies) == 6
        assert all(b["eventName"] == "GetObject" for b in bodies)
        assert all(b["eventSource"] == "s3.amazonaws.com" for b in bodies)
        assert all(b["readOnly"] is True for b in bodies)
        assert all(b["managementEvent"] is False for b in bodies)  # data event
        assert all(b.get("storage_object_download_marker") for b in bodies)
        assert all(b["dataset"] == _DATASET for b in bodies)
        assert all(b.get("bucket_name") for b in bodies)

    def test_storage_object_download_gcp_burst_objects_get(self, monkeypatch):
        # Alert: "An identity performed a suspicious download of multiple cloud
        # storage objects" (GCP variant).
        state, stub = self._run_with_stub(
            monkeypatch, provider="gcp",
            event_pattern="storage_object_download", burst_count=4,
        )
        bodies = _bodies(stub)
        assert len(bodies) == 4
        assert all(
            b["protoPayload"]["methodName"] == "storage.objects.get" for b in bodies
        )
        assert all(b["dataset"] == _DATASET for b in bodies)

    def test_secrets_dump_burst(self, monkeypatch):
        # Alert: "Suspicious secrets dump activity".
        state, stub = self._run_with_stub(
            monkeypatch, provider="aws", event_pattern="secrets_dump", burst_count=5,
        )
        bodies = _bodies(stub)
        assert len(bodies) == 5
        assert all(b["eventName"] == "GetSecretValue" for b in bodies)
        assert all(b["eventSource"] == "secretsmanager.amazonaws.com" for b in bodies)
        assert all(b.get("secrets_dump_marker") for b in bodies)

    def test_ssm_parameter_retrieval_burst(self, monkeypatch):
        # Alert: "Suspicious AWS SSM parameters retrieval activity".
        state, stub = self._run_with_stub(
            monkeypatch, provider="aws",
            event_pattern="ssm_parameter_retrieval", burst_count=7,
        )
        bodies = _bodies(stub)
        assert len(bodies) == 7
        assert all(b["eventName"] == "GetParameters" for b in bodies)
        assert all(b["eventSource"] == "ssm.amazonaws.com" for b in bodies)
        assert all(b.get("with_decryption") is True for b in bodies)
        assert all(b.get("ssm_parameter_retrieval_marker") for b in bodies)

    def test_iam_enumeration_sequence(self, monkeypatch):
        # Alert: "IAM Enumeration sequence".
        state, stub = self._run_with_stub(
            monkeypatch, provider="aws", event_pattern="iam_enumeration",
        )
        bodies = _bodies(stub)
        names = [b["eventName"] for b in bodies]
        assert names == [
            "ListUsers", "ListRoles", "ListPolicies",
            "GetAccountAuthorizationDetails",
        ]
        assert all(b["readOnly"] is True for b in bodies)
        # Default (user) identity.
        assert all(b["userIdentity"]["type"] == "IAMUser" for b in bodies)
        assert all(b.get("iam_enumeration_marker") for b in bodies)

    def test_iam_enumeration_non_user_identity(self, monkeypatch):
        # Alert: "Unusual IAM enumeration activity by a non-user Identity".
        state, stub = self._run_with_stub(
            monkeypatch, provider="aws", event_pattern="iam_enumeration_non_user",
        )
        bodies = _bodies(stub)
        assert [b["eventName"] for b in bodies] == [
            "ListUsers", "ListRoles", "ListPolicies",
            "GetAccountAuthorizationDetails",
        ]
        # The non-user identity is what the ABIOC keys on.
        assert all(b["userIdentity"]["type"] == "AssumedRole" for b in bodies)
        assert all("sessionContext" in b["userIdentity"] for b in bodies)
        assert all(b.get("non_user_identity") is True for b in bodies)
        assert all(b.get("iam_enumeration_non_user_marker") for b in bodies)

    def test_iam_enumeration_non_user_gcp_service_account_principal(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, provider="gcp", event_pattern="iam_enumeration_non_user",
        )
        bodies = _bodies(stub)
        assert all(
            "gserviceaccount.invalid"
            in b["protoPayload"]["authenticationInfo"]["principalEmail"]
            for b in bodies
        )

    def test_s3_bucket_enumeration_sequence(self, monkeypatch):
        # Alert: "AWS S3 Buckets enumeration activity".
        state, stub = self._run_with_stub(
            monkeypatch, provider="aws", event_pattern="s3_bucket_enumeration",
        )
        bodies = _bodies(stub)
        assert [b["eventName"] for b in bodies] == [
            "ListBuckets", "GetBucketAcl", "GetBucketPolicy", "ListObjects",
        ]
        assert all(b["eventSource"] == "s3.amazonaws.com" for b in bodies)
        assert all(b.get("s3_bucket_enumeration_marker") for b in bodies)

    def test_bigquery_exfiltration_foreign_project(self, monkeypatch):
        # Alert: "BigQuery table or query results exfiltrated to a foreign project".
        state, stub = self._run_with_stub(
            monkeypatch, provider="gcp", event_pattern="bigquery_exfiltration",
        )
        bodies = _bodies(stub)
        assert len(bodies) == 2
        assert all(
            b["protoPayload"]["methodName"]
            == "google.cloud.bigquery.v2.JobService.InsertJob"
            for b in bodies
        )
        assert all(b.get("foreign_project") is True for b in bodies)
        assert all(b.get("destination_project_id") == "attacker-project-999" for b in bodies)
        assert all(b.get("bigquery_exfiltration_marker") for b in bodies)

    def test_service_account_impersonation_gcp(self, monkeypatch):
        # Alert: "GCP service account impersonation attempt".
        state, stub = self._run_with_stub(
            monkeypatch, provider="gcp", event_pattern="service_account_impersonation",
        )
        bodies = _bodies(stub)
        assert len(bodies) == 1
        assert (
            bodies[0]["protoPayload"]["methodName"]
            == "google.iam.credentials.v1.IAMCredentials.GenerateAccessToken"
        )
        assert bodies[0].get("target_service_account", "").endswith(
            "gserviceaccount.invalid"
        )
        assert bodies[0].get("service_account_impersonation_marker") is True

    def test_service_account_impersonation_aws_analog_assumerole(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, provider="aws", event_pattern="service_account_impersonation",
        )
        bodies = _bodies(stub)
        assert bodies[0]["eventName"] == "AssumeRole"
        assert bodies[0]["eventSource"] == "sts.amazonaws.com"

    def test_dry_run_new_pattern_no_network(self, monkeypatch):
        def _boom(self, params):  # noqa: ARG001
            raise AssertionError("client should not be built in dry-run")
        monkeypatch.setattr(CloudAuditEmitter, "_build_client", _boom)
        campaign = _campaign(dry_run=True, event_pattern="bigquery_exfiltration",
                             provider="gcp")
        state = _run(CampaignExecutor(audit=AuditLogger(file_path=None)).execute(campaign))
        sr = state.step_results[0]
        assert sr.status == "success"
        assert sr.detail["dry_run"] is True
        assert sr.bytes_sent == 0

    def test_no_new_pattern_trips_c2_gate(self):
        # Every technique the expanded plugin declares must stay out of the
        # C2 tactic — the plugin must never require c2_authorized.
        from eal_simulator.safety import is_c2_plugin
        assert is_c2_plugin(
            "cloud_audit_emitter", CloudAuditEmitter.Meta.mitre_techniques,
        ) is False

    def test_auth_token_never_leaks_into_new_pattern_bodies(self, monkeypatch):
        # The ingestion token is a client-level header; it must never appear in
        # any emitted record body.
        state, stub = self._run_with_stub(
            monkeypatch, provider="aws", event_pattern="secrets_dump",
            burst_count=3, auth_token="super-secret-hec-token",
        )
        for r in stub.requests:
            content = r["content"]
            text = content.decode("utf-8") if isinstance(content, (bytes, bytearray)) else content
            assert "super-secret-hec-token" not in text


# --------------------------------------------------------------------------
# Injectable transport — auth header (exercised on the real _build_client)
# --------------------------------------------------------------------------


class TestAuthHeader:
    def test_no_auth_header_when_token_absent(self):
        emitter = CloudAuditEmitter()
        emitter._verify_tls = False
        params = CloudAuditEmitterParams.model_validate({
            "collector_url": _DEFAULT_COLLECTOR,
        })
        client = emitter._build_client(params)
        try:
            assert "authorization" not in {k.lower() for k in client.headers}
        finally:
            _run(client.aclose())

    def test_bearer_auth_header_set_from_token(self):
        emitter = CloudAuditEmitter()
        emitter._verify_tls = False
        params = CloudAuditEmitterParams.model_validate({
            "collector_url": _DEFAULT_COLLECTOR,
            "auth_token": "tok-123",
        })
        client = emitter._build_client(params)
        try:
            assert client.headers["authorization"] == "Bearer tok-123"
        finally:
            _run(client.aclose())

    def test_raw_token_when_scheme_empty(self):
        emitter = CloudAuditEmitter()
        emitter._verify_tls = False
        params = CloudAuditEmitterParams.model_validate({
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

    def test_content_type_ndjson_when_batch(self):
        emitter = CloudAuditEmitter()
        emitter._verify_tls = False
        params = CloudAuditEmitterParams.model_validate({
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
        assert reg.has("cloud_audit_emitter")

    def test_metadata_lists_analytics_and_abioc_targets(self):
        from eal_simulator import get_default_registry

        meta = get_default_registry().get("cloud_audit_emitter").metadata()
        assert any("Analytics" in t for t in meta["eal_targets"])
        assert any("ABIOC" in t for t in meta["eal_targets"])
        assert "T1098" in meta["mitre_techniques"]
        props = meta["params_schema"]["properties"]
        # Shared base fields + net-new ingestion door are exposed in the schema.
        assert "collector_url" in props
        assert "event_pattern" in props
        assert "auth_token" in props
        assert "compress" in props
        assert "batch" in props

    def test_base_params_shared_fields_present(self):
        # The subclass inherits the exemplar field-set verbatim.
        fields = CloudAuditEmitterParams.model_fields
        for name in AnalyticsEmitterParams.model_fields:
            assert name in fields

    def test_not_c2_classified(self):
        # Cloud/identity techniques must NOT trip the C2 gate — only
        # simulation_authorized is required, not c2_authorized.
        from eal_simulator.safety import is_c2_plugin

        assert is_c2_plugin(
            "cloud_audit_emitter", CloudAuditEmitter.Meta.mitre_techniques,
        ) is False

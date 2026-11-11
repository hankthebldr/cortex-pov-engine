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
import hashlib
import itertools
import json
import types
from datetime import datetime, timezone
from typing import Any

import httpx
import pytest

from eal_simulator import AuditLogger, Campaign, CampaignExecutor
from eal_simulator.analytics_emitter import AnalyticsEmitterParams
from eal_simulator.plugins import cloud_audit_emitter as cae
from eal_simulator.plugins.cloud_audit_emitter import (
    CloudAuditEmitter,
    CloudAuditEmitterParams,
    _ANOMALOUS_IP,
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
        assert p.auth_scheme == ""  # XSIAM HTTP collector wants the bare key
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

    def test_bare_key_auth_header_set_from_token(self):
        emitter = CloudAuditEmitter()
        emitter._verify_tls = False
        params = CloudAuditEmitterParams.model_validate({
            "collector_url": _DEFAULT_COLLECTOR,
            "auth_token": "tok-123",
        })
        client = emitter._build_client(params)
        try:
            assert client.headers["authorization"] == "tok-123"
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


# --------------------------------------------------------------------------
# Report-indicator source-IP override
# --------------------------------------------------------------------------

_OVR_IP = "203.0.113.77"


def _ovr_events(provider, pattern, **kw):
    p = CloudAuditEmitterParams(
        collector_url=_DEFAULT_COLLECTOR, provider=provider,
        event_pattern=pattern, burst_count=4, **kw,
    )
    return CloudAuditEmitter().build_events(p, sim_run_id="cortexsim-x-i1-aa", iteration=1)


def _caller_ip(provider, evt):
    if provider == "aws":
        return evt["sourceIPAddress"]
    return evt["protoPayload"]["requestMetadata"]["callerIp"]


class TestSourceIpOverride:
    def test_default_none(self):
        assert CloudAuditEmitterParams(collector_url=_DEFAULT_COLLECTOR).source_ip is None

    @pytest.mark.parametrize("provider,pattern", [
        ("aws", "storage_object_download"),
        ("aws", "console_login_anomaly"),
        ("gcp", "storage_object_download"),
        ("gcp", "secrets_dump"),
    ])
    def test_override_on_every_record(self, provider, pattern):
        events = _ovr_events(provider, pattern, source_ip=_OVR_IP)
        assert len(events) > 1
        assert {_caller_ip(provider, e) for e in events} == {_OVR_IP}

    @pytest.mark.parametrize("provider", ["aws", "gcp"])
    def test_unset_keeps_constant(self, provider):
        events = _ovr_events(provider, "storage_object_download")
        assert {_caller_ip(provider, e) for e in events} == {_ANOMALOUS_IP}

    @pytest.mark.parametrize("bad", ["10.0.0", "300.1.1.1", "a.b.c.d", "10.0.0.1.2", "::1", ""])
    def test_source_ip_must_be_dotted_quad(self, bad):
        with pytest.raises(ValueError):
            CloudAuditEmitterParams(collector_url=_DEFAULT_COLLECTOR, source_ip=bad)

    def test_rfc1918_allowed(self):
        p = CloudAuditEmitterParams(collector_url=_DEFAULT_COLLECTOR, source_ip="10.9.9.9")
        assert p.source_ip == "10.9.9.9"


# --------------------------------------------------------------------------
# The eleven pre-existing presets are frozen, field for field
# --------------------------------------------------------------------------
#
# The logging-service presets below extended the record builders (request
# parameters, a per-call resource name, a log name). That change is ADDITIVE
# ONLY: a scenario that drives one of the eleven original presets must post the
# bytes it posted before. This block is the permanent proof.
#
# How the table was captured — and how to re-derive it: check out the plugin at
# origin/dev 8c90739 (blob 888365f, the commit this branch was cut from, i.e.
# BEFORE any logging-service preset existed), then for every preset x provider
# render ``build_events`` and the canary-planted ``records_for`` with
#   * ``datetime.now()`` frozen to 2026-01-01T00:00:00+00:00,
#   * ``secrets.token_hex(n)`` returning ``"0" * 2n``,
#   * ``burst_count=3``, ``sim_run_id="cortexsim-golden-i1-0000"``, iteration 1,
# and take ``sha256(json.dumps(records, separators=(",", ":")))``. Key order is
# NOT normalised, so a reordered record fails as loudly as a changed value.

_LEGACY_PATTERNS = (
    "storage_object_download", "secrets_dump", "ssm_parameter_retrieval",
    "iam_enumeration", "iam_enumeration_non_user", "s3_bucket_enumeration",
    "bigquery_exfiltration", "service_account_impersonation",
    "iam_privilege_escalation", "s3_public_exposure", "console_login_anomaly",
)
_PROVIDERS = ("aws", "gcp")
_GOLDEN_RUN_ID = "cortexsim-golden-i1-0000"
_GOLDEN_CANARY = "csim-golden-0001"

# (pattern, provider) -> (record count, sha256 build_events, sha256 canary-planted)
_LEGACY_GOLDEN: dict[tuple[str, str], tuple[int, str, str]] = {
    ("bigquery_exfiltration", "aws"): (
        2, "3e469f826164a0c447a70aca233f8d27a79596df0836acad061ce33fb95d2021",
        "98eaabcf27d94c7ee84feef97fb0e48e4bcf91c2c64b46164e616b5f78fb6431"),
    ("bigquery_exfiltration", "gcp"): (
        2, "c06a221f1c3e9f9a3cdc9be87b3f03e408fe73a55bef348ec16d0fcf2ecc9dbf",
        "a47700c4b5529c7346929741d0430f24132a0806e8e38d91527385ad10b91012"),
    ("console_login_anomaly", "aws"): (
        4, "ee494cac7d68b71f6e727c7187e6d43d72ed50d8694086b35d94f5e15193c35c",
        "a87cf1ed2c3e97a4c68ab1bb20fa0e4e3455aeda93e5c3a177865e0a887989fc"),
    ("console_login_anomaly", "gcp"): (
        4, "c6d33ffa796009bfdee930b9ed3a1fb667cf392d5663c1cf064d31662c186a22",
        "6cca814e9bd4249789c4f1fc3828e232d10ca6dd880f325b5bee41370f885558"),
    ("iam_enumeration", "aws"): (
        4, "4e9eae920366b78853cef9e521b31276ce3ad9ae239f04647b9e1dd1eda41bbc",
        "d9a8ad86f89c3805bc45bbff40978c0c6c256753f4aa260f7030f9c5c587f7bb"),
    ("iam_enumeration", "gcp"): (
        4, "2d4316bcf1c9aaab7f435f0edb213ece0df58ee160cb294e1833ebc53d9182ce",
        "f68779ac1f17dc507242bff8ae40b6b5dd4ae079081f240162fb699618cf74e5"),
    ("iam_enumeration_non_user", "aws"): (
        4, "87981ac1b42ade5e54f0d9938a066c31fd5803b36c259b25532734e396871617",
        "66568c7f197b8b932d5a5b4a33781688b4fe934d32509216b9d96efcfb091e5b"),
    ("iam_enumeration_non_user", "gcp"): (
        4, "9d164e096f89cb48d11660c73c7133d03826c53ed61ec1681f8de8f5c32dd97a",
        "a99e6bad3ab1f482174de6e2f157928a24ac5d263ed9a7aeb9a2009109280c17"),
    ("iam_privilege_escalation", "aws"): (
        2, "e31f389a58d87a0f76d25b4209dbd0bc0f5b57682dc91a26b66a33230136ddfe",
        "0b591c49d1a495047f46faa23277619bfbfeb78227c54638dce6056045134ed3"),
    ("iam_privilege_escalation", "gcp"): (
        2, "9ce4b2697927683c4c960bc1a0d0739f7b4c10b85167a42f13d54ce2b2436832",
        "470316352a33635bce1e49647503e52178ac99827f40aece13de7cfbb0b16eb3"),
    ("s3_bucket_enumeration", "aws"): (
        4, "5989244c8e58bfcbef2cdfd248dba7364806c04dbdfd98035dc6f9195b7df2bd",
        "20f1fb76c46c8d132bdd98d1545d0958c1344b8beee3a8e17d046077a657aa7f"),
    ("s3_bucket_enumeration", "gcp"): (
        4, "f52aa5a773b5588e4823d3943178964be386413e5d01737f20128222dcf0674f",
        "5c6dcfb65a1ce92ad523858ab8ed3f62ea8c826b98bf69f924e9cf65efbf02d1"),
    ("s3_public_exposure", "aws"): (
        1, "5902bd896b46ea269ab364fd329554493bf7db3d72571ae763c9356441b28dfd",
        "f286565cf87890cdd92929c1d075b3f0212653810a4a35f000128ea2d627d3cc"),
    ("s3_public_exposure", "gcp"): (
        1, "a8bcfb9621cec2be3d0c71f6cd663e50e519ce178dce405d2eec460dc0addd63",
        "b58fbbc65934778a5e7e907e2b4d03e0e41a15f2b19ca8bd1a3daa1824382873"),
    ("secrets_dump", "aws"): (
        3, "98822ec6e243a2e5655f056b3fc09094b9d067351e972b36eb6005f2093d9ab6",
        "0e93244b90ba814c23f41aa93ec8192bbf59629f261c03ab2c9ff5e09baf80e3"),
    ("secrets_dump", "gcp"): (
        3, "dda6a13b8c5985790cd0ca43fd32d7c58aca28213ba878b27af98fe50ab590a3",
        "395b2f7000719777e2f5b6924b6485b882bfee086a8e47e47c9ac98dc0ba8a29"),
    ("service_account_impersonation", "aws"): (
        1, "8881b78c377f4879273b7f566bbefe543e5f9725cd0e8411b9331145751633d7",
        "10d488146c57c0dad2d840842b0edbfbabb50cd9601f79c46e27bcb12fe1c168"),
    ("service_account_impersonation", "gcp"): (
        1, "6447ec2fd6737f33b368776e8f43cefe46f89413d6570ae1285c2bff11ba5400",
        "834c7bcf99c2b8cb333bc53d529f88fc2e921f403fe6fccd1fb8baba1f238386"),
    ("ssm_parameter_retrieval", "aws"): (
        3, "f048618baca0ee505ef03fc1b7acb34439ff2cf9f8d223b60e4b9aa4cfcbfe0e",
        "62f2da7527c0a3016cbd29e6aa09271641592020d548b0f7f2ee7ab8773c245b"),
    ("ssm_parameter_retrieval", "gcp"): (
        3, "f28921385358921a0cb1120185de8fabb83dac0edc1cf6197247e1db8db914e3",
        "328f2d0f10db89a4d739b086609db1b24a6bf52f72476fc18b46a41670c80dca"),
    ("storage_object_download", "aws"): (
        3, "fdce6aa95ece3374c1e34c8a2f984a78c4854c9ede92ae7f30d2bfd4121f4d36",
        "862360b86ebc770ad062854a1d816e75efeb68ab495287906d09d9c70af87f47"),
    ("storage_object_download", "gcp"): (
        3, "b58d73c4f8607dff9932c3f29ee44881d7c1fcb1839ca3e69e5f0cf03a37812e",
        "04abe6e04b023b3f4ea93da798710739d9b575ca64868d4599b91b1c25dcaa70"),
}

# Every top-level key any of the eleven original presets wrote at origin/dev.
_LEGACY_AWS_KEYS = frozenset({
    "awsRegion", "bigquery_exfiltration_marker", "bucket_name",
    "console_login_anomaly_marker", "cortexsim_run_id", "dataset",
    "destination_project_id", "enumeration_scope", "errorCode", "eventName",
    "eventSource", "eventTime", "eventType", "eventVersion", "foreign_project",
    "iam_enumeration_marker", "iam_enumeration_non_user_marker", "login_result",
    "managementEvent", "mfa_used", "non_user_identity", "parameter_path",
    "privilege_escalation_marker", "public_exposure_marker", "readOnly",
    "recipientAccountId", "s3_bucket_enumeration_marker", "secret_scope",
    "secrets_dump_marker", "service_account_impersonation_marker",
    "sourceIPAddress", "ssm_parameter_retrieval_marker",
    "storage_object_download_marker", "target_service_account", "userAgent",
    "userIdentity", "with_decryption",
})
_LEGACY_GCP_KEYS = frozenset({
    "bigquery_exfiltration_marker", "bucket_name", "console_login_anomaly_marker",
    "cortexsim_run_id", "dataset", "destination_project_id", "enumeration_scope",
    "foreign_project", "iam_enumeration_marker", "iam_enumeration_non_user_marker",
    "insertId", "login_result", "mfa_used", "non_user_identity", "parameter_path",
    "privilege_escalation_marker", "protoPayload", "public_exposure_marker",
    "resource", "s3_bucket_enumeration_marker", "secret_scope",
    "secrets_dump_marker", "service_account_impersonation_marker", "severity",
    "ssm_parameter_retrieval_marker", "storage_object_download_marker",
    "target_service_account", "timestamp", "with_decryption",
})
_LEGACY_GCP_PROTOPAYLOAD_KEYS = frozenset({
    "@type", "authenticationInfo", "methodName", "requestMetadata",
    "resourceName", "serviceName", "status",
})


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):  # noqa: ARG003
        return datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def _freeze(monkeypatch) -> None:
    """Pin the two volatile inputs of the record builders (clock + insertId)."""
    monkeypatch.setattr(cae, "datetime", _FrozenDatetime)
    monkeypatch.setattr(
        cae, "secrets", types.SimpleNamespace(token_hex=lambda n: "0" * (2 * n)),
    )


def _frozen_records(monkeypatch, provider, pattern, *, canary=None, burst_count=3):
    _freeze(monkeypatch)
    extra = {"canary_token": canary} if canary else {}
    params = CloudAuditEmitterParams(
        collector_url=_DEFAULT_COLLECTOR, provider=provider,
        event_pattern=pattern, burst_count=burst_count, **extra,
    )
    emitter = CloudAuditEmitter()
    build = emitter.records_for if canary else emitter.build_events
    return build(params, sim_run_id=_GOLDEN_RUN_ID, iteration=1)


def _digest(records) -> str:
    blob = json.dumps(records, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


_LEGACY_COMBOS = sorted(itertools.product(_LEGACY_PATTERNS, _PROVIDERS))


class TestLegacyPresetsUnchanged:
    """The eleven original presets emit the records they emitted at origin/dev."""

    def test_golden_table_covers_every_legacy_preset_and_provider(self):
        assert set(_LEGACY_GOLDEN) == set(_LEGACY_COMBOS)
        assert len(_LEGACY_GOLDEN) == 22
        # None was renamed or dropped to make room for the new ones.
        assert set(_LEGACY_PATTERNS) <= set(_EVENT_PATTERNS)

    @pytest.mark.parametrize("pattern,provider", _LEGACY_COMBOS)
    def test_build_events_is_byte_identical(self, monkeypatch, pattern, provider):
        count, plain_sha, _ = _LEGACY_GOLDEN[(pattern, provider)]
        records = _frozen_records(monkeypatch, provider, pattern)
        assert len(records) == count
        assert _digest(records) == plain_sha, (
            f"{pattern}/{provider} no longer renders the records it rendered at "
            f"origin/dev 8c90739:\n{json.dumps(records, indent=1)}"
        )

    @pytest.mark.parametrize("pattern,provider", _LEGACY_COMBOS)
    def test_canary_planted_records_are_byte_identical(self, monkeypatch, pattern, provider):
        count, _, planted_sha = _LEGACY_GOLDEN[(pattern, provider)]
        records = _frozen_records(monkeypatch, provider, pattern, canary=_GOLDEN_CANARY)
        assert len(records) == count
        assert _digest(records) == planted_sha, (
            f"{pattern}/{provider} canary-planted records changed:\n"
            f"{json.dumps(records, indent=1)}"
        )

    @pytest.mark.parametrize("pattern,provider", _LEGACY_COMBOS)
    def test_records_for_without_canary_is_build_events(self, monkeypatch, pattern, provider):
        # The wire path and the offline-bundle path both go through
        # records_for; with no canary it must add nothing to a legacy record.
        _freeze(monkeypatch)
        params = CloudAuditEmitterParams(
            collector_url=_DEFAULT_COLLECTOR, provider=provider,
            event_pattern=pattern, burst_count=3,
        )
        emitter = CloudAuditEmitter()
        via_seam = emitter.records_for(params, sim_run_id=_GOLDEN_RUN_ID, iteration=1)
        assert _digest(via_seam) == _LEGACY_GOLDEN[(pattern, provider)][1]

    def test_aws_record_literal(self, monkeypatch):
        # One fully spelled-out record per provider, so the digest table has a
        # human-readable anchor: this is the WHOLE record, nothing more.
        assert _frozen_records(monkeypatch, "aws", "s3_public_exposure") == [{
            "dataset": "cloud_audit_logs",
            "eventVersion": "1.08",
            "eventTime": "2026-01-01T00:00:00+00:00",
            "eventSource": "s3.amazonaws.com",
            "eventName": "PutBucketPolicy",
            "eventType": "AwsApiCall",
            "awsRegion": "us-east-1",
            "sourceIPAddress": "203.0.113.201",
            "userAgent": "aws-cli/2.15.0 Python/3.11",
            "userIdentity": {
                "type": "IAMUser",
                "arn": "arn:aws:iam::123456789012:user/cortexsim-canary",
                "accountId": "123456789012",
                "userName": "cortexsim-canary",
            },
            "errorCode": None,
            "readOnly": False,
            "managementEvent": True,
            "recipientAccountId": "123456789012",
            "public_exposure_marker": True,
            "cortexsim_run_id": "cortexsim-golden-i1-0000",
        }]

    def test_gcp_record_literal(self, monkeypatch):
        assert _frozen_records(monkeypatch, "gcp", "s3_public_exposure") == [{
            "dataset": "cloud_audit_logs",
            "insertId": "0000000000000000",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "severity": "NOTICE",
            "resource": {
                "type": "project",
                "labels": {"project_id": "cortexsim-canary"},
            },
            "protoPayload": {
                "@type": "type.googleapis.com/google.cloud.audit.AuditLog",
                "serviceName": "storage.googleapis.com",
                "methodName": "storage.setIamPermissions",
                "resourceName": "projects/cortexsim-canary",
                "authenticationInfo": {
                    "principalEmail": "cortexsim-canary@cortexsim-canary.invalid",
                },
                "requestMetadata": {
                    "callerIp": "203.0.113.201",
                    "callerSuppliedUserAgent": "google-cloud-sdk gcloud/470.0.0",
                },
                "status": {},
            },
            "public_exposure_marker": True,
            "cortexsim_run_id": "cortexsim-golden-i1-0000",
        }]

    @pytest.mark.parametrize("pattern", _LEGACY_PATTERNS)
    def test_legacy_records_carry_none_of_the_new_fields(self, monkeypatch, pattern):
        # Readable twin of the digests: no request parameters, no event
        # category, no log name, no flat logging hints, the fixed project-level
        # resource name — on any record of any original preset.
        for rec in _frozen_records(monkeypatch, "aws", pattern):
            assert set(rec) <= _LEGACY_AWS_KEYS, sorted(set(rec) - _LEGACY_AWS_KEYS)
        for rec in _frozen_records(monkeypatch, "gcp", pattern):
            assert set(rec) <= _LEGACY_GCP_KEYS, sorted(set(rec) - _LEGACY_GCP_KEYS)
            assert set(rec["protoPayload"]) == _LEGACY_GCP_PROTOPAYLOAD_KEYS
            assert rec["protoPayload"]["resourceName"] == "projects/cortexsim-canary"
            assert rec["resource"] == {
                "type": "project", "labels": {"project_id": "cortexsim-canary"},
            }

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
import re
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


# --------------------------------------------------------------------------
# Logging-service presets (cloud log tampering / log re-routing)
# --------------------------------------------------------------------------
#
# Every literal below is written out here rather than imported from the plugin,
# so this is a specification the plugin has to meet, not a mirror of it. Event
# names, event sources, request field names and the management/data split come
# from the provider documentation cited in the plugin's module docstring — not
# from CLI spellings (``stop-logging``) or IAM permission names
# (``logging.sinks.update``).

_NEW_PATTERNS = (
    "log_stop", "log_destination_delete", "log_router_delete", "log_key_swap",
    "log_object_overwrite", "log_router_create", "log_router_redirect",
    "stream_bucket_delete",
)
_NEW_COMBOS = sorted(itertools.product(_NEW_PATTERNS, _PROVIDERS))
# (pattern, provider) pairs that fan out: burst_count object deletes, then ONE
# bucket delete. GCP log_destination_delete is a single Cloud Logging call.
_BURST_COMBOS = (
    ("log_destination_delete", "aws"),
    ("stream_bucket_delete", "aws"),
    ("stream_bucket_delete", "gcp"),
)

_RUN_ID = "cortexsim-x-i1-aa"
_AWS_PRINCIPAL = "arn:aws:iam::123456789012:user/cortexsim-canary"
_GCP_PRINCIPAL = "cortexsim-canary@cortexsim-canary.invalid"

_S3 = "s3.amazonaws.com"
_CLOUDTRAIL = "cloudtrail.amazonaws.com"
_LOGGING = "logging.googleapis.com"
_GCS = "storage.googleapis.com"
_CFG = "google.logging.v2.ConfigServiceV2."
_REQ = "type.googleapis.com/google.logging.v2."

_TRAIL = "cortexsim-canary-trail"
_TRAIL_ARN = "arn:aws:cloudtrail:us-east-1:123456789012:trail/cortexsim-canary-trail"
_SHADOW_TRAIL = "cortexsim-canary-shadow-trail"
_SHADOW_TRAIL_ARN = (
    "arn:aws:cloudtrail:us-east-1:123456789012:trail/cortexsim-canary-shadow-trail"
)
_TRAIL_BUCKET = "cortexsim-canary-cloudtrail-logs"
_STREAM_BUCKET = "cortexsim-canary-stream-archive"
_ATTACKER_S3_BUCKET = "cortexsim-canary-attacker-logs-999"
_EXTERNAL_KMS_KEY = "arn:aws:kms:us-east-1:444455556666:alias/cortexsim-canary-external-key"

_SINK = "projects/cortexsim-canary/sinks/cortexsim-canary-sink"
_SHADOW_SINK = "projects/cortexsim-canary/sinks/cortexsim-canary-shadow-sink"
_SINK_DESTINATION = "storage.googleapis.com/cortexsim-canary-log-archive"
_ATTACKER_SINK_DESTINATION = "storage.googleapis.com/cortexsim-canary-attacker-log-drop"
_LOG_BUCKET = (
    "projects/cortexsim-canary/locations/global/buckets/cortexsim-canary-log-bucket"
)
_EXTERNAL_CMEK_KEY = (
    "projects/attacker-project-999/locations/global/keyRings/"
    "cortexsim-canary-ring/cryptoKeys/cortexsim-canary-key"
)
_GCS_BUCKET = "cortexsim-canary-log-archive"
_GCS_BUCKET_RESOURCE = "projects/_/buckets/cortexsim-canary-log-archive"

_PROJECT_RESOURCE = {"type": "project", "labels": {"project_id": "cortexsim-canary"}}
_GCS_RESOURCE = {
    "type": "gcs_bucket",
    "labels": {
        "project_id": "cortexsim-canary",
        "bucket_name": "cortexsim-canary-log-archive",
        "location": "us",
    },
}

# Flat hint keys that only SOME calls carry; a record must not carry one it has
# no value for (a null hint reads as a value in XQL).
_OPTIONAL_HINTS = ("log_object_key", "log_destination", "kms_key_id", "log_disabled")


def _trail_log_key(n: int) -> str:
    return (
        "AWSLogs/123456789012/CloudTrail/us-east-1/2026/01/01/"
        f"cortexsim-canary-{n:04d}.json.gz"
    )


def _stream_key(n: int) -> str:
    return f"replica/2026/01/01/cortexsim-canary-{n:04d}.json.gz"


def _gcs_object(n: int) -> str:
    return f"cloudaudit.googleapis.com/activity/2026/01/01/cortexsim-canary-{n:04d}.json"


def _aws_call(name, source, request, *, read_only=False, management=True, **hints):
    return {
        "name": name, "source": source, "request": request,
        "read_only": read_only, "management": management, "hints": hints,
    }


def _gcp_call(method, service, resource_name, request=None, *,
              admin_activity=True, resource=None, **hints):
    return {
        "name": method, "source": service, "resource_name": resource_name,
        "request": request, "admin_activity": admin_activity,
        "resource": resource or _PROJECT_RESOURCE, "hints": hints,
    }


def _s3_empty_then_delete(bucket, key_fn, burst):
    arn = f"arn:aws:s3:::{bucket}"
    return [
        _aws_call("DeleteObject", _S3, {"bucketName": bucket, "key": key_fn(n)},
                  management=False, log_resource_name=arn, log_object_key=key_fn(n))
        for n in range(1, burst + 1)
    ] + [
        _aws_call("DeleteBucket", _S3, {"bucketName": bucket}, log_resource_name=arn),
    ]


def _gcs_object_call(method, n):
    # Object reads and writes are Data Access entries (DATA_READ / DATA_WRITE).
    return _gcp_call(
        method, _GCS, f"{_GCS_BUCKET_RESOURCE}/objects/{_gcs_object(n)}",
        admin_activity=False, resource=_GCS_RESOURCE,
        log_resource_name=_GCS_BUCKET_RESOURCE, log_object_key=_gcs_object(n),
    )


def _expected(pattern: str, provider: str, burst: int) -> list[dict[str, Any]]:
    """The calls a preset must emit, in order, for one provider."""
    trail_bucket_arn = f"arn:aws:s3:::{_TRAIL_BUCKET}"
    table = {
        # -- 1. stop logging ------------------------------------------------
        ("log_stop", "aws"): [
            _aws_call("StopLogging", _CLOUDTRAIL, {"name": _TRAIL_ARN},
                      log_resource_name=_TRAIL_ARN),
        ],
        ("log_stop", "gcp"): [
            _gcp_call(_CFG + "UpdateSink", _LOGGING, _SINK, {
                "@type": _REQ + "UpdateSinkRequest",
                "sinkName": _SINK,
                "sink": {
                    "name": "cortexsim-canary-sink",
                    "destination": _SINK_DESTINATION,
                    "disabled": True,
                },
                "updateMask": "disabled",
            }, log_resource_name=_SINK, log_disabled=True),
        ],
        # -- 2. delete the log storage destination --------------------------
        ("log_destination_delete", "aws"): _s3_empty_then_delete(
            _TRAIL_BUCKET, _trail_log_key, burst),
        ("log_destination_delete", "gcp"): [
            _gcp_call(_CFG + "DeleteBucket", _LOGGING, _LOG_BUCKET, {
                "@type": _REQ + "DeleteBucketRequest",
                "name": _LOG_BUCKET,
            }, log_resource_name=_LOG_BUCKET),
        ],
        # -- 3. delete the log router ---------------------------------------
        ("log_router_delete", "aws"): [
            _aws_call("DeleteTrail", _CLOUDTRAIL, {"name": _TRAIL_ARN},
                      log_resource_name=_TRAIL_ARN),
        ],
        ("log_router_delete", "gcp"): [
            _gcp_call(_CFG + "DeleteSink", _LOGGING, _SINK, {
                "@type": _REQ + "DeleteSinkRequest",
                "sinkName": _SINK,
            }, log_resource_name=_SINK),
        ],
        # -- 4. swap the encryption key for an external one -----------------
        ("log_key_swap", "aws"): [
            _aws_call("UpdateTrail", _CLOUDTRAIL,
                      {"name": _TRAIL, "kmsKeyId": _EXTERNAL_KMS_KEY},
                      log_resource_name=_TRAIL_ARN, kms_key_id=_EXTERNAL_KMS_KEY),
        ],
        ("log_key_swap", "gcp"): [
            _gcp_call(_CFG + "UpdateBucket", _LOGGING, _LOG_BUCKET, {
                "@type": _REQ + "UpdateBucketRequest",
                "name": _LOG_BUCKET,
                "bucket": {"cmekSettings": {"kmsKeyName": _EXTERNAL_CMEK_KEY}},
                "updateMask": "cmekSettings",
            }, log_resource_name=_LOG_BUCKET, kms_key_id=_EXTERNAL_CMEK_KEY),
        ],
        # -- 5. log poisoning: read a stored log object, overwrite it -------
        ("log_object_overwrite", "aws"): [
            _aws_call("GetObject", _S3,
                      {"bucketName": _TRAIL_BUCKET, "key": _trail_log_key(1)},
                      read_only=True, management=False,
                      log_resource_name=trail_bucket_arn,
                      log_object_key=_trail_log_key(1)),
            _aws_call("PutObject", _S3,
                      {"bucketName": _TRAIL_BUCKET, "key": _trail_log_key(1)},
                      read_only=False, management=False,
                      log_resource_name=trail_bucket_arn,
                      log_object_key=_trail_log_key(1)),
        ],
        ("log_object_overwrite", "gcp"): [
            _gcs_object_call("storage.objects.get", 1),
            _gcs_object_call("storage.objects.create", 1),
        ],
        # -- 6. a second log route to attacker storage ----------------------
        ("log_router_create", "aws"): [
            _aws_call("CreateTrail", _CLOUDTRAIL, {
                "name": _SHADOW_TRAIL,
                "s3BucketName": _ATTACKER_S3_BUCKET,
                "isMultiRegionTrail": True,
            }, log_resource_name=_SHADOW_TRAIL_ARN, log_destination=_ATTACKER_S3_BUCKET),
        ],
        ("log_router_create", "gcp"): [
            _gcp_call(_CFG + "CreateSink", _LOGGING, _SHADOW_SINK, {
                "@type": _REQ + "CreateSinkRequest",
                "parent": "projects/cortexsim-canary",
                "sink": {
                    "name": "cortexsim-canary-shadow-sink",
                    "destination": _ATTACKER_SINK_DESTINATION,
                },
                "uniqueWriterIdentity": True,
            }, log_resource_name=_SHADOW_SINK, log_destination=_ATTACKER_SINK_DESTINATION),
        ],
        # -- 7. redirect the existing route ---------------------------------
        ("log_router_redirect", "aws"): [
            _aws_call("UpdateTrail", _CLOUDTRAIL,
                      {"name": _TRAIL, "s3BucketName": _ATTACKER_S3_BUCKET},
                      log_resource_name=_TRAIL_ARN, log_destination=_ATTACKER_S3_BUCKET),
        ],
        ("log_router_redirect", "gcp"): [
            _gcp_call(_CFG + "UpdateSink", _LOGGING, _SINK, {
                "@type": _REQ + "UpdateSinkRequest",
                "sinkName": _SINK,
                "sink": {
                    "name": "cortexsim-canary-sink",
                    "destination": _ATTACKER_SINK_DESTINATION,
                },
                "updateMask": "destination",
                "uniqueWriterIdentity": True,
            }, log_resource_name=_SINK, log_destination=_ATTACKER_SINK_DESTINATION),
        ],
        # -- 8. empty and delete the bucket a data stream writes to ---------
        ("stream_bucket_delete", "aws"): _s3_empty_then_delete(
            _STREAM_BUCKET, _stream_key, burst),
        ("stream_bucket_delete", "gcp"): [
            _gcs_object_call("storage.objects.delete", n) for n in range(1, burst + 1)
        ] + [
            _gcp_call("storage.buckets.delete", _GCS, _GCS_BUCKET_RESOURCE,
                      admin_activity=True, resource=_GCS_RESOURCE,
                      log_resource_name=_GCS_BUCKET_RESOURCE),
        ],
    }
    return table[(pattern, provider)]


def _render(provider, pattern, *, burst_count=4, via_seam=False, **kw):
    params = CloudAuditEmitterParams(
        collector_url=_DEFAULT_COLLECTOR, provider=provider,
        event_pattern=pattern, burst_count=burst_count, **kw,
    )
    emitter = CloudAuditEmitter()
    build = emitter.records_for if via_seam else emitter.build_events
    return build(params, sim_run_id=_RUN_ID, iteration=1)


def _assert_hints(rec, provider, exp, principal):
    assert rec["cloud_provider"] == provider
    assert rec["api_operation"] == exp["name"]
    assert rec["principal_id"] == principal
    for key, value in exp["hints"].items():
        assert rec[key] == value, key
        assert type(rec[key]) is type(value), key
    for key in _OPTIONAL_HINTS:
        if key not in exp["hints"]:
            assert key not in rec, f"{key} must be absent on {exp['name']}"


def _assert_aws_record(rec, exp, marker):
    assert rec["dataset"] == "cloud_audit_logs"
    assert rec["eventName"] == exp["name"]
    assert rec["eventSource"] == exp["source"]
    assert rec["eventType"] == "AwsApiCall"
    # Exact request parameters: keys AND values, nothing extra.
    assert rec["requestParameters"] == exp["request"]
    assert rec["readOnly"] is exp["read_only"]
    assert rec["managementEvent"] is exp["management"]
    assert rec["eventCategory"] == ("Management" if exp["management"] else "Data")
    assert rec["errorCode"] is None
    assert rec["userIdentity"]["type"] == "IAMUser"
    assert rec["userIdentity"]["arn"] == _AWS_PRINCIPAL
    assert rec["recipientAccountId"] == "123456789012"
    assert rec[marker] is True
    assert rec["cortexsim_run_id"] == _RUN_ID
    _assert_hints(rec, "aws", exp, _AWS_PRINCIPAL)


def _assert_gcp_record(rec, exp, marker):
    payload = rec["protoPayload"]
    assert rec["dataset"] == "cloud_audit_logs"
    assert payload["@type"] == "type.googleapis.com/google.cloud.audit.AuditLog"
    assert payload["methodName"] == exp["name"]
    assert payload["serviceName"] == exp["source"]
    assert payload["resourceName"] == exp["resource_name"]
    if exp["request"] is None:
        # Cloud Storage writes request/response only under detailed audit
        # logging mode, which is off by default — so no request block.
        assert "request" not in payload
    else:
        assert payload["request"] == exp["request"]
    # Admin Activity vs Data Access is the GCP management/data split.
    log_id = "activity" if exp["admin_activity"] else "data_access"
    assert rec["logName"] == (
        f"projects/cortexsim-canary/logs/cloudaudit.googleapis.com%2F{log_id}"
    )
    assert rec["resource"] == exp["resource"]
    assert payload["authenticationInfo"]["principalEmail"] == _GCP_PRINCIPAL
    assert payload["status"] == {}
    assert rec["severity"] == "NOTICE"
    assert rec[marker] is True
    assert rec["cortexsim_run_id"] == _RUN_ID
    _assert_hints(rec, "gcp", exp, _GCP_PRINCIPAL)


_ASSERT_RECORD = {"aws": _assert_aws_record, "gcp": _assert_gcp_record}


def _op(provider, rec):
    return rec["eventName"] if provider == "aws" else rec["protoPayload"]["methodName"]


def _walk(obj, path=""):
    """Yield (path, key, value) for every key in a nested record."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield path, k, v
            yield from _walk(v, f"{path}.{k}" if path else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk(v, f"{path}[{i}]")


# Value shapes that would mean a credential leaked into a record.
_CREDENTIAL_SHAPES = (
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),                    # AWS access key id
    re.compile(r"(?<![A-Za-z0-9/+=])[A-Za-z0-9/+=]{40}(?![A-Za-z0-9/+=])"),  # AWS secret key
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),               # PEM private key
    re.compile(r"\bya29\.[0-9A-Za-z_-]+"),                           # Google OAuth token
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),                        # Google API key
    re.compile(r"\beyJ[0-9A-Za-z_-]{8,}\.[0-9A-Za-z_-]{8,}\."),      # JWT
    re.compile(r"\b(?:ghp|gho|ghs|github_pat)_[0-9A-Za-z_]{20,}"),   # GitHub token
    re.compile(r"\bxox[abprs]-[0-9A-Za-z-]{10,}"),                   # Slack token
    re.compile(r"(?i)\bbearer\s+[0-9a-z._~+/-]{16,}"),               # bearer header
)
# Key names (lower-cased, separators dropped) that would hold a credential.
_CREDENTIAL_KEY_SUFFIXES = (
    "accesskeyid", "secretaccesskey", "sessiontoken", "token", "password",
    "passwd", "secret", "privatekey", "apikey", "authorization", "credential",
    "credentials",
)
# Researcher lab values that appear in the two posts' figures. They are not
# indicators and must never surface as attributes of an emitted record.
_POST_LAB_VALUES = (
    "sink-blog-simulation", "log-bucket-blog-simulation",
    "kubernetes-training-project", "victim-sink", "victim-bucket-for-logging",
    "sensitive-bucket-for-backup-purposes", "modified-to-being-undetected",
    "1.1.1.1",
)


class TestLoggingServicePresets:
    """Eight presets for cloud log tampering and log re-routing."""

    def _run_with_stub(self, monkeypatch, *, status_code=202, **campaign_kw):
        stub = _RecordingClient(status_code=status_code)
        monkeypatch.setattr(
            CloudAuditEmitter, "_build_client", lambda self, params: stub,
        )
        campaign = _campaign(**campaign_kw)
        executor = CampaignExecutor(audit=AuditLogger(file_path=None))
        state = _run(executor.execute(campaign))
        return state, stub

    # -- registration -----------------------------------------------------

    def test_all_eight_presets_registered(self):
        assert set(_NEW_PATTERNS) <= set(_EVENT_PATTERNS)
        # Exactly the eleven originals plus these eight — nothing slipped in.
        assert set(_EVENT_PATTERNS) == set(_LEGACY_PATTERNS) | set(_NEW_PATTERNS)
        assert len(_EVENT_PATTERNS) == 19

    @pytest.mark.parametrize("bad", [
        "log_blind",                # not a preset
        "stop-logging",             # AWS CLI spelling
        "delete-trail",             # AWS CLI spelling
        "StopLogging",              # an API event name is not a preset name
        "logging.sinks.update",     # an IAM permission is not a preset name
        "put_event_selectors",      # not in either post; deliberately not built
        "azure_diagnostic_delete",  # no Azure provider
    ])
    def test_unknown_event_pattern_still_raises(self, bad):
        with pytest.raises(Exception, match="event_pattern must be one of"):
            CloudAuditEmitterParams.model_validate({
                "collector_url": _DEFAULT_COLLECTOR, "event_pattern": bad,
            })

    @pytest.mark.parametrize("pattern", _NEW_PATTERNS)
    def test_new_preset_refuses_unknown_provider(self, pattern):
        with pytest.raises(Exception, match="provider must be one of"):
            CloudAuditEmitterParams.model_validate({
                "collector_url": _DEFAULT_COLLECTOR, "provider": "azure",
                "event_pattern": pattern,
            })

    # -- the records, call by call ----------------------------------------

    @pytest.mark.parametrize("pattern,provider", _NEW_COMBOS)
    def test_records_match_provider_documentation(self, pattern, provider):
        # Exact event name, event source, request-parameter keys and values,
        # read-only flag, management/data class, resource name and flat hints
        # for every record, in order.
        burst = 4
        records = _render(provider, pattern, burst_count=burst)
        expected = _expected(pattern, provider, burst)
        assert [_op(provider, r) for r in records] == [e["name"] for e in expected]
        for rec, exp in zip(records, expected, strict=True):
            _ASSERT_RECORD[provider](rec, exp, f"{pattern}_marker")

    @pytest.mark.parametrize("pattern,provider", _NEW_COMBOS)
    def test_records_carry_only_their_own_marker(self, pattern, provider):
        for rec in _render(provider, pattern):
            markers = {k for k in rec if k.endswith("_marker")}
            assert markers == {f"{pattern}_marker"}

    # -- burst behaviour --------------------------------------------------

    @pytest.mark.parametrize("pattern,provider", _BURST_COMBOS)
    @pytest.mark.parametrize("burst", [2, 7, 25])
    def test_burst_presets_empty_the_bucket_then_delete_it(self, pattern, provider, burst):
        records = _render(provider, pattern, burst_count=burst)
        # burst_count object deletes, then exactly ONE bucket delete, last.
        assert len(records) == burst + 1
        object_op = "DeleteObject" if provider == "aws" else "storage.objects.delete"
        bucket_op = "DeleteBucket" if provider == "aws" else "storage.buckets.delete"
        assert [_op(provider, r) for r in records] == [object_op] * burst + [bucket_op]
        # Every delete names a DIFFERENT object — a burst of one key repeated is
        # one delete seen N times, not a bucket being emptied.
        keys = [r["log_object_key"] for r in records[:-1]]
        assert len(set(keys)) == burst
        assert "log_object_key" not in records[-1]
        # ... and they all sit in the one bucket the terminal call deletes.
        assert len({r["log_resource_name"] for r in records}) == 1
        if provider == "aws":
            assert [r["requestParameters"]["key"] for r in records[:-1]] == keys
            assert {r["requestParameters"]["bucketName"] for r in records} == {
                records[-1]["requestParameters"]["bucketName"]
            }
            # Object deletes are data events; the bucket delete is management.
            assert [r["managementEvent"] for r in records] == [False] * burst + [True]
            assert [r["eventCategory"] for r in records] == ["Data"] * burst + ["Management"]
        else:
            bucket = records[-1]["protoPayload"]["resourceName"]
            assert [r["protoPayload"]["resourceName"] for r in records[:-1]] == [
                f"{bucket}/objects/{k}" for k in keys
            ]
            assert [r["logName"].rsplit("%2F", 1)[1] for r in records] == (
                ["data_access"] * burst + ["activity"]
            )

    @pytest.mark.parametrize(
        "pattern,provider",
        [c for c in _NEW_COMBOS if c not in _BURST_COMBOS],
    )
    def test_non_burst_presets_ignore_burst_count(self, pattern, provider):
        small = _render(provider, pattern, burst_count=2)
        large = _render(provider, pattern, burst_count=50)
        assert len(small) == len(large) == len(_expected(pattern, provider, 2))
        assert [_op(provider, r) for r in small] == [_op(provider, r) for r in large]

    def test_gcp_log_bucket_delete_is_one_call_not_a_burst(self):
        # A Cloud Logging log bucket has no object-level API: deleting it is a
        # single DeleteBucket (the bucket then sits in DELETE_REQUESTED).
        records = _render("gcp", "log_destination_delete", burst_count=25)
        assert len(records) == 1
        assert records[0]["protoPayload"]["serviceName"] == "logging.googleapis.com"

    @pytest.mark.parametrize("provider", _PROVIDERS)
    def test_object_overwrite_reads_then_writes_the_same_object_as_one_identity(self, provider):
        read, write = _render(provider, "log_object_overwrite")
        assert read["log_object_key"] == write["log_object_key"]
        assert read["log_resource_name"] == write["log_resource_name"]
        assert read["principal_id"] == write["principal_id"]
        if provider == "aws":
            assert (read["eventName"], write["eventName"]) == ("GetObject", "PutObject")
            assert read["requestParameters"] == write["requestParameters"]
            assert (read["readOnly"], write["readOnly"]) == (True, False)
            assert read["userIdentity"] == write["userIdentity"]
        else:
            assert (
                read["protoPayload"]["methodName"], write["protoPayload"]["methodName"],
            ) == ("storage.objects.get", "storage.objects.create")
            assert read["protoPayload"]["resourceName"] == write["protoPayload"]["resourceName"]
            assert (
                read["protoPayload"]["authenticationInfo"]
                == write["protoPayload"]["authenticationInfo"]
            )

    # -- what must NOT be in a record --------------------------------------

    def test_operation_inventory_is_exactly_the_victim_side_calls(self):
        # The whole vocabulary of the eight presets, per provider. Anything an
        # attacker does in their OWN account (creating the KMS key, editing its
        # policy, deleting it, re-creating the bucket name) is absent from a
        # victim's audit log and must be absent here.
        seen: dict[str, set[tuple[str, str]]] = {"aws": set(), "gcp": set()}
        for pattern, provider in _NEW_COMBOS:
            for rec in _render(provider, pattern):
                source = (rec["eventSource"] if provider == "aws"
                          else rec["protoPayload"]["serviceName"])
                seen[provider].add((source, _op(provider, rec)))
        assert seen["aws"] == {
            (_CLOUDTRAIL, "StopLogging"), (_CLOUDTRAIL, "DeleteTrail"),
            (_CLOUDTRAIL, "UpdateTrail"), (_CLOUDTRAIL, "CreateTrail"),
            (_S3, "GetObject"), (_S3, "PutObject"),
            (_S3, "DeleteObject"), (_S3, "DeleteBucket"),
        }
        assert seen["gcp"] == {
            (_LOGGING, _CFG + "UpdateSink"), (_LOGGING, _CFG + "DeleteSink"),
            (_LOGGING, _CFG + "CreateSink"), (_LOGGING, _CFG + "UpdateBucket"),
            (_LOGGING, _CFG + "DeleteBucket"),
            (_GCS, "storage.objects.get"), (_GCS, "storage.objects.create"),
            (_GCS, "storage.objects.delete"), (_GCS, "storage.buckets.delete"),
        }

    @pytest.mark.parametrize("pattern,provider", _NEW_COMBOS)
    def test_no_credential_shaped_value_in_any_record(self, pattern, provider):
        token = "super-secret-hec-token-0123456789"
        records = _render(provider, pattern, via_seam=True, auth_token=token)
        blob = json.dumps(records)
        assert token not in blob
        for shape in _CREDENTIAL_SHAPES:
            assert not shape.search(blob), f"{shape.pattern} matched in {pattern}/{provider}"
        for rec in records:
            for path, key, _ in _walk(rec):
                flat = re.sub(r"[^a-z0-9]", "", key.lower())
                assert not flat.endswith(_CREDENTIAL_KEY_SUFFIXES), (
                    f"credential-named key {path}.{key} in {pattern}/{provider}"
                )

    def test_attacker_destinations_are_synthetic_canary_names(self):
        # Every place a record names something the ATTACKER controls: the
        # bucket a new/redirected trail delivers to, the sink destination, the
        # external encryption key.
        destinations: dict[tuple[str, str, str], str] = {}
        for pattern in ("log_router_create", "log_router_redirect", "log_key_swap"):
            for provider in _PROVIDERS:
                for rec in _render(provider, pattern):
                    for hint in ("log_destination", "kms_key_id"):
                        if hint in rec:
                            destinations[(pattern, provider, hint)] = rec[hint]
        assert set(destinations) == {
            ("log_router_create", "aws", "log_destination"),
            ("log_router_create", "gcp", "log_destination"),
            ("log_router_redirect", "aws", "log_destination"),
            ("log_router_redirect", "gcp", "log_destination"),
            ("log_key_swap", "aws", "kms_key_id"),
            ("log_key_swap", "gcp", "kms_key_id"),
        }
        for where, value in destinations.items():
            assert "cortexsim-canary" in value, (where, value)
        assert set(destinations.values()) == {
            _ATTACKER_S3_BUCKET, _ATTACKER_SINK_DESTINATION,
            _EXTERNAL_KMS_KEY, _EXTERNAL_CMEK_KEY,
        }
        # "External" has to be visible in the value: the key lives outside the
        # victim account / project the trail and log bucket belong to.
        assert ":123456789012:" not in _EXTERNAL_KMS_KEY
        assert not _EXTERNAL_CMEK_KEY.startswith("projects/cortexsim-canary/")

    @pytest.mark.parametrize("pattern,provider", _NEW_COMBOS)
    def test_hints_restate_the_provider_true_fields(self, pattern, provider):
        # A flat hint is a projection of a nested value, never a second opinion.
        for rec in _render(provider, pattern):
            if provider == "aws":
                req = rec["requestParameters"]
                assert rec["principal_id"] == rec["userIdentity"]["arn"]
                if "log_destination" in rec:
                    assert rec["log_destination"] == req["s3BucketName"]
                if "kms_key_id" in rec:
                    assert rec["kms_key_id"] == req["kmsKeyId"]
                if "log_object_key" in rec:
                    assert rec["log_object_key"] == req["key"]
                    assert rec["log_resource_name"] == f"arn:aws:s3:::{req['bucketName']}"
            else:
                payload = rec["protoPayload"]
                req = payload.get("request", {})
                assert rec["principal_id"] == payload["authenticationInfo"]["principalEmail"]
                if "log_destination" in rec:
                    assert rec["log_destination"] == req["sink"]["destination"]
                if "kms_key_id" in rec:
                    assert rec["kms_key_id"] == req["bucket"]["cmekSettings"]["kmsKeyName"]
                if "log_disabled" in rec:
                    assert rec["log_disabled"] is req["sink"]["disabled"] is True
                if "log_object_key" in rec:
                    assert payload["resourceName"] == (
                        f"{rec['log_resource_name']}/objects/{rec['log_object_key']}"
                    )
                else:
                    assert payload["resourceName"] == rec["log_resource_name"]

    @pytest.mark.parametrize("pattern,provider", _NEW_COMBOS)
    def test_no_researcher_lab_value_from_the_posts(self, pattern, provider):
        blob = json.dumps(_render(provider, pattern, burst_count=3))
        for value in _POST_LAB_VALUES:
            assert value not in blob

    # -- seams the records pass through ------------------------------------

    @pytest.mark.parametrize("pattern,provider", _NEW_COMBOS)
    def test_principal_hint_follows_the_canary_planted_principal(self, pattern, provider):
        # The canary plant rewrites the GCP principalEmail in place; the flat
        # principal_id must not be left pointing at the pre-plant identity.
        token = "csim-9f2a41c0d3e7"
        for rec in _render(provider, pattern, via_seam=True, canary_token=token):
            assert rec["cortexsim_canary"] == token
            if provider == "aws":
                assert rec["userIdentity"]["userName"] == f"cortexsim-canary-{token}"
                assert rec["principal_id"] == rec["userIdentity"]["arn"]
            else:
                planted = f"{token}@cortexsim-canary.invalid"
                assert rec["protoPayload"]["authenticationInfo"]["principalEmail"] == planted
                assert rec["principal_id"] == planted

    @pytest.mark.parametrize("pattern,provider", _NEW_COMBOS)
    def test_source_ip_override_applies(self, pattern, provider):
        events = _render(provider, pattern, source_ip=_OVR_IP)
        assert {_caller_ip(provider, e) for e in events} == {_OVR_IP}
        assert {_caller_ip(provider, e) for e in _render(provider, pattern)} == {_ANOMALOUS_IP}

    @pytest.mark.parametrize("pattern,provider", _NEW_COMBOS)
    def test_records_do_not_alias_the_preset_templates(self, pattern, provider):
        # plant_canary and downstream consumers mutate records in place; one
        # record's edit must never bleed into the next render.
        before = json.dumps(_render(provider, pattern), sort_keys=True)
        for rec in _render(provider, pattern):
            for _, _, value in list(_walk(rec)):
                if isinstance(value, dict):
                    value["tampered"] = True
        after = json.dumps(_render(provider, pattern), sort_keys=True)
        assert _strip_volatile(before) == _strip_volatile(after)

    # -- through the executor (stubbed transport, no network) ---------------

    def test_live_run_posts_object_deletes_then_the_bucket_delete(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, provider="gcp", event_pattern="stream_bucket_delete",
            burst_count=3,
        )
        sr = state.step_results[0]
        assert sr.status == "success"
        bodies = _bodies(stub)
        assert [b["protoPayload"]["methodName"] for b in bodies] == (
            ["storage.objects.delete"] * 3 + ["storage.buckets.delete"]
        )
        assert sr.detail["events_posted"] == 4
        assert sr.detail["event_pattern"] == "stream_bucket_delete"
        assert all(r["url"] == _DEFAULT_COLLECTOR for r in stub.requests)

    def test_live_run_aws_router_redirect_single_record(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, provider="aws", event_pattern="log_router_redirect",
            iterations=2,
        )
        bodies = _bodies(stub)
        assert [b["eventName"] for b in bodies] == ["UpdateTrail", "UpdateTrail"]
        assert all(
            b["requestParameters"] == {"name": _TRAIL, "s3BucketName": _ATTACKER_S3_BUCKET}
            for b in bodies
        )
        # One simulation run id per iteration, carried in the body.
        assert len({b["cortexsim_run_id"] for b in bodies}) == 2

    @pytest.mark.parametrize("pattern,provider", _NEW_COMBOS)
    def test_dry_run_builds_no_client(self, monkeypatch, pattern, provider):
        def _boom(self, params):  # noqa: ARG001
            raise AssertionError("client should not be built in dry-run")
        monkeypatch.setattr(CloudAuditEmitter, "_build_client", _boom)
        campaign = _campaign(dry_run=True, event_pattern=pattern, provider=provider)
        state = _run(CampaignExecutor(audit=AuditLogger(file_path=None)).execute(campaign))
        sr = state.step_results[0]
        assert sr.status == "success"
        assert sr.detail["dry_run"] is True
        assert sr.bytes_sent == 0

    def test_offline_bundle_hook_renders_the_same_calls(self):
        params = CloudAuditEmitterParams(
            collector_url=_DEFAULT_COLLECTOR, provider="aws",
            event_pattern="log_destination_delete", burst_count=3,
        )
        planned = CloudAuditEmitter().plan_offline_records(
            params, sim_run_id=_RUN_ID, iteration=1,
        )
        assert [r["eventName"] for r in planned] == ["DeleteObject"] * 3 + ["DeleteBucket"]

    # -- metadata ----------------------------------------------------------

    def test_meta_declares_the_new_techniques_and_version(self):
        meta = CloudAuditEmitter.Meta
        assert meta.version == "1.2.0"
        for technique in ("T1562.008", "T1485", "T1565.001", "T1537"):
            assert technique in meta.mitre_techniques
        # The originals are all still declared.
        for technique in ("T1098", "T1530", "T1078.004", "T1552.001", "T1555.006",
                          "T1087.004", "T1069.003", "T1580", "T1619"):
            assert technique in meta.mitre_techniques
        assert len(meta.mitre_techniques) == len(set(meta.mitre_techniques))

    def test_new_techniques_do_not_trip_the_c2_gate(self):
        from eal_simulator.safety import is_c2_plugin
        assert is_c2_plugin(
            "cloud_audit_emitter", CloudAuditEmitter.Meta.mitre_techniques,
        ) is False

    def test_event_pattern_field_and_docstring_name_every_preset(self):
        described = CloudAuditEmitterParams.model_fields["event_pattern"].description
        for pattern in _EVENT_PATTERNS:
            assert pattern in described, f"{pattern} missing from the field description"
            assert pattern in cae.__doc__, f"{pattern} missing from the module docstring"

    def test_meta_description_and_targets_cover_log_tampering(self):
        meta = CloudAuditEmitter.Meta
        assert "log" in meta.description.lower()
        targets = " | ".join(meta.eal_targets)
        for needle in ("StopLogging", "DeleteTrail", "UpdateTrail", "CreateTrail",
                       "UpdateSink", "DeleteSink", "CreateSink"):
            assert needle in targets, needle

    def test_no_detector_is_presented_as_armable(self):
        # The vendor alerts these records correspond to need a natively
        # onboarded audit source plus a 14-day activation / 30-day training
        # period. Records POSTed to an HTTP collector do not arm them, so the
        # plugin must not declare them as detectors it can fire.
        manifest = CloudAuditEmitter.analytics_manifest()
        assert manifest["detectors"] == []
        assert manifest["datasets"] == ["cloud_audit_logs"]
        assert manifest["data_sources"] == ["aws_audit_log", "gcp_audit_log"]
        assert manifest["supports_negative_control"] is False


def _strip_volatile(blob: str) -> str:
    """Drop the per-record clock and insertId so two renders compare equal."""
    blob = re.sub(r'"(eventTime|timestamp)": "[^"]+"', r'"\1": "T"', blob)
    return re.sub(r'"insertId": "[0-9a-f]+"', '"insertId": "I"', blob)

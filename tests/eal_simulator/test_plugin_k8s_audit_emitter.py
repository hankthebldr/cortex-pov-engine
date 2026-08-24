"""Tests for the k8s_audit_emitter EAL plugin (on the analytics_emitter spine).

We stub ``httpx.AsyncClient`` so no real outbound traffic is generated; each
test asserts the JSON shape posted to the collector, the transport toggles
(batch / gzip / auth), and the audit events emitted. The shared transport
driver (``AnalyticsLogEmitter``) is already proven by the cloud_audit_emitter
suite — these tests focus on the Kubernetes-audit source shape + its
per-pattern record fan-out, plus dry-run / safety / registration.
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
from eal_simulator.plugins.k8s_audit_emitter import (
    K8sAuditEmitter,
    K8sAuditEmitterParams,
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
    provider: str = "kubernetes",
    event_pattern: str = "pod_exec",
    iterations: int = 1,
    burst_count: int = 8,
    collector_url: str = _DEFAULT_COLLECTOR,
    target_allowlist: list[str] | None = None,
    dry_run: bool = False,
    **extra_params,
) -> Campaign:
    spec = {
        "campaign_id": "CMP-K8SAUDIT-INTEG-001",
        "name": "k8s_audit_emitter test",
        "dry_run": dry_run,
        "steps": [{
            "step_id": "step-01",
            "plugin": "k8s_audit_emitter",
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
            K8sAuditEmitterParams.model_validate({})

    def test_collector_url_must_be_http_or_https(self):
        with pytest.raises(Exception, match="http or https"):
            K8sAuditEmitterParams.model_validate({
                "collector_url": "ftp://x.invalid/cb",
            })

    def test_unknown_provider_rejected(self):
        with pytest.raises(Exception, match="provider must be one of"):
            K8sAuditEmitterParams.model_validate({
                "collector_url": _DEFAULT_COLLECTOR, "provider": "openshift",
            })

    def test_unknown_event_pattern_rejected(self):
        with pytest.raises(Exception, match="event_pattern must be one of"):
            K8sAuditEmitterParams.model_validate({
                "collector_url": _DEFAULT_COLLECTOR,
                "event_pattern": "coin_mining",
            })

    def test_event_pattern_normalised_to_lowercase(self):
        p = K8sAuditEmitterParams.model_validate({
            "collector_url": _DEFAULT_COLLECTOR,
            "event_pattern": "POD_EXEC",
        })
        assert p.event_pattern == "pod_exec"

    def test_dataset_is_kubernetes_audit_logs(self):
        p = K8sAuditEmitterParams.model_validate({
            "collector_url": _DEFAULT_COLLECTOR,
        })
        assert p.dataset == "kubernetes_audit_logs"

    def test_auth_defaults_and_transport_toggles(self):
        p = K8sAuditEmitterParams.model_validate({
            "collector_url": _DEFAULT_COLLECTOR,
        })
        assert p.auth_token is None
        assert p.auth_header == "Authorization"
        assert p.auth_scheme == "Bearer"
        assert p.compress is False
        assert p.batch is False

    def test_auth_token_is_secret_and_not_reprd(self):
        p = K8sAuditEmitterParams.model_validate({
            "collector_url": _DEFAULT_COLLECTOR,
            "auth_token": "super-secret-hec-token",
        })
        assert "super-secret-hec-token" not in repr(p)
        assert "super-secret-hec-token" not in str(p)
        assert "super-secret-hec-token" not in p.model_dump_json()
        assert p.auth_token.get_secret_value() == "super-secret-hec-token"

    def test_base_params_shared_fields_present(self):
        fields = K8sAuditEmitterParams.model_fields
        for name in AnalyticsEmitterParams.model_fields:
            assert name in fields


# --------------------------------------------------------------------------
# Provider event-shape adapter
# --------------------------------------------------------------------------


class TestProviderShapes:
    def test_single_provider_registered(self):
        assert _list_providers() == ["kubernetes"]

    def test_six_event_patterns_registered(self):
        assert _list_event_patterns() == sorted(_EVENT_PATTERNS)
        assert len(_EVENT_PATTERNS) == 6

    def test_k8s_audit_shape(self):
        from eal_simulator.plugins.k8s_audit_emitter import _PATTERN_CALLS
        marker, calls = _PATTERN_CALLS["pod_exec"]
        evt = _PROVIDER_BUILDERS["kubernetes"](
            call=calls[0], marker=marker, sim_run_id="cortexsim-x-i1-aa",
        )
        assert evt["dataset"] == _DATASET
        assert evt["apiVersion"] == "audit.k8s.io/v1"
        assert evt["kind"] == "Event"
        assert evt["verb"] == "create"
        assert evt["objectRef"]["resource"] == "pods"
        assert evt["objectRef"]["subresource"] == "exec"
        assert evt["user"]["username"].startswith("system:serviceaccount:")
        assert evt[marker] is True
        assert evt["cortexsim_run_id"] == "cortexsim-x-i1-aa"

    def test_list_verb_has_no_object_name(self):
        from eal_simulator.plugins.k8s_audit_emitter import _PATTERN_CALLS
        marker, calls = _PATTERN_CALLS["secret_enumeration"]
        evt = _PROVIDER_BUILDERS["kubernetes"](
            call=calls[0], marker=marker, sim_run_id="cortexsim-x-i1-bb",
        )
        assert evt["verb"] == "list"
        assert evt["objectRef"]["resource"] == "secrets"
        assert "name" not in evt["objectRef"]


# --------------------------------------------------------------------------
# Plugin run path — stubbed httpx (no network)
# --------------------------------------------------------------------------


class TestPluginRun:
    def _run_with_stub(self, monkeypatch, *, status_code=202, raise_exc=None, **campaign_kw):
        stub = _RecordingClient(status_code=status_code, raise_exc=raise_exc)
        monkeypatch.setattr(
            K8sAuditEmitter, "_build_client", lambda self, params: stub,
        )
        campaign = _campaign(**campaign_kw)
        executor = CampaignExecutor(audit=AuditLogger(file_path=None))
        state = _run(executor.execute(campaign))
        return state, stub

    def test_dry_run_does_not_invoke_client(self, monkeypatch):
        def _boom(self, params):  # noqa: ARG001
            raise AssertionError("client should not be built in dry-run")
        monkeypatch.setattr(K8sAuditEmitter, "_build_client", _boom)

        campaign = _campaign(dry_run=True)
        state = _run(CampaignExecutor(audit=AuditLogger(file_path=None)).execute(campaign))
        sr = state.step_results[0]
        assert sr.status == "success"
        assert sr.detail["dry_run"] is True
        assert sr.events_emitted == 1
        assert sr.bytes_sent == 0

    def test_pod_exec_posts_single_exec_record(self, monkeypatch):
        state, stub = self._run_with_stub(monkeypatch, event_pattern="pod_exec")
        assert state.step_results[0].status == "success"
        bodies = _bodies(stub)
        assert len(bodies) == 1
        assert bodies[0]["objectRef"]["subresource"] == "exec"
        assert bodies[0].get("pod_exec_marker") is True
        assert bodies[0]["dataset"] == _DATASET

    def test_unusual_sa_api_call_single_record(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="unusual_sa_api_call",
        )
        bodies = _bodies(stub)
        assert len(bodies) == 1
        assert bodies[0]["objectRef"]["resource"] == "clusterrolebindings"
        assert bodies[0].get("unusual_sa_api_call_marker") is True

    def test_secret_access_single_get(self, monkeypatch):
        state, stub = self._run_with_stub(monkeypatch, event_pattern="secret_access")
        bodies = _bodies(stub)
        assert len(bodies) == 1
        assert bodies[0]["verb"] == "get"
        assert bodies[0]["objectRef"]["resource"] == "secrets"
        assert bodies[0].get("secret_access_marker") is True

    def test_secret_enumeration_lists_then_bursts_gets(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="secret_enumeration", burst_count=5,
        )
        bodies = _bodies(stub)
        # 1 list + burst_count gets.
        assert len(bodies) == 6
        assert bodies[0]["verb"] == "list"
        assert [b["verb"] for b in bodies[1:]] == ["get"] * 5
        assert all(b.get("secret_enumeration_marker") for b in bodies)

    def test_permission_enumeration_bursts_reviews_then_rules(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="permission_enumeration", burst_count=4,
        )
        bodies = _bodies(stub)
        # burst_count selfsubjectaccessreviews + 1 selfsubjectrulesreviews.
        assert len(bodies) == 5
        resources = [b["objectRef"]["resource"] for b in bodies]
        assert resources == ["selfsubjectaccessreviews"] * 4 + ["selfsubjectrulesreviews"]
        assert all(b.get("permission_enumeration_marker") for b in bodies)

    def test_privileged_pod_creation_carries_privileged_spec(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="privileged_pod_creation",
        )
        bodies = _bodies(stub)
        assert len(bodies) == 1
        assert bodies[0]["verb"] == "create"
        assert bodies[0]["objectRef"]["resource"] == "pods"
        sc = bodies[0]["requestObject"]["spec"]["containers"][0]["securityContext"]
        assert sc["privileged"] is True
        assert bodies[0].get("privileged_pod_creation_marker") is True

    def test_iterations_multiply_record_count(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="secret_enumeration", burst_count=3,
            iterations=2,
        )
        # (1 list + 3 gets) * 2 iterations.
        assert len(stub.requests) == 8
        assert state.step_results[0].detail["events_posted"] == 8

    def test_run_id_carried_in_body(self, monkeypatch):
        state, stub = self._run_with_stub(monkeypatch, event_pattern="pod_exec")
        body = _bodies(stub)[0]
        assert body["cortexsim_run_id"].startswith("cortexsim-")
        assert "-i1-" in body["cortexsim_run_id"]

    # -- transport toggles ------------------------------------------------

    def test_batch_mode_single_ndjson_post(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="secret_enumeration", burst_count=3,
            batch=True,
        )
        assert len(stub.requests) == 1  # 4 records, ONE POST
        req = stub.requests[0]
        assert req["headers"]["content-type"] == "application/x-ndjson"
        records = _bodies(stub, batch=True)
        assert len(records) == 4
        detail = state.step_results[0].detail
        assert detail["events_posted"] == 4
        assert detail["batch"] is True

    def test_compress_mode_gzips_body(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="secret_access", compress=True,
        )
        req = stub.requests[0]
        assert req["headers"]["content-encoding"] == "gzip"
        assert req["content"][:2] == b"\x1f\x8b"
        assert _bodies(stub)[0]["verb"] == "get"

    # -- telemetry / identity --------------------------------------------

    def test_telemetry_headers_injected_lowercase(self, monkeypatch):
        state, stub = self._run_with_stub(monkeypatch)
        headers = stub.requests[0]["headers"]
        assert "x-simulation-run-id" in headers
        assert headers["x-simulation-run-id"].startswith("cortexsim-")
        assert headers["x-simulation-source"].startswith("cortexsim-eal-simulator")

    def test_request_url_targets_collector(self, monkeypatch):
        state, stub = self._run_with_stub(monkeypatch)
        for r in stub.requests:
            assert r["url"] == _DEFAULT_COLLECTOR

    # -- safety + resilience ---------------------------------------------

    def test_safety_violation_when_collector_not_allowlisted(self, monkeypatch):
        stub = _RecordingClient()
        monkeypatch.setattr(
            K8sAuditEmitter, "_build_client", lambda self, params: stub,
        )
        campaign = _campaign(target_allowlist=["other.invalid"])
        state = _run(CampaignExecutor(audit=AuditLogger(file_path=None)).execute(campaign))
        sr = state.step_results[0]
        assert sr.status == "error"
        assert "safety_violation" in (sr.error or "")
        assert stub.requests == []

    def test_http_error_does_not_crash_iteration(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch, event_pattern="pod_exec", iterations=2,
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
        assert len(stub.requests) == 2  # both attempted despite errors
        assert sr.detail["response_status_counts"].get(0, 0) == 2
        assert sr.detail["events_posted"] == 0


# --------------------------------------------------------------------------
# Registry / metadata
# --------------------------------------------------------------------------


class TestRegistration:
    def test_plugin_registered_with_default_registry(self):
        from eal_simulator import get_default_registry

        reg = get_default_registry()
        assert reg.has("k8s_audit_emitter")

    def test_metadata_lists_analytics_and_abioc_targets(self):
        from eal_simulator import get_default_registry

        meta = get_default_registry().get("k8s_audit_emitter").metadata()
        assert any("Analytics" in t for t in meta["eal_targets"])
        assert any("ABIOC" in t for t in meta["eal_targets"])
        assert "T1609" in meta["mitre_techniques"]
        props = meta["params_schema"]["properties"]
        assert "collector_url" in props
        assert "event_pattern" in props
        assert "auth_token" in props
        assert "compress" in props
        assert "batch" in props

    def test_not_c2_classified(self):
        # Kubernetes/identity techniques must NOT trip the C2 gate.
        from eal_simulator.safety import is_c2_plugin

        assert is_c2_plugin(
            "k8s_audit_emitter", K8sAuditEmitter.Meta.mitre_techniques,
        ) is False

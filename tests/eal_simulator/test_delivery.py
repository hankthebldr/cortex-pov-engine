"""Delivery accounting + error taxonomy.

These tests pin the behaviour that closed the worst POV failure mode: a
campaign reporting "success" while the collector accepted nothing. Every
assertion here fails against the pre-ledger emitters, which counted any POST
that did not raise as a delivered record.
"""
from __future__ import annotations

import asyncio
import socket
import ssl

import httpx
import pytest

from eal_simulator import AuditLogger, Campaign, CampaignExecutor
from eal_simulator.delivery import (
    COLLECTOR_ERROR,
    COLLECTOR_REDIRECTED,
    COLLECTOR_REJECTED,
    COLLECTOR_THROTTLED,
    COLLECTOR_TIMEOUT,
    COLLECTOR_TLS_ERROR,
    COLLECTOR_UNAUTHORIZED,
    COLLECTOR_UNAVAILABLE,
    COLLECTOR_UNREACHABLE,
    PLUGIN_MISCONFIGURED,
    REMEDIATION,
    TRANSPORT_ERROR,
    DeliveryLedger,
    classify_exception,
    classify_status,
    summarise_step_results,
)
from eal_simulator.plugins.k8s_audit_emitter import K8sAuditEmitter


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------


class TestClassifyStatus:
    @pytest.mark.parametrize("code", [200, 201, 202, 204, 299])
    def test_2xx_is_delivered(self, code):
        assert classify_status(code) is None

    @pytest.mark.parametrize("code,expected", [
        (301, COLLECTOR_REDIRECTED),
        (302, COLLECTOR_REDIRECTED),
        (400, COLLECTOR_REJECTED),
        (401, COLLECTOR_UNAUTHORIZED),
        (403, COLLECTOR_UNAUTHORIZED),
        (404, COLLECTOR_REJECTED),
        (413, COLLECTOR_REJECTED),
        (429, COLLECTOR_THROTTLED),
        (500, COLLECTOR_ERROR),
        (502, COLLECTOR_ERROR),
        (503, COLLECTOR_UNAVAILABLE),
    ])
    def test_non_2xx_codes(self, code, expected):
        assert classify_status(code) == expected

    def test_redirect_is_a_failure_because_clients_do_not_follow(self):
        # follow_redirects=False everywhere in this package: a 302 to a captive
        # portal ingests nothing, so it must never read as delivered.
        assert classify_status(302) == COLLECTOR_REDIRECTED

    def test_every_failure_code_has_remediation(self):
        for code in {classify_status(c) for c in (301, 400, 401, 429, 500, 503)}:
            assert REMEDIATION[code].strip()


class TestClassifyException:
    def test_connect_error_is_unreachable(self):
        assert classify_exception(httpx.ConnectError("boom")) == COLLECTOR_UNREACHABLE

    def test_timeout_is_timeout(self):
        assert classify_exception(httpx.ReadTimeout("slow")) == COLLECTOR_TIMEOUT

    def test_ssl_error_is_tls(self):
        assert classify_exception(ssl.SSLError("handshake")) == COLLECTOR_TLS_ERROR

    def test_connect_error_mentioning_cert_is_tls_not_unreachable(self):
        exc = httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] unable to verify")
        assert classify_exception(exc) == COLLECTOR_TLS_ERROR

    def test_stdlib_socket_and_dns_errors_classify(self):
        assert classify_exception(socket.gaierror("no such host")) == COLLECTOR_UNREACHABLE
        assert classify_exception(ConnectionRefusedError("refused")) == COLLECTOR_UNREACHABLE

    def test_unknown_exception_falls_back(self):
        assert classify_exception(ValueError("???")) == TRANSPORT_ERROR


# --------------------------------------------------------------------------
# Ledger
# --------------------------------------------------------------------------


class TestDeliveryLedger:
    def test_all_accepted_is_success(self):
        ledger = DeliveryLedger()
        for _ in range(3):
            assert ledger.record_response(202, records=2, wire_bytes=100) is None
        assert ledger.outcome == "success"
        assert ledger.records_delivered == 6
        assert ledger.bytes_delivered == 300
        assert ledger.error_summary() is None

    def test_all_rejected_is_error_not_success(self):
        ledger = DeliveryLedger()
        for _ in range(4):
            assert ledger.record_response(401, records=1, wire_bytes=50) == (
                COLLECTOR_UNAUTHORIZED
            )
        assert ledger.outcome == "error"
        assert ledger.records_delivered == 0
        assert ledger.records_attempted == 4
        # Bytes went on the wire but nothing was ingested — the gap is the point.
        assert ledger.bytes_attempted == 200
        assert ledger.bytes_delivered == 0
        assert COLLECTOR_UNAUTHORIZED in ledger.error_summary()

    def test_mixed_is_partial(self):
        ledger = DeliveryLedger()
        ledger.record_response(202, records=5, wire_bytes=100)
        ledger.record_response(500, records=5, wire_bytes=100)
        assert ledger.outcome == "partial"
        assert ledger.dominant_failure == COLLECTOR_ERROR

    def test_nothing_attempted_is_success(self):
        assert DeliveryLedger().outcome == "success"

    def test_exception_keeps_zero_status_sentinel(self):
        ledger = DeliveryLedger()
        ledger.record_exception(httpx.ConnectError("boom"), records=1, wire_bytes=10)
        assert ledger.status_counts == {0: 1}
        assert ledger.failure_codes() == [COLLECTOR_UNREACHABLE]

    def test_dominant_failure_is_the_one_that_lost_most_records(self):
        ledger = DeliveryLedger()
        ledger.record_response(404, records=1, wire_bytes=10)
        ledger.record_response(429, records=9, wire_bytes=10)
        assert ledger.dominant_failure == COLLECTOR_THROTTLED

    def test_to_dict_carries_remediation_per_failure(self):
        ledger = DeliveryLedger()
        ledger.record_response(404, records=1, wire_bytes=10)
        payload = ledger.to_dict()
        assert payload["outcome"] == "error"
        assert payload["failures"][0]["code"] == COLLECTOR_REJECTED
        assert payload["failures"][0]["status_code"] == 404
        assert payload["failures"][0]["remediation"]


# --------------------------------------------------------------------------
# Run rollup
# --------------------------------------------------------------------------


def _step(delivery=None, detail=None):
    return {"detail": {**(detail or {}), **({"delivery": delivery} if delivery else {})}}


class TestSummariseStepResults:
    def test_all_delivered(self):
        summary = summarise_step_results([
            _step({"records_attempted": 4, "records_delivered": 4,
                   "bytes_attempted": 40, "bytes_delivered": 40, "failures": []}),
        ])
        assert summary["delivery_verdict"] == "delivered"
        assert summary["records_delivered"] == 4

    def test_none_delivered_reads_not_delivered(self):
        summary = summarise_step_results([
            _step({"records_attempted": 4, "records_delivered": 0,
                   "bytes_attempted": 40, "bytes_delivered": 0,
                   "failures": [{"code": COLLECTOR_UNAUTHORIZED, "records": 4}]}),
        ])
        assert summary["delivery_verdict"] == "not_delivered"
        assert summary["dominant_failure"] == COLLECTOR_UNAUTHORIZED
        assert summary["remediation"]

    def test_partial_across_steps(self):
        summary = summarise_step_results([
            _step({"records_attempted": 4, "records_delivered": 4,
                   "bytes_attempted": 40, "bytes_delivered": 40, "failures": []}),
            _step({"records_attempted": 4, "records_delivered": 0,
                   "bytes_attempted": 40, "bytes_delivered": 0,
                   "failures": [{"code": COLLECTOR_ERROR, "records": 4}]}),
        ])
        assert summary["delivery_verdict"] == "partial"
        assert summary["steps_total"] == 2
        assert summary["steps_with_delivery"] == 2

    def test_non_collector_steps_are_not_applicable(self):
        summary = summarise_step_results([_step(detail={"target": "example.test"})])
        assert summary["delivery_verdict"] == "not_applicable"
        assert summary["steps_with_delivery"] == 0

    def test_legacy_rows_without_delivery_block_do_not_crash(self):
        assert summarise_step_results([{}, {"detail": None}])["delivery_verdict"] == (
            "not_applicable"
        )


# --------------------------------------------------------------------------
# End-to-end through the emitter spine.
# --------------------------------------------------------------------------


_COLLECTOR = "https://collector.cortexsim-canary.invalid/logs/v1/event"


class _StubClient:
    def __init__(self, status_code=202):
        self.status_code = status_code
        self.requests = []

    async def post(self, url, *, headers=None, content=None):
        self.requests.append(content)

        class _R:
            def __init__(self, code):
                self.status_code = code

        return _R(self.status_code)

    async def aclose(self):
        return None


def _campaign(plugin="k8s_audit_emitter", params=None, steps=None):
    return Campaign.model_validate({
        "campaign_id": "CMP-DELIV-001",
        "name": "delivery accounting",
        "authorized_by": "tester@example.com",
        "simulation_authorized": True,
        "target_allowlist": ["collector.cortexsim-canary.invalid"],
        "dry_run": False,
        "steps": steps or [{
            "step_id": "step-01",
            "plugin": plugin,
            "params": params or {
                "collector_url": _COLLECTOR,
                "provider": "kubernetes",
                "event_pattern": "pod_exec",
                "iterations": 1,
                "burst_count": 2,
            },
        }],
    })


def _run(campaign):
    return asyncio.run(
        CampaignExecutor(audit=AuditLogger(file_path=None)).execute(campaign)
    )


class TestEmitterHonoursDelivery:
    def _run_with_status(self, monkeypatch, status_code):
        stub = _StubClient(status_code=status_code)
        monkeypatch.setattr(K8sAuditEmitter, "_build_client", lambda self, p: stub)
        return _run(_campaign()), stub

    def test_collector_rejection_is_not_a_green_step(self, monkeypatch):
        state, stub = self._run_with_status(monkeypatch, 401)
        result = state.step_results[0]
        # Bytes went out — that is exactly why the old code called it success.
        assert stub.requests, "the plugin did put bytes on the wire"
        assert result.status == "error"
        assert result.events_emitted == 0
        assert result.bytes_sent == 0
        delivery = result.detail["delivery"]
        assert delivery["records_attempted"] == len(stub.requests)
        assert delivery["bytes_attempted"] > 0
        assert delivery["failures"][0]["code"] == COLLECTOR_UNAUTHORIZED

    def test_redirect_is_not_delivered(self, monkeypatch):
        state, _ = self._run_with_status(monkeypatch, 302)
        result = state.step_results[0]
        assert result.status == "error"
        assert result.detail["delivery"]["failures"][0]["code"] == COLLECTOR_REDIRECTED

    def test_accepted_records_stay_green(self, monkeypatch):
        state, _ = self._run_with_status(monkeypatch, 202)
        result = state.step_results[0]
        assert result.status == "success"
        assert result.detail["delivery"]["outcome"] == "success"
        assert result.error is None

    def test_campaign_rollup_flags_a_run_that_ingested_nothing(self, monkeypatch):
        state, _ = self._run_with_status(monkeypatch, 500)
        payload = state.to_dict()
        # The campaign itself still "completed" — every step ran to the end.
        assert payload["status"] == "complete"
        # ...but the rollup refuses to let that read as a delivered POV.
        assert payload["delivery"]["delivery_verdict"] == "not_delivered"
        assert payload["delivery"]["dominant_failure"] == COLLECTOR_ERROR
        assert payload["delivery"]["remediation"]

    def test_audit_stream_marks_refused_posts_as_failures(self, monkeypatch):
        events = []
        stub = _StubClient(status_code=404)
        monkeypatch.setattr(K8sAuditEmitter, "_build_client", lambda self, p: stub)

        class _CapturingAudit(AuditLogger):
            def emit(self, event):
                events.append(event)

        executor = CampaignExecutor(audit=_CapturingAudit(file_path=None))
        asyncio.run(executor.execute(_campaign()))

        posts = [e for e in events
                 if e["event"]["action"] == "k8s_audit_emitter_event"]
        assert posts, "expected per-record audit events"
        assert all(e["event"]["outcome"] == "failure" for e in posts)
        assert all(e["cortexsim"]["failure_code"] == COLLECTOR_REJECTED for e in posts)
        assert all(e["cortexsim"]["delivered"] is False for e in posts)


class TestExecutorMisconfiguration:
    def test_unknown_plugin_is_tagged_plugin_misconfigured(self):
        campaign = _campaign(steps=[{
            "step_id": "step-01", "plugin": "no_such_plugin", "params": {},
        }])
        result = _run(campaign).step_results[0]
        assert result.status == "error"
        assert result.detail["failure_code"] == PLUGIN_MISCONFIGURED
        assert result.detail["remediation"]

    def test_invalid_params_is_tagged_plugin_misconfigured(self):
        campaign = _campaign(params={"collector_url": "ftp://nope/"})
        result = _run(campaign).step_results[0]
        assert result.status == "error"
        assert result.detail["failure_code"] == PLUGIN_MISCONFIGURED

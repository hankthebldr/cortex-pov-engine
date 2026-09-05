"""Tests for the third_party_firewall_emitter EAL plugin (Third-Party Firewalls
data source, third_party_firewall_raw dataset).

We stub ``httpx.AsyncClient`` so no real traffic is generated. Each test asserts
the detector-true shape of the POSTed records, the per-detector negative control
(a record in the same dataset that must NOT fire), and the shared spine
behaviour (delivery accounting, dry-run, safety gate) inherited from
analytics_emitter.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from eal_simulator import AuditLogger, Campaign, CampaignExecutor
from eal_simulator.plugins.third_party_firewall_emitter import (
    ThirdPartyFirewallEmitter,
    ThirdPartyFirewallParams,
    _DATASET,
    _EVENT_PATTERNS,
    _PORT_SCAN_MIN,
    _HOST_SWEEP_MIN,
    _DENIED_SPIKE_MIN,
    _list_event_patterns,
)


def _run(coro):
    return asyncio.run(coro)


class _RecordingClient:
    def __init__(self, status_code: int = 202, raise_exc: Exception | None = None):
        self.requests: list[dict[str, Any]] = []
        self.status_code = status_code
        self.raise_exc = raise_exc
        self.closed = False

    async def post(self, url: str, *, headers=None, content=None):
        self.requests.append({"url": url, "headers": dict(headers or {}), "content": content})
        if self.raise_exc is not None:
            raise self.raise_exc

        class _R:
            def __init__(self, status_code: int) -> None:
                self.status_code = status_code

        return _R(self.status_code)

    async def aclose(self) -> None:
        self.closed = True


_COLLECTOR = "https://collector.cortexsim-canary.invalid/logs/v1/event"


def _campaign(*, event_pattern="port_scan", negative_control=False, iterations=1,
              burst_count=8, dry_run=False, target_allowlist=None, **extra) -> Campaign:
    spec = {
        "campaign_id": "CMP-3PFW-001",
        "name": "third-party firewall test",
        "dry_run": dry_run,
        "steps": [{
            "step_id": "step-01",
            "plugin": "third_party_firewall_emitter",
            "params": {
                "collector_url": _COLLECTOR,
                "event_pattern": event_pattern,
                "negative_control": negative_control,
                "iterations": iterations,
                "burst_count": burst_count,
                "sleep_seconds": 0.0,
                **extra,
            },
        }],
    }
    if not dry_run:
        spec.update({
            "simulation_authorized": True,
            "authorized_by": "tester",
            "target_allowlist": target_allowlist or ["collector.cortexsim-canary.invalid"],
        })
    return Campaign.model_validate(spec)


def _bodies(stub: _RecordingClient) -> list[dict[str, Any]]:
    out = []
    for r in stub.requests:
        content = r["content"]
        text = content.decode("utf-8") if isinstance(content, (bytes, bytearray)) else content
        out.append(json.loads(text))
    return out


def _run_with_stub(monkeypatch, **campaign_kw):
    stub = _RecordingClient()
    monkeypatch.setattr(ThirdPartyFirewallEmitter, "_build_client", lambda self, params: stub)
    state = _run(CampaignExecutor(audit=AuditLogger(file_path=None)).execute(_campaign(**campaign_kw)))
    return state, stub


class TestParams:
    def test_collector_url_required(self):
        with pytest.raises(Exception):
            ThirdPartyFirewallParams.model_validate({})

    def test_unknown_pattern_rejected(self):
        with pytest.raises(Exception, match="event_pattern must be one of"):
            ThirdPartyFirewallParams.model_validate({"collector_url": _COLLECTOR, "event_pattern": "nope"})

    def test_negative_control_default_false(self):
        p = ThirdPartyFirewallParams.model_validate({"collector_url": _COLLECTOR})
        assert p.negative_control is False

    def test_dataset(self):
        p = ThirdPartyFirewallParams.model_validate({"collector_url": _COLLECTOR})
        assert p.dataset == _DATASET

    def test_three_patterns(self):
        assert _list_event_patterns() == sorted(_EVENT_PATTERNS)
        assert len(_EVENT_PATTERNS) == 3


class TestPositiveIsDetectorTrue:
    def test_port_scan_clears_distinct_port_floor(self, monkeypatch):
        state, stub = _run_with_stub(monkeypatch, event_pattern="port_scan")
        assert state.step_results[0].status == "success"
        bodies = _bodies(stub)
        ports = {b["dst_port"] for b in bodies}
        assert len(ports) >= _PORT_SCAN_MIN
        assert all(b["dataset"] == _DATASET for b in bodies)
        assert all(b["action"] == "reset-both" for b in bodies)
        assert all(b.get("port_scan_marker") for b in bodies)
        # All to ONE dst — the port-scan (not host-sweep) shape.
        assert len({b["dst_ip"] for b in bodies}) == 1

    def test_host_sweep_clears_distinct_host_floor(self, monkeypatch):
        state, stub = _run_with_stub(monkeypatch, event_pattern="host_sweep")
        bodies = _bodies(stub)
        assert len({b["dst_ip"] for b in bodies}) >= _HOST_SWEEP_MIN
        # One port across many hosts.
        assert {b["dst_port"] for b in bodies} == {445}

    def test_denied_spike_clears_floor_and_all_denied(self, monkeypatch):
        state, stub = _run_with_stub(monkeypatch, event_pattern="denied_conn_spike")
        bodies = _bodies(stub)
        assert len(bodies) >= _DENIED_SPIKE_MIN
        assert all(b["action"] == "deny" for b in bodies)


class TestNegativeControlMustNotFire:
    def test_port_scan_negative_is_two_allowed_ports(self, monkeypatch):
        state, stub = _run_with_stub(monkeypatch, event_pattern="port_scan", negative_control=True)
        bodies = _bodies(stub)
        assert len(bodies) == 2
        assert {b["dst_port"] for b in bodies} == {443, 80}
        assert all(b["action"] == "allow" for b in bodies)
        assert all(b["dataset"] == _DATASET for b in bodies)
        assert all(b.get("negative_control") for b in bodies)
        # The negative control lands in the SAME dataset but is nowhere near the
        # port-scan floor — the falsifiability control.
        assert len({b["dst_port"] for b in bodies}) < _PORT_SCAN_MIN

    def test_host_sweep_negative_is_two_hosts(self, monkeypatch):
        state, stub = _run_with_stub(monkeypatch, event_pattern="host_sweep", negative_control=True)
        bodies = _bodies(stub)
        assert len({b["dst_ip"] for b in bodies}) == 2 < _HOST_SWEEP_MIN

    def test_denied_spike_negative_is_two_events(self, monkeypatch):
        state, stub = _run_with_stub(monkeypatch, event_pattern="denied_conn_spike", negative_control=True)
        bodies = _bodies(stub)
        assert len(bodies) == 2 < _DENIED_SPIKE_MIN


class TestRunPath:
    def test_dry_run_no_client(self, monkeypatch):
        def _boom(self, params):
            raise AssertionError("no client in dry-run")
        monkeypatch.setattr(ThirdPartyFirewallEmitter, "_build_client", _boom)
        state = _run(CampaignExecutor(audit=AuditLogger(file_path=None)).execute(_campaign(dry_run=True)))
        assert state.step_results[0].status == "success"
        assert state.step_results[0].detail["dry_run"] is True

    def test_delivery_accounting_2xx(self, monkeypatch):
        state, stub = _run_with_stub(monkeypatch, event_pattern="host_sweep")
        detail = state.step_results[0].detail
        assert detail["delivery"]["outcome"] == "success"
        assert detail["events_posted"] == len(stub.requests)

    def test_non_2xx_is_not_delivered(self, monkeypatch):
        stub = _RecordingClient(status_code=401)
        monkeypatch.setattr(ThirdPartyFirewallEmitter, "_build_client", lambda self, params: stub)
        state = _run(CampaignExecutor(audit=AuditLogger(file_path=None)).execute(_campaign(event_pattern="port_scan")))
        sr = state.step_results[0]
        assert sr.detail["delivery"]["records_delivered"] == 0
        assert sr.status == "error"

    def test_safety_gate_blocks_unlisted_collector(self, monkeypatch):
        stub = _RecordingClient()
        monkeypatch.setattr(ThirdPartyFirewallEmitter, "_build_client", lambda self, params: stub)
        state = _run(CampaignExecutor(audit=AuditLogger(file_path=None)).execute(
            _campaign(target_allowlist=["other.invalid"])))
        assert state.step_results[0].status == "error"
        assert stub.requests == []


class TestMetadata:
    def test_family_manifest(self):
        from eal_simulator import get_default_registry
        reg = get_default_registry()
        assert reg.has("third_party_firewall_emitter")
        m = ThirdPartyFirewallEmitter.analytics_manifest()
        assert m["data_sources"] == ["third_party_firewalls"]
        assert m["supports_negative_control"] is True
        assert len(m["detectors"]) == 3
        assert m["datasets"] == [_DATASET]

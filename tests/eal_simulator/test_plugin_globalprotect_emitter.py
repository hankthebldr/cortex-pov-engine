"""Tests for the globalprotect_emitter EAL plugin (PAN GlobalProtect data
source, panw_ngfw_globalprotect_raw dataset), including negative controls.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from eal_simulator import AuditLogger, Campaign, CampaignExecutor
from eal_simulator.plugins.globalprotect_emitter import (
    GlobalProtectEmitter,
    GlobalProtectParams,
    _DATASET,
    _EVENT_PATTERNS,
    _BRUTE_FORCE_MIN,
    _list_event_patterns,
)


def _run(coro):
    return asyncio.run(coro)


class _RecordingClient:
    def __init__(self, status_code: int = 202):
        self.requests: list[dict[str, Any]] = []
        self.status_code = status_code

    async def post(self, url: str, *, headers=None, content=None):
        self.requests.append({"url": url, "headers": dict(headers or {}), "content": content})

        class _R:
            def __init__(self, status_code: int) -> None:
                self.status_code = status_code

        return _R(self.status_code)

    async def aclose(self) -> None:
        pass


_COLLECTOR = "https://collector.cortexsim-canary.invalid/logs/v1/event"


def _campaign(*, event_pattern="portal_brute_force", negative_control=False, burst_count=8) -> Campaign:
    return Campaign.model_validate({
        "campaign_id": "CMP-GP-001",
        "name": "gp test",
        "dry_run": False,
        "simulation_authorized": True,
        "authorized_by": "tester",
        "target_allowlist": ["collector.cortexsim-canary.invalid"],
        "steps": [{
            "step_id": "step-01",
            "plugin": "globalprotect_emitter",
            "params": {
                "collector_url": _COLLECTOR,
                "event_pattern": event_pattern,
                "negative_control": negative_control,
                "burst_count": burst_count,
                "sleep_seconds": 0.0,
            },
        }],
    })


def _bodies(stub: _RecordingClient) -> list[dict[str, Any]]:
    out = []
    for r in stub.requests:
        content = r["content"]
        text = content.decode("utf-8") if isinstance(content, (bytes, bytearray)) else content
        out.append(json.loads(text))
    return out


def _run_with_stub(monkeypatch, **kw):
    stub = _RecordingClient()
    monkeypatch.setattr(GlobalProtectEmitter, "_build_client", lambda self, params: stub)
    state = _run(CampaignExecutor(audit=AuditLogger(file_path=None)).execute(_campaign(**kw)))
    return state, stub


class TestParams:
    def test_three_patterns(self):
        assert _list_event_patterns() == sorted(_EVENT_PATTERNS)
        assert len(_EVENT_PATTERNS) == 3

    def test_dataset(self):
        assert GlobalProtectParams.model_validate({"collector_url": _COLLECTOR}).dataset == _DATASET


class TestPositiveIsDetectorTrue:
    def test_portal_brute_force_failures_then_success(self, monkeypatch):
        state, stub = _run_with_stub(monkeypatch, event_pattern="portal_brute_force")
        bodies = _bodies(stub)
        failures = [b for b in bodies if b["status"] == "failure"]
        success = [b for b in bodies if b["status"] == "success"]
        assert len(failures) >= _BRUTE_FORCE_MIN
        assert len(success) == 1
        assert all(b["dataset"] == _DATASET for b in bodies)

    def test_impossible_travel_two_countries(self, monkeypatch):
        state, stub = _run_with_stub(monkeypatch, event_pattern="impossible_travel")
        bodies = _bodies(stub)
        assert len(bodies) == 2
        assert all(b["status"] == "success" for b in bodies)
        assert len({b["public_ip_country"] for b in bodies}) == 2

    def test_anomalous_geo_far_country(self, monkeypatch):
        state, stub = _run_with_stub(monkeypatch, event_pattern="anomalous_geo")
        b = _bodies(stub)[0]
        assert b["status"] == "success"
        assert b["public_ip_country"] != "US"


class TestNegativeControlMustNotFire:
    def test_brute_force_negative_single_success(self, monkeypatch):
        state, stub = _run_with_stub(monkeypatch, event_pattern="portal_brute_force", negative_control=True)
        bodies = _bodies(stub)
        assert [b["status"] for b in bodies] == ["success"]

    def test_impossible_travel_negative_same_country(self, monkeypatch):
        state, stub = _run_with_stub(monkeypatch, event_pattern="impossible_travel", negative_control=True)
        bodies = _bodies(stub)
        assert len({b["public_ip_country"] for b in bodies}) == 1

    def test_anomalous_geo_negative_home_country(self, monkeypatch):
        state, stub = _run_with_stub(monkeypatch, event_pattern="anomalous_geo", negative_control=True)
        assert _bodies(stub)[0]["public_ip_country"] == "US"


class TestMetadata:
    def test_manifest(self):
        m = GlobalProtectEmitter.analytics_manifest()
        assert m["data_sources"] == ["pan_global_protect"]
        assert m["supports_negative_control"] is True
        assert len(m["detectors"]) == 3

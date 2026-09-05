"""Tests for the third_party_alert_emitter EAL plugin (Third-Party Alerts data
source, third_party_alerts_raw dataset), including the per-detector negative
control that lands in the same dataset but must NOT raise.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from eal_simulator import AuditLogger, Campaign, CampaignExecutor
from eal_simulator.plugins.third_party_alert_emitter import (
    ThirdPartyAlertEmitter,
    ThirdPartyAlertParams,
    _DATASET,
    _EVENT_PATTERNS,
    _MALWARE_CATEGORIES,
    _MALWARE_ACTIONS,
    _REPEAT_MIN,
    _SURFACING_SEVERITIES,
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


def _campaign(*, event_pattern="high_severity_alert", negative_control=False,
              burst_count=8, target_allowlist=None) -> Campaign:
    return Campaign.model_validate({
        "campaign_id": "CMP-3PALERT-001",
        "name": "third-party alert test",
        "dry_run": False,
        "simulation_authorized": True,
        "authorized_by": "tester",
        "target_allowlist": target_allowlist or ["collector.cortexsim-canary.invalid"],
        "steps": [{
            "step_id": "step-01",
            "plugin": "third_party_alert_emitter",
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
    monkeypatch.setattr(ThirdPartyAlertEmitter, "_build_client", lambda self, params: stub)
    state = _run(CampaignExecutor(audit=AuditLogger(file_path=None)).execute(_campaign(**kw)))
    return state, stub


class TestParams:
    def test_three_patterns(self):
        assert _list_event_patterns() == sorted(_EVENT_PATTERNS)
        assert len(_EVENT_PATTERNS) == 3

    def test_dataset(self):
        assert ThirdPartyAlertParams.model_validate({"collector_url": _COLLECTOR}).dataset == _DATASET


class TestPositiveIsDetectorTrue:
    def test_high_severity_is_surfacing(self, monkeypatch):
        state, stub = _run_with_stub(monkeypatch, event_pattern="high_severity_alert")
        b = _bodies(stub)[0]
        assert b["severity"] in _SURFACING_SEVERITIES
        assert b["dataset"] == _DATASET

    def test_malware_verdict_category_and_action(self, monkeypatch):
        state, stub = _run_with_stub(monkeypatch, event_pattern="malware_verdict")
        b = _bodies(stub)[0]
        assert b["category"] in _MALWARE_CATEGORIES
        assert b["action"] in _MALWARE_ACTIONS

    def test_repeated_host_alerts_clear_floor_same_host(self, monkeypatch):
        state, stub = _run_with_stub(monkeypatch, event_pattern="repeated_host_alerts")
        bodies = _bodies(stub)
        assert len(bodies) >= _REPEAT_MIN
        assert len({b["src_host"] for b in bodies}) == 1  # all on ONE host


class TestNegativeControlMustNotFire:
    def test_high_severity_negative_is_informational(self, monkeypatch):
        state, stub = _run_with_stub(monkeypatch, event_pattern="high_severity_alert", negative_control=True)
        b = _bodies(stub)[0]
        assert b["severity"] not in _SURFACING_SEVERITIES
        assert b["dataset"] == _DATASET
        assert b.get("negative_control") is True

    def test_malware_negative_is_policy_allowed(self, monkeypatch):
        state, stub = _run_with_stub(monkeypatch, event_pattern="malware_verdict", negative_control=True)
        b = _bodies(stub)[0]
        assert b["category"] not in _MALWARE_CATEGORIES
        assert b["action"] == "allowed"

    def test_repeated_negative_single_alert(self, monkeypatch):
        state, stub = _run_with_stub(monkeypatch, event_pattern="repeated_host_alerts", negative_control=True)
        bodies = _bodies(stub)
        assert len(bodies) == 1 < _REPEAT_MIN


class TestMetadata:
    def test_manifest(self):
        m = ThirdPartyAlertEmitter.analytics_manifest()
        assert m["data_sources"] == ["third_party_alerts"]
        assert m["supports_negative_control"] is True
        assert len(m["detectors"]) == 3

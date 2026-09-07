"""Tests for the duo_auth_emitter EAL plugin (Duo data source, duo_auth_raw
dataset), including the per-detector negative control.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from eal_simulator import AuditLogger, Campaign, CampaignExecutor
from eal_simulator.plugins.duo_auth_emitter import (
    DuoAuthEmitter,
    DuoAuthParams,
    _DATASET,
    _EVENT_PATTERNS,
    _FATIGUE_MIN,
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


def _campaign(*, event_pattern="mfa_fatigue", negative_control=False, burst_count=8) -> Campaign:
    return Campaign.model_validate({
        "campaign_id": "CMP-DUO-001",
        "name": "duo test",
        "dry_run": False,
        "simulation_authorized": True,
        "authorized_by": "tester",
        "target_allowlist": ["collector.cortexsim-canary.invalid"],
        "steps": [{
            "step_id": "step-01",
            "plugin": "duo_auth_emitter",
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
    monkeypatch.setattr(DuoAuthEmitter, "_build_client", lambda self, params: stub)
    state = _run(CampaignExecutor(audit=AuditLogger(file_path=None)).execute(_campaign(**kw)))
    return state, stub


class TestParams:
    def test_three_patterns(self):
        assert _list_event_patterns() == sorted(_EVENT_PATTERNS)
        assert len(_EVENT_PATTERNS) == 3

    def test_dataset(self):
        assert DuoAuthParams.model_validate({"collector_url": _COLLECTOR}).dataset == _DATASET


class TestPositiveIsDetectorTrue:
    def test_mfa_fatigue_denied_burst_then_success(self, monkeypatch):
        state, stub = _run_with_stub(monkeypatch, event_pattern="mfa_fatigue")
        bodies = _bodies(stub)
        denied = [b for b in bodies if b["result"] in ("denied", "fraud")]
        success = [b for b in bodies if b["result"] == "success"]
        assert len(denied) >= _FATIGUE_MIN
        assert len(success) == 1
        assert all(b["dataset"] == _DATASET for b in bodies)

    def test_fraud_reported(self, monkeypatch):
        state, stub = _run_with_stub(monkeypatch, event_pattern="fraud_reported")
        b = _bodies(stub)[0]
        assert b["result"] == "fraud"

    def test_anomalous_geo_far_country(self, monkeypatch):
        state, stub = _run_with_stub(monkeypatch, event_pattern="anomalous_geo")
        b = _bodies(stub)[0]
        assert b["result"] == "success"
        assert b["access_device"]["location"]["country"] != "US"


class TestNegativeControlMustNotFire:
    def test_mfa_fatigue_negative_single_approval(self, monkeypatch):
        state, stub = _run_with_stub(monkeypatch, event_pattern="mfa_fatigue", negative_control=True)
        bodies = _bodies(stub)
        assert [b["result"] for b in bodies] == ["success"]
        assert not [b for b in bodies if b["result"] in ("denied", "fraud")]

    def test_fraud_negative_is_success(self, monkeypatch):
        state, stub = _run_with_stub(monkeypatch, event_pattern="fraud_reported", negative_control=True)
        assert _bodies(stub)[0]["result"] == "success"

    def test_anomalous_geo_negative_home_country(self, monkeypatch):
        state, stub = _run_with_stub(monkeypatch, event_pattern="anomalous_geo", negative_control=True)
        assert _bodies(stub)[0]["access_device"]["location"]["country"] == "US"


class TestMetadata:
    def test_manifest(self):
        m = DuoAuthEmitter.analytics_manifest()
        assert m["data_sources"] == ["duo"]
        assert m["supports_negative_control"] is True
        assert len(m["detectors"]) == 3

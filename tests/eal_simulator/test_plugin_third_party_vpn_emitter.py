"""Tests for the third_party_vpn_emitter EAL plugin (Third-Party VPNs data
source, third_party_vpn_raw dataset), including the per-detector negative
control that lands in the same dataset but must NOT fire.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from eal_simulator import AuditLogger, Campaign, CampaignExecutor
from eal_simulator.plugins.third_party_vpn_emitter import (
    ThirdPartyVpnEmitter,
    ThirdPartyVpnParams,
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


def _campaign(*, event_pattern="impossible_travel", negative_control=False,
              burst_count=8, dry_run=False, target_allowlist=None) -> Campaign:
    spec = {
        "campaign_id": "CMP-3PVPN-001",
        "name": "third-party vpn test",
        "dry_run": dry_run,
        "steps": [{
            "step_id": "step-01",
            "plugin": "third_party_vpn_emitter",
            "params": {
                "collector_url": _COLLECTOR,
                "event_pattern": event_pattern,
                "negative_control": negative_control,
                "burst_count": burst_count,
                "sleep_seconds": 0.0,
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


def _run_with_stub(monkeypatch, **kw):
    stub = _RecordingClient()
    monkeypatch.setattr(ThirdPartyVpnEmitter, "_build_client", lambda self, params: stub)
    state = _run(CampaignExecutor(audit=AuditLogger(file_path=None)).execute(_campaign(**kw)))
    return state, stub


class TestParams:
    def test_three_patterns(self):
        assert _list_event_patterns() == sorted(_EVENT_PATTERNS)
        assert len(_EVENT_PATTERNS) == 3

    def test_dataset(self):
        assert ThirdPartyVpnParams.model_validate({"collector_url": _COLLECTOR}).dataset == _DATASET

    def test_bad_user_rejected(self):
        with pytest.raises(Exception, match="user principal"):
            ThirdPartyVpnParams.model_validate({"collector_url": _COLLECTOR, "target_user": "nope"})


class TestPositiveIsDetectorTrue:
    def test_impossible_travel_two_distinct_countries(self, monkeypatch):
        state, stub = _run_with_stub(monkeypatch, event_pattern="impossible_travel")
        bodies = _bodies(stub)
        assert len(bodies) == 2
        assert all(b["auth_result"] == "success" for b in bodies)
        assert len({b["src_country"] for b in bodies}) == 2  # geo-distant
        assert all(b["dataset"] == _DATASET for b in bodies)

    def test_brute_force_success_burst_then_success(self, monkeypatch):
        state, stub = _run_with_stub(monkeypatch, event_pattern="brute_force_success")
        bodies = _bodies(stub)
        failures = [b for b in bodies if b["auth_result"] == "failure"]
        successes = [b for b in bodies if b["auth_result"] == "success"]
        assert len(failures) >= _BRUTE_FORCE_MIN
        assert len(successes) == 1  # the break-in after the burst

    def test_anomalous_geo_success_from_far_country(self, monkeypatch):
        state, stub = _run_with_stub(monkeypatch, event_pattern="anomalous_geo")
        bodies = _bodies(stub)
        assert len(bodies) == 1
        assert bodies[0]["auth_result"] == "success"
        assert bodies[0].get("anomalous_country") is True
        assert bodies[0]["src_country"] != "US"  # not the user's usual country


class TestNegativeControlMustNotFire:
    def test_impossible_travel_negative_same_country(self, monkeypatch):
        state, stub = _run_with_stub(monkeypatch, event_pattern="impossible_travel", negative_control=True)
        bodies = _bodies(stub)
        assert len(bodies) == 2
        assert len({b["src_country"] for b in bodies}) == 1  # no travel at all
        assert all(b.get("negative_control") for b in bodies)

    def test_brute_force_negative_single_success_no_failures(self, monkeypatch):
        state, stub = _run_with_stub(monkeypatch, event_pattern="brute_force_success", negative_control=True)
        bodies = _bodies(stub)
        assert [b["auth_result"] for b in bodies] == ["success"]
        assert not [b for b in bodies if b["auth_result"] == "failure"]

    def test_anomalous_geo_negative_usual_country(self, monkeypatch):
        state, stub = _run_with_stub(monkeypatch, event_pattern="anomalous_geo", negative_control=True)
        b = _bodies(stub)[0]
        assert b["src_country"] == "US"  # the home country
        assert b.get("negative_control") is True


class TestMetadata:
    def test_manifest(self):
        m = ThirdPartyVpnEmitter.analytics_manifest()
        assert m["data_sources"] == ["third_party_vpns"]
        assert m["supports_negative_control"] is True
        assert len(m["detectors"]) == 3

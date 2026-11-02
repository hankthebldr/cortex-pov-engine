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


def _campaign(*, event_pattern="portal_brute_force", negative_control=False, burst_count=8, **extra) -> Campaign:
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
                **extra,
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
    def test_four_patterns(self):
        assert _list_event_patterns() == sorted(_EVENT_PATTERNS)
        assert len(_EVENT_PATTERNS) == 4

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
        assert len(m["detectors"]) == 4


class TestGatewayConnectedWithoutAuth:
    def test_gateway_connected_without_auth_positive_single_record(self, monkeypatch):
        _, stub = _run_with_stub(
            monkeypatch, event_pattern="gateway_connected_without_auth",
            public_ip="198.51.100.55", private_ip="10.20.0.77",
            machine_name="HOST-9", endpoint_mac="02:00:00:00:00:09",
        )
        bodies = _bodies(stub)
        assert len(bodies) == 1
        b = bodies[0]
        assert b["eventid"] == "gateway-connected" and b["stage"] == "gateway-connected"
        assert b["status"] == "success"
        assert b["public_ip"] == "198.51.100.55" and b["private_ip"] == "10.20.0.77"
        assert b["machine_name"] == "HOST-9"
        assert b["endpoint_mac"] == "02:00:00:00:00:09"
        assert b["auth_method"] == "auth-cookie"
        assert b["preceding_auth_event"] == "NONE"
        assert b["gateway_connected_without_auth_marker"] is True

    def test_negative_control_emits_portal_auth_first(self, monkeypatch):
        _, stub = _run_with_stub(
            monkeypatch, event_pattern="gateway_connected_without_auth",
            negative_control=True,
        )
        bodies = _bodies(stub)
        assert len(bodies) == 2
        auth, conn = bodies
        assert auth["eventid"] == "portal-auth" and auth["stage"] == "portal-auth"
        assert auth["status"] == "success"
        assert auth["srcuser"] == conn["srcuser"]
        assert auth["public_ip"] == conn["public_ip"]
        assert auth["timestamp"] < conn["timestamp"]
        assert conn["eventid"] == "gateway-connected"
        assert conn["preceding_auth_event"] == "portal-auth"

    def test_default_public_ip_is_far_ip(self):
        from eal_simulator.plugins.globalprotect_emitter import _FAR_IP
        ev = GlobalProtectEmitter().build_events(
            GlobalProtectParams.model_validate(
                {"collector_url": _COLLECTOR, "event_pattern": "gateway_connected_without_auth"}
            ), sim_run_id="x", iteration=1,
        )[0]
        assert ev["public_ip"] == _FAR_IP
        assert ev["private_ip"].startswith("10.20.0.")

    def test_public_and_private_ip_validated(self):
        import pytest
        for f in ("public_ip", "private_ip"):
            for bad in ("1.2.3", "300.1.1.1", "a.b.c.d", ""):
                with pytest.raises(ValueError):
                    GlobalProtectParams.model_validate({"collector_url": _COLLECTOR, f: bad})

    def test_machine_name_non_empty(self):
        import pytest
        for f in ("machine_name", "endpoint_mac"):
            with pytest.raises(ValueError):
                GlobalProtectParams.model_validate({"collector_url": _COLLECTOR, f: "  "})

    def test_existing_patterns_unchanged(self, monkeypatch):
        counts = {}
        for pat in ("portal_brute_force", "impossible_travel", "anomalous_geo"):
            _, stub = _run_with_stub(monkeypatch, event_pattern=pat)
            counts[pat] = len(_bodies(stub))
        assert counts == {"portal_brute_force": 13, "impossible_travel": 2, "anomalous_geo": 1}

    def test_detector_entry(self):
        d = GlobalProtectEmitter.analytics_manifest()["detectors"][-1]
        assert d["mitre"] == "T1556"
        assert d["negative_control"] == "the same session preceded by a portal-auth success"

"""Tests for the ngfw_threat_emitter EAL plugin (analytics_emitter spine).

``httpx.AsyncClient`` is stubbed so no real traffic is generated. The plugin
emits a synthetic WildFire verdict (THREAT) log. Its hard rule: a synthetic log
may show detection and enrichment, never prevention, so ``action`` accepts only
``alert`` and ``allow``.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from eal_simulator import AuditLogger, Campaign, CampaignExecutor
from eal_simulator.plugins.ngfw_threat_emitter import (
    NgfwThreatEmitter,
    NgfwThreatEmitterParams,
    _DATASET,
)


_COLLECTOR = "https://collector.cortexsim-canary.invalid/logs/v1/event"
_SHA = "A" * 32 + "b" * 32
_BLOCK_MSG = "synthetic threat log may not claim a block"


def _run(coro):
    return asyncio.run(coro)


class _RecordingClient:
    """Stub ``httpx.AsyncClient`` capturing every POST the plugin makes."""

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


def _params(**kw) -> NgfwThreatEmitterParams:
    base = {"collector_url": _COLLECTOR, "file_sha256": _SHA, "file_name": "invoice.exe"}
    base.update(kw)
    return NgfwThreatEmitterParams.model_validate(base)


def _events(**kw) -> list[dict[str, Any]]:
    return NgfwThreatEmitter().build_events(
        _params(**kw), sim_run_id="cortexsim-x-i1-aa", iteration=1,
    )


class TestRegistration:
    def test_registered_with_default_registry(self):
        from eal_simulator import get_default_registry

        assert get_default_registry().has("ngfw_threat_emitter")

    def test_dataset(self):
        assert _DATASET == "panw_ngfw_threat_raw"
        assert _params().dataset == "panw_ngfw_threat_raw"


class TestParamValidation:
    @pytest.mark.parametrize("bad", ["", "abc", "g" * 64, "a" * 63, "a" * 65])
    def test_malformed_file_sha256_rejected(self, bad):
        with pytest.raises(ValueError):
            _params(file_sha256=bad)

    def test_file_sha256_required(self):
        with pytest.raises(ValueError):
            NgfwThreatEmitterParams.model_validate(
                {"collector_url": _COLLECTOR, "file_name": "x.exe"}
            )

    def test_file_name_required_non_empty(self):
        with pytest.raises(ValueError):
            _params(file_name="")

    def test_verdict_enum(self):
        with pytest.raises(ValueError):
            _params(verdict="clean")

    @pytest.mark.parametrize(
        "bad", ["Block", "DENY", "drop", "reset-both", "sinkhole"],
    )
    def test_forbidden_action_values_rejected(self, bad):
        with pytest.raises(ValueError, match=_BLOCK_MSG):
            _params(action=bad)

    @pytest.mark.parametrize("given,stored", [("alert", "alert"), ("ALLOW", "allow")])
    def test_alert_and_allow_accepted_lowercased(self, given, stored):
        assert _params(action=given).action == stored

    def test_default_action_is_alert(self):
        assert _params().action == "alert"

    def test_dst_pair_must_be_set_together(self):
        with pytest.raises(ValueError, match="together"):
            _params(dst_domain="evil.example")
        with pytest.raises(ValueError, match="together"):
            _params(dst_ip="198.51.100.9")

    @pytest.mark.parametrize("bad", ["10.0.0", "300.1.1.1", "a.b.c.d", "::1", ""])
    def test_src_ip_must_be_dotted_quad(self, bad):
        with pytest.raises(ValueError):
            _params(src_ip=bad)

    @pytest.mark.parametrize("field", ["src_host", "src_user"])
    @pytest.mark.parametrize("empty", ["", "   "])
    def test_empty_source_strings_rejected(self, field, empty):
        with pytest.raises(ValueError, match="non-empty"):
            _params(**{field: empty})

    @pytest.mark.parametrize("kw", [
        {"dst_domain": "", "dst_ip": "198.51.100.9"},
        {"dst_domain": "evil.example", "dst_ip": ""},
        {"dst_domain": "  ", "dst_ip": "198.51.100.9"},
    ])
    def test_empty_destination_strings_rejected(self, kw):
        with pytest.raises(ValueError, match="non-empty"):
            _params(**kw)

    def test_unknown_event_pattern_rejected(self):
        with pytest.raises(ValueError, match="event_pattern must be one of"):
            _params(event_pattern="nope")

    def test_negative_control_unsupported(self):
        with pytest.raises(ValueError, match="negative_control is not supported"):
            _params(negative_control=True)


class TestRecordShape:
    def test_record_fields(self):
        (evt,) = _events()
        assert evt["dataset"] == "panw_ngfw_threat_raw"
        assert evt["subtype"] == "wildfire"
        assert evt["filedigest"] == _SHA.lower()
        assert evt["filename"] == "invoice.exe"
        assert evt["filetype"] == "PE"
        assert evt["verdict"] == "malicious"
        assert evt["category"] == "wildfire-virus"
        assert evt["action"] == "alert"
        assert evt["app"] == "web-browsing"
        assert (evt["proto"], evt["dst_port"]) == ("tcp", 443)
        assert evt["severity"] == "critical"
        assert evt["wildfire_verdict_marker"] is True
        assert evt["cortexsim_run_id"] == "cortexsim-x-i1-aa"
        assert evt["threat_name"]
        assert evt["dst_domain"] == evt["url_domain"]

    def test_non_malicious_verdict_is_medium(self):
        assert _events(verdict="grayware")[0]["severity"] == "medium"
        assert _events(verdict="benign")[0]["verdict"] == "benign"

    def test_dst_override(self):
        (evt,) = _events(dst_domain="evil.example", dst_ip="198.51.100.9")
        assert (evt["dst"], evt["dst_domain"], evt["url_domain"]) == (
            "198.51.100.9", "evil.example", "evil.example",
        )

    def test_src_overrides(self):
        (evt,) = _events(src_ip="192.168.1.50", src_host="WS-01", src_user="corp\\v")
        assert evt["src"] == "192.168.1.50"
        assert evt["endpoint_device_name"] == "WS-01"
        assert evt["user"] == "corp\\v"

    def test_src_unset_keeps_constants(self):
        a = _events()[0]
        b = _events(src_ip="10.9.9.9")[0]
        assert b["src"] == "10.9.9.9"
        assert (b["user"], b["endpoint_device_name"]) == (a["user"], a["endpoint_device_name"])

    def test_never_emits_a_block_action(self):
        for action in ("alert", "allow"):
            assert _events(action=action)[0]["action"] in {"alert", "allow"}


class TestPluginRun:
    def test_one_record_per_iteration(self, monkeypatch):
        stub = _RecordingClient()
        monkeypatch.setattr(NgfwThreatEmitter, "_build_client", lambda self, params: stub)
        campaign = Campaign.model_validate({
            "campaign_id": "CMP-NGFWTHREAT-001",
            "name": "ngfw_threat_emitter test",
            "dry_run": False,
            "simulation_authorized": True,
            "authorized_by": "tester",
            "target_allowlist": ["collector.cortexsim-canary.invalid"],
            "steps": [{
                "step_id": "step-01",
                "plugin": "ngfw_threat_emitter",
                "params": {
                    "collector_url": _COLLECTOR,
                    "file_sha256": _SHA,
                    "file_name": "invoice.exe",
                    "dst_domain": "evil.example",
                    "dst_ip": "198.51.100.9",
                    "iterations": 3,
                    "sleep_seconds": 0.0,
                },
            }],
        })
        _run(CampaignExecutor(audit=AuditLogger(file_path=None)).execute(campaign))
        assert len(stub.requests) == 3
        for r in stub.requests:
            body = json.loads(r["content"])
            assert body["filedigest"] == _SHA.lower()
            assert body["verdict"] == "malicious"
            assert body["dst_domain"] == "evil.example"
            assert body["dataset"] == "panw_ngfw_threat_raw"


class TestManifestAndWhitelist:
    def test_manifest_declares_one_detector(self):
        dets = NgfwThreatEmitter.analytics_manifest()["detectors"]
        assert len(dets) == 1
        assert dets[0]["alert"] == "WildFire malicious verdict on downloaded file"

    def test_block_with_unknown_kwarg_still_refused(self):
        with pytest.raises(ValueError, match=_BLOCK_MSG):
            _params(action="block", bogus_field="x")

    def test_unknown_kwarg_never_changes_action(self):
        assert _params(bogus_field="block").action == "alert"


class TestThreatNameOverride:
    """Final review M6: threat_name replaces the hard-coded canary name."""

    def test_default_threat_name_unchanged(self):
        assert _events()[0]["threat_name"] == "Virus/Win32.WGeneric.canary"

    def test_threat_name_override(self):
        ev = _events(threat_name="Trojan/Win64.RustyStealer.a")[0]
        assert ev["threat_name"] == "Trojan/Win64.RustyStealer.a"

    def test_threat_name_override_applies_to_any_verdict(self):
        assert _events(verdict="grayware", threat_name="Grayware/Win32.X")[0]["threat_name"] == "Grayware/Win32.X"

    @pytest.mark.parametrize("empty", ["", "   "])
    def test_threat_name_non_empty_when_set(self, empty):
        with pytest.raises(ValueError, match="non-empty"):
            _params(threat_name=empty)

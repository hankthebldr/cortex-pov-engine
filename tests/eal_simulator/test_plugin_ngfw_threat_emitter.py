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
    if kw.get("event_pattern") == "vulnerability_inbound":
        # the inbound pattern rejects WildFire params, so only pass what is asked
        base = {"collector_url": _COLLECTOR}
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

    def test_dst_domain_alone_rejected(self):
        with pytest.raises(ValueError, match="dst_domain requires dst_ip"):
            _params(dst_domain="evil.example")

    def test_dst_ip_alone_blanks_domain_fields(self):
        rec = _events(dst_ip="198.51.100.9")[0]
        assert rec["dst"] == "198.51.100.9"
        assert rec["dst_domain"] == "" and rec["url_domain"] == ""

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
    def test_manifest_declares_two_detectors(self):
        dets = NgfwThreatEmitter.analytics_manifest()["detectors"]
        assert len(dets) == 2
        assert dets[0]["alert"] == "WildFire malicious verdict on downloaded file"
        assert dets[1]["alert"] == (
            "Vulnerability threat from a publicly routed source against a web server"
        )
        assert dets[1]["mitre"] == "T1190"
        assert dets[1]["dataset"] == "panw_ngfw_threat_raw"
        assert dets[1]["key_fields"] == ["subtype", "src", "threat_id"]
        assert "T1190" in NgfwThreatEmitter.analytics_manifest()["mitre_techniques"]

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


class TestVulnerabilityInbound:
    def test_vulnerability_inbound_record_fields(self):
        rec = _events(
            event_pattern="vulnerability_inbound", file_sha256=None, file_name=None,
            threat_id=91234, uri="/cgi-bin/x.php", victim_ip="10.0.5.5",
            victim_host="WEB-01", dst_port=8443, app="web-browsing",
        )[0]
        assert rec["subtype"] == "vulnerability"
        assert rec["category"] == "code-execution"
        assert rec["severity"] == "critical"
        assert rec["direction"] == "client-to-server"
        assert rec["src"] == "198.51.100.23"
        assert rec["dst"] == "10.0.5.5"
        assert (rec["src_zone"], rec["dst_zone"]) == ("untrust", "trust")
        assert rec["rule"] == "allow-inbound-web"
        assert rec["misc"] == "/cgi-bin/x.php"
        assert rec["threat_id"] == 91234
        assert rec["dst_port"] == 8443
        assert rec["endpoint_device_name"] == "WEB-01"
        assert rec["threat_name"] == "CortexSim synthetic web-application exploit attempt"
        assert rec["vulnerability_inbound_marker"] is True
        assert rec["action"] == "alert"
        assert "filedigest" not in rec and "verdict" not in rec
        for k in ("filename", "filetype", "wildfire_verdict_marker"):
            assert k not in rec

    def test_vulnerability_inbound_defaults(self):
        rec = _events(event_pattern="vulnerability_inbound", file_sha256=None, file_name=None)[0]
        assert rec["dst"] == "10.0.12.34"
        assert rec["dst_port"] == 443
        assert rec["misc"] == "/index.php"
        assert rec["threat_id"] == 0
        assert rec["endpoint_device_name"] == "WKS-CORTEXSIM-0007"

    def test_vulnerability_inbound_needs_no_file_fields(self):
        p = NgfwThreatEmitterParams.model_validate(
            {"collector_url": _COLLECTOR, "event_pattern": "vulnerability_inbound"}
        )
        assert p.file_sha256 is None and p.file_name is None

    @pytest.mark.parametrize("missing", ["file_sha256", "file_name"])
    def test_wildfire_still_requires_file_fields(self, missing):
        base = {"collector_url": _COLLECTOR, "file_sha256": _SHA, "file_name": "a.exe"}
        del base[missing]
        with pytest.raises(ValueError, match="required for wildfire_malicious_file"):
            NgfwThreatEmitterParams.model_validate(base)

    @pytest.mark.parametrize(
        "bad", ["block", "deny", "drop", "reset-both", "sinkhole", "Block"],
    )
    def test_vulnerability_inbound_refuses_block(self, bad):
        with pytest.raises(ValueError, match=_BLOCK_MSG):
            _params(event_pattern="vulnerability_inbound", action=bad)

    @pytest.mark.parametrize("bad", ["10.0.0", "300.1.1.1", "a.b.c.d", ""])
    def test_victim_ip_dotted_quad(self, bad):
        with pytest.raises(ValueError):
            _params(victim_ip=bad)

    @pytest.mark.parametrize("bad", [0, 65536])
    def test_dst_port_bounds(self, bad):
        with pytest.raises(ValueError):
            _params(dst_port=bad)


class TestVulnerabilityInboundContract:
    @pytest.mark.parametrize("field,value", [
        ("dst_ip", "198.51.100.9"),
        ("dst_domain", "evil.example"),
        ("file_sha256", "a" * 64),
        ("file_name", "x.exe"),
        ("file_type", "ELF"),
        ("verdict", "grayware"),
    ])
    def test_vulnerability_inbound_rejects_wildfire_and_dst_params(self, field, value):
        kw = {"event_pattern": "vulnerability_inbound", field: value}
        if field == "dst_domain":
            kw["dst_ip"] = "198.51.100.9"
            with pytest.raises(ValueError, match="is not used by vulnerability_inbound"):
                _params(**kw)
            return
        with pytest.raises(ValueError, match=f"{field} is not used by vulnerability_inbound"):
            _params(**kw)

    def test_vulnerability_inbound_defaults_still_validate(self):
        # file_type / verdict at their defaults, victim_ip / victim_host set.
        rec = _events(event_pattern="vulnerability_inbound",
                      victim_ip="10.0.9.9", victim_host="WEB-01")[0]
        assert rec["dst"] == "10.0.9.9"

    def test_vulnerability_inbound_carries_every_field_the_unit42_hunt_references(self):
        import json
        import re
        from pathlib import Path

        card = json.loads(next(
            (Path(__file__).resolve().parents[2] / "detection_scanner" / "ttps")
            .glob("TTP-2026-0196-*.json")).read_text())
        query = next(
            o["query"] for o in card["detections"]["xql_queries"]
            if "panw_ngfw_threat_raw" in (o.get("query") or "")
        )
        # Explicit list, cross-checked against the verbatim query text.
        expected = {"sub_type", "to_zone", "source_ip", "dest_ip", "dest_port",
                    "nat_dest", "threat_id", "threat_name", "action", "severity"}
        for name in expected:
            assert re.search(rf"\b{name}\b", query), f"{name} not in the hunt query"
        # Regex-derived: filter LHS, by-keys, values()/count_distinct()/incidr() args.
        derived = set(re.findall(r"filter\s+(?:not\s*\()?\s*([a-z_]+)\s*(?:=|!=|!~=|~=)", query))
        derived |= set(re.findall(r"(?:values|count_distinct|incidr)\(\s*([a-z_]+)", query))
        for m in re.finditer(r"\bby\s+([a-z_,\s]+)$", query, re.M):
            derived |= {t.strip() for t in m.group(1).split(",") if t.strip()}
        derived.discard("_time")
        assert derived <= expected | {"threat_name"}, derived - expected
        rec = _events(event_pattern="vulnerability_inbound")[0]
        missing = sorted((expected | derived) - set(rec))
        assert not missing, f"record lacks fields the hunt references: {missing}"
        assert rec["sub_type"] == rec["subtype"]
        assert rec["source_ip"] == rec["src"] and rec["dest_ip"] == rec["dst"]
        assert rec["nat_dest"] == rec["dst"] and rec["dest_port"] == rec["dst_port"]
        assert rec["to_zone"] == rec["dst_zone"] and rec["from_zone"] == rec["src_zone"]

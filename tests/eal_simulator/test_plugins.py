"""Plugin-level tests — dry-run path for every built-in plugin.

We exercise dry_run=True so the tests don't emit real network packets, which
both keeps CI hermetic and confirms the safety branches work.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from eal_simulator import (
    AuditLogger,
    Campaign,
    CampaignExecutor,
    get_default_registry,
)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


_PLUGIN_LABEL = {
    "c2_http_beacon": "C2",
    "dns_tunnel_exfil": "DNS",
    "bulk_https_exfil": "BULK",
    "stratum_tcp_connect": "STRA",
    "smb_rpc_sweep": "SMB",
}


@pytest.fixture
def executor() -> CampaignExecutor:
    reg = get_default_registry()
    return CampaignExecutor(registry=reg, audit=AuditLogger(file_path=None))


@pytest.mark.parametrize("plugin,params", [
    ("c2_http_beacon", {
        "target_url": "http://testmynids.org/uid/index.html",
        "iterations": 3,
        "sleep_seconds": 0.1,
        "jitter_pct": 10,
    }),
    ("dns_tunnel_exfil", {
        "base_domain": "testmynids.org",
        "chunks": 5,
        "chunk_size_bytes": 8,
        "sleep_seconds": 0.0,
    }),
    ("bulk_https_exfil", {
        "target_url": "https://exfil-receiver.testmynids.org/upload",
        "total_bytes": 1024,
        "chunk_bytes": 512,
        "request_count": 1,
    }),
    ("stratum_tcp_connect", {
        "target_host": "10.50.0.42",
        "target_port": 3333,
        "iterations": 1,
        "sleep_seconds": 0.0,
    }),
    ("smb_rpc_sweep", {
        "target_cidr": "10.50.10.0/30",
        "ports": [445],
        "max_hosts": 2,
    }),
])
def test_plugin_dry_run_completes(executor: CampaignExecutor, plugin: str, params: dict[str, Any]):
    campaign = Campaign.model_validate({
        "campaign_id": f"CMP-{_PLUGIN_LABEL[plugin]}-001",
        "name": f"dry-run {plugin}",
        "dry_run": True,
        "steps": [{"step_id": "step-01", "plugin": plugin, "params": params}],
    })
    state = _run(executor.execute(campaign))
    assert state.status == "complete", f"{plugin}: {state.error}"
    assert len(state.step_results) == 1
    sr = state.step_results[0]
    assert sr.status == "success", f"{plugin}: {sr.error}"
    assert sr.detail.get("dry_run") is True


@pytest.mark.parametrize("plugin,bad_params", [
    ("c2_http_beacon", {"target_url": "ftp://nope", "iterations": 1}),
    ("c2_http_beacon", {"target_url": "http://x.test", "iterations": 0}),
    ("dns_tunnel_exfil", {"base_domain": "", "chunks": 1}),
    ("dns_tunnel_exfil", {"base_domain": "x.test", "encoding": "rot13"}),
    ("bulk_https_exfil", {"target_url": "ftp://x", "total_bytes": 1}),
    ("bulk_https_exfil", {"target_url": "https://x.test", "total_bytes": 0}),
    ("stratum_tcp_connect", {"target_host": "x", "target_port": 0}),
    ("stratum_tcp_connect", {"target_host": "x", "target_port": 3333,
                              "wallet": "bad wallet with space"}),
    ("smb_rpc_sweep", {"target_cidr": "not-a-cidr"}),
    ("smb_rpc_sweep", {"target_cidr": "10.0.0.0/24", "ports": []}),
])
def test_plugin_param_validation_rejects_bad_input(plugin: str, bad_params: dict[str, Any]):
    reg = get_default_registry()
    cls = reg.get(plugin)
    with pytest.raises(Exception):
        cls.validate_params(bad_params)


def test_c2_http_beacon_metadata_lists_eal_targets():
    reg = get_default_registry()
    meta = reg.get("c2_http_beacon").metadata()
    assert "Unusual User-Agent" in meta["eal_targets"]
    assert "T1071.001" in meta["mitre_techniques"]


def test_stratum_metadata_marks_well_known_ports():
    reg = get_default_registry()
    meta = reg.get("stratum_tcp_connect").metadata()
    assert meta["mitre_techniques"] == ["T1496"]


def test_dns_tunnel_dry_run_emits_event(executor: CampaignExecutor):
    campaign = Campaign.model_validate({
        "campaign_id": "CMP-DNS-002",
        "name": "dry-run dns",
        "dry_run": True,
        "steps": [{
            "step_id": "step-01",
            "plugin": "dns_tunnel_exfil",
            "params": {"base_domain": "testmynids.org", "chunks": 2},
        }],
    })
    state = _run(executor.execute(campaign))
    sr = state.step_results[0]
    assert sr.events_emitted >= 1
    assert sr.detail["chunks_planned"] == 2

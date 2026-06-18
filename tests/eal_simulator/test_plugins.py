"""Plugin-level tests — dry-run path for every built-in plugin.

We exercise dry_run=True so the tests don't emit real network packets, which
both keeps CI hermetic and confirms the safety branches work.

The shared parametrized matrix (``test_plugin_dry_run_completes``) covers every
plugin whose dry-run path resolves purely from params. The two CLI-backed
plugins (``airs_prompt_attack`` and ``browser_attack_runner``) need a stub
binary (and, for the browser, a campaign file) on disk, so they get dedicated
dry-run tests below — keeping the dry-run regression contract for all 13
built-in plugins in this one file (closes EAL-G04).
"""
from __future__ import annotations

import asyncio
import json
import stat
import textwrap
from pathlib import Path
from typing import Any

import pytest

from eal_simulator import (
    AuditLogger,
    Campaign,
    CampaignExecutor,
    get_default_registry,
)


def _run(coro):
    return asyncio.run(coro)


_PLUGIN_LABEL = {
    "c2_http_beacon": "C2",
    "dns_tunnel_exfil": "DNS",
    "bulk_https_exfil": "BULK",
    "stratum_tcp_connect": "STRA",
    "smb_rpc_sweep": "SMB",
    "ftp_egress": "FTP",
    "ssh_egress": "SSH",
    "llm_provider_egress": "LLM",
    "oauth_grant_emulator": "OAUTH",
    "idp_signin_emulator": "IDP",
    "agentic_egress": "AGENT",
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
    ("ftp_egress", {
        "target_host": "ftp-sinkhole.invalid",
        "iterations": 1,
        "sleep_seconds": 0.0,
    }),
    ("ssh_egress", {
        "target_host": "ssh-sinkhole.invalid",
        "iterations": 1,
        "sleep_seconds": 0.0,
    }),
    ("llm_provider_egress", {
        "provider": "openai",
        "iterations": 1,
        "sleep_seconds": 0.0,
    }),
    ("oauth_grant_emulator", {
        "provider": "okta",
        "iterations": 1,
        "sleep_seconds": 0.0,
    }),
    ("idp_signin_emulator", {
        "collector_url": "https://idp-collector.invalid/events",
        "iterations": 1,
        "sleep_seconds": 0.0,
    }),
    ("agentic_egress", {
        # mcp_server -> mcp/<artifact_name>; this typosquat artifact ships in
        # the in-tree cortex-malicious-agentic-pack so dry-run can resolve it.
        "target_url": "https://staging.invalid/upload",
        "component": "mcp_server",
        "artifact_name": "anthroopic-calculator",
        "iterations": 1,
        "sleep_seconds": 0.0,
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


def test_smb_sweep_authorises_every_host_not_just_base(executor: CampaignExecutor):
    """Regression: a partial-overlap allowlist must NOT permit out-of-scope sweeps.

    Codex review on PR #9 flagged that smb_rpc_sweep called ``authorise``
    once on the network base and then iterated freely. We construct a
    campaign whose allowlist only admits the base address (10.99.0.0)
    but not the rest of the /30 sweep, and verify the plugin skips the
    out-of-scope hosts in live mode rather than emitting connect attempts.
    """
    campaign = Campaign.model_validate({
        "campaign_id": "CMP-SMB-REGRESSION-001",
        "name": "smb sweep partial allowlist",
        "dry_run": False,
        "simulation_authorized": True,
        "authorized_by": "tester",
        # Only the network base is authorised; siblings are out of scope.
        "target_allowlist": ["10.99.0.0"],
        "steps": [{
            "step_id": "step-01",
            "plugin": "smb_rpc_sweep",
            "params": {
                "target_cidr": "10.99.0.0/30",
                "ports": [445],
                "max_hosts": 4,
                "connect_timeout": 0.1,
                "inter_host_delay": 0.0,
            },
        }],
    })
    state = _run(executor.execute(campaign))
    sr = state.step_results[0]
    assert sr.status == "success"
    # Every host inside 10.99.0.0/30 except .0 should be skipped.
    assert sr.detail["hosts_skipped_unauthorised"] >= 1
    assert sr.detail["hosts_probed"] == 0


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


# --- CLI-backed plugins -------------------------------------------------------
# These two plugins shell out to an external binary, so their dry-run path needs
# a stub binary (and, for the browser, a campaign file) on disk. They cannot ride
# the simple ``(plugin, params)`` matrix above, but we keep their dry-run
# regression coverage in this central file so all 13 built-in plugins are
# exercised here (closes EAL-G04).

def _make_stub_cli(tmp_path: Path, name: str) -> Path:
    """Write a tiny executable that emits one JSONL run-meta line on stdout."""
    bin_path = tmp_path / name
    bin_path.write_text(
        "#!/usr/bin/env bash\n"
        "cat <<'EOF'\n"
        '{"entry_type": "run_meta"}\n'
        "EOF\n",
        encoding="utf-8",
    )
    bin_path.chmod(bin_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return bin_path


def test_airs_prompt_attack_dry_run_completes(executor: CampaignExecutor, tmp_path: Path):
    # The plugin resolves the binary even in dry-run, so provide a stub.
    stub = _make_stub_cli(tmp_path, "cortex-prompt-attacker")
    campaign = Campaign.model_validate({
        "campaign_id": "CMP-AIRS-001",
        "name": "dry-run airs",
        "dry_run": True,
        "steps": [{
            "step_id": "step-01",
            "plugin": "airs_prompt_attack",
            "params": {
                "target_url": "http://canary.invalid/owasp/llm01/chat",
                "probes_dir": str(tmp_path),
                "binary": str(stub),
                "iterations": 1,
            },
        }],
    })
    state = _run(executor.execute(campaign))
    assert state.status == "complete", state.error
    sr = state.step_results[0]
    assert sr.status == "success", sr.error
    assert sr.detail.get("dry_run") is True


def test_browser_attack_runner_dry_run_completes(executor: CampaignExecutor, tmp_path: Path):
    stub = _make_stub_cli(tmp_path, "cortex-browser-attacker")
    campaign_file = tmp_path / "campaign.yml"
    campaign_file.write_text(textwrap.dedent("""
        campaign_id: BC-BROWSER-100
        name: smoke
        browser_channel: stub
        actions:
          - action: navigate
            params:
              url: https://allowed.invalid/
    """).strip(), encoding="utf-8")
    campaign = Campaign.model_validate({
        "campaign_id": "CMP-BROWSER-001",
        "name": "dry-run browser",
        "dry_run": True,
        "steps": [{
            "step_id": "step-01",
            "plugin": "browser_attack_runner",
            "params": {
                "campaign_path": str(campaign_file),
                "allowlist_host": "allowed.invalid",
                "binary": str(stub),
                "browser_channel": "stub",
                "headless": True,
                "timeout_seconds": 30.0,
            },
        }],
    })
    state = _run(executor.execute(campaign))
    assert state.status == "complete", state.error
    sr = state.step_results[0]
    assert sr.status == "success", sr.error
    assert sr.detail.get("dry_run") is True

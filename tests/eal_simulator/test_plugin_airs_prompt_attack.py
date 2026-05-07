"""Tests for the airs_prompt_attack EAL plugin.

The plugin shells out to the cortex-prompt-attacker CLI; we test it by
substituting a stub binary that emits canned JSONL on stdout, so the
test suite does not depend on the attacker package being installed.
"""
from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
from pathlib import Path

import pytest

from eal_simulator import AuditLogger, Campaign, CampaignExecutor


def _make_stub_attacker(tmp_path: Path, jsonl_lines: list[dict]) -> Path:
    """Write a tiny shell script that emits the supplied JSONL on stdout."""
    payload = "\n".join(json.dumps(line) for line in jsonl_lines)
    bin_path = tmp_path / "cortex-prompt-attacker"
    bin_path.write_text(
        f"#!/usr/bin/env bash\n"
        f"cat <<'EOF'\n{payload}\nEOF\n"
        f"echo '{{\"summary\": {{\"attempts_run\": {len(jsonl_lines) - 1}}}}}' >&2\n",
        encoding="utf-8",
    )
    bin_path.chmod(bin_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return bin_path


def _campaign(*, target_url: str, probes_dir: str, binary: str) -> Campaign:
    return Campaign.model_validate({
        "campaign_id": "CMP-AIRS-INTEG-001",
        "name": "airs plugin test",
        "dry_run": False,
        "simulation_authorized": True,
        "authorized_by": "tester",
        "target_allowlist": ["canary.invalid"],
        "steps": [{
            "step_id": "step-01",
            "plugin": "airs_prompt_attack",
            "params": {
                "target_url": target_url,
                "probes_dir": probes_dir,
                "binary": binary,
                "iterations": 1,
                "timeout_seconds": 30.0,
            },
        }],
    })


def _run_async(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_plugin_dry_run_does_not_invoke_binary(tmp_path: Path):
    # No binary on PATH; dry_run should still succeed.
    campaign = Campaign.model_validate({
        "campaign_id": "CMP-AIRS-DRY-001",
        "name": "dry",
        "dry_run": True,
        "steps": [{
            "step_id": "step-01",
            "plugin": "airs_prompt_attack",
            "params": {
                "target_url": "http://canary.invalid/owasp/llm01/chat",
                "probes_dir": str(tmp_path),
                "binary": str(tmp_path / "cortex-prompt-attacker"),  # doesn't exist
                "iterations": 1,
            },
        }],
    })
    # In dry-run the plugin still resolves the binary first; create a stub
    # so resolution succeeds.
    stub = _make_stub_attacker(tmp_path, [{"entry_type": "run_meta"}])
    campaign.steps[0].params["binary"] = str(stub)

    executor = CampaignExecutor(audit=AuditLogger(file_path=None))
    state = _run_async(executor.execute(campaign))
    sr = state.step_results[0]
    assert sr.status == "success"
    assert sr.detail["dry_run"] is True


def test_plugin_missing_binary_returns_error(tmp_path: Path):
    campaign = Campaign.model_validate({
        "campaign_id": "CMP-AIRS-NOBIN-001",
        "name": "missing binary",
        "dry_run": False,
        "simulation_authorized": True,
        "authorized_by": "t",
        "target_allowlist": ["canary.invalid"],
        "steps": [{
            "step_id": "step-01",
            "plugin": "airs_prompt_attack",
            "params": {
                "target_url": "http://canary.invalid/owasp/llm01/chat",
                "probes_dir": str(tmp_path),
                "binary": str(tmp_path / "absolutely-not-on-path"),
                "iterations": 1,
            },
        }],
    })
    state = _run_async(CampaignExecutor(audit=AuditLogger(file_path=None)).execute(campaign))
    assert state.step_results[0].status == "error"
    assert "not found" in (state.step_results[0].error or "")


def test_plugin_consumes_jsonl_and_increments_counts(tmp_path: Path):
    jsonl = [
        {"entry_type": "run_meta", "@timestamp": "2026-05-07T00:00:00Z",
         "probes_total": 2, "target_url": "http://canary.invalid/owasp/llm01/chat",
         "mutators": ["noop"], "scorers": ["vulnerable_flag"]},
        {"entry_type": "attempt", "uuid": "u1",
         "probe_classname": "ignore_prev", "owasp_id": "LLM01",
         "outcome": "vuln", "detector_results": {"vulnerable_flag": True},
         "targets": ["http://canary.invalid/owasp/llm01/chat"]},
        {"entry_type": "attempt", "uuid": "u2",
         "probe_classname": "safe_probe", "owasp_id": "LLM01",
         "outcome": "clean", "detector_results": {"vulnerable_flag": False},
         "targets": ["http://canary.invalid/owasp/llm01/chat"]},
    ]
    stub = _make_stub_attacker(tmp_path, jsonl)
    campaign = _campaign(
        target_url="http://canary.invalid/owasp/llm01/chat",
        probes_dir=str(tmp_path),
        binary=str(stub),
    )
    state = _run_async(CampaignExecutor(audit=AuditLogger(file_path=None)).execute(campaign))
    sr = state.step_results[0]
    assert sr.status == "success", sr.error
    assert sr.events_emitted == 3  # run_meta + 2 attempts
    assert sr.detail["attempts_run"] == 2
    assert sr.detail["vuln_count"] == 1
    assert sr.detail["clean_count"] == 1


def test_plugin_safety_violation_when_target_not_in_allowlist(tmp_path: Path):
    stub = _make_stub_attacker(tmp_path, [{"entry_type": "run_meta"}])
    campaign = Campaign.model_validate({
        "campaign_id": "CMP-AIRS-SAFETY-001",
        "name": "safety",
        "dry_run": False,
        "simulation_authorized": True,
        "authorized_by": "t",
        "target_allowlist": ["allowed.invalid"],
        "steps": [{
            "step_id": "step-01",
            "plugin": "airs_prompt_attack",
            "params": {
                "target_url": "http://other.invalid/owasp/llm01/chat",
                "probes_dir": str(tmp_path),
                "binary": str(stub),
                "iterations": 1,
            },
        }],
    })
    state = _run_async(CampaignExecutor(audit=AuditLogger(file_path=None)).execute(campaign))
    assert state.step_results[0].status == "error"
    assert "safety_violation" in (state.step_results[0].error or "")


def test_plugin_metadata_lists_eal_targets():
    from eal_simulator import get_default_registry

    cls = get_default_registry().get("airs_prompt_attack")
    meta = cls.metadata()
    assert "AIRS Prompt Injection" in meta["eal_targets"]
    assert "T1656" in meta["mitre_techniques"]

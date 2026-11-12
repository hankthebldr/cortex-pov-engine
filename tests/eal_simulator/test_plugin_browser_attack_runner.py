"""Tests for the browser_attack_runner EAL plugin.

The plugin runs the cortex-browser-attacker it finds on SimCore's PATH; we
put a stub of that name first on PATH that emits canned JSONL on stdout, so
the test suite does not depend on the real CLI being installed.
"""
from __future__ import annotations

import asyncio
import json
import os
import stat
import textwrap
from pathlib import Path

import pytest

from eal_simulator import AuditLogger, Campaign, CampaignExecutor


def _make_stub_binary(tmp_path: Path, jsonl_lines: list[dict],
                      summary_lines: int = 0) -> Path:
    payload = "\n".join(json.dumps(line) for line in jsonl_lines)
    bin_path = tmp_path / "cortex-browser-attacker"
    summary_obj = {"summary": {"actions_run": max(0, len(jsonl_lines) - 1)}}
    bin_path.write_text(
        f"#!/usr/bin/env bash\n"
        f"cat <<'EOF'\n{payload}\nEOF\n"
        f"echo '{json.dumps(summary_obj)}' >&2\n",
        encoding="utf-8",
    )
    bin_path.chmod(bin_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return bin_path


def _make_campaign_file(tmp_path: Path) -> Path:
    p = tmp_path / "campaign.yml"
    p.write_text(textwrap.dedent("""
        campaign_id: BC-BROWSER-100
        name: smoke
        browser_channel: stub
        actions:
          - action: navigate
            params:
              url: https://allowed.invalid/
    """).strip())
    return p


def _on_path(monkeypatch, directory: Path) -> None:
    """Put ``directory`` first on SimCore's PATH, where the plugin looks."""
    monkeypatch.setenv("PATH", f"{directory}{os.pathsep}{os.environ.get('PATH', '')}")


def _campaign(*, allowlist_host: str, campaign_path: str,
              dry_run: bool = False) -> Campaign:
    spec = {
        "campaign_id": "CMP-BROWSER-INTEG-001",
        "name": "browser plugin test",
        "dry_run": dry_run,
        "steps": [{
            "step_id": "step-01",
            "plugin": "browser_attack_runner",
            "params": {
                "campaign_path": campaign_path,
                "allowlist_host": allowlist_host,
                "browser_channel": "stub",
                "headless": True,
                "timeout_seconds": 30.0,
            },
        }],
    }
    if not dry_run:
        spec.update({
            "simulation_authorized": True,
            "authorized_by": "tester",
            "target_allowlist": ["allowed.invalid"],
        })
    return Campaign.model_validate(spec)


def _run(coro):
    return asyncio.run(coro)


def test_plugin_dry_run_does_not_invoke_binary(tmp_path: Path, monkeypatch):
    _make_stub_binary(tmp_path, [{"entry_type": "run_meta"}])
    _on_path(monkeypatch, tmp_path)
    campaign_path = _make_campaign_file(tmp_path)
    campaign = _campaign(
        allowlist_host="allowed.invalid",
        campaign_path=str(campaign_path),
        dry_run=True,
    )
    state = _run(CampaignExecutor(audit=AuditLogger(file_path=None)).execute(campaign))
    sr = state.step_results[0]
    assert sr.status == "success"
    assert sr.detail["dry_run"] is True


def test_plugin_missing_binary_returns_error(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "nothing-installed-here"))
    campaign_path = _make_campaign_file(tmp_path)
    campaign = _campaign(
        allowlist_host="allowed.invalid",
        campaign_path=str(campaign_path),
    )
    state = _run(CampaignExecutor(audit=AuditLogger(file_path=None)).execute(campaign))
    sr = state.step_results[0]
    assert sr.status == "error"
    assert "not found" in (sr.error or "")


def test_plugin_missing_campaign_file_returns_error(tmp_path: Path, monkeypatch):
    _make_stub_binary(tmp_path, [{"entry_type": "run_meta"}])
    _on_path(monkeypatch, tmp_path)
    campaign = _campaign(
        allowlist_host="allowed.invalid",
        campaign_path=str(tmp_path / "nonexistent.yml"),
    )
    state = _run(CampaignExecutor(audit=AuditLogger(file_path=None)).execute(campaign))
    sr = state.step_results[0]
    assert sr.status == "error"
    assert "campaign_path not found" in (sr.error or "")


def test_plugin_consumes_jsonl_and_increments_counts(tmp_path: Path, monkeypatch):
    jsonl = [
        {"entry_type": "run_meta", "@timestamp": "2026-05-08T00:00:00Z",
         "campaign_id": "BC-BROWSER-100", "name": "smoke",
         "action_count": 3, "browser_channel": "stub",
         "target_allowlist": ["allowed.invalid"]},
        {"entry_type": "action_attempt", "uuid": "u1",
         "action_name": "navigate", "outcome": "success",
         "target_url": "https://allowed.invalid/",
         "target_origin": "allowed.invalid"},
        {"entry_type": "action_attempt", "uuid": "u2",
         "action_name": "install_extension", "outcome": "blocked",
         "notes": {"blocked_by_policy": True}},
        {"entry_type": "action_attempt", "uuid": "u3",
         "action_name": "paste", "outcome": "failure",
         "error": "selector not found"},
    ]
    _make_stub_binary(tmp_path, jsonl)
    _on_path(monkeypatch, tmp_path)
    campaign_path = _make_campaign_file(tmp_path)
    campaign = _campaign(
        allowlist_host="allowed.invalid",
        campaign_path=str(campaign_path),
    )
    state = _run(CampaignExecutor(audit=AuditLogger(file_path=None)).execute(campaign))
    sr = state.step_results[0]
    assert sr.status == "success", sr.error
    assert sr.events_emitted == 4  # run_meta + 3 actions
    assert sr.detail["actions_run"] == 3
    assert sr.detail["success_count"] == 1
    assert sr.detail["blocked_count"] == 1
    assert sr.detail["failure_count"] == 1


def test_plugin_safety_violation_when_target_not_in_allowlist(tmp_path: Path, monkeypatch):
    _make_stub_binary(tmp_path, [{"entry_type": "run_meta"}])
    _on_path(monkeypatch, tmp_path)
    campaign_path = _make_campaign_file(tmp_path)
    campaign = Campaign.model_validate({
        "campaign_id": "CMP-BROWSER-SAFETY-001",
        "name": "safety",
        "dry_run": False,
        "simulation_authorized": True,
        "authorized_by": "t",
        "target_allowlist": ["other-host.invalid"],
        "steps": [{
            "step_id": "step-01",
            "plugin": "browser_attack_runner",
            "params": {
                "campaign_path": str(campaign_path),
                "allowlist_host": "rogue.invalid",
                "browser_channel": "stub",
            },
        }],
    })
    state = _run(CampaignExecutor(audit=AuditLogger(file_path=None)).execute(campaign))
    sr = state.step_results[0]
    assert sr.status == "error"
    assert "safety_violation" in (sr.error or "")


def test_params_binary_is_never_the_executable(tmp_path: Path, monkeypatch):
    """The CLI comes from SimCore's PATH. A ``binary`` key in the step params is
    caller input and must not choose the program that runs."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = _make_stub_binary(bin_dir, [{"entry_type": "run_meta"}])
    _on_path(monkeypatch, bin_dir)
    marker = tmp_path / "caller-binary-ran"
    planted = tmp_path / "caller-chosen"
    planted.write_text(f"#!/bin/sh\ntouch '{marker}'\n", encoding="utf-8")
    planted.chmod(0o755)

    campaign = _campaign(
        allowlist_host="allowed.invalid",
        campaign_path=str(_make_campaign_file(tmp_path)),
    )
    campaign.steps[0].params["binary"] = str(planted)
    state = _run(CampaignExecutor(audit=AuditLogger(file_path=None)).execute(campaign))

    assert not marker.exists(), "the plugin ran the executable named in params.binary"
    assert state.step_results[0].detail["binary"] == str(stub)


def test_plugin_metadata_lists_eal_targets():
    from eal_simulator import get_default_registry

    cls = get_default_registry().get("browser_attack_runner")
    meta = cls.metadata()
    assert any("Prisma Browser" in t for t in meta["eal_targets"])
    assert "T1552" in meta["mitre_techniques"]
    assert "campaign_path" in meta["params_schema"]["properties"]

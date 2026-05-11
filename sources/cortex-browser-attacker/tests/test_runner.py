"""Runner tests — JSONL emission, allowlist enforcement, dry-run."""
from __future__ import annotations

import io
import json

import pytest

from cortex_browser_attacker.browser import StubDriver
from cortex_browser_attacker.campaign import BrowserCampaign
from cortex_browser_attacker.runner import Runner


def _campaign(**kw):
    base = {
        "campaign_id": "BC-BROWSER-001",
        "name": "test",
        "browser_channel": "stub",
        "actions": [
            {"action": "navigate", "params": {"url": "https://allowed.invalid/"}},
            {"action": "paste", "params": {"selector": "#p", "content": "x"}},
        ],
    }
    base.update(kw)
    return BrowserCampaign.model_validate(base)


def test_dry_run_emits_run_meta_then_action_lines():
    buf = io.StringIO()
    c = _campaign()  # dry_run defaults to True
    summary = Runner(StubDriver(), out_stream=buf).run(c)

    lines = [json.loads(L) for L in buf.getvalue().splitlines()]
    assert lines[0]["entry_type"] == "run_meta"
    assert lines[0]["action_count"] == 2
    attempts = [L for L in lines if L["entry_type"] == "action_attempt"]
    assert len(attempts) == 2
    assert all(L["notes"].get("dry_run") is True for L in attempts)
    assert summary.actions_run == 2 and summary.success_count == 2


def test_live_run_drives_stub_and_emits_real_outcomes():
    driver = StubDriver()
    buf = io.StringIO()
    c = _campaign(
        dry_run=False,
        simulation_authorized=True,
        authorized_by="t",
        target_allowlist=["allowed.invalid"],
    )
    summary = Runner(driver, out_stream=buf).run(c)
    assert summary.actions_run == 2
    assert summary.success_count == 2
    # StubDriver records: start, goto, type_into, stop
    methods = [call.method for call in driver.calls]
    assert "goto" in methods and "type_into" in methods


def test_safety_violation_on_non_allowlisted_navigate():
    driver = StubDriver()
    buf = io.StringIO()
    c = _campaign(
        dry_run=False,
        simulation_authorized=True,
        authorized_by="t",
        target_allowlist=["only-this.invalid"],
        actions=[
            {"action": "navigate", "params": {"url": "https://attacker.invalid/"}}
        ],
    )
    summary = Runner(driver, out_stream=buf).run(c)
    assert summary.failure_count == 1
    # No goto call — safety check refused the action before dispatch.
    assert not any(c.method == "goto" for c in driver.calls)
    line = [json.loads(L) for L in buf.getvalue().splitlines()
            if json.loads(L).get("entry_type") == "action_attempt"][0]
    assert "safety_violation" in line["error"]


def test_blocked_install_extension_counted_separately():
    driver = StubDriver(extension_blocked=True)
    buf = io.StringIO()
    c = _campaign(
        dry_run=False,
        simulation_authorized=True,
        authorized_by="t",
        target_allowlist=["allowed.invalid"],
        actions=[
            {"action": "navigate", "params": {"url": "https://allowed.invalid/"}},
            {"action": "install_extension", "params": {"crx_path": "/tmp/x.crx"}},
        ],
    )
    summary = Runner(driver, out_stream=buf).run(c)
    assert summary.success_count == 1
    assert summary.blocked_count == 1
    assert summary.failure_count == 0


def test_on_error_abort_stops_campaign():
    driver = StubDriver()
    buf = io.StringIO()
    c = _campaign(
        dry_run=False,
        simulation_authorized=True,
        authorized_by="t",
        target_allowlist=["only-this.invalid"],
        actions=[
            {"action": "navigate", "params": {"url": "https://attacker.invalid/"}, "on_error": "abort"},
            {"action": "paste", "params": {"selector": "#p", "content": "x"}},
        ],
    )
    summary = Runner(driver, out_stream=buf).run(c)
    # Aborted after step 1; step 2 never runs.
    assert summary.actions_run == 1
    assert summary.failure_count == 1


def test_subnet_in_allowlist_authorises_ip():
    driver = StubDriver()
    buf = io.StringIO()
    c = _campaign(
        dry_run=False,
        simulation_authorized=True,
        authorized_by="t",
        target_allowlist=["10.0.0.0/24"],
        actions=[
            {"action": "navigate", "params": {"url": "http://10.0.0.5/"}},
        ],
    )
    summary = Runner(driver, out_stream=buf).run(c)
    assert summary.success_count == 1

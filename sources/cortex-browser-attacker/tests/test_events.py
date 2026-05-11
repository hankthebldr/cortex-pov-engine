"""events.py — ActionResult → ECS mapper tests."""
from __future__ import annotations

from cortex_browser_attacker.events import action_result_to_ecs, run_meta_to_ecs


def test_action_result_to_ecs_carries_browser_action_name():
    result = {
        "action_name": "paste",
        "target_url": "https://x.invalid/signin",
        "target_origin": "x.invalid",
        "outcome": "success",
        "page_url": "https://x.invalid/signin",
        "page_title": "Sign in",
        "duration_seconds": 0.04,
        "expected_detection": "PB DLP",
        "cortex_canary": "CANARY-1",
        "notes": {"selector": "#password", "chars_typed": 12},
    }
    ev = action_result_to_ecs(result, campaign_id="BC-X-001",
                              run_id="r1", step_id="step-01")
    assert ev["event"]["action"] == "browser_paste"
    assert ev["event"]["outcome"] == "success"
    assert ev["cortexsim"]["campaign_id"] == "BC-X-001"
    assert ev["cortexsim"]["target_origin"] == "x.invalid"
    assert ev["cortexsim"]["cortex_canary"] == "CANARY-1"
    assert ev["url"]["full"] == "https://x.invalid/signin"
    assert ev["url"]["domain"] == "x.invalid"


def test_action_result_blocked_maps_to_success_outcome():
    """install_extension blocked-by-policy is a *positive* outcome —
    the policy fired. Ensure ECS event reflects that."""
    result = {
        "action_name": "install_extension",
        "outcome": "blocked",
        "notes": {"blocked_by_policy": True},
    }
    ev = action_result_to_ecs(result)
    assert ev["event"]["outcome"] == "success"
    assert ev["cortexsim"]["outcome"] == "blocked"


def test_action_result_failure_maps_to_failure():
    ev = action_result_to_ecs({"action_name": "download", "outcome": "failure"})
    assert ev["event"]["outcome"] == "failure"


def test_run_meta_to_ecs_emits_start_event():
    meta = {
        "@timestamp": "2026-05-08T00:00:00+00:00",
        "campaign_id": "BC-X-001",
        "name": "test",
        "action_count": 4,
        "browser_channel": "prisma",
        "target_allowlist": ["x.invalid"],
    }
    ev = run_meta_to_ecs(meta, run_id="r")
    assert ev["event"]["action"] == "browser_campaign_started"
    assert ev["cortexsim"]["browser_channel"] == "prisma"
    assert ev["cortexsim"]["action_count"] == 4


def test_drop_none_removes_unset_fields():
    ev = action_result_to_ecs({"action_name": "navigate", "outcome": "success"})
    assert "campaign_id" not in ev["cortexsim"]
    # url block was {full: None, domain: None} so it should be omitted.
    assert "url" not in ev or ev["url"] is None

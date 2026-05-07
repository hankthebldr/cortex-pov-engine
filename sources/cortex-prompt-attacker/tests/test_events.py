"""Attempt → ECS event mapper tests."""
from __future__ import annotations

from cortex_prompt_attacker.events import attempt_to_ecs, run_meta_to_ecs


def test_attempt_to_ecs_carries_owasp_and_outcome():
    attempt = {
        "uuid": "u1",
        "probe_classname": "ignore_previous",
        "owasp_id": "LLM01",
        "severity": "high",
        "outcome": "vuln",
        "mutators_applied": ["noop", "base64"],
        "duration_seconds": 0.1,
        "detector_results": {"system_prompt_leak": True, "secret_leak": False},
        "targets": ["http://canary/owasp/llm01/chat"],
    }
    ev = attempt_to_ecs(attempt, campaign_id="CMP-X-001", run_id="r1", step_id="step-01")
    assert ev["event"]["action"] == "airs_probe_attempt"
    assert ev["event"]["outcome"] == "success"  # vuln + clean both map to success
    assert ev["cortexsim"]["owasp_id"] == "LLM01"
    assert ev["cortexsim"]["outcome"] == "vuln"
    assert ev["cortexsim"]["detected_by"] == ["system_prompt_leak"]
    assert ev["cortexsim"]["target_url"] == "http://canary/owasp/llm01/chat"


def test_attempt_to_ecs_error_outcome_marks_failure():
    ev = attempt_to_ecs({"outcome": "error", "probe_classname": "x"})
    assert ev["event"]["outcome"] == "failure"


def test_run_meta_to_ecs_emits_start_event():
    meta = {
        "@timestamp": "2026-05-07T00:00:00+00:00",
        "probes_total": 5, "target_url": "http://t",
        "mutators": ["noop"], "scorers": ["x"],
    }
    ev = run_meta_to_ecs(meta, campaign_id="C", run_id="R")
    assert ev["event"]["action"] == "airs_probe_run_started"
    assert ev["event"]["type"] == ["start"]
    assert ev["cortexsim"]["probes_total"] == 5


def test_drop_none_removes_unset_fields():
    ev = attempt_to_ecs({"outcome": "clean"})
    assert "campaign_id" not in ev["cortexsim"]

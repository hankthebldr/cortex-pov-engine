"""Pure tests for engine.pairing (run pairing validation + report/block delta)."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Optional

from engine.pairing import build_pair_delta, validate_pair

NOW = datetime(2026, 10, 26, 12, 0, 0)


@dataclass(frozen=True)
class _R:
    scenario_id: str = "S"
    target: Optional[str] = "a1"
    agent_profile_mode: str = "report"
    status: str = "complete"
    completed_at: Optional[datetime] = NOW


def _ok(first, **kw):
    args = dict(scenario_id="S", target_agent_id="a1", agent_profile_mode="block")
    args.update(kw)
    return validate_pair(first, **args)


def test_validate_pair_rules():
    first = _R()
    assert _ok(first) is None
    assert "same scenario" in _ok(first, scenario_id="T")
    assert "same target" in _ok(first, target_agent_id="a2")
    assert "terminal" in _ok(replace(first, status="running", completed_at=None))
    assert "terminal" in _ok(replace(first, status="aborted"))
    assert "terminal" in _ok(replace(first, completed_at=None))
    assert "different" in _ok(first, agent_profile_mode="report")
    assert "report" in _ok(first, agent_profile_mode="unknown")
    assert "report" in _ok(replace(first, agent_profile_mode="unknown"))
    assert _ok(replace(first, status="failed")) is None
    assert _ok(replace(first, agent_profile_mode="block"), agent_profile_mode="report") is None


def _res(step, det, outcome, mttd=None, reason=None, module=None):
    return {"step_id": step, "step_name": f"name-{step}", "detection_id": det,
            "expected_detection": f"desc-{det}", "signal_type": "BIOC",
            "prevention_module": module, "outcome": outcome,
            "outcome_reason": reason, "mttd_seconds": mttd}


def test_build_pair_delta_joins_on_step_and_detection():
    report_run = {"run_id": "r1", "agent_profile_mode": "report", "severed_at_step": None}
    block_run = {"run_id": "r2", "agent_profile_mode": "block", "severed_at_step": "step-02"}
    report_results = [_res("step-01", "d1", "detected", 4.0), _res("step-02", "d2", "detected", 6.0)]
    block_results = [_res("step-02", "d2", "prevented", 1.0, "terminated", "btp"),
                     _res("step-01", "d1", "prevented", 2.0, "terminated", "btp")]
    # pass in swapped order: report side is chosen by mode, not position
    delta = build_pair_delta(block_run, block_results, report_run, report_results)
    assert delta["report_run_id"] == "r1" and delta["block_run_id"] == "r2"
    assert delta["severed_at_step"] == "step-02"
    assert [s["step_id"] for s in delta["steps"]] == ["step-01", "step-02"]
    d = delta["steps"][0]["detections"][0]
    assert d["detection_id"] == "d1" and d["prevention_module"] == "btp"
    assert d["report"] == {"outcome": "detected", "mttd_seconds": 4.0}
    assert d["block"] == {"outcome": "prevented", "outcome_reason": "terminated", "mttd_seconds": 2.0}


def test_build_pair_delta_one_sided_detection_has_none_side():
    report_run = {"run_id": "r1", "agent_profile_mode": "report"}
    block_run = {"run_id": "r2", "agent_profile_mode": "block"}
    delta = build_pair_delta(report_run, [_res("step-01", "d1", "detected", 3.0)],
                             block_run, [_res("step-01", "d9", "prevented")])
    dets = {d["detection_id"]: d for d in delta["steps"][0]["detections"]}
    assert dets["d1"]["block"] is None and dets["d1"]["report"]["outcome"] == "detected"
    assert dets["d9"]["report"] is None and dets["d9"]["block"]["outcome"] == "prevented"


def test_build_pair_delta_falls_back_to_expected_detection_key():
    report_run = {"run_id": "r1", "agent_profile_mode": "report"}
    block_run = {"run_id": "r2", "agent_profile_mode": "block"}
    r = _res("step-01", None, "detected")
    b = _res("step-01", None, "prevented")
    delta = build_pair_delta(report_run, [r], block_run, [b])
    assert len(delta["steps"][0]["detections"]) == 1

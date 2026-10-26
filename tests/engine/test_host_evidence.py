"""Host evidence: severed-step detection and projection onto Result rows."""
from __future__ import annotations

from types import SimpleNamespace as NS

from engine import host_evidence as he
from engine import outcome as oc


def _s(step_id, termination, reason=None):
    return NS(step_id=step_id, termination=termination, reason=reason)


def _r(step_id, expected="detect", **kw):
    return NS(step_id=step_id, expected_outcome=expected, outcome=None,
              outcome_reason=None, kpi_verdict=None, **kw)


def test_severed_step_is_first_host_witness():
    steps = [_s("step-01", "exited"), _s("step-02", "anchor_terminated"),
             _s("step-03", "signaled"), _s("step-04", "not_run", oc.UPSTREAM_SEVERED)]
    assert he.severed_step(steps) == "step-02"
    assert he.severed_step([_s("step-01", "exited"), _s("step-02", "timeout")]) is None
    assert he.severed_step([]) is None


def test_apply_host_outcomes_marks_not_reached_and_not_executed():
    steps = [_s("step-01", "exited"),
             _s("step-02", "not_run", oc.UPSTREAM_SEVERED),
             _s("step-03", "not_run", oc.TOOL_ABSENT),
             _s("step-04", "not_run", oc.UPSTREAM_NOT_EXECUTED),
             _s("step-05", "not_run", oc.UPSTREAM_FAILED)]
    rows = [_r(f"step-0{i}") for i in range(1, 6)] + [_r("step-02")]
    touched = he.apply_host_outcomes(rows, steps, agent_profile_mode="report")
    assert touched == 5
    assert [(r.outcome, r.outcome_reason) for r in rows] == [
        (None, None),
        ("not_reached", oc.UPSTREAM_SEVERED),
        ("not_executed", oc.TOOL_ABSENT),
        ("not_executed", oc.UPSTREAM_NOT_EXECUTED),
        ("not_reached", oc.UPSTREAM_FAILED),
        ("not_reached", oc.UPSTREAM_SEVERED),
    ]


def test_prevent_result_on_severed_step_is_pending_awaiting_alert():
    steps = [_s("step-02", "anchor_terminated")]
    prevent, detect = _r("step-02", "prevent"), _r("step-02", "detect")
    assert he.apply_host_outcomes([prevent, detect], steps, agent_profile_mode="block") == 1
    assert (prevent.outcome, prevent.outcome_reason, prevent.kpi_verdict) == (
        "pending", oc.PREVENTION_UNCONFIRMED_NO_ALERT, "pending")
    assert (detect.outcome, detect.kpi_verdict) == (None, None)


def test_prevent_result_in_report_mode_is_left_alone():
    prevent = _r("step-02", "prevent")
    assert he.apply_host_outcomes([prevent], [_s("step-02", "signaled")],
                                  agent_profile_mode="report") == 0
    assert prevent.outcome is None


def test_tenant_decided_outcome_is_not_clobbered():
    r = _r("step-03")
    r.outcome, r.outcome_reason = "prevented", None
    assert he.apply_host_outcomes([r], [_s("step-03", "not_run", oc.UPSTREAM_SEVERED)],
                                  agent_profile_mode="block") == 0
    assert (r.outcome, r.outcome_reason) == ("prevented", None)

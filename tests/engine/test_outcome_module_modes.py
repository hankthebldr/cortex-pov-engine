"""Per-module Action Mode grading (XDR protection-profile review, finding F1).

XDR sets Block / Report / Disabled per module. A Detected alert from a module
the profile told to Report is that module working as configured, not
``DETECTED_NOT_PREVENTED``.
"""
from __future__ import annotations

from engine.outcome import (
    DETECTED_NOT_PREVENTED,
    MODULE_DISABLED,
    MODULE_NOT_IN_BLOCK,
    PreventVerdict,
    evaluate_prevent,
    resolve_module_mode,
)


def _block(**kw):
    base = dict(agent_profile_mode="block", alert_outcome=None, termination=None,
                window_closed=False, block_mode_status=None)
    base.update(kw)
    return evaluate_prevent(**base)


def test_no_module_mode_keeps_run_level_block_grading():
    v = _block(alert_outcome="detected", termination="exited")
    assert v == PreventVerdict("detected", "fail", DETECTED_NOT_PREVENTED)


def test_report_module_in_block_run_reads_detected_not_fail():
    v = _block(alert_outcome="detected", termination="exited", module_mode="report")
    assert v == PreventVerdict("detected", None, MODULE_NOT_IN_BLOCK)


def test_disabled_module_silence_is_pending_not_missed():
    v = _block(termination="exited", window_closed=True, module_mode="disabled")
    assert v == PreventVerdict("pending", "pending", MODULE_DISABLED)


def test_resolver_returns_attested_mode_else_none():
    modes = {"btp": "report"}
    assert resolve_module_mode(agent_profile_mode="block", module_modes=modes,
                               prevention_module="btp") == "report"
    # Unattested module, no map, or a report run: the run-level mode governs.
    assert resolve_module_mode(agent_profile_mode="block", module_modes=modes,
                               prevention_module="local_analysis") is None
    assert resolve_module_mode(agent_profile_mode="block", module_modes=None,
                               prevention_module="btp") is None
    assert resolve_module_mode(agent_profile_mode="report", module_modes=modes,
                               prevention_module="btp") is None

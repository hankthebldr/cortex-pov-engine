"""Report-mode / block-mode run pairing.

A POV runs the same scenario twice on the same endpoint: once with the Cortex
XDR agent profile in report mode, then in block mode. The operator declares the
mode at launch (the engine never touches the profile) and the second launch
links to the first. This module validates that link and computes the per-
detection delta the report renders. Pure: no DB, no I/O.
"""
from __future__ import annotations

from typing import Any, Optional

from engine.outcome import PROFILE_MODES  # noqa: F401  (vocabulary owner)

_PAIRABLE_MODES = frozenset({"report", "block"})
_TERMINAL_STATUSES = frozenset({"complete", "failed"})


def validate_pair(
    first: Any,
    *,
    scenario_id: str,
    target_agent_id: Optional[str],
    agent_profile_mode: str,
) -> Optional[str]:
    """Return an error detail if a launch may not pair with ``first``, else None."""
    if first.scenario_id != scenario_id:
        return (
            f"a pair must run the same scenario: first run is '{first.scenario_id}', "
            f"this launch is '{scenario_id}'"
        )
    if first.target != target_agent_id:
        return (
            f"a pair must run on the same target endpoint: first run targeted "
            f"'{first.target}', this launch targets '{target_agent_id}'"
        )
    if first.agent_profile_mode not in _PAIRABLE_MODES or agent_profile_mode not in _PAIRABLE_MODES:
        return (
            "both runs of a pair must declare agent_profile_mode 'report' or 'block' "
            f"(first run '{first.agent_profile_mode}', this launch '{agent_profile_mode}')"
        )
    if first.agent_profile_mode == agent_profile_mode:
        return (
            "the two runs of a pair must declare different agent_profile_mode values "
            f"(both '{agent_profile_mode}')"
        )
    if first.status not in _TERMINAL_STATUSES or first.completed_at is None:
        return (
            f"the first run must be terminal (complete or failed, with completed_at set); "
            f"it is '{first.status}'"
        )
    return None


def _key(result: dict) -> tuple:
    return (result.get("step_id"), result.get("detection_id") or result.get("expected_detection"))


def build_pair_delta(
    first: dict,
    first_results: list[dict],
    second: dict,
    second_results: list[dict],
) -> dict:
    """Join two runs' results per ``(step_id, detection_id or expected_detection)``.

    The report-mode run is whichever dict declares ``agent_profile_mode == "report"``.
    A detection present on one side only still appears, with the other side ``None``.
    """
    if first.get("agent_profile_mode") == "report":
        report_run, report_results, block_run, block_results = first, first_results, second, second_results
    else:
        report_run, report_results, block_run, block_results = second, second_results, first, first_results

    report_by_key = {_key(r): r for r in report_results}
    block_by_key = {_key(r): r for r in block_results}

    steps: dict[Any, dict] = {}
    # dict preserves first-seen order; report side first, then block-only keys.
    for key in [*report_by_key, *(k for k in block_by_key if k not in report_by_key)]:
        rep = report_by_key.get(key)
        blk = block_by_key.get(key)
        base = rep or blk
        step = steps.setdefault(
            base.get("step_id"),
            {"step_id": base.get("step_id"), "step_name": base.get("step_name"), "detections": []},
        )
        step["detections"].append({
            "detection_id": base.get("detection_id") or base.get("expected_detection"),
            "signal_type": base.get("signal_type"),
            "prevention_module": (blk or {}).get("prevention_module") or (rep or {}).get("prevention_module"),
            "report": None if rep is None else {
                "outcome": rep.get("outcome"),
                "mttd_seconds": rep.get("mttd_seconds"),
            },
            "block": None if blk is None else {
                "outcome": blk.get("outcome"),
                "outcome_reason": blk.get("outcome_reason"),
                "mttd_seconds": blk.get("mttd_seconds"),
            },
        })

    return {
        "report_run_id": report_run.get("run_id"),
        "block_run_id": block_run.get("run_id"),
        "severed_at_step": block_run.get("severed_at_step"),
        "steps": sorted(steps.values(), key=lambda s: str(s["step_id"])),
    }

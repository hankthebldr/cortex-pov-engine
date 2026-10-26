"""Canary run: proof that the agent profile is really in block mode.

A block-mode launch carries an operator attestation the engine cannot verify.
A canary is a separate one-step run of a benign built-in trigger the agent is
expected to terminate; its reconciled outcome is the evidence. This module is
pure: it is handed a run and its results and touches no session.

A canary the host severed but the tenant has not yet confirmed (no ``prevented``
Result) is *unconfirmed* -- a host witness alone never confirms the profile, so
the DC reconciles the canary before launching the block run.
"""
from __future__ import annotations

from typing import Any, Optional, Sequence

from engine.outcome import (
    BLOCK_MODE_CONFIRMED,
    BLOCK_MODE_UNCONFIRMED_STATUS,
    CANARY_NOT_AVAILABLE,
    OUTCOME_PREVENTED,
)

_TERMINAL_STATUSES = frozenset({"complete", "failed"})


def canary_scenario_id(platform: str) -> Optional[str]:
    """Configured canary scenario for ``platform``; None when unset/unknown."""
    from config import settings  # noqa: PLC0415

    if platform == "linux":
        return settings.CORTEXSIM_CANARY_SCENARIO_LINUX or None
    return None


def is_terminal(run: Any) -> bool:
    return run.status in _TERMINAL_STATUSES and run.completed_at is not None


def block_mode_status_from(canary_run: Optional[Any], canary_results: Sequence[Any]) -> str:
    if canary_run is None:
        return CANARY_NOT_AVAILABLE
    if any(r.outcome == OUTCOME_PREVENTED for r in canary_results):
        return BLOCK_MODE_CONFIRMED
    return BLOCK_MODE_UNCONFIRMED_STATUS

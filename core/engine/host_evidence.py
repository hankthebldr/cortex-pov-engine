"""Project host-side step evidence onto seeded ``Result`` rows.

Pure: no DB session. ``steps`` is any sequence of objects exposing
``.step_id``, ``.termination`` and ``.reason`` -- a ``StepOutcomeIn`` from
``/complete`` or a ``StepOutcome`` ORM row. Results are mutated in place; the
caller owns the commit.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional, Sequence

from engine.outcome import (
    HOST_WITNESS,
    OUTCOME_PENDING,
    PREVENTION_UNCONFIRMED_NO_ALERT,
    host_outcome_for_step,
)


def severed_step(steps: Iterable[Any]) -> Optional[str]:
    """First step whose termination is a host witness (the agent killed it)."""
    for s in steps:
        if s.termination in HOST_WITNESS:
            return s.step_id
    return None


def apply_host_outcomes(results: Sequence[Any], steps: Iterable[Any], *,
                        agent_profile_mode: str) -> int:
    """Set host-derived ``outcome`` on Results; return the number touched.

    * A step that never ran (``not_run``) marks all its Results ``not_reached``
      / ``not_executed`` with the reason carried through.
    * A ``prevent`` Result on a step the host witnessed being killed, in block
      mode, is seeded ``pending`` -- the host saw a kill but the alert (the
      second witness) may still arrive.
    """
    by_step = {s.step_id: s for s in steps}
    touched = 0
    for r in results:
        s = by_step.get(r.step_id)
        if s is None:
            continue
        host_outcome = host_outcome_for_step(s.termination, s.reason)
        if host_outcome is not None:
            r.outcome = host_outcome
            r.outcome_reason = s.reason
            touched += 1
        elif (r.expected_outcome == "prevent" and agent_profile_mode == "block"
              and s.termination in HOST_WITNESS):
            r.outcome = OUTCOME_PENDING
            r.outcome_reason = PREVENTION_UNCONFIRMED_NO_ALERT
            r.kpi_verdict = "pending"
            touched += 1
    return touched

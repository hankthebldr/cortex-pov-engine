"""Project host-side step evidence onto seeded ``Result`` rows.

Pure: no DB session. ``steps`` is any sequence of objects exposing
``.step_id``, ``.termination`` and ``.reason`` -- a ``StepOutcomeIn`` from
``/complete`` or a ``StepOutcome`` ORM row. Results are mutated in place; the
caller owns the commit.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional, Sequence

from engine.outcome import (
    HOST_WITNESS,
    OUTCOME_MISSED,
    OUTCOME_PENDING,
    PREVENTION_UNCONFIRMED_BEACON_LOST,
    PREVENTION_UNCONFIRMED_NO_ALERT,
    RUN_ABORTED,
    TERMINATION_ABORTED,
    TERMINATION_BEACON_LOST,
    grades_as_prevention,
    host_outcome_for_step,
    resolve_module_mode,
)


def severed_step(steps: Iterable[Any]) -> Optional[str]:
    """First step whose termination is a host witness (the agent killed it)."""
    for s in steps:
        if s.termination in HOST_WITNESS:
            return s.step_id
    return None


def apply_host_outcomes(results: Sequence[Any], steps: Iterable[Any], *,
                        agent_profile_mode: str,
                        module_modes: Optional[Mapping[str, str]] = None) -> int:
    """Set host-derived ``outcome`` on Results; return the number touched.

    * Only Results whose ``outcome`` is None/pending are touched -- except that
      a host exclusion, or an ``aborted`` termination (-> ``pending`` /
      ``RUN_ABORTED``), also replaces a Tier-1 silent ``missed``.
    * A step that never ran (``not_run``) marks all its Results ``not_reached``
      / ``not_executed`` with the reason carried through, for any expectation.
      A ``missed`` row is replaceable (silence on a step that never ran is not
      a miss; its ``fail`` is cleared so it leaves every verdict); a decided
      ``detected`` / ``prevented`` row is not.
    * A ``prevent`` Result on a step the host witnessed being killed, in block
      mode, is seeded ``pending`` -- the host saw a kill but the alert (the
      second witness) may still arrive. "Block mode" is per row: a module the
      operator attested Report or Disabled (``module_modes``) is not graded as
      a block, so a kill on its step is not its prevention.
    """
    by_step = {s.step_id: s for s in steps}
    touched = 0
    for r in results:
        s = by_step.get(r.step_id)
        if s is None:
            continue
        host_outcome = host_outcome_for_step(s.termination, s.reason)
        if s.termination == TERMINATION_ABORTED and r.outcome == OUTCOME_MISSED:
            # Round 4 C-1: the operator cut the step short, so a Tier-1 miss
            # is undetermined. Its Tier-1 fail goes with it; never a decided hit.
            r.outcome = OUTCOME_PENDING
            r.outcome_reason = RUN_ABORTED
            if r.kpi_verdict == "fail":
                r.kpi_verdict = "pending"
            touched += 1
            continue
        if host_outcome is not None and r.outcome == OUTCOME_MISSED:
            # Tier 1 judged silence a miss; the host now says the step never
            # ran, which is better evidence than silence.
            r.outcome = host_outcome
            r.outcome_reason = s.reason
            r.kpi_verdict = None
            touched += 1
            continue
        # A tenant-decided hit (prevented/detected) stands; host evidence only
        # fills an undecided Result.
        if r.outcome not in (None, OUTCOME_PENDING):
            continue
        if host_outcome is not None:
            r.outcome = host_outcome
            r.outcome_reason = s.reason
            touched += 1
            continue
        as_block = r.expected_outcome == "prevent" and grades_as_prevention(
            agent_profile_mode, resolve_module_mode(
                agent_profile_mode=agent_profile_mode, module_modes=module_modes,
                prevention_module=getattr(r, "prevention_module", None)))
        if as_block and s.termination in HOST_WITNESS:
            r.outcome = OUTCOME_PENDING
            r.outcome_reason = PREVENTION_UNCONFIRMED_NO_ALERT
            r.kpi_verdict = "pending"
            touched += 1
        elif as_block and s.termination == TERMINATION_BEACON_LOST:
            # A lost beacon is a witness of nothing: hold the prevention open.
            r.outcome = OUTCOME_PENDING
            r.outcome_reason = PREVENTION_UNCONFIRMED_BEACON_LOST
            r.kpi_verdict = "pending"
            touched += 1
    return touched

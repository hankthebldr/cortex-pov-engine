"""Target-readiness preflight — "can THIS HOST run this scenario?"

The gap this closes, stated plainly (docs/design/agent-runtime-dependencies.md
has the full evidence): SIM-EDR-001 step-05 downloads mimipenguin.sh, which
shells out to python internally. A target with no python interpreter at all
still reported the step as exit_code=0/OK, because the step's own
`|| echo '[*] ... complete'` fallback swallowed the failure. The fix is
enforced beacon-side (agent/beacon/client.go::resolveRuntimeDeps — a step
whose declared `requires_interpreters` cannot be satisfied is never executed,
never masked, and always reports a distinguishable non-zero result), which is
the ONLY place that can be trusted, because it is the only place that can
check the REAL target at the REAL moment of execution.

This module is the other half: PREFLIGHT VISIBILITY. `POST /api/connectors/{kind}/preflight`
answers "is my TENANT reachable" before a POV; nothing answered "can this HOST
run this scenario" before this. `evaluate_runtime_readiness` compares a
scenario's declared per-step `requires_interpreters` against an agent's
advertised interpreter roster (`Agent.interpreters`, populated at registration
from the beacon's own `executor.AvailableLogicalNames()`) so an operator can
see a gap BEFORE dispatch instead of discovering it mid-run.

THIS IS ADVISORY, NOT THE ENFORCEMENT. An agent's advertised roster is a
snapshot from its last registration — it can go stale (a package removed after
registration, a beacon that has not re-registered). The beacon's own live
check is what makes "never present as success" true by construction; this
module only makes the common case ("this run cannot possibly succeed against
this target") visible before an operator burns a launch on it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

# Kept in sync (by hand, not by codegen) with agent/executor/interpreter.go's
# interpreterAliases. A drift here only widens or narrows the ADVISORY
# preflight signal — it can never cause a false "ran successfully", because
# the beacon re-resolves for real at execution time regardless of what this
# table says.
INTERPRETER_ALIASES: dict[str, tuple[str, ...]] = {
    "python": (
        "python", "python3", "python3.13", "python3.12", "python3.11",
        "python3.10", "python3.9", "python3.8", "python3.7", "python3.6",
        "python2.7", "python2",
    ),
    "perl": ("perl",),
    "ruby": ("ruby",),
    "node": ("node", "nodejs"),
}


def interpreter_satisfied(logical: str, available: Iterable[str]) -> bool:
    """True when ``available`` (an agent's advertised logical interpreter
    names, e.g. from ``Agent.interpreters``) satisfies ``logical``.

    The agent already reports LOGICAL names (it resolves its own aliases via
    ``executor.AvailableLogicalNames()`` before registering), so the common
    case is a direct membership check. The alias table is also consulted so a
    preflight run against an older agent record that happened to record a
    concrete binary name (or a hand-built test fixture) still resolves
    sensibly rather than under-reporting readiness.
    """
    available_set = set(available or [])
    if logical in available_set:
        return True
    for alias in INTERPRETER_ALIASES.get(logical, ()):
        if alias in available_set:
            return True
    return False


@dataclass
class StepGap:
    step_id: str
    missing: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"step_id": self.step_id, "missing": self.missing}


@dataclass
class RuntimeReadiness:
    ready: bool
    gaps: list[StepGap] = field(default_factory=list)
    agent_interpreters: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "gaps": [g.to_dict() for g in self.gaps],
            "agent_interpreters": self.agent_interpreters,
        }


def _steps_of(scenario: Any) -> list[dict[str, Any]]:
    """Scenario is either the ORM row (``.steps`` is a JSON list of dicts, the
    ``StepSchema.model_dump()`` shape) or a plain list of step dicts — the
    engine's own ``Task.steps`` shape. Both are handled so this function can be
    called from the launch path and from a unit test with a bare list alike.
    """
    steps = getattr(scenario, "steps", scenario)
    return steps or []


def evaluate_runtime_readiness(
    scenario: Any,
    agent_interpreters: Optional[Iterable[str]],
) -> RuntimeReadiness:
    """Compare a scenario's declared per-step ``requires_interpreters``
    against ``agent_interpreters`` (an agent's advertised roster). Pure — no
    I/O, no DB access — so it is cheap to call on every launch and trivial to
    unit test.
    """
    available = list(agent_interpreters or [])
    gaps: list[StepGap] = []
    for step in _steps_of(scenario):
        required = step.get("requires_interpreters") or []
        missing = [r for r in required if not interpreter_satisfied(r, available)]
        if missing:
            gaps.append(StepGap(step_id=step.get("id", ""), missing=missing))
    return RuntimeReadiness(ready=not gaps, gaps=gaps, agent_interpreters=available)

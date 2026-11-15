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
scenario's declared per-step `requires_interpreters` against what the agent
reported at registration, so an operator can see a gap BEFORE dispatch instead
of discovering it mid-run.

The beacon resolves each declared name on PATH, so the field also carries names
that are not interpreters (sudo, ssh, scp, sshpass). Each name is judged by
what it is:

* an interpreter (python, perl, ruby, node) against the interpreter roster
  (`Agent.interpreters`, from the beacon's own `executor.AvailableLogicalNames()`);
* any other name against the beacon's tool facts (`Agent.runtime_facts.tools`,
  what it found of the list it looked for). Found is fine. Looked for and not
  found is a gap. Never looked for is `unverified`: nothing is known, so it is
  not a gap.

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

# The list every beacon looks for (agent/runtimefacts/facts.go DefaultProbeTools;
# a Go test fails if the two drift).
from engine.agent_manifest import PROBE_TOOLS

GAP_CODE = "RUNTIME_DEPENDENCY_GAP"
INSTALL_NOT_ENABLED_CODE = "RUNTIME_INSTALL_NOT_ENABLED"

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
    #: Names the beacon never looked for. Nothing is known about them, so they
    #: are not a gap.
    unverified: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"step_id": self.step_id, "missing": self.missing}
        if self.unverified:
            out["unverified"] = self.unverified
        return out


@dataclass
class RuntimeReadiness:
    #: False only when some step has a ``missing`` name.
    ready: bool
    #: One entry per step with a ``missing`` or an ``unverified`` name.
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


def _tool_facts(agent_facts: Any) -> Optional[tuple[set[str], set[str]]]:
    """``(found, looked_for)`` from an agent's ``runtime_facts``, or None when
    the agent sent no tool list (an older beacon, or no facts at all).

    ``looked_for`` is the beacon's own ``probed`` list when it sends one, else
    the list every beacon looks for.
    """
    if not isinstance(agent_facts, dict):
        return None
    tools = agent_facts.get("tools")
    if not isinstance(tools, list):
        return None
    probed = agent_facts.get("probed")
    return set(tools), set(probed if isinstance(probed, list) else PROBE_TOOLS)


def evaluate_runtime_readiness(
    scenario: Any,
    agent_interpreters: Optional[Iterable[str]],
    agent_facts: Optional[dict[str, Any]] = None,
) -> RuntimeReadiness:
    """Compare a scenario's declared per-step ``requires_interpreters``
    against what the agent reported: ``agent_interpreters`` (its interpreter
    roster) and ``agent_facts`` (its ``runtime_facts``, for names that are not
    interpreters). Pure — no I/O, no DB access — so it is cheap to call on
    every launch and trivial to unit test.
    """
    available = list(agent_interpreters or [])
    tool_facts = _tool_facts(agent_facts)
    gaps: list[StepGap] = []
    for step in _steps_of(scenario):
        missing: list[str] = []
        unverified: list[str] = []
        for name in step.get("requires_interpreters") or []:
            if interpreter_satisfied(name, available):
                continue
            if name in INTERPRETER_ALIASES:
                missing.append(name)
            elif tool_facts is None:
                unverified.append(name)
            elif name not in tool_facts[0]:
                (missing if name in tool_facts[1] else unverified).append(name)
        if missing or unverified:
            gaps.append(StepGap(
                step_id=step.get("id", ""), missing=missing, unverified=unverified))
    return RuntimeReadiness(
        ready=not any(g.missing for g in gaps), gaps=gaps, agent_interpreters=available)


def launch_warnings(
    readiness: RuntimeReadiness,
    agent_id: str,
    *,
    install_requested: bool = False,
    install_enabled: bool = False,
) -> list[dict[str, Any]]:
    """The warnings a launch against ``agent_id`` carries, as ``{code, ...}``
    dicts. Neither refuses the launch.

    * ``RUNTIME_DEPENDENCY_GAP`` when a step has a ``missing`` name.
    * ``RUNTIME_INSTALL_NOT_ENABLED`` when the launch asked for a runtime
      install and this deployment has not enabled it.
    """
    out: list[dict[str, Any]] = []
    gapped = [g for g in readiness.gaps if g.missing]
    if gapped:
        missing = list(dict.fromkeys(name for g in gapped for name in g.missing))
        out.append({
            "code": GAP_CODE,
            "agent_id": agent_id,
            "steps": [g.step_id for g in gapped],
            "missing": missing,
            "detail": (
                f"The agent did not report {', '.join(missing)}, and the beacon will "
                f"not run these steps without {'it' if len(missing) == 1 else 'them'}."
            ),
        })
    if install_requested and not install_enabled:
        out.append({
            "code": INSTALL_NOT_ENABLED_CODE,
            "agent_id": agent_id,
            "detail": (
                "Runtime install is off on this SimCore "
                "(CORTEXSIM_AGENT_ALLOW_RUNTIME_INSTALL), so nothing will be installed."
            ),
        })
    return out

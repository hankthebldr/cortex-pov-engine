"""Launch gate for container agents.

A beacon running in a pod has ONE fixed posture and a FIXED set of tools. A
scenario that needs more than the pod has must be refused BEFORE dispatch: a
step that fails for lack of privilege or tooling would otherwise be scored as a
detection the customer's Cortex missed — a false negative we manufactured.

Pure and side-effect free; the orchestrator calls :func:`evaluate` and owns the
refusal / warning plumbing. Host and VM beacons are untouched (rule 1).

The gate applies ONLY to an agent the manifest deployed, recognised by a
non-empty ``posture_profile`` string (the manifest's start script always passes
``--posture-profile``). A beacon in Docker/Podman or a foreign pod reports
``container: true`` but no profile; it is not ours to gate, so ``""``,
whitespace, ``None`` or a missing key is a strict no-op — no refusal, no
warning, no ``required_binaries`` stamping — exactly as for ``container: false``.

Within a gated agent, missing data is never read as "nothing needed":

* ``tools`` absent / None means the roster is UNKNOWN, not empty — the tool
  check is skipped and ``AGENT_TOOL_ROSTER_UNKNOWN`` warns.
* a profile string outside :data:`agent_manifest.PROFILES` is refused outright.

Posture is compared on findings (``findings_for``) AND on RBAC rules, namespaced
and cluster-scoped: SIM-CDR-001 plants only findings baseline already has, yet
needs a namespace Role (secrets get/list) that no baseline pod holds, so findings
alone cannot tell them apart.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from engine import agent_manifest as am
from engine import k8s_manifest as km

CODE_POSTURE = "AGENT_POSTURE_INSUFFICIENT"
CODE_TOOL_ABSENT = "AGENT_TOOL_ABSENT"
WARN_NO_RBAC_FOR_KUBECTL = "AGENT_NO_RBAC_FOR_KUBECTL"
WARN_ROSTER_UNKNOWN = "AGENT_TOOL_ROSTER_UNKNOWN"


@dataclass(frozen=True)
class GateRefusal:
    code: str  # AGENT_POSTURE_INSUFFICIENT | AGENT_TOOL_ABSENT
    error: str
    detail: dict


@dataclass(frozen=True)
class GateResult:
    refusal: Optional[GateRefusal]
    warnings: list[dict] = field(default_factory=list)


def step_required_binaries(command: str) -> list[str]:
    """Thin wrapper over :func:`k8s_manifest.required_binaries` (code-only scan)."""
    return list(km.required_binaries(command or ""))


def scenario_view(scenario: Any, steps: Iterable[dict]) -> dict:
    """The dict :func:`evaluate` reads, for an ORM scenario and RESOLVED steps.

    ``steps`` are the steps the beacon will actually receive (adapter and stitch
    placeholders expanded), not ``scenario.steps``. The pull path runs
    ``command`` verbatim — ``platform_variants`` is consulted only by the push
    generator — so ``command`` is the exact text to scan.

    The ``Scenario`` ORM has no ``cluster_posture`` column yet, so the posture is
    hydrated from the YAML exactly as ``api.scenarios`` does for the push gate;
    reading the DB alone would make the posture check vacuous.
    """
    posture = getattr(scenario, "cluster_posture", None)
    if not posture:
        from api.scenarios import _disk_posture  # noqa: PLC0415  (import cycle)

        posture = _disk_posture(getattr(scenario, "scenario_id", "") or "")
    return {"steps": list(steps), "cluster_posture": posture}


# ── posture ──────────────────────────────────────────────────────────────────


def _covers(have: list[Any], need: Any) -> bool:
    def sub(wanted: list[str], held: list[str]) -> bool:
        return "*" in held or set(wanted) <= set(held)

    return any(
        sub(need.api_groups, h.api_groups)
        and sub(need.resources, h.resources)
        and sub(need.verbs, h.verbs)
        for h in have
    )


def uncovered_rules(
    posture: km.ClusterPosture, *, cluster_have: list[Any], namespace_have: list[Any]
) -> list[dict]:
    """The scenario's RBAC rules that the held rules do not cover, each with its scope.

    A cluster-wide rule is also held inside every namespace, so it covers a
    namespaced need; a namespaced rule never covers a cluster-scoped need. Cluster
    needs are compared as rules too: a non-wildcard cluster rule (``nodes list``)
    maps to no finding of its own, so the finding comparison alone cannot see it.
    """
    sa = posture.service_account
    out = [
        {"scope": "cluster", **r.model_dump()}
        for r in sa.cluster_role_rules
        if not _covers(list(cluster_have), r)
    ]
    out += [
        {"scope": "namespace", **r.model_dump()}
        for r in sa.namespace_role_rules
        if not _covers(list(cluster_have) + list(namespace_have), r)
    ]
    return out


def _missing_rules(posture: Optional[km.ClusterPosture], profile: str) -> list[dict]:
    if posture is None:
        return []
    sa = am.agent_posture(profile).service_account
    return uncovered_rules(
        posture,
        cluster_have=list(sa.cluster_role_rules),
        namespace_have=list(sa.namespace_role_rules),
    )


def _satisfying_profile(needed: set[str], posture: Optional[km.ClusterPosture]) -> Optional[str]:
    for p in am.PROFILES:
        if needed <= am.profile_findings(p) and not _missing_rules(posture, p):
            return p
    return None


def is_gated(facts: Optional[dict]) -> bool:
    """True only for a container agent the manifest deployed (non-empty profile)."""
    if not facts or not facts.get("container"):
        return False
    profile = facts.get("posture_profile")
    return isinstance(profile, str) and bool(profile.strip())


def _posture_refusal(posture: Optional[km.ClusterPosture], facts: dict) -> Optional[GateRefusal]:
    raw = facts.get("posture_profile")
    needed = {f.slug for f in km.findings_for(posture)} if posture is not None else set()
    profile = raw
    if profile not in am.PROFILES:
        return GateRefusal(
            CODE_POSTURE,
            f"the agent reports posture profile {raw!r}, which this engine does not "
            "know, so it cannot show the pod has what the scenario needs",
            {
                "missing_findings": sorted(needed),
                "missing_rbac_rules": [],
                "agent_profile": raw,
                "satisfying_profile": _satisfying_profile(needed, posture),
            },
        )
    missing = sorted(needed - am.profile_findings(profile))
    missing_rules = _missing_rules(posture, profile)
    if not missing and not missing_rules:
        return None
    sat = _satisfying_profile(needed, posture)
    return GateRefusal(
        CODE_POSTURE,
        f"the scenario needs cluster posture a {profile!r} agent pod does not have "
        f"(missing findings: {missing or 'none'}; missing RBAC rules: "
        f"{len(missing_rules)}). Run it from an agent deployed with "
        f"{sat!r}." if sat else
        f"the scenario needs cluster posture a {profile!r} agent pod does not have "
        f"(missing findings: {missing or 'none'}; missing RBAC rules: "
        f"{len(missing_rules)}) and no agent profile provides it.",
        {
            "missing_findings": missing,
            "missing_rbac_rules": missing_rules,
            "agent_profile": raw,
            "satisfying_profile": sat,
        },
    )


# ── entry point ──────────────────────────────────────────────────────────────


def evaluate(
    scenario: dict,
    agent_runtime_facts: Optional[dict],
    *,
    allow_missing_tools: bool = False,
) -> GateResult:
    facts = agent_runtime_facts
    if not is_gated(facts):
        return GateResult(None, [])

    posture = km.parse_posture(scenario)
    refusal = _posture_refusal(posture, facts)
    if refusal is not None:
        return GateResult(refusal, [])

    warnings: list[dict] = []
    steps = scenario.get("steps") or []
    per_step = [(s.get("id"), step_required_binaries(s.get("command") or "")) for s in steps]

    roster = facts.get("tools")
    if roster is None:
        warnings.append({
            "code": WARN_ROSTER_UNKNOWN,
            "detail": "the agent did not report its tool roster; tools were not checked",
        })
    else:
        have = set(roster)
        missing = [
            {"step": sid, "tools": [b for b in bins if b not in have]}
            for sid, bins in per_step
            if any(b not in have for b in bins)
        ]
        if missing and not allow_missing_tools:
            return GateResult(
                GateRefusal(
                    CODE_TOOL_ABSENT,
                    "the agent pod lacks tools the scenario's steps call: "
                    + "; ".join(f"{m['step']}: {', '.join(m['tools'])}" for m in missing)
                    + ". Re-deploy the agent with prepare=packages or an image that "
                    "carries them, or relaunch with allow_missing_tools=true to run anyway.",
                    {
                        "missing": missing,
                        "prepare_status": facts.get("prepare_status"),
                        "override": "allow_missing_tools",
                    },
                ),
                warnings,
            )

    profile = facts["posture_profile"]
    kubectl_steps = [sid for sid, bins in per_step if "kubectl" in bins]
    if kubectl_steps and profile == "baseline":
        warnings.append({"code": WARN_NO_RBAC_FOR_KUBECTL, "steps": kubectl_steps})
    return GateResult(None, warnings)


# ── launch-level: every agent target, each on its own facts ──────────────────


def agent_step_targets(steps: Iterable[dict], launch_target: str) -> dict[str, list[dict]]:
    """Agent-channel steps grouped by EFFECTIVE target (step ``target`` or the launch target).

    The same partition rule as ``orchestrator._partition_channels``: a step whose
    channel is ``eal`` runs in SimCore's process and reaches no beacon.
    """
    from engine.scenario_loader import effective_channel  # noqa: PLC0415

    out: dict[str, list[dict]] = {}
    for s in steps:
        if effective_channel(s) == "eal":
            continue
        out.setdefault(s.get("target") or launch_target, []).append(s)
    return out


def stamp_required_binaries(steps: Iterable[dict]) -> None:
    """Union each step's ``required_binaries`` with the binaries its command calls.

    The beacon's preflight then stops a step whose tool vanished after the roster
    was taken (``TOOL_ABSENT``). Mutates the steps in place.
    """
    for s in steps:
        union = list(s.get("required_binaries") or [])
        for b in step_required_binaries(s.get("command") or ""):
            if b not in union:
                union.append(b)
        if union:
            s["required_binaries"] = union


def evaluate_launch(
    cluster_posture: Any,
    steps: list[dict],
    launch_target: str,
    facts_by_agent: dict[str, Optional[dict]],
    *,
    allow_missing_tools: bool = False,
) -> GateResult:
    """Gate every agent target that receives agent-channel steps.

    Each target is judged on ITS OWN ``runtime_facts`` and only the steps routed
    to it. The first refusal wins and names the agent (``detail.agent_id``);
    every warning carries the ``agent_id`` it is about. Steps of gated targets
    are stamped with ``required_binaries``; ungated targets are left untouched.
    """
    warnings: list[dict] = []
    gated: list[list[dict]] = []
    for agent_id, mine in agent_step_targets(steps, launch_target).items():
        facts = facts_by_agent.get(agent_id)
        if not is_gated(facts):
            continue
        result = evaluate(
            {"steps": mine, "cluster_posture": cluster_posture}, facts,
            allow_missing_tools=allow_missing_tools,
        )
        if result.refusal is not None:
            r = result.refusal
            return GateResult(
                GateRefusal(r.code, f"agent {agent_id!r}: {r.error}",
                            {**r.detail, "agent_id": agent_id}),
                [],
            )
        warnings += [{**w, "agent_id": agent_id} for w in result.warnings]
        gated.append(mine)
    for mine in gated:
        stamp_required_binaries(mine)
    return GateResult(None, warnings)

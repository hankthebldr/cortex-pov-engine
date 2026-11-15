"""Pull-mode scenario cleanup: what a task carries and what a run keeps (#324).

A push bundle has always run the scenario's ``cleanup.commands`` on exit. A
pull run did not: the beacon removed only the tools it staged, so every run
left the scenario's files, units, cron lines and accounts on the target.

The pull task now carries the cleanup for the agent's operating system, taken
from the resolver the push bundle uses (``push_generator._resolve_cleanup``).
The beacon runs it when the task ends, writes one ``=== CLEANUP`` section to
the run output, and reports the outcome on ``/complete``; SimCore keeps that
outcome on ``runs.cleanup``, one entry per task.

Everything pull cleanup needs on the server lives here; the orchestrator, the
runs API and the report call in with one line each.

Wire shapes
-----------
Task (omitted when no command runs on the target)::

    "cleanup": {"commands": [{"command": str, "shell": "" | "powershell" | "cmd"}],
                "timeout_seconds": 120}

``/complete`` (absent from a beacon that predates this)::

    "cleanup": {"status": "clean" | "failed" | "not_run",
                "ran": int, "failed": int, "reason": str | null}

``runs.cleanup``::

    [{"agent_id": str | null, "task_id": str | null,
      "status": ..., "ran": int, "failed": int, "reason": str | null}]
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

#: What a beacon that runs cleanup advertises (``agent/capabilities.go``).
CLEANUP_CAPABILITY = "cleanup"
#: How long one cleanup command may run on the target.
CLEANUP_TIMEOUT_SECONDS = 120
#: Launch warning: the target's beacon will not run the cleanup it is sent.
CLEANUP_NOT_RUN = "CLEANUP_NOT_RUN"
#: ``reason`` a beacon sends with ``not_run`` when no step of the task started,
#: so there is nothing on the host to clean.
REASON_NO_STEP_STARTED = "NO_STEP_STARTED"


# ---------------------------------------------------------------------------
# The task
# ---------------------------------------------------------------------------


def task_cleanup(scenario: Any, target: str) -> Optional[dict[str, Any]]:
    """The ``cleanup`` block of a pull task for ``target`` (``posix`` /
    ``windows``), or ``None`` when no command of the scenario runs there.

    Uses the push bundle's resolution, so the two delivery modes run the same
    lines: a Windows-only teardown line is withheld from a POSIX target and a
    POSIX line from a Windows one.
    """
    from engine.push_generator import _resolve_cleanup  # noqa: PLC0415

    kept, _withheld = _resolve_cleanup(
        {"cleanup": getattr(scenario, "cleanup", None)}, target)  # type: ignore[arg-type]
    if not kept:
        return None
    return {
        # The beacon's default interpreter is named "" (sh on POSIX), exactly as
        # a step's `shell` is.
        "commands": [{"command": cmd, "shell": "" if shell == "bash" else shell}
                     for cmd, shell in kept],
        "timeout_seconds": CLEANUP_TIMEOUT_SECONDS,
    }


@dataclass
class PullCleanupPlan:
    """Cleanup for every beacon task of one launch."""

    #: agent_id -> the task's ``cleanup`` block. An agent with nothing to run
    #: has no entry.
    by_agent: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: One info-level ``CLEANUP_NOT_RUN`` per agent that has commands to run
    #: and does not advertise the capability.
    warnings: list[dict[str, Any]] = field(default_factory=list)


async def plan_pull_cleanup(
    db: AsyncSession, scenario: Any, steps: list[dict[str, Any]], launch_target: str,
) -> PullCleanupPlan:
    """Resolve the scenario's cleanup for each agent that gets a task.

    Reads only, so the launch's dry run reports the same warnings. Every
    endpoint of a composed run gets the lines its own OS runs; the schema has
    one cleanup list per scenario, not one per endpoint.

    The commands are attached whether or not the agent advertises
    ``cleanup``: the roster is the beacon's last registration and can be older
    than the beacon, and an old beacon ignores a key it does not know.
    """
    from engine import agent_launch_gate as gate  # noqa: PLC0415
    from engine.orchestrator import (  # noqa: PLC0415
        AgentOsUnrecognised,
        platform_target_for_os,
    )
    from models import Agent  # noqa: PLC0415

    plan = PullCleanupPlan()
    if not ((getattr(scenario, "cleanup", None) or {}).get("commands")):
        return plan
    for agent_id in gate.agent_step_targets(steps, launch_target):
        agent = (await db.execute(
            select(Agent).where(Agent.agent_id == agent_id))).scalar_one_or_none()
        if agent is None:
            continue
        try:
            target, _ = platform_target_for_os(agent.os)
        except AgentOsUnrecognised:
            continue
        block = task_cleanup(scenario, target)
        if block is None:
            continue
        plan.by_agent[agent_id] = block
        if CLEANUP_CAPABILITY not in (agent.capabilities or []):
            plan.warnings.append({
                "code": CLEANUP_NOT_RUN,
                "level": "info",
                "agent_id": agent_id,
                "detail": "this beacon does not run scenario cleanup; update it, "
                          "or clean the host by hand after the run",
            })
    return plan


# ---------------------------------------------------------------------------
# The outcome a beacon reports
# ---------------------------------------------------------------------------


def _count(value: Any) -> int:
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


#: Append one entry to ``runs.cleanup`` unless this task already has one. One
#: statement, so a fan-out's endpoints finishing together cannot lose an entry
#: and a retried ``/complete`` cannot add a second.
_APPEND = text(
    "UPDATE runs SET cleanup = json_insert("
    "  CASE WHEN json_valid(cleanup) AND json_type(cleanup) = 'array'"
    "       THEN cleanup ELSE '[]' END, '$[#]', json(:entry)) "
    "WHERE run_id = :run_id AND NOT EXISTS ("
    "  SELECT 1 FROM json_each("
    "    CASE WHEN json_valid(runs.cleanup) AND json_type(runs.cleanup) = 'array'"
    "         THEN runs.cleanup ELSE '[]' END) "
    "  WHERE json_extract(value, '$.task_id') IS :task_id"
    "    AND json_extract(value, '$.agent_id') IS :agent_id)"
)


async def record_outcome(
    db: AsyncSession, run_id: str, outcome: Optional[dict[str, Any]], *,
    agent_id: Optional[str], task_id: Optional[str],
) -> bool:
    """Store one task's cleanup outcome on its run (caller commits).

    ``outcome`` is the ``cleanup`` object of a ``/complete`` body, or ``None``
    from a beacon that sent none (nothing is written). Returns whether an
    entry was added.
    """
    if not isinstance(outcome, dict):
        return False
    agent = (agent_id or "").strip() or None
    task = (task_id or "").strip() or None
    reason = outcome.get("reason")
    entry = {
        "agent_id": agent,
        "task_id": task,
        "status": str(outcome.get("status") or "")[:32],
        "ran": _count(outcome.get("ran")),
        "failed": _count(outcome.get("failed")),
        "reason": None if reason in (None, "") else str(reason)[:200],
    }
    res = await db.execute(_APPEND, {
        "entry": json.dumps(entry), "run_id": run_id,
        "task_id": task, "agent_id": agent,
    })
    return bool(res.rowcount)


# ---------------------------------------------------------------------------
# The run output and the report
# ---------------------------------------------------------------------------

#: The section's frame, as ``agent/beacon/cleanup.go`` writes it.
_HEADER_RE = re.compile(r"=== CLEANUP · (\d+) commands? · ")
_FOOTER_RE = re.compile(r"^--- cleanup (\d+)/(\d+) ")
#: What ends a section that never reached its last footer.
_NEXT_FRAME = ("=== STEP ", "=== ARTIFACT STAGING", "--- COMPLETION SUMMARY ---")
_WARNED_AGENT_RE = re.compile(rf"^!! {CLEANUP_NOT_RUN}: agent '([^']+)'", re.MULTILINE)


def without_cleanup_sections(output: str) -> str:
    """``output`` with every ``=== CLEANUP`` section removed.

    For readers that judge the STEPS by the run output: a cleanup command
    printing ``command not found`` is not a step that failed to run.
    """
    if "=== CLEANUP" not in output:
        return output
    kept: list[str] = []
    inside = False
    for line in output.splitlines(keepends=True):
        if not inside:
            header = _HEADER_RE.search(line)
            if header is None:
                kept.append(line)
            else:
                inside = True
                if header.start():
                    kept.append(line[:header.start()] + "\n")
            continue
        footer = _FOOTER_RE.match(line)
        if footer is not None:
            inside = footer.group(1) != footer.group(2)
        elif line.startswith(_NEXT_FRAME):
            inside = False
            kept.append(line)
    return "".join(kept)


def report_caveat(run: Any, scenario: Optional[Any]) -> list[str]:
    """The cleanup caveat block of the markdown report, or ``[]``.

    A pull run whose beacon reported nothing gets the commands to run by hand,
    as before. One that cleaned up gets no caveat; one whose cleanup failed, or
    ran on only some endpoints, says which.
    """
    cleanup = (getattr(scenario, "cleanup", None) or {}) if scenario is not None else {}
    commands = [c for c in (cleanup.get("commands") or [])
                if isinstance(c, str) and c.strip()]
    if getattr(run, "mode", None) != "pull" or not commands:
        return []
    listing = ["", "```", *commands, "```"]
    plural = "s" if len(commands) != 1 else ""

    entries = [e for e in (getattr(run, "cleanup", None) or []) if isinstance(e, dict)]
    if not entries:
        return [
            "### Cleanup Not Run",
            "",
            f"The beacon did not run this scenario's cleanup.commands: {len(commands)} "
            f"command{plural} not run. Run them on the target by hand, or the "
            "scenario's artefacts stay on the endpoint:",
            *listing,
        ]

    problems: list[str] = []
    for e in entries:
        who = f"agent `{e.get('agent_id') or 'unknown'}`"
        reason = f" ({e['reason']})" if e.get("reason") else ""
        if e.get("status") == "failed":
            problems.append(
                f"- {who}: {e.get('failed', 0)} of {e.get('ran', 0)} commands failed{reason}")
        elif e.get("status") != "clean" and e.get("reason") != REASON_NO_STEP_STARTED:
            problems.append(f"- {who}: cleanup not run{reason}")
    reported = {e.get("agent_id") for e in entries}
    for agent in dict.fromkeys(_WARNED_AGENT_RE.findall(getattr(run, "output", "") or "")):
        if agent not in reported:
            problems.append(f"- agent `{agent}`: cleanup not run (its beacon does not run it)")
    if not problems:
        return []
    return [
        "### Cleanup Incomplete",
        "",
        "Cleanup did not finish clean on every endpoint. The `=== CLEANUP` section "
        "of the run output has each command's result:",
        "",
        *problems,
        "",
        f"Run what is left of the scenario's {len(commands)} cleanup command{plural} "
        "by hand, or its artefacts stay on the endpoint:",
        *listing,
    ]

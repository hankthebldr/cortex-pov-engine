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

The outcome is reported by the target, which may be hostile. So the entry it
fills is written by the launch (which agent, which task, how many commands),
the outcome can only fill the entry of the task it names, its fields are
normalised when stored, and nothing in it reaches the report as Markdown.

Wire shapes
-----------
Task (omitted when no command runs on the target)::

    "cleanup": {"commands": [{"command": str, "shell": "" | "powershell" | "cmd"}],
                "timeout_seconds": 120}

``/complete`` (absent from a beacon that predates this)::

    "cleanup": {"status": "clean" | "failed" | "not_run",
                "ran": int, "failed": int, "reason": str | null}

``runs.cleanup`` (written at launch as ``pending``, filled by ``/complete``)::

    [{"agent_id": str, "task_id": str, "commands": int,
      "status": "pending" | "clean" | "failed" | "not_run",
      "ran": int, "failed": int, "reason": str | null}]
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

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
#: An entry the launch wrote that no beacon has filled yet.
STATUS_PENDING = "pending"
#: What a beacon may report.
REPORTED_STATUSES = frozenset({"clean", "failed", "not_run"})
#: The longest ``reason`` kept.
REASON_MAX_CHARS = 200


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


def seed_run(run: Any, issued: Iterable[tuple[str, Any]]) -> None:
    """Write one ``pending`` entry onto ``run.cleanup`` for each task that
    carries cleanup (caller commits). ``issued`` is the launch's
    ``(agent_id, Task)`` pairs.

    This is the server's own record of which agent each task went to and how
    many commands it was sent. ``/complete`` fills the entry; it never creates
    one, so what a beacon reports is filed under the agent the task was issued
    to and counted against what it was sent. A run with no cleanup to send
    keeps ``runs.cleanup`` NULL.
    """
    entries = [
        {"agent_id": agent_id, "task_id": task.task_id,
         "commands": len(task.cleanup.get("commands") or []),
         "status": STATUS_PENDING, "ran": 0, "failed": 0, "reason": None}
        for agent_id, task in issued if getattr(task, "cleanup", None)
    ]
    if entries:
        run.cleanup = entries


# ---------------------------------------------------------------------------
# The outcome a beacon reports
# ---------------------------------------------------------------------------

#: C0 and C1 control characters and the Unicode line and paragraph separators.
_CONTROL_RE = re.compile("[\x00-\x1f\x7f-\x9f  ]+")
#: A count a beacon may send, before it is capped at the task's command count.
_COUNT_CEILING = 1_000_000


def one_line(value: Any, limit: int = REASON_MAX_CHARS) -> Optional[str]:
    """``value`` as one line of at most ``limit`` characters, or ``None`` when
    nothing is left: control characters and line breaks become spaces and runs
    of whitespace collapse."""
    if value is None:
        return None
    line = " ".join(_CONTROL_RE.sub(" ", str(value)).split())
    return line[:limit] or None


def _count(value: Any) -> int:
    """A non-negative int from a reported count; anything else is 0."""
    if isinstance(value, bool):
        return 0
    try:
        return min(max(int(value), 0), _COUNT_CEILING)
    except (TypeError, ValueError, OverflowError):
        return 0


def normalise_outcome(outcome: Any) -> Optional[dict[str, Any]]:
    """The ``cleanup`` object of a ``/complete`` body as stored, or ``None``
    when the beacon sent none.

    ``status`` is one of :data:`REPORTED_STATUSES`; anything else is a failed
    cleanup with reason ``unrecognised status``. ``ran`` and ``failed`` are
    non-negative ints, ``failed`` no larger than ``ran`` (both are capped at the
    task's command count when stored). ``reason`` is one line.
    """
    if not isinstance(outcome, dict):
        return None
    status = outcome.get("status")
    reason = one_line(outcome.get("reason"))
    if not isinstance(status, str) or status not in REPORTED_STATUSES:
        status, reason = "failed", "unrecognised status"
    ran = _count(outcome.get("ran"))
    return {"status": status, "ran": ran,
            "failed": min(_count(outcome.get("failed")), ran), "reason": reason}


#: Fill the ``pending`` entry of one task. One statement, so a fan-out's
#: endpoints finishing together cannot lose an entry; an entry already filled
#: is left alone, so a retried ``/complete`` changes nothing; and a task the
#: launch wrote no entry for matches nothing.
_FILL = text(
    "UPDATE runs SET cleanup = ("
    "  SELECT json_group_array(json(entry)) FROM ("
    "    SELECT CASE"
    "      WHEN json_extract(value, '$.task_id') = :task_id"
    "           AND json_extract(value, '$.status') = 'pending'"
    "      THEN json_set(value,"
    "             '$.status', :status,"
    "             '$.ran', min(:ran, coalesce(json_extract(value, '$.commands'), 0)),"
    "             '$.failed', min(:failed, coalesce(json_extract(value, '$.commands'), 0)),"
    "             '$.reason', :reason)"
    "      ELSE value END AS entry"
    "    FROM json_each(runs.cleanup) ORDER BY key)) "
    "WHERE run_id = :run_id"
    "  AND json_valid(cleanup) AND json_type(cleanup) = 'array'"
    "  AND EXISTS ("
    "    SELECT 1 FROM json_each(runs.cleanup)"
    "    WHERE json_extract(value, '$.task_id') = :task_id"
    "      AND json_extract(value, '$.status') = 'pending')"
)


async def record_outcome(
    db: AsyncSession, run_id: str, outcome: Any, *, task_id: Optional[str],
) -> bool:
    """Store one task's reported cleanup outcome on its run (caller commits).

    ``outcome`` is the ``cleanup`` object of a ``/complete`` body. It fills the
    entry :func:`seed_run` wrote for ``task_id`` and nothing else: the agent
    and the command count stay the launch's, and a body naming a task that was
    sent no cleanup stores nothing. Returns whether an entry was filled.
    """
    stored = normalise_outcome(outcome)
    task = (task_id or "").strip()
    if stored is None or not task:
        return False
    res = await db.execute(_FILL, {"run_id": run_id, "task_id": task, **stored})
    return bool(res.rowcount)


# ---------------------------------------------------------------------------
# The run output and the report
# ---------------------------------------------------------------------------

#: The section's frame, as ``agent/beacon/cleanup.go`` writes it.
_HEADER_RE = re.compile(r"=== CLEANUP · (\d+) commands? · ")
_FOOTER_RE = re.compile(r"^--- cleanup (\d+)/(\d+) ")
#: What ends a section that never reached its last footer.
_NEXT_FRAME = ("=== STEP ", "=== ARTIFACT STAGING", "--- COMPLETION SUMMARY ---")


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


def _code(value: Any) -> str:
    """``value`` as a Markdown inline code span: one line with no backtick of
    its own, so nothing in it is read as Markdown or HTML and it cannot start
    a line, a heading, a list item or a table row."""
    return "`" + (one_line(value) or "?").replace("`", "'") + "`"


def report_caveat(run: Any, scenario: Optional[Any]) -> list[str]:
    """The cleanup caveat block of the markdown report, or ``[]``.

    A pull run with no reported cleanup gets the commands to run by hand, as
    before. One that cleaned up gets no caveat; one whose cleanup failed, or
    was reported by only some endpoints, says which.

    Everything a beacon reported is rendered through :func:`_code` or as an
    int: it comes from the target and must not be able to write report text.
    """
    cleanup = (getattr(scenario, "cleanup", None) or {}) if scenario is not None else {}
    commands = [c for c in (cleanup.get("commands") or [])
                if isinstance(c, str) and c.strip()]
    if getattr(run, "mode", None) != "pull" or not commands:
        return []
    listing = ["", "```", *commands, "```"]
    plural = "s" if len(commands) != 1 else ""

    entries = [e for e in (getattr(run, "cleanup", None) or []) if isinstance(e, dict)]
    if all(e.get("status") == STATUS_PENDING for e in entries):
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
        who = f"agent {_code(e.get('agent_id'))}"
        status = e.get("status")
        reason = f" ({_code(e['reason'])})" if e.get("reason") else ""
        if status == "clean":
            continue
        if status == STATUS_PENDING:
            problems.append(f"- {who}: no cleanup reported")
        elif status == "failed":
            problems.append(
                f"- {who}: {_count(e.get('ran'))} of {_count(e.get('commands'))} "
                f"commands ran, {_count(e.get('failed'))} failed{reason}")
        elif e.get("reason") != REASON_NO_STEP_STARTED:
            problems.append(f"- {who}: cleanup not run{reason}")
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

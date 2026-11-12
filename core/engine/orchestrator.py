"""
CortexSim Orchestrator.

Routes run requests to the appropriate execution path:
  - mode="pull"  → creates Run record, enqueues task for the waiting agent
  - mode="push"  → creates Run record, generates bundle, returns download URL

Manages an in-memory task queue: Dict[agent_id, List[Task]]
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("cortexsim.orchestrator")


# ---------------------------------------------------------------------------
# Task dataclass — queued for pull-mode agents
# ---------------------------------------------------------------------------


# The capability a beacon must advertise before it may be handed a task that
# declares staged artifacts (``agent/capabilities.go::agentCapabilities``).
#
# This exists because "optional and additive" is NOT sufficient here. An old
# beacon decodes a task with ``artifacts`` using ``encoding/json``, which
# SILENTLY DROPS the unknown key. It would then run every step without its
# tooling, exit 0 on all of them, and the missing detections would read in a POV
# report as gaps in the customer's coverage — the precise manufactured false
# negative artifact staging exists to prevent, delivered by the back-compat
# mechanism itself.
ARTIFACT_FETCH_CAPABILITY = "artifact-fetch"


@dataclass(eq=False)
class _TaskWaiter:
    """One parked ``GET /api/agents/{id}/tasks?wait=N`` (see ``wait_for_task``).

    ``eq=False``: waiters are told apart by identity, never by value.
    """

    loop: asyncio.AbstractEventLoop
    event: asyncio.Event
    #: A newer poll for the same agent arrived; this one is never woken by an
    #: enqueue and its caller should not dequeue.
    superseded: bool = False


@dataclass
class Task:
    task_id: str
    run_id: str
    scenario_id: str
    steps: list[dict[str, Any]]
    identity_context: Optional[str]
    # scenario.execution_identity["default"] — the agent uses this as the
    # last-resort username when a step carries no identity and the launch
    # supplied no identity_context (see the Phase 2 identity-resolution rule).
    identity_default: Optional[str] = None
    # scenario.cgo_anchor — the declared Causality Group Owner. When present (with
    # the steps' causality blocks) it signals the beacon to execute the run as a
    # connected process chain rooted at this anchor. None for contract-less
    # scenarios, which the beacon runs per-step exactly as before.
    cgo_anchor: Optional[dict[str, Any]] = None
    # Staged tool artifacts this task requires BEFORE any step runs — the
    # beacon half of the payload shelf (docs/reference/payload-shelf.md).
    #
    # Each entry is the shape `payload_shelf.compose(consumer="beacon")`
    # produces, projected onto the beacon's wire contract:
    #
    #   {"name": "linpeas.sh",                  # THE SHELF KEY
    #    "sha256": "0ea7e9…",                   # 64 lowercase hex, resolved on
    #                                           #   THIS SimCore at enqueue time
    #    "size_bytes": 1106683,                 # optional; distinguishes a
    #                                           #   truncating proxy from tampering
    #    "path": "/api/shelf/payload/linpeas.sh",  # SERVER-RELATIVE, never a URL
    #    "dest": "/tmp/.cache/sysinfo.sh",      # absolute, computed server-side
    #    "mode": "0755",                        # POSIX octal; recorded, not
    #                                           #   applied, on Windows
    #    "steps": ["step-05"]}                  # attribution for a staging failure
    #
    # `path` is deliberately server-RELATIVE: the beacon joins it onto its own
    # ServerURL, so task data can never redirect the fetch to an origin the
    # operator never named. The digest travels IN the task so the beacon
    # verifies against a value it carried in rather than one it fetched from the
    # same server it is trusting — the identical anchoring
    # ``k8s_manifest._resolve_payloads`` uses for the pod.
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    # See docs/design/agent-runtime-dependencies.md. Mirrors
    # CORTEXSIM_XSIAM_ALLOW_WRITE's posture: only True when BOTH the launch
    # request explicitly asked for it AND CORTEXSIM_AGENT_ALLOW_RUNTIME_INSTALL
    # is set on this deployment (see launch()). When True, the beacon may
    # attempt a package-manager install to satisfy a step's declared
    # `requires_interpreters` instead of refusing to run it.
    runtime_install_authorized: bool = False
    created_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "task_id": self.task_id,
            "run_id": self.run_id,
            "scenario_id": self.scenario_id,
            "steps": self.steps,
            "identity_context": self.identity_context,
            "identity_default": self.identity_default,
            "created_at": self.created_at.isoformat(),
        }
        if self.cgo_anchor:
            payload["cgo_anchor"] = self.cgo_anchor
        # Omitted entirely when empty — the same pattern cgo_anchor uses — so a
        # task for any of today's 169 scenarios is byte-identical to what
        # shipped before artifact staging existed.
        if self.artifacts:
            payload["artifacts"] = self.artifacts
        # Omitted when False — matches Go's `omitempty` on the bool field, so a
        # task for any run that never touched this feature is byte-identical.
        if self.runtime_install_authorized:
            payload["runtime_install_authorized"] = True
        return payload

    def requires_artifact_fetch(self) -> bool:
        """True when this task cannot be honoured by a beacon that predates
        artifact staging."""
        return bool(self.artifacts)


# ---------------------------------------------------------------------------
# LaunchResult
# ---------------------------------------------------------------------------


@dataclass
class LaunchResult:
    success: bool
    run_id: Optional[str] = None
    mode: Optional[str] = None
    message: str = ""
    download_url: Optional[str] = None  # push mode only
    error: Optional[str] = None
    # Machine code + structured payload for a refusal. `error` stays the prose
    # a human reads; these are what a console branches on instead of regexing
    # the sentence. Default LAUNCH_FAILED preserves the historical envelope for
    # every refusal that has not been given a specific code yet.
    error_code: str = "LAUNCH_FAILED"
    error_detail: dict[str, Any] = field(default_factory=dict)
    # The HTTP status this refusal should surface as, when the layer that
    # refused already knows it — PayloadResolutionError carries its own
    # `http_status`, and the shelf route (core/api/payloads.py) has always
    # honoured it. Carrying the value beats re-deriving it from a hand-kept
    # list of codes in the API, which cannot stay right: the payload family is
    # NOT uniformly 409 (PayloadDestRefused is 400), so an enumeration makes
    # the two routes disagree about the same exception. None = let the API
    # decide from `error_code`, which is the historical behaviour.
    http_status: Optional[int] = None
    # Things the operator must know about a launch that was NOT refused. Empty
    # for an unremarkable launch, so the API response for one is byte-identical
    # to before. Two element shapes share the list, each as its producer and
    # its tests define it:
    #   * ``str`` -- a reservation SimCore also writes onto Run.output: the
    #     target beacon is offline (the task waits), its OS is unknown (posix
    #     was assumed), or a step runs a platform variant whose expectations
    #     were authored against the primary command;
    #   * ``dict`` -- an advisory, non-refusing notice from the container-agent
    #     launch gate (engine/agent_launch_gate.py), e.g.
    #     ``{"code": "AGENT_NO_RBAC_FOR_KUBECTL", "steps": [...], "agent_id": ...}``,
    #     which SimCore also writes onto Run.output as one ``!! CODE: ...`` line
    #     (``_gate_advisory_line``).
    # The API surfaces both under ``warnings``.
    warnings: list[Any] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Pull-mode platform gate (BEACON-01 / CONT-01)
# ---------------------------------------------------------------------------
#
# A pull task used to ship ``scenario.steps`` verbatim, whatever the target's
# operating system. A Windows beacon handed a POSIX primary runs it through
# PowerShell, which exits 0 on text it cannot execute: the step "succeeds",
# nothing the scenario expects fires, and every expectation reads `missed` — a
# false negative about the customer's Windows coverage that CortexSim made.
#
# The push path already answers "can this content run on that OS?" per target
# (``push_generator.resolve_target``). The pull path now asks the SAME function,
# so a scenario that cannot emit a Windows bundle cannot be dispatched to a
# Windows beacon either, and the two delivery modes cannot disagree.

#: ``Agent.os`` values (lower-cased) and the execution target each one means.
#: The beacon registers ``runtime.GOOS``; the installers send the same strings.
_WINDOWS_AGENT_OS = frozenset({"windows"})
_POSIX_AGENT_OS = frozenset({"linux", "darwin", "macos"})


class AgentOsUnrecognised(ValueError):
    """``Agent.os`` names no OS this engine knows how to target.

    Raised rather than defaulted: guessing ``posix`` for an OS nobody
    recognised is how a run executes the wrong dialect and reads green.
    """


def platform_target_for_os(os_value: Optional[str]) -> tuple[str, Optional[str]]:
    """``(target, warning)`` for an agent's registered OS.

    ``windows`` → ``windows``; ``linux`` / ``darwin`` / ``macos`` → ``posix``.
    An EMPTY value (a legacy registration that never said) is treated as
    ``posix`` — the historical behaviour — with an explicit warning, because
    that is an assumption the run record must carry. Any other value raises
    :class:`AgentOsUnrecognised`.
    """
    norm = (os_value or "").strip().lower()
    if not norm:
        return "posix", (
            "!! TARGET_AGENT_OS_UNKNOWN: the target agent registered no operating "
            "system, so POSIX commands were dispatched. If this endpoint is "
            "Windows, every step ran the wrong dialect — re-register the beacon."
        )
    if norm in _WINDOWS_AGENT_OS:
        return "windows", None
    if norm in _POSIX_AGENT_OS:
        return "posix", None
    raise AgentOsUnrecognised(norm)


def resolve_steps_for_target(
    steps: list[dict[str, Any]], target: str,
) -> tuple[list[dict[str, Any]], list[str], list[tuple[str, str]]]:
    """Resolve each step's command for ``target``.

    Returns ``(dispatched_steps, variant_markers, unresolved)``:

    * ``dispatched_steps`` — NEW step dicts (the input is never mutated: the
      scenario row is shared across runs) whose ``command`` is the text the
      target can run, plus ``shell`` (``powershell`` / ``cmd`` on windows, ``""``
      on posix — the beacon picks its interpreter from it) and
      ``resolved_from`` (``primary`` / ``neutral`` / ``variant:<os>``). A
      ``primary`` resolution keeps the authored command byte-for-byte.
    * ``variant_markers`` — one ``!! PLATFORM VARIANT`` line per step that runs
      a variant: its ``expected_detections`` were authored against the primary
      command, so a miss on it is not yet a coverage measurement.
    * ``unresolved`` — ``(step_id, REASON_CODE)`` for every step the target
      cannot run. The caller REFUSES when this is non-empty: a partially
      dispatched chain runs green, emits a subset of the attack, and the report
      reads the gap as the customer's miss.

    Pure: no DB, no I/O. Resolution reads the RAW authored steps — exactly the
    document the push generator judges — so pull and push agree on every
    scenario; ``{adapter:*}`` / ``{stitch:*}`` rendering happens afterwards on
    whatever text was chosen.
    """
    from engine.push_generator import resolve_target  # noqa: PLC0415

    resolution = resolve_target({"steps": steps}, target)  # type: ignore[arg-type]
    by_id = {r.step_id: r for r in resolution.steps}
    dispatched: list[dict[str, Any]] = []
    markers: list[str] = []
    for step in steps:
        new_step = dict(step)
        r = by_id.get(step.get("id", "step-??"))
        if r is not None:
            if r.source != "primary":
                new_step["command"] = r.command
            new_step["shell"] = r.shell if target == "windows" else ""
            new_step["resolved_from"] = r.source
            if r.source.startswith("variant:"):
                variant_os = r.source.split(":", 1)[1]
                markers.append(
                    f"!! PLATFORM VARIANT: step={r.step_id} ran its {variant_os} "
                    "variant; its expected_detections were authored against the "
                    "primary command and are unverified for this OS"
                )
        dispatched.append(new_step)
    return dispatched, markers, list(resolution.unresolved)


def _label_matches_os(platform: Any, agent_os: str) -> bool:
    """Does one ``platforms`` label name ``agent_os``? The console's rule
    (``scenarioPreflight.js::matchesOs``): a container/k8s step runs wherever
    the runtime is, which a Linux beacon satisfies; macos and darwin are one."""
    p = str(platform).strip().lower()
    if p == agent_os:
        return True
    if p in ("container", "k8s") and agent_os == "linux":
        return True
    return {p, agent_os} == {"macos", "darwin"}


def posix_family_markers(
    steps: list[dict[str, Any]], agent_os: Optional[str], agent_id: str,
) -> list[str]:
    """One ``!! PLATFORM MISMATCH`` line per step whose labels exclude this
    POSIX beacon's OS (RG-UI-01, server half).

    ``resolve_steps_for_target`` knows two targets, and a darwin beacon is
    ``posix``: it is sent the primary command of a Linux-only step, and a Linux
    beacon the commands of a macOS-only one. The step ``platforms`` labels are
    the only linux-versus-macOS statement the corpus makes, so they are read
    here exactly as the console's launch preflight reads them
    (``posixFamilyCheck``): a step that declares a non-Windows platform, none
    of which names this OS, is marked. A step labelled only ``windows`` says
    nothing about linux versus macOS and is left alone, as is a step with no
    labels. Nothing is refused and no command changes: the line goes onto the
    run and the launch response, because a miss on such a step is not
    evidence about the detection it expects.

    Empty for a Windows beacon, an OS that registered nothing (that already
    carries ``TARGET_AGENT_OS_UNKNOWN``) and an unremarkable launch.
    """
    os_norm = (agent_os or "").strip().lower()
    if os_norm not in _POSIX_AGENT_OS:
        return []
    markers: list[str] = []
    for step in steps:
        labels = [str(p).strip().lower() for p in (step.get("platforms") or [])]
        posix_labels = [p for p in labels if p != "windows"]
        if not posix_labels or any(_label_matches_os(p, os_norm) for p in labels):
            continue
        markers.append(
            f"!! PLATFORM MISMATCH: step={step.get('id', 'step-??')} is authored for "
            f"{'/'.join(posix_labels)}, not {os_norm}; SimCore dispatched it unchanged "
            f"to agent '{agent_id}' ({os_norm}), so its expected_detections are "
            "unverified on this OS"
        )
    return markers


def _unsatisfiable_refusal(
    *, target: str, agent_id: str, agent_os: Optional[str],
    unresolved: list[tuple[str, str]],
) -> "LaunchResult":
    """The 409 for a pull launch whose content the target OS cannot run.

    Mirrors ``BUNDLE_TARGET_UNSATISFIABLE`` (the push twin) so a console
    renders both with one code path.
    """
    from engine.push_generator import unsatisfiable_hints  # noqa: PLC0415

    # The push twin's per-reason fix (MIXED_DIALECT / CMD_PERCENT_CONTEXT need
    # the variant rewritten, not another declared), plus the pull alternative.
    hint = "; ".join(unsatisfiable_hints(target, [r for _, r in unresolved])) + (
        ", or launch against a POSIX agent" if target == "windows"
        else ", or launch against a Windows agent")
    steps = ", ".join(f"{sid} ({reason})" for sid, reason in unresolved)
    return LaunchResult(
        success=False,
        error=(
            f"PULL_TARGET_UNSATISFIABLE: agent '{agent_id}' is {target} and "
            f"cannot run step(s) [{steps}] — {hint}"
        ),
        error_code="PULL_TARGET_UNSATISFIABLE",
        error_detail={
            "target": target,
            "agent_id": agent_id,
            "agent_os": agent_os,
            "unresolved": [{"step_id": s, "reason": r} for s, r in unresolved],
            "hint": hint,
        },
        http_status=409,
    )


def _os_refusal(agent_id: str, agent_os: str) -> "LaunchResult":
    return LaunchResult(
        success=False,
        error=(
            f"PULL_TARGET_OS_UNRECOGNISED: agent '{agent_id}' registered os="
            f"'{agent_os}', which names no target this engine can dispatch to "
            "(windows, linux, darwin, macos) — re-register the beacon"
        ),
        error_code="PULL_TARGET_OS_UNRECOGNISED",
        error_detail={"agent_id": agent_id, "agent_os": agent_os},
        http_status=409,
    )


def _offline_warning(agent: Any, now: datetime) -> Optional[str]:
    """A non-refusing warning when the target beacon is not polling."""
    from api.agents import _derive_status  # noqa: PLC0415 — one liveness rule

    status, age = _derive_status(getattr(agent, "last_seen", None), now)
    if status != "offline":
        return None
    seen = "never" if getattr(agent, "last_seen", None) is None else f"{int(age)}s ago"
    return (
        f"TARGET_AGENT_OFFLINE: agent '{agent.agent_id}' last polled {seen}; the "
        "task is queued and runs only when its beacon polls again"
    )


def _gate_advisory_line(warning: dict[str, Any]) -> str:
    """A container-agent gate advisory (engine/agent_launch_gate.py) as the
    reservation text Run.output carries for it (RG-RUNS-06), e.g.
    ``AGENT_NO_RBAC_FOR_KUBECTL: agent 'pod-1'; steps step-01``. The launch
    response keeps the dict itself."""
    parts = []
    if warning.get("agent_id"):
        parts.append(f"agent '{warning['agent_id']}'")
    if warning.get("steps"):
        parts.append("steps " + ", ".join(str(s) for s in warning["steps"]))
    if warning.get("detail"):
        parts.append(str(warning["detail"]))
    return f"{warning['code']}: " + "; ".join(parts)


@dataclass
class _PullPlan:
    """The single-endpoint dispatch decided BEFORE the Run row exists."""

    steps: list[dict[str, Any]]
    target: str
    output_markers: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class Orchestrator:
    """
    Durable task queue + DB run record manager.

    The in-memory queue (``_queue``) is the hot path, but it is now a
    write-through cache over the ``queued_tasks`` DB table (GAP-API-005): every
    enqueue is mirrored to the DB, every dequeue/abort deletes the row, and the
    queue is rehydrated from the DB on startup (:meth:`rehydrate`). A SimCore
    restart therefore no longer strands a ``running`` Run with a vanished task.
    """

    def __init__(self) -> None:
        # agent_id -> list of pending Task objects
        self._queue: dict[str, list[Task]] = {}
        # run_ids the operator aborted — drives the agent's /control stop
        # signal and prevents a queued-but-undelivered task from executing.
        # Unbounded in principle but runs are few/short; pruned on /complete.
        self._aborted: set[str] = set()
        # agent_id -> the long-polls parked for that agent, oldest first, for
        # sub-second wakeup. ONE entry per waiter (OPS-09): all waiters used to
        # share a single asyncio.Event, which wakes first-in first-out, so the
        # next task went to the OLDEST poll — after a beacon restart that is
        # the handler whose socket is closed. The durable row was deleted, the
        # run read delivered, and no host received it. Now a newer poll
        # supersedes the older ones and an enqueue wakes only the current one.
        #
        # Each entry carries its own loop: an asyncio.Event binds to the loop
        # that first awaits it and raises "bound to a different event loop" on
        # any other. uvicorn runs one loop per process, but TestClient does not
        # — which is precisely why no Python test covered this path at first.
        self._waiters: dict[str, list[_TaskWaiter]] = {}
        # run_id -> when a beacon last polled GET /api/runs/{id}/control. The
        # beacon polls it before every step and every 2 s while one runs, so
        # it is the one signal that says "a beacon is executing THIS run" —
        # an agent's last_seen only says the agent is alive. In memory on
        # purpose: it is a liveness signal, and a restart is measured from
        # `started_at` (see engine.beacon_lost.terminalise_abandoned_runs).
        self._control_polls: dict[str, datetime] = {}
        # Idle evidence per ENDPOINT, for a multi-endpoint fan-out. The beacon
        # executes one task at a time inside its poll loop, so a GET /tasks
        # from an agent proves that agent is holding no task at that moment.
        # `_task_polls[agent]` is the sequence number of its latest poll;
        # `_deliveries[run][agent]` the sequence number at which that agent
        # collected the run's task. A poll numbered after the delivery means
        # the agent has let go of its partition (finished it, or dropped its
        # /complete). Sequence numbers, not clock readings, because the poll
        # that COLLECTS a task and the delivery it causes share a timestamp.
        # In memory, like `_control_polls`: a delivery from before this
        # process booted is unknown, and then any poll since boot counts.
        self._liveness_seq = itertools.count(1)
        self._task_polls: dict[str, int] = {}
        self._deliveries: dict[str, dict[str, int]] = {}
        self.started_at: datetime = datetime.utcnow()

    # ------------------------------------------------------------------
    # control-channel liveness (OPS-03)
    # ------------------------------------------------------------------

    #: Bound on the control-poll map. Entries are dropped when a run reaches a
    #: terminal state; this only matters if that never happens.
    _CONTROL_POLL_CAP = 10_000

    def note_control_poll(self, run_id: str, when: Optional[datetime] = None) -> None:
        """Record that a beacon just polled this run's control channel."""
        if run_id not in self._control_polls and len(self._control_polls) >= self._CONTROL_POLL_CAP:
            oldest = min(self._control_polls, key=self._control_polls.__getitem__)
            self._control_polls.pop(oldest, None)
        self._control_polls[run_id] = when or datetime.utcnow()

    def last_control_poll(self, run_id: str) -> Optional[datetime]:
        """When a beacon last polled this run's control channel, this process."""
        return self._control_polls.get(run_id)

    def forget_control_poll(self, run_id: str) -> None:
        """Drop a terminal run's liveness entries (control poll and per-endpoint
        deliveries). Idempotent."""
        self._control_polls.pop(run_id, None)
        self._deliveries.pop(run_id, None)

    def note_task_poll(self, agent_id: str) -> None:
        """Record that ``agent_id`` just polled ``/tasks`` — it holds no task."""
        if agent_id not in self._task_polls and len(self._task_polls) >= self._CONTROL_POLL_CAP:
            self._task_polls.pop(next(iter(self._task_polls)), None)
        self._task_polls[agent_id] = next(self._liveness_seq)

    def note_delivery(self, run_id: str, agent_id: str) -> None:
        """Record that ``agent_id`` just collected its task for ``run_id``."""
        if run_id not in self._deliveries and len(self._deliveries) >= self._CONTROL_POLL_CAP:
            self._deliveries.pop(next(iter(self._deliveries)), None)
        self._deliveries.setdefault(run_id, {})[agent_id] = next(self._liveness_seq)

    def idle_since_delivery(self, run_id: str, agent_id: str) -> bool:
        """Has ``agent_id`` polled for new work since it could have collected
        its task for ``run_id``?

        True means the agent has let go of that partition. False — including
        "no poll from this agent since boot" — means it may still hold it.
        """
        polled = self._task_polls.get(agent_id)
        if polled is None:
            return False
        return polled > self._deliveries.get(run_id, {}).get(agent_id, 0)

    def has_queued_task(self, run_id: str) -> bool:
        """Is any task for ``run_id`` still waiting in an agent's queue?"""
        return any(t.run_id == run_id for q in self._queue.values() for t in q)

    def reset_liveness(self) -> None:
        """Forget every liveness record. A restarted process has none."""
        self._control_polls.clear()
        self._task_polls.clear()
        self._deliveries.clear()

    async def reap_abandoned(
        self, agent_id: str, db: AsyncSession, now: Optional[datetime] = None,
    ) -> list[str]:
        """Terminalise ``agent_id``'s delivered runs its beacon walked away from.

        Never lets a reaping failure break task delivery: the run stays
        ``running`` and the next poll tries again.

        Runs in its OWN session on the caller's engine: terminalisation may
        roll back (a racing ``/complete``, a scoring fault), and a rollback on
        the request's session would expire the ``Agent`` row the /tasks
        handler still reads after this returns.
        """
        from config import settings as _settings  # noqa: PLC0415
        from engine.beacon_lost import terminalise_abandoned_runs  # noqa: PLC0415

        bind = getattr(db, "bind", None)
        try:
            if bind is None:  # pragma: no cover - an unbound session: share it
                return await terminalise_abandoned_runs(
                    db, now or datetime.utcnow(),
                    after_seconds=int(_settings.CORTEXSIM_BEACON_LOST_AFTER_SECONDS),
                    agent_id=agent_id, control_polls=self,
                )
            async with AsyncSession(bind, expire_on_commit=False) as reap_db:
                return await terminalise_abandoned_runs(
                    reap_db, now or datetime.utcnow(),
                    after_seconds=int(_settings.CORTEXSIM_BEACON_LOST_AFTER_SECONDS),
                    agent_id=agent_id, control_polls=self,
                )
        except Exception:
            logger.exception("abandoned-run reaping failed agent=%s", agent_id)
            return []

    # ------------------------------------------------------------------
    # launch
    # ------------------------------------------------------------------

    async def launch(
        self,
        scenario_id: str,
        mode: str,
        db: AsyncSession,
        target_agent_id: Optional[str] = None,
        identity: Optional[str] = None,
        consent: Optional[dict[str, bool]] = None,
        allow_runtime_install: bool = False,
        agent_profile_mode: str = "unknown",
        bas_mode_attested: Optional[bool] = None,
        soakless_attested: Optional[bool] = None,
        attested_by: Optional[str] = None,
        paired_with_run_id: Optional[str] = None,
        canary_run_id: Optional[str] = None,
        allow_missing_tools: bool = False,
        module_modes: Optional[dict[str, str]] = None,
    ) -> LaunchResult:
        """
        Create a Run record and route to pull or push path.

        ``allow_missing_tools`` turns off ONLY the container-agent tool refusal
        (``AGENT_TOOL_ABSENT``); a posture gap is never overridable.

        ``consent`` carries launch-time authorizations. Honoured keys:

          - ``c2_authorized`` (bool) — required when any external_tools
            entry references a ``safety_class: c2-framework`` adapter
          - ``simulation_authorized`` (bool) — required for
            ``dual-use-lab-only`` adapters
          - ``cluster_privilege_authorized`` (bool) — required for a **push**
            launch of a scenario whose ``cluster_posture`` plants cluster-scoped
            or wildcard RBAC
          - ``node_access_authorized`` (bool) — required for a **push** launch
            of a scenario whose ``cluster_posture`` plants privileged /
            hostPID / hostNetwork / hostPath / hostPort capabilities. Also
            requires ``CORTEXSIM_ALLOW_PRIVILEGED_K8S`` on the deployment.

        The last two are ORTHOGONAL, not nested: a manifest that wants wildcard
        RBAC and touches no node needs only the first.

        ``allow_runtime_install`` (docs/design/agent-runtime-dependencies.md) —
        per-run authorization for the beacon to attempt a package-manager
        install when a step's declared ``requires_interpreters`` is absent on
        the target, instead of refusing to run the step. Mirrors
        ``CORTEXSIM_XSIAM_ALLOW_WRITE``'s two-key posture: this flag ALONE does
        nothing — it also requires ``CORTEXSIM_AGENT_ALLOW_RUNTIME_INSTALL`` on
        this deployment. The EFFECTIVE (both-gates) value is what is recorded
        on the Run and threaded onto the Task; a request that asked for it on a
        deployment that has not enabled it is recorded honestly as False, not
        silently upgraded or silently dropped without a trace.

        Prevention-efficacy launch context (``agent_profile_mode`` and the
        attestation fields) is operator-declared and persisted verbatim on the
        Run, including ``module_modes`` (per-module Action Mode, validated at
        the API edge). ``paired_with_run_id`` links a block-mode launch to its completed
        report-mode run and is validated by ``pairing.validate_pair`` after the
        consent gate and before the Run row exists, so a refused pair
        (``PAIR_INVALID``) seeds nothing.

        Missing consent aborts the launch with a structured error and creates
        no Run record.
        """
        from config import settings as _settings  # noqa: PLC0415
        from models import Agent, Run, Scenario  # noqa: PLC0415
        from engine.runtime_preflight import evaluate_runtime_readiness  # noqa: PLC0415

        # Fetch scenario
        result = await db.execute(
            select(Scenario).where(Scenario.scenario_id == scenario_id)
        )
        scenario: Optional[Scenario] = result.scalar_one_or_none()
        if scenario is None:
            return LaunchResult(
                success=False,
                error=f"Scenario '{scenario_id}' not found",
            )

        # The launch consent gate. Refuse to create the Run record if the
        # scenario uses a gated adapter, or would stage a Kubernetes workload
        # that plants gated cluster capabilities, without the matching consent.
        gate_error = _check_launch_consent(scenario, consent or {}, mode=mode)
        if gate_error is not None:
            logger.warning(
                "Launch refused scenario=%s code=%s reason=%s",
                scenario_id, _refusal_code(gate_error), gate_error,
            )
            return LaunchResult(
                success=False,
                error=str(gate_error),
                error_code=_refusal_code(gate_error),
                error_detail=_refusal_detail(gate_error),
            )

        # Pair validation: after consent, before any Run row, so a refused pair
        # leaves the database untouched.
        if paired_with_run_id:
            from engine.pairing import validate_pair  # noqa: PLC0415

            first = (await db.execute(
                select(Run).where(Run.run_id == paired_with_run_id)
            )).scalar_one_or_none()
            if first is None:
                pair_error: Optional[str] = (
                    f"paired_with_run_id '{paired_with_run_id}' does not match any run"
                )
            else:
                pair_error = validate_pair(
                    first,
                    scenario_id=scenario_id,
                    target_agent_id=target_agent_id,
                    agent_profile_mode=agent_profile_mode,
                )
                if pair_error is None:
                    other = (await db.execute(
                        select(Run.run_id).where(
                            Run.paired_with_run_id == paired_with_run_id
                        ).limit(1)
                    )).scalar_one_or_none()
                    if other is not None:
                        pair_error = (
                            f"first run {paired_with_run_id} is already paired with {other}"
                        )
            if pair_error is not None:
                logger.warning(
                    "Launch refused scenario=%s code=PAIR_INVALID reason=%s",
                    scenario_id, pair_error,
                )
                return LaunchResult(
                    success=False,
                    error="Run pairing refused",
                    error_code="PAIR_INVALID",
                    error_detail=pair_error,
                )

        # Canary validation: same position and refusal shape as the pair check.
        block_mode_status: Optional[str] = None
        if canary_run_id:
            from engine import canary  # noqa: PLC0415
            from models import Result  # noqa: PLC0415

            canary_run = (await db.execute(
                select(Run).where(Run.run_id == canary_run_id)
            )).scalar_one_or_none()
            canary_error: Optional[str] = None
            if canary_run is None:
                canary_error = f"canary_run_id '{canary_run_id}' does not match any run"
            elif canary_run.scenario_id != canary.canary_scenario_id("linux"):
                canary_error = (
                    f"run {canary_run_id} is scenario '{canary_run.scenario_id}', "
                    "not the configured canary scenario"
                )
            elif canary_run.target != target_agent_id:
                canary_error = (
                    f"canary run {canary_run_id} targeted '{canary_run.target}', "
                    f"not this launch's target '{target_agent_id}'"
                )
            elif canary_run.status == "aborted":
                canary_error = f"canary run {canary_run_id} was aborted and proves nothing"
            elif not canary.is_terminal(canary_run):
                canary_error = f"canary run {canary_run_id} has not finished"
            if canary_error is not None:
                logger.warning(
                    "Launch refused scenario=%s code=CANARY_INVALID reason=%s",
                    scenario_id, canary_error,
                )
                return LaunchResult(
                    success=False,
                    error="Canary run refused",
                    error_code="CANARY_INVALID",
                    error_detail=canary_error,
                )
            canary_results = (await db.execute(
                select(Result).where(Result.run_id == canary_run_id)
            )).scalars().all()
            block_mode_status = canary.block_mode_status_from(canary_run, canary_results)

        # Two-key gate — same posture as CORTEXSIM_XSIAM_ALLOW_WRITE. A single
        # mis-set request body can never authorize a target mutation on its own.
        runtime_install_authorized = bool(
            allow_runtime_install and _settings.CORTEXSIM_AGENT_ALLOW_RUNTIME_INSTALL
        )

        # ADVISORY preflight (docs/design/agent-runtime-dependencies.md):
        # visible before dispatch, never the enforcement. Only meaningful for a
        # pull-mode launch against a known agent; push mode and an unresolved
        # target record no gaps here (None, not [] — "not checked", not
        # "checked and clean").
        runtime_dependency_gaps: Optional[list[dict[str, Any]]] = None
        pull_plan: Optional[_PullPlan] = None
        launch_warnings: list[str] = []
        if mode == "pull" and target_agent_id:
            agent_row = (await db.execute(
                select(Agent).where(Agent.agent_id == target_agent_id)
            )).scalar_one_or_none()
            # The platform gate and the enrollment check run BEFORE the Run
            # row exists, so a launch that cannot be dispatched seeds nothing.
            planned = self._plan_single_endpoint_pull(
                scenario, target_agent_id, agent_row,
            )
            if isinstance(planned, LaunchResult):
                logger.warning(
                    "Launch refused scenario=%s code=%s target=%s detail=%s",
                    scenario_id, planned.error_code, target_agent_id,
                    planned.error_detail,
                )
                return planned
            pull_plan = planned
            if agent_row is not None:
                offline = _offline_warning(agent_row, datetime.utcnow())
                if offline:
                    launch_warnings.append(offline)
                readiness = evaluate_runtime_readiness(scenario, agent_row.interpreters)
                runtime_dependency_gaps = [g.to_dict() for g in readiness.gaps]
                if readiness.gaps:
                    logger.warning(
                        "Runtime-dependency preflight gap scenario=%s agent=%s gaps=%s "
                        "(advisory — the beacon still checks live at execution time)",
                        scenario_id, target_agent_id, runtime_dependency_gaps,
                    )

        run_id = str(uuid.uuid4())
        now = datetime.utcnow()

        # The profile mode is itself an attestation, as is any attestation field.
        attested = (
            agent_profile_mode != "unknown"
            or bas_mode_attested is not None
            or soakless_attested is not None
            or attested_by is not None
            or module_modes is not None
        )

        run = Run(
            run_id=run_id,
            scenario_id=scenario_id,
            mode=mode,
            target=target_agent_id,
            identity_context=identity,
            status="pending",
            started_at=now,
            runtime_install_authorized=runtime_install_authorized,
            runtime_dependency_gaps=runtime_dependency_gaps,
            agent_profile_mode=agent_profile_mode,
            bas_mode_attested=bas_mode_attested,
            soakless_attested=soakless_attested,
            attested_by=attested_by,
            attested_at=now if attested else None,
            paired_with_run_id=paired_with_run_id,
            canary_run_id=canary_run_id,
            block_mode_status=block_mode_status,
            module_modes=module_modes,
        )
        db.add(run)
        try:
            await db.commit()
        except IntegrityError:
            # The unique partial index on runs.paired_with_run_id is the
            # authority when two launches race past the pre-insert read.
            await db.rollback()
            if paired_with_run_id:
                return LaunchResult(
                    success=False,
                    error="Run pairing refused",
                    error_code="PAIR_INVALID",
                    error_detail=f"first run {paired_with_run_id} is already paired",
                )
            raise

        # Auto-populate Result records from scenario expected_detections
        await self._seed_results(run_id, scenario, now, db)

        logger.info(
            "Run created run_id=%s scenario=%s mode=%s target=%s",
            run_id,
            scenario_id,
            mode,
            target_agent_id,
        )

        if mode == "pull":
            launched = await self._handle_pull(
                run_id, scenario, target_agent_id, identity, db,
                runtime_install_authorized=runtime_install_authorized,
                pull_plan=pull_plan,
                launch_warnings=launch_warnings,
                allow_missing_tools=allow_missing_tools,
            )
            # Launch-time warnings first (offline target), then whatever the
            # dispatch itself had to say (variants, unknown OS, the
            # container-agent gate's advisory notices), then the SOC Framework
            # Auto Triage advisories.
            launched.warnings = launch_warnings + launched.warnings
            return await self._with_socfw_advisories(
                launched, db, scenario, paired_with_run_id, agent_profile_mode)
        elif mode == "push":
            result = await self._handle_push(run_id, scenario, db)
            return await self._with_socfw_advisories(
                result, db, scenario, paired_with_run_id, agent_profile_mode)
        else:
            return await self._refuse_after_seed(
                db, run_id,
                error=f"Unknown mode '{mode}' — must be 'pull' or 'push'",
            )

    # ------------------------------------------------------------------
    # pull pre-flight: enrollment + platform gate, before any Run row
    # ------------------------------------------------------------------

    @staticmethod
    def _plan_single_endpoint_pull(
        scenario: Any, target_agent_id: str, agent_row: Optional[Any],
    ) -> "Optional[_PullPlan | LaunchResult]":
        """Decide a single-endpoint pull dispatch, or refuse it, pre-seed.

        Returns ``None`` for a composed (multi-channel / per-step target) run:
        its partitions are resolved per target agent in :meth:`_handle_pull`,
        after that path's own all-or-nothing enrollment check.

        Refusals (each a 409 with no Run, no Result, no task):

        * ``TARGET_AGENT_NOT_ENROLLED`` — the target has no Agent row. This
          used to seed a run and queue a task that nothing would ever collect,
          which then sat ``running`` until a restart called it an orphan.
        * ``PULL_TARGET_OS_UNRECOGNISED`` — ``Agent.os`` names no known OS.
        * ``PULL_TARGET_UNSATISFIABLE`` — some step has no command the target
          OS can run (the push path's ``BUNDLE_TARGET_UNSATISFIABLE``).
        """
        from engine.scenario_loader import effective_channel  # noqa: PLC0415

        steps = list(getattr(scenario, "steps", None) or [])
        if any(effective_channel(s) != "agent" or s.get("target") for s in steps):
            return None
        if agent_row is None:
            return LaunchResult(
                success=False,
                error=(
                    f"TARGET_AGENT_NOT_ENROLLED: target agent '{target_agent_id}' is "
                    "not enrolled — enroll it, or launch against an enrolled agent"
                ),
                error_code="TARGET_AGENT_NOT_ENROLLED",
                error_detail={
                    "missing_agents": [target_agent_id],
                    "referencing_steps": {
                        target_agent_id: [s.get("id") for s in steps],
                    },
                },
                http_status=409,
            )
        try:
            target, os_warning = platform_target_for_os(agent_row.os)
        except AgentOsUnrecognised:
            return _os_refusal(target_agent_id, agent_row.os)
        dispatched, markers, unresolved = resolve_steps_for_target(steps, target)
        if unresolved:
            return _unsatisfiable_refusal(
                target=target, agent_id=target_agent_id, agent_os=agent_row.os,
                unresolved=unresolved,
            )
        return _PullPlan(
            steps=dispatched, target=target,
            output_markers=([os_warning] if os_warning else []) + markers
            + posix_family_markers(steps, agent_row.os, target_agent_id),
        )

    # ------------------------------------------------------------------
    # SOC Framework Auto Triage advisory (never refuses, never calls a tenant)
    # ------------------------------------------------------------------

    async def _with_socfw_advisories(
        self,
        result: LaunchResult,
        db: AsyncSession,
        scenario: Any,
        paired_with_run_id: Optional[str],
        agent_profile_mode: str,
    ) -> LaunchResult:
        """Append SOCFW_TRIAGE_MAY_SEAL advisories to a successful launch.

        Reads only the stored response-layer probe of each ``xsiam_tenant``
        integration. A problem here is logged and dropped: an advisory must
        never fail or block the launch.

        "Paired" = the launch carries ``paired_with_run_id`` (the block-mode
        half of a pair) or declares ``agent_profile_mode == "block"``. There is
        no other pair flag on the launch request; the report-mode first run of a
        pair is indistinguishable from an ordinary run at launch time.
        """
        if not result.success:
            return result
        try:
            from engine.socfw_advisory import triage_advisories  # noqa: PLC0415
            from security.credentials import CredentialStore  # noqa: PLC0415

            detection_types = {
                d.get("type")
                for st in (scenario.steps or []) if isinstance(st, dict)
                for d in (st.get("expected_detections") or []) if isinstance(d, dict)
                if d.get("type")
            }
            paired = bool(paired_with_run_id) or agent_profile_mode == "block"
            now = datetime.utcnow()
            extra: list[dict[str, Any]] = []
            for row in await CredentialStore(db).list_integrations("xsiam_tenant"):
                extra.extend(triage_advisories(
                    tenant=row.name, detection_types=detection_types, paired=paired,
                    response_layer=getattr(row, "response_layer", None),
                    read_at=getattr(row, "response_layer_read_at", None), now=now))
            if extra:
                result.warnings = [*result.warnings, *extra]
        except Exception:  # noqa: BLE001
            logger.warning("socfw advisory skipped run_id=%s", result.run_id, exc_info=True)
        return result

    # ------------------------------------------------------------------
    # refusal after the Run row exists
    # ------------------------------------------------------------------

    async def _refuse_after_seed(
        self,
        db: AsyncSession,
        run_id: str,
        *,
        error: str,
        error_code: str = "LAUNCH_FAILED",
        error_detail: Optional[dict[str, Any]] = None,
        http_status: Optional[int] = None,
    ) -> LaunchResult:
        """Refuse a launch whose Run row is already committed, leaving it TERMINAL.

        ``launch()`` creates the Run and seeds its Result rows before several
        checks that can still refuse. Returning from one of those without
        touching the row left it in ``pending`` — a NON-terminal state that
        means, everywhere else in this engine, *work still owed*. A refused
        launch owes nothing. It is finished, and the only honest record of it is
        a ``failed`` run carrying the reason.

        Left ``pending`` the row is also swept by :meth:`rehydrate` on the next
        restart and stamped as an orphan whose "queued task was lost" — a
        SimCore failure that never happened, written into a record a DC exports.

        The seeded Result rows are deliberately KEPT: they record what the
        scenario expected to detect, and a failed run with its expectations
        intact is evidence. They are stamped ``not_executed`` /
        ``LAUNCH_REFUSED`` (OPS-01): nothing ran, so no alert can ever be
        credited to them and they leave every efficacy denominator.

        Refusals that fire BEFORE the row is committed (the launch consent gate)
        must keep creating no run at all — a ``failed`` run would read as "we
        tried", and an unauthorized launch never did.
        """
        from models import Run  # noqa: PLC0415

        run: Optional[Run] = (await db.execute(
            select(Run).where(Run.run_id == run_id)
        )).scalar_one_or_none()
        if run is not None:
            run.status = "failed"
            run.completed_at = datetime.utcnow()
            run.output = (run.output or "") + (
                f"\n--- LAUNCH REFUSED — {error} ---\n"
            )
            await stamp_not_executed(db, run_id, REASON_LAUNCH_REFUSED, commit=False)
            await db.commit()
            await _publish_run_status(run_id, "failed")

        logger.warning(
            "Launch refused after seeding run_id=%s reason=%s", run_id, error
        )
        return LaunchResult(
            success=False,
            run_id=run_id,
            error=error,
            error_code=error_code,
            error_detail=error_detail or {},
            http_status=http_status,
        )

    # ------------------------------------------------------------------
    # pull path
    # ------------------------------------------------------------------

    async def _dispatch_eal_in_process(
        self,
        run_id: str,
        scenario: Any,
        eal_steps: list[dict[str, Any]],
        binding: Any,
        executed_at: datetime,
        db: AsyncSession,
    ) -> dict[str, dict[str, Any]]:
        """Dispatch the run's EAL-channel steps IN-PROCESS (Phase 3b).

        Builds ONE Campaign from the eal steps — each step's authored
        ``eal.params`` merged with the stitch binding projected onto that
        plugin's own fields (the shared identity principal via ``canary_token``;
        see ``eal_simulator.stitch_params``) — and runs it synchronously through
        ``CampaignExecutor``. Defaults to **dry_run** (records pre-rendered,
        nothing POSTed): the safe baseline. Real delivery (``dry_run=false``) is
        a consent-gated follow-up, not this cut. Seeds each eal step's
        ``expected_detections`` as ``Result`` rows with a REAL ``executed_at``,
        and returns a per-step outcome for the ``channel_dispatch`` ledger. It
        NEVER fabricates an ingest or a CONFIRMED verdict (Gate A5).
        """
        from eal_simulator import get_default_registry  # noqa: PLC0415
        from eal_simulator.executor import CampaignExecutor  # noqa: PLC0415
        from eal_simulator.campaign import Campaign, CampaignStep  # noqa: PLC0415
        from eal_simulator.stitch_params import (  # noqa: PLC0415
            stitch_binding_to_eal_params,
        )
        from models import Result  # noqa: PLC0415

        registry = get_default_registry()
        outcomes: dict[str, dict[str, Any]] = {}
        campaign_steps: list[CampaignStep] = []
        for s in eal_steps:
            step_id = s.get("id")
            eal = s.get("eal") or {}
            plugin_name = eal.get("plugin")
            try:
                plugin_cls = registry.get(plugin_name) if plugin_name else None
            except KeyError:
                plugin_cls = None
            if plugin_cls is None:
                outcomes[step_id] = {
                    "status": "error", "code": "PLUGIN_MISCONFIGURED",
                    "detail": f"plugin '{plugin_name}' is not registered",
                }
                continue
            injected = stitch_binding_to_eal_params(binding, plugin_cls)
            params = {**(eal.get("params") or {}), **injected}
            try:
                campaign_steps.append(CampaignStep(
                    step_id=step_id, plugin=plugin_name, params=params,
                ))
            except Exception as exc:  # bad step_id shape etc. — honest per-step error
                outcomes[step_id] = {
                    "status": "error", "code": "EAL_STEP_INVALID", "detail": str(exc),
                }

        resolutions: dict[str, Any] = {}
        if campaign_steps:
            try:
                # Collector steps take their destination from the instance's
                # collector targets, like every other in-process consumer.
                # Never with keys: this path is dry-run only.
                from eal_simulator.targets import apply_targets  # noqa: PLC0415
                from engine.collector_targets import load_specs  # noqa: PLC0415

                resolved, res = apply_targets(
                    {"steps": [cs.model_dump() for cs in campaign_steps], "target_allowlist": []},
                    registry, await load_specs(db),
                )
                campaign_steps = [CampaignStep(**rs) for rs in resolved["steps"]]
                resolutions = {r.step_id: r for r in res}
                campaign = Campaign(
                    # Ephemeral, not persisted; run_id (passed to execute) is the
                    # real disambiguator. campaign_id must match CMP-{LABEL}-{NNN}.
                    campaign_id="CMP-COMPOSER-001",
                    name=f"composer eal dispatch {run_id[:8]}",
                    steps=campaign_steps,
                    dry_run=True,  # safe baseline; real delivery is consent-gated (follow-up)
                )
                executor = CampaignExecutor(registry=registry)
                state = await executor.execute(campaign, run_id=run_id)
            except Exception as exc:  # a dispatch fault must never crash the launch
                logger.exception("EAL in-process dispatch faulted run_id=%s", run_id)
                for cs in campaign_steps:
                    outcomes[cs.step_id] = {
                        "status": "error", "code": "EAL_DISPATCH_FAULT", "detail": str(exc),
                    }
            else:
                by_step = {r.step_id: r for r in state.step_results}
                for cs in campaign_steps:
                    r = by_step.get(cs.step_id)
                    if r is None:
                        outcomes[cs.step_id] = {
                            "status": "error", "code": "EAL_NO_RESULT",
                            "detail": "executor returned no result for this step",
                        }
                        continue
                    if r.status == "success":
                        status = "dry_run" if state.dry_run else "dispatched"
                        detail = (
                            "dry-run: records pre-rendered, not delivered"
                            if state.dry_run
                            else f"{r.events_emitted} record(s) delivered"
                        )
                    elif r.status == "skipped":
                        status, detail = "skipped", "step skipped"
                    else:
                        status, detail = "not_delivered", str(r.detail or "delivery failed")
                    outcomes[cs.step_id] = {
                        "status": status, "code": None, "detail": detail,
                    }
            for step_id, r in resolutions.items():
                if step_id in outcomes:
                    outcomes[step_id]["collector_target"] = r.target
                    outcomes[step_id]["collector_source"] = r.source

        # Seed a Result row per expected detection for the eal steps, with a REAL
        # executed_at (the seeding 3a deferred): the step actually ran here.
        for s in eal_steps:
            step_id = s.get("id")
            step_name = s.get("name", "")
            step_technique = s.get("mitre_technique")
            for det in s.get("expected_detections", []):
                db.add(Result(
                    run_id=run_id,
                    step_id=step_id,
                    step_name=step_name,
                    plane=det.get("plane", scenario.plane),
                    signal_type=det.get("type", "Analytics"),
                    expected_detection=det.get("description", ""),
                    observed=False,
                    executed_at=executed_at,
                    ttp_ref=det.get("ttp_ref"),
                    detection_id=det.get("detection_id"),
                    mitre_technique=step_technique,
                    verification_xql=det.get("verification_xql"),
                    kpi_contribution=det.get("kpi_contribution"),
                    kpi_verdict="pending" if det.get("verification_xql") else None,
                ))
        await db.commit()
        return outcomes

    async def _resolve_composed_partitions(
        self,
        scenario: Any,
        launch_target: str,
        db: AsyncSession,
        output_markers: list[str],
        warnings: list[str],
    ) -> tuple[list[dict[str, Any]], Optional[LaunchResult]]:
        """Platform-resolve a composed run, one agent partition at a time.

        Returns ``(steps, refusal)``. ``steps`` keeps the authored order; agent
        steps carry their partition agent's resolution, EAL steps (which run
        in SimCore's own process) are untouched. ``refusal`` is a
        ``PULL_TARGET_*`` LaunchResult when any partition cannot run on its
        agent — all-or-nothing, the same posture as the enrollment check.

        A partition whose agent is NOT enrolled is left unresolved and the raw
        steps are returned: :meth:`_handle_pull`'s existing check refuses that
        launch as ``TARGET_AGENT_NOT_ENROLLED``, which stays the first answer an
        operator gets for a phantom endpoint.
        """
        from models import Agent  # noqa: PLC0415

        raw = list(getattr(scenario, "steps", None) or [])
        partitions, _eal = _partition_channels(raw, launch_target)
        rows: dict[str, Any] = {}
        for tgt in partitions:
            rows[tgt] = (await db.execute(
                select(Agent).where(Agent.agent_id == tgt)
            )).scalar_one_or_none()
        if any(row is None for row in rows.values()):
            return raw, None

        replaced: dict[int, dict[str, Any]] = {}
        now = datetime.utcnow()
        for tgt, part in partitions.items():
            row = rows[tgt]
            try:
                target, os_warning = platform_target_for_os(row.os)
            except AgentOsUnrecognised:
                return raw, _os_refusal(tgt, row.os)
            dispatched, markers, unresolved = resolve_steps_for_target(part, target)
            if unresolved:
                return raw, _unsatisfiable_refusal(
                    target=target, agent_id=tgt, agent_os=row.os,
                    unresolved=unresolved,
                )
            if os_warning:
                output_markers.append(f"{os_warning} (agent '{tgt}')")
            output_markers.extend(markers)
            output_markers.extend(posix_family_markers(part, row.os, tgt))
            if tgt != launch_target:  # launch() already warned for its own target
                offline = _offline_warning(row, now)
                if offline:
                    warnings.append(offline)
            for original, new in zip(part, dispatched):
                replaced[id(original)] = new
        return [replaced.get(id(s), s) for s in raw], None

    async def _handle_pull(
        self,
        run_id: str,
        scenario: Any,
        target_agent_id: Optional[str],
        identity: Optional[str],
        db: AsyncSession,
        runtime_install_authorized: bool = False,
        pull_plan: Optional[_PullPlan] = None,
        launch_warnings: Optional[list[str]] = None,
        allow_missing_tools: bool = False,
    ) -> LaunchResult:
        """``launch_warnings`` are launch()'s own reservations (an offline
        target). They are written onto Run.output with the dispatch markers; the
        caller still returns them in ``warnings``, so they are not added here.

        ``allow_missing_tools`` turns off ONLY the container-agent gate's tool
        refusal (``AGENT_TOOL_ABSENT``); a posture gap is never overridable."""
        from models import Agent, Run  # noqa: PLC0415

        if not target_agent_id:
            return await self._refuse_after_seed(
                db, run_id,
                error="target_agent_id is required for pull mode",
            )
        output_markers: list[str] = list(pull_plan.output_markers) if pull_plan else []

        execution_identity = getattr(scenario, "execution_identity", None) or {}

        # THE link the whole payload shelf turns on: the console stages a public
        # tool, this resolves a digest-bound plan, and the beacon fetches and
        # verifies it before any step runs. Without this call every other piece
        # is built and unreachable — the task carries no artifacts, the beacon
        # stages nothing, and a scenario wired to a shelf artifact runs its steps
        # with no tooling. The absent detection then reads in the POV report as
        # "Cortex missed it", which is a false negative manufactured by us and
        # shown to a customer.
        from engine.payload_shelf import PayloadResolutionError  # noqa: PLC0415

        try:
            artifacts = _compose_artifacts(scenario)
        except PayloadResolutionError as exc:
            # Refuse at LAUNCH, not on the target. A run that cannot be tooled
            # must never reach a customer endpoint half-armed, and the operator
            # is still at the console to read why.
            # Forward the shelf's OWN code, structured payload and status.
            # Rebuilding the refusal by hand collapsed all of it: the operator
            # got a flat 422 saying their launch body was malformed, when the
            # body was fine and the fix is ./scripts/build-payloads.sh on this
            # host. `to_error()` is the exact envelope the shelf route returns,
            # so a console parses both refusals with one code path.
            return await self._refuse_after_seed(
                db, run_id,
                error=f"{exc.code}: {exc.detail}",
                error_code=exc.code,
                error_detail=exc.to_error(),
                http_status=exc.http_status,
            )

        # Phase-2 Composer Stitch Context. When the scenario carries an authored
        # `stitch_context`, resolve it to the run's concrete shared entities and
        # substitute {stitch:*} into every step's command AFTER the adapter pass,
        # so each step (and, in Phase 3, each channel) emits the SAME 5-tuple /
        # principal / host / container / cloud resource and the signals correlate.
        # The launch target (the Agent row, exposing `.hostname`) feeds `from_agent`.
        # A scenario with NO stitch_context yields `binding = None` and every
        # {stitch:*} is left verbatim — byte-identical to today. `seed=run_id` makes
        # the binding deterministic and reproducible, never random.
        from engine.stitch_context import (  # noqa: PLC0415
            StitchContextValidationError,
            resolve_stitch_context,
        )

        stitch_spec = getattr(scenario, "stitch_context", None)
        target_agent: Optional[Agent] = None
        if stitch_spec:
            agent_result = await db.execute(
                select(Agent).where(Agent.agent_id == target_agent_id)
            )
            target_agent = agent_result.scalar_one_or_none()
        try:
            binding = resolve_stitch_context(
                stitch_spec, seed=run_id, target=target_agent
            )
        except StitchContextValidationError as exc:
            # A persisted spec that no longer validates must refuse at LAUNCH,
            # not inject a half-resolved binding onto a customer endpoint. The
            # operator is still at the console to read why (fail-closed, Gate A5).
            return await self._refuse_after_seed(
                db, run_id,
                error=f"STITCH_CONTEXT_INVALID: {exc}",
            )

        egress_warnings = _egress_warnings(scenario)

        # ── Platform gate (BEACON-01) ───────────────────────────────────────
        # Choose, per step, the command text the target OS can actually run —
        # BEFORE {adapter:*} / {stitch:*} rendering, on the same raw document
        # the push generator judges. The single-endpoint plan was decided (and
        # refusals issued) before the Run existed; a composed run is resolved
        # here, per target agent, once every target is known to be enrolled.
        dispatch_warnings: list[str] = []
        if pull_plan is not None:
            raw_steps = pull_plan.steps
        else:
            raw_steps, refusal = await self._resolve_composed_partitions(
                scenario, target_agent_id, db, output_markers, dispatch_warnings,
            )
            if refusal is not None:
                return await self._refuse_after_seed(
                    db, run_id,
                    error=refusal.error,
                    error_code=refusal.error_code,
                    error_detail=refusal.error_detail,
                    http_status=refusal.http_status,
                )

        adapter_steps, required_binaries = _resolve_adapter_placeholders_with_binaries(
            raw_steps
        )
        steps = _resolve_stitch_placeholders(adapter_steps, binding)
        # The beacon refuses a step whose binary is not on PATH (not_run /
        # TOOL_ABSENT); this is the server half that fills the list. Written
        # only when non-empty, like `platforms` (the beacon field is omitempty).
        for s_ in steps:
            req = required_binaries.get(s_.get("id"))
            if req:
                s_["required_binaries"] = list(req)

        # Container-agent launch gate (engine/agent_launch_gate.py). A pod has one
        # fixed posture and tool roster; refuse HERE rather than let a step fail
        # for lack of privilege/tooling and read as a Cortex miss. Every agent
        # target of a composed run is gated on its own facts. A host / VM /
        # legacy agent, or a container beacon the manifest did not deploy (no
        # posture profile), is a no-op.
        gate_warnings = await self._container_agent_gate(
            db, run_id, scenario, steps, target_agent_id, allow_missing_tools
        )
        if isinstance(gate_warnings, LaunchResult):
            return gate_warnings

        # ── Channel dispatch (Phase 3a skeleton) ────────────────────────────
        # A run is "multichannel" the moment ANY step carries a non-agent
        # channel or a per-step target. A run with none — the whole shipped
        # corpus and every Phase-1/2 draft — takes the ORIGINAL single-Task path
        # below and Run.channel_dispatch stays NULL: byte-identical to today,
        # proven by test. ``effective_channel`` is the ONE absent-⇒-'agent' rule
        # (shared with the loader) so dispatch and persistence cannot disagree.
        from engine.scenario_loader import effective_channel  # noqa: PLC0415

        is_multichannel = any(
            effective_channel(s) != "agent" or s.get("target") for s in steps
        )

        channel_dispatch: Optional[list[dict[str, Any]]] = None
        eal_outcomes: dict[str, dict[str, Any]] = {}
        now = datetime.utcnow()
        # Tasks are persisted as they are built but put on the in-memory queue —
        # which is what wakes a long-polling beacon — only AFTER the run's
        # `running` / `open_tasks` commit below. Enqueuing first let a fast
        # beacon collect and /complete the run inside the launch window (a
        # multichannel launch dispatches its EAL steps in between), after which
        # the launch wrote `running` over the finished run and it never
        # terminalised again.
        to_enqueue: list[tuple[str, Task]] = []

        if not is_multichannel:
            task = Task(
                task_id=str(uuid.uuid4()),
                run_id=run_id,
                scenario_id=scenario.scenario_id,
                steps=steps,
                identity_context=identity,
                identity_default=execution_identity.get("default"),
                cgo_anchor=getattr(scenario, "cgo_anchor", None),
                artifacts=artifacts,
                runtime_install_authorized=runtime_install_authorized,
            )
            # Mirror the task to the durable queue so a restart can rehydrate it.
            await self._persist_task(db, target_agent_id, task)
            to_enqueue.append((target_agent_id, task))
            queued_message = f"Task queued for agent '{target_agent_id}'"
            n_tasks = 1
        else:
            # Partition agent-channel steps by their EFFECTIVE target (the step's
            # own `target` — the second-endpoint case — or the launch target).
            # EAL steps are set aside for a pending marker: recognised and
            # validated, but NEVER dispatched in 3a (that is 3b).
            agent_partitions, eal_steps = _partition_channels(steps, target_agent_id)

            # Phase 3b: an all-EAL run IS dispatchable — its EAL steps run
            # in-process below and the run terminalises at launch (there is no
            # beacon task to complete it). n_tasks counts only beacon tasks, so
            # it is 0 for an all-EAL run.
            n_tasks = len(agent_partitions)

            # All-or-nothing: verify every distinct target endpoint is enrolled
            # BEFORE enqueuing anything. A step that names a phantom second
            # endpoint is refused honestly (TARGET_AGENT_NOT_ENROLLED), never a
            # silent no-op or a task nothing will ever collect (Gate A5).
            missing: list[str] = []
            for tgt in agent_partitions:
                row = (await db.execute(
                    select(Agent).where(Agent.agent_id == tgt)
                )).scalar_one_or_none()
                if row is None:
                    missing.append(tgt)
            if missing:
                referencing = {
                    tgt: [s.get("id") for s in agent_partitions[tgt]]
                    for tgt in missing
                }
                logger.warning(
                    "Launch refused run_id=%s code=TARGET_AGENT_NOT_ENROLLED missing=%s",
                    run_id, sorted(missing),
                )
                # Terminate the seeded run so a refused multichannel launch
                # does not leave an orphaned run stuck at its initial state.
                return await self._refuse_after_seed(
                    db, run_id,
                    error=(
                        "TARGET_AGENT_NOT_ENROLLED: composed step target(s) "
                        f"{sorted(missing)} are not enrolled — enroll them or "
                        "retarget the step(s) before launch"
                    ),
                    error_code="TARGET_AGENT_NOT_ENROLLED",
                    error_detail={
                        "missing_agents": sorted(missing),
                        "referencing_steps": referencing,
                    },
                )

            # Enqueue ONE Task per distinct target agent, each carrying only its
            # own steps and the artifacts those steps need. Every endpoint shares
            # the ONE resolved stitch binding (seed=run_id, above) so all channels
            # plant identical entities.
            for tgt, tgt_steps in agent_partitions.items():
                step_ids = {s.get("id") for s in tgt_steps}
                tgt_artifacts = [
                    a for a in artifacts
                    if not a.get("steps") or (set(a.get("steps") or []) & step_ids)
                ]
                task = Task(
                    task_id=str(uuid.uuid4()),
                    run_id=run_id,
                    scenario_id=scenario.scenario_id,
                    steps=tgt_steps,
                    identity_context=identity,
                    identity_default=execution_identity.get("default"),
                    cgo_anchor=getattr(scenario, "cgo_anchor", None),
                    artifacts=tgt_artifacts,
                    runtime_install_authorized=runtime_install_authorized,
                )
                await self._persist_task(db, tgt, task)
                to_enqueue.append((tgt, task))

            # Phase 3b: dispatch the EAL steps IN-PROCESS (synchronous),
            # injecting the shared identity principal, and seed their results.
            # No beacon is involved — the executor runs the campaign here.
            if eal_steps:
                eal_outcomes = await self._dispatch_eal_in_process(
                    run_id, scenario, eal_steps, binding, now, db
                )

            # The honest routing ledger: agent steps enqueued to their endpoint,
            # eal steps carrying their real dispatch outcome (dispatched /
            # dry_run / not_delivered), never a fabrication.
            channel_dispatch = _build_channel_dispatch(
                steps, target_agent_id, eal_outcomes=eal_outcomes
            )
            n_eal = len(eal_steps)
            queued_message = (
                f"Multi-channel run: {len(steps) - n_eal} agent step(s) queued "
                f"across {len(agent_partitions)} endpoint(s); {n_eal} eal step(s) "
                "dispatched in-process"
            )

        # Update run status (shared by both paths).
        run_result = await db.execute(
            select(Run).where(Run.run_id == run_id)
        )
        run: Optional[Run] = run_result.scalar_one_or_none()
        if run:
            if n_tasks == 0:
                # All-EAL run: the EAL steps ran in-process synchronously above,
                # and there is no beacon task to complete the run — so it is
                # terminal at launch. Failed only if EVERY eal step failed to run
                # (misconfigured / plugin missing); a dry-run dispatch is a real
                # dispatch and terminates 'complete'.
                all_failed = bool(eal_outcomes) and all(
                    o.get("status") == "error" for o in eal_outcomes.values()
                )
                run.status = "failed" if all_failed else "complete"
                run.completed_at = now
                run.open_tasks = 0
            else:
                run.status = "running"
                # How many beacon tasks must report before the run is terminal —
                # 1 for a single endpoint, N for a multi-endpoint fan-out. EAL
                # steps ran in-process and are NOT counted here.
                run.open_tasks = n_tasks
            # The per-run home for the RESOLVED values actually injected into this
            # run's step commands — the real 5-tuple / UPN / host / CI / cloud
            # resource, nothing invented (Gate A5). NULL for a context-less
            # scenario. The report and Run lens quote this to show what executed.
            run.stitch_binding = binding.values if binding else None
            # NULL for a single-channel run (byte-identical); the routing ledger
            # for a multichannel one.
            run.channel_dispatch = channel_dispatch
            await db.commit()

        # Surface the egress warning on the run itself, before any beacon can
        # write: the console shows Run.output, a log line is not seen by a DC.
        # The platform markers (CONT-09: a step running a variant whose
        # expectations were authored against the primary; an agent that never
        # said which OS it is) go on the same record, for the same reason.
        # An offline target (launch()'s own, and a composed run's other
        # endpoints) goes on the record too (RG-RUNS-06): the launch response
        # reaches only the tab that launched, not another browser or the report.
        # So do the container-agent gate's advisories, rendered as one line each.
        reservations = (
            list(launch_warnings or []) + dispatch_warnings
            + [_gate_advisory_line(w) for w in gate_warnings]
        )
        if egress_warnings or output_markers or reservations:
            lines = "".join(
                f"!! TARGET_EGRESS_REQUIRED: {aid} — {text}\n"
                for aid, text in egress_warnings
            ) + "".join(f"{m}\n" for m in output_markers) + "".join(
                f"!! {w}\n" for w in reservations)
            await db.execute(
                update(Run)
                .where(Run.run_id == run_id)
                .values(output=func.coalesce(Run.output, "") + lines)
            )
            await db.commit()

        # Only now is the run's state durable enough for a beacon to act on.
        for agent_id, task in to_enqueue:
            self._enqueue(agent_id, task)

        if run:
            await _publish_run_status(run_id, run.status)

        logger.info(
            "Task(s) enqueued run_id=%s target=%s multichannel=%s",
            run_id, target_agent_id, is_multichannel,
        )
        return LaunchResult(
            success=True,
            run_id=run_id,
            mode="pull",
            message=queued_message,
            # The platform/offline reservations (strings), then the
            # container-agent gate's advisory notices (dicts); all of them
            # are on Run.output too.
            warnings=dispatch_warnings + output_markers + list(gate_warnings),
        )

    async def _container_agent_gate(
        self,
        db: AsyncSession,
        run_id: str,
        scenario: Any,
        steps: list[dict[str, Any]],
        target_agent_id: str,
        allow_missing_tools: bool,
    ) -> "list[dict[str, Any]] | LaunchResult":
        """Run the container-agent gate for every agent target of the run.

        Thin: loads each target's ``runtime_facts`` and hands off to
        :func:`agent_launch_gate.evaluate_launch`, which judges each target on its
        own facts and steps and stamps ``required_binaries``. Returns the advisory
        warnings, or a refused ``LaunchResult`` (run row already ``failed``).
        """
        from models import Agent  # noqa: PLC0415
        from engine import agent_launch_gate as gate  # noqa: PLC0415

        targets = gate.agent_step_targets(steps, target_agent_id)
        facts_by_agent: dict[str, Any] = {}
        for agent_id in targets:
            agent = (await db.execute(
                select(Agent).where(Agent.agent_id == agent_id)
            )).scalar_one_or_none()
            facts_by_agent[agent_id] = getattr(agent, "runtime_facts", None) if agent else None
        if not any(gate.is_gated(f) for f in facts_by_agent.values()):
            return []

        posture = gate.scenario_view(scenario, [])["cluster_posture"]
        result = gate.evaluate_launch(
            posture, steps, target_agent_id, facts_by_agent,
            allow_missing_tools=allow_missing_tools,
        )
        if result.refusal is not None:
            r = result.refusal
            return await self._refuse_after_seed(
                db, run_id,
                error=f"{r.code}: {r.error}",
                error_code=r.code,
                error_detail=r.detail,
                http_status=409,
            )
        return result.warnings

    # ------------------------------------------------------------------
    # push path
    # ------------------------------------------------------------------

    async def _handle_push(
        self, run_id: str, scenario: Any, db: AsyncSession
    ) -> LaunchResult:
        # Generate a download URL — the actual content is produced on demand
        # by the /api/scenarios/{id}/download endpoint.
        #
        # GAP-API-004 / GAP-PUSH-001 — a push bundle never phones home, so the
        # run previously orphaned at 'pending' forever. We advance it to the
        # terminal 'staged' state on bundle generation: the work product (the
        # self-contained bundle) is ready and SimCore has no further role. A DC
        # who later runs the bundle reads detections directly in XSIAM.
        from models import Run  # noqa: PLC0415

        download_base = f"/api/scenarios/{scenario.scenario_id}/download"
        run_result = await db.execute(select(Run).where(Run.run_id == run_id))
        run: Optional[Run] = run_result.scalar_one_or_none()
        if run:
            run.status = "staged"
            run.completed_at = datetime.utcnow()
            run.output = (run.output or "") + (
                "\n--- PUSH BUNDLE STAGED ---\n"
                f"Bundle downloadable at {download_base}. Execute on an "
                "authorized target; review detections in the Cortex console.\n"
            )
            await db.commit()
            await _publish_run_status(run_id, "staged")
        logger.info("Push bundle staged run_id=%s scenario=%s", run_id, scenario.scenario_id)
        return LaunchResult(
            success=True,
            run_id=run_id,
            mode="push",
            message="Push bundle staged for download",
            download_url=download_base,
        )

    # ------------------------------------------------------------------
    # Result seeding — auto-create Result rows from scenario steps
    # ------------------------------------------------------------------

    async def _seed_results(
        self,
        run_id: str,
        scenario: Any,
        executed_at: datetime,
        db: AsyncSession,
    ) -> None:
        """
        Create one Result row per expected_detection across all scenario steps.
        Sets executed_at so MTTD can be calculated when the DC marks observed_at.

        Phase 1: when a detection carries ``ttp_ref`` / ``detection_id``,
        copy the resolved card's BIOC / XQL / correlation logic onto the
        Result row so the POV report can render it inline.
        """
        from models import Result  # noqa: PLC0415
        from engine.ttp_catalog import catalog  # noqa: PLC0415
        from engine.scenario_loader import effective_channel  # noqa: PLC0415

        steps = scenario.steps or []
        count = 0
        enriched = 0
        for step in steps:
            # An EAL-channel step is NOT dispatched in Phase 3a
            # (EAL_DISPATCH_PENDING), so seeding a Result row with
            # executed_at=now would falsely claim it ran. Skip it — 3b seeds
            # the EAL detections when it actually dispatches the campaign.
            if effective_channel(step) == "eal":
                continue
            step_id = step.get("id", "unknown")
            step_name = step.get("name", "")
            step_technique = step.get("mitre_technique")
            for detection in step.get("expected_detections", []):
                ttp_ref = detection.get("ttp_ref")
                detection_id = detection.get("detection_id")
                card = catalog.find(ttp_ref, detection_id) if ttp_ref else None

                result = Result(
                    run_id=run_id,
                    step_id=step_id,
                    step_name=step_name,
                    plane=detection.get("plane", scenario.plane),
                    signal_type=detection.get("type", "BIOC"),
                    expected_detection=detection.get("description", ""),
                    observed=False,
                    executed_at=executed_at,
                    ttp_ref=ttp_ref,
                    detection_id=detection_id,
                    mitre_technique=step_technique,
                    # Phase 2 — carry the verification contract onto the row so
                    # verifier.py can score it without re-reading the YAML.
                    # `pending` (not None) marks a row the verifier owns; rows
                    # with no verification_xql stay unscoreable by design.
                    verification_xql=detection.get("verification_xql"),
                    kpi_contribution=detection.get("kpi_contribution"),
                    kpi_verdict="pending" if detection.get("verification_xql") else None,
                    expected_outcome=detection.get("outcome", "detect"),
                )
                if card is not None:
                    if card.kind == "prevention":
                        result.prevention_module = card.module
                    result.detection_kind = card.kind
                    result.detection_logic = card.logic
                    result.detection_severity = card.severity
                    result.detection_name = card.name
                    # Surface the card's MITRE technique back into the
                    # Result row when it is more specific than the step's.
                    if not result.mitre_technique and card.mitre_techniques:
                        result.mitre_technique = card.mitre_techniques[0]
                    enriched += 1
                db.add(result)
                count += 1

        await db.commit()
        logger.info(
            "Seeded %d expected detection results for run_id=%s (%d enriched from TTP catalog)",
            count, run_id, enriched,
        )

    # ------------------------------------------------------------------
    # Queue management
    # ------------------------------------------------------------------

    def _enqueue(self, agent_id: str, task: Task) -> None:
        if agent_id not in self._queue:
            self._queue[agent_id] = []
        self._queue[agent_id].append(task)
        self._wake_current_waiter(agent_id)

    def _wake_current_waiter(self, agent_id: str) -> None:
        """Wake the newest non-superseded long-poll for ``agent_id``, if any.

        Only that one: a superseded waiter belongs to a connection its beacon
        has since replaced, and waking it hands the task to a closed socket.
        A waiter whose loop is gone is dropped instead of raising.
        """
        waiters = self._waiters.get(agent_id)
        if not waiters:
            return
        try:
            running: Optional[asyncio.AbstractEventLoop] = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        for waiter in reversed(list(waiters)):
            if waiter.superseded:
                continue
            if waiter.loop.is_closed():
                self._forget_waiter(agent_id, waiter)
                continue
            if running is waiter.loop:
                waiter.event.set()
            else:
                # Enqueued from outside the waiter's loop (or from no loop at
                # all): hand the wakeup to the owning loop rather than touching
                # its futures from here.
                try:
                    waiter.loop.call_soon_threadsafe(waiter.event.set)
                except RuntimeError:  # closed between the check and the call
                    self._forget_waiter(agent_id, waiter)
                    continue
            return

    def _forget_waiter(self, agent_id: str, waiter: "_TaskWaiter") -> None:
        waiters = self._waiters.get(agent_id)
        if not waiters:
            return
        try:
            waiters.remove(waiter)
        except ValueError:
            pass
        if not waiters:
            self._waiters.pop(agent_id, None)

    async def wait_for_task(self, agent_id: str, timeout: float) -> bool:
        """Wait up to ``timeout`` seconds for a task to be enqueued for ``agent_id``.

        Returns ``True`` when this waiter is still the agent's CURRENT poll and
        ``False`` when a newer poll for the same agent superseded it while it
        waited (OPS-09). A beacon holds one long-poll at a time, so a second
        one means the first belongs to a connection the beacon has abandoned —
        a restart inside the wait window. The caller should not dequeue for a
        superseded waiter; if it does anyway, it finds the queue as the current
        waiter left it, because an enqueue never wakes a superseded one.

        A superseded waiter is NOT woken early: it runs out its own timeout.
        Waking it would make two beacons that share an agent id spin against
        each other.
        """
        loop = asyncio.get_running_loop()
        waiter = _TaskWaiter(loop=loop, event=asyncio.Event())
        waiters = self._waiters.setdefault(agent_id, [])
        for older in waiters:
            older.superseded = True
        waiters.append(waiter)
        try:
            if not self._queue.get(agent_id):
                try:
                    await asyncio.wait_for(waiter.event.wait(), timeout=timeout)
                except asyncio.TimeoutError:
                    pass
            return not waiter.superseded
        finally:
            self._forget_waiter(agent_id, waiter)

    def dequeue(self, agent_id: str) -> Optional[Task]:
        """Pop the next deliverable task for an agent from the in-memory queue.

        Skips (and discards) any queued task whose run was aborted — defence
        in depth in case an abort raced an in-flight enqueue.

        NOTE: this only touches the in-memory cache. Prefer
        :meth:`dequeue_for_agent` (DB-aware) on the request path so the durable
        ``queued_tasks`` row is removed too.
        """
        queue = self._queue.get(agent_id, [])
        while queue:
            task = queue.pop(0)
            if task.run_id in self._aborted:
                logger.info(
                    "dequeue skipping aborted task task_id=%s run_id=%s",
                    task.task_id, task.run_id,
                )
                continue
            return task
        return None

    async def dequeue_for_agent(
        self, agent_id: str, db: AsyncSession
    ) -> Optional[Task]:
        """DB-aware dequeue: pop the next deliverable task AND delete its
        durable ``queued_tasks`` row so it is not re-delivered after a restart.

        The run is stamped ``delivered_at`` in the same commit. Deleting the
        row erases the only other evidence that the task reached a beacon, and
        :meth:`rehydrate` needs that evidence to leave an executing run alone.

        Every poll first reaps this agent's ABANDONED runs (OPS-03): a beacon
        asking for new work is alive, and if it has stopped polling a delivered
        run's control channel it is no longer executing that run. This is the
        only periodic signal that fires for such an agent — its fresh
        ``last_seen`` keeps the offline-agent beacon-lost sweep away.

        The poll is recorded as idle evidence for this agent BEFORE the reap,
        and the delivery it causes AFTER, so the collecting poll is never
        mistaken for "has let go of the task it just collected".
        """
        self.note_task_poll(agent_id)
        await self.reap_abandoned(agent_id, db)
        task = self.dequeue(agent_id)
        if task is not None:
            self.note_delivery(task.run_id, agent_id)
            from models import QueuedTask, Run  # noqa: PLC0415
            from sqlalchemy import delete, update  # noqa: PLC0415

            await db.execute(delete(QueuedTask).where(QueuedTask.task_id == task.task_id))
            await db.execute(
                update(Run)
                .where(Run.run_id == task.run_id, Run.delivered_at.is_(None))
                .values(delivered_at=datetime.utcnow())
            )
            await db.commit()
        return task

    def peek_queue(self, agent_id: str) -> list[Task]:
        """Return all pending tasks for an agent without removing them."""
        return list(self._queue.get(agent_id, []))

    # ------------------------------------------------------------------
    # Abort support
    # ------------------------------------------------------------------

    def abort(self, run_id: str) -> None:
        """Mark a run aborted: record the id (so the agent's /control poll
        returns ``abort=true``) and drop any queued-but-undelivered task for
        the run from every agent queue (in-memory only).

        Prefer :meth:`abort_persisted` on the request path so the durable
        ``queued_tasks`` rows are also removed.
        """
        self._aborted.add(run_id)
        for agent_id, tasks in list(self._queue.items()):
            self._queue[agent_id] = [t for t in tasks if t.run_id != run_id]
        logger.info("run aborted run_id=%s", run_id)

    async def abort_persisted(self, run_id: str, db: AsyncSession) -> None:
        """DB-aware abort: drop in-memory tasks for the run AND delete its
        durable ``queued_tasks`` rows so a restart never re-delivers them."""
        self.abort(run_id)
        from models import QueuedTask  # noqa: PLC0415
        from sqlalchemy import delete  # noqa: PLC0415

        await db.execute(delete(QueuedTask).where(QueuedTask.run_id == run_id))
        await db.commit()

    def is_aborted(self, run_id: str) -> bool:
        """True if ``run_id`` was aborted via :meth:`abort` this process."""
        return run_id in self._aborted

    def clear_aborted(self, run_id: str) -> None:
        """Drop a run from the aborted set once it reaches a terminal state,
        so the in-memory set doesn't grow without bound. Idempotent."""
        self._aborted.discard(run_id)

    # ------------------------------------------------------------------
    # Durable-queue persistence (GAP-API-005)
    # ------------------------------------------------------------------

    async def _persist_task(
        self, db: AsyncSession, agent_id: str, task: Task
    ) -> None:
        """Mirror an enqueued Task to the ``queued_tasks`` table."""
        from models import QueuedTask  # noqa: PLC0415

        db.add(QueuedTask(
            task_id=task.task_id,
            run_id=task.run_id,
            agent_id=agent_id,
            scenario_id=task.scenario_id,
            payload=task.to_dict(),
            created_at=task.created_at,
        ))
        await db.commit()

    async def rehydrate(self, db: AsyncSession) -> dict[str, int]:
        """Rebuild the in-memory queue from the durable ``queued_tasks`` table
        and reconcile orphaned ``running`` runs. Called once from the FastAPI
        lifespan on startup.

        Reconciliation rules:
          * Every persisted task is re-loaded into the in-memory queue so a
            waiting agent receives it after the restart.
          * Any Run still in ``pending``/``running`` whose task did NOT survive
            in the durable queue AND was never delivered is marked ``failed``
            (its work was lost) so it does not hang forever. A note is appended
            to the run output, and its Results are stamped ``not_executed`` /
            ``TASK_LOST_ON_RESTART`` (OPS-01) so no later alert is credited to
            a run that never reached the target.
          * A run whose task WAS delivered (``delivered_at`` set) has no row by
            design — delivery deletes it — and is left ``running``: failing it
            made /control stop the beacon mid-step and put "the run never
            reached the target" in the POV report of a run that did.

            Leaving it running is NOT the same as "its own /complete
            terminalises it" (the claim this docstring used to make). The
            beacon may have POSTed its /complete into the restart window, and
            it does not wait for an answer the way a long-poll does; the run
            then never hears from it again. Its agent keeps polling /tasks, so
            ``last_seen`` stays fresh and the offline-agent beacon-lost sweep
            never fires either. Such a run is reaped by
            :func:`engine.beacon_lost.terminalise_abandoned_runs` (OPS-03): no
            ``/control`` poll for the run within
            ``CORTEXSIM_BEACON_LOST_AFTER_SECONDS`` of ``max(boot,
            delivered_at)`` while its agent is demonstrably alive, no task for
            it is still queued, and EVERY endpoint holding one of its tasks has
            polled ``/tasks`` since boot means no beacon is executing it. A
            multi-endpoint fan-out is not reaped while a second endpoint has
            yet to collect its task or has not been seen idle since.

        Returns counts ``{rehydrated, failed_orphans, in_flight}`` for logging.
        """
        # A fresh boot: no control poll, idle poll or delivery record survives a
        # restart, so the abandoned-run sweep measures silence from now, never
        # from before the process lived, and needs every endpoint to be seen
        # idle again before it reaps.
        self.started_at = datetime.utcnow()
        self.reset_liveness()
        from models import QueuedTask, Run  # noqa: PLC0415

        # 1. Re-load every durable task into the in-memory queue.
        result = await db.execute(select(QueuedTask).order_by(QueuedTask.created_at))
        rows = result.scalars().all()
        live_run_ids: set[str] = set()
        rehydrated = 0
        for row in rows:
            task = _task_from_payload(row.payload)
            if task is None:
                continue
            self._enqueue(row.agent_id, task)
            live_run_ids.add(task.run_id)
            rehydrated += 1

        # 2. Mark orphaned non-terminal runs as failed (their task is gone).
        run_result = await db.execute(
            select(Run).where(Run.status.in_(("pending", "running")))
        )
        failed_orphans = 0
        in_flight = 0
        now = datetime.utcnow()
        for run in run_result.scalars().all():
            if run.run_id in live_run_ids:
                continue
            if run.delivered_at is not None:
                in_flight += 1
                continue
            # Push-mode runs are not queue-backed; a push run advanced to a
            # terminal staged state below would not be 'pending' here, so any
            # pending/running pull run with no surviving task is a true orphan.
            run.status = "failed"
            run.completed_at = now
            run.output = (run.output or "") + (
                "\n--- RUN FAILED ON RESTART — queued task was lost "
                "(SimCore restarted before the agent picked it up) ---\n"
            )
            # A composed run's EAL steps ran in SimCore's own process at launch;
            # only the beacon partitions were lost.
            await stamp_not_executed(
                db, run.run_id, REASON_TASK_LOST_ON_RESTART, commit=False,
                exclude_steps=in_process_steps(run),
            )
            failed_orphans += 1
        if failed_orphans:
            await db.commit()

        logger.info(
            "orchestrator rehydrate: %d task(s) restored, %d orphaned run(s) failed, "
            "%d delivered run(s) left running on their beacon",
            rehydrated, failed_orphans, in_flight,
        )
        return {"rehydrated": rehydrated, "failed_orphans": failed_orphans,
                "in_flight": in_flight}


# ---------------------------------------------------------------------------
# Tool-adapter helpers (Phase A)
# ---------------------------------------------------------------------------


_ADAPTER_PLACEHOLDER_RE = __import__("re").compile(r"\{adapter:(TOOL-[A-Z0-9-]+)\}")

# Phase-2 Composer Stitch Context. Mirrors _ADAPTER_PLACEHOLDER_RE: a step command
# reads `curl --local-port {stitch:src_port} https://{stitch:dst_ip}/...` and the
# resolved binding substitutes the SAME concrete entities into every step so the
# channels correlate. KEY is one of the nine entity keys (lowercase snake); the
# grammar is deliberately loose (`[a-z_]+`) so an unknown key still MATCHES and is
# then left verbatim by _render_stitch — the agent surfaces the miss, exactly as an
# unresolved {adapter:*} does.
_STITCH_PLACEHOLDER_RE = __import__("re").compile(r"\{stitch:([a-z_]+)\}")


def _compose_artifacts(scenario: Any) -> list[dict[str, Any]]:
    """Resolve this scenario's staged tools onto the beacon's wire contract.

    Returns ``[]`` when the scenario references no shelf-backed adapter — the
    overwhelming majority of the corpus — so an ordinary run enqueues a task
    byte-identical to the one it enqueued before the shelf existed.

    Raises :class:`PayloadResolutionError` when a tool IS declared but cannot be
    resolved. That refusal is the point: the alternative is a task that runs the
    TTP without its tool.
    """
    from engine.payload_shelf import compose  # noqa: PLC0415 — import cycle

    plan = compose(
        scenario={
            "scenario_id": getattr(scenario, "scenario_id", None),
            "external_tools": getattr(scenario, "external_tools", None) or [],
            "cluster_posture": getattr(scenario, "cluster_posture", None) or {},
        },
        consumer="beacon",
    )
    for warning in plan.warnings:
        # WARNING, not INFO: TARGET_EGRESS_REQUIRED means a step may run on a
        # target that lacks its tool, which reads as "Cortex missed it".
        logger.warning(
            "payload plan %s (%s): %s",
            plan.composition_id, warning.get("code"), warning.get("detail"),
        )
    return plan.to_beacon_artifacts()


def _egress_warnings(scenario: Any) -> list[tuple[str, str]]:
    """``(adapter_id, text)`` for every referenced adapter the target must
    already carry (the shelf has nothing staged for it).

    A separate call from :func:`_compose_artifacts` so that function keeps its
    one-argument contract. Only reached after ``_compose_artifacts`` succeeded,
    so ``compose`` cannot raise a resolution error here.
    """
    from engine.payload_shelf import compose  # noqa: PLC0415 — import cycle

    plan = compose(
        scenario={
            "scenario_id": getattr(scenario, "scenario_id", None),
            "external_tools": getattr(scenario, "external_tools", None) or [],
            "cluster_posture": getattr(scenario, "cluster_posture", None) or {},
        },
        consumer="beacon",
    )
    return [
        (str(w.get("adapter_id") or ""), str(w.get("detail") or ""))
        for w in plan.warnings
        if w.get("code") == "TARGET_EGRESS_REQUIRED"
    ]


def _task_from_payload(payload: dict[str, Any]) -> Optional[Task]:
    """Reconstruct a :class:`Task` from a persisted ``queued_tasks.payload``
    dict (the round-trip of ``Task.to_dict()``). Returns ``None`` on a
    malformed payload so a single corrupt row can't abort rehydrate."""
    if not isinstance(payload, dict):
        return None
    try:
        created = payload.get("created_at")
        return Task(
            task_id=payload["task_id"],
            run_id=payload["run_id"],
            scenario_id=payload["scenario_id"],
            steps=payload.get("steps") or [],
            identity_context=payload.get("identity_context"),
            identity_default=payload.get("identity_default"),
            cgo_anchor=payload.get("cgo_anchor"),
            # Read back or a SimCore restart rehydrates a task that has
            # FORGOTTEN its tooling — the beacon would then run every step
            # without the tool it needs and report green. That is the same
            # manufactured false negative arriving by a side door.
            artifacts=payload.get("artifacts") or [],
            # Same rehydrate-honesty rationale as artifacts immediately above:
            # a restart must not silently DROP an operator's runtime-install
            # authorization for an in-flight run, which would make the
            # rehydrated task refuse a step the operator explicitly permitted.
            runtime_install_authorized=bool(payload.get("runtime_install_authorized", False)),
            created_at=datetime.fromisoformat(created) if created else datetime.utcnow(),
        )
    except (KeyError, TypeError, ValueError):  # pragma: no cover - defensive
        logger.warning("skipping malformed queued_tasks payload during rehydrate")
        return None


def allow_privileged_k8s() -> bool:
    """Deployment-level kill switch for node-access Kubernetes workloads.

    Default **false**. Lives here rather than in the API layer so the launch
    gate and the generation gate cannot answer this question differently:
    ``core/api/scenarios.py`` delegates to it.

    ``Settings`` wins when the config track has landed the field; the raw env
    var is the fallback so the switch is real today.
    """
    from config import settings  # noqa: PLC0415

    val = getattr(settings, "CORTEXSIM_ALLOW_PRIVILEGED_K8S", None)
    if val is None:
        val = __import__("os").getenv("CORTEXSIM_ALLOW_PRIVILEGED_K8S", "")
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in {"1", "true", "yes", "on"}


class LaunchRefusal(str):
    """A gate refusal that is BOTH the human sentence and the machine payload.

    Deliberately a ``str`` subclass. The gate's contract has always been
    "return prose or None", and every caller and test in the tree relies on
    that — but a console cannot branch on prose, so it was regexing the
    sentence to find out which consent key to offer. Subclassing keeps every
    existing ``err is None`` / ``"key" in err`` assertion true while carrying
    ``code`` and ``detail`` for the API layer.
    """

    code: str
    detail: dict[str, Any]

    def __new__(cls, message: str, *, code: str, detail: Optional[dict] = None):
        obj = super().__new__(cls, message)
        obj.code = code
        obj.detail = detail or {}
        return obj


def _refusal_code(err: Optional[str]) -> str:
    return getattr(err, "code", "LAUNCH_FAILED") if err is not None else "LAUNCH_FAILED"


def _refusal_detail(err: Optional[str]) -> dict[str, Any]:
    return dict(getattr(err, "detail", {}) or {}) if err is not None else {}


def _check_launch_consent(
    scenario: Any,
    consent: dict[str, bool],
    *,
    mode: Optional[str] = None,
) -> Optional[str]:
    """THE launch-time authorization gate. One entry point, two clauses.

    Returns an error string when refused, ``None`` when cleared — so
    :meth:`Orchestrator.launch` is unchanged and a refusal still creates no Run
    record.

    Clause 1 — **tool adapters** (:func:`_check_adapter_consent`): unchanged.
    Clause 2 — **cluster posture**: delegates to
    :func:`engine.k8s_manifest.check_cluster_consent`, the single authority the
    generation gate in ``core/api/scenarios.py`` also calls. Two surfaces, one
    decision function; a second, parallel implementation would be a defect.

    Launch is the SECONDARY gate and must not be presented as sufficient. The
    primary boundary is generation, because the dangerous artifact is the file
    that leaves SimCore: it is applied by ``kubectl`` on a laptop days later, on
    a host SimCore never sees, by someone who may not be the person who
    downloaded it.
    """
    adapter_error = _check_adapter_consent(scenario, consent)
    if adapter_error is not None:
        return adapter_error
    return _check_cluster_launch_consent(scenario, consent, mode=mode)


def _check_cluster_launch_consent(
    scenario: Any,
    consent: dict[str, bool],
    *,
    mode: Optional[str],
) -> Optional[str]:
    """Cluster-posture clause of the launch gate.

    Scoped to **push** mode on purpose. Push is the mode that stages an artifact
    for the DC to apply against a real cluster, and for a scenario declaring
    ``cluster_posture`` that artifact IS the Kubernetes manifest — the workload
    is the agent. Pull mode dispatches a task to an enrolled beacon and never
    emits a manifest, so gating it would fire the prompt on a case it does not
    describe. That is how an operator learns the flags are ceremony, and a gate
    that is ceremony is worse than no gate.
    """
    if mode != "push":
        return None
    raw = getattr(scenario, "cluster_posture", None)
    if not raw:
        return None

    from engine import k8s_manifest as km  # noqa: PLC0415

    scenario_id = getattr(scenario, "scenario_id", "UNKNOWN")
    try:
        posture = km.parse_posture({"cluster_posture": raw})
    except Exception as exc:  # a posture the model refuses outright
        return (
            f"Scenario {scenario_id} declares a cluster_posture this engine "
            f"refuses regardless of consent: {exc}"
        )
    if posture is None:
        return None
    try:
        km.check_cluster_consent(
            scenario_id, posture, consent or {},
            allow_privileged_k8s=allow_privileged_k8s(),
        )
    except km.ClusterConsentRequired as exc:
        return _cluster_refusal_text(exc)
    return None


def _cluster_refusal_text(exc: "Any") -> str:
    """Render the shipped refusal as one actionable sentence.

    Same voice as the adapter clause, and it names the capability, the object it
    lands on, and the literal consent key — "this scenario is privileged" is not
    actionable; "the pod template sets privileged: true" is.
    """
    body = exc.to_error()
    caps = ", ".join(
        f"{row['capability']} on the {row['object']}"
        for row in body.get("requested_capabilities", [])
    ) or "gated cluster capabilities"
    if exc.disabled_by_deployment:
        return (
            f"Scenario {exc.scenario_id} would create a {body['risk_tier']} "
            f"Kubernetes workload ({caps}) but CORTEXSIM_ALLOW_PRIVILEGED_K8S is "
            f"false on this SimCore. That is a deployment setting owned by "
            f"whoever runs this SimCore — no consent key will change it. "
            f"See docs/reference/k8s-delivery.md."
        )
    keys = ", ".join(exc.missing)
    relaunch = " and ".join(f"consent.{k}=true" for k in exc.missing)
    return (
        f"Scenario {exc.scenario_id} would create a {body['risk_tier']} "
        f"Kubernetes workload ({caps}) but consent {keys} is not set. "
        f"Re-launch with {relaunch} to proceed. Generating the manifest itself "
        f"additionally requires POST /api/scenarios/{exc.scenario_id}/bundle "
        f"with the same consent plus authorized_by — a launch does not authorise "
        f"the artifact. See docs/reference/k8s-delivery.md."
    )


def _check_adapter_consent(scenario: Any, consent: dict[str, bool]) -> Optional[str]:
    """Tool-adapter clause of :func:`_check_launch_consent`.

    Validate that the operator authorised every gated adapter the
    scenario references. Returns an error string when refused, ``None`` when
    cleared.

    Scenarios that use no adapter_refs (back-compat path) always pass.

    EVERY unmet requirement is collected in ONE pass. Returning at the first
    one made the DC re-launch, get the next refusal, re-launch again — three
    round trips in front of a customer to learn two facts SimCore knew before
    the first. The message still leads with the first adapter so the prose is
    unchanged in the single-adapter case; ``detail`` carries the full set.
    """
    from tools.adapter_catalog import catalog  # noqa: PLC0415

    #: safety_class → the consent key that clears it.
    gated = {"c2-framework": "c2_authorized",
             "dual-use-lab-only": "simulation_authorized"}

    reasons: list[dict[str, Any]] = []
    required: list[str] = []
    tools = scenario.external_tools or []

    for tool in tools:
        adapter_ref = tool.get("adapter_ref") if isinstance(tool, dict) else None
        if not adapter_ref:
            continue
        adapter = catalog.find(adapter_ref)
        if adapter is None:
            # Loader already warned; treat as advisory at launch time too.
            continue

        if adapter.safety_class == "destructive":
            if not (adapter.cleanup and adapter.cleanup.commands):
                # Defence in depth — the loader rejects this case already,
                # but if a deprecated_by/migration left a stale destructive
                # adapter in the catalog we refuse to dispatch it. NOT a
                # consent problem: no key the operator can set fixes it, so it
                # keeps its own code and short-circuits.
                return LaunchRefusal(
                    f"Scenario references destructive adapter {adapter_ref!r} "
                    f"but its catalog entry has no cleanup commands — refusing launch.",
                    code="ADAPTER_MISSING_CLEANUP",
                    detail={"adapter_id": adapter_ref},
                )
            continue

        key = gated.get(adapter.safety_class)
        if key is None or consent.get(key):
            continue
        if key not in required:
            required.append(key)
        reasons.append({
            "adapter_id": adapter_ref,
            "name": adapter.name,
            "version": adapter.version,
            "safety_class": adapter.safety_class,
            "required_consent": key,
        })

    if not reasons:
        return None

    first = reasons[0]
    label = ("C2-framework" if first["safety_class"] == "c2-framework" else "dual-use")
    also = ""
    if len(reasons) > 1:
        also = (f" ({len(reasons) - 1} further gated adapter(s) in this scenario "
                f"also need consent — see detail.reasons)")
    message = (
        f"Scenario uses {label} adapter '{first['adapter_id']}' "
        f"({first['name']} v{first['version']}) but consent "
        f"{first['required_consent']} is not set. Re-launch with "
        f"consent.{first['required_consent']}=true to proceed.{also}"
    )
    return LaunchRefusal(
        message,
        code="CONSENT_REQUIRED",
        detail={"required_consent": required, "reasons": reasons},
    )


def _resolve_adapter_placeholders(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Substitute ``{adapter:TOOL-XYZ}`` placeholders in step commands with
    the resolved adapter's ``run_template`` rendered with its ``default_args``.

    Unresolved placeholders are left as-is so the agent surfaces the failure
    cleanly instead of silently dropping the step.

    Returns a NEW list of step dicts; the input is never mutated (scenarios
    are loaded once at boot and shared across runs).
    """
    return _resolve_adapter_placeholders_with_binaries(steps)[0]


def _resolve_adapter_placeholders_with_binaries(
    steps: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    """:func:`_resolve_adapter_placeholders` plus, per step id, the
    ``install.binary`` of every pack whose placeholder was substituted into
    that step (deduplicated, in substitution order).

    A resolved pack with no ``install.binary`` contributes nothing and logs
    ``TOOL_PRESENCE_UNCHECKED`` -- the step runs unchecked, never silently
    treated as verified.
    """
    from tools.adapter_catalog import catalog  # noqa: PLC0415

    rendered_steps: list[dict[str, Any]] = []
    binaries: dict[str, list[str]] = {}
    for step in steps:
        new_step = dict(step)  # shallow copy is sufficient — we only edit ``command``
        cmd = new_step.get("command")
        if isinstance(cmd, str) and "{adapter:" in cmd:
            step_id = new_step.get("id")

            def _sub(m: Any, _sid: Any = step_id) -> str:
                adapter = catalog.find(m.group(1))
                rendered = _render_adapter(adapter, m.group(0))
                if adapter is not None and rendered != m.group(0):
                    binary = getattr(adapter.install, "binary", None)
                    if binary:
                        bucket = binaries.setdefault(_sid, [])
                        if binary not in bucket:
                            bucket.append(binary)
                    else:
                        logger.warning(
                            "TOOL_PRESENCE_UNCHECKED adapter=%s step=%s",
                            adapter.adapter_id, _sid,
                        )
                return rendered

            new_step["command"] = _ADAPTER_PLACEHOLDER_RE.sub(_sub, cmd)
        rendered_steps.append(new_step)
    return rendered_steps, binaries


def _render_adapter(adapter: Optional[Any], original_placeholder: str) -> str:
    """Render an adapter's ``run_template`` with its ``default_args``.

    Returns the original placeholder text on miss so the failure surfaces in
    the agent's output instead of expanding to an empty command (which would
    look like success).
    """
    if adapter is None or adapter.invoke is None:
        logger.warning("Adapter placeholder unresolved: %s", original_placeholder)
        return original_placeholder
    try:
        return adapter.invoke.run_template.format(
            binary=adapter.install.binary or "",
            **adapter.invoke.default_args,
        )
    except KeyError as exc:
        logger.warning(
            "Adapter %s run_template missing default for placeholder %s — leaving raw",
            adapter.adapter_id, exc,
        )
        return original_placeholder


def _resolve_stitch_placeholders(
    steps: list[dict[str, Any]],
    binding: Optional[Any],
) -> list[dict[str, Any]]:
    """Substitute ``{stitch:KEY}`` placeholders in step commands with the run's
    resolved Stitch-Context entities — the SIBLING of
    :func:`_resolve_adapter_placeholders`.

    ``binding`` is the :class:`engine.stitch_context.StitchBinding` produced by
    ``resolve_stitch_context(scenario.stitch_context, seed=run_id, target=agent)``,
    or ``None`` when the scenario declares no ``stitch_context`` (Phase-1 drafts
    and the whole shipped corpus). A ``None`` binding means every ``{stitch:*}`` is
    left verbatim, so a scenario without a context runs byte-identically to today.

    The honesty rule is identical to the adapter path: a ``{stitch:KEY}`` whose
    KEY is not one of the nine entity keys, or whose value did not resolve (the
    spec omitted that key), is LEFT AS-IS so the agent's own output surfaces the
    miss — never expanded to an empty string, which would read as success.

    Returns a NEW list of step dicts; the input is never mutated (scenarios are
    loaded once at boot and shared across runs).
    """
    rendered_steps: list[dict[str, Any]] = []
    for step in steps:
        new_step = dict(step)  # shallow copy is sufficient — we only edit ``command``
        cmd = new_step.get("command")
        if isinstance(cmd, str) and "{stitch:" in cmd:
            new_step["command"] = _STITCH_PLACEHOLDER_RE.sub(
                lambda m: _render_stitch(binding, m.group(1), m.group(0)),
                cmd,
            )
        rendered_steps.append(new_step)
    return rendered_steps


def _render_stitch(
    binding: Optional[Any],
    key: str,
    original_placeholder: str,
) -> str:
    """Render one ``{stitch:KEY}`` to its concrete resolved value.

    Returns the original placeholder text on any miss — no binding, an unknown
    key, or a key the spec never declared (``binding.get`` yields ``None``) — so
    the failure surfaces in the agent's output instead of expanding to an empty
    command (which would look like success). ``StitchBinding.get`` never raises,
    so an unknown key resolves to ``None`` and is left raw here.
    """
    if binding is None:
        return original_placeholder
    value = binding.get(key)
    if value is None:
        logger.warning("Stitch placeholder unresolved: %s", original_placeholder)
        return original_placeholder
    return str(value)


def _partition_channels(
    steps: list[dict[str, Any]],
    launch_target: str,
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    """Split a channel-typed step list into ``(agent_partitions, eal_steps)``.

    Agent-channel steps are grouped by their EFFECTIVE target — the step's own
    ``target`` (the "second endpoint" case) or, absent one, the launch target —
    preserving step order within each partition and first-seen order across
    partitions (dict insertion order). EAL-channel steps are returned separately:
    in Phase 3a they are recognised and validated but NEVER dispatched (that is
    3b), so they join no beacon task.

    Pure — no DB, no I/O. The caller verifies each partition's target is enrolled
    and enqueues one Task per partition.
    """
    from engine.scenario_loader import effective_channel  # noqa: PLC0415

    agent_partitions: dict[str, list[dict[str, Any]]] = {}
    eal_steps: list[dict[str, Any]] = []
    for step in steps:
        if effective_channel(step) == "eal":
            eal_steps.append(step)
            continue
        target = step.get("target") or launch_target
        agent_partitions.setdefault(target, []).append(step)
    return agent_partitions, eal_steps


def _build_channel_dispatch(
    steps: list[dict[str, Any]],
    launch_target: str,
    eal_outcomes: Optional[dict[str, dict[str, Any]]] = None,
) -> list[dict[str, Any]]:
    """Build the per-run ledger persisted on ``Run.channel_dispatch`` — one
    entry per step, in step order.

    An agent step records the endpoint it was enqueued to; an eal step records
    its real in-process dispatch outcome from ``eal_outcomes`` (Phase 3b —
    dispatched / dry_run / not_delivered / error), never a fabricated EAL result
    (Gate A5). When no outcome is supplied for an eal step it falls back to the
    honest ``EAL_DISPATCH_PENDING`` marker. Populated only for a multichannel
    run; a run with no channel-typed step keeps ``Run.channel_dispatch`` NULL.
    """
    from engine.scenario_loader import effective_channel  # noqa: PLC0415

    eal_outcomes = eal_outcomes or {}
    ledger: list[dict[str, Any]] = []
    for step in steps:
        step_id = step.get("id")
        if effective_channel(step) == "eal":
            outcome = eal_outcomes.get(step_id)
            if outcome is not None:
                ledger.append({
                    "step_id": step_id,
                    "channel": "eal",
                    "plugin": (step.get("eal") or {}).get("plugin"),
                    "target": None,
                    "status": outcome.get("status", "dispatched"),
                    "code": outcome.get("code"),
                    "detail": outcome.get("detail"),
                    # Present only for collector steps, so a ledger entry for
                    # any other eal step is unchanged.
                    **{k: outcome[k] for k in ("collector_target", "collector_source")
                       if k in outcome},
                })
            else:
                ledger.append({
                    "step_id": step_id,
                    "channel": "eal",
                    "plugin": (step.get("eal") or {}).get("plugin"),
                    "target": None,
                    "status": "pending",
                    "code": "EAL_DISPATCH_PENDING",
                    "detail": (
                        "EAL dispatch not yet run; no campaign run, no EAL "
                        "result fabricated"
                    ),
                })
        else:
            ledger.append({
                "step_id": step_id,
                "channel": "agent",
                "target": step.get("target") or launch_target,
                "status": "enqueued",
                "code": None,
            })
    return ledger


#: ``Result.outcome_reason`` values for a run that terminated WITHOUT executing
#: (OPS-01). Each one names the path that ended it, so a report can say why a
#: row left the denominator rather than only that it did.
REASON_LAUNCH_REFUSED = "LAUNCH_REFUSED"
REASON_TASK_LOST_ON_RESTART = "TASK_LOST_ON_RESTART"
REASON_BEACON_LOST_UNCLASSIFIED = "BEACON_LOST_UNCLASSIFIED"
REASON_ABORTED_BEFORE_DELIVERY = "ABORTED_BEFORE_DELIVERY"
REASON_AGENT_CANNOT_STAGE_ARTIFACTS = "AGENT_CANNOT_STAGE_ARTIFACTS"


async def stamp_not_executed(
    db: AsyncSession,
    run_id: str,
    reason: str,
    *,
    commit: bool = True,
    exclude_steps: Any = (),
) -> int:
    """Stamp every Result of a run that never executed ``not_executed``.

    The shared helper for every path that terminates a run without executing
    it: a launch refused after seeding, an orphan failed by :meth:`rehydrate`,
    a beacon-lost run that cannot be classified, an abort before delivery, and
    (for the owner of ``core/api/agents.py``) a task refused at the
    artifact-staging capability gate.

    Before this, those runs kept their seeded rows with ``outcome`` NULL —
    i.e. *pending, awaiting reconcile*. A matching alert from anything else on
    the host then credited a detection, with a real-looking MTTD, to a run that
    never touched the endpoint. ``not_executed`` is in
    ``EXCLUDED_FROM_DENOMINATOR``: ``apply_verdicts`` never credits such a row
    and every coverage figure leaves it out.

    A row the host already marked ``not_reached`` / ``not_executed`` keeps its
    own (better) reason. ``exclude_steps`` names steps that DID execute
    in-process (a composed run's EAL steps) and must not be stamped.

    Returns the number of rows stamped. ``commit=False`` leaves the commit to
    a caller that is mid-transaction.
    """
    from engine.outcome import (  # noqa: PLC0415
        EXCLUDED_FROM_DENOMINATOR,
        OUTCOME_NOT_EXECUTED,
    )
    from models import Result  # noqa: PLC0415

    if not reason:
        raise ValueError("stamp_not_executed needs a reason")
    skip = set(exclude_steps or ())
    rows = (await db.execute(
        select(Result).where(Result.run_id == run_id)
    )).scalars().all()
    stamped = 0
    for r in rows:
        if r.step_id in skip or r.outcome in EXCLUDED_FROM_DENOMINATOR:
            continue
        r.outcome = OUTCOME_NOT_EXECUTED
        r.outcome_reason = reason
        stamped += 1
    if commit:
        await db.commit()
    if stamped:
        logger.info("run_id=%s: %d result(s) stamped not_executed (%s)",
                    run_id, stamped, reason)
    return stamped


def in_process_steps(run: Any) -> set[str]:
    """Step ids a composed run executed inside SimCore (its EAL steps).

    Read from ``Run.channel_dispatch``; empty for a single-channel run.
    """
    ledger = getattr(run, "channel_dispatch", None) or []
    return {
        e.get("step_id") for e in ledger
        if isinstance(e, dict) and e.get("channel") == "eal" and e.get("step_id")
    }


async def _publish_run_status(
    run_id: str, status: str, step_id: Optional[str] = None
) -> None:
    """Publish a ``run.status`` event onto the live event bus.

    Swallows any bus error so a publishing hiccup can never abort the run
    transition that triggered it.
    """
    try:
        from events import event_bus  # noqa: PLC0415

        await event_bus.publish(
            run_id,
            {"type": "run.status", "run_id": run_id,
             "data": {"status": status, "step_id": step_id}},
        )
    except Exception:  # pragma: no cover - defensive
        logger.exception("event_bus publish failed run_id=%s status=%s", run_id, status)


# Module-level singleton — imported by API layer
orchestrator = Orchestrator()

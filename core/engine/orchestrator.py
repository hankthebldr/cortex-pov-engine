"""
CortexSim Orchestrator.

Routes run requests to the appropriate execution path:
  - mode="pull"  → creates Run record, enqueues task for the waiting agent
  - mode="push"  → creates Run record, generates bundle, returns download URL

Manages an in-memory task queue: Dict[agent_id, List[Task]]
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select
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
        # agent_id -> (loop, asyncio.Event) for sub-second long-polling wakeup.
        # The loop is stored alongside because asyncio.Event binds itself to the
        # first loop that awaits it and raises "bound to a different event loop"
        # on any other. uvicorn runs one loop per process so a bare Event
        # survives in production, but it makes the whole path unreachable from
        # TestClient — which is precisely why no Python test covered it.
        self._events: dict[str, tuple[asyncio.AbstractEventLoop, asyncio.Event]] = {}

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
    ) -> LaunchResult:
        """
        Create a Run record and route to pull or push path.

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
        if mode == "pull" and target_agent_id:
            agent_row = (await db.execute(
                select(Agent).where(Agent.agent_id == target_agent_id)
            )).scalar_one_or_none()
            if agent_row is not None:
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
        )
        db.add(run)
        await db.commit()

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
            return await self._handle_pull(
                run_id, scenario, target_agent_id, identity, db,
                runtime_install_authorized=runtime_install_authorized,
            )
        elif mode == "push":
            return await self._handle_push(run_id, scenario, db)
        else:
            return LaunchResult(
                success=False,
                run_id=run_id,
                error=f"Unknown mode '{mode}' — must be 'pull' or 'push'",
            )

    # ------------------------------------------------------------------
    # pull path
    # ------------------------------------------------------------------

    async def _handle_pull(
        self,
        run_id: str,
        scenario: Any,
        target_agent_id: Optional[str],
        identity: Optional[str],
        db: AsyncSession,
        runtime_install_authorized: bool = False,
    ) -> LaunchResult:
        from models import Run  # noqa: PLC0415

        if not target_agent_id:
            return LaunchResult(
                success=False,
                run_id=run_id,
                error="target_agent_id is required for pull mode",
            )

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
            return LaunchResult(
                success=False,
                run_id=run_id,
                error=f"{exc.code}: {exc.detail}",
            )

        task = Task(
            task_id=str(uuid.uuid4()),
            run_id=run_id,
            scenario_id=scenario.scenario_id,
            steps=_resolve_adapter_placeholders(scenario.steps or []),
            identity_context=identity,
            identity_default=execution_identity.get("default"),
            cgo_anchor=getattr(scenario, "cgo_anchor", None),
            artifacts=artifacts,
            runtime_install_authorized=runtime_install_authorized,
        )
        self._enqueue(target_agent_id, task)
        # Mirror the task to the durable queue so a restart can rehydrate it.
        await self._persist_task(db, target_agent_id, task)

        # Update run status to running
        run_result = await db.execute(
            select(Run).where(Run.run_id == run_id)
        )
        run: Optional[Run] = run_result.scalar_one_or_none()
        if run:
            run.status = "running"
            await db.commit()
            await _publish_run_status(run_id, "running")

        logger.info(
            "Task enqueued task_id=%s agent=%s run_id=%s",
            task.task_id,
            target_agent_id,
            run_id,
        )
        return LaunchResult(
            success=True,
            run_id=run_id,
            mode="pull",
            message=f"Task queued for agent '{target_agent_id}'",
        )

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

        steps = scenario.steps or []
        count = 0
        enriched = 0
        for step in steps:
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
                )
                if card is not None:
                    result.detection_kind = card.kind
                    result.detection_logic = card.logic
                    result.detection_severity = card.severity
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
        entry = self._events.get(agent_id)
        if entry is not None:
            bound_loop, event = entry
            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None
            if running is bound_loop:
                event.set()
            else:
                # Enqueued from outside the waiter's loop (or from no loop at
                # all): hand the wakeup to the owning loop rather than touching
                # its futures from here.
                bound_loop.call_soon_threadsafe(event.set)

    async def wait_for_task(self, agent_id: str, timeout: float) -> None:
        """Wait up to timeout seconds for a task to be enqueued for agent_id."""
        loop = asyncio.get_running_loop()
        entry = self._events.get(agent_id)
        if entry is None or entry[0] is not loop:
            # First wait for this agent, or a different loop from the one the
            # cached Event bound to. Reusing the stale Event raises
            # RuntimeError("... is bound to a different event loop").
            event = asyncio.Event()
            self._events[agent_id] = (loop, event)
        else:
            event = entry[1]
        event.clear()
        if self._queue.get(agent_id):
            return
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass

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
        """
        task = self.dequeue(agent_id)
        if task is not None:
            await self._delete_persisted_task(db, task.task_id)
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

    async def _delete_persisted_task(self, db: AsyncSession, task_id: str) -> None:
        """Remove a delivered/aborted task's durable row."""
        from models import QueuedTask  # noqa: PLC0415
        from sqlalchemy import delete  # noqa: PLC0415

        await db.execute(delete(QueuedTask).where(QueuedTask.task_id == task_id))
        await db.commit()

    async def rehydrate(self, db: AsyncSession) -> dict[str, int]:
        """Rebuild the in-memory queue from the durable ``queued_tasks`` table
        and reconcile orphaned ``running`` runs. Called once from the FastAPI
        lifespan on startup.

        Reconciliation rules:
          * Every persisted task is re-loaded into the in-memory queue so a
            waiting agent receives it after the restart.
          * Any Run still in ``pending``/``running`` whose task did NOT survive
            in the durable queue is marked ``failed`` (its work was lost) so it
            does not hang forever. A note is appended to the run output.

        Returns counts ``{rehydrated, failed_orphans}`` for logging.
        """
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
        now = datetime.utcnow()
        for run in run_result.scalars().all():
            if run.run_id in live_run_ids:
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
            failed_orphans += 1
        if failed_orphans:
            await db.commit()

        logger.info(
            "orchestrator rehydrate: %d task(s) restored, %d orphaned run(s) failed",
            rehydrated, failed_orphans,
        )
        return {"rehydrated": rehydrated, "failed_orphans": failed_orphans}


# ---------------------------------------------------------------------------
# Tool-adapter helpers (Phase A)
# ---------------------------------------------------------------------------


_ADAPTER_PLACEHOLDER_RE = __import__("re").compile(r"\{adapter:(TOOL-[A-Z0-9-]+)\}")


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
        logger.info(
            "payload plan %s (%s): %s",
            plan.composition_id, warning.get("code"), warning.get("detail"),
        )
    return plan.to_beacon_artifacts()


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
    from tools.adapter_catalog import catalog  # noqa: PLC0415

    rendered_steps: list[dict[str, Any]] = []
    for step in steps:
        new_step = dict(step)  # shallow copy is sufficient — we only edit ``command``
        cmd = new_step.get("command")
        if isinstance(cmd, str) and "{adapter:" in cmd:
            new_step["command"] = _ADAPTER_PLACEHOLDER_RE.sub(
                lambda m: _render_adapter(catalog.find(m.group(1)), m.group(0)),
                cmd,
            )
        rendered_steps.append(new_step)
    return rendered_steps


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

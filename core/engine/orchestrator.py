"""
CortexSim Orchestrator.

Routes run requests to the appropriate execution path:
  - mode="pull"  → creates Run record, enqueues task for the waiting agent
  - mode="push"  → creates Run record, generates bundle, returns download URL

Manages an in-memory task queue: Dict[agent_id, List[Task]]
"""

from __future__ import annotations

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


@dataclass
class Task:
    task_id: str
    run_id: str
    scenario_id: str
    steps: list[dict[str, Any]]
    identity_context: Optional[str]
    created_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "run_id": self.run_id,
            "scenario_id": self.scenario_id,
            "steps": self.steps,
            "identity_context": self.identity_context,
            "created_at": self.created_at.isoformat(),
        }


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


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class Orchestrator:
    """
    In-memory task queue + DB run record manager.

    The task queue is ephemeral (lost on restart).  The Run table in SQLite
    is the durable source of truth; agents re-register on startup and will
    receive pending tasks if the queue is repopulated.
    """

    def __init__(self) -> None:
        # agent_id -> list of pending Task objects
        self._queue: dict[str, list[Task]] = {}

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
    ) -> LaunchResult:
        """
        Create a Run record and route to pull or push path.
        """
        from models import Run, Scenario  # noqa: PLC0415

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
            return await self._handle_pull(run_id, scenario, target_agent_id, identity, db)
        elif mode == "push":
            return self._handle_push(run_id, scenario)
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
    ) -> LaunchResult:
        from models import Run  # noqa: PLC0415

        if not target_agent_id:
            return LaunchResult(
                success=False,
                run_id=run_id,
                error="target_agent_id is required for pull mode",
            )

        task = Task(
            task_id=str(uuid.uuid4()),
            run_id=run_id,
            scenario_id=scenario.scenario_id,
            steps=scenario.steps or [],
            identity_context=identity,
        )
        self._enqueue(target_agent_id, task)

        # Update run status to running
        run_result = await db.execute(
            select(Run).where(Run.run_id == run_id)
        )
        run: Optional[Run] = run_result.scalar_one_or_none()
        if run:
            run.status = "running"
            await db.commit()

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

    def _handle_push(self, run_id: str, scenario: Any) -> LaunchResult:
        # Generate a download URL — the actual content is produced on demand
        # by the /api/scenarios/{id}/download endpoint.
        download_base = f"/api/scenarios/{scenario.scenario_id}/download"
        logger.info("Push bundle ready run_id=%s scenario=%s", run_id, scenario.scenario_id)
        return LaunchResult(
            success=True,
            run_id=run_id,
            mode="push",
            message="Push bundle ready for download",
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

    def dequeue(self, agent_id: str) -> Optional[Task]:
        """Pop the next task for an agent, or return None."""
        queue = self._queue.get(agent_id, [])
        if not queue:
            return None
        return queue.pop(0)

    def peek_queue(self, agent_id: str) -> list[Task]:
        """Return all pending tasks for an agent without removing them."""
        return list(self._queue.get(agent_id, []))


# Module-level singleton — imported by API layer
orchestrator = Orchestrator()

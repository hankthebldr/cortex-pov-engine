"""
Campaign executor — turns a declarative ``Campaign`` into actual plugin runs.

Architecture: the executor accepts a ``Campaign``, resolves each step's plugin
class through the ``PluginRegistry``, validates the per-step params against
the plugin's Pydantic schema, then runs the steps sequentially (campaigns are
narrative orderings, not parallel fan-outs). Each step emits ECS-formatted
audit events through ``AuditLogger`` and returns a ``SimulationResult``.

The executor is async-first. Callers may either:
  * ``await CampaignExecutor.execute(campaign)`` directly (blocking the
    caller for the campaign's duration), or
  * Submit it to a ``TaskQueue`` (in-memory background task by default,
    Celery if available) for fire-and-forget long-running campaigns.

Both modes share the same code path, so a campaign behaves identically
regardless of how it was launched.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

from .audit import AuditLogger, ecs_event
from .base import BaseSimulation, SimulationContext, SimulationResult
from .campaign import Campaign, CampaignStep
from .registry import PluginRegistry, get_default_registry
from .safety import SafetyError, SafetyPolicy


logger = logging.getLogger("cortexsim.eal.executor")


# ---------------------------------------------------------------------------
# Run state — kept in-process; the API layer mirrors interesting fields to DB.
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class ExecutorState:
    run_id: str
    campaign_id: str
    status: str  # pending | running | complete | failed | aborted
    started_at: datetime
    completed_at: Optional[datetime] = None
    step_results: list[SimulationResult] = dataclasses.field(default_factory=list)
    error: Optional[str] = None
    dry_run: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "campaign_id": self.campaign_id,
            "status": self.status,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "step_results": [r.to_dict() for r in self.step_results],
            "error": self.error,
            "dry_run": self.dry_run,
        }


# ---------------------------------------------------------------------------
# Task-queue abstraction — keeps the door open for Celery/Redis without
# forcing the dependency on smaller deployments.
# ---------------------------------------------------------------------------


class TaskQueue:
    """Minimal interface for submitting an awaitable as a background task."""

    async def submit(self, coro_factory: Callable[[], Awaitable[Any]]) -> str:
        raise NotImplementedError


class InMemoryTaskQueue(TaskQueue):
    """Default implementation — uses ``asyncio.create_task``.

    Suitable for single-replica deployments and unit tests. For multi-pod
    workers, swap in a ``CeleryTaskQueue`` (see ``celery_queue.py`` in the
    deploy/ tree for a wiring example).
    """

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}

    async def submit(self, coro_factory: Callable[[], Awaitable[Any]]) -> str:
        task_id = str(uuid.uuid4())
        task = asyncio.create_task(coro_factory(), name=f"eal-{task_id}")
        self._tasks[task_id] = task
        task.add_done_callback(lambda _t, k=task_id: self._tasks.pop(k, None))
        return task_id

    def task_ids(self) -> list[str]:
        return list(self._tasks)

    async def cancel(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if task is None:
            return False
        task.cancel()
        return True


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------


class CampaignExecutor:
    def __init__(
        self,
        registry: Optional[PluginRegistry] = None,
        audit: Optional[AuditLogger] = None,
        plugin_factory: Optional[Callable[[type[BaseSimulation]], BaseSimulation]] = None,
    ) -> None:
        self.registry = registry or get_default_registry()
        self.audit = audit or AuditLogger()
        self._plugin_factory = plugin_factory or (lambda cls: cls())

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def execute(self, campaign: Campaign) -> ExecutorState:
        """Run the campaign synchronously; return final ExecutorState."""
        run_id = str(uuid.uuid4())
        state = ExecutorState(
            run_id=run_id,
            campaign_id=campaign.campaign_id,
            status="pending",
            started_at=_utcnow(),
            dry_run=campaign.dry_run,
        )

        policy = SafetyPolicy(
            simulation_authorized=campaign.simulation_authorized,
            authorized_by=campaign.authorized_by,
            target_allowlist=campaign.target_allowlist,
            dry_run=campaign.dry_run,
        )

        self._emit_event(
            ecs_event(
                action="campaign_started",
                outcome="success",
                category="process",
                type_="start",
                message=f"Campaign {campaign.campaign_id} started",
                campaign_id=campaign.campaign_id,
                run_id=run_id,
                extra={"dry_run": campaign.dry_run, "policy": policy.to_dict()},
            )
        )

        try:
            policy.assert_campaign_authorized()
        except SafetyError as exc:
            state.status = "failed"
            state.error = f"safety_violation: {exc}"
            state.completed_at = _utcnow()
            self._emit_event(
                ecs_event(
                    action="campaign_refused",
                    outcome="failure",
                    category="iam",
                    type_="denied",
                    message=str(exc),
                    campaign_id=campaign.campaign_id,
                    run_id=run_id,
                )
            )
            return state

        state.status = "running"

        for step in campaign.steps:
            result = await self._run_step(campaign, run_id, step, policy)
            state.step_results.append(result)
            if result.status == "error" and step.on_error == "abort":
                state.status = "aborted"
                state.error = f"step {step.step_id} failed: {result.error}"
                break

        if state.status == "running":
            state.status = "complete"
        state.completed_at = _utcnow()

        self._emit_event(
            ecs_event(
                action="campaign_finished",
                outcome="success" if state.status == "complete" else "failure",
                category="process",
                type_="end",
                message=f"Campaign {campaign.campaign_id} finished: {state.status}",
                campaign_id=campaign.campaign_id,
                run_id=run_id,
                extra={
                    "step_count": len(state.step_results),
                    "duration_seconds": (state.completed_at - state.started_at).total_seconds(),
                },
            )
        )
        return state

    async def submit(self, campaign: Campaign, queue: Optional[TaskQueue] = None) -> str:
        """Submit a campaign to the background task queue and return its id."""
        queue = queue or InMemoryTaskQueue()
        return await queue.submit(lambda c=campaign: self.execute(c))

    # ------------------------------------------------------------------
    # Step runner
    # ------------------------------------------------------------------

    async def _run_step(
        self,
        campaign: Campaign,
        run_id: str,
        step: CampaignStep,
        policy: SafetyPolicy,
    ) -> SimulationResult:
        started_at = _utcnow()

        try:
            plugin_cls = self.registry.get(step.plugin)
        except KeyError as exc:
            return self._failure(step, started_at, f"plugin_not_found: {exc}")

        try:
            params = plugin_cls.validate_params(step.params)
        except Exception as exc:
            return self._failure(step, started_at, f"params_invalid: {exc}")

        plugin = self._plugin_factory(plugin_cls)
        sim_run_id = BaseSimulation.new_simulation_run_id()

        async def emit(payload: dict[str, Any]) -> None:
            self._emit_event(payload)

        ctx = SimulationContext(
            campaign_id=campaign.campaign_id,
            run_id=run_id,
            step_id=step.step_id,
            simulation_run_id=sim_run_id,
            dry_run=campaign.dry_run,
            target_allowlist=list(campaign.target_allowlist),
            emit_event=emit,
            params=params,
        )
        # Attach the policy without leaking it into the dataclass surface —
        # plugins that need it call ctx.params; the policy is consulted via
        # the helper below. Stashing it as an attribute keeps the dataclass
        # frozen-friendly while remaining accessible.
        setattr(ctx, "_policy", policy)
        setattr(ctx, "authorise", policy.authorise)

        self._emit_event(
            ecs_event(
                action="step_started",
                outcome="success",
                category="process",
                type_="start",
                message=f"step {step.step_id} ({step.plugin}) started",
                campaign_id=campaign.campaign_id,
                run_id=run_id,
                step_id=step.step_id,
                plugin=step.plugin,
                extra={"simulation_run_id": sim_run_id, "dry_run": campaign.dry_run},
            )
        )

        try:
            result = await plugin.run(ctx)
        except SafetyError as exc:
            return self._failure(step, started_at, f"safety_violation: {exc}", plugin=step.plugin)
        except asyncio.CancelledError:
            return self._failure(step, started_at, "cancelled", plugin=step.plugin)
        except Exception as exc:
            logger.exception("step %s failed unexpectedly", step.step_id)
            return self._failure(step, started_at, f"plugin_error: {exc}", plugin=step.plugin)

        # Normalise: plugins must return a SimulationResult, but be defensive.
        if not isinstance(result, SimulationResult):
            return self._failure(
                step, started_at,
                f"plugin returned {type(result).__name__}, expected SimulationResult",
                plugin=step.plugin,
            )

        self._emit_event(
            ecs_event(
                action="step_finished",
                outcome=result.status,
                category="process",
                type_="end",
                message=f"step {step.step_id} ({step.plugin}) finished: {result.status}",
                campaign_id=campaign.campaign_id,
                run_id=run_id,
                step_id=step.step_id,
                plugin=step.plugin,
                bytes_sent=result.bytes_sent,
                extra={"events_emitted": result.events_emitted, "duration_seconds": result.duration_seconds},
            )
        )
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _failure(
        self,
        step: CampaignStep,
        started_at: datetime,
        error: str,
        plugin: Optional[str] = None,
    ) -> SimulationResult:
        return SimulationResult(
            plugin=plugin or step.plugin,
            step_id=step.step_id,
            status="error",
            started_at=started_at,
            completed_at=_utcnow(),
            events_emitted=0,
            error=error,
        )

    def _emit_event(self, event: dict[str, Any]) -> None:
        try:
            self.audit.emit(event)
        except Exception:  # pragma: no cover - audit must never crash a run
            logger.exception("audit emit failed")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)

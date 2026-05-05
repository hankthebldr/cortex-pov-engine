"""
BaseSimulation — abstract base class every EAL simulator plugin must inherit.

The contract is intentionally narrow: a plugin declares its identity via a
``Meta`` inner class, validates its parameters into a typed Pydantic model,
and implements a single async ``run`` coroutine. The executor never calls
anything else on a plugin, so subclasses cannot accidentally expose
side-channel state.

Plugins may be sync or IO-bound; everything is awaited inside the executor's
event loop. Long blocking calls (e.g. raw socket sends) should be wrapped in
``asyncio.to_thread`` by the plugin itself — the executor will not do this.
"""

from __future__ import annotations

import abc
import dataclasses
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any, ClassVar, Optional

from pydantic import BaseModel


logger = logging.getLogger("cortexsim.eal.base")


@dataclasses.dataclass
class SimulationContext:
    """Per-step execution context passed into every plugin invocation.

    Attributes:
        campaign_id   Stable identifier of the parent campaign.
        run_id        Unique identifier for this campaign execution.
        step_id       Identifier of the campaign step this plugin call belongs to.
        simulation_run_id  UUID injected as the ``X-Simulation-Run-ID`` HTTP
                      header so SOC analysts can filter simulator traffic.
        dry_run       If True, the plugin must NOT emit real network traffic;
                      it should compute and log the planned actions only.
        target_allowlist  Hostnames / CIDRs the safety policy has authorised.
        emit_event    Async callback the plugin uses to stream structured
                      events back to the executor (audit log, progress).
        params        The validated Pydantic params model for this step.
        deadline_at   Optional ISO-8601 timestamp after which the plugin should
                      stop and return early (cooperative cancellation).
    """

    campaign_id: str
    run_id: str
    step_id: str
    simulation_run_id: str
    dry_run: bool
    target_allowlist: list[str]
    emit_event: Callable[[dict[str, Any]], Awaitable[None]]
    params: BaseModel
    deadline_at: Optional[datetime] = None

    @property
    def telemetry_headers(self) -> dict[str, str]:
        """HTTP headers every plugin should add to outbound requests."""
        return {
            "X-Simulation-Run-ID": self.simulation_run_id,
            "X-Simulation-Campaign-ID": self.campaign_id,
            "X-Simulation-Source": "cortexsim-eal-simulator/1.0",
        }


@dataclasses.dataclass
class SimulationResult:
    """Structured outcome a plugin returns from ``run``."""

    plugin: str
    step_id: str
    status: str  # "success" | "error" | "skipped"
    started_at: datetime
    completed_at: datetime
    events_emitted: int
    bytes_sent: int = 0
    detail: dict[str, Any] = dataclasses.field(default_factory=dict)
    error: Optional[str] = None

    @property
    def duration_seconds(self) -> float:
        return (self.completed_at - self.started_at).total_seconds()

    def to_dict(self) -> dict[str, Any]:
        return {
            "plugin": self.plugin,
            "step_id": self.step_id,
            "status": self.status,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
            "duration_seconds": self.duration_seconds,
            "events_emitted": self.events_emitted,
            "bytes_sent": self.bytes_sent,
            "detail": self.detail,
            "error": self.error,
        }


class BaseSimulation(abc.ABC):
    """Abstract base class every EAL simulator plugin must implement.

    Subclasses must:
      1. Define a ``Meta`` inner class with at minimum ``name``, ``version``,
         ``description``, ``mitre_techniques`` (list[str]), ``eal_targets``
         (list[str]) and ``params_model`` (a Pydantic ``BaseModel`` subclass).
      2. Implement ``async run(ctx: SimulationContext) -> SimulationResult``.

    The registry uses ``Meta.name`` as the lookup key, so it must be unique
    across all loaded plugins.
    """

    class Meta:
        name: ClassVar[str]
        version: ClassVar[str] = "1.0.0"
        description: ClassVar[str] = ""
        mitre_techniques: ClassVar[list[str]] = []
        eal_targets: ClassVar[list[str]] = []
        params_model: ClassVar[type[BaseModel]]

    # ------------------------------------------------------------------
    # Helpers shared by every plugin (do not override).
    # ------------------------------------------------------------------

    @classmethod
    def metadata(cls) -> dict[str, Any]:
        meta = cls.Meta
        required = ("name", "params_model")
        for attr in required:
            if not getattr(meta, attr, None):
                raise TypeError(
                    f"{cls.__name__}.Meta missing required attribute '{attr}'"
                )
        return {
            "name": meta.name,
            "version": getattr(meta, "version", "1.0.0"),
            "description": getattr(meta, "description", ""),
            "mitre_techniques": list(getattr(meta, "mitre_techniques", [])),
            "eal_targets": list(getattr(meta, "eal_targets", [])),
            "params_schema": meta.params_model.model_json_schema(),
            "class": f"{cls.__module__}.{cls.__name__}",
        }

    @classmethod
    def validate_params(cls, raw: dict[str, Any]) -> BaseModel:
        """Validate raw params against the plugin's Pydantic model."""
        return cls.Meta.params_model.model_validate(raw or {})

    @staticmethod
    def new_simulation_run_id() -> str:
        """Generate a unique ID suitable for the X-Simulation-Run-ID header."""
        return f"cortexsim-{uuid.uuid4()}"

    @staticmethod
    def utcnow() -> datetime:
        return datetime.now(timezone.utc)

    # ------------------------------------------------------------------
    # Plugin contract.
    # ------------------------------------------------------------------

    @abc.abstractmethod
    async def run(self, ctx: SimulationContext) -> SimulationResult:
        """Execute the simulation. Must be idempotent w.r.t. the deadline."""

    async def dry_run(self, ctx: SimulationContext) -> SimulationResult:
        """Default dry-run shim — subclasses may override for richer planning.

        The executor sets ``ctx.dry_run = True`` and calls ``run`` directly;
        plugins should branch on ``ctx.dry_run`` themselves rather than relying
        on this method. It exists primarily so dry-run logic can be unit-tested
        independently when desired.
        """
        ctx_dry = dataclasses.replace(ctx, dry_run=True)
        return await self.run(ctx_dry)

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        try:
            return f"<Plugin {self.Meta.name} v{self.Meta.version}>"
        except Exception:
            return f"<Plugin {self.__class__.__name__}>"

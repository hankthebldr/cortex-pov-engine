"""Shared fixtures for EAL simulator tests."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import pytest

from eal_simulator import (
    AuditLogger,
    BaseSimulation,
    Campaign,
    CampaignExecutor,
    PluginRegistry,
    SimulationContext,
    SimulationResult,
)
from eal_simulator.registry import reset_default_registry
from eal_simulator.safety import SafetyPolicy
from pydantic import BaseModel, Field


class _DummyParams(BaseModel):
    target: str = "example.test"
    iterations: int = Field(default=1, ge=1, le=10)


class DummyPlugin(BaseSimulation):
    """In-test plugin that records every invocation but never sends traffic."""

    invocations: list[dict[str, Any]] = []

    class Meta:
        name = "test_dummy"
        version = "0.0.1"
        description = "Test-only plugin"
        mitre_techniques = ["T0000"]
        eal_targets = ["test"]
        params_model = _DummyParams

    async def run(self, ctx: SimulationContext) -> SimulationResult:  # type: ignore[override]
        params: _DummyParams = ctx.params  # type: ignore[assignment]
        # Always exercise the safety authorise hook so the policy is covered.
        getattr(ctx, "authorise")(params.target)
        self.__class__.invocations.append({
            "campaign_id": ctx.campaign_id,
            "step_id": ctx.step_id,
            "dry_run": ctx.dry_run,
            "target": params.target,
            "headers": dict(ctx.telemetry_headers),
        })
        await ctx.emit_event({
            "@timestamp": datetime.now(timezone.utc).isoformat(),
            "event": {"action": "dummy_run", "outcome": "success"},
            "cortexsim": {"plugin": self.Meta.name, "step_id": ctx.step_id},
        })
        return SimulationResult(
            plugin=self.Meta.name,
            step_id=ctx.step_id,
            status="success",
            started_at=self.utcnow(),
            completed_at=self.utcnow(),
            events_emitted=1,
            detail={"target": params.target, "iterations": params.iterations},
        )


@pytest.fixture(autouse=True)
def _reset_registry():
    """Each test starts with a clean default registry."""
    DummyPlugin.invocations = []
    reset_default_registry()
    yield
    reset_default_registry()


@pytest.fixture
def isolated_registry() -> PluginRegistry:
    """A registry pre-populated with only the dummy plugin."""
    reg = PluginRegistry()
    reg.register(DummyPlugin)
    return reg


@pytest.fixture
def memory_audit() -> AuditLogger:
    """An AuditLogger that emits via Python logging only (no file)."""
    return AuditLogger(file_path=None)


@pytest.fixture
def make_executor(isolated_registry, memory_audit):
    def _factory() -> CampaignExecutor:
        return CampaignExecutor(registry=isolated_registry, audit=memory_audit)

    return _factory


@pytest.fixture
def sample_campaign() -> Campaign:
    return Campaign.model_validate({
        "campaign_id": "CMP-TEST-001",
        "name": "unit test campaign",
        "authorized_by": "tester@example.com",
        "simulation_authorized": True,
        "target_allowlist": ["example.test"],
        "dry_run": False,
        "steps": [
            {"step_id": "step-01", "plugin": "test_dummy",
             "params": {"target": "example.test"}}
        ],
    })


@pytest.fixture
def event_loop():
    """Provide a dedicated loop so async tests can run without pytest-asyncio
    auto-mode interactions with the rest of the suite."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()

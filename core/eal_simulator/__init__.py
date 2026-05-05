"""
EAL Traffic Simulator — enterprise-grade emulation of threat actor network
behaviour to trigger Palo Alto Networks Enhanced Application Logs (EALs) and
validate Cortex XDR / XSIAM Network Detection and Response analytics.

Top-level public surface:
  - BaseSimulation        — abstract contract every plugin must implement
  - PluginRegistry        — dynamic loader / lookup
  - Campaign / CampaignStep / PluginInvocation — declarative campaign schema
  - CampaignExecutor      — async orchestrator with task-queue abstraction
  - AuditLogger           — ECS-JSON structured audit emitter
  - SafetyError           — raised when a campaign violates safety policy

The simulator is read-only with respect to Cortex (no API connection); it only
emits controlled network telemetry. Every emitted HTTP request carries the
``X-Simulation-Run-ID`` header so SOC analysts can filter simulation traffic
out of incident reviews.
"""

from __future__ import annotations

from .audit import AuditLogger, ecs_event
from .base import BaseSimulation, SimulationContext, SimulationResult
from .campaign import Campaign, CampaignStep, PluginInvocation
from .executor import CampaignExecutor, ExecutorState
from .registry import PluginRegistry, get_default_registry
from .safety import SafetyError, SafetyPolicy

__all__ = [
    "AuditLogger",
    "BaseSimulation",
    "Campaign",
    "CampaignExecutor",
    "CampaignStep",
    "ExecutorState",
    "PluginInvocation",
    "PluginRegistry",
    "SafetyError",
    "SafetyPolicy",
    "SimulationContext",
    "SimulationResult",
    "ecs_event",
    "get_default_registry",
]

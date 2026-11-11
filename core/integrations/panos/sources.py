# core/integrations/panos/sources.py
"""Per-agent source checks (spec §5.3, §5.4).

Task 5 ships only the contract: ``AgentRef`` and a ``check_sources`` that reports the
no-agent case honestly. Task 6 replaces the body with the address, NAT, collision and
policy-path checks.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from . import codes
from .context import PreflightContext

NO_AGENTS_DETAIL = (
    "No enrolled agent was selected, so no source path through this firewall was "
    "checked. A firewall-wide green does not show that any agent's traffic is "
    "inspected or logged.")


@dataclass(frozen=True)
class AgentRef:
    agent_id: str
    hostname: str
    last_ip: Optional[str]


async def check_sources(ctx: PreflightContext, agents: list[AgentRef]) -> list[dict[str, Any]]:
    if not agents:
        return [{"code": codes.PN_NO_AGENTS, "status": "degraded", "detail": NO_AGENTS_DETAIL}]
    return []

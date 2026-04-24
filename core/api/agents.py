"""
CortexSim API — /api/agents router.

Endpoints:
  GET  /api/agents                   — list all connected agents
  POST /api/agents/register          — agent registers itself
  GET  /api/agents/{agent_id}/tasks  — agent polls for next task (returns task or null)
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from engine.orchestrator import orchestrator
from models import Agent

logger = logging.getLogger("cortexsim.api.agents")

router = APIRouter(prefix="/agents", tags=["agents"])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class RegisterRequest(BaseModel):
    agent_id: str
    hostname: str
    os: str
    capabilities: list[str] = []


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("")
async def list_agents(db: AsyncSession = Depends(get_db)):
    """Return all registered agents."""
    result = await db.execute(select(Agent).order_by(Agent.last_seen.desc()))
    agents = result.scalars().all()
    logger.info("list_agents count=%d", len(agents))
    return {"agents": [a.to_dict() for a in agents], "total": len(agents)}


@router.post("/register")
async def register_agent(
    body: RegisterRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Register a pull-model beacon agent.
    Idempotent — re-registering an existing agent_id updates its metadata.
    """
    result = await db.execute(
        select(Agent).where(Agent.agent_id == body.agent_id)
    )
    existing: Optional[Agent] = result.scalar_one_or_none()
    now = datetime.utcnow()

    if existing is None:
        agent = Agent(
            agent_id=body.agent_id,
            hostname=body.hostname,
            os=body.os,
            capabilities=body.capabilities,
            registered_at=now,
            last_seen=now,
            status="online",
        )
        db.add(agent)
        logger.info("register_agent NEW agent_id=%s hostname=%s os=%s", body.agent_id, body.hostname, body.os)
    else:
        existing.hostname = body.hostname
        existing.os = body.os
        existing.capabilities = body.capabilities
        existing.last_seen = now
        existing.status = "online"
        logger.info("register_agent UPDATED agent_id=%s", body.agent_id)

    await db.commit()
    return {
        "status": "registered",
        "agent_id": body.agent_id,
        "message": "Agent registered successfully",
    }


@router.get("/{agent_id}/tasks")
async def poll_tasks(
    agent_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Agent polls for its next pending task.
    Returns the task dict if one is available, or {"task": null} if the queue is empty.
    Also updates agent last_seen timestamp.
    """
    # Update last_seen
    result = await db.execute(
        select(Agent).where(Agent.agent_id == agent_id)
    )
    agent: Optional[Agent] = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "Agent not found", "code": "AGENT_NOT_FOUND", "detail": f"agent_id='{agent_id}' — register first via POST /api/agents/register"},
        )

    agent.last_seen = datetime.utcnow()
    agent.status = "online"
    await db.commit()

    task = orchestrator.dequeue(agent_id)
    if task is None:
        logger.debug("poll_tasks agent=%s no tasks", agent_id)
        return {"task": None}

    logger.info("poll_tasks agent=%s dispatching task_id=%s run_id=%s", agent_id, task.task_id, task.run_id)
    return {"task": task.to_dict()}

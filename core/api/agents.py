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

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
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


_AGENT_MODULE = "github.com/hankthebldr/cortexsim/agent"


def _resolve_server(request: Request, override: Optional[str]) -> str:
    """Server URL the agent beacons back to. Prefer an explicit override
    (the DC knows the jumpbox's reachable address); else derive from the
    request so `curl <host>/api/agents/install | bash` just works."""
    if override:
        return override.rstrip("/")
    return str(request.base_url).rstrip("/")


def _linux_installer(server: str, agent_id: str, interval: int) -> str:
    return f"""#!/usr/bin/env bash
# ─── CortexSim agent installer (Linux) ──────────────────────────────────────
# Builds the stdlib-only Go beacon and registers it against SimCore.
# Override any value via env: CORTEXSIM_SERVER / CORTEXSIM_AGENT_ID / CORTEXSIM_INTERVAL.
set -euo pipefail
SERVER="${{CORTEXSIM_SERVER:-{server}}}"
AGENT_ID="${{CORTEXSIM_AGENT_ID:-{agent_id}}}"
INTERVAL="${{CORTEXSIM_INTERVAL:-{interval}}}"
echo "[cortexsim] target server : $SERVER"
echo "[cortexsim] agent id      : $AGENT_ID"

if ! command -v go >/dev/null 2>&1; then
  echo "[cortexsim] ERROR: Go 1.21+ is required (https://go.dev/dl). Install Go and re-run." >&2
  exit 1
fi

WORKDIR="$(mktemp -d)"; BIN="$WORKDIR/cortexsim-agent"
if [ -n "${{CORTEXSIM_SRC:-}}" ] && [ -d "$CORTEXSIM_SRC/agent" ]; then
  echo "[cortexsim] building from local source: $CORTEXSIM_SRC/agent"
  ( cd "$CORTEXSIM_SRC/agent" && go build -o "$BIN" . )
else
  echo "[cortexsim] installing module {_AGENT_MODULE}@latest"
  GOBIN="$WORKDIR" go install {_AGENT_MODULE}@latest
  [ -x "$WORKDIR/agent" ] && BIN="$WORKDIR/agent"
fi

echo "[cortexsim] launching beacon (Ctrl-C to stop) …"
exec "$BIN" --server "$SERVER" --id "$AGENT_ID" --interval "$INTERVAL"
"""


def _windows_installer(server: str, agent_id: str, interval: int) -> str:
    return f"""# ─── CortexSim agent installer (Windows / PowerShell) ───────────────────────
# Builds the stdlib-only Go beacon and registers it against SimCore.
# Override via env: CORTEXSIM_SERVER / CORTEXSIM_AGENT_ID / CORTEXSIM_INTERVAL.
$ErrorActionPreference = 'Stop'
$Server   = if ($env:CORTEXSIM_SERVER)   {{ $env:CORTEXSIM_SERVER }}   else {{ '{server}' }}
$AgentId  = if ($env:CORTEXSIM_AGENT_ID) {{ $env:CORTEXSIM_AGENT_ID }} else {{ '{agent_id}' }}
$Interval = if ($env:CORTEXSIM_INTERVAL) {{ $env:CORTEXSIM_INTERVAL }} else {{ '{interval}' }}
Write-Host "[cortexsim] target server : $Server"
Write-Host "[cortexsim] agent id      : $AgentId"

if (-not (Get-Command go -ErrorAction SilentlyContinue)) {{
  Write-Error '[cortexsim] Go 1.21+ is required (https://go.dev/dl). Install Go and re-run.'
  exit 1
}}

$Work = New-Item -ItemType Directory -Path (Join-Path $env:TEMP ("cortexsim-" + [guid]::NewGuid()))
$env:GOBIN = $Work.FullName
Write-Host '[cortexsim] installing module {_AGENT_MODULE}@latest'
go install {_AGENT_MODULE}@latest
$Bin = Join-Path $Work.FullName 'agent.exe'
Write-Host '[cortexsim] launching beacon (Ctrl-C to stop) ...'
& $Bin --server $Server --id $AgentId --interval $Interval
"""


@router.get("/install")
async def agent_installer(
    request: Request,
    os: str = "linux",
    id: str = "jumpbox-01",
    server: Optional[str] = None,
    interval: int = 10,
):
    """Generate a ready-to-run agent installer for the chosen OS.

    Linux  → bash  (`curl -fsSL <server>/api/agents/install?os=linux&id=<id> | bash`)
    Windows→ PowerShell (.ps1)

    The script builds the stdlib-only Go beacon (Go 1.21+ on the target) and
    launches it pointed at this SimCore. No binary hosting — the agent is built
    on the target, matching the documented Go-toolchain requirement.
    """
    os_norm = (os or "linux").strip().lower()
    if os_norm not in {"linux", "windows"}:
        raise HTTPException(
            status_code=400,
            detail={"error": "Unsupported OS", "code": "BAD_OS",
                    "detail": "os must be 'linux' or 'windows'"},
        )
    resolved = _resolve_server(request, server)
    if os_norm == "windows":
        body = _windows_installer(resolved, id, interval)
        media, fname = "text/plain; charset=utf-8", "install-cortexsim-agent.ps1"
    else:
        body = _linux_installer(resolved, id, interval)
        media, fname = "text/x-shellscript; charset=utf-8", "install-cortexsim-agent.sh"
    return PlainTextResponse(
        body, media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


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

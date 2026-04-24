"""
CortexSim API — /api/tools router.

Endpoints:
  GET  /api/tools                        — list all tools + status
  POST /api/tools/{tool_name}/install    — trigger install via ToolInstantiator.install()
  POST /api/tools/{tool_name}/start      — start tool with body params
  POST /api/tools/{tool_name}/stop       — stop tool
  GET  /api/tools/{tool_name}/status     — health check + status
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import ToolInstance
from tools.instantiator import InstallResult, StartResult, StopResult, ToolStatus, instantiator
from tools.registry import TOOL_REGISTRY

logger = logging.getLogger("cortexsim.api.tools")

router = APIRouter(prefix="/tools", tags=["tools"])


class StartParams(BaseModel):
    params: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Helpers: sync ToolInstance DB record with in-process state
# ---------------------------------------------------------------------------


async def _sync_tool_instance(
    tool_name: str,
    status: str,
    db: AsyncSession,
    pid: Optional[int] = None,
    install_path: Optional[str] = None,
    port: Optional[int] = None,
    health_check_now: bool = False,
) -> None:
    """Upsert a ToolInstance row to reflect current tool state."""
    result = await db.execute(
        select(ToolInstance).where(ToolInstance.tool_name == tool_name)
    )
    instance: Optional[ToolInstance] = result.scalar_one_or_none()

    now = datetime.utcnow()

    if instance is None:
        instance = ToolInstance(
            tool_name=tool_name,
            status=status,
            pid=pid,
            install_path=install_path,
            port=port,
        )
        db.add(instance)
    else:
        instance.status = status
        if pid is not None:
            instance.pid = pid
        if install_path is not None:
            instance.install_path = install_path
        if port is not None:
            instance.port = port

    if status == "installed" and instance.installed_at is None:
        instance.installed_at = now
    if health_check_now:
        instance.last_health_check = now

    await db.commit()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("")
async def list_tools(db: AsyncSession = Depends(get_db)):
    """List all tools with their registry definition and current runtime status."""
    statuses = instantiator.list_all()

    # Merge with DB state for persistence across restarts
    db_result = await db.execute(select(ToolInstance))
    db_instances: dict[str, ToolInstance] = {
        ti.tool_name: ti for ti in db_result.scalars().all()
    }

    tools_out = []
    for ts in statuses:
        entry = TOOL_REGISTRY.get(ts.tool_name, {})
        db_row = db_instances.get(ts.tool_name)

        # Live process status takes precedence; DB fills gaps on restart
        effective_status = ts.status
        if ts.status == "not_installed" and db_row and db_row.status in ("installed", "stopped"):
            effective_status = db_row.status

        tools_out.append({
            "tool_name": ts.tool_name,
            "status": effective_status,
            "pid": ts.pid or (db_row.pid if db_row else None),
            "port": ts.port or (db_row.port if db_row else None),
            "install_path": ts.install_path,
            "description": entry.get("description", ""),
            "plane": entry.get("plane", []),
            "type": entry.get("type", ""),
            "last_health_check": db_row.last_health_check.isoformat() if db_row and db_row.last_health_check else None,
        })

    logger.info("list_tools count=%d", len(tools_out))
    return {"tools": tools_out, "total": len(tools_out)}


@router.post("/{tool_name}/install")
async def install_tool(tool_name: str, db: AsyncSession = Depends(get_db)):
    """Build the tool from its submodule source."""
    if tool_name not in TOOL_REGISTRY:
        raise HTTPException(
            status_code=404,
            detail={"error": "Tool not found", "code": "TOOL_NOT_FOUND", "detail": f"tool_name='{tool_name}'"},
        )

    logger.info("install_tool tool=%s", tool_name)
    result: InstallResult = instantiator.install(tool_name)

    if result.success:
        await _sync_tool_instance(
            tool_name=tool_name,
            status="installed",
            db=db,
            install_path=result.install_path,
            port=TOOL_REGISTRY[tool_name].get("port"),
        )
        return {"status": "installed", "tool_name": tool_name, "message": result.message, "install_path": result.install_path}
    else:
        raise HTTPException(
            status_code=500,
            detail={"error": result.message, "code": "INSTALL_FAILED", "detail": result.error or ""},
        )


@router.post("/{tool_name}/start")
async def start_tool(
    tool_name: str,
    body: StartParams = StartParams(),
    db: AsyncSession = Depends(get_db),
):
    """Start the tool as a managed process."""
    if tool_name not in TOOL_REGISTRY:
        raise HTTPException(
            status_code=404,
            detail={"error": "Tool not found", "code": "TOOL_NOT_FOUND", "detail": f"tool_name='{tool_name}'"},
        )

    logger.info("start_tool tool=%s params=%s", tool_name, body.params)
    result: StartResult = instantiator.start(tool_name, body.params)

    if result.success:
        await _sync_tool_instance(
            tool_name=tool_name,
            status="running",
            db=db,
            pid=result.pid,
            port=TOOL_REGISTRY[tool_name].get("port"),
        )
        return {"status": "running", "tool_name": tool_name, "pid": result.pid, "message": result.message}
    else:
        raise HTTPException(
            status_code=500,
            detail={"error": result.error or "Start failed", "code": "START_FAILED", "detail": ""},
        )


@router.post("/{tool_name}/stop")
async def stop_tool(tool_name: str, db: AsyncSession = Depends(get_db)):
    """Stop the running tool process."""
    if tool_name not in TOOL_REGISTRY:
        raise HTTPException(
            status_code=404,
            detail={"error": "Tool not found", "code": "TOOL_NOT_FOUND", "detail": f"tool_name='{tool_name}'"},
        )

    logger.info("stop_tool tool=%s", tool_name)
    result: StopResult = instantiator.stop(tool_name)

    if result.success:
        await _sync_tool_instance(
            tool_name=tool_name,
            status="stopped",
            db=db,
        )
        return {"status": "stopped", "tool_name": tool_name, "message": result.message}
    else:
        raise HTTPException(
            status_code=500,
            detail={"error": result.error or "Stop failed", "code": "STOP_FAILED", "detail": ""},
        )


@router.get("/{tool_name}/status")
async def tool_status(tool_name: str, db: AsyncSession = Depends(get_db)):
    """Perform a live health check and return current status."""
    if tool_name not in TOOL_REGISTRY:
        raise HTTPException(
            status_code=404,
            detail={"error": "Tool not found", "code": "TOOL_NOT_FOUND", "detail": f"tool_name='{tool_name}'"},
        )

    ts: ToolStatus = instantiator.status(tool_name)
    healthy = instantiator.health_check(tool_name)

    await _sync_tool_instance(
        tool_name=tool_name,
        status=ts.status,
        db=db,
        pid=ts.pid,
        health_check_now=True,
    )

    logger.info("tool_status tool=%s status=%s healthy=%s", tool_name, ts.status, healthy)
    return {
        "tool_name": tool_name,
        "status": ts.status,
        "pid": ts.pid,
        "port": ts.port,
        "healthy": healthy,
        "description": ts.description,
        "plane": ts.plane,
    }

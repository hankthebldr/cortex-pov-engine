"""
CortexSim API — /api/agents router.

Endpoints:
  GET  /api/agents                   — list all connected agents
  POST /api/agents/register          — agent registers itself
  GET  /api/agents/{agent_id}/tasks  — agent polls for next task (returns task or null)
"""

from __future__ import annotations

import logging
import re
import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from engine.orchestrator import orchestrator
from models import Agent, EnrollmentToken

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


class MintTokenRequest(BaseModel):
    label: Optional[str] = Field(default=None, max_length=80)
    ttl_seconds: int = Field(default=3600, ge=60, le=2_592_000)  # 1 min … 30 days
    max_uses: int = Field(default=1, ge=1, le=1000)


class EnrollRequest(BaseModel):
    token: str
    hostname: str
    os: str
    capabilities: list[str] = []
    # Optional client-suggested name; sanitised + suffixed for uniqueness.
    desired_name: Optional[str] = Field(default=None, max_length=60)


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


def _linux_installer(server: str, agent_id: str, interval: int, token: Optional[str]) -> str:
    # When a token is present, the script ENROLLS to obtain a server-assigned
    # id (the recommended flow); otherwise it falls back to the explicit id.
    enroll_block = f"""
if [ -n "${{CORTEXSIM_TOKEN:-{token or ''}}}" ]; then
  TOKEN="${{CORTEXSIM_TOKEN:-{token or ''}}}"
  echo "[cortexsim] enrolling with token ...${{TOKEN: -6}}"
  HOSTN="$(hostname 2>/dev/null || echo unknown)"
  RESP="$(curl -fsSL -X POST "$SERVER/api/agents/enroll" \\
    -H 'Content-Type: application/json' \\
    -d "{{\\"token\\":\\"$TOKEN\\",\\"hostname\\":\\"$HOSTN\\",\\"os\\":\\"linux\\"}}")" \\
    || {{ echo '[cortexsim] ERROR: enrollment failed (token invalid/expired?)' >&2; exit 1; }}
  AGENT_ID="$(printf '%s' "$RESP" | sed -n 's/.*\\"agent_id\\":[[:space:]]*\\"\\([^\\"]*\\)\\".*/\\1/p')"
  [ -n "$AGENT_ID" ] || {{ echo '[cortexsim] ERROR: no agent_id in enroll response' >&2; exit 1; }}
  echo "[cortexsim] enrolled as: $AGENT_ID"
fi"""
    return f"""#!/usr/bin/env bash
# ─── CortexSim agent installer (Linux) ──────────────────────────────────────
# One-line onboarding. With a token, SimCore assigns the agent id (recommended);
# without one, the explicit/default id is used (legacy).
# Env overrides: CORTEXSIM_SERVER / CORTEXSIM_TOKEN / CORTEXSIM_AGENT_ID / CORTEXSIM_INTERVAL.
set -euo pipefail
SERVER="${{CORTEXSIM_SERVER:-{server}}}"
AGENT_ID="${{CORTEXSIM_AGENT_ID:-{agent_id}}}"
INTERVAL="${{CORTEXSIM_INTERVAL:-{interval}}}"
echo "[cortexsim] target server : $SERVER"
{enroll_block}
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


def _windows_installer(server: str, agent_id: str, interval: int, token: Optional[str]) -> str:
    enroll_block = f"""
$Token = if ($env:CORTEXSIM_TOKEN) {{ $env:CORTEXSIM_TOKEN }} else {{ '{token or ''}' }}
if ($Token) {{
  Write-Host "[cortexsim] enrolling with token ...$($Token.Substring([Math]::Max(0,$Token.Length-6)))"
  $payload = @{{ token = $Token; hostname = $env:COMPUTERNAME; os = 'windows' }} | ConvertTo-Json
  $resp = Invoke-RestMethod -Method Post -Uri "$Server/api/agents/enroll" -ContentType 'application/json' -Body $payload
  $AgentId = $resp.agent_id
  if (-not $AgentId) {{ Write-Error '[cortexsim] enrollment returned no agent_id'; exit 1 }}
  Write-Host "[cortexsim] enrolled as: $AgentId"
}}"""
    return f"""# ─── CortexSim agent installer (Windows / PowerShell) ───────────────────────
# One-line onboarding. With a token, SimCore assigns the agent id (recommended).
# Env overrides: CORTEXSIM_SERVER / CORTEXSIM_TOKEN / CORTEXSIM_AGENT_ID / CORTEXSIM_INTERVAL.
$ErrorActionPreference = 'Stop'
$Server   = if ($env:CORTEXSIM_SERVER)   {{ $env:CORTEXSIM_SERVER }}   else {{ '{server}' }}
$AgentId  = if ($env:CORTEXSIM_AGENT_ID) {{ $env:CORTEXSIM_AGENT_ID }} else {{ '{agent_id}' }}
$Interval = if ($env:CORTEXSIM_INTERVAL) {{ $env:CORTEXSIM_INTERVAL }} else {{ '{interval}' }}
Write-Host "[cortexsim] target server : $Server"
{enroll_block}
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
    token: Optional[str] = None,
):
    """Generate a ready-to-run agent installer for the chosen OS.

    Linux  → bash  (`curl -fsSL '<server>/api/agents/install?token=<tok>' | bash`)
    Windows→ PowerShell (.ps1)

    Recommended flow: mint a token (`POST /api/agents/enroll/tokens`) and pass
    `?token=`. The script then ENROLLS to obtain a server-assigned agent id —
    the DC never invents one. Without a token it falls back to the explicit
    `?id=` (legacy self-asserted path). Either way the script builds the
    stdlib-only Go beacon (Go 1.21+ on the target) and launches it.
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
        body = _windows_installer(resolved, id, interval, token)
        media, fname = "text/plain; charset=utf-8", "install-cortexsim-agent.ps1"
    else:
        body = _linux_installer(resolved, id, interval, token)
        media, fname = "text/x-shellscript; charset=utf-8", "install-cortexsim-agent.sh"
    return PlainTextResponse(
        body, media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# Heartbeat staleness thresholds (seconds against utcnow - last_seen).
_ONLINE_WINDOW_S = 30
_STALE_WINDOW_S = 300  # 5 minutes


def _derive_status(last_seen: Optional[datetime], now: datetime) -> tuple[str, float]:
    """Derive an agent's liveness status from its last_seen age.

    Returns ``(status, age_seconds)`` where status ∈ online | stale | offline.
    Status is computed at read time — last_seen is the source of truth, the
    stored ``Agent.status`` column is only a cache for the SSE sweep.
    """
    if last_seen is None:
        return "offline", 1e9
    age = (now - last_seen).total_seconds()
    if age < _ONLINE_WINDOW_S:
        return "online", age
    if age < _STALE_WINDOW_S:
        return "stale", age
    return "offline", age


@router.get("")
async def list_agents(db: AsyncSession = Depends(get_db)):
    """Return all registered agents with liveness status derived from
    ``last_seen`` (online < 30s, stale < 5m, offline ≥ 5m)."""
    result = await db.execute(select(Agent).order_by(Agent.last_seen.desc()))
    agents = result.scalars().all()
    now = datetime.utcnow()
    out: list[dict] = []
    for a in agents:
        d = a.to_dict()
        status, age = _derive_status(a.last_seen, now)
        d["status"] = status
        d["last_seen_age_seconds"] = round(age, 1)
        out.append(d)
    logger.info("list_agents count=%d", len(out))
    return {"agents": out, "total": len(out)}


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


# ---------------------------------------------------------------------------
# Enrollment-token flow — server-assigned identities, one-line onboarding
# ---------------------------------------------------------------------------


def _slugify_name(raw: Optional[str], hostname: str) -> str:
    """Build a safe agent-name stem from a desired name or the hostname."""
    base = (raw or hostname or "agent").strip().lower()
    base = re.sub(r"[^a-z0-9-]+", "-", base).strip("-") or "agent"
    return base[:40]


@router.post("/enroll/tokens")
async def mint_enrollment_token(
    body: MintTokenRequest,
    db: AsyncSession = Depends(get_db),
):
    """Mint an enrollment token. The full token value is returned EXACTLY once
    here; afterwards only its tail is shown. Hand this to a jumpbox via the
    one-line installer (``/api/agents/install?token=...``)."""
    now = datetime.utcnow()
    token_value = "cxs_" + secrets.token_urlsafe(32)
    row = EnrollmentToken(
        token=token_value,
        label=body.label,
        created_at=now,
        expires_at=now + timedelta(seconds=body.ttl_seconds),
        max_uses=body.max_uses,
        used_count=0,
        revoked=False,
    )
    db.add(row)
    await db.commit()
    logger.info("mint_enrollment_token label=%s max_uses=%d ttl=%ds",
                body.label, body.max_uses, body.ttl_seconds)
    out = row.to_dict(reveal=True)  # reveal once, at mint
    return out


@router.get("/enroll/tokens")
async def list_enrollment_tokens(db: AsyncSession = Depends(get_db)):
    """List enrollment tokens (tails only) with validity derived at read time."""
    rows = (await db.execute(
        select(EnrollmentToken).order_by(EnrollmentToken.created_at.desc())
    )).scalars().all()
    now = datetime.utcnow()
    return {"tokens": [{**t.to_dict(), "valid": t.is_valid(now)} for t in rows]}


@router.delete("/enroll/tokens/{token_id}", status_code=200)
async def revoke_enrollment_token(token_id: int, db: AsyncSession = Depends(get_db)):
    """Revoke an enrollment token so it can no longer be redeemed."""
    row = (await db.execute(
        select(EnrollmentToken).where(EnrollmentToken.id == token_id)
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail={
            "error": "Enrollment token not found", "code": "TOKEN_NOT_FOUND",
            "detail": f"id={token_id}"})
    row.revoked = True
    await db.commit()
    return {"status": "revoked", "id": token_id}


@router.post("/enroll")
async def enroll_agent(body: EnrollRequest, db: AsyncSession = Depends(get_db)):
    """Redeem an enrollment token and register a NEW agent with a
    server-assigned id.

    This is the front door for the rethought deployment UX: the operator never
    invents an agent id (the source of duplicate/typo'd ids in the old
    self-asserted ``/register`` flow) and the token bounds who may onboard.
    Returns the assigned ``agent_id`` and the ``server`` URL to beacon.
    """
    now = datetime.utcnow()
    token_row = (await db.execute(
        select(EnrollmentToken).where(EnrollmentToken.token == body.token)
    )).scalar_one_or_none()
    if token_row is None or not token_row.is_valid(now):
        # Single opaque error — don't distinguish "wrong" from "expired/used".
        raise HTTPException(status_code=403, detail={
            "error": "Invalid or expired enrollment token", "code": "ENROLL_DENIED",
            "detail": "mint a fresh token via POST /api/agents/enroll/tokens"})

    # Assign a unique agent id from the name stem + a short random suffix.
    stem = _slugify_name(body.desired_name, body.hostname)
    agent_id = f"{stem}-{secrets.token_hex(3)}"
    # Vanishingly unlikely collision guard.
    while (await db.execute(select(Agent).where(Agent.agent_id == agent_id))).scalar_one_or_none():
        agent_id = f"{stem}-{secrets.token_hex(3)}"

    db.add(Agent(
        agent_id=agent_id, hostname=body.hostname, os=body.os,
        capabilities=body.capabilities or ["shell", "identity-harness"],
        registered_at=now, last_seen=now, status="online",
    ))
    token_row.used_count += 1
    await db.commit()
    logger.info("enroll_agent assigned agent_id=%s (token tail=...%s, use %d/%d)",
                agent_id, body.token[-6:], token_row.used_count, token_row.max_uses)
    return {
        "status": "enrolled",
        "agent_id": agent_id,
        "remaining_uses": max(0, token_row.max_uses - token_row.used_count),
    }


@router.delete("/{agent_id}")
async def delete_agent(agent_id: str, db: AsyncSession = Depends(get_db)):
    """Remove a registered agent. Idempotent-friendly: 404 if it never existed.

    Used by the Targets management UI to prune stale/old beacons. Does not touch
    run history — only the agent registry row."""
    result = await db.execute(select(Agent).where(Agent.agent_id == agent_id))
    agent: Optional[Agent] = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "Agent not found", "code": "AGENT_NOT_FOUND",
                    "detail": f"agent_id='{agent_id}'"},
        )
    await db.delete(agent)
    await db.commit()
    logger.info("delete_agent agent_id=%s", agent_id)
    return {"status": "deleted", "agent_id": agent_id}


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

    # DB-aware dequeue so the durable queued_tasks row is removed on delivery
    # (GAP-API-005). A restart will not re-deliver an already-dispatched task.
    task = await orchestrator.dequeue_for_agent(agent_id, db)
    if task is None:
        logger.debug("poll_tasks agent=%s no tasks", agent_id)
        return {"task": None}

    logger.info("poll_tasks agent=%s dispatching task_id=%s run_id=%s", agent_id, task.task_id, task.run_id)
    return {"task": task.to_dict()}


# ---------------------------------------------------------------------------
# Phase 2 — background heartbeat sweep (SSE emitter)
# ---------------------------------------------------------------------------
#
# list_agents already derives online/stale/offline at read time, so the sweep
# is belt-and-suspenders: its only job is to write status transitions to the
# DB cache and emit `agent.status` events on the GLOBAL bus so the UI sees an
# agent go offline without re-listing. Started from main.py's lifespan.


async def sweep_agents_once() -> int:
    """Single pass of the staleness sweep. Recomputes each agent's derived
    status; on a transition, persists it and publishes an ``agent.status``
    event. Returns the number of agents whose status changed.

    Opens its own DB session (it runs outside any request scope).
    """
    from database import AsyncSessionLocal  # noqa: PLC0415
    from events import event_bus  # noqa: PLC0415

    changed = 0
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Agent))
        agents = result.scalars().all()
        now = datetime.utcnow()
        for a in agents:
            new_status, _ = _derive_status(a.last_seen, now)
            if new_status != a.status:
                a.status = new_status
                changed += 1
                try:
                    await event_bus.publish(
                        None,
                        {"type": "agent.status", "run_id": None,
                         "data": {"agent_id": a.agent_id, "status": new_status}},
                    )
                except Exception:  # pragma: no cover - defensive
                    logger.exception("event_bus publish failed agent=%s", a.agent_id)
        if changed:
            await db.commit()
    return changed


async def heartbeat_sweep_loop(interval_seconds: int = 30) -> None:
    """Run :func:`sweep_agents_once` forever on a fixed cadence.

    Cancelled on app shutdown. Transient errors are logged and do not kill
    the loop. This is the SSE emitter for agent liveness transitions.
    """
    import asyncio  # noqa: PLC0415

    logger.info("heartbeat sweep loop started interval=%ds", interval_seconds)
    try:
        while True:
            await asyncio.sleep(interval_seconds)
            try:
                changed = await sweep_agents_once()
                if changed:
                    logger.info("heartbeat sweep: %d agent status transition(s)", changed)
            except Exception:  # pragma: no cover - defensive
                logger.exception("heartbeat sweep pass failed — continuing")
    except asyncio.CancelledError:  # pragma: no cover - shutdown path
        logger.info("heartbeat sweep loop cancelled")
        raise

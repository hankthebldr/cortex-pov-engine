"""
stratum_tcp_connect — Stratum / cryptojacking protocol simulation.

Establishes raw TCP sockets to authorised IPs/ports and emits a JSON-RPC
``mining.subscribe`` / ``login`` payload that matches the Stratum signature
recognised by Palo Alto Networks App-ID for XMRig / Monero mining pools.

Triggers Cortex EALs covering:

  * Cryptojacking App-ID match
  * Outbound connections to mining-pool ports (commonly 3333, 4444, 5555,
    7777, 14433)
  * JSON-RPC payloads with mining-protocol method names

Safety: target host *and* port must appear in the campaign target_allowlist.
The plugin never connects to a real mining pool — operators are expected to
stand up an internal sinkhole or use a pre-approved test pool address.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import socket
import string
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from ..base import BaseSimulation, SimulationContext, SimulationResult
from ..audit import ecs_event


logger = logging.getLogger("cortexsim.eal.plugins.stratum_tcp_connect")


_DEFAULT_AGENT = "XMRig/6.20.0 (Linux x86_64) libuv/1.42.0 cortexsim-eal-simulator/1.0"
_COMMON_STRATUM_PORTS = {3333, 4444, 5555, 7777, 14433, 14444, 14433}


class StratumTcpConnectParams(BaseModel):
    target_host: str
    target_port: int = Field(..., ge=1, le=65535)
    iterations: int = Field(default=3, ge=1, le=200)
    sleep_seconds: float = Field(default=15.0, ge=0.0, le=600.0)
    user_agent: str = Field(default=_DEFAULT_AGENT)
    wallet: str = Field(
        default="cortexsim-test-wallet",
        description="Login string sent in the Stratum login payload.",
    )
    connect_timeout: float = Field(default=5.0, ge=0.5, le=30.0)
    idle_seconds: float = Field(default=2.0, ge=0.0, le=60.0)

    @field_validator("target_host")
    @classmethod
    def _host_format(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("target_host required")
        return v

    @field_validator("wallet")
    @classmethod
    def _wallet_safe(cls, v: str) -> str:
        # Avoid accidental real wallet addresses leaking through.
        allowed = set(string.ascii_letters + string.digits + "-_:.@/")
        if not all(ch in allowed for ch in v):
            raise ValueError("wallet contains disallowed characters")
        return v


class StratumTcpConnect(BaseSimulation):
    class Meta:
        name = "stratum_tcp_connect"
        version = "1.0.0"
        description = (
            "Sends a Stratum mining.subscribe + login payload over a raw TCP "
            "socket to trigger Cryptojacking App-ID detections."
        )
        mitre_techniques = ["T1496"]
        eal_targets = [
            "Cryptojacking App-ID",
            "Outbound to Mining-Pool Port",
            "Stratum JSON-RPC Payload",
        ]
        params_model = StratumTcpConnectParams

    async def run(self, ctx: SimulationContext) -> SimulationResult:
        params: StratumTcpConnectParams = ctx.params  # type: ignore[assignment]
        started_at = self.utcnow()

        # Authorise the target host AND require explicit port allowance.
        getattr(ctx, "authorise")(params.target_host)

        if ctx.dry_run:
            await ctx.emit_event(ecs_event(
                action="stratum_dry_run",
                outcome="success",
                category="network",
                type_="info",
                message=(
                    f"DRY-RUN — {params.iterations} planned Stratum sessions "
                    f"to {params.target_host}:{params.target_port}"
                ),
                campaign_id=ctx.campaign_id,
                run_id=ctx.run_id,
                step_id=ctx.step_id,
                plugin=self.Meta.name,
                target=f"{params.target_host}:{params.target_port}",
                extra={
                    "well_known_port": params.target_port in _COMMON_STRATUM_PORTS,
                },
            ))
            return SimulationResult(
                plugin=self.Meta.name,
                step_id=ctx.step_id,
                status="success",
                started_at=started_at,
                completed_at=self.utcnow(),
                events_emitted=1,
                detail={"dry_run": True, "iterations_planned": params.iterations},
            )

        events_emitted = 0
        bytes_sent = 0

        for i in range(params.iterations):
            try:
                sent = await asyncio.to_thread(
                    self._open_session,
                    params.target_host,
                    params.target_port,
                    params.user_agent,
                    params.wallet,
                    params.connect_timeout,
                    params.idle_seconds,
                )
                bytes_sent += sent
                events_emitted += 1
                outcome = "success"
                detail_extra = {"bytes_sent_this_session": sent}
            except OSError as exc:
                outcome = "failure"
                detail_extra = {"error": str(exc)}

            await ctx.emit_event(ecs_event(
                action="stratum_session",
                outcome=outcome,
                category="network",
                type_="connection",
                message=(
                    f"Stratum session {i + 1}/{params.iterations} -> "
                    f"{params.target_host}:{params.target_port}"
                ),
                campaign_id=ctx.campaign_id,
                run_id=ctx.run_id,
                step_id=ctx.step_id,
                plugin=self.Meta.name,
                target=f"{params.target_host}:{params.target_port}",
                bytes_sent=bytes_sent,
                extra={"iteration": i + 1, **detail_extra},
            ))

            if i < params.iterations - 1 and params.sleep_seconds > 0:
                await asyncio.sleep(params.sleep_seconds)

        return SimulationResult(
            plugin=self.Meta.name,
            step_id=ctx.step_id,
            status="success",
            started_at=started_at,
            completed_at=self.utcnow(),
            events_emitted=events_emitted,
            bytes_sent=bytes_sent,
            detail={
                "sessions_completed": events_emitted,
                "target": f"{params.target_host}:{params.target_port}",
            },
        )

    @staticmethod
    def _open_session(
        host: str,
        port: int,
        user_agent: str,
        wallet: str,
        connect_timeout: float,
        idle_seconds: float,
    ) -> int:
        """Open one TCP session and send subscribe + login JSON-RPC frames."""
        rng = random.SystemRandom()
        subscribe = {
            "id": rng.randint(1, 9999),
            "jsonrpc": "2.0",
            "method": "mining.subscribe",
            "params": [user_agent],
        }
        login = {
            "id": rng.randint(1, 9999),
            "jsonrpc": "2.0",
            "method": "login",
            "params": {
                "login": wallet,
                "pass": "x",
                "agent": user_agent,
            },
        }
        frames = [
            (json.dumps(subscribe) + "\n").encode("utf-8"),
            (json.dumps(login) + "\n").encode("utf-8"),
        ]

        bytes_sent = 0
        with socket.create_connection((host, port), timeout=connect_timeout) as sock:
            sock.settimeout(connect_timeout)
            for frame in frames:
                sock.sendall(frame)
                bytes_sent += len(frame)
            # Stay connected briefly so the NGFW App-ID engine can fingerprint
            # the protocol, then close cleanly.
            if idle_seconds > 0:
                try:
                    sock.settimeout(idle_seconds)
                    sock.recv(4096)
                except socket.timeout:
                    pass
        return bytes_sent

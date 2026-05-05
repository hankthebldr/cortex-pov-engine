"""
bulk_https_exfil — large outbound transfer simulation.

Streams a randomised payload of configurable size to an authorised HTTPS
endpoint to trigger Cortex EALs covering:

  * Anomalous Data Transfer Size (single session)
  * Outbound bytes exceeding host baseline
  * Long-lived HTTPS sessions

Every request carries the simulator telemetry headers, and the destination
is validated against the campaign target_allowlist.
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Optional
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field, field_validator

from ..base import BaseSimulation, SimulationContext, SimulationResult
from ..audit import ecs_event


logger = logging.getLogger("cortexsim.eal.plugins.bulk_https_exfil")


_MAX_TOTAL = 16 * 1024 * 1024 * 1024  # 16 GiB hard ceiling per single run.


class BulkHttpsExfilParams(BaseModel):
    target_url: str
    total_bytes: int = Field(default=512 * 1024 * 1024, ge=1, le=_MAX_TOTAL)
    chunk_bytes: int = Field(default=1 * 1024 * 1024, ge=1, le=64 * 1024 * 1024)
    method: str = Field(default="POST")
    request_timeout: float = Field(default=300.0, ge=1.0, le=3_600.0)
    request_count: int = Field(
        default=1, ge=1, le=64,
        description="If >1, the total volume is split across N requests.",
    )

    @field_validator("method")
    @classmethod
    def _method_upper(cls, v: str) -> str:
        v = v.upper()
        if v not in ("POST", "PUT"):
            raise ValueError("method must be POST or PUT")
        return v

    @field_validator("target_url")
    @classmethod
    def _url_format(cls, v: str) -> str:
        parsed = urlparse(v)
        if parsed.scheme not in ("http", "https"):
            raise ValueError("target_url must use http or https")
        if not parsed.hostname:
            raise ValueError("target_url must include a hostname")
        return v


def _random_chunks(total: int, chunk: int):
    """Yield random byte buffers totalling exactly *total* bytes."""
    rng = random.SystemRandom()
    sent = 0
    while sent < total:
        size = min(chunk, total - sent)
        yield bytes(rng.getrandbits(8) for _ in range(size))
        sent += size


class BulkHttpsExfil(BaseSimulation):
    class Meta:
        name = "bulk_https_exfil"
        version = "1.0.0"
        description = (
            "Streams configurable-size random payloads to an authorised HTTPS "
            "endpoint to trigger anomalous-bytes-out detections."
        )
        mitre_techniques = ["T1041", "T1567"]
        eal_targets = [
            "Anomalous Data Transfer Size",
            "Long-Lived HTTPS Session",
        ]
        params_model = BulkHttpsExfilParams

    async def run(self, ctx: SimulationContext) -> SimulationResult:
        params: BulkHttpsExfilParams = ctx.params  # type: ignore[assignment]
        started_at = self.utcnow()

        host = urlparse(params.target_url).hostname or ""
        getattr(ctx, "authorise")(host)

        if ctx.dry_run:
            await ctx.emit_event(ecs_event(
                action="bulk_exfil_dry_run",
                outcome="success",
                category="network",
                type_="info",
                message=(
                    f"DRY-RUN — {params.total_bytes} bytes planned across "
                    f"{params.request_count} request(s) to {params.target_url}"
                ),
                campaign_id=ctx.campaign_id,
                run_id=ctx.run_id,
                step_id=ctx.step_id,
                plugin=self.Meta.name,
                target=params.target_url,
            ))
            return SimulationResult(
                plugin=self.Meta.name,
                step_id=ctx.step_id,
                status="success",
                started_at=started_at,
                completed_at=self.utcnow(),
                events_emitted=1,
                detail={"dry_run": True, "total_bytes_planned": params.total_bytes},
            )

        per_request = params.total_bytes // params.request_count
        events_emitted = 0
        total_sent = 0

        async with httpx.AsyncClient(
            timeout=params.request_timeout,
            follow_redirects=False,
            verify=False,
        ) as client:
            for i in range(params.request_count):
                size = per_request if i < params.request_count - 1 else (
                    params.total_bytes - total_sent
                )

                async def _stream():  # noqa: D401 — local generator.
                    for chunk in _random_chunks(size, params.chunk_bytes):
                        yield chunk

                try:
                    resp = await client.request(
                        params.method,
                        params.target_url,
                        headers=ctx.telemetry_headers,
                        content=_stream(),
                    )
                    total_sent += size
                    events_emitted += 1
                    await ctx.emit_event(ecs_event(
                        action="bulk_exfil_request",
                        outcome="success",
                        category="network",
                        type_="connection",
                        message=f"streamed {size} bytes -> {params.target_url}",
                        campaign_id=ctx.campaign_id,
                        run_id=ctx.run_id,
                        step_id=ctx.step_id,
                        plugin=self.Meta.name,
                        target=params.target_url,
                        bytes_sent=total_sent,
                        extra={
                            "iteration": i + 1,
                            "request_bytes": size,
                            "status_code": resp.status_code,
                        },
                    ))
                except httpx.HTTPError as exc:
                    await ctx.emit_event(ecs_event(
                        action="bulk_exfil_request",
                        outcome="failure",
                        category="network",
                        type_="error",
                        message=f"upload failed: {exc}",
                        campaign_id=ctx.campaign_id,
                        run_id=ctx.run_id,
                        step_id=ctx.step_id,
                        plugin=self.Meta.name,
                        target=params.target_url,
                    ))

        return SimulationResult(
            plugin=self.Meta.name,
            step_id=ctx.step_id,
            status="success",
            started_at=started_at,
            completed_at=self.utcnow(),
            events_emitted=events_emitted,
            bytes_sent=total_sent,
            detail={
                "requests_completed": events_emitted,
                "total_bytes_sent": total_sent,
                "target_url": params.target_url,
            },
        )

"""
c2_http_beacon — periodic HTTP/S beaconing simulation.

Models malware that calls home on a fixed interval with anomalous
User-Agent and rotating URI patterns. Triggers Cortex EALs covering:

  * Unusual User-Agent strings on outbound HTTP
  * Periodic / regular interval beaconing
  * Suspicious URI patterns (DGA-style, base64-tail tokens)

Safety: every request is gated by the campaign's ``target_allowlist`` and
carries the ``X-Simulation-Run-ID`` header for SOC filtering.
"""

from __future__ import annotations

import asyncio
import logging
import random
import string
from datetime import timezone
from typing import Optional
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field, field_validator

from ..base import BaseSimulation, SimulationContext, SimulationResult
from ..audit import ecs_event


logger = logging.getLogger("cortexsim.eal.plugins.c2_http_beacon")


_DEFAULT_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; CortexSimBeacon/1.0)",
    "Mozilla/4.0 (compatible; MSIE 7.0; Windows NT 6.1; CortexSimBeacon/1.0)",
    "curl/7.68.0 CortexSim/1.0",
    "Go-http-client/1.1 CortexSimBeacon/1.0",
    "python-requests/2.28.0 CortexSimBeacon/1.0",
]


class C2HttpBeaconParams(BaseModel):
    target_url: str = Field(..., description="Beacon callback URL.")
    iterations: int = Field(default=10, ge=1, le=10_000)
    sleep_seconds: float = Field(default=30.0, ge=0.1, le=86_400.0)
    jitter_pct: float = Field(default=20.0, ge=0.0, le=90.0)
    method: str = Field(default="GET", description="HTTP method.")
    user_agents: list[str] = Field(default_factory=lambda: list(_DEFAULT_USER_AGENTS))
    dga_query_param: bool = Field(
        default=True,
        description="If true, append a random DGA-style query parameter to "
                    "each request to exercise URI-anomaly EALs.",
    )
    request_timeout: float = Field(default=10.0, ge=0.5, le=120.0)
    body_size_bytes: int = Field(default=0, ge=0, le=1_048_576)

    @field_validator("method")
    @classmethod
    def _method_upper(cls, v: str) -> str:
        v = v.upper().strip()
        if v not in ("GET", "POST", "HEAD"):
            raise ValueError("method must be GET, POST or HEAD")
        return v

    @field_validator("target_url")
    @classmethod
    def _target_url_well_formed(cls, v: str) -> str:
        parsed = urlparse(v)
        if parsed.scheme not in ("http", "https"):
            raise ValueError("target_url must use http or https")
        if not parsed.hostname:
            raise ValueError("target_url must include a hostname")
        return v


def _random_dga_token(length: int = 12) -> str:
    rng = random.SystemRandom()
    alphabet = string.ascii_lowercase + string.digits
    return "".join(rng.choice(alphabet) for _ in range(length))


def _jittered_sleep(base: float, pct: float) -> float:
    if pct <= 0:
        return base
    rng = random.SystemRandom()
    delta = base * (pct / 100.0)
    return max(0.0, base + rng.uniform(-delta, delta))


class C2HttpBeacon(BaseSimulation):
    class Meta:
        name = "c2_http_beacon"
        version = "1.0.0"
        description = (
            "Simulated HTTP/S command-and-control beacon with rotating "
            "User-Agent strings and configurable jitter."
        )
        mitre_techniques = ["T1071.001", "T1071", "T1568"]
        eal_targets = [
            "Unusual User-Agent",
            "Periodic Beaconing",
            "DGA-style URI",
        ]
        params_model = C2HttpBeaconParams

    async def run(self, ctx: SimulationContext) -> SimulationResult:
        params: C2HttpBeaconParams = ctx.params  # type: ignore[assignment]
        started_at = self.utcnow()

        host = urlparse(params.target_url).hostname or ""
        # Authorise the target up front; the executor injected the policy.
        getattr(ctx, "authorise")(host)

        events_emitted = 0
        bytes_sent = 0
        rng = random.SystemRandom()

        if ctx.dry_run:
            await ctx.emit_event(ecs_event(
                action="c2_beacon_dry_run",
                outcome="success",
                category="network",
                type_="info",
                message=f"DRY-RUN — {params.iterations} planned beacons to {params.target_url}",
                campaign_id=ctx.campaign_id,
                run_id=ctx.run_id,
                step_id=ctx.step_id,
                plugin=self.Meta.name,
                target=params.target_url,
                extra={"iterations": params.iterations, "sleep_seconds": params.sleep_seconds},
            ))
            return SimulationResult(
                plugin=self.Meta.name,
                step_id=ctx.step_id,
                status="success",
                started_at=started_at,
                completed_at=self.utcnow(),
                events_emitted=1,
                bytes_sent=0,
                detail={"dry_run": True, "iterations_planned": params.iterations},
            )

        async with httpx.AsyncClient(
            timeout=params.request_timeout,
            follow_redirects=False,
            verify=False,  # POVs frequently MitM through customer NGFW with self-signed cert
        ) as client:
            for i in range(params.iterations):
                ua = rng.choice(params.user_agents) if params.user_agents else "CortexSim/1.0"
                headers = {**ctx.telemetry_headers, "User-Agent": ua}

                url = params.target_url
                if params.dga_query_param:
                    sep = "&" if "?" in url else "?"
                    url = f"{url}{sep}q={_random_dga_token()}"

                body: Optional[bytes] = None
                if params.method == "POST" and params.body_size_bytes > 0:
                    body = bytes(rng.getrandbits(8) for _ in range(params.body_size_bytes))

                try:
                    resp = await client.request(
                        params.method, url, headers=headers, content=body,
                    )
                    bytes_sent += params.body_size_bytes + len(url.encode())
                    await ctx.emit_event(ecs_event(
                        action="c2_beacon_request",
                        outcome="success",
                        category="network",
                        type_="connection",
                        message=f"beacon {i + 1}/{params.iterations} -> {url}",
                        campaign_id=ctx.campaign_id,
                        run_id=ctx.run_id,
                        step_id=ctx.step_id,
                        plugin=self.Meta.name,
                        target=url,
                        bytes_sent=bytes_sent,
                        extra={
                            "iteration": i + 1,
                            "user_agent": ua,
                            "status_code": resp.status_code,
                            "method": params.method,
                        },
                    ))
                    events_emitted += 1
                except httpx.HTTPError as exc:
                    await ctx.emit_event(ecs_event(
                        action="c2_beacon_request",
                        outcome="failure",
                        category="network",
                        type_="error",
                        message=f"beacon {i + 1}/{params.iterations} failed: {exc}",
                        campaign_id=ctx.campaign_id,
                        run_id=ctx.run_id,
                        step_id=ctx.step_id,
                        plugin=self.Meta.name,
                        target=url,
                        extra={"iteration": i + 1, "error": str(exc)},
                    ))

                if i < params.iterations - 1:
                    await asyncio.sleep(_jittered_sleep(params.sleep_seconds, params.jitter_pct))

        return SimulationResult(
            plugin=self.Meta.name,
            step_id=ctx.step_id,
            status="success",
            started_at=started_at,
            completed_at=self.utcnow(),
            events_emitted=events_emitted,
            bytes_sent=bytes_sent,
            detail={
                "iterations_completed": events_emitted,
                "target_url": params.target_url,
                "method": params.method,
            },
        )

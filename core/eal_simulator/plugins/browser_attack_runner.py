"""
browser_attack_runner — EAL plugin that drives ``cortex-browser-attacker``
against the customer's deployed Prisma Browser (or vanilla Chromium).

Same shell-out-to-CLI pattern as ``airs_prompt_attack``: subprocess-launch
the binary, parse line-buffered JSONL on stdout, forward every record
into the simulator's ECS audit pipeline.

The customer's Prisma Browser tenant forwards its own telemetry to the
customer XSIAM tenant via the existing PB→XSIAM path. This plugin just
*produces the activity* — it does not bridge PB to XSIAM.

Safety: the EAL plugin authorises the configured ``allowlist_host``
(usually a single staging host that the browser campaign reaches) via
``ctx.authorise()``. The campaign YAML itself also declares a
``target_allowlist`` so navigation actions are double-gated.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import shutil
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator

from ..audit import ecs_event
from ..base import BaseSimulation, SimulationContext, SimulationResult


logger = logging.getLogger("cortexsim.eal.plugins.browser_attack_runner")


try:  # pragma: no cover - import-time path
    from cortex_browser_attacker import events as cba_events  # type: ignore

    _CBA_EVENTS_AVAILABLE = True
except ImportError:  # pragma: no cover
    cba_events = None  # type: ignore
    _CBA_EVENTS_AVAILABLE = False


_DEFAULT_BIN = "cortex-browser-attacker"


class BrowserAttackRunnerParams(BaseModel):
    campaign_path: str = Field(
        ...,
        description="Path to the browser campaign YAML to execute.",
    )
    allowlist_host: str = Field(
        ...,
        description="The primary host the browser actions navigate to; "
                    "checked against the EAL campaign's target_allowlist.",
    )
    browser_channel: str = Field(
        default="chromium",
        description="'prisma' for managed Prisma Browser, 'chromium' "
                    "for vanilla, 'stub' for unit tests.",
    )
    headless: bool = Field(default=True)
    timeout_seconds: float = Field(default=600.0, ge=1.0, le=7200.0)
    binary: Optional[str] = Field(
        default=None,
        description="Override the cortex-browser-attacker binary path.",
    )

    @field_validator("browser_channel")
    @classmethod
    def _channel_known(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ("prisma", "chromium", "stub"):
            raise ValueError("browser_channel must be prisma | chromium | stub")
        return v

    @field_validator("campaign_path")
    @classmethod
    def _campaign_path_safe(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("campaign_path required")
        if "\x00" in v:
            raise ValueError("campaign_path must not contain null bytes")
        return v


def _resolve_binary(override: Optional[str]) -> Optional[str]:
    if override:
        if os.path.isabs(override):
            return override if os.path.isfile(override) else None
        return shutil.which(override)
    return shutil.which(_DEFAULT_BIN)


def _build_argv(params: BrowserAttackRunnerParams, binary: str) -> list[str]:
    argv = [
        binary, "run",
        "--campaign", params.campaign_path,
        "--browser-channel", params.browser_channel,
        "--out", "-",
    ]
    if params.headless:
        argv.append("--headless")
    else:
        argv.append("--no-headless")
    # The CLI's --live flag flips dry_run=false; we always ask for it.
    # The campaign YAML still has the final say via its Pydantic
    # validator (refuses live without authorisation block).
    argv.append("--live")
    return argv


class BrowserAttackRunner(BaseSimulation):
    class Meta:
        name = "browser_attack_runner"
        version = "1.0.0"
        description = (
            "Drives cortex-browser-attacker (Playwright + Chromium / Prisma "
            "Browser) against an authorised target, forwarding every action "
            "attempt into the EAL audit pipeline as an ECS event."
        )
        mitre_techniques = ["T1552", "T1189", "T1176", "T1567", "T1113"]
        eal_targets = [
            "Prisma Browser — credential paste into untrusted origin",
            "Prisma Browser — drive-by download",
            "Prisma Browser — risky extension install policy",
            "Prisma Browser — cross-origin SaaS paste DLP",
            "Prisma Browser — screen-capture policy",
        ]
        params_model = BrowserAttackRunnerParams

    async def run(self, ctx: SimulationContext) -> SimulationResult:
        params: BrowserAttackRunnerParams = ctx.params  # type: ignore[assignment]
        started_at = self.utcnow()

        getattr(ctx, "authorise")(params.allowlist_host)

        # Fail fast if the campaign file doesn't exist.
        if not Path(params.campaign_path).exists():
            return SimulationResult(
                plugin=self.Meta.name,
                step_id=ctx.step_id,
                status="error",
                started_at=started_at,
                completed_at=self.utcnow(),
                events_emitted=0,
                error=f"campaign_path not found: {params.campaign_path}",
            )

        binary = _resolve_binary(params.binary)
        if binary is None:
            return SimulationResult(
                plugin=self.Meta.name,
                step_id=ctx.step_id,
                status="error",
                started_at=started_at,
                completed_at=self.utcnow(),
                events_emitted=0,
                error=(
                    f"binary '{params.binary or _DEFAULT_BIN}' not found on PATH; "
                    "install cortex-browser-attacker[playwright] and run "
                    "'playwright install chromium'"
                ),
            )

        if ctx.dry_run:
            await ctx.emit_event(ecs_event(
                action="browser_attack_runner_dry_run",
                outcome="success",
                category="process",
                type_="info",
                message=(
                    f"DRY-RUN — would invoke {binary} against "
                    f"{params.campaign_path} (channel={params.browser_channel})"
                ),
                campaign_id=ctx.campaign_id,
                run_id=ctx.run_id,
                step_id=ctx.step_id,
                plugin=self.Meta.name,
                target=params.allowlist_host,
                extra={
                    "campaign_path": params.campaign_path,
                    "browser_channel": params.browser_channel,
                },
            ))
            return SimulationResult(
                plugin=self.Meta.name,
                step_id=ctx.step_id,
                status="success",
                started_at=started_at,
                completed_at=self.utcnow(),
                events_emitted=1,
                detail={"dry_run": True, "binary": binary},
            )

        argv = _build_argv(params, binary)
        logger.info("browser_attack_runner invoking: %s", shlex.join(argv))

        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Per-context counters mirrored from the airs plugin pattern.
        setattr(ctx, "_browser_events_emitted", 0)
        setattr(ctx, "_browser_actions_run", 0)
        setattr(ctx, "_browser_success_count", 0)
        setattr(ctx, "_browser_blocked_count", 0)
        setattr(ctx, "_browser_failure_count", 0)

        try:
            await asyncio.wait_for(
                self._consume_stdout(proc, ctx),
                timeout=params.timeout_seconds,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return SimulationResult(
                plugin=self.Meta.name,
                step_id=ctx.step_id,
                status="error",
                started_at=started_at,
                completed_at=self.utcnow(),
                events_emitted=getattr(ctx, "_browser_events_emitted", 0),
                error=f"timeout after {params.timeout_seconds}s",
            )

        rc = await proc.wait()
        stderr_blob = (await proc.stderr.read()).decode("utf-8", errors="replace") if proc.stderr else ""

        summary_obj = None
        for line in reversed(stderr_blob.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                summary_obj = json.loads(line).get("summary")
                break
            except json.JSONDecodeError:
                continue

        return SimulationResult(
            plugin=self.Meta.name,
            step_id=ctx.step_id,
            status="success" if rc == 0 else "error",
            started_at=started_at,
            completed_at=self.utcnow(),
            events_emitted=getattr(ctx, "_browser_events_emitted", 0),
            detail={
                "binary": binary,
                "exit_code": rc,
                "actions_run": getattr(ctx, "_browser_actions_run", 0),
                "success_count": getattr(ctx, "_browser_success_count", 0),
                "blocked_count": getattr(ctx, "_browser_blocked_count", 0),
                "failure_count": getattr(ctx, "_browser_failure_count", 0),
                "summary": summary_obj,
                "campaign_path": params.campaign_path,
                "browser_channel": params.browser_channel,
            },
            error=stderr_blob[-1024:].strip() if rc != 0 else None,
        )

    # ------------------------------------------------------------------

    async def _consume_stdout(
        self,
        proc: asyncio.subprocess.Process,
        ctx: SimulationContext,
    ) -> None:
        if proc.stdout is None:  # pragma: no cover - defensive
            return

        async for raw_line in proc.stdout:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("browser_attack_runner: non-JSON stdout: %s", line[:128])
                continue

            entry_type = payload.get("entry_type")
            if entry_type == "run_meta":
                event = (
                    cba_events.run_meta_to_ecs(
                        payload,
                        campaign_id=ctx.campaign_id,
                        run_id=ctx.run_id,
                        step_id=ctx.step_id,
                    )
                    if _CBA_EVENTS_AVAILABLE else
                    _fallback_event("browser_campaign_started", payload, ctx)
                )
            else:
                event = (
                    cba_events.action_result_to_ecs(
                        payload,
                        campaign_id=ctx.campaign_id,
                        run_id=ctx.run_id,
                        step_id=ctx.step_id,
                    )
                    if _CBA_EVENTS_AVAILABLE else
                    _fallback_event(
                        f"browser_{payload.get('action_name', 'unknown')}",
                        payload, ctx,
                    )
                )
                ctx._browser_actions_run += 1  # type: ignore[attr-defined]
                outcome = payload.get("outcome")
                if outcome == "success":
                    ctx._browser_success_count += 1  # type: ignore[attr-defined]
                elif outcome == "blocked":
                    ctx._browser_blocked_count += 1  # type: ignore[attr-defined]
                else:
                    ctx._browser_failure_count += 1  # type: ignore[attr-defined]

            await ctx.emit_event(event)
            ctx._browser_events_emitted += 1  # type: ignore[attr-defined]


def _fallback_event(action: str, payload: dict, ctx: SimulationContext) -> dict:
    return ecs_event(
        action=action,
        outcome="success",
        category="web",
        type_="info",
        message=f"{action} payload",
        campaign_id=ctx.campaign_id,
        run_id=ctx.run_id,
        step_id=ctx.step_id,
        plugin="browser_attack_runner",
        extra={"raw": payload},
    )

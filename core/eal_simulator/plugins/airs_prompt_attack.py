"""
airs_prompt_attack — EAL plugin that drives ``cortex-prompt-attacker``
against an AIRS validation target (typically ``cortex-vulnerable-llm``).

Architecture: this plugin is the first "attacker-shells-out" plugin in
the EAL family. The other built-ins are in-process generators (httpx,
sockets, DNS); this one ``subprocess.run``s the prompt-attacker CLI,
parses its line-buffered JSONL output, and forwards every record into
the simulator's ECS audit pipeline.

Safety: ``target_url`` is parsed and the host is checked against the
campaign's ``target_allowlist`` via ``ctx.authorise(host)`` exactly like
the other HTTP-emitting plugins.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import os
import shlex
import shutil
import subprocess
import sys
from typing import Optional
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator

from ..audit import ecs_event
from ..base import BaseSimulation, SimulationContext, SimulationResult


logger = logging.getLogger("cortexsim.eal.plugins.airs_prompt_attack")


@dataclasses.dataclass
class _Counters:
    """Run-scoped tallies returned from ``_consume_stdout`` (no ctx mutation)."""

    events_emitted: int = 0
    attempts_run: int = 0
    vuln_count: int = 0
    clean_count: int = 0
    error_count: int = 0


# Optional import — only needed at runtime when the attacker is installed.
try:  # pragma: no cover - import-time path
    from cortex_prompt_attacker import events as cpa_events  # type: ignore

    _CPA_EVENTS_AVAILABLE = True
except ImportError:  # pragma: no cover - import-time path
    cpa_events = None  # type: ignore
    _CPA_EVENTS_AVAILABLE = False


_DEFAULT_BIN = "cortex-prompt-attacker"


class AirsPromptAttackParams(BaseModel):
    target_url: str = Field(..., description="AIRS target URL the attacker POSTs to.")
    probes_dir: str = Field(..., description="Directory of probe YAMLs (or comma-sep paths).")
    mutators: list[str] = Field(
        default_factory=list,
        description="Default mutator chain (probes can override).",
    )
    scorers: list[str] = Field(
        default_factory=list,
        description="Default scorer list (probes can override).",
    )
    iterations: int = Field(default=1, ge=1, le=200)
    timeout_seconds: float = Field(default=120.0, ge=1.0, le=3600.0)
    request_timeout: float = Field(default=30.0, ge=1.0, le=300.0)
    extra_headers: dict[str, str] = Field(default_factory=dict)
    insecure_tls: bool = Field(default=False)
    binary: Optional[str] = Field(
        default=None,
        description="Override the cortex-prompt-attacker binary path.",
    )

    @field_validator("target_url")
    @classmethod
    def _url_format(cls, v: str) -> str:
        parsed = urlparse(v)
        if parsed.scheme not in ("http", "https"):
            raise ValueError("target_url must use http or https")
        if not parsed.hostname:
            raise ValueError("target_url must include a hostname")
        return v


def _resolve_binary(override: Optional[str]) -> Optional[str]:
    if override:
        if os.path.isabs(override):
            return override if os.path.isfile(override) else None
        return shutil.which(override)
    return shutil.which(_DEFAULT_BIN)


def _build_argv(params: AirsPromptAttackParams, binary: str) -> list[str]:
    argv = [
        binary, "run",
        "--probes", params.probes_dir,
        "--target-url", params.target_url,
        "--iterations", str(params.iterations),
        "--timeout", str(params.request_timeout),
        "--out", "-",
    ]
    if params.mutators:
        argv += ["--mutators", ",".join(params.mutators)]
    if params.scorers:
        argv += ["--scorers", ",".join(params.scorers)]
    for k, v in params.extra_headers.items():
        argv += ["--header", f"{k}={v}"]
    if params.insecure_tls:
        argv.append("--insecure")
    return argv


class AirsPromptAttack(BaseSimulation):
    class Meta:
        name = "airs_prompt_attack"
        version = "1.0.0"
        description = (
            "Drives cortex-prompt-attacker against an AIRS target, forwarding "
            "every probe attempt into the EAL audit pipeline as ECS events."
        )
        mitre_techniques = ["T1656", "T1059", "T1499"]
        eal_targets = [
            "AIRS Prompt Injection",
            "AIRS Tool-Call Abuse",
            "AIRS RAG Poisoning",
            "AIRS Token DoS",
        ]
        params_model = AirsPromptAttackParams

    async def run(self, ctx: SimulationContext) -> SimulationResult:
        params: AirsPromptAttackParams = ctx.params  # type: ignore[assignment]
        started_at = self.utcnow()

        host = urlparse(params.target_url).hostname or ""
        getattr(ctx, "authorise")(host)

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
                    "install cortex-prompt-attacker (sources/cortex-prompt-attacker)"
                ),
            )

        if ctx.dry_run:
            await ctx.emit_event(ecs_event(
                action="airs_prompt_attack_dry_run",
                outcome="success",
                category="process",
                type_="info",
                message=(
                    f"DRY-RUN — would invoke {binary} against {params.target_url} "
                    f"with probes={params.probes_dir}"
                ),
                campaign_id=ctx.campaign_id,
                run_id=ctx.run_id,
                step_id=ctx.step_id,
                plugin=self.Meta.name,
                target=params.target_url,
                extra={
                    "probes_dir": params.probes_dir,
                    "iterations": params.iterations,
                    "mutators": params.mutators,
                    "scorers": params.scorers,
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
        logger.info("airs_prompt_attack invoking: %s", shlex.join(argv))

        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        counters = _Counters()
        try:
            await asyncio.wait_for(
                self._consume_stdout(proc, ctx, counters),
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
                events_emitted=counters.events_emitted,
                error=f"timeout after {params.timeout_seconds}s",
            )

        rc = await proc.wait()
        stderr_blob = (await proc.stderr.read()).decode("utf-8", errors="replace") if proc.stderr else ""

        # Counters are returned from _consume_stdout (no ctx mutation, EAL-G08).
        events_emitted = counters.events_emitted
        attempts_run = counters.attempts_run
        vuln_count = counters.vuln_count
        clean_count = counters.clean_count
        error_count = counters.error_count

        # Parse the runner summary out of stderr (last JSON line).
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
            events_emitted=events_emitted,
            detail={
                "binary": binary,
                "exit_code": rc,
                "attempts_run": attempts_run,
                "vuln_count": vuln_count,
                "clean_count": clean_count,
                "error_count": error_count,
                "summary": summary_obj,
                "target_url": params.target_url,
            },
            error=stderr_blob[-1024:].strip() if rc != 0 else None,
        )

    # ------------------------------------------------------------------
    # Stdout pump — translates JSONL → ECS via cortex_prompt_attacker.events
    # ------------------------------------------------------------------

    async def _consume_stdout(
        self,
        proc: asyncio.subprocess.Process,
        ctx: SimulationContext,
        counters: "_Counters",
    ) -> None:
        """Drain the attacker's JSONL stdout, emit ECS events, tally counters.

        Counters are accumulated on the passed-in ``_Counters`` instance and
        consumed by ``run`` — no ``ctx`` mutation (EAL-G08).
        """
        if proc.stdout is None:  # pragma: no cover - defensive
            return

        async for raw_line in proc.stdout:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("airs_prompt_attack: non-JSON stdout: %s", line[:128])
                continue

            entry_type = payload.get("entry_type")
            if entry_type == "run_meta":
                event = (
                    cpa_events.run_meta_to_ecs(
                        payload,
                        campaign_id=ctx.campaign_id,
                        run_id=ctx.run_id,
                        step_id=ctx.step_id,
                    )
                    if _CPA_EVENTS_AVAILABLE else
                    _fallback_event("airs_probe_run_started", payload, ctx)
                )
            else:
                event = (
                    cpa_events.attempt_to_ecs(
                        payload,
                        campaign_id=ctx.campaign_id,
                        run_id=ctx.run_id,
                        step_id=ctx.step_id,
                    )
                    if _CPA_EVENTS_AVAILABLE else
                    _fallback_event("airs_probe_attempt", payload, ctx)
                )
                counters.attempts_run += 1
                outcome = payload.get("outcome")
                if outcome == "vuln":
                    counters.vuln_count += 1
                elif outcome == "clean":
                    counters.clean_count += 1
                else:
                    counters.error_count += 1

            await ctx.emit_event(event)
            counters.events_emitted += 1


def _fallback_event(action: str, payload: dict, ctx: SimulationContext) -> dict:
    """Emit a minimal ECS event when ``cortex_prompt_attacker.events`` is
    unavailable (e.g. the package isn't on sys.path inside the test env)."""
    return ecs_event(
        action=action,
        outcome="success",
        category="network",
        type_="info",
        message=f"{action} payload",
        campaign_id=ctx.campaign_id,
        run_id=ctx.run_id,
        step_id=ctx.step_id,
        plugin="airs_prompt_attack",
        extra={"raw": payload},
    )

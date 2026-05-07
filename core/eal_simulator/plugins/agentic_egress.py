"""
agentic_egress — emulates consumer fetches of agentic supply-chain
artifacts (MCP servers / packages, Claude skills, VS Code / Chrome
extensions, PyPI packages) so the customer's NGFW sees the egress
shape and matches App-ID + URL filtering.

The artifact source is the in-tree
``sources/cortex-malicious-agentic-pack/`` tree. We never stand up a
real malicious staging server; we POST or GET against a URL the
operator supplies, so the campaign's ``target_allowlist`` is the only
thing on the wire that decides whether traffic flows.

The plugin tarballs the requested component directory at request time
and posts it. For ``component=pypi_mirror`` we additionally do a
GET-then-POST sequence shaped like ``pip download`` so URL-filter
rules that key on the GET probe also fire.

Components and their User-Agent fingerprint (how a real client would
look on the wire):

  ============== ========================== =======================================
  component      directory                  User-Agent
  ============== ========================== =======================================
  mcp_server     mcp/<name>                 claude-desktop/<ver> mcp-client/0.1
  mcp_package    mcp/<name>                 npm/10.x.x node/v22.0.0
  pypi_mirror    pypi/<name>                pip/24.x python/3.11
  claude_skill   claude-skills/<name>       claude-desktop/<ver> skills/0.1
  vscode_ext     vscode/<name>              VSCode/1.85.0 (vsx-fetch)
  chrome_ext     chrome/<name>              Chrome/120.0.0.0 (extension-installer)
  ============== ========================== =======================================
"""

from __future__ import annotations

import asyncio
import dataclasses
import io
import logging
import os
import re
import tarfile
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field, field_validator

from ..audit import ecs_event
from ..base import BaseSimulation, SimulationContext, SimulationResult


logger = logging.getLogger("cortexsim.eal.plugins.agentic_egress")


# --------------------------------------------------------------------------
# Component → consumer fingerprint
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class _Component:
    name: str
    subdir: str            # path under cortex-malicious-agentic-pack/
    user_agent: str
    artifact_suffix: str   # the synthesised filename suffix
    method: str = "POST"   # default; pypi_mirror flips to GET+POST


_COMPONENTS: dict[str, _Component] = {
    "mcp_server": _Component(
        name="mcp_server", subdir="mcp",
        user_agent="claude-desktop/0.7.0 mcp-client/0.1",
        artifact_suffix=".tar.gz",
    ),
    "mcp_package": _Component(
        name="mcp_package", subdir="mcp",
        user_agent="npm/10.5.0 node/v22.0.0 linux x64 workspaces/false",
        artifact_suffix=".tgz",
    ),
    "pypi_mirror": _Component(
        name="pypi_mirror", subdir="pypi",
        user_agent="pip/24.0 {python_implementation} python/{python_version}",
        artifact_suffix=".tar.gz",
    ),
    "claude_skill": _Component(
        name="claude_skill", subdir="claude-skills",
        user_agent="claude-desktop/0.7.0 skills/0.1",
        artifact_suffix=".skill",
    ),
    "vscode_ext": _Component(
        name="vscode_ext", subdir="vscode",
        user_agent="VSCode/1.85.0 (vsx-fetch)",
        artifact_suffix=".vsix",
    ),
    "chrome_ext": _Component(
        name="chrome_ext", subdir="chrome",
        user_agent="Chrome/120.0.0.0 (extension-installer)",
        artifact_suffix=".crx",
    ),
}


def _list_components() -> list[str]:
    return sorted(_COMPONENTS)


# --------------------------------------------------------------------------
# Pydantic params
# --------------------------------------------------------------------------


_ARTIFACT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class AgenticEgressParams(BaseModel):
    target_url: str = Field(
        ...,
        description="Staging URL the consumer client would hit. The plugin "
                    "POSTs / GETs against this URL — the host must be in the "
                    "campaign target_allowlist.",
    )
    component: str = Field(..., description="One of mcp_server | mcp_package | "
                                             "pypi_mirror | claude_skill | "
                                             "vscode_ext | chrome_ext.")
    artifact_name: str = Field(
        ...,
        description="Subdirectory under cortex-malicious-agentic-pack/<component-dir>/ "
                    "to package and send (e.g. anthroopic-calculator).",
    )
    iterations: int = Field(default=1, ge=1, le=50)
    sleep_seconds: float = Field(default=0.0, ge=0.0, le=600.0)
    request_timeout: float = Field(default=15.0, ge=1.0, le=300.0)
    pack_root: Optional[str] = Field(
        default=None,
        description="Override the on-disk path to cortex-malicious-agentic-pack/ "
                    "(defaults to the in-tree sibling).",
    )

    @field_validator("component")
    @classmethod
    def _component_known(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in _COMPONENTS:
            raise ValueError(
                f"component must be one of {_list_components()}, got '{v}'"
            )
        return v

    @field_validator("artifact_name")
    @classmethod
    def _artifact_name_safe(cls, v: str) -> str:
        v = v.strip()
        if not _ARTIFACT_NAME_RE.match(v):
            raise ValueError(
                "artifact_name must match [A-Za-z0-9][A-Za-z0-9._-]* (no path traversal)"
            )
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


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _resolve_pack_root(override: Optional[str]) -> Path:
    """Return the on-disk root of cortex-malicious-agentic-pack/.

    Order of precedence: explicit param override → CORTEXSIM_BASE_DIR env →
    walk up from this file to find the in-tree sibling.
    """
    if override:
        return Path(override).resolve()

    base_env = os.environ.get("CORTEXSIM_BASE_DIR")
    if base_env:
        candidate = Path(base_env) / "sources" / "cortex-malicious-agentic-pack"
        if candidate.is_dir():
            return candidate.resolve()

    here = Path(__file__).resolve()
    # core/eal_simulator/plugins/agentic_egress.py → .../core/eal_simulator/plugins
    # walk up to repo root (4 parents) then into sources/.
    for parent in here.parents:
        candidate = parent / "sources" / "cortex-malicious-agentic-pack"
        if candidate.is_dir():
            return candidate.resolve()
    raise FileNotFoundError(
        "could not locate cortex-malicious-agentic-pack/ on disk; pass "
        "pack_root explicitly or set CORTEXSIM_BASE_DIR"
    )


def _resolve_artifact_dir(pack_root: Path, component: _Component, artifact_name: str) -> Path:
    """Resolve and validate the artifact path, refusing path traversal."""
    component_dir = (pack_root / component.subdir).resolve()
    artifact_dir = (component_dir / artifact_name).resolve()
    # Guarantee the resolved path stays inside the component directory.
    if component_dir not in artifact_dir.parents and artifact_dir != component_dir:
        raise ValueError(
            f"artifact path escapes component directory: {artifact_dir}"
        )
    if not artifact_dir.is_dir():
        raise FileNotFoundError(f"artifact directory not found: {artifact_dir}")
    return artifact_dir


def _tarball_directory(artifact_dir: Path, artifact_name: str) -> bytes:
    """Stream the directory to an in-memory gzipped tar."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(str(artifact_dir), arcname=artifact_name)
    return buf.getvalue()


def _format_user_agent(template: str) -> str:
    import platform

    return template.format(
        python_implementation=platform.python_implementation(),
        python_version=platform.python_version(),
    )


# --------------------------------------------------------------------------
# Plugin
# --------------------------------------------------------------------------


class AgenticEgress(BaseSimulation):
    class Meta:
        name = "agentic_egress"
        version = "1.0.0"
        description = (
            "Emulates an agentic-AI consumer client (Claude Desktop, Cursor, "
            "pip, VS Code Marketplace, Chrome Web Store) fetching a malicious "
            "supply-chain artifact, so the customer NGFW sees the egress."
        )
        mitre_techniques = ["T1195", "T1195.002", "T1176", "T1059"]
        eal_targets = [
            "KOI — typosquat package fetch",
            "KOI — extension marketplace risk",
            "KOI — agentic skill fetch with hidden injection",
            "NGFW EAL — npm/pip/marketplace App-ID match",
        ]
        params_model = AgenticEgressParams

    async def run(self, ctx: SimulationContext) -> SimulationResult:
        params: AgenticEgressParams = ctx.params  # type: ignore[assignment]
        started_at = self.utcnow()

        component = _COMPONENTS[params.component]
        host = urlparse(params.target_url).hostname or ""
        getattr(ctx, "authorise")(host)

        # Resolve and tarball up front so a missing artifact fails fast even
        # in dry-run (the plugin is only useful if it can find the pack).
        try:
            pack_root = _resolve_pack_root(params.pack_root)
            artifact_dir = _resolve_artifact_dir(
                pack_root, component, params.artifact_name,
            )
            artifact_bytes = _tarball_directory(artifact_dir, params.artifact_name)
        except (FileNotFoundError, ValueError) as exc:
            return SimulationResult(
                plugin=self.Meta.name,
                step_id=ctx.step_id,
                status="error",
                started_at=started_at,
                completed_at=self.utcnow(),
                events_emitted=0,
                error=f"artifact_resolution_failed: {exc}",
            )

        artifact_filename = f"{params.artifact_name}{component.artifact_suffix}"

        if ctx.dry_run:
            await ctx.emit_event(ecs_event(
                action="agentic_egress_dry_run",
                outcome="success",
                category="network",
                type_="info",
                message=(
                    f"DRY-RUN — would fetch {artifact_filename} ({len(artifact_bytes)} bytes) "
                    f"as {component.name} from {params.target_url}"
                ),
                campaign_id=ctx.campaign_id,
                run_id=ctx.run_id,
                step_id=ctx.step_id,
                plugin=self.Meta.name,
                target=params.target_url,
                extra={
                    "component": component.name,
                    "artifact": artifact_filename,
                    "artifact_bytes": len(artifact_bytes),
                    "iterations_planned": params.iterations,
                },
            ))
            return SimulationResult(
                plugin=self.Meta.name,
                step_id=ctx.step_id,
                status="success",
                started_at=started_at,
                completed_at=self.utcnow(),
                events_emitted=1,
                bytes_sent=0,
                detail={
                    "dry_run": True,
                    "component": component.name,
                    "artifact": artifact_filename,
                    "artifact_bytes": len(artifact_bytes),
                },
            )

        events_emitted = 0
        bytes_sent = 0
        client = self._build_client(params, component)

        try:
            for i in range(params.iterations):
                events_for_iter, bytes_for_iter = await self._send_one(
                    client, params, component, artifact_filename, artifact_bytes,
                    ctx, iteration=i + 1,
                )
                events_emitted += events_for_iter
                bytes_sent += bytes_for_iter

                if i < params.iterations - 1 and params.sleep_seconds > 0:
                    await asyncio.sleep(params.sleep_seconds)
        finally:
            await client.aclose()

        return SimulationResult(
            plugin=self.Meta.name,
            step_id=ctx.step_id,
            status="success",
            started_at=started_at,
            completed_at=self.utcnow(),
            events_emitted=events_emitted,
            bytes_sent=bytes_sent,
            detail={
                "component": component.name,
                "artifact": artifact_filename,
                "iterations_completed": params.iterations,
                "target": host,
            },
        )

    # ----------------------------------------------------------------------
    # Internals — split out so unit tests patch them cleanly
    # ----------------------------------------------------------------------

    def _build_client(
        self,
        params: AgenticEgressParams,
        component: _Component,
    ) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=params.request_timeout,
            verify=False,
            follow_redirects=False,
            headers={"user-agent": _format_user_agent(component.user_agent)},
        )

    async def _send_one(
        self,
        client: httpx.AsyncClient,
        params: AgenticEgressParams,
        component: _Component,
        artifact_filename: str,
        artifact_bytes: bytes,
        ctx: SimulationContext,
        *,
        iteration: int,
    ) -> tuple[int, int]:
        """Send one (or, for pypi_mirror, two) requests. Returns (events, bytes)."""
        events = 0
        sent = 0
        sep = "&" if "?" in params.target_url else "?"
        artifact_url = (
            f"{params.target_url}"
            f"{'/' if not params.target_url.endswith('/') else ''}"
            f"{artifact_filename}"
        )

        headers = {
            **{k.lower(): v for k, v in ctx.telemetry_headers.items()},
            "x-simulation-run-id": f"{ctx.simulation_run_id}-i{iteration}",
        }

        # pypi_mirror does the GET-probe-then-POST shape that real `pip
        # download` makes against a custom index-url. Other components do
        # a single fetch.
        if component.name == "pypi_mirror":
            try:
                resp = await client.get(
                    f"{params.target_url}{sep}name={params.artifact_name}",
                    headers=headers,
                )
                events += 1
                await ctx.emit_event(ecs_event(
                    action="agentic_egress_index_probe",
                    outcome="success",
                    category="network",
                    type_="connection",
                    message=(
                        f"index probe (component={component.name}) -> "
                        f"{params.target_url} status={resp.status_code}"
                    ),
                    campaign_id=ctx.campaign_id,
                    run_id=ctx.run_id,
                    step_id=ctx.step_id,
                    plugin=self.Meta.name,
                    target=params.target_url,
                    extra={
                        "iteration": iteration,
                        "component": component.name,
                        "status_code": resp.status_code,
                    },
                ))
            except httpx.HTTPError as exc:
                events += 1
                await self._emit_failure_event(ctx, params, component, iteration,
                                               exc, kind="agentic_egress_index_probe")

        # The actual artifact fetch. We POST the bytes (a real client
        # GETs them; this plugin always POSTs so the artifact body is
        # what the NGFW DLP / SCA layer inspects).
        try:
            resp = await client.request(
                "POST",
                artifact_url,
                headers={**headers, "content-type": "application/octet-stream"},
                content=artifact_bytes,
            )
            events += 1
            sent += len(artifact_bytes)
            await ctx.emit_event(ecs_event(
                action="agentic_egress_artifact_fetch",
                outcome="success",
                category="network",
                type_="connection",
                message=(
                    f"fetch component={component.name} {artifact_filename} "
                    f"-> {params.target_url} status={resp.status_code}"
                ),
                campaign_id=ctx.campaign_id,
                run_id=ctx.run_id,
                step_id=ctx.step_id,
                plugin=self.Meta.name,
                target=params.target_url,
                bytes_sent=sent,
                extra={
                    "iteration": iteration,
                    "component": component.name,
                    "artifact": artifact_filename,
                    "artifact_bytes": len(artifact_bytes),
                    "status_code": resp.status_code,
                    "user_agent": _format_user_agent(component.user_agent),
                },
            ))
        except httpx.HTTPError as exc:
            events += 1
            await self._emit_failure_event(
                ctx, params, component, iteration, exc,
                kind="agentic_egress_artifact_fetch",
            )

        return events, sent

    async def _emit_failure_event(
        self,
        ctx: SimulationContext,
        params: AgenticEgressParams,
        component: _Component,
        iteration: int,
        exc: BaseException,
        *,
        kind: str,
    ) -> None:
        await ctx.emit_event(ecs_event(
            action=kind,
            outcome="failure",
            category="network",
            type_="error",
            message=f"{kind} failed iteration={iteration}: {exc}",
            campaign_id=ctx.campaign_id,
            run_id=ctx.run_id,
            step_id=ctx.step_id,
            plugin=self.Meta.name,
            target=params.target_url,
            extra={
                "iteration": iteration,
                "component": component.name,
                "error": str(exc),
            },
        ))

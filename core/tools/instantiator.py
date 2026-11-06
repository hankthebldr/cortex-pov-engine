"""
CortexSim Tool Instantiation Layer — Section 4.3 spec.

Manages the full lifecycle of external tools (build, run, stop, health-check).

CRITICAL CONSTRAINT: NO WRAPPER CODE.
The instantiator formats the run_template string from TOOL_REGISTRY with the provided
params, then passes the result directly to subprocess.Popen.  It does NOT create wrapper
functions or translate arguments.  SimCore is the process manager, not a translation layer.
"""

from __future__ import annotations

import logging
import os
import re
import shlex
import string
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import httpx
import psutil

from tools.registry import TOOL_REGISTRY

logger = logging.getLogger("cortexsim.tools")


# ---------------------------------------------------------------------------
# Caller-supplied start params (SEC-02 / OPS-02)
# ---------------------------------------------------------------------------
#
# start() used to seed binary/source_path/port from the registry and then run
# ``sub_context.update(params)`` — the request body OVERRODE the registry — so
# ``POST /api/tools/xdrtop/start {"params": {"binary": "<anything>"}}`` ran an
# arbitrary command line as SimCore's user (root in the image) from an
# unauthenticated route. The registry is the only source of what runs; the
# caller may fill exactly the placeholders the template leaves open, and each
# value must be one argv token of a known shape.

#: Placeholders the registry itself fills. Never the caller's to set.
_REGISTRY_SUPPLIED = frozenset({"source_path", "binary", "port"})

_MITRE_ID_RE = re.compile(r"^T\d{4}(\.\d{3})?$")
#: Generic placeholder value: one plain token. Deliberately narrow — no
#: whitespace, quotes, backslashes, shell metacharacters or format braces.
_GENERIC_VALUE_RE = re.compile(r"^[A-Za-z0-9_.:/@+,=-]{1,256}$")
#: Characters no path value may carry, whatever the OS: whitespace, control
#: characters, quotes and format braces would each change how the template
#: formats or how shlex splits it.
_PATH_FORBIDDEN_RE = re.compile(r"[\s\x00-\x1f\x7f\"'`{}]")


class ToolParamError(ValueError):
    """A caller-supplied start param was refused. Maps to 400 BAD_TOOL_PARAM."""

    code = "BAD_TOOL_PARAM"

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def _template_fields(template: str) -> set[str]:
    """Placeholder names a ``str.format`` template references."""
    names: set[str] = set()
    for _literal, field_name, _spec, _conv in string.Formatter().parse(template):
        if field_name is None:
            continue
        # "{a.b}" / "{a[0]}" — attribute/index access on a param is never a
        # legitimate tool template; keep the root name so it is still refused.
        root = re.split(r"[.\[]", field_name, maxsplit=1)[0]
        names.add(root)
    return names


def _validate_value(name: str, value: Any, base_dir: str) -> str:
    """Return the validated string value for placeholder ``name`` or raise."""
    if not isinstance(value, str):
        raise ToolParamError(
            f"param '{name}' must be a string, got {type(value).__name__}"
        )
    if not value:
        raise ToolParamError(f"param '{name}' must not be empty")
    if value.startswith("-"):
        raise ToolParamError(
            f"param '{name}' may not start with '-' (it would be read as an "
            f"option by the tool): {value!r}"
        )
    if name == "mitre_id":
        if not _MITRE_ID_RE.fullmatch(value):
            raise ToolParamError(
                f"param 'mitre_id' must be one ATT&CK technique id like T1082 or "
                f"T1059.004, got {value!r}"
            )
        return value
    if name.endswith("_path"):
        if _PATH_FORBIDDEN_RE.search(value):
            raise ToolParamError(
                f"param '{name}' contains whitespace, a control character, a "
                f"quote or a brace: {value!r}"
            )
        base = Path(base_dir).resolve()
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = base / candidate
        resolved = candidate.resolve()
        if resolved != base and base not in resolved.parents:
            raise ToolParamError(
                f"param '{name}' must resolve under CORTEXSIM_BASE_DIR ({base}); "
                f"{value!r} resolves to {resolved}"
            )
        return str(resolved)
    if not _GENERIC_VALUE_RE.fullmatch(value):
        raise ToolParamError(
            f"param '{name}' must be a single plain token "
            f"([A-Za-z0-9_.:/@+,=-], at most 256 chars), got {value!r}"
        )
    return value


def validate_start_params(
    tool_name: str, entry: dict, params: Any, base_dir: str,
) -> dict[str, str]:
    """Validate caller params against a registry entry; return the accepted set.

    Raises :class:`ToolParamError` naming the first problem. Pure — never
    spawns anything — so the API can call it before any side effect.
    """
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise ToolParamError(f"params must be an object, got {type(params).__name__}")

    template = entry.get("run_template")
    if not isinstance(template, str) or not template:
        raise ToolParamError(
            f"tool '{tool_name}' has no run_template, so it cannot be started "
            f"through this endpoint"
        )

    # A template field the registry supplies is closed even when this entry
    # does not define it: a missing {port} is a registry defect for format() to
    # report, never an opening for the caller to fill.
    open_fields = _template_fields(template) - _REGISTRY_SUPPLIED

    for key in sorted(params):
        if not isinstance(key, str):
            raise ToolParamError(f"param names must be strings, got {key!r}")
        if key in _REGISTRY_SUPPLIED or key in entry:
            raise ToolParamError(
                f"param '{key}' is defined by the tool registry for '{tool_name}' "
                f"and cannot be overridden by the request"
            )
        if key not in open_fields:
            accepted = ", ".join(sorted(open_fields)) or "none"
            raise ToolParamError(
                f"param '{key}' is not a placeholder of the '{tool_name}' "
                f"run_template (accepted: {accepted})"
            )

    missing = sorted(open_fields - set(params))
    if missing:
        raise ToolParamError(
            f"tool '{tool_name}' requires param(s) {', '.join(missing)}"
        )

    return {k: _validate_value(k, params[k], base_dir) for k in sorted(params)}


# ---------------------------------------------------------------------------
# Result / status dataclasses
# ---------------------------------------------------------------------------


@dataclass
class InstallResult:
    tool_name: str
    success: bool
    message: str
    install_path: Optional[str] = None
    error: Optional[str] = None


@dataclass
class StartResult:
    tool_name: str
    success: bool
    pid: Optional[int] = None
    message: str = ""
    error: Optional[str] = None


@dataclass
class StopResult:
    tool_name: str
    success: bool
    message: str = ""
    error: Optional[str] = None


@dataclass
class ToolStatus:
    tool_name: str
    status: str                       # not_installed | installed | running | stopped
    pid: Optional[int] = None
    port: Optional[int] = None
    install_path: Optional[str] = None
    last_health_check: Optional[datetime] = None
    healthy: Optional[bool] = None
    description: str = ""
    plane: list[str] = field(default_factory=list)
    tool_type: str = ""


# ---------------------------------------------------------------------------
# ToolInstantiator
# ---------------------------------------------------------------------------


class ToolInstantiator:
    """
    Manages build and process lifecycle for every tool in TOOL_REGISTRY.

    State is intentionally in-memory (augmented by the ToolInstance DB table
    which is written by the API layer).  The instantiator itself is stateless
    between restarts; the DB is the durable source of truth for PIDs / status.
    """

    def __init__(self, base_dir: str = "/app") -> None:
        self._base_dir = base_dir
        # pid cache: tool_name -> Popen object (ephemeral, lost on restart)
        self._processes: dict[str, subprocess.Popen] = {}

    # ------------------------------------------------------------------
    # install
    # ------------------------------------------------------------------

    def install(self, tool_name: str) -> InstallResult:
        """
        Build the tool from its submodule source using the registered build_cmd.
        Runs synchronously (build can take minutes for Rust crates).
        """
        if tool_name not in TOOL_REGISTRY:
            return InstallResult(
                tool_name=tool_name,
                success=False,
                message="Unknown tool",
                error=f"Tool '{tool_name}' is not in TOOL_REGISTRY",
            )

        entry = TOOL_REGISTRY[tool_name]
        source_path = os.path.join(self._base_dir, entry["source_path"])
        build_cmd = entry["build_cmd"]

        logger.info("Installing tool=%s source=%s cmd=%s", tool_name, source_path, build_cmd)

        if not os.path.isdir(source_path):
            return InstallResult(
                tool_name=tool_name,
                success=False,
                message="Source directory not found",
                error=f"Expected submodule at {source_path} — run: git submodule update --init --recursive",
            )

        try:
            result = subprocess.run(
                build_cmd,
                shell=True,
                cwd=source_path,
                capture_output=True,
                text=True,
                timeout=600,  # 10-minute hard cap for Rust compilation
            )
            if result.returncode != 0:
                logger.error("Install failed tool=%s stderr=%s", tool_name, result.stderr[:500])
                return InstallResult(
                    tool_name=tool_name,
                    success=False,
                    message="Build command failed",
                    error=result.stderr[:1000],
                )

            install_path = os.path.join(self._base_dir, entry.get("binary", entry["source_path"]))
            logger.info("Install success tool=%s path=%s", tool_name, install_path)
            return InstallResult(
                tool_name=tool_name,
                success=True,
                message="Build completed successfully",
                install_path=install_path,
            )
        except subprocess.TimeoutExpired:
            return InstallResult(
                tool_name=tool_name,
                success=False,
                message="Build timed out after 600 seconds",
                error="subprocess.TimeoutExpired",
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Unexpected install error tool=%s", tool_name)
            return InstallResult(
                tool_name=tool_name,
                success=False,
                message="Unexpected error during install",
                error=str(exc),
            )

    # ------------------------------------------------------------------
    # start
    # ------------------------------------------------------------------

    def validate_start_params(self, tool_name: str, params: Any) -> dict[str, str]:
        """Refuse-or-accept caller params for ``tool_name`` without side effects.

        Raises :class:`ToolParamError` (400 BAD_TOOL_PARAM at the API).
        """
        return validate_start_params(
            tool_name, TOOL_REGISTRY[tool_name], params, self._base_dir,
        )

    def start(self, tool_name: str, params: dict) -> StartResult:
        """
        Start the tool using its run_template, formatted with params.

        The formatted string is split by shlex and passed directly to
        subprocess.Popen — NO translation, NO wrappers.

        Caller params fill ONLY the placeholders the registry leaves open and
        are validated first (:func:`validate_start_params`); a refused param
        raises :class:`ToolParamError` and nothing is spawned.
        """
        if tool_name not in TOOL_REGISTRY:
            return StartResult(
                tool_name=tool_name,
                success=False,
                error=f"Tool '{tool_name}' is not in TOOL_REGISTRY",
            )

        entry = TOOL_REGISTRY[tool_name]
        accepted = self.validate_start_params(tool_name, params)

        # Build the template substitution context. Registry values first, then
        # the caller's — which validate_start_params has already proven cannot
        # name a registry key, so nothing here can be overridden.
        sub_context: dict = {"source_path": os.path.join(self._base_dir, entry["source_path"])}
        if "binary" in entry:
            sub_context["binary"] = os.path.join(self._base_dir, entry["binary"])
        if "port" in entry:
            sub_context["port"] = entry["port"]
        # Each caller value is quoted so shlex.split yields it back as exactly
        # one argv token (a Windows path's backslashes survive too).
        sub_context.update({k: shlex.quote(v) for k, v in accepted.items()})

        try:
            cmd_str = entry["run_template"].format(**sub_context)
        except KeyError as exc:
            return StartResult(
                tool_name=tool_name,
                success=False,
                error=f"Missing template parameter: {exc}",
            )

        logger.info("Starting tool=%s cmd=%s", tool_name, cmd_str)

        # Check if already running
        if tool_name in self._processes:
            proc = self._processes[tool_name]
            if proc.poll() is None:
                return StartResult(
                    tool_name=tool_name,
                    success=False,
                    pid=proc.pid,
                    error=f"Tool is already running with PID {proc.pid}",
                )

        try:
            argv = shlex.split(cmd_str)
            proc = subprocess.Popen(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            self._processes[tool_name] = proc
            logger.info("Started tool=%s pid=%d", tool_name, proc.pid)
            return StartResult(
                tool_name=tool_name,
                success=True,
                pid=proc.pid,
                message=f"Process started with PID {proc.pid}",
            )
        except FileNotFoundError as exc:
            return StartResult(
                tool_name=tool_name,
                success=False,
                error=f"Binary not found — install the tool first: {exc}",
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Unexpected start error tool=%s", tool_name)
            return StartResult(
                tool_name=tool_name,
                success=False,
                error=str(exc),
            )

    # ------------------------------------------------------------------
    # stop
    # ------------------------------------------------------------------

    def stop(self, tool_name: str) -> StopResult:
        """Terminate the running process for tool_name."""
        proc = self._processes.get(tool_name)

        if proc is None:
            # Attempt to find by PID if process table was lost (e.g., restart)
            return StopResult(
                tool_name=tool_name,
                success=False,
                error="No running process tracked for this tool (was it started by this instance?)",
            )

        if proc.poll() is not None:
            del self._processes[tool_name]
            return StopResult(
                tool_name=tool_name,
                success=True,
                message="Process had already exited",
            )

        try:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)

            del self._processes[tool_name]
            logger.info("Stopped tool=%s", tool_name)
            return StopResult(tool_name=tool_name, success=True, message="Process terminated")
        except Exception as exc:  # noqa: BLE001
            logger.exception("Error stopping tool=%s", tool_name)
            return StopResult(tool_name=tool_name, success=False, error=str(exc))

    # ------------------------------------------------------------------
    # status
    # ------------------------------------------------------------------

    def status(self, tool_name: str) -> ToolStatus:
        """Return current status of a tool without performing a live health check."""
        if tool_name not in TOOL_REGISTRY:
            return ToolStatus(
                tool_name=tool_name,
                status="unknown",
                description="Not in TOOL_REGISTRY",
            )

        entry = TOOL_REGISTRY[tool_name]
        proc = self._processes.get(tool_name)

        if proc is not None and proc.poll() is None:
            current_status = "running"
            pid = proc.pid
        elif proc is not None:
            current_status = "stopped"
            pid = proc.pid
        else:
            # Check if binary/source exists to determine installed vs not_installed
            binary_key = entry.get("binary")
            if binary_key:
                binary_path = os.path.join(self._base_dir, binary_key)
                current_status = "installed" if os.path.isfile(binary_path) else "not_installed"
            else:
                source_path = os.path.join(self._base_dir, entry["source_path"])
                current_status = "installed" if os.path.isdir(source_path) else "not_installed"
            pid = None

        return ToolStatus(
            tool_name=tool_name,
            status=current_status,
            pid=pid,
            port=entry.get("port"),
            install_path=os.path.join(self._base_dir, entry.get("binary", entry["source_path"])),
            description=entry.get("description", ""),
            plane=entry.get("plane", []),
            tool_type=entry.get("type", ""),
        )

    # ------------------------------------------------------------------
    # health_check
    # ------------------------------------------------------------------

    def health_check(self, tool_name: str) -> bool:
        """
        Perform a live health check.
        - For services with a health_check URL: HTTP GET via httpx.
        - For binary/service processes without URL: check psutil process liveness.
        Returns True if healthy, False otherwise.
        """
        if tool_name not in TOOL_REGISTRY:
            return False

        entry = TOOL_REGISTRY[tool_name]
        health_url = entry.get("health_check")

        if health_url:
            try:
                resp = httpx.get(health_url, timeout=5.0)
                return resp.status_code < 500
            except Exception:  # noqa: BLE001
                return False

        # Fallback: check psutil liveness
        proc = self._processes.get(tool_name)
        if proc is None:
            return False
        try:
            ps = psutil.Process(proc.pid)
            return ps.status() not in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False

    # ------------------------------------------------------------------
    # list_all
    # ------------------------------------------------------------------

    def list_all(self) -> list[ToolStatus]:
        """Return ToolStatus for every tool in TOOL_REGISTRY."""
        return [self.status(name) for name in TOOL_REGISTRY]


# Module-level singleton — imported by API layer
instantiator = ToolInstantiator()

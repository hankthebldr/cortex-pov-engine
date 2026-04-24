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
import shlex
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import httpx
import psutil

from tools.registry import TOOL_REGISTRY

logger = logging.getLogger("cortexsim.tools")


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

    def start(self, tool_name: str, params: dict) -> StartResult:
        """
        Start the tool using its run_template, formatted with params.

        The formatted string is split by shlex and passed directly to
        subprocess.Popen — NO translation, NO wrappers.
        """
        if tool_name not in TOOL_REGISTRY:
            return StartResult(
                tool_name=tool_name,
                success=False,
                error=f"Tool '{tool_name}' is not in TOOL_REGISTRY",
            )

        entry = TOOL_REGISTRY[tool_name]

        # Build the template substitution context
        sub_context: dict = {"source_path": os.path.join(self._base_dir, entry["source_path"])}
        if "binary" in entry:
            sub_context["binary"] = os.path.join(self._base_dir, entry["binary"])
        if "port" in entry:
            sub_context["port"] = entry["port"]
        # Caller-supplied params override registry defaults
        sub_context.update(params)

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

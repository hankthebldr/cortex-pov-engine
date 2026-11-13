"""scripts/mcp-stdio-docker.sh selects the one running SimCore container and
execs the stdio MCP server inside it. Driven against a stub ``docker`` on PATH;
no real docker call is made."""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "mcp-stdio-docker.sh"


def _run(tmp_path: Path, ps_output: str, *args: str, env_extra: dict | None = None):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    log = tmp_path / "docker.log"
    stub = bin_dir / "docker"
    stub.write_text(
        "#!/bin/sh\n"
        'echo "$@" >> "$STUB_LOG"\n'
        'if [ "$1" = "ps" ]; then printf "%s" "$STUB_PS"; fi\n'
    )
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "STUB_PS": ps_output,
        "STUB_LOG": str(log),
        "HOME": str(tmp_path),
    }
    env.update(env_extra or {})
    proc = subprocess.run(
        ["/bin/bash", str(SCRIPT), *args],
        env=env, capture_output=True, text=True, timeout=30,
    )
    return proc, (log.read_text() if log.exists() else "")


def test_zero_containers_exits_2(tmp_path):
    proc, _ = _run(tmp_path, "", "--print")
    assert proc.returncode == 2
    assert "no SimCore container" in proc.stderr


def test_two_containers_exits_2_naming_the_count(tmp_path):
    proc, _ = _run(tmp_path, "aaa111\nbbb222\n", "--print")
    assert proc.returncode == 2
    assert "2 SimCore containers; set CORTEXSIM_COMPOSE_PROJECT" in proc.stderr


def test_one_container_prints_the_exec_line(tmp_path):
    proc, log = _run(tmp_path, "abc123\n", "--print")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "docker exec -i abc123 python -m mcp_server"
    assert "label=com.docker.compose.service=simcore" in log
    assert "com.docker.compose.project" not in log


def test_project_narrows_the_selection(tmp_path):
    proc, log = _run(
        tmp_path, "abc123\n", "--print",
        env_extra={"CORTEXSIM_COMPOSE_PROJECT": "pov-a"},
    )
    assert proc.returncode == 0, proc.stderr
    assert "label=com.docker.compose.project=pov-a" in log


def test_allow_host_actions_is_passed_only_when_set(tmp_path):
    unset, _ = _run(tmp_path, "abc123\n", "--print")
    assert "CORTEXSIM_MCP_ALLOW_HOST_ACTIONS" not in unset.stdout
    setp, _ = _run(
        tmp_path, "abc123\n", "--print",
        env_extra={"CORTEXSIM_MCP_ALLOW_HOST_ACTIONS": "true"},
    )
    assert setp.stdout.strip() == (
        "docker exec -i -e CORTEXSIM_MCP_ALLOW_HOST_ACTIONS abc123 python -m mcp_server"
    )


def test_without_print_it_execs_docker(tmp_path):
    proc, log = _run(tmp_path, "abc123\n")
    assert proc.returncode == 0, proc.stderr
    assert "exec -i abc123 python -m mcp_server" in log

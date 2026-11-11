"""``python -m mcp_server`` as a process, the way the image boot gate runs it."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import yaml

from mcp_server.manifest import validate_manifest
from mcp_server.naming import CANONICAL_IDS, wire_name

CORE = Path(__file__).resolve().parents[2] / "core"


def _run(*args: str, **extra_env: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(CORE), **extra_env}
    env.pop("CORTEXSIM_URL", None)
    return subprocess.run([sys.executable, "-m", "mcp_server", *args], cwd=CORE, env=env,
                          capture_output=True, text=True, timeout=120)


def test_list_tools_prints_41():
    proc = _run("--list-tools")
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.strip().splitlines()
    assert lines[-1] == "41 tools"
    rows = [line.split("\t") for line in lines[:-1]]
    assert all(len(r) == 4 for r in rows)
    assert [r[0] for r in rows] == list(CANONICAL_IDS)
    assert [r[1] for r in rows] == [wire_name(i) for i in CANONICAL_IDS]
    assert {r[2] for r in rows} <= {"read", "write", "destructive"}
    assert {r[3] for r in rows} <= {"none", "simcore", "tenant", "host"}


def test_manifest_flag_prints_yaml_that_validates():
    proc = _run("--manifest")
    assert proc.returncode == 0, proc.stderr
    doc = yaml.safe_load(proc.stdout)
    validate_manifest(doc)
    assert len(doc["tools"]) == 41


def test_tenant_call_ceiling_defaults_from_the_same_setting_as_the_mount():
    proc = _run("--help", CORTEXSIM_MCP_MAX_TENANT_CALLS_PER_MINUTE="7")
    assert proc.returncode == 0, proc.stderr
    assert "(default: 7)" in " ".join(proc.stdout.split())

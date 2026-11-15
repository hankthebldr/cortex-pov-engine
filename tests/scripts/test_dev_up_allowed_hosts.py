"""dev-up.sh seeds CORTEXSIM_ALLOWED_HOSTS with this jumpbox's own names.

The host guard (core/security/host_guard.py) admits localhost, IP literals and
the process's own hostname. In the shipped container that hostname is the
container id, so a DC browsing to the jumpbox by name, or a Windows beacon
started with ``--server http://JUMPBOX:8888``, got 400 HOST_NOT_ALLOWED until
someone set the variable by hand, and ``curl -fsSL ... | bash`` hid the
remediation behind "error: 400". Driven with a stub ``hostname`` on PATH in the
same throwaway layout as test_dev_up_volume_warning.py; no Docker, no network.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.scripts.test_dev_up_volume_warning import _write_exec, sandbox  # noqa: F401 (fixture)

pytestmark = pytest.mark.skipif(
    os.name == "nt" or shutil.which("bash") is None,
    reason="dev-up.sh is a bash script for POSIX jumpboxes",
)


def _hostname_stub(bindir: Path, short: str, fqdn: str) -> None:
    _write_exec(bindir / "hostname", f"""#!/usr/bin/env bash
case "$1" in
  -s) echo "{short}" ;;
  -f) echo "{fqdn}" ;;
  *)  echo "{short}" ;;
esac
""")


def _run(root: Path, bindir: Path) -> subprocess.CompletedProcess:
    env = {"PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}", "HOME": str(root)}
    return subprocess.run(["bash", str(root / "scripts" / "dev-up.sh")],
                          cwd=root, env=env, capture_output=True, text=True, timeout=60)


def _allowed(root: Path) -> str:
    for line in (root / ".env").read_text().splitlines():
        if line.startswith("CORTEXSIM_ALLOWED_HOSTS="):
            return line.split("=", 1)[1]
    raise AssertionError("generated .env has no CORTEXSIM_ALLOWED_HOSTS line")


def test_a_generated_env_admits_the_jumpbox_short_and_fqdn(sandbox):  # noqa: F811
    root, bindir = sandbox
    _hostname_stub(bindir, "WINLAB-JB01", "winlab-jb01.corp.example")
    proc = _run(root, bindir)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _allowed(root) == "winlab-jb01,winlab-jb01.corp.example"


def test_localhost_and_junk_names_are_not_seeded(sandbox):  # noqa: F811
    root, bindir = sandbox
    _hostname_stub(bindir, "localhost", "bad name;rm -rf /")
    proc = _run(root, bindir)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert _allowed(root) == ""
    assert "CORTEXSIM_ALLOWED_HOSTS is empty" in out

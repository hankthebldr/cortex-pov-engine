"""OPS-12 — dev-up.sh must not silently orphan an encrypted vault.

``scripts/dev-up.sh`` generates ``.env`` with a fresh ``CORTEXSIM_SECRET``
whenever ``.env`` is missing — and its own placeholder-refusal message tells a
DC to ``rm .env && scripts/dev-up.sh``. The ``cortex-pov-engine_simcore_db``
volume outlives that: it still holds every credential encrypted under the OLD
secret, which the new one cannot open. Nothing said so until a tenant test
returned ``CRYPTO_ERROR`` in front of the customer.

dev-up.sh now checks (read-only: ``docker volume inspect``, guarded with
``command -v docker``) and warns loudly before generating a new secret over an
existing vault, naming both remediations. It does not stop: a DC who meant to
start fresh should not be blocked, only told.

Driven against stub ``docker`` / ``curl`` binaries on PATH, in a throwaway copy
of the script's repo layout, so no real Docker or network is touched.

RED on base cc26ddf: no warning was printed.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(
    os.name == "nt" or shutil.which("bash") is None,
    reason="dev-up.sh is a bash script for POSIX jumpboxes",
)

DOCKER_STUB = """#!/usr/bin/env bash
case "$1" in
  info) exit 0 ;;
  --version) echo "Docker version 99.0.0-stub"; exit 0 ;;
  context) echo default; exit 0 ;;
  volume)
    if [[ "$2" == "inspect" && "$3" == "cortex-pov-engine_simcore_db" && -n "${STUB_VOLUME_EXISTS:-}" ]]; then
      echo '[{"Name":"cortex-pov-engine_simcore_db"}]'; exit 0
    fi
    exit 1 ;;
  compose) exit 0 ;;
esac
exit 0
"""

CURL_STUB = """#!/usr/bin/env bash
printf '%s' '{"status":"ok","components":{}}'
"""


def _write_exec(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture
def sandbox(tmp_path):
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    shutil.copy2(REPO / "scripts" / "dev-up.sh", root / "scripts" / "dev-up.sh")
    shutil.copy2(REPO / ".env.example", root / ".env.example")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _write_exec(bindir / "docker", DOCKER_STUB)
    _write_exec(bindir / "curl", CURL_STUB)
    return root, bindir


def _run(root: Path, bindir: Path, *, volume_exists: bool):
    env = {
        "PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}",
        "HOME": str(root),
    }
    if volume_exists:
        env["STUB_VOLUME_EXISTS"] = "1"
    return subprocess.run(
        ["bash", str(root / "scripts" / "dev-up.sh")],
        cwd=root, env=env, capture_output=True, text=True, timeout=60,
    )


def test_warns_when_generating_a_secret_over_an_existing_vault(sandbox):
    root, bindir = sandbox
    proc = _run(root, bindir, volume_exists=True)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert (root / ".env").is_file()
    assert "cortex-pov-engine_simcore_db" in out, out
    assert "MASTER_KEY_MISMATCH" in out, out
    assert "restore" in out.lower() and "re-enter" in out.lower(), out


def test_silent_on_a_fresh_host(sandbox):
    root, bindir = sandbox
    proc = _run(root, bindir, volume_exists=False)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "MASTER_KEY_MISMATCH" not in out


def test_silent_when_env_already_exists(sandbox):
    """The warning is about GENERATING a new secret; an existing .env keeps
    its secret and the vault stays readable."""
    root, bindir = sandbox
    first = _run(root, bindir, volume_exists=False)
    assert first.returncode == 0, first.stdout + first.stderr
    again = _run(root, bindir, volume_exists=True)
    out = again.stdout + again.stderr
    assert again.returncode == 0, out
    assert "MASTER_KEY_MISMATCH" not in out

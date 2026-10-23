"""install.sh must refuse to run outside a checkout, before it touches the host.

install.sh is the full source bootstrap: every step after the toolchain install
works on files relative to SCRIPT_DIR. Piped (``curl … | bash``) or run as a
lone downloaded release asset, SCRIPT_DIR holds no repo. Before this guard,
every build step skipped with a warning, ``docker compose up`` was skipped too,
and the script printed "Installation complete" and exited 0 (measured
2026-10-19). The release notes advertised exactly that invocation until c565f9e.

These tests run the real script. Every command that could change the host is
shadowed on PATH by a stub that records its argv and exits 1, so a missing
guard fails the test instead of installing a toolchain.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO / "install.sh"
CODE = "INSTALL_NOT_A_CHECKOUT"

# Anything install.sh could use to change the host. None of them may run when
# the guard fires; if the guard is missing they fail closed instead of acting.
HOST_MUTATORS = (
    "sudo", "apt-get", "curl", "wget", "docker", "docker-compose",
    "systemctl", "usermod", "go", "cargo", "rustup", "npm", "pip3",
)

BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="needs bash")


@pytest.fixture
def sandbox(tmp_path):
    """An empty working directory, a stubbed PATH, and the stub call log."""
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    log = tmp_path / "stub-calls.log"
    log.touch()
    for name in HOST_MUTATORS:
        stub = stubs / name
        stub.write_text('#!/bin/sh\nprintf \'%s %s\\n\' "$(basename "$0")" "$*" >> "$STUB_LOG"\nexit 1\n')
        stub.chmod(0o755)
    work = tmp_path / "not-a-checkout"
    work.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    env = {
        "PATH": f"{stubs}:/usr/bin:/bin",
        "HOME": str(home),
        "USER": "tester",
        "LANG": "C",
        "STUB_LOG": str(log),
    }
    return work, env, log


def _run(argv, *, cwd, env, stdin=None):
    proc = subprocess.run(
        argv, cwd=cwd, env=env, input=stdin,
        capture_output=True, timeout=60,
    )
    return proc.returncode, proc.stdout.decode() + proc.stderr.decode()


def _assert_refused_before_anything_ran(rc, out, log):
    assert rc != 0, f"install.sh exited 0 outside a checkout:\n{out}"
    assert CODE in out, f"missing stable refusal code {CODE}:\n{out}"
    assert "Installation complete" not in out
    # The guard runs first: not even the OS check, let alone the toolchain.
    assert "Checking OS compatibility" not in out, out
    assert log.read_text() == "", f"host-mutating commands ran:\n{log.read_text()}"


def test_piped_into_bash_from_an_empty_directory_is_refused(sandbox):
    """`curl -fsSL …/install.sh | sudo bash`: the script arrives on stdin."""
    work, env, log = sandbox
    rc, out = _run([BASH], cwd=work, env=env, stdin=INSTALL_SH.read_bytes())
    _assert_refused_before_anything_ran(rc, out, log)


def test_lone_downloaded_copy_is_refused(sandbox):
    """The release asset saved on its own and run as ./install.sh."""
    work, env, log = sandbox
    shutil.copy2(INSTALL_SH, work / "install.sh")
    rc, out = _run([BASH, "install.sh"], cwd=work, env=env)
    _assert_refused_before_anything_ran(rc, out, log)


def test_real_checkout_passes_the_guard(sandbox):
    """Negative control: the repo's own install.sh, invoked from elsewhere.

    SCRIPT_DIR resolves to the checkout, so the guard must stay silent and
    the run must reach the next step. It still stops there: on macOS at the
    Linux check, on Linux at the first stubbed `sudo apt-get`.
    """
    work, env, log = sandbox
    rc, out = _run([BASH, str(INSTALL_SH)], cwd=work, env=env)
    assert CODE not in out, f"guard misfired on a real checkout:\n{out}"
    assert "Checking OS compatibility" in out, out
    assert rc != 0  # the stubs and the OS check end the run; nothing is installed
    if os.uname().sysname == "Linux":
        assert log.read_text().startswith("sudo apt-get update"), log.read_text()

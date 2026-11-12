"""scripts/lab/publish-agent.sh: no baked token, and a Host-guard refusal that says so.

SEC-16 (the script's half). ``--token`` wrote the enrollment token into
``dist/agent/enroll.sh``, which ``docker-compose.yml`` mounts at
``/app/core/static/agent`` and SimCore serves without authentication at
``/agent/enroll.sh``. A token can be minted for up to 1000 uses and 30 days, so
anyone who could fetch one static file could enroll agents for a month. The
script now refuses ``--token``; a victim supplies its token at run time
(``CORTEXSIM_TOKEN=... bash``), which the generated installer always supported.
How SimCore STORES tokens is a separate product decision and is untouched.

RG-INSTALL-03 (this installer's half). The generated ``enroll.sh`` probed
``/api/health`` with ``curl -f``, which discards the response body. When the
baked ``--server`` is a DNS name SimCore's Host guard does not admit, the answer
is ``400 HOST_NOT_ALLOWED`` with the fix in the body, and the victim saw "cannot
reach ... is SimCore up? is the firewall open?" for a server that had just
answered.

Driven with a stub ``go`` (publish side) and a stub ``curl`` (victim side) on
PATH, in a throwaway copy of the script's layout. No beacon is built, nothing
is downloaded, and the generated installer is only ever run up to a refusal.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "lab" / "publish-agent.sh"

pytestmark = pytest.mark.skipif(
    os.name == "nt" or shutil.which("bash") is None,
    reason="publish-agent.sh is a bash script for a POSIX control plane",
)

TOKEN = "cxs_THIS-IS-A-TEST-TOKEN-0123456789"
SERVER = "http://192.0.2.10:8888"

# Stands in for the Go toolchain: creates whatever `-o <path>` names.
GO_STUB = """#!/usr/bin/env bash
printf 'go %s\\n' "$*" >> "${STUB_LOG}"
out=""
while [[ $# -gt 0 ]]; do
  if [[ "$1" == "-o" ]]; then out="$2"; shift 2; else shift; fi
done
[[ -n "${out}" ]] && printf 'stub beacon\\n' > "${out}"
exit 0
"""

# Stands in for curl on the victim. Like the real one, a flag cluster with 'f'
# (-fsS, -fsSL) turns an HTTP error into exit 22 with NO body on stdout.
CURL_STUB = """#!/usr/bin/env bash
printf 'curl %s\\n' "$*" >> "${STUB_LOG}"
fail_on_error=0; url=""
for a in "$@"; do
  case "$a" in
    --*) ;;
    -*f*) fail_on_error=1 ;;
    http*) url="$a" ;;
  esac
done
case "${url}" in
  */api/health)
    if [[ "${STUB_HEALTH}" == "host_not_allowed" ]]; then
      (( fail_on_error )) && exit 22
      printf '%s' '{"error":"Host not allowed","code":"HOST_NOT_ALLOWED","detail":"add it to CORTEXSIM_ALLOWED_HOSTS"}'
      exit 0
    fi
    if [[ "${STUB_HEALTH}" == "down" ]]; then exit 7; fi
    printf '%s' '{"status":"degraded","components":{}}'; exit 0 ;;
  *) exit 22 ;;   # the beacon download and the enroll POST: never succeed here
esac
"""


def _write_exec(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture
def sandbox(tmp_path):
    root = tmp_path / "repo"
    (root / "scripts" / "lab").mkdir(parents=True)
    shutil.copy2(SCRIPT, root / "scripts" / "lab" / "publish-agent.sh")
    (root / "agent").mkdir()
    (root / "core" / "static").mkdir(parents=True)
    (root / ".env").write_text("CORTEXSIM_ENV=development\nCORTEXSIM_PORT=8888\n")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _write_exec(bindir / "go", GO_STUB)
    log = tmp_path / "stub.log"
    log.touch()
    return root, bindir, log


def _publish(sandbox, *args: str, extra_env: dict[str, str] | None = None):
    root, bindir, log = sandbox
    env = {"PATH": f"{bindir}{os.pathsep}/usr/bin{os.pathsep}/bin", "HOME": str(root),
           "STUB_LOG": str(log)}
    env.update(extra_env or {})
    proc = subprocess.run(["bash", str(root / "scripts" / "lab" / "publish-agent.sh"), *args],
                          cwd=root, env=env, capture_output=True, text=True, timeout=60)
    return proc, proc.stdout + proc.stderr


def _published_files(root: Path) -> list[Path]:
    return [p for base in (root / "dist", root / "core" / "static") if base.exists()
            for p in base.rglob("*") if p.is_file()]


# --------------------------------------------------------------------------
# SEC-16: the token is never written into the served tree
# --------------------------------------------------------------------------

def test_publish_agent_refuses_to_bake_a_token(sandbox):
    root, _, log = sandbox
    proc, out = _publish(sandbox, "--server", SERVER, "--token", TOKEN)
    assert proc.returncode == 2, f"--token was accepted:\n{out}"
    assert "--token" in out and "CORTEXSIM_TOKEN" in out, out
    assert "/agent/enroll.sh" in out, f"the refusal does not say where the token would be served:\n{out}"
    assert TOKEN not in out, "the refusal echoed the token to the terminal"
    leaked = [str(p.relative_to(root)) for p in _published_files(root) if TOKEN in p.read_text(errors="replace")]
    assert not leaked, f"the token was written into the served tree: {leaked}"
    assert log.read_text() == "", "the beacon build started before the refusal"


def test_publish_agent_generates_an_installer_that_takes_its_token_at_run_time(sandbox):
    root, _, _ = sandbox
    proc, out = _publish(sandbox, "--server", SERVER)
    assert proc.returncode == 0, out
    enroll = (root / "dist" / "agent" / "enroll.sh").read_text()
    assert 'TOKEN="${CORTEXSIM_TOKEN:-}"' in enroll, (
        "the generated installer has a baked default token, or no longer reads CORTEXSIM_TOKEN"
    )
    assert f'SERVER="${{CORTEXSIM_SERVER:-{SERVER}}}"' in enroll
    assert "CORTEXSIM_TOKEN=cxs_" in out, "the next step (pass the token at run time) is not printed"
    syntax = subprocess.run(["bash", "-n", str(root / "dist" / "agent" / "enroll.sh")],
                            capture_output=True, text=True)
    assert syntax.returncode == 0, syntax.stderr


def test_publish_agent_does_not_bake_a_token_from_its_own_environment(sandbox):
    """CORTEXSIM_TOKEN exported on the CONTROL PLANE must not leak into the file."""
    root, _, _ = sandbox
    proc, out = _publish(sandbox, "--server", SERVER, extra_env={"CORTEXSIM_TOKEN": TOKEN})
    assert proc.returncode == 0, out
    leaked = [str(p.relative_to(root)) for p in _published_files(root) if TOKEN in p.read_text(errors="replace")]
    assert not leaked, leaked


def test_publish_agent_overwrites_an_installer_that_an_older_run_baked_a_token_into(sandbox):
    root, _, _ = sandbox
    for stale in (root / "dist" / "agent" / "enroll.sh", root / "core" / "static" / "agent" / "enroll.sh"):
        stale.parent.mkdir(parents=True, exist_ok=True)
        stale.write_text(f'TOKEN="${{CORTEXSIM_TOKEN:-{TOKEN}}}"\n')
    proc, out = _publish(sandbox, "--server", SERVER)
    assert proc.returncode == 0, out
    leaked = [str(p.relative_to(root)) for p in _published_files(root) if TOKEN in p.read_text(errors="replace")]
    assert not leaked, f"a previously baked token is still being served from: {leaked}"


def test_publish_agent_runs_when_the_checkout_has_no_env_file(sandbox):
    """Found while writing these tests: with no .env the script read the port
    with `sed <missing file> | head` under `set -euo pipefail`, so it exited 1
    on that line having printed nothing at all."""
    root, _, _ = sandbox
    (root / ".env").unlink()
    proc, out = _publish(sandbox, "--server", SERVER)
    assert proc.returncode == 0, f"rc={proc.returncode}, output: {out!r}"
    assert (root / "dist" / "agent" / "enroll.sh").is_file()


def test_publish_agent_help_prints_the_whole_header_and_no_code(sandbox):
    proc, out = _publish(sandbox, "--help")
    assert proc.returncode == 0, out
    assert "--token is refused" in out, "the help text lost the line about --token"
    assert "set -euo pipefail" not in out, "the help text ran past the header into the script"


# --------------------------------------------------------------------------
# RG-INSTALL-03: a DNS --server the allowlist does not carry
# --------------------------------------------------------------------------

@pytest.mark.parametrize("allowlist, server, warned", [
    (None, "http://jumpbox01.corp.example:8888", True),                      # no line at all
    ("", "http://jumpbox01.corp.example:8888", True),
    ("other.corp.example", "http://jumpbox01.corp.example:8888", True),
    ("jumpbox01,jumpbox01.corp.example", "http://JUMPBOX01.corp.example:8888", False),
    ("*.corp.example", "http://jumpbox01.corp.example:8888", False),
    ("*", "http://jumpbox01.corp.example:8888", False),
    ("*.corp.example", "http://corp.example:8888", True),                    # the guard's own rule
    ("", "http://192.0.2.10:8888", False),                                   # IP literals are always admitted
    ("", "http://[fd00::10]:8888", False),
])
def test_publish_agent_says_when_the_server_name_is_not_in_the_allowlist(sandbox, allowlist, server, warned):
    root, _, _ = sandbox
    env_text = "CORTEXSIM_ENV=development\nCORTEXSIM_PORT=8888\n"
    if allowlist is not None:
        env_text += f"CORTEXSIM_ALLOWED_HOSTS={allowlist}\n"
    (root / ".env").write_text(env_text)
    proc, out = _publish(sandbox, "--server", server)
    assert proc.returncode == 0, out
    assert ("HOST_NOT_ALLOWED" in out) is warned, out
    if warned:
        assert "CORTEXSIM_ALLOWED_HOSTS" in out


# --------------------------------------------------------------------------
# RG-INSTALL-03: the generated installer names a Host-guard refusal
# --------------------------------------------------------------------------

def _enroll(sandbox, tmp_path, health: str):
    root, bindir, log = sandbox
    proc, out = _publish(sandbox, "--server", "http://jumpbox01.corp.example:8888")
    assert proc.returncode == 0, out
    victim_bin = tmp_path / "victim-bin"
    victim_bin.mkdir()
    _write_exec(victim_bin / "curl", CURL_STUB)
    log.write_text("")
    home = tmp_path / "victim-home"
    home.mkdir()
    env = {"PATH": f"{victim_bin}{os.pathsep}/usr/bin{os.pathsep}/bin", "HOME": str(home),
           "STUB_LOG": str(log), "STUB_HEALTH": health, "CORTEXSIM_TOKEN": TOKEN}
    run = subprocess.run(["bash", str(root / "dist" / "agent" / "enroll.sh")],
                         cwd=home, env=env, capture_output=True, text=True, timeout=60)
    return run, run.stdout + run.stderr, log.read_text()


def test_generated_installer_names_a_host_guard_refusal(sandbox, tmp_path):
    run, out, calls = _enroll(sandbox, tmp_path, "host_not_allowed")
    assert run.returncode != 0, out
    assert "HOST_NOT_ALLOWED" in out and "CORTEXSIM_ALLOWED_HOSTS" in out, (
        f"SimCore answered 400 HOST_NOT_ALLOWED and the installer reported:\n{out}"
    )
    assert "jumpbox01.corp.example" in out, "the refused name is not shown"
    assert "/api/agents/enroll" not in calls and "/agent/cortexsim-agent" not in calls, (
        f"the installer went on after the refusal:\n{calls}"
    )
    assert TOKEN not in out


def test_generated_installer_still_says_unreachable_when_nothing_answers(sandbox, tmp_path):
    run, out, calls = _enroll(sandbox, tmp_path, "down")
    assert run.returncode != 0, out
    assert "cannot reach" in out and "HOST_NOT_ALLOWED" not in out, out
    assert "/api/agents/enroll" not in calls


def test_generated_installer_passes_the_preflight_on_a_degraded_simcore(sandbox, tmp_path):
    """Every SimCore reads degraded today (api_auth). That is up, not down."""
    run, out, calls = _enroll(sandbox, tmp_path, "up")
    # The stub refuses the beacon download, which is the step after the preflight.
    assert "download failed" in out, f"the preflight did not pass on a degraded SimCore:\n{out}"
    assert "/agent/cortexsim-agent-" in calls

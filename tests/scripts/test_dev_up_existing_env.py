"""dev-up.sh over an EXISTING .env: the upgrade path (RG-GUARD-01, RG-INSTALL-03, OPS-19).

The Host guard (core/security/host_guard.py) admits localhost, IP literals, the
process's own hostname and the names in ``CORTEXSIM_ALLOWED_HOSTS``. In the
shipped container the hostname is the container id, so the jumpbox's own name is
admitted only through that variable. ``dev-up.sh`` seeded it only when it
GENERATED ``.env``. A deployment that already had one (every upgrade, and every
worktree that reuses the root ``.env``) took the "already present, leaving it
untouched" branch and booted with an empty allowlist: every beacon started as
``--server http://<jumpbox-name>:8888`` and every console session opened by name
got ``400 HOST_NOT_ALLOWED``, and dev-up.sh said nothing.

What these pin:

* a bare run never edits ``.env``. It names the consequence, passes this host's
  names to compose for this bring-up, and repeats after the bring-up that the
  value is not in ``.env``;
* ``--apply`` writes the one line, after a backup, and touches no other key;
* a value the operator already chose (in ``.env`` or in the shell) is left alone;
* a ``CORTEXSIM_VERSION`` pin that disagrees with the tree is named before the
  build re-tags new code under the old version (OPS-19).

Driven with stub ``docker`` / ``curl`` / ``hostname`` on PATH in a throwaway copy
of the script's repo layout: no Docker, no network.
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

SECRET = "Zm9vYmFyLXRlc3Qtb25seS1ub3QtYS1yZWFsLXNlY3JldC0wMTIzNDU2Nzg5"
UNSET = "<unset>"

# Records what `docker compose ... up` would have been given. `${VAR-<unset>}`
# distinguishes "not in the environment" from "set to the empty string".
DOCKER_STUB = """#!/usr/bin/env bash
case "$1" in
  info) exit 0 ;;
  --version) echo "Docker version 99.0.0-stub"; exit 0 ;;
  context) echo default; exit 0 ;;
  volume) exit 1 ;;
  compose)
    for a in "$@"; do
      if [[ "$a" == "up" ]]; then
        printf '%s' "${CORTEXSIM_ALLOWED_HOSTS-<unset>}" > "${STUB_COMPOSE_LOG}"
      fi
    done
    exit 0 ;;
esac
exit 0
"""

CURL_STUB = """#!/usr/bin/env bash
printf '%s' '{"status":"degraded","components":{"api_auth":{"status":"degraded","code":"API_UNAUTHENTICATED","detail":"x"}}}'
"""


def _write_exec(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _hostname_stub(bindir: Path, short: str, fqdn: str) -> None:
    _write_exec(bindir / "hostname", f"""#!/usr/bin/env bash
case "$1" in
  -s) echo "{short}" ;;
  -f) echo "{fqdn}" ;;
  *)  echo "{short}" ;;
esac
""")


def _compose_file(root: Path, *, default_version: str) -> None:
    """The two lines of docker-compose.yml that carry the version default,
    spelled as the real file spells them (asserted below)."""
    (root / "docker-compose.yml").write_text(
        "services:\n"
        "  simcore:\n"
        f"    image: cortex-pov-engine-simcore:${{CORTEXSIM_VERSION:-{default_version}}}\n"
        f"    container_name: cortex-pov-engine-simcore-v${{CORTEXSIM_VERSION:-{default_version}}}\n"
    )


@pytest.fixture
def sandbox(tmp_path):
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    shutil.copy2(REPO / "scripts" / "dev-up.sh", root / "scripts" / "dev-up.sh")
    shutil.copy2(REPO / ".env.example", root / ".env.example")
    # The tree's own version, and compose's fallback, where dev-up.sh reads them.
    (root / "core" / "api").mkdir(parents=True)
    (root / "core" / "api" / "health.py").write_text('APP_VERSION = "9.9.9"\n')
    _compose_file(root, default_version="9.9.9")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _write_exec(bindir / "docker", DOCKER_STUB)
    _write_exec(bindir / "curl", CURL_STUB)
    _hostname_stub(bindir, "JUMPBOX01", "jumpbox01.corp.example")
    return root, bindir, tmp_path / "compose-env.log"


def _env_file(root: Path, *extra: str, version: str | None = "9.9.9") -> Path:
    """A pre-round-1 .env: no CORTEXSIM_ALLOWED_HOSTS line unless given."""
    lines = ["CORTEXSIM_ENV=development", f"CORTEXSIM_SECRET={SECRET}", "CORTEXSIM_PORT=8888"]
    if version is not None:
        lines.append(f"CORTEXSIM_VERSION={version}")
    lines.extend(extra)
    path = root / ".env"
    path.write_text("\n".join(lines) + "\n")
    path.chmod(0o600)
    return path


def _run(sandbox, *args: str, shell_env: dict[str, str] | None = None):
    root, bindir, log = sandbox
    env = {
        "PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}",
        "HOME": str(root),
        "STUB_COMPOSE_LOG": str(log),
    }
    env.update(shell_env or {})
    proc = subprocess.run(
        ["bash", str(root / "scripts" / "dev-up.sh"), *args],
        cwd=root, env=env, capture_output=True, text=True, timeout=60,
    )
    return proc, proc.stdout + proc.stderr


def _compose_saw(sandbox) -> str:
    log = sandbox[2]
    assert log.is_file(), "`docker compose ... up` was never invoked"
    return log.read_text()


def _backups(root: Path) -> list[Path]:
    return sorted((root / ".env-backups").glob("*/.env"))


SEED = "jumpbox01,jumpbox01.corp.example"


# --------------------------------------------------------------------------
# Bare run: warn, admit this host's names for this bring-up, never edit .env
# --------------------------------------------------------------------------

def test_dev_up_existing_env_without_an_allowlist_warns_and_names_the_consequence(sandbox):
    root = sandbox[0]
    _env_file(root)
    proc, out = _run(sandbox)
    assert proc.returncode == 0, out
    assert "CORTEXSIM_ALLOWED_HOSTS" in out, out
    assert "HOST_NOT_ALLOWED" in out, out
    assert SEED in out, f"the warning does not show the value it would use:\n{out}"
    assert "--apply" in out, f"the warning does not say how to persist the value:\n{out}"


def test_dev_up_existing_env_without_an_allowlist_admits_this_hosts_names_for_the_bring_up(sandbox):
    root = sandbox[0]
    _env_file(root)
    proc, out = _run(sandbox)
    assert proc.returncode == 0, out
    assert _compose_saw(sandbox) == SEED, (
        "compose was started without this jumpbox's names, so every beacon and "
        "console session that uses one is answered 400 HOST_NOT_ALLOWED"
    )


def test_dev_up_bare_run_leaves_an_existing_env_byte_identical(sandbox):
    root = sandbox[0]
    path = _env_file(root)
    before = path.read_bytes()
    proc, out = _run(sandbox)
    assert proc.returncode == 0, out
    assert path.read_bytes() == before, "a bare dev-up.sh edited an existing .env"
    assert not (root / ".env-backups").exists(), "a bare run wrote a backup, so it wrote .env"


def test_dev_up_says_after_the_bring_up_that_the_value_is_not_persisted(sandbox):
    """The first warning scrolls away under the image build's output. The fact
    that a plain `docker compose up` will drop the names has to be on screen
    when the script returns."""
    root = sandbox[0]
    _env_file(root)
    proc, out = _run(sandbox)
    assert proc.returncode == 0, out
    tail = out[out.index("CortexSim is up"):]
    assert "CORTEXSIM_ALLOWED_HOSTS" in tail and "--apply" in tail, tail


def test_dev_up_existing_env_with_an_empty_allowlist_line_is_treated_like_a_missing_one(sandbox):
    """`cp .env.example .env` and a hand-filled secret leaves the line empty."""
    root = sandbox[0]
    _env_file(root, "CORTEXSIM_ALLOWED_HOSTS=")
    proc, out = _run(sandbox)
    assert proc.returncode == 0, out
    assert "HOST_NOT_ALLOWED" in out, out
    assert _compose_saw(sandbox) == SEED


def test_dev_up_existing_env_that_already_names_hosts_is_left_alone(sandbox):
    root = sandbox[0]
    path = _env_file(root, "CORTEXSIM_ALLOWED_HOSTS=pov.lab.example")
    before = path.read_bytes()
    proc, out = _run(sandbox)
    assert proc.returncode == 0, out
    assert "HOST_NOT_ALLOWED" not in out, f"warned about a configured allowlist:\n{out}"
    # Compose reads .env itself; dev-up.sh must not shadow the operator's value.
    assert _compose_saw(sandbox) == UNSET
    assert path.read_bytes() == before


def test_dev_up_an_allowlist_from_the_shell_wins_and_is_not_overridden(sandbox):
    root = sandbox[0]
    _env_file(root)
    proc, out = _run(sandbox, shell_env={"CORTEXSIM_ALLOWED_HOSTS": "custom.lab.example"})
    assert proc.returncode == 0, out
    assert _compose_saw(sandbox) == "custom.lab.example"
    assert "HOST_NOT_ALLOWED" not in out, out


def test_dev_up_an_explicitly_empty_shell_allowlist_is_respected_and_said(sandbox):
    """An operator who wants SimCore to answer by IP only says so by exporting
    the variable empty. That is a choice, not an omission: no names are added,
    and the script says which one it is."""
    root = sandbox[0]
    _env_file(root)
    proc, out = _run(sandbox, shell_env={"CORTEXSIM_ALLOWED_HOSTS": ""})
    assert proc.returncode == 0, out
    assert _compose_saw(sandbox) == ""
    assert "CORTEXSIM_ALLOWED_HOSTS" in out and "IP" in out, out


def test_dev_up_existing_env_and_an_unreadable_hostname_says_no_name_is_admitted(sandbox):
    root, bindir, _ = sandbox
    _hostname_stub(bindir, "localhost", "bad name;rm -rf /")
    _env_file(root)
    proc, out = _run(sandbox)
    assert proc.returncode == 0, out
    assert _compose_saw(sandbox) == UNSET
    assert "CORTEXSIM_ALLOWED_HOSTS" in out and "HOST_NOT_ALLOWED" in out, out


# --------------------------------------------------------------------------
# --apply: persist the one line, after a backup
# --------------------------------------------------------------------------

def test_dev_up_apply_appends_the_allowlist_and_keeps_every_other_line(sandbox):
    root = sandbox[0]
    path = _env_file(root)
    before = path.read_text()
    proc, out = _run(sandbox, "--apply")
    assert proc.returncode == 0, out
    after = path.read_text()
    assert after.startswith(before), "--apply rewrote lines it does not own"
    added = [ln for ln in after[len(before):].splitlines() if ln and not ln.startswith("#")]
    assert added == [f"CORTEXSIM_ALLOWED_HOSTS={SEED}"], added
    assert stat.S_IMODE(path.stat().st_mode) == 0o600, "the secret-bearing .env lost mode 600"


def test_dev_up_apply_writes_a_private_backup_of_the_previous_env(sandbox):
    root = sandbox[0]
    path = _env_file(root)
    before = path.read_bytes()
    proc, out = _run(sandbox, "--apply")
    assert proc.returncode == 0, out
    backups = _backups(root)
    assert len(backups) == 1, f"expected one backup under .env-backups/<timestamp>/.env, got {backups}"
    assert backups[0].read_bytes() == before
    assert stat.S_IMODE(backups[0].stat().st_mode) == 0o600
    assert str(backups[0].parent.relative_to(root)) in out, "the backup path was not printed"


def test_dev_up_apply_fills_an_empty_line_in_place(sandbox):
    root = sandbox[0]
    path = _env_file(root, "CORTEXSIM_ALLOWED_HOSTS=", "CORTEXSIM_CORS_ORIGINS=")
    before = path.read_text().splitlines()
    proc, out = _run(sandbox, "--apply")
    assert proc.returncode == 0, out
    after = path.read_text().splitlines()
    expected = [f"CORTEXSIM_ALLOWED_HOSTS={SEED}" if ln == "CORTEXSIM_ALLOWED_HOSTS=" else ln
                for ln in before]
    assert after == expected


def test_dev_up_apply_is_idempotent(sandbox):
    root = sandbox[0]
    path = _env_file(root)
    first, out1 = _run(sandbox, "--apply")
    assert first.returncode == 0, out1
    once = path.read_bytes()
    second, out2 = _run(sandbox, "--apply")
    assert second.returncode == 0, out2
    assert path.read_bytes() == once
    assert len(_backups(root)) == 1, "a second --apply with nothing to change wrote a backup"
    assert "HOST_NOT_ALLOWED" not in out2, out2


def test_dev_up_never_prints_the_secret(sandbox):
    root = sandbox[0]
    _env_file(root)
    for args in ((), ("--apply",)):
        proc, out = _run(sandbox, *args)
        assert proc.returncode == 0, out
        assert SECRET not in out, "dev-up.sh printed CORTEXSIM_SECRET"


def test_dev_up_refuses_an_unknown_flag_before_starting_anything(sandbox):
    """`--aply` used to be ignored, so the run looked like it had applied."""
    root = sandbox[0]
    _env_file(root)
    proc, out = _run(sandbox, "--aply")
    assert proc.returncode == 2, out
    assert "--aply" in out
    assert not sandbox[2].exists(), "compose was started despite the unknown flag"


def test_dev_up_dev_overlay_flag_still_works(sandbox):
    root = sandbox[0]
    _env_file(root, "CORTEXSIM_ALLOWED_HOSTS=pov.lab.example")
    for flag in ("--dev", "dev"):
        proc, out = _run(sandbox, flag)
        assert proc.returncode == 0, out
        assert "docker-compose.dev.yml" in out, out


# --------------------------------------------------------------------------
# OPS-19: a CORTEXSIM_VERSION pin that disagrees with the tree
# --------------------------------------------------------------------------

def test_dev_up_names_a_version_pin_that_disagrees_with_the_tree(sandbox):
    root = sandbox[0]
    _env_file(root, "CORTEXSIM_ALLOWED_HOSTS=pov.lab.example", version="1.0.2")
    proc, out = _run(sandbox)
    assert proc.returncode == 0, out
    assert "CORTEXSIM_VERSION" in out, out
    assert "1.0.2" in out and "9.9.9" in out, out
    # Says what goes wrong, not only that two numbers differ.
    assert "cortex-pov-engine-simcore:1.0.2" in out and "/api/health" in out, out


def test_dev_up_is_quiet_when_the_version_pin_matches_the_tree(sandbox):
    root = sandbox[0]
    _env_file(root, "CORTEXSIM_ALLOWED_HOSTS=pov.lab.example", version="9.9.9")
    proc, out = _run(sandbox)
    assert proc.returncode == 0, out
    assert "disagrees" not in out and "re-tag" not in out, out


def test_dev_up_without_a_version_pin_reports_what_compose_will_use(sandbox):
    """No pin anywhere: compose falls back to the default in docker-compose.yml.
    dev-up.sh used to print its own hardcoded copy of that number, which a
    release bump could leave behind; the line it prints is now read from the
    file compose reads."""
    root = sandbox[0]
    _compose_file(root, default_version="7.7.7")
    (root / "core" / "api" / "health.py").write_text('APP_VERSION = "7.7.7"\n')
    _env_file(root, "CORTEXSIM_ALLOWED_HOSTS=pov.lab.example", version=None)
    proc, out = _run(sandbox)
    assert proc.returncode == 0, out
    assert "cortex-pov-engine-simcore:7.7.7" in out, out
    assert "disagrees" not in out, out


def test_dev_up_names_a_compose_default_that_disagrees_with_the_tree(sandbox):
    root = sandbox[0]
    _compose_file(root, default_version="7.7.7")  # the tree says 9.9.9
    _env_file(root, "CORTEXSIM_ALLOWED_HOSTS=pov.lab.example", version=None)
    proc, out = _run(sandbox)
    assert proc.returncode == 0, out
    assert "docker-compose.yml" in out and "7.7.7" in out and "9.9.9" in out, out
    assert "disagrees" in out, out


def test_dev_up_a_version_from_the_shell_is_the_one_compared(sandbox):
    """The shell wins over .env in compose, so it is the value that tags the image."""
    root = sandbox[0]
    _env_file(root, "CORTEXSIM_ALLOWED_HOSTS=pov.lab.example", version="9.9.9")
    proc, out = _run(sandbox, shell_env={"CORTEXSIM_VERSION": "1.0.2"})
    assert proc.returncode == 0, out
    assert "cortex-pov-engine-simcore:1.0.2" in out and "disagrees" in out, out


def test_dev_up_reads_the_version_default_the_way_the_real_compose_file_spells_it():
    """The sandbox compose file above is hand-written. If the real one stops
    matching what dev-up.sh parses, the comparison silently stops happening."""
    import re

    text = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
    m = re.search(r"^\s*image:.*\$\{CORTEXSIM_VERSION:-([^}]+)\}", text, re.M)
    assert m, "docker-compose.yml no longer carries image: ...${CORTEXSIM_VERSION:-<default>}"
    health = (REPO / "core" / "api" / "health.py").read_text(encoding="utf-8")
    app = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', health, re.M)
    assert app, "core/api/health.py no longer carries APP_VERSION = \"...\""
    assert m.group(1) == app.group(1), (
        f"docker-compose.yml defaults CORTEXSIM_VERSION to {m.group(1)} but the code "
        f"reports {app.group(1)}: a bare `docker compose up` tags the image with one "
        f"version while /api/health reports the other"
    )


def test_dev_up_says_when_the_trees_version_cannot_be_read(sandbox):
    root = sandbox[0]
    (root / "core" / "api" / "health.py").unlink()
    _env_file(root, "CORTEXSIM_ALLOWED_HOSTS=pov.lab.example", version="1.0.2")
    proc, out = _run(sandbox)
    assert proc.returncode == 0, out
    assert "APP_VERSION" in out, f"an unreadable tree version passed in silence:\n{out}"

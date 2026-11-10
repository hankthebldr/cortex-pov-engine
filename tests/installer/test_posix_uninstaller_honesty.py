"""The POSIX uninstaller must report what it FOUND, not just what it removed.

The base script counted removals only. Every removal was `rm -f "$p" && cs_gone`,
so a removal that failed (a non-root run against a root install, a read-only
prefix, a process owned by another user) incremented nothing — and the script
then printed "nothing to remove — no CortexSim beacon found on this host" and
exited 0. A DC leaving a customer site read that as a clean endpoint while the
beacon was still installed and, in the process case, still running.

These tests EXECUTE the generated script. Every system path in it is rewritten
to a scratch directory before it runs, and systemctl / launchctl / pkill /
pgrep are stubs on PATH, so nothing outside ``tmp_path`` is read, written,
signalled or removed — on a developer laptop or in the CI container alike.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")

IS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0

# Every absolute system path the uninstaller may touch. Each one is rewritten
# into the sandbox; the guard below refuses to run a script that still names a
# system prefix after rewriting, so template drift cannot reach the real host.
SYSTEM_PATHS = (
    "/etc/systemd/system",
    "/usr/local/bin/cortexsim-agent",
    "/Library/LaunchDaemons",
    "/var/log/cortexsim-agent.log",
    "/opt/cortexsim/cache",
    "/opt/cortexsim/tools",
    "/tmp/.cortexsim",
)
SYSTEM_PREFIXES = ("/etc/systemd", "/usr/local/bin", "/Library/LaunchDaemons", "/var/log",
                   "/opt/cortexsim", "/tmp/.cortexsim")


def _script() -> str:
    from api.agents import _POSIX_UNINSTALLER

    return _POSIX_UNINSTALLER


def _stub(path: Path, body: str) -> None:
    path.write_text("#!/bin/sh\n" + body + "\n")
    path.chmod(0o755)


@pytest.fixture
def sandbox(tmp_path):
    root = tmp_path / "sys"
    paths = {
        "/etc/systemd/system": root / "etc-systemd-system",
        "/usr/local/bin/cortexsim-agent": root / "usr-local-bin" / "cortexsim-agent",
        "/Library/LaunchDaemons": root / "Library-LaunchDaemons",
        "/var/log/cortexsim-agent.log": root / "var-log" / "cortexsim-agent.log",
        "/opt/cortexsim/cache": root / "opt-cortexsim" / "cache",
        "/opt/cortexsim/tools": root / "opt-cortexsim" / "tools",
        "/tmp/.cortexsim": root / "tmp-dot-cortexsim",
    }
    for key, p in paths.items():
        if key.startswith(("/opt/", "/tmp/")):
            continue  # beacon cache trees: absent unless a test creates them
        (p if key.endswith(("system", "LaunchDaemons")) else p.parent).mkdir(parents=True, exist_ok=True)

    # HOME lives under a directory literally named "locked" so the optional
    # refusing `rm` below can target it by path (root ignores chmod).
    home = tmp_path / "locked" / "home"
    home.mkdir(parents=True)
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    calls = tmp_path / "calls.log"
    for tool in ("systemctl", "launchctl", "pkill"):
        _stub(fakebin / tool, f'echo "{tool} $*" >> "{calls}"\nexit 1')
    _stub(fakebin / "pgrep", f'echo "pgrep $*" >> "{calls}"\nexit 1')  # no beacon running

    env = {k: v for k, v in os.environ.items() if not k.startswith("CORTEXSIM_")}
    env["PATH"] = f"{fakebin}{os.pathsep}{env['PATH']}"
    env["HOME"] = str(home)
    # The uninstaller removes the beacon's ${TMPDIR}/cortexsim-cache and
    # ${XDG_CACHE_HOME}/cortexsim: point both into the sandbox, never the host.
    tmpdir = tmp_path / "tmpdir"
    tmpdir.mkdir()
    env["TMPDIR"] = str(tmpdir)
    env["XDG_CACHE_HOME"] = str(home / ".cache")
    yield {"paths": paths, "home": home, "fakebin": fakebin, "env": env,
           "calls": calls, "tmp": tmp_path, "tmpdir": tmpdir}
    # chmod 555 dirs must be writable again or tmp_path cleanup fails.
    for dirpath, dirnames, _ in os.walk(tmp_path):
        for d in dirnames:
            try:
                os.chmod(os.path.join(dirpath, d), 0o755)
            except OSError:
                pass


def _rewrite(script: str, sandbox) -> str:
    for old, new in sandbox["paths"].items():
        if old in script:
            script = script.replace(old, str(new))
    for prefix in SYSTEM_PREFIXES:
        assert prefix not in script, (
            f"the uninstaller still names {prefix} after sandboxing — refusing to run it "
            f"against the real host; add the new path to SYSTEM_PATHS")
    return script


def _run(script: str, sandbox, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    env = dict(sandbox["env"])
    if extra_env:
        env.update(extra_env)
    return subprocess.run(["bash", "-s"], input=script, capture_output=True, text=True,
                          env=env, timeout=60)


def _refusing_rm(sandbox) -> None:
    """An `rm` that refuses anything under .../locked/..., delegating the rest.
    Needed because root ignores the chmod 555 the non-root case relies on."""
    real_rm = shutil.which("rm")
    _stub(sandbox["fakebin"] / "rm",
          'for a in "$@"; do case "$a" in */locked/*) echo "rm: $a: Operation not permitted" >&2; exit 1;; esac; done\n'
          f'exec {real_rm} "$@"')


def _readonly(*dirs: Path) -> None:
    for d in dirs:
        d.chmod(stat.S_IRUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)


# ---------------------------------------------------------------------------


def test_every_system_path_is_named_by_the_template():
    """Drift guard for the sandbox: if the uninstaller stops naming one of these,
    the rewrite list is stale (and the guard in _rewrite would also fire)."""
    script = _script()
    for p in SYSTEM_PATHS:
        assert p in script, p


def test_the_uninstaller_parses():
    p = subprocess.run(["bash", "-n", "/dev/stdin"], input=_script(),
                       capture_output=True, text=True, timeout=10)
    assert p.returncode == 0, p.stderr


@pytest.mark.skipif(IS_ROOT, reason="asserts the non-root refusal; root may remove these")
def test_a_root_install_seen_by_a_non_root_uninstall_is_refused_up_front(sandbox):
    """The exact base defect: a system unit in a directory this user cannot write.
    Base: rm fails silently, REMOVED stays 0, "nothing to remove", rc 0."""
    unit_dir = sandbox["paths"]["/etc/systemd/system"]
    (unit_dir / "cortexsim-agent.service").write_text("[Service]\nExecStart=/x\n")
    _readonly(unit_dir)

    p = _run(_rewrite(_script(), sandbox), sandbox)
    out = p.stdout + p.stderr
    assert p.returncode != 0, out
    assert "nothing to remove" not in out, out
    assert "NEEDS_ROOT" in out, out
    assert "sudo" in out
    assert (unit_dir / "cortexsim-agent.service").exists()  # refused before touching anything


def test_an_item_that_cannot_be_removed_is_incomplete_not_absent(sandbox):
    bindir = sandbox["home"] / ".local" / "bin"
    bindir.mkdir(parents=True)
    beacon = bindir / "cortexsim-agent"
    beacon.write_text("#!/bin/sh\n")
    _readonly(bindir)
    if IS_ROOT:
        _refusing_rm(sandbox)

    p = _run(_rewrite(_script(), sandbox), sandbox)
    out = p.stdout + p.stderr
    assert p.returncode == 1, out
    assert "nothing to remove" not in out, out
    assert "code=UNINSTALL_INCOMPLETE" in out, out
    assert str(beacon) in out, "each found-but-not-removed item must be named"
    assert beacon.exists()


def test_a_beacon_that_survives_the_kill_is_incomplete(sandbox):
    """Base: `pkill` returning 0 was reported as "removed: running beacon
    process(es)" without ever checking the process was gone."""
    _stub(sandbox["fakebin"] / "pgrep", "echo 4242\nexit 0")   # always still running
    _stub(sandbox["fakebin"] / "pkill", "exit 0")              # claims success

    p = _run(_rewrite(_script(), sandbox), sandbox)
    out = p.stdout + p.stderr
    assert p.returncode == 1, out
    assert "code=UNINSTALL_INCOMPLETE" in out, out
    assert "running" in out


def test_a_clean_host_says_nothing_to_remove_and_exits_zero(sandbox):
    p = _run(_rewrite(_script(), sandbox), sandbox)
    out = p.stdout + p.stderr
    assert p.returncode == 0, out
    assert "nothing to remove" in out
    assert "DELETE /api/agents/<agent_id>" in out


def test_user_install_is_fully_removed_including_log_and_state(sandbox):
    home = sandbox["home"]
    (home / ".local" / "bin").mkdir(parents=True)
    (home / ".local" / "bin" / "cortexsim-agent").write_text("#!/bin/sh\n")
    (home / ".cortexsim").mkdir()
    (home / ".cortexsim" / "cortexsim-agent.log").write_text("log\n")
    unit = home / ".config" / "systemd" / "user" / "cortexsim-agent.service"
    unit.parent.mkdir(parents=True)
    unit.write_text("[Service]\n")

    p = _run(_rewrite(_script(), sandbox), sandbox)
    out = p.stdout + p.stderr
    assert p.returncode == 0, out
    assert not (home / ".local" / "bin" / "cortexsim-agent").exists()
    assert not (home / ".cortexsim").exists(), "the user-mode log/state dir must go too"
    assert not unit.exists()
    assert "nothing to remove" not in out
    assert "3 of 3" in out, out


def test_a_custom_install_prefix_is_found(sandbox):
    """`CORTEXSIM_BIN_DIR` relocates the install; the uninstaller must look there."""
    prefix = sandbox["tmp"] / "opt" / "bin"
    prefix.mkdir(parents=True)
    (prefix / "cortexsim-agent").write_text("#!/bin/sh\n")
    p = _run(_rewrite(_script(), sandbox), sandbox, {"CORTEXSIM_BIN_DIR": str(prefix)})
    out = p.stdout + p.stderr
    assert p.returncode == 0, out
    assert not (prefix / "cortexsim-agent").exists(), out


@pytest.mark.skipif(not IS_ROOT, reason="exercises the root branch")
def test_as_root_the_system_log_is_removed(sandbox):
    log = sandbox["paths"]["/var/log/cortexsim-agent.log"]
    log.write_text("x\n")
    p = _run(_rewrite(_script(), sandbox), sandbox)
    assert p.returncode == 0, p.stdout + p.stderr
    assert not log.exists()


def test_the_beacon_staging_caches_are_found_and_removed(sandbox):
    """The beacon caches staged dual-use tools (linpeas, pspy, ...) under
    /opt/cortexsim/cache, ${XDG_CACHE_HOME}/cortexsim or ${TMPDIR}/cortexsim-cache
    and stages under /tmp/.cortexsim (agent/beacon/artifact.go). An uninstall
    that never looked there printed "nothing to remove" over a host that still
    carried them."""
    caches = [
        sandbox["paths"]["/opt/cortexsim/cache"],
        sandbox["paths"]["/tmp/.cortexsim"],
        sandbox["tmpdir"] / "cortexsim-cache",
        sandbox["home"] / ".cache" / "cortexsim",
    ]
    for c in caches:
        c.mkdir(parents=True)
        (c / "linpeas.sh").write_text("#!/bin/sh\n")
    p = _run(_rewrite(_script(), sandbox), sandbox)
    out = p.stdout + p.stderr
    assert p.returncode == 0, out
    for c in caches:
        assert not c.exists(), f"{c} survived the uninstall:\n{out}"
    assert "nothing to remove" not in out
    assert f"{len(caches)} of {len(caches)}" in out, out


def test_a_cache_dir_override_is_removed_only_when_it_names_cortexsim(sandbox):
    named = sandbox["tmp"] / "lab" / "cortexsim-tools"
    unnamed = sandbox["tmp"] / "lab" / "operator-data"
    for d in (named, unnamed):
        d.mkdir(parents=True)
    p = _run(_rewrite(_script(), sandbox), sandbox, {"CORTEXSIM_CACHE_DIR": str(named),
                                                    "CORTEXSIM_STAGE_ROOT": str(unnamed)})
    out = p.stdout + p.stderr
    assert p.returncode == 0, out
    assert not named.exists(), out
    assert unnamed.exists(), "a tree we did not name must never be deleted"

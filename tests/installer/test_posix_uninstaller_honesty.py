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
import re
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
# The single line on which the uninstaller names where other users' homes live.
HOME_ROOTS_LINE = 'HOME_ROOTS=("/home" "/Users")'


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

    # Ambient SUDO_* would send the root branch looking in a REAL user's home.
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("CORTEXSIM_", "SUDO_"))}
    env["PATH"] = f"{fakebin}{os.pathsep}{env['PATH']}"
    env["HOME"] = str(home)
    # Stands in for /home and /Users, which the root branch globs.
    homes = tmp_path / "homes"
    homes.mkdir()
    # The uninstaller removes the beacon's ${TMPDIR}/cortexsim-cache and
    # ${XDG_CACHE_HOME}/cortexsim: point both into the sandbox, never the host.
    tmpdir = tmp_path / "tmpdir"
    tmpdir.mkdir()
    env["TMPDIR"] = str(tmpdir)
    env["XDG_CACHE_HOME"] = str(home / ".cache")
    yield {"paths": paths, "home": home, "fakebin": fakebin, "env": env,
           "calls": calls, "tmp": tmp_path, "tmpdir": tmpdir, "homes": homes}
    # chmod 555 dirs must be writable again or tmp_path cleanup fails.
    for dirpath, dirnames, _ in os.walk(tmp_path):
        for d in dirnames:
            try:
                os.chmod(os.path.join(dirpath, d), 0o755)
            except OSError:
                pass


def _rewrite(script: str, sandbox) -> str:
    # The root branch globs other users' homes. Left as written it would read
    # (and, as real root, remove from) the real /home and /Users, so the one
    # line that names them is swapped for the sandbox, and any OTHER mention of
    # either in code (not comments) refuses the run.
    assert script.count(HOME_ROOTS_LINE) <= 1, "the home roots are declared more than once"
    script = script.replace(HOME_ROOTS_LINE, 'HOME_ROOTS=("@@SANDBOX_HOMES@@")')
    code = "\n".join(ln for ln in script.splitlines() if not ln.lstrip().startswith("#"))
    stray = re.findall(r"(?<![A-Za-z0-9_.$-])/(?:home|Users)\b[^\n]*", code)
    assert not stray, (
        f"the uninstaller names a real home root outside {HOME_ROOTS_LINE!r}: {stray} — "
        "refusing to run it against the real host")
    for old, new in sandbox["paths"].items():
        if old in script:
            script = script.replace(old, str(new))
    for prefix in SYSTEM_PREFIXES:
        assert prefix not in script, (
            f"the uninstaller still names {prefix} after sandboxing — refusing to run it "
            f"against the real host; add the new path to SYSTEM_PATHS")
    return script.replace("@@SANDBOX_HOMES@@", str(sandbox["homes"]))


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


# ---------------------------------------------------------------------------
# Under sudo the user-mode install must still be found
# ---------------------------------------------------------------------------
#
# The console's remove command is `| sudo bash`; its install one-liner has no
# sudo, so a DC logged in as a normal user gets the user-mode install (a systemd
# --user unit with Restart=always). sudo resets HOME to root's on Ubuntu >= 19.10,
# Debian and RHEL, and the uninstaller looked for that install only through
# $HOME. It killed the process, printed "done -- removed 1 of 1 item(s)", exited
# 0, and the user's systemd restarted the beacon ten seconds later.
#
# `id`, `getent` and `stat` are stubbed, so the root branch runs on a developer
# laptop and inside the CI image alike. Not run under a real sudo or a real
# systemd: what `systemctl --user --machine=` does on a real host is unproven.


def _fake_id(sandbox, uid: int, name: str, others: dict | None = None) -> None:
    """`id` as the script sees it. Every file under test belongs to the real
    user either way, so nothing but the script's own branching changes."""
    real = shutil.which("id")
    arms = "".join(f'  "-u {n}") echo {u}; exit 0;;\n' for n, u in (others or {}).items())
    _stub(sandbox["fakebin"] / "id",
          f'case "$*" in\n  "-u") echo {uid}; exit 0;;\n  "-un") echo {name}; exit 0;;\n{arms}esac\n'
          f'exec {real} "$@"')


def _user_install(home: Path) -> dict:
    """What the installer leaves for a non-root user on a systemd host."""
    unit = home / ".config" / "systemd" / "user" / "cortexsim-agent.service"
    unit.parent.mkdir(parents=True)
    unit.write_text("[Service]\nRestart=always\nRestartSec=10\n")
    wants = unit.parent / "default.target.wants"
    wants.mkdir()
    (wants / "cortexsim-agent.service").symlink_to(unit)
    beacon = home / ".local" / "bin" / "cortexsim-agent"
    beacon.parent.mkdir(parents=True)
    beacon.write_text("#!/bin/sh\n")
    state = home / ".cortexsim"
    state.mkdir()
    (state / "cortexsim-agent.log").write_text("log\n")
    return {"unit": unit, "enable-link": wants / "cortexsim-agent.service",
            "beacon": beacon, "state": state}


def _as_sudo(sandbox, user: str, home: Path, uid: int = 1000) -> dict:
    """Environment of `sudo bash` run by ``user``: uid 0, HOME reset to root's,
    SUDO_USER set, the passwd lookup and the home's owner answering ``user``."""
    _fake_id(sandbox, 0, "root", {user: uid})
    _stub(sandbox["fakebin"] / "getent",
          f'[ "$*" = "passwd {user}" ] && {{ echo "{user}:x:{uid}:{uid}::{home}:/bin/bash"; exit 0; }}\n'
          'exit 2')
    real_stat = shutil.which("stat")
    _stub(sandbox["fakebin"] / "stat",
          f'for a in "$@"; do [ "$a" = "{home}" ] && {{ echo {user}; exit 0; }}; done\n'
          f'exec {real_stat} "$@"')
    root_home = sandbox["tmp"] / "roothome"
    root_home.mkdir(exist_ok=True)
    return {"HOME": str(root_home), "SUDO_USER": user, "SUDO_UID": str(uid),
            "XDG_CACHE_HOME": str(root_home / ".cache")}


def _systemctl(sandbox, ok: tuple = ()) -> None:
    """A systemctl that succeeds only for a call containing one of ``ok``."""
    arms = "".join(f'  *"{frag}"*) exit 0;;\n' for frag in ok)
    _stub(sandbox["fakebin"] / "systemctl",
          f'echo "systemctl $*" >> "{sandbox["calls"]}"\ncase "$*" in\n{arms}  *) ;;\nesac\nexit 1')


def _running_until_killed(sandbox) -> None:
    flag = sandbox["tmp"] / "killed"
    _stub(sandbox["fakebin"] / "pkill",
          f'echo "pkill $*" >> "{sandbox["calls"]}"\n: > "{flag}"\nexit 0')
    _stub(sandbox["fakebin"] / "pgrep", f'[ -f "{flag}" ] && exit 1\necho 4242\nexit 0')


def _survivors(inst: dict) -> list:
    return [f"{k}: {v}" for k, v in inst.items() if v.exists() or v.is_symlink()]


def test_the_home_roots_are_declared_on_the_one_rewritable_line():
    """Drift guard for the sandbox: `_rewrite` swaps exactly this line."""
    assert _script().count(HOME_ROOTS_LINE) == 1


def test_under_sudo_the_invoking_users_install_is_found_and_removed(sandbox):
    """The reported defect. Executed on c852eac: `removed: running beacon
    process(es)`, `done -- removed 1 of 1 item(s)`, exit 0, with the unit, the
    binary and the log all still on disk."""
    dc_home = sandbox["tmp"] / "elsewhere" / "dc"   # outside the globbed roots:
    dc_home.mkdir(parents=True)                     # only SUDO_USER can find it
    inst = _user_install(dc_home)
    env = _as_sudo(sandbox, "dc", dc_home)
    _systemctl(sandbox, ok=("--user --machine=dc@.host",))
    _running_until_killed(sandbox)

    p = _run(_rewrite(_script(), sandbox), sandbox, env)
    out = p.stdout + p.stderr
    assert not _survivors(inst), f"a user-mode install survived `sudo bash`: {_survivors(inst)}\n{out}"
    assert p.returncode == 0, out
    assert "done -- removed 4 of 4 item(s)" in out, out   # unit, process, binary, state dir
    calls = sandbox["calls"].read_text()
    assert "systemctl --user --machine=dc@.host disable --now cortexsim-agent" in calls, calls


def test_as_root_a_user_install_in_another_home_is_found_without_sudo_user(sandbox):
    """`sudo -i`, `su -` and a root login carry no SUDO_USER. The beacon is on
    the host all the same, so a root run sweeps the home roots for it."""
    dc_home = sandbox["homes"] / "dc"
    dc_home.mkdir()
    inst = _user_install(dc_home)
    env = _as_sudo(sandbox, "dc", dc_home)
    del env["SUDO_USER"], env["SUDO_UID"]
    _stub(sandbox["fakebin"] / "getent", "exit 2")
    _systemctl(sandbox, ok=("--user --machine=dc@.host",))

    p = _run(_rewrite(_script(), sandbox), sandbox, env)
    out = p.stdout + p.stderr
    assert not _survivors(inst), f"{_survivors(inst)}\n{out}"
    assert p.returncode == 0, out
    assert "nothing to remove" not in out, out
    assert "done -- removed 3 of 3 item(s)" in out, out


def test_under_sudo_a_user_unit_root_cannot_stop_is_never_done(sandbox):
    """dc's systemd user manager is running and neither `--machine=` nor
    `runuser` reaches it. Killing the process is then a ten-second fix
    (Restart=always), so the verdict must be UNINSTALL_INCOMPLETE, never done."""
    dc_home = sandbox["homes"] / "dc"
    dc_home.mkdir()
    inst = _user_install(dc_home)
    env = _as_sudo(sandbox, "dc", dc_home)
    _systemctl(sandbox, ok=("is-active --quiet user@1000.service",))
    _stub(sandbox["fakebin"] / "runuser", f'echo "runuser $*" >> "{sandbox["calls"]}"\nexit 1')
    _running_until_killed(sandbox)

    p = _run(_rewrite(_script(), sandbox), sandbox, env)
    out = p.stdout + p.stderr
    assert p.returncode == 1, out
    assert "code=UNINSTALL_INCOMPLETE" in out, out
    assert "done --" not in out, out
    assert str(inst["unit"]) in out and "as dc" in out, out
    assert inst["unit"].exists(), "kept, so dc's own run still has a unit to disable"
    assert not inst["beacon"].exists(), "the binary is taken off the host regardless"
    assert "runuser -u dc" in sandbox["calls"].read_text()


def test_under_sudo_with_no_user_manager_running_the_unit_file_is_the_whole_job(sandbox):
    """No `user@<uid>.service` means nothing is supervising the unit: an
    unreachable manager that is not running must not read as a stuck beacon."""
    dc_home = sandbox["homes"] / "dc"
    dc_home.mkdir()
    inst = _user_install(dc_home)
    env = _as_sudo(sandbox, "dc", dc_home)
    _systemctl(sandbox)   # every call fails, is-active included

    p = _run(_rewrite(_script(), sandbox), sandbox, env)
    out = p.stdout + p.stderr
    assert p.returncode == 0, out
    assert not _survivors(inst), f"{_survivors(inst)}\n{out}"
    assert "done -- removed 3 of 3 item(s)" in out, out


def test_a_non_root_run_never_looks_in_another_users_home(sandbox):
    """The sweep is the root branch's alone: a normal user's uninstall stays
    inside their own HOME even when SUDO_USER leaks in from the environment."""
    other = sandbox["homes"] / "someone-else"
    other.mkdir()
    inst = _user_install(other)
    _fake_id(sandbox, 501, "dc")

    p = _run(_rewrite(_script(), sandbox), sandbox, {"SUDO_USER": "someone-else"})
    out = p.stdout + p.stderr
    assert p.returncode == 0, out
    assert "nothing to remove" in out, out
    assert len(_survivors(inst)) == len(inst), out

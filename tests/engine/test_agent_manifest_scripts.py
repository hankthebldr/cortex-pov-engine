import os
import platform
import stat
import subprocess
import time

import pytest

from engine import identity_spec
from engine import k8s_manifest as km
from engine.agent_manifest import PROBE_TOOLS, PREPARE_MODES, fetch_script, start_script

SHA = "a" * 64
EXP = "2026-11-11T00:00:00Z"


@pytest.mark.parametrize(
    "s",
    [
        fetch_script(arch="amd64", beacon_sha256=SHA),
        start_script(arch="amd64", prepare="packages", expires_at=EXP, profile="baseline"),
        start_script(arch="arm64", prepare="none", expires_at=EXP, profile="lab-privileged"),
    ],
)
def test_scripts_parse_as_posix_sh(s):
    assert subprocess.run(["sh", "-n"], input=s, text=True).returncode == 0


def test_probe_tools_is_the_manifest_list():
    assert PROBE_TOOLS is km._KNOWN_BINS
    assert PREPARE_MODES == ("packages", "none")


def test_fetch_pins_the_carried_digest_and_never_asks_the_server_for_one():
    s = fetch_script(arch="arm64", beacon_sha256=SHA)
    assert SHA in s and "arch=arm64" in s
    assert "/binary/sha256" not in s
    assert s.index("sha256sum") < s.index("mv ")
    for code in ("BAD_SERVER_URL", "BEACON_FETCH_FAILED", "BEACON_DIGEST_MISMATCH"):
        assert code in s
    assert "exit 78" in s


def test_start_guards_arch_before_exec_and_bounds_package_calls():
    s = start_script(arch="amd64", prepare="packages", expires_at=EXP, profile="baseline")
    assert s.index("ARCH_MISMATCH") < s.index('"$AGENT" --server')
    assert "exec /cortexsim/cortexsim-agent" not in s
    assert "trap " in s and "--subreaper" in s and "LIFETIME_EXPIRED" in s
    assert "timeout 120" in s and "PREPARE_FAILED" in s
    assert "--enroll-token-file /secret/token" in s and f"--expires-at {EXP}" in s
    assert "cxs_" not in s


def test_start_renders_the_shared_identity_spec_accounts():
    s = start_script(arch="amd64", prepare="packages", expires_at=EXP, profile="baseline")
    for name in identity_spec.service_accounts():
        assert name in s
    for name in identity_spec.direct_identities():
        assert f" {name} " not in s.split("for u in", 1)[1].split(";", 1)[0] + " "


def test_prepare_none_installs_nothing():
    s = start_script(arch="amd64", prepare="none", expires_at="x", profile="baseline")
    assert "apt-get" not in s and "apk add" not in s and "PREPARE_SKIPPED" in s


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(arch="riscv", prepare="none", expires_at=EXP, profile="baseline"),
        dict(arch="amd64", prepare="bogus", expires_at=EXP, profile="baseline"),
        dict(arch="amd64", prepare="none", expires_at="x; rm -rf /", profile="baseline"),
    ],
)
def test_start_refuses_unsafe_inputs(kwargs):
    with pytest.raises(ValueError):
        start_script(**kwargs)


def _run_fetch(tmp_path, server_value, wget_stub=True):
    secret = tmp_path / "secret"
    secret.mkdir()
    (secret / "server").write_text(server_value)
    termlog = tmp_path / "termlog"
    bindir = tmp_path / "bin"
    bindir.mkdir()
    seen = tmp_path / "wget_args"
    if wget_stub:
        stub = bindir / "wget"
        stub.write_text(f'#!/bin/sh\nprintf \'%s\\n\' "$@" > "{seen}"\nexit 1\n')
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    script = tmp_path / "fetch.sh"
    script.write_text(fetch_script(arch="amd64", beacon_sha256=SHA))
    env = {
        **os.environ,
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "CORTEXSIM_SECRET_DIR": str(secret),
        "CORTEXSIM_TERMLOG": str(termlog),
    }
    proc = subprocess.run(["sh", str(script)], env=env, capture_output=True, text=True)
    return proc, termlog, seen


def test_fetch_rejects_bad_server_in_a_real_shell(tmp_path):
    proc, termlog, seen = _run_fetch(tmp_path, "simcore:8888")
    assert proc.returncode == 78
    assert "BAD_SERVER_URL" in termlog.read_text()
    assert not seen.exists()  # never reached wget


def test_fetch_strips_trailing_slash_before_building_the_url(tmp_path):
    proc, termlog, seen = _run_fetch(tmp_path, "http://simcore:8888/\n")
    assert proc.returncode == 78  # stub wget fails on purpose
    assert "BEACON_FETCH_FAILED" in termlog.read_text()
    args = seen.read_text()
    assert "http://simcore:8888/api/agents/binary?os=linux&arch=amd64" in args
    assert "//api" not in args


# ── supervision, executed for real under sh ─────────────────────────────────
_HOST_ARCH = {"x86_64": "amd64", "AMD64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(
    platform.machine(), "amd64"
)


def _start(tmp_path, beacon_body):
    ready = tmp_path / "ready"
    beacon = tmp_path / "beacon"
    beacon.write_text("#!/bin/sh\n" + beacon_body.replace("@READY@", str(ready)))
    beacon.chmod(beacon.stat().st_mode | stat.S_IEXEC)
    script = tmp_path / "start.sh"
    script.write_text(
        start_script(arch=_HOST_ARCH, prepare="none", expires_at=EXP, profile="baseline")
    )
    out = tmp_path / "out"
    env = {
        **os.environ,
        "CORTEXSIM_AGENT_BIN": str(beacon),
        "CORTEXSIM_TERMLOG": str(tmp_path / "termlog"),
    }
    proc = subprocess.Popen(
        ["sh", str(script)], env=env, stdout=out.open("w"), stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return proc, out, ready


def _wait_for(pred, timeout):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


_TERM_EXITS_ZERO = 'trap \'kill $s 2>/dev/null; exit 0\' TERM\nsleep 30 &\ns=$!\ntouch @READY@\nwait $s\n'


def test_beacon_exit_code_is_passed_through(tmp_path):
    proc, out, _ = _start(tmp_path, "exit 3\n")
    assert proc.wait(timeout=10) == 3
    assert "LIFETIME_EXPIRED" not in out.read_text()


def test_term_that_makes_the_beacon_exit_zero_is_a_shutdown_not_an_expiry(tmp_path):
    proc, out, ready = _start(tmp_path, _TERM_EXITS_ZERO)
    try:
        assert _wait_for(ready.exists, 5)
        proc.terminate()
        assert proc.wait(timeout=5) == 0
        assert "LIFETIME_EXPIRED" not in out.read_text()
    finally:
        if proc.poll() is None:
            proc.kill()


def test_unprompted_beacon_exit_zero_idles_until_termed(tmp_path):
    proc, out, _ = _start(tmp_path, "exit 0\n")
    try:
        assert _wait_for(lambda: "LIFETIME_EXPIRED" in out.read_text(), 5)
        time.sleep(1)
        assert proc.poll() is None  # still idling, not crash-looping
        proc.terminate()
        assert proc.wait(timeout=2) == 0
    finally:
        if proc.poll() is None:
            proc.kill()


# ── account creation, executed under sh with recording stubs (kind run, D3) ──
# alpine ships a www-data GROUP (gid 82) with no user, and `useradd www-data`
# refuses ("group www-data exists - ... use -g").

from engine.agent_manifest import account_creation_fragment


def _run_accounts(tmp_path, *, users, groups, tool):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    calls = tmp_path / "calls"
    stubs = {
        "id": 'for u in $KNOWN_USERS; do [ "$u" = "$1" ] && exit 0; done; exit 1\n',
        "getent": '[ "$1" = group ] || exit 2\n'
                  'for g in $KNOWN_GROUPS; do [ "$g" = "$2" ] && exit 0; done; exit 2\n',
        tool: f'echo "{tool} $*" >> "{calls}"\n',
    }
    for name, body in stubs.items():
        p = bindir / name
        p.write_text("#!/bin/sh\n" + body)
        p.chmod(0o755)
    status = tmp_path / "status"
    script = (
        f'STATUS="{status}"\n: > "$STATUS"\nPREP_BAD=0\n'
        'pf() { echo "PREPARE_FAILED $*" >> "$STATUS"; PREP_BAD=1; }\n'
        + account_creation_fragment(["www-data"])
        + 'echo "bad=$PREP_BAD"\n'
    )
    env = {"PATH": str(bindir), "KNOWN_USERS": " ".join(users), "KNOWN_GROUPS": " ".join(groups)}
    p = subprocess.run(["/bin/sh", "-c", script], env=env, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return (calls.read_text().splitlines() if calls.exists() else []), p.stdout, status.read_text()


@pytest.mark.parametrize("tool,want", [
    ("useradd", "useradd -m -s /bin/sh -g www-data www-data"),
    ("adduser", "adduser -D -s /bin/sh -G www-data www-data"),
])
def test_existing_group_without_user_gets_the_user_created_in_that_group(tmp_path, tool, want):
    calls, out, status = _run_accounts(tmp_path, users=[], groups=["www-data"], tool=tool)
    assert calls == [want]
    assert "bad=0" in out and status == ""


@pytest.mark.parametrize("tool,want", [
    ("useradd", "useradd -m -s /bin/sh -U www-data"),
    ("adduser", "adduser -D -s /bin/sh www-data"),
])
def test_absent_user_and_group_are_both_created(tmp_path, tool, want):
    calls, out, status = _run_accounts(tmp_path, users=[], groups=[], tool=tool)
    assert calls == [want]
    assert "bad=0" in out


@pytest.mark.parametrize("tool", ["useradd", "adduser"])
def test_existing_user_runs_nothing(tmp_path, tool):
    calls, out, _ = _run_accounts(tmp_path, users=["www-data"], groups=["www-data"], tool=tool)
    assert calls == []
    assert "bad=0" in out


def test_start_script_uses_the_account_fragment():
    s = start_script(arch="amd64", prepare="packages", expires_at=EXP, profile="baseline")
    assert account_creation_fragment(identity_spec.service_accounts()).strip() in s

"""A cron entry or systemd unit that root runs checks its scratch directory first.

SIM-EDR-023 installs a root cron entry and SIM-EDR-024 an enabled systemd
timer. Every five minutes each runs a script from a fixed directory under
/var/tmp. The steps check that directory before they use it
(test_scenario_sandbox_ownership.py), but the entry can outlive it: the beacon
does not run a scenario's ``cleanup`` commands, and a tmp cleaner or a partial
manual cleanup can remove the directory and leave the entry. Any local user
could then create the directory and the script inside it, and root would run
that script. A check inside the script does not help, because in that case the
script is theirs.

The rule these tests hold:
  - the command root runs checks the directory itself, in one lstat, before it
    runs anything from it: it must read ``directory:0:700``, the directory the
    creating step makes. Otherwise it runs nothing from it and retires the
    entry (cron removes its file, the unit disables its timer). /var/tmp is
    sticky, so once a root-owned directory passes, no other user can replace it;
  - the step that creates the directory first retires an entry left by an
    earlier run, so that entry cannot fire while the directory is gone;
  - the payload still expires on its own, and the scenario cleanup still
    removes the entry;
  - each re-execution still carries the hidden /var/tmp path the card's BIOC
    keys on.

The static tests read the scenario text and run anywhere. The behaviour tests
run the installed cron command and the unit's ExecStart against planted
directories. They need root and GNU stat, so they run only inside a container
(the CI image), like the behaviour tests in test_scenario_sandbox_ownership.py.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

CRON = {
    "sid": "SIM-EDR-023",
    "sandbox": "/var/tmp/.cortexsim-ice",
    "entry": "/etc/cron.d/cortexsim-ice",
    "script": "ice-run.sh",
}
UNIT = {
    "sid": "SIM-EDR-024",
    "sandbox": "/var/tmp/.cortexsim-sshd",
    "entry": "/etc/systemd/system/cortexsim-sync.service",
    "timer": "cortexsim-sync.timer",
    "script": "sync.sh",
}
# What the step that (re)creates the sandbox must do before its `rm -rf`.
RETIRE_FIRST = {
    "SIM-EDR-023": [r"rm -f /etc/cron\.d/cortexsim-ice(?=\s)"],
    "SIM-EDR-024": [
        r"systemctl disable --now cortexsim-sync\.timer(?=\s)",
        r"systemctl stop cortexsim-sync\.service(?=\s)",
        r"rm -f /etc/systemd/system/cortexsim-sync\.service /etc/systemd/system/cortexsim-sync\.timer(?=\s)",
    ],
}
SANDBOX = {CRON["sid"]: CRON["sandbox"], UNIT["sid"]: UNIT["sandbox"]}


def _scenario(sid: str) -> dict:
    for path in (REPO_ROOT / "scenarios").rglob("*.yml"):
        doc = yaml.safe_load(path.read_text())
        if isinstance(doc, dict) and doc.get("scenario_id") == sid:
            return doc
    raise AssertionError(f"{sid} not found")


def _written(doc: dict, target: str) -> str:
    """The body a step writes to ``target`` with ``cat > target <<'EOF'``."""
    for step in doc["steps"]:
        m = re.search(rf"cat > {re.escape(target)} <<'(\w+)'\n(.*?)\n\1\n", step.get("command") or "", re.S)
        if m:
            return m.group(2)
    raise AssertionError(f"no step writes {target}")


def _gate(sandbox: str) -> re.Pattern:
    """The one-lookup check: a real directory (not a symlink), owned by root, mode 0700."""
    return re.compile(rf'\[ "\$\(stat -c %F:%u:%a {re.escape(sandbox)} 2>/dev/null\)" = "directory:0:700" \]')


# --------------------------------------------------------------------------- cron


def _cron_command(field: str) -> str:
    """The command cron hands to ``/bin/sh -c``.

    In a crontab ``\\%`` is a literal ``%`` and a bare ``%`` ends the command
    (the rest becomes its stdin), so a bare ``%`` in ``stat -c %F`` would cut
    the check short and the entry would never run its payload.
    """
    out, i = [], 0
    while i < len(field):
        if field.startswith("\\%", i):
            out.append("%")
            i += 2
            continue
        assert field[i] != "%", f"a bare % ends the cron command after {field[:i]!r}: write \\%"
        out.append(field[i])
        i += 1
    return "".join(out)


def _cron_entry() -> tuple[str, str]:
    """(user, command) of the one job the scenario installs."""
    body = _written(_scenario(CRON["sid"]), CRON["entry"])
    jobs = [line for line in body.splitlines()
            if line.strip() and not line.lstrip().startswith("#") and not re.match(r"[A-Za-z_]+=", line)]
    assert len(jobs) == 1, jobs
    _m, _h, _dom, _mon, _dow, user, field = jobs[0].split(None, 6)
    return user, _cron_command(field)


def test_the_cron_entry_checks_the_directory_before_it_runs_anything_from_it():
    user, command = _cron_entry()
    assert user == "root"
    gate = _gate(CRON["sandbox"]).match(command)
    assert gate, f"the cron command does not start with the one-lookup check: {command!r}"
    rest = re.fullmatch(rf" && (?P<run>.+) \|\| rm -f {re.escape(CRON['entry'])}", command[gate.end():])
    assert rest, f"the cron command must run the payload only on a pass and remove its entry otherwise: {command!r}"
    assert f"{CRON['sandbox']}/{CRON['script']}" in rest.group("run")


# --------------------------------------------------------------------------- systemd


def _section(body: str, name: str) -> list[tuple[str, str]]:
    settings, current = [], None
    for line in body.splitlines():
        line = line.strip()
        if not line or line[0] in "#;":
            continue
        if line.startswith("["):
            current = line.strip("[]")
            continue
        if current == name:
            key, _, value = line.partition("=")
            settings.append((key.strip(), value.strip()))
    return settings


def _systemd_argv(value: str) -> list[str]:
    """The argv systemd runs for an Exec*= value: specifiers, then quoting, then ``$$``."""
    assert not re.findall(r"%[^%]", value.replace("%%", "")), (
        f"systemd expands a bare %X as a specifier: write %% for a literal % in {value!r}")
    held = value.replace("%%", "%").replace("$$", "\0")
    assert "${" not in held, f"systemd substitutes ${{...}}: write $$ for a literal $ in {value!r}"
    words = shlex.split(held)
    assert not [w for w in words if re.fullmatch(r"\$[A-Za-z_]\w*", w)], value
    return [w.replace("\0", "$") for w in words]


def _service() -> list[tuple[str, str]]:
    return _section(_written(_scenario(UNIT["sid"]), UNIT["entry"]), "Service")


def test_the_systemd_unit_checks_the_directory_before_it_runs_anything_from_it():
    service = _service()
    starts = [_systemd_argv(v) for k, v in service if k == "ExecStart"]
    others = [k for k, _ in service if k.startswith("Exec") and k != "ExecStart"]
    assert len(starts) == 1 and not others, (starts, others)
    argv = starts[0]
    assert argv[:2] == ["/bin/sh", "-c"] and len(argv) == 3, f"ExecStart must be one /bin/sh -c script: {argv}"
    script = argv[2]
    gate = _gate(UNIT["sandbox"]).match(script)
    assert gate, f"ExecStart does not start with the one-lookup check: {script!r}"
    rest = re.fullmatch(r" && (?P<run>.+) \|\| (?P<retire>systemctl [^;&|]+)", script[gate.end():])
    assert rest, f"ExecStart must run the payload only on a pass and disable its timer otherwise: {script!r}"
    assert f"{UNIT['sandbox']}/{UNIT['script']}" in rest.group("run")
    assert {"disable", "--now", UNIT["timer"]} <= set(rest.group("retire").split()), rest.group("retire")


# --------------------------------------------------------------------------- both


@pytest.mark.parametrize("sid", sorted(RETIRE_FIRST))
def test_the_step_that_recreates_the_directory_first_retires_an_entry_left_by_an_earlier_run(sid):
    sandbox = SANDBOX[sid]
    creating = []
    for step in _scenario(sid)["steps"]:
        command = step.get("command") or ""
        for var in re.findall(rf"(?m)^\s*([A-Za-z_]\w*)={re.escape(sandbox)}\s*$", command):
            m = re.search(rf'rm -rf "\${var}"\n\s*mkdir -m 700 "\${var}"', command)
            if m:
                creating.append((step["id"], command[:m.start()]))
    assert len(creating) == 1, creating
    step_id, before = creating[0]
    missing = [p for p in RETIRE_FIRST[sid] if not re.search(p, before)]
    assert not missing, f"{sid} {step_id} replaces {sandbox} without first doing: {missing}"


def test_the_payload_still_expires_and_the_cleanup_still_removes_the_entry():
    ice = _scenario(CRON["sid"])
    assert "rm -f /etc/cron.d/cortexsim-ice" in _written(ice, '"$D/ice-run.sh"')
    assert {"rm -f /etc/cron.d/cortexsim-ice", "rm -rf /var/tmp/.cortexsim-ice"} <= set(ice["cleanup"]["commands"])
    sync = _scenario(UNIT["sid"])
    assert "systemctl disable --now cortexsim-sync.timer" in _written(sync, '"$K/sync.sh"')
    cleanup = sync["cleanup"]["commands"]
    assert "systemctl disable --now cortexsim-sync.timer || true" in cleanup
    assert "rm -f /etc/systemd/system/cortexsim-sync.service /etc/systemd/system/cortexsim-sync.timer" in cleanup


def _bioc_command_line_pattern(sid: str) -> str:
    doc = _scenario(sid)
    card = next((REPO_ROOT / "detection_scanner" / "ttps").glob(f"{doc['steps'][0]['expected_detections'][0]['ttp_ref']}-*.json"))
    biocs = json.loads(card.read_text())["detections"]["biocs"]
    logic = next(b["logic"] for b in biocs if b["name"].endswith("Runs Payload From Hidden Var Tmp Directory"))
    return re.search(r'action_process_image_command_line ~= "([^"]+)"', logic).group(1)


def test_each_re_execution_still_carries_the_path_the_bioc_keys_on():
    """The BIOC matches the command line of the process cron or the systemd executor starts."""
    _, command = _cron_entry()
    assert re.search(_bioc_command_line_pattern(CRON["sid"]), command), command
    (start,) = [_systemd_argv(v) for k, v in _service() if k == "ExecStart"]
    assert re.search(_bioc_command_line_pattern(UNIT["sid"]), " ".join(start)), start


# --------------------------------------------------------------------------- behaviour

_IN_CONTAINER = os.path.exists("/.dockerenv") or os.environ.get("CORTEXSIM_SANDBOX_TESTS") == "1"
needs_root_in_a_container = pytest.mark.skipif(
    os.geteuid() != 0 or not _IN_CONTAINER,
    reason="plants root-owned and other-owned directories: runs only as root inside a container "
           "(the CI image), or with CORTEXSIM_SANDBOX_TESTS=1")
OTHER_UID = 4242  # stands in for another local user
PLANTS = ["missing", "other-owned", "symlink", "writable-by-others"]


def _plant(kind: str, sandbox: Path, elsewhere: Path, script: str, body: str) -> None:
    """Put at ``sandbox`` what another local user, or a cleanup, could leave there."""
    if kind == "missing":
        return
    target = elsewhere if kind == "symlink" else sandbox
    target.mkdir()
    (target / script).write_text(body)
    if kind == "other-owned":
        os.chmod(target, 0o700)
        os.chown(target, OTHER_UID, OTHER_UID)
    elif kind == "symlink":            # to root's own 0700 directory, which would itself pass
        os.chmod(target, 0o700)
        sandbox.symlink_to(target)
    elif kind == "writable-by-others":
        os.chmod(target, 0o777)


def _localise(text: str, spec: dict, tmp: Path) -> str:
    """The same text with the fixed system paths moved under ``tmp``."""
    return text.replace(spec["sandbox"], str(tmp / "sandbox")).replace(spec["entry"], str(tmp / "entry"))


def _sh(argv: list[str], tmp: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "PATH": f"{tmp / 'bin'}:{os.environ.get('PATH', '/usr/bin:/bin')}"}
    return subprocess.run(argv, capture_output=True, text=True, timeout=30, env=env)


def _stub_systemctl(tmp: Path) -> Path:
    """A systemctl on PATH that records its arguments instead of acting."""
    log = tmp / "systemctl.log"
    (tmp / "bin").mkdir(exist_ok=True)
    stub = tmp / "bin" / "systemctl"
    stub.write_text(f'#!/bin/sh\necho "$*" >> {log}\n')
    stub.chmod(0o755)
    return log


def _cron_run(tmp: Path) -> subprocess.CompletedProcess:
    _, command = _cron_entry()
    return _sh(["/bin/sh", "-c", _localise(command, CRON, tmp)], tmp)


def _unit_run(tmp: Path) -> subprocess.CompletedProcess:
    (start,) = [_systemd_argv(v) for k, v in _service() if k == "ExecStart"]
    return _sh([_localise(word, UNIT, tmp) for word in start], tmp)


def _clean(tmp: Path) -> None:
    for name in ("sandbox", "elsewhere"):
        path = tmp / name
        if path.is_symlink():
            path.unlink()
        else:
            shutil.rmtree(path, ignore_errors=True)


@needs_root_in_a_container
@pytest.mark.parametrize("plant", PLANTS)
def test_the_cron_entry_runs_nothing_from_a_directory_root_does_not_own(plant, tmp_path):
    ran = tmp_path / "ran"
    (tmp_path / "entry").write_text("leftover entry\n")
    _plant(plant, tmp_path / "sandbox", tmp_path / "elsewhere", CRON["script"], f"touch {ran}\n")
    try:
        done = _cron_run(tmp_path)
        assert not ran.exists(), (plant, done)
        assert not (tmp_path / "entry").exists(), ("the entry must remove itself", plant, done)
    finally:
        _clean(tmp_path)


@needs_root_in_a_container
@pytest.mark.parametrize("plant", PLANTS)
def test_the_unit_runs_nothing_from_a_directory_root_does_not_own(plant, tmp_path):
    ran = tmp_path / "ran"
    log = _stub_systemctl(tmp_path)
    _plant(plant, tmp_path / "sandbox", tmp_path / "elsewhere", UNIT["script"], f"touch {ran}\n")
    try:
        done = _unit_run(tmp_path)
        assert not ran.exists(), (plant, done)
        assert log.exists() and f"disable --now {UNIT['timer']}" in log.read_text(), (
            "the unit must disable its timer", plant, done)
    finally:
        _clean(tmp_path)


def _roots_sandbox(tmp: Path, name: str, body: str) -> Path:
    sandbox = tmp / "sandbox"
    sandbox.mkdir(mode=0o700)
    os.chmod(sandbox, 0o700)
    (sandbox / name).write_text(body)
    return sandbox


@needs_root_in_a_container
def test_the_cron_entry_runs_root_s_own_directory_and_the_payload_still_expires(tmp_path):
    ran = tmp_path / "ran"
    (tmp_path / "entry").write_text("entry\n")
    _roots_sandbox(tmp_path, CRON["script"], f"touch {ran}\n")
    try:
        done = _cron_run(tmp_path)
        assert ran.exists() and (tmp_path / "entry").exists(), done
        # The real payload, past its deadline, removes the entry itself.
        _clean(tmp_path)
        script = _localise(_written(_scenario(CRON["sid"]), '"$D/ice-run.sh"'), CRON, tmp_path)
        sandbox = _roots_sandbox(tmp_path, CRON["script"], script + "\n")
        (sandbox / "expires-at").write_text("0\n")
        done = _cron_run(tmp_path)
        assert not (tmp_path / "entry").exists(), done
        assert "expired" in (sandbox / "runs.log").read_text(), done
    finally:
        _clean(tmp_path)


@needs_root_in_a_container
def test_the_unit_runs_root_s_own_directory_and_the_payload_still_expires(tmp_path):
    ran = tmp_path / "ran"
    log = _stub_systemctl(tmp_path)
    _roots_sandbox(tmp_path, UNIT["script"], f"touch {ran}\n")
    try:
        done = _unit_run(tmp_path)
        assert ran.exists() and not log.exists(), done
        # The real payload, past its deadline, disables its own timer.
        _clean(tmp_path)
        script = _localise(_written(_scenario(UNIT["sid"]), '"$K/sync.sh"'), UNIT, tmp_path)
        sandbox = _roots_sandbox(tmp_path, UNIT["script"], script + "\n")
        (sandbox / "expires-at").write_text("0\n")
        done = _unit_run(tmp_path)
        assert f"disable --now {UNIT['timer']}" in log.read_text(), done
        assert "expired" in (sandbox / "runs.log").read_text(), done
    finally:
        _clean(tmp_path)

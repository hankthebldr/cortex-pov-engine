"""A root unit or cron line never runs a program from a /tmp path another user can create.

SIM-CDR-006 enabled a root systemd unit (``Restart=always``) whose ExecStart
was a fixed path under /tmp that the scenario never created, plus a root
crontab watchdog that re-enables the unit every ten minutes. SIM-CDR-002 added
a root crontab line that runs /tmp/.xmr/xmrig every five minutes. /tmp is
emptied at boot and by tmp cleaners, and the beacon does not run a scenario's
``cleanup``, so these entries outlive what they point at. Any local user who
creates that path then has root run their program.

The rule these tests hold (the same one SIM-EDR-023/024 follow):
  - the scenario makes its own directory, owned by the account the entry runs
    as and not writable by others, and the entry runs nothing from it unless
    one lstat reads it that way. Otherwise the entry retires itself: the unit
    disables itself, a crontab line removes itself. /tmp is sticky, so a
    directory that passes cannot be replaced by another user;
  - the check is in ExecStart, not ExecCondition=, which systemd before 243
    ignores with a log warning;
  - the step that makes the directory first retires a unit or watchdog left by
    an earlier run;
  - the SIM-CDR-006 payload still expires on its own, the cleanups still remove
    everything, and the card IOCs still name the files the run creates;
  - the cards' illustrative payloads stop (``set -eu``) unless the directory
    they make passes the same check, and install the checked entries.

The static tests read scenario and card text and run anywhere. The behaviour
tests run the unit's ExecStart, the cron commands and the card payloads against
planted directories, with ``systemctl`` and ``crontab`` stubbed. They need root
and GNU stat, so they run only inside a container (the CI image).
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

NODE = {
    "sid": "SIM-CDR-006",
    "sandbox": "/tmp/.cortexsim-node",
    "unit": "/etc/systemd/system/cortexsim-node.service",
    "service": "cortexsim-node.service",
    "beacon": ".cortexsim_node_beacon",
}
OLD_NODE_TARGET = "/tmp/.cortexsim_node_beacon"   # the unit's target before the fix
XMR = {"sid": "SIM-CDR-002", "sandbox": "/tmp/.xmr", "binary": "xmrig"}
ROOT_OWNER = "0"
MY_OWNER = r"\$\(id -u\)"


def _scenario(sid: str) -> dict:
    for path in (REPO_ROOT / "scenarios").rglob("*.yml"):
        doc = yaml.safe_load(path.read_text())
        if isinstance(doc, dict) and doc.get("scenario_id") == sid:
            return doc
    raise AssertionError(f"{sid} not found")


def _step(sid: str, step_id: str) -> str:
    return next(s["command"] for s in _scenario(sid)["steps"] if s["id"] == step_id)


def _card(sid: str) -> dict:
    ref = _scenario(sid)["steps"][0]["expected_detections"][0]["ttp_ref"]
    (path,) = (REPO_ROOT / "detection_scanner" / "ttps").glob(f"{ref}-*.json")
    return json.loads(path.read_text())


def _written(text: str, target: str) -> str:
    """The body ``text`` writes to ``target`` with ``cat > target <<'EOF'``."""
    m = re.search(rf"cat > {re.escape(target)} <<'(\w+)'\n(.*?)\n\1\n", text, re.S)
    assert m, f"nothing writes {target}"
    return m.group(2)


def _gate(sandbox: str, owner: str) -> str:
    """The one-lookup check: a real directory (not a symlink), this owner, this mode."""
    return (rf'\[ "\$\(stat -c %F:%u:%a {re.escape(sandbox)} 2>/dev/null\)" = '
            rf'"directory:{owner}:(?P<mode>[0-7]{{3}})" \]')


def _cron_command(field: str) -> str:
    """What cron hands to /bin/sh -c: ``\\%`` is a literal %, a bare % ends the command."""
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


def _quoted_jobs(text: str, schedule: str) -> list[str]:
    """Crontab lines ``text`` writes as a quoted string that starts with ``schedule``."""
    jobs = []
    for m in re.finditer(re.escape(schedule + " "), text):
        quote = text[m.start() - 1]
        if quote in "'\"":
            jobs.append(text[m.start():text.index(quote, m.start())])
    return jobs


def _job_command(job: str) -> str:
    """A user-crontab line (no user field) -> the command cron runs."""
    return _cron_command(job.split(None, 5)[5])


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


def _node_service() -> list[tuple[str, str]]:
    return _section(_written(_step(NODE["sid"], "step-01"), NODE["unit"]), "Service")


def _node_start() -> list[str]:
    (start,) = [_systemd_argv(v) for k, v in _node_service() if k == "ExecStart"]
    return start


def _watchdog() -> str:
    (job,) = _quoted_jobs(_step(NODE["sid"], "step-03"), "*/10 * * * *")
    return job


def _miner_jobs() -> list[str]:
    return _quoted_jobs(_step(XMR["sid"], "step-05"), "*/5 * * * *")


# --------------------------------------------------------------------------- static: SIM-CDR-006


def test_the_node_unit_checks_its_directory_before_it_runs_anything():
    execs = [k for k, _ in _node_service() if k.startswith("Exec")]
    # The check lives in ExecStart: systemd before 243 ignores ExecCondition=.
    assert execs == ["ExecStart"], execs
    argv = _node_start()
    assert argv[:2] == ["/bin/sh", "-c"] and len(argv) == 3, f"ExecStart must be one /bin/sh -c script: {argv}"
    m = re.fullmatch(_gate(NODE["sandbox"], ROOT_OWNER)
                     + rf" && (?P<run>.+) \|\| systemctl disable --now {re.escape(NODE['service'])}", argv[2])
    assert m and m.group("mode") == "700", f"ExecStart must check root's 0700 directory and disable itself otherwise: {argv[2]!r}"
    assert f"{NODE['sandbox']}/{NODE['beacon']}" in m.group("run")


def test_the_watchdog_re_enables_the_unit_only_while_the_directory_checks_out():
    command = _job_command(_watchdog())
    m = re.fullmatch(rf"if {_gate(NODE['sandbox'], ROOT_OWNER)}; then (?P<then>.+); else (?P<else>.+); fi", command)
    assert m and m.group("mode") == "700", f"the watchdog must check root's 0700 directory first: {command!r}"
    assert f"systemctl enable --now {NODE['service']}" in m.group("then")
    assert m.group("else") == f"crontab -l | grep -v {NODE['service']} | crontab -", m.group("else")


def test_the_step_that_makes_the_node_directory_first_retires_a_leftover_unit_and_watchdog():
    command = _step(NODE["sid"], "step-01")
    var = re.search(rf"(?m)^\s*([A-Za-z_]\w*)={re.escape(NODE['sandbox'])}\s*$", command)
    assert var, f"step-01 does not make {NODE['sandbox']}"
    made = re.search(rf'rm -rf "\${var.group(1)}"\n\s*mkdir -m 700 "\${var.group(1)}"', command)
    assert made, f"step-01 does not make {NODE['sandbox']} exclusively"
    before = command[:made.start()]
    assert f"systemctl disable --now {NODE['service']}" in before
    assert re.search(rf"crontab -l[^\n]*\| grep -v {re.escape(NODE['service'])} \| crontab -", before)


def test_the_node_beacon_still_expires_and_the_cleanup_still_removes_everything():
    doc = _scenario(NODE["sid"])
    beacon = _written(_step(NODE["sid"], "step-01"), f'"$N/{NODE["beacon"]}"')
    assert "expires-at" in beacon
    assert f"grep -v {NODE['service']} | crontab -" in beacon
    assert f"systemctl disable --now {NODE['service']}" in beacon
    cleanup = doc["cleanup"]["commands"]
    assert f"rm -f {NODE['unit']}" in cleanup
    assert any(f"grep -v {NODE['service']} | crontab -" in c for c in cleanup)
    assert f"rm -rf {NODE['sandbox']}" in cleanup


def test_the_card_iocs_still_name_what_the_run_creates():
    iocs = {(i["ioc_type"], i["value"]) for i in _card(NODE["sid"])["detections"]["iocs"]}
    assert {("filepath", NODE["unit"]), ("filename", NODE["beacon"])} <= iocs
    # The run writes the unit there, and creates the beacon file under that name.
    step = _step(NODE["sid"], "step-01")
    assert _written(step, NODE["unit"]) and _written(step, f'"$N/{NODE["beacon"]}"')


# --------------------------------------------------------------------------- static: SIM-CDR-002


def _file_gate(path: str, owner: str) -> str:
    """The same lookup for the program itself: a regular file (not a symlink), this owner, this mode."""
    return (rf'\[ "\$\(stat -c %F:%u:%a {re.escape(path)} 2>/dev/null\)" = '
            rf'"regular file:{owner}:(?P<fmode>[0-7]{{3}})" \]')


def test_the_miner_cron_lines_check_the_directory_and_the_program_before_they_run_it():
    """The miner comes out of a downloaded archive, so the program's own owner and mode are checked too."""
    jobs = _miner_jobs()
    assert len(jobs) == 2, jobs           # root's crontab, then nobody's spool file
    binary = f"{XMR['sandbox']}/{XMR['binary']}"
    root = re.fullmatch(rf"if {_gate(XMR['sandbox'], MY_OWNER)} && {_file_gate(binary, MY_OWNER)}; then (?P<then>.+); "
                        r"else crontab -l \| grep -v xmrig \| crontab -; fi", _job_command(jobs[0]))
    assert root, f"root's line must check its own 0755 directory and its own program first: {jobs[0]!r}"
    nobody = re.fullmatch(rf"if {_gate(XMR['sandbox'], ROOT_OWNER)} && {_file_gate(binary, ROOT_OWNER)}; "
                          r"then (?P<then>.+); fi", _job_command(jobs[1]))
    assert nobody, f"nobody's line must check root's 0755 directory and root's program first: {jobs[1]!r}"
    for m in (root, nobody):
        assert m.group("mode") == "755"
        assert int(m.group("fmode"), 8) & 0o022 == 0, "a program others can write to would still run"
        # The XMRig execution BIOC keys on "xmrig" in the command line cron starts.
        assert binary in m.group("then")


def _tar_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if re.search(r"\btar -x", line)]


def test_the_miner_is_extracted_as_the_running_user_with_a_set_mode():
    """GNU tar run as root keeps the archive's owner and mode unless told not to."""
    for where, text in (("SIM-CDR-002 step-02", _step(XMR["sid"], "step-02")),
                        ("TTP-2026-0023 payload", _card(XMR["sid"])["execution"]["payload"]["code"])):
        (line,) = _tar_lines(text)
        assert "--no-same-owner" in line and "--no-same-permissions" in line, (where, line)
        assert re.search(rf"chmod 755 (?:{re.escape(XMR['sandbox'])}|\"\$D)/{XMR['binary']}\b", text), where


# --------------------------------------------------------------------------- static: card payloads

CARDS = {
    NODE["sid"]: {"sandbox": NODE["sandbox"], "owner": ROOT_OWNER, "mode": "700"},
    XMR["sid"]: {"sandbox": XMR["sandbox"], "owner": MY_OWNER, "mode": "755"},
}


def _payload(sid: str) -> str:
    return _card(sid)["execution"]["payload"]["code"]


def _sandbox_var(code: str, sandbox: str) -> str | None:
    m = re.search(rf"(?:^|[;\s])([A-Za-z_]\w*)={re.escape(sandbox)}(?=[;\s]|$)", code)
    return m.group(1) if m else None


def _payload_head(code: str, sandbox: str) -> str:
    """The payload up to the first line that uses something inside the sandbox."""
    var = _sandbox_var(code, sandbox)
    uses = [sandbox + "/"] + ([f'"${var}/', f"${var}/"] if var else [])
    lines = code.splitlines()
    for i, line in enumerate(lines):
        if any(use in line for use in uses):
            return "\n".join(lines[:i])
    return code


@pytest.mark.parametrize("sid", sorted(CARDS))
def test_a_card_payload_stops_before_it_uses_a_directory_it_does_not_own(sid):
    rule, code = CARDS[sid], _payload(sid)
    head = _payload_head(code, rule["sandbox"])
    assert re.search(r"(?m)^set -eu$", head), "the payload must stop at the first failing command"
    var = _sandbox_var(head, rule["sandbox"])
    assert var, f"the payload never makes {rule['sandbox']} before it uses it"
    assert f"mkdir -p {rule['sandbox']}" not in code and f'mkdir -p "${var}"' not in code
    assert re.search(rf'rm -rf "\${var}"[;\n]\s*mkdir -m {rule["mode"]} "\${var}"', head), "not made exclusively"
    assert re.search(rf'\[ "\$\(stat -c %F:%u:%a "\${var}" 2>/dev/null\)" = "directory:{rule["owner"]}:{rule["mode"]}" \] '
                     rf'\|\| \{{[^\n]*exit 1; \}}', head), "no one-lookup ownership check before the first use"


def test_the_card_payloads_install_the_checked_entries():
    node = _payload(NODE["sid"])
    start = next(line for line in _written(_step(NODE["sid"], "step-01"), NODE["unit"]).splitlines()
                 if line.startswith("ExecStart="))
    assert start in node and _watchdog() in node
    assert _miner_jobs()[0] in _payload(XMR["sid"])


# --------------------------------------------------------------------------- behaviour

_IN_CONTAINER = os.path.exists("/.dockerenv") or os.environ.get("CORTEXSIM_SANDBOX_TESTS") == "1"
needs_root_in_a_container = pytest.mark.skipif(
    os.geteuid() != 0 or not _IN_CONTAINER,
    reason="plants root-owned and other-owned directories: runs only as root inside a container "
           "(the CI image), or with CORTEXSIM_SANDBOX_TESTS=1")
OTHER_UID = 4242  # stands in for another local user
PLANTS = ["missing", "other-owned", "symlink", "writable-by-others"]


def _localise(text: str, tmp: Path) -> str:
    """The same text with the fixed system paths moved under ``tmp``."""
    for old, new in ((OLD_NODE_TARGET, f"{tmp}/sandbox/{NODE['beacon']}"),
                     (NODE["sandbox"], f"{tmp}/sandbox"), (XMR["sandbox"], f"{tmp}/sandbox"),
                     ("/etc/systemd/system/", f"{tmp}/units/")):
        text = text.replace(old, new)
    return text


def _stubs(tmp: Path) -> dict[str, Path]:
    """systemctl and crontab on PATH that record their arguments instead of acting."""
    (tmp / "bin").mkdir(exist_ok=True)
    (tmp / "units").mkdir(exist_ok=True)
    logs = {name: tmp / f"{name}.log" for name in ("systemctl", "crontab")}
    bodies = {
        "systemctl": f'echo "$*" >> {logs["systemctl"]}\n[ "$1" = is-enabled ] && exit 1\nexit 0\n',
        "crontab": f'echo "$*" >> {logs["crontab"]}\ncase "$1" in -l) echo "# {NODE["service"]} xmrig";; '
                   f'-) cat > /dev/null;; esac\nexit 0\n',
    }
    for name in ("curl", "tar", "useradd"):
        bodies[name] = "exit 0\n"
    for name, body in bodies.items():
        stub = tmp / "bin" / name
        stub.write_text("#!/bin/sh\n" + body)
        stub.chmod(0o755)
    return logs


def _log(path: Path) -> str:
    return path.read_text() if path.exists() else ""


def _sh(argv: list[str], tmp: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "PATH": f"{tmp / 'bin'}:{os.environ.get('PATH', '/usr/bin:/bin')}"}
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=30, env=env)
    except OSError as exc:                    # the old unit's target did not exist
        return subprocess.CompletedProcess(argv, 127, "", str(exc))


def _plant(kind: str, tmp: Path, mode: int, files: dict[str, str]) -> None:
    """Put at the sandbox path what another local user, or a cleanup, could leave there."""
    sandbox, elsewhere = tmp / "sandbox", tmp / "elsewhere"
    if kind == "missing":
        return
    target = elsewhere if kind == "symlink" else sandbox
    target.mkdir()
    for name, body in files.items():
        (target / name).write_text(body)
        (target / name).chmod(0o755)
    if kind == "other-owned":
        os.chmod(target, mode)
        os.chown(target, OTHER_UID, OTHER_UID)
    elif kind == "symlink":                   # to root's own directory, which would itself pass
        os.chmod(target, mode)
        sandbox.symlink_to(target)
    elif kind == "writable-by-others":
        os.chmod(target, 0o777)
    else:
        os.chmod(target, mode)                # "roots-own"


def _clean(tmp: Path) -> None:
    for name in ("sandbox", "elsewhere"):
        path = tmp / name
        if path.is_symlink():
            path.unlink()
        else:
            shutil.rmtree(path, ignore_errors=True)


def _marker(ran: Path) -> str:
    return f"#!/bin/sh\ntouch {ran}\n"


@needs_root_in_a_container
@pytest.mark.parametrize("plant", PLANTS)
def test_the_node_unit_runs_nothing_from_a_directory_root_does_not_own(plant, tmp_path):
    logs, ran = _stubs(tmp_path), tmp_path / "ran"
    _plant(plant, tmp_path, 0o700, {NODE["beacon"]: _marker(ran)})
    try:
        done = _sh([_localise(word, tmp_path) for word in _node_start()], tmp_path)
        assert not ran.exists(), (plant, done)
        assert f"disable --now {NODE['service']}" in _log(logs["systemctl"]), ("the unit must disable itself", plant, done)
    finally:
        _clean(tmp_path)


@needs_root_in_a_container
def test_the_node_unit_runs_root_s_own_directory_and_the_beacon_still_expires(tmp_path):
    logs, ran = _stubs(tmp_path), tmp_path / "ran"
    _plant("roots-own", tmp_path, 0o700, {NODE["beacon"]: _marker(ran)})
    try:
        done = _sh([_localise(word, tmp_path) for word in _node_start()], tmp_path)
        assert ran.exists() and "disable" not in _log(logs["systemctl"]), done
        # The real beacon, past its deadline, drops the watchdog and disables its unit.
        _clean(tmp_path)
        beacon = _localise(_written(_step(NODE["sid"], "step-01"), f'"$N/{NODE["beacon"]}"'), tmp_path)
        _plant("roots-own", tmp_path, 0o700, {NODE["beacon"]: beacon + "\n", "expires-at": "0\n"})
        done = _sh([_localise(word, tmp_path) for word in _node_start()], tmp_path)
        assert "-" in _log(logs["crontab"]).split(), ("the watchdog was not dropped", done)
        assert f"disable --now {NODE['service']}" in _log(logs["systemctl"]), done
    finally:
        _clean(tmp_path)


@needs_root_in_a_container
@pytest.mark.parametrize("plant", PLANTS + ["roots-own"])
def test_the_watchdog_re_enables_the_unit_only_from_root_s_own_directory(plant, tmp_path):
    logs = _stubs(tmp_path)
    _plant(plant, tmp_path, 0o700, {})
    try:
        done = _sh(["/bin/sh", "-c", _localise(_job_command(_watchdog()), tmp_path)], tmp_path)
        enabled = f"enable --now {NODE['service']}" in _log(logs["systemctl"])
        dropped = "-" in _log(logs["crontab"]).split()
        assert (enabled, dropped) == ((True, False) if plant == "roots-own" else (False, True)), (plant, done)
    finally:
        _clean(tmp_path)


@needs_root_in_a_container
@pytest.mark.parametrize("plant", PLANTS + ["roots-own"])
@pytest.mark.parametrize("which", ["root-crontab", "nobody-spool"])
def test_the_miner_cron_lines_run_the_miner_only_from_root_s_own_directory(which, plant, tmp_path):
    logs, ran = _stubs(tmp_path), tmp_path / "ran"
    _plant(plant, tmp_path, 0o755, {XMR["binary"]: _marker(ran)})
    job = _miner_jobs()[0 if which == "root-crontab" else 1]
    try:
        done = _sh(["/bin/sh", "-c", _localise(_job_command(job), tmp_path)], tmp_path)
        assert ran.exists() == (plant == "roots-own"), (which, plant, done)
        if which == "root-crontab" and plant != "roots-own":
            assert "-" in _log(logs["crontab"]).split(), ("the line must remove itself", plant, done)
    finally:
        _clean(tmp_path)


@needs_root_in_a_container
@pytest.mark.parametrize("program", ["other-owned", "group-writable", "writable-by-others", "symlink"])
@pytest.mark.parametrize("which", ["root-crontab", "nobody-spool"])
def test_the_miner_cron_lines_refuse_a_program_root_does_not_control(which, program, tmp_path):
    """Root's own 0755 directory, but the program in it is not root's alone: it does not run."""
    logs, ran = _stubs(tmp_path), tmp_path / "ran"
    _plant("roots-own", tmp_path, 0o755, {})
    binary = tmp_path / "sandbox" / XMR["binary"]
    if program == "symlink":                   # to a root-owned 0755 program, which would itself pass
        (tmp_path / "real-xmrig").write_text(_marker(ran))
        (tmp_path / "real-xmrig").chmod(0o755)
        binary.symlink_to(tmp_path / "real-xmrig")
    else:
        binary.write_text(_marker(ran))
        binary.chmod({"other-owned": 0o755, "group-writable": 0o775, "writable-by-others": 0o777}[program])
        if program == "other-owned":
            os.chown(binary, OTHER_UID, OTHER_UID)
    job = _miner_jobs()[0 if which == "root-crontab" else 1]
    try:
        done = _sh(["/bin/sh", "-c", _localise(_job_command(job), tmp_path)], tmp_path)
        assert not ran.exists(), (which, program, done)
        if which == "root-crontab":
            assert "-" in _log(logs["crontab"]).split(), ("the line must remove itself", program, done)
    finally:
        _clean(tmp_path)


@needs_root_in_a_container
def test_the_extracted_miner_belongs_to_the_extracting_user_and_nobody_else_can_write_it(tmp_path):
    """Run step-02 against an archive whose member belongs to uid 4242 with mode 0777."""
    import tarfile                                                    # noqa: PLC0415
    member = tmp_path / "member"
    member.write_text("#!/bin/sh\nexit 0\n")
    archive = tmp_path / "xmrig.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        info = tar.gettarinfo(str(member), arcname="xmrig-6.21.0/xmrig")
        info.uid = info.gid = OTHER_UID
        info.uname = info.gname = ""             # so tar maps the numeric uid, not a name
        info.mode = 0o777
        with member.open("rb") as fh:
            tar.addfile(info, fh)
    _stubs(tmp_path)
    curl = tmp_path / "bin" / "curl"            # "downloads" the archive above to the -o path
    curl.write_text(f'#!/bin/sh\nwhile [ $# -gt 0 ]; do [ "$1" = -o ] && out=$2; shift; done\ncp {archive} "$out"\n')
    (tmp_path / "bin" / "tar").unlink()         # the real tar does the extraction
    _plant("roots-own", tmp_path, 0o755, {})
    try:
        done = _sh(["/bin/sh", "-c", _localise(_step(XMR["sid"], "step-02"), tmp_path)], tmp_path)
        st = os.lstat(tmp_path / "sandbox" / XMR["binary"])
        assert st.st_uid == 0 and st.st_mode & 0o022 == 0, (oct(st.st_mode), st.st_uid, done)
    finally:
        _clean(tmp_path)


def _payload_run(sid: str, tmp: Path) -> subprocess.CompletedProcess:
    """The card payload up to its first use of the directory, then a marker."""
    _stubs(tmp)
    head = _localise(_payload_head(_payload(sid), CARDS[sid]["sandbox"]), tmp)
    return _sh(["/bin/bash", "-c", head + "\necho REACHED\n"], tmp)


@needs_root_in_a_container
@pytest.mark.parametrize("race", ["recreated-after-rm", "recreated-and-mkdir-skipped"])
@pytest.mark.parametrize("sid", sorted(CARDS))
def test_a_card_payload_stops_when_another_user_owns_the_directory(sid, race, tmp_path):
    """The rm stub recreates the directory as another user between ``rm -rf`` and ``mkdir``."""
    sandbox = tmp_path / "sandbox"
    real = {tool: shutil.which(tool) for tool in ("rm", "mkdir", "chown")}
    (tmp_path / "bin").mkdir()
    for name, body in (("rm", f'{real["rm"]} "$@"\nfor a in "$@"; do last=$a; done\n'
                              f'if [ "$last" = "{sandbox}" ]; then {real["mkdir"]} -m 755 "$last"; '
                              f'{real["chown"]} {OTHER_UID}:{OTHER_UID} "$last"; fi\n'),
                       ("mkdir", "exit 0\n")):
        if name == "mkdir" and race != "recreated-and-mkdir-skipped":
            continue
        (tmp_path / "bin" / name).write_text("#!/bin/sh\n" + body)
        (tmp_path / "bin" / name).chmod(0o755)
    sandbox.mkdir()                                   # planted before the payload runs
    os.chown(sandbox, OTHER_UID, OTHER_UID)
    try:
        done = _payload_run(sid, tmp_path)
        assert "REACHED" not in done.stdout and done.returncode != 0, (race, done)
    finally:
        _clean(tmp_path)


@needs_root_in_a_container
@pytest.mark.parametrize("sid", sorted(CARDS))
def test_a_card_payload_makes_its_own_directory_and_carries_on(sid, tmp_path):
    try:
        done = _payload_run(sid, tmp_path)
        assert "REACHED" in done.stdout, done
        st = os.lstat(tmp_path / "sandbox")
        assert (st.st_uid, oct(st.st_mode & 0o7777)) == (0, "0o" + CARDS[sid]["mode"]), done
    finally:
        _clean(tmp_path)

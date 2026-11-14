"""A scenario's scratch directory cannot be taken over by another local user.

Twelve scenarios keep their artifacts in a fixed directory under /tmp, /var/tmp
or /dev/shm. Made with ``mkdir -p``, that directory could be created first by
any local user, who then owned it: ``mkdir -p`` succeeds on an existing
directory and a later ``chmod`` does not change the owner. Steps running as
root then wrote into it and ran files from it. An adversarial review reproduced
this as a root code execution on a sibling scenario (2026-11-13).

The rule the steps now follow:
  - the step that creates the directory removes whatever is at the path and
    makes it with ``mkdir -m MODE`` and no ``-p``, so an existing entry stops
    the step;
  - every other step that uses it first checks, in ONE lookup, that it is a
    directory (not a symlink) with that mode, owned by the running user, or by
    root where a service account runs inside a root-made directory:
    ``[ "$(stat -c %F:%u:%a "$D" 2>/dev/null)" = "directory:$(id -u):700" ]``.

One lookup matters. An earlier form used three tests (``-d``, ``! -L``, ``-O``).
Where root follows another user's symlink (``fs.protected_symlinks`` off), a
user exchanging a directory and a symlink between those tests got a root step
past it in 73 of 300 passes under dash and 50 of 300 under bash; the one-lookup
form let none through.

The first test reads the scenario text and runs anywhere. The second runs each
step's own guard against a planted directory; it needs root and fixed system
paths, so it runs only inside a container (the CI image).
"""
from __future__ import annotations

import os
import pwd
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# scenario id -> the fixed scratch directories its steps create
SANDBOXES = {
    "SIM-CDR-002": ["/tmp/.xmr"],
    "SIM-CDR-005": ["/tmp/wf_sim"],
    "SIM-CDR-006": ["/tmp/.cortexsim-node"],
    "SIM-CDR-018": ["/tmp/cortexsim-cdr-018"],
    "SIM-EDR-023": ["/var/tmp/.cortexsim-ice"],
    "SIM-EDR-024": ["/var/tmp/.cortexsim-sshd"],
    "SIM-KOI-012": ["/var/tmp/.cortexsim-axios"],
    "SIM-MP-019": ["/tmp/cortexsim-mp019"],
    "SIM-MP-024": ["/tmp/cortexsim-mp-024", "/dev/shm/.gs-cortexsim-mp024"],
    "SIM-MP-032": ["/tmp/cortexsim-mp-032"],
    "SIM-MP-035": ["/tmp/cortexsim-mp-035"],
    "SIM-MP-036": ["/tmp/cortexsim-mp-036"],
    "SIM-MP-037": ["/tmp/cortexsim-mp-037", "/dev/shm/cortexsim-mp-037"],
}
RUNS_AS_BEACON = {"root", "direct", "container-runtime"}
OTHER_UID = 4242  # stands in for another local user


def _scenarios() -> dict[str, dict]:
    found = {}
    for path in (REPO_ROOT / "scenarios").rglob("*.yml"):
        doc = yaml.safe_load(path.read_text())
        if isinstance(doc, dict) and doc.get("scenario_id") in SANDBOXES:
            found[doc["scenario_id"]] = doc
    assert set(found) == set(SANDBOXES)
    return found


def _refs(command: str, sandbox: str) -> list[str]:
    """Every way ``command`` spells the sandbox root: the path, and variables set to it."""
    names = re.findall(rf"(?m)(?:^|;)\s*([A-Za-z_][A-Za-z0-9_]*)={re.escape(sandbox)}\s*(?:;|$)", command)
    return [sandbox] + [form for n in names for form in (f'"${n}"', f"${n}")]


def _guard(command: str, sandbox: str) -> tuple[str, re.Match, re.Match] | None:
    """(kind, first guard, the guard that defines the kind) for ``sandbox``, or None.

    A step that creates the directory is a 'create' step even when an
    ownership-tested read comes first (SIM-KOI-012 reads a pidfile that way).
    """
    for ref in _refs(command, sandbox):
        r = re.escape(ref)
        create = re.search(rf"rm -rf {r}(?: &&|\n)\s*mkdir -m [0-7]{{3}} {r}(?: \|\| \{{[^\n]*?exit 1; \}})?", command)
        lookup = rf"\$\(stat -c %F:%u:%a {r} 2>/dev/null\)"
        mine = r'"directory:\$\(id -u\):[0-7]{3}"'
        check = re.search(
            rf'(?:\[ "{lookup}" = {mine} \]'                                               # the owner's check
            rf'|s={lookup}; \[ "\$s" = "directory:0:[0-7]{{3}}" \] \|\| \[ "\$s" = {mine} \])'   # root's or mine
            rf"(?: \|\| \{{[^\n]*?exit 1; \}})?", command)
        found = [m for m in (create, check) if m]
        if found:
            return ("create" if create else "check"), min(found, key=lambda m: m.start()), create or check
    return None


def _first_use(command: str, sandbox: str) -> int | None:
    """Offset of the first reference to something inside the sandbox."""
    hits = [command.find(ref.rstrip('"') + "/") for ref in _refs(command, sandbox)]
    hits = [h for h in hits if h >= 0]
    return min(hits) if hits else None


def _cases():
    for sid, doc in sorted(_scenarios().items()):
        default = (doc.get("execution_identity") or {}).get("default")
        for sandbox in SANDBOXES[sid]:
            for step in doc["steps"]:
                command = step.get("command") or ""
                if sandbox in command:
                    yield pytest.param(sid, step["id"], step.get("identity") or default, sandbox, command,
                                       id=f"{sid}:{step['id']}:{sandbox}")


CASES = list(_cases())


@pytest.mark.parametrize("sid, step_id, identity, sandbox, command", CASES)
def test_a_step_guards_its_scratch_directory_before_using_it(sid, step_id, identity, sandbox, command):
    assert f"mkdir -p {sandbox}\n" not in command + "\n" and f"mkdir -p {sandbox} " not in command
    guard = _guard(command, sandbox)
    assert guard is not None, "no creation or ownership guard for " + sandbox
    use = _first_use(command, sandbox)
    assert use is None or guard[1].start() < use, "the sandbox is used before it is guarded"


def test_each_scratch_directory_is_created_once_with_the_mode_its_checks_expect():
    for sid, doc in _scenarios().items():
        for sandbox in SANDBOXES[sid]:
            guards = [(s["id"], g) for s in doc["steps"] if (g := _guard(s.get("command") or "", sandbox))]
            made = [re.search(r"mkdir -m ([0-7]{3})", g[2].group(0)).group(1) for _, g in guards if g[0] == "create"]
            assert len(made) == 1, (sid, sandbox, made)
            expected = {mode for _, g in guards if g[0] == "check"
                        for mode in re.findall(r"directory:[^:]+:([0-7]{3})", g[2].group(0))}
            assert expected <= set(made), (sid, sandbox, "created " + made[0], "checked for " + str(expected))


# --------------------------------------------------------------------------- behaviour

_IN_CONTAINER = os.path.exists("/.dockerenv") or os.environ.get("CORTEXSIM_SANDBOX_TESTS") == "1"
needs_root_in_a_container = pytest.mark.skipif(
    os.geteuid() != 0 or not _IN_CONTAINER,
    reason="plants directories at fixed system paths as root: runs only as root inside a container "
           "(the CI image), or with CORTEXSIM_SANDBOX_TESTS=1")


def _guard_script(command: str, sandbox: str) -> str:
    """The step's own guard, as written, with the assignments it relies on."""
    _, _, match = _guard(command, sandbox)
    head = command[:match.start()]
    same_line = head.rsplit("\n", 1)[-1]          # e.g. `D=/tmp/.xmr; ` before the guard
    keep = [line.strip() for line in head[:len(head) - len(same_line)].splitlines()
            if re.fullmatch(r"\s*(?:set -eu|[A-Za-z_][A-Za-z0-9_]*=\S+)\s*", line)]
    prefix = same_line if re.fullmatch(r'(?:\s*|.*"\s*)[A-Za-z_][A-Za-z0-9_]*=\S+;\s*', same_line) else ""
    prefix = re.sub(r'^.*"\s*(?=[A-Za-z_][A-Za-z0-9_]*=)', "", prefix)   # a one-line YAML command
    return "\n".join(keep + [prefix + match.group(0), "echo REACHED"]) + "\n"


def _run(script: str, identity: str) -> subprocess.CompletedProcess:
    argv = ["/bin/sh", "-c", script]
    if identity not in RUNS_AS_BEACON:
        argv = ["runuser", "-u", identity, "--"] + argv
    return subprocess.run(argv, capture_output=True, text=True, timeout=30)


def _uid(identity: str) -> int:
    return 0 if identity in RUNS_AS_BEACON else pwd.getpwnam(identity).pw_uid


def _clear(*paths: str) -> None:
    for path in paths:
        if os.path.islink(path):
            os.unlink(path)
        else:
            shutil.rmtree(path, ignore_errors=True)


def _plant_dir(path: str, uid: int) -> None:
    os.mkdir(path, 0o755)
    Path(path, "planted").write_text("planted\n")
    os.chown(path, uid, uid)
    os.chown(os.path.join(path, "planted"), uid, uid)


@needs_root_in_a_container
@pytest.mark.parametrize("sid, step_id, identity, sandbox, command", CASES)
def test_a_planted_scratch_directory_is_never_used(sid, step_id, identity, sandbox, command, tmp_path):
    kind = _guard(command, sandbox)[0]
    script = _guard_script(command, sandbox)
    runner = _uid(identity)
    elsewhere = str(tmp_path / "elsewhere")
    os.chmod(tmp_path, 0o755)
    try:
        if kind == "create":
            # Another user's directory is at the path: the step ends with its own
            # fresh directory, or it stops. It never carries on in theirs.
            _clear(sandbox); _plant_dir(sandbox, OTHER_UID)
            done = _run(script, identity)
            if "REACHED" in done.stdout:
                assert os.stat(sandbox).st_uid == runner and not os.listdir(sandbox), done
            else:
                assert done.returncode != 0, done
            # A symlink at the path is replaced, and its target is left alone.
            _clear(sandbox); _plant_dir(elsewhere, OTHER_UID); os.symlink(elsewhere, sandbox)
            done = _run(script, identity)
            if "REACHED" in done.stdout:
                assert not os.path.islink(sandbox) and os.stat(sandbox).st_uid == runner, done
            else:
                assert done.returncode != 0, done
            assert os.listdir(elsewhere) == ["planted"]
            # Nothing at the path: the step makes the directory and owns it.
            _clear(sandbox)
            done = _run(script, identity)
            assert "REACHED" in done.stdout and os.stat(sandbox).st_uid == runner, done
        else:
            mode = int(re.search(r"directory:\$\(id -u\):([0-7]{3})", script).group(1), 8)
            owner = 0 if "directory:0:" in script else runner   # a service account inside a root-made directory
            for plant in ("missing", "other-owned", "symlink", "writable-by-others"):
                _clear(sandbox, elsewhere)
                if plant == "other-owned":
                    _plant_dir(sandbox, OTHER_UID)
                elif plant == "symlink":      # to a directory that would itself pass
                    os.mkdir(elsewhere); os.chmod(elsewhere, mode); os.chown(elsewhere, owner, owner)
                    os.symlink(elsewhere, sandbox)
                elif plant == "writable-by-others":
                    os.mkdir(sandbox); os.chmod(sandbox, 0o777); os.chown(sandbox, owner, owner)
                done = _run(script, identity)
                assert done.returncode == 1 and "REACHED" not in done.stdout, (plant, done)
            # The directory the creating step makes is accepted.
            _clear(sandbox, elsewhere)
            os.mkdir(sandbox); os.chmod(sandbox, mode); os.chown(sandbox, owner, owner)
            done = _run(script, identity)
            assert done.returncode == 0 and "REACHED" in done.stdout, done
    finally:
        _clear(sandbox, elsewhere)

"""Every POSIX bundle's cleanup() must run to its last line, and must not try
to run a Windows command.

Why this exists. A scenario's ``cleanup.commands`` is one list for every
target, so a scenario that also emits a Windows bundle carries Windows teardown
lines (``reg.exe delete ...``, ``cmd.exe /c if exist "%TEMP%\\x" rd ...``,
``Remove-Item ...``). ``generate_bash`` used to write ALL of them into
``cleanup()``. Two things went wrong with that, at different times:

* A double-quoted ``$env:`` line raised ``env: unbound variable`` (the bundle
  runs under ``set -u``), which ends the shell from inside the EXIT trap: every
  later line is skipped, so the Linux teardown never runs and "Cleanup
  complete" is never logged. ``bash -n`` and shellcheck pass it.
* Rewritten ``$``-free, each line still printed ``command not found`` into the
  teardown log a DC keeps and shows a customer: 25 lines across 10 scenarios,
  7 in SIM-EDR-003 alone. The same lines went into the K8s bootstrap as
  ``( cmd.exe ... ) || true``.

The generator now withholds a Windows-only line from the POSIX resolution
(``resolve_target(..., "posix").skipped_cleanup``) and the bash bundle names it
in a ``# skipped (Windows-only): ...`` comment instead of running it.

What this guard does. For every POSIX-emittable scenario it renders the real
bash bundle with ``generate_bash``, lifts the ``cleanup()`` function out
verbatim, and runs it under ``set -euo pipefail`` from the EXIT trap, as the
bundle does. Nothing it names may touch this machine:

* ``PATH`` is an empty directory and ``command_not_found_handle`` logs each
  external command (``rm``, ``pkill``, ``userdel``, ``systemctl`` ...) instead
  of running it, then returns 127 as a real host's "command not found" does;
* the shell is restricted (``set -r``) before cleanup runs, so no output
  redirection, no command named by a path, no ``cd``/``exec``;
* ``kill`` is disabled as a builtin, so it is logged like an external command;
* ``HOME`` and the working directory are throwaway directories.

A DEBUG trap records the line number of every simple command bash reaches.
The guard asserts that each cleanup line the resolver kept was reached, that
"Cleanup complete" was logged, and that no command bash was asked to run is a
Windows one (an ``.exe``, a Verb-Noun cmdlet or a cmd built-in). That last
check reads what bash ATTEMPTED, so it does not depend on the generator's own
classifier being right.

Static checks sit beside it: the bundle's ``cleanup()`` holds exactly the lines
the resolver kept, every withheld line is named in a comment, and only a line
that starts with a Windows command is ever withheld.

This proves the bash side only, on this host's bash. It says nothing about the
Windows bundle, and nothing here ran on Windows.
"""
from __future__ import annotations

import functools
import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from engine import push_generator as pg
from engine import scenario_loader as sl

REPO = Path(__file__).resolve().parents[2]


def _load_corpus() -> dict[str, dict]:
    logging.disable(logging.CRITICAL)
    try:
        out: dict[str, dict] = {}
        for path in sl._find_yaml_files(str(REPO / "scenarios")):
            schema, err = sl._parse_and_validate(path)
            if err or schema is None:
                continue
            out[schema.scenario_id] = schema.model_dump()
        return out
    finally:
        logging.disable(logging.NOTSET)


CORPUS = _load_corpus()
POSIX_BUNDLES = sorted(
    sid for sid, d in CORPUS.items() if "posix" in pg.pushable_targets(d)
)


def _cleanup_lines(d: dict) -> list[str]:
    return [c for c in ((d.get("cleanup") or {}).get("commands") or []) if isinstance(c, str)]


def _kept(d: dict) -> list[str]:
    """The cleanup lines the POSIX resolution keeps, in order."""
    return [c for c, _ in pg.resolve_target(d, "posix").cleanup]


def test_the_posix_bundle_set_is_not_empty():
    """A zero is degraded, not ok: an empty set would green every check below."""
    assert len(POSIX_BUNDLES) >= 150, len(POSIX_BUNDLES)
    assert sum(1 for s in POSIX_BUNDLES if _cleanup_lines(CORPUS[s])) >= 150


# ---------------------------------------------------------------------------
# A bash that can run the harness
# ---------------------------------------------------------------------------

def _bash_major(path: str) -> int:
    try:
        out = subprocess.run(
            [path, "-c", "printf '%s' \"${BASH_VERSINFO[0]}\""],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        return int(out or 0)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return 0


def _find_bash() -> str | None:
    """bash >= 4 (command_not_found_handle). macOS /bin/bash is 3.2."""
    candidates = [shutil.which("bash"), "/opt/homebrew/bin/bash", "/usr/local/bin/bash",
                  "/usr/bin/bash", "/bin/bash"]
    for c in candidates:
        if c and Path(c).exists() and _bash_major(c) >= 4:
            return c
    return None


BASH = _find_bash()


def test_a_bash_that_can_run_the_harness_is_available_in_ci():
    """A skipped execution guard in CI reads exactly like a passing one."""
    if os.environ.get("CI"):
        assert BASH, "CI must provide bash >= 4 to execute the bundle cleanup() guard"


# ---------------------------------------------------------------------------
# The harness
# ---------------------------------------------------------------------------

_CLEANUP_START = "cleanup() {\n"
_CLEANUP_END = '    log INFO "Cleanup complete"\n}\n'
_RUN_HDR = re.compile(r'(?m)^    log INFO "Running cleanup for scenario=[^"\n]*"\n')
_SKIP_COMMENT = "    # skipped (Windows-only): "

# 127 is what a real host returns for a missing command. A stub that returned 0
# would run the right-hand side of `missing-tool && rm -rf ...`, which no
# customer host does.
_PRELUDE = """\
set -euo pipefail
TIMESTAMP="20261106_000000"
LOG_FILE="/tmp/cortexsim-{sid}-${{TIMESTAMP}}.log"
log() {{ printf 'CXS-LOG [%s] %s\\n' "$1" "$2"; }}
command_not_found_handle() {{ printf 'CXS-STUB %s\\n' "$*"; return 127; }}
enable -n kill
"""

_EPILOGUE = """\
trap 'printf "CXS-LINE %s\\n" "$LINENO"' DEBUG
set -o functrace
trap cleanup EXIT
set -r
exit 0
"""


def _cleanup_function(bundle: str) -> str:
    start = bundle.index(_CLEANUP_START)
    end = bundle.index(_CLEANUP_END, start) + len(_CLEANUP_END)
    return bundle[start:end]


def _cleanup_body(fn: str) -> str:
    """The lines between 'Running cleanup' and 'Cleanup complete'."""
    hdr = _RUN_HDR.search(fn)
    assert hdr, "cleanup() has no 'Running cleanup' line; the template changed"
    return fn[hdr.end(): fn.index(_CLEANUP_END)]


def _harness(sid: str, d: dict) -> tuple[str, list[tuple[str, int, int]]]:
    """(script, [(kept cleanup line, first script line, last script line)]).

    Each line the resolver kept is located in the rendered function, in order,
    so a line the resolver keeps but the bundle does not carry fails here.
    """
    fn = _cleanup_function(pg.generate_bash(d))
    prelude = _PRELUDE.format(sid=sid)
    script = prelude + fn + _EPILOGUE
    hdr = _RUN_HDR.search(fn)
    assert hdr, f"{sid}: cleanup() has no 'Running cleanup' line; the template changed"
    first_fn_line = prelude.count("\n") + 1
    spans: list[tuple[str, int, int]] = []
    pos = hdr.end()
    for text in _kept(d):
        needle = "    " + text + "\n"
        at = fn.find(needle, pos)
        assert at >= 0, (
            f"{sid}: the POSIX resolution keeps a cleanup line the bash bundle "
            f"does not carry: {text.splitlines()[0][:140]!r}"
        )
        line = first_fn_line + fn[:at].count("\n")
        spans.append((text, line, line + text.count("\n")))
        pos = at + len(needle)
    return script, spans


def _run_cleanup(sid: str, d: dict) -> tuple[subprocess.CompletedProcess, list[tuple[str, int, int]]]:
    script, spans = _harness(sid, d)
    with tempfile.TemporaryDirectory(prefix="cxs-cleanup-") as tmp:
        root = Path(tmp)
        (root / "empty-path").mkdir()
        (root / "home").mkdir()
        (root / "cwd").mkdir()
        f = root / "cleanup.sh"
        f.write_text(script, encoding="utf-8")
        env = {
            "PATH": str(root / "empty-path"),
            "HOME": str(root / "home"),
            "USER": "root",
            "LOGNAME": "root",
            "SHELL": "/bin/bash",
            "LANG": "C",
            "TERM": "dumb",
        }
        proc = subprocess.run(
            [BASH, "--noprofile", "--norc", str(f)],
            cwd=root / "cwd", env=env, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=30,
        )
    return proc, spans


@functools.lru_cache(maxsize=None)
def _corpus_run(sid: str) -> tuple[subprocess.CompletedProcess, tuple[tuple[str, int, int], ...]]:
    """One harness run per corpus scenario, shared by the checks below."""
    proc, spans = _run_cleanup(sid, CORPUS[sid])
    return proc, tuple(spans)


def _gaps(proc: subprocess.CompletedProcess, spans) -> list[str]:
    hit = {int(m.group(1)) for m in re.finditer(r"(?m)^CXS-LINE (\d+)$", proc.stdout)}
    gaps = [
        f"cleanup line never reached: {cmd.splitlines()[0][:140]!r}"
        for cmd, a, b in spans
        if not any(a <= n <= b for n in hit)
    ]
    if "CXS-LOG [INFO] Cleanup complete" not in proc.stdout:
        gaps.append("'Cleanup complete' was never logged: cleanup() ended early")
    if gaps:
        err = "\n    ".join(proc.stderr.strip().splitlines()[-4:])
        gaps.append(f"bash exit {proc.returncode}; stderr tail:\n    {err}")
    return gaps


def _cleanup_gaps(sid: str, d: dict) -> list[str]:
    return _gaps(*_run_cleanup(sid, d))


#: argv[0] of a command only Windows has. Deliberately NOT the generator's
#: classifier: this reads what bash was asked to run.
_WIN_EXE_ARGV0 = re.compile(r"(?i)\.(?:exe|cmd|bat|ps1)$")
_CMDLET_ARGV0 = re.compile(r"^[A-Z][a-z]+-[A-Z][A-Za-z]+$")
_CMD_BUILTIN_ARGV0 = frozenset({"del", "rd", "erase"})


def _is_windows_argv0(tok: str) -> bool:
    return bool(
        _WIN_EXE_ARGV0.search(tok) or _CMDLET_ARGV0.match(tok) or tok.lower() in _CMD_BUILTIN_ARGV0
    )


def _windows_commands_attempted(proc: subprocess.CompletedProcess) -> list[str]:
    """Every stubbed command whose argv[0] is a Windows command. On a customer
    host each one is a ``command not found`` line in the teardown log."""
    return [
        m.group(0)[len("CXS-STUB "):][:140]
        for m in re.finditer(r"(?m)^CXS-STUB (\S+).*$", proc.stdout)
        if _is_windows_argv0(m.group(1))
    ]


@pytest.mark.skipif(BASH is None, reason="no bash >= 4 on this host")
@pytest.mark.parametrize("sid", POSIX_BUNDLES)
def test_bash_bundle_cleanup_runs_every_line(sid):
    proc, spans = _corpus_run(sid)
    gaps = _gaps(proc, spans)
    assert not gaps, f"{sid}:\n  " + "\n  ".join(gaps)


@pytest.mark.skipif(BASH is None, reason="no bash >= 4 on this host")
@pytest.mark.parametrize("sid", POSIX_BUNDLES)
def test_bash_bundle_cleanup_attempts_no_windows_command(sid):
    """RG-PUSH-01. SIM-EDR-003's Linux bundle asked bash for schtasks.exe,
    cmd.exe (x5) and reg.exe during teardown: seven `command not found` lines
    in the log a DC shows the customer, reading as a failed cleanup."""
    proc, _ = _corpus_run(sid)
    tried = _windows_commands_attempted(proc)
    assert not tried, (
        f"{sid}: the bash cleanup() tried to run {len(tried)} Windows command(s):\n  "
        + "\n  ".join(tried)
    )


# ---------------------------------------------------------------------------
# The harness itself must be able to fail, and must not touch this host
# ---------------------------------------------------------------------------

def _synthetic(cleanup: list[str]) -> dict:
    return {
        "scenario_id": "SIM-TEST-001",
        "steps": [{"id": "step-01", "command": "echo hi", "identity": "direct"}],
        "cleanup": {"commands": cleanup},
    }


@pytest.mark.skipif(BASH is None, reason="no bash >= 4 on this host")
def test_the_harness_catches_an_unbound_variable_stranding_later_teardown():
    """`set -u` stays on inside cleanup(): an unbound `$VAR` ends the shell from
    the EXIT trap and strands every later line."""
    d = _synthetic([
        'rm -rf "$CORTEXSIM_NEVER_SET/stage"',
        "rm -rf /tmp/cortexsim-mp016",
    ])
    gaps = _cleanup_gaps("SIM-TEST-001", d)
    assert any("rm -rf /tmp/cortexsim-mp016" in g for g in gaps), gaps
    assert any("Cleanup complete" in g for g in gaps), gaps


@pytest.mark.skipif(BASH is None, reason="no bash >= 4 on this host")
def test_a_dollar_env_windows_line_no_longer_strands_posix_teardown():
    """The 7e6e91e MP-016 shape, verbatim: a double-quoted $env: Windows line
    ahead of the Linux rm. It used to end the shell; it is now withheld."""
    d = _synthetic([
        'Remove-Item -Recurse -Force -ErrorAction SilentlyContinue -Path "$env:TEMP\\cortexsim-mp016"',
        "rm -rf /tmp/cortexsim-mp016",
    ])
    proc, spans = _run_cleanup("SIM-TEST-001", d)
    assert _gaps(proc, spans) == []
    assert [s[0] for s in spans] == ["rm -rf /tmp/cortexsim-mp016"]
    assert "CXS-STUB rm -rf /tmp/cortexsim-mp016" in proc.stdout
    assert _windows_commands_attempted(proc) == []


@pytest.mark.skipif(BASH is None, reason="no bash >= 4 on this host")
def test_cmd_shaped_windows_lines_are_withheld_and_posix_lines_still_run():
    d = _synthetic([
        "rm -rf /tmp/cortexsim-mp016",
        'cmd.exe /c if exist "%TEMP%\\cortexsim-mp016" rd /s /q "%TEMP%\\cortexsim-mp016"',
        'reg.exe delete "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run" /v cortexsim-x /f',
        "echo '[CortexSim] cleanup complete'",
    ])
    proc, spans = _run_cleanup("SIM-TEST-001", d)
    assert _gaps(proc, spans) == []
    assert [s[0] for s in spans] == ["rm -rf /tmp/cortexsim-mp016", "echo '[CortexSim] cleanup complete'"]
    assert _windows_commands_attempted(proc) == []
    assert "[CortexSim] cleanup complete" in proc.stdout


@pytest.mark.skipif(BASH is None, reason="no bash >= 4 on this host")
def test_the_windows_command_detector_can_fail():
    """`sc.exe delete X`, `Clear-History` and a bare `del` carry no marker the
    generator treats as Windows, so they still reach bash. The detector must
    see them: it reads argv[0], not the generator's classifier."""
    lines = ["sc.exe delete cortexsim-x", "Clear-History", "del x"]
    d = _synthetic(["rm -rf /tmp/x", *lines])
    assert _kept(d) == ["rm -rf /tmp/x", *lines], "the fixture must reach bash to prove anything"
    proc, _ = _run_cleanup("SIM-TEST-001", d)
    assert _windows_commands_attempted(proc) == lines


@pytest.mark.parametrize("tok", ["rm", "apt-get", "docker-compose", "pkill", "x86_64-linux-gnu-gcc",
                                 "systemctl", "update-rc.d", "kubectl"])
def test_the_windows_command_detector_ignores_posix_commands(tok):
    assert not _is_windows_argv0(tok)


@pytest.mark.skipif(BASH is None, reason="no bash >= 4 on this host")
def test_a_missing_tool_does_not_run_the_right_hand_side_of_and():
    """The stub returns 127, as a real host does: `missing && rm` must not
    reach rm. A stub returning 0 hid this."""
    d = _synthetic(["cortexsim-absent-tool --flag && rm -rf /tmp/cortexsim-rhs", "rm -f /tmp/after"])
    proc, spans = _run_cleanup("SIM-TEST-001", d)
    assert "CXS-STUB cortexsim-absent-tool --flag" in proc.stdout
    assert "CXS-STUB rm -rf /tmp/cortexsim-rhs" not in proc.stdout
    assert "CXS-STUB rm -f /tmp/after" in proc.stdout
    assert _gaps(proc, spans) == []


@pytest.mark.skipif(BASH is None, reason="no bash >= 4 on this host")
def test_the_harness_runs_nothing_and_writes_nothing():
    """rm is logged, not run; a redirect is refused, so no file appears."""
    with tempfile.TemporaryDirectory(prefix="cxs-victim-") as victim:
        keep = Path(victim) / "keep.txt"
        keep.write_text("x", encoding="utf-8")
        made = Path(victim) / "made.txt"
        d = _synthetic([f"rm -f {keep}", f"echo pwned > {made}", "kill -0 1"])
        proc, _ = _run_cleanup("SIM-TEST-001", d)
        assert keep.exists(), "the harness ran a real rm"
        assert not made.exists(), "the harness let a redirect write a file"
        assert f"CXS-STUB rm -f {keep}" in proc.stdout
        assert "CXS-STUB kill -0 1" in proc.stdout
        assert "CXS-LOG [INFO] Cleanup complete" in proc.stdout


# ---------------------------------------------------------------------------
# Static: what the bundle carries is what the resolver kept, and what it
# withholds is named and is really Windows
# ---------------------------------------------------------------------------

def _windows_headed(cmd: str) -> bool:
    """The line STARTS with a Windows command. Independent of the generator."""
    toks = cmd.strip().split()
    return bool(toks) and _is_windows_argv0(toks[0])


def _flat(cmd: str) -> str:
    return " ".join(cmd.split())


@pytest.mark.parametrize("sid", POSIX_BUNDLES)
def test_bash_cleanup_is_exactly_what_the_resolver_kept(sid):
    """generate_bash and resolve_target must not disagree about cleanup, and a
    withheld line must be visible to a DC reading the bundle."""
    d = CORPUS[sid]
    r = pg.resolve_target(d, "posix")
    body = _cleanup_body(_cleanup_function(pg.generate_bash(d)))
    comments = [ln for ln in body.split("\n") if ln.startswith(_SKIP_COMMENT)]
    code = "".join(
        ln + "\n" for ln in body.split("\n")[:-1] if not ln.startswith(_SKIP_COMMENT)
    )
    if r.cleanup:
        assert code == "".join(f"    {c}\n" for c, _ in r.cleanup), sid
    elif r.skipped_cleanup:
        assert code == "", f"{sid}: code in a cleanup() the resolver left empty:\n{code}"
    else:
        assert code == "    true  # no cleanup commands defined\n", sid
    assert [c[len(_SKIP_COMMENT):] for c in comments] == [_flat(c) for c in r.skipped_cleanup], sid
    assert set(_kept(d)) | set(r.skipped_cleanup) == set(_cleanup_lines(d)), (
        f"{sid}: a cleanup line is neither kept nor named as withheld"
    )


def test_windows_cleanup_lines_are_withheld_from_the_posix_resolution():
    """Every cleanup line that starts with a Windows command, in a bundle a
    Linux host runs, is withheld. A zero is degraded: the corpus has them."""
    reached: list[str] = []
    seen = 0
    for sid in POSIX_BUNDLES:
        d = CORPUS[sid]
        r = pg.resolve_target(d, "posix")
        kept = _kept(d)
        for cmd in _cleanup_lines(d):
            if not _windows_headed(cmd):
                continue
            seen += 1
            if cmd in kept or cmd not in r.skipped_cleanup:
                reached.append(f"{sid}: {_flat(cmd)[:140]}")
    assert seen >= 20, f"only {seen} Windows-headed cleanup lines found; the walk is not covering the corpus"
    assert not reached, (
        f"{len(reached)} Windows cleanup line(s) still reach a POSIX cleanup():\n  "
        + "\n  ".join(reached)
    )


@pytest.mark.parametrize("sid", POSIX_BUNDLES)
def test_only_a_windows_headed_line_is_withheld_from_a_posix_cleanup(sid):
    """The other direction: a withheld line is teardown a Linux host never
    runs, so withholding a POSIX line would leave residue with no error."""
    wrong = [
        _flat(c)[:140] for c in pg.resolve_target(CORPUS[sid], "posix").skipped_cleanup
        if not _windows_headed(c)
    ]
    assert not wrong, f"{sid}: POSIX-looking teardown withheld from the bash bundle:\n  " + "\n  ".join(wrong)

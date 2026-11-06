"""Every POSIX bundle's cleanup() must run to its last line.

Why this exists. ``push_generator.resolve_target(..., "posix")`` keeps EVERY
cleanup line, Windows ones included, and ``generate_bash`` writes them into
``cleanup()`` verbatim. That is by design (cleanup never gates emittability),
and a Windows line such as ``reg.exe delete ... /f`` is a harmless
command-not-found on Linux. But the bundle runs under ``set -euo pipefail``
and ``cleanup()`` only turns off ``-e``. ``-u`` stays on, and bash still
expands ``$`` inside double quotes, so a Windows teardown line like::

    Remove-Item -Recurse -Force -Path "$env:TEMP\\cortexsim-mp016"

raises ``env: unbound variable``. In a non-interactive shell that ends the
shell from inside the EXIT trap: every later cleanup line is skipped, so the
Linux teardown (``rm -rf /tmp/cortexsim-mp016``) never runs, and the
"Cleanup complete" log line is never written. ``bash -n`` and shellcheck pass
it; only executing ``cleanup()`` shows it.

What this guard does. For every POSIX-emittable scenario it renders the real
bash bundle with ``generate_bash``, lifts the ``cleanup()`` function out
verbatim, and runs it under ``set -euo pipefail`` from the EXIT trap, as the
bundle does. Nothing it names may touch this machine:

* ``PATH`` is an empty directory and ``command_not_found_handle`` logs each
  external command (``rm``, ``pkill``, ``userdel``, ``systemctl`` ...) instead
  of running it;
* the shell is restricted (``set -r``) before cleanup runs, so no output
  redirection, no command named by a path, no ``cd``/``exec``;
* ``kill`` is disabled as a builtin, so it is logged like an external command;
* ``HOME`` and the working directory are throwaway directories.

A DEBUG trap records the line number of every simple command bash reaches.
The guard asserts that each rendered cleanup line was reached and that
"Cleanup complete" was logged.

Two static checks sit beside it: a Windows-shaped cleanup line in a POSIX
bundle must be bash-inert (no ``$`` or backtick outside single quotes, no
unquoted redirection or background ``&``, no ``\\"`` inside double quotes),
and POSIX teardown lines come before the first Windows line, so a Windows
line cannot strand them.

This proves the bash side only, on this host's bash. It says nothing about the
Windows bundle, and nothing here ran on Windows.
"""
from __future__ import annotations

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
    sid for sid, d in CORPUS.items()
    if not pg.push_not_supported(d) and "posix" in pg.emittable_targets(d)
)


def _cleanup_lines(d: dict) -> list[str]:
    return [c for c in ((d.get("cleanup") or {}).get("commands") or []) if isinstance(c, str)]


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

_PRELUDE = """\
set -euo pipefail
TIMESTAMP="20261106_000000"
LOG_FILE="/tmp/cortexsim-{sid}-${{TIMESTAMP}}.log"
log() {{ printf 'CXS-LOG [%s] %s\\n' "$1" "$2"; }}
command_not_found_handle() {{ printf 'CXS-STUB %s\\n' "$*"; return 0; }}
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


def _harness(sid: str, d: dict) -> tuple[str, list[tuple[str, int, int]]]:
    """(script, [(cleanup line, first script line, last script line)])."""
    fn = _cleanup_function(pg.generate_bash(d))
    prelude = _PRELUDE.format(sid=sid)
    script = prelude + fn + _EPILOGUE
    hdr = _RUN_HDR.search(fn)
    assert hdr, f"{sid}: cleanup() has no 'Running cleanup' line; the template changed"
    line = prelude.count("\n") + fn[: hdr.end()].count("\n") + 1
    spans: list[tuple[str, int, int]] = []
    # The same list generate_bash renders, one `    {cmd}` per entry.
    for cmd in ((d.get("cleanup") or {}).get("commands") or []):
        text = str(cmd)
        n = text.count("\n") + 1
        if text.strip():
            spans.append((text, line, line + n - 1))
        line += n
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


def _cleanup_gaps(sid: str, d: dict) -> list[str]:
    proc, spans = _run_cleanup(sid, d)
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


@pytest.mark.skipif(BASH is None, reason="no bash >= 4 on this host")
@pytest.mark.parametrize("sid", POSIX_BUNDLES)
def test_bash_bundle_cleanup_runs_every_line(sid):
    gaps = _cleanup_gaps(sid, CORPUS[sid])
    assert not gaps, f"{sid}:\n  " + "\n  ".join(gaps)


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
def test_the_harness_catches_a_dollar_env_windows_line_stranding_posix_teardown():
    """The 7e6e91e MP-016 shape, verbatim: a double-quoted $env: Windows line
    ahead of the Linux rm."""
    d = _synthetic([
        'Remove-Item -Recurse -Force -ErrorAction SilentlyContinue -Path "$env:TEMP\\cortexsim-mp016"',
        "rm -rf /tmp/cortexsim-mp016",
    ])
    gaps = _cleanup_gaps("SIM-TEST-001", d)
    assert any("rm -rf /tmp/cortexsim-mp016" in g for g in gaps), gaps
    assert any("Cleanup complete" in g for g in gaps), gaps


@pytest.mark.skipif(BASH is None, reason="no bash >= 4 on this host")
def test_the_harness_accepts_cmd_shaped_windows_lines():
    d = _synthetic([
        "rm -rf /tmp/cortexsim-mp016",
        'cmd.exe /c if exist "%TEMP%\\cortexsim-mp016" rd /s /q "%TEMP%\\cortexsim-mp016"',
        'reg.exe delete "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run" /v cortexsim-x /f',
        "echo '[CortexSim] cleanup complete'",
    ])
    assert _cleanup_gaps("SIM-TEST-001", d) == []


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
# Static: a Windows line in a bash bundle must be bash-inert, and must not
# precede POSIX teardown
# ---------------------------------------------------------------------------

def _bash_hazards(cmd: str) -> list[str]:
    """What bash would do to ``cmd`` beyond 'command not found'."""
    bad: list[str] = []
    state: str | None = None
    i, n = 0, len(cmd)
    while i < n:
        c = cmd[i]
        if state == "'":
            if c == "'":
                state = None
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            if state == '"' and cmd[i + 1] == '"':
                bad.append('`\\"` inside double quotes: bash and cmd split this differently')
            i += 2
            continue
        if c == "$":
            bad.append("`$` expands in bash (unbound under set -u, or worse)")
        elif c == "`":
            bad.append("a backtick runs a bash command substitution")
        elif state is None:
            if c == "'":
                state = "'"
            elif c == '"':
                state = '"'
            elif c in "<>":
                bad.append(f"an unquoted `{c}` is a bash redirection on the Linux host")
            elif c == "&":
                if i + 1 < n and cmd[i + 1] == "&":
                    i += 2
                    continue
                bad.append("an unquoted `&` backgrounds a bash job")
            elif c in "()":
                bad.append(f"an unquoted `{c}` is bash syntax")
        elif state == '"' and c == '"':
            state = None
        i += 1
    if state is not None:
        bad.append(f"unterminated {state} quote")
    return sorted(set(bad))


def _windows_line(cmd: str) -> bool:
    return not pg.is_posix_shaped(cmd)


def _echo_only(cmd: str) -> bool:
    """A completion message, not teardown: one echo/printf, nothing chained."""
    code = pg.code_only(cmd).strip()
    return bool(re.match(r"(?:echo|printf)\b", code)) and not re.search(r"[;&|<>`]|\$\(", code)


@pytest.mark.parametrize("sid", POSIX_BUNDLES)
def test_windows_cleanup_lines_in_a_bash_bundle_are_bash_inert(sid):
    bad = [
        f"{cmd[:120]!r}: {h}"
        for cmd in _cleanup_lines(CORPUS[sid]) if _windows_line(cmd)
        for h in _bash_hazards(cmd)
    ]
    assert not bad, f"{sid}:\n  " + "\n  ".join(bad)


@pytest.mark.parametrize("sid", POSIX_BUNDLES)
def test_posix_teardown_precedes_every_windows_cleanup_line(sid):
    lines = _cleanup_lines(CORPUS[sid])
    win = [i for i, c in enumerate(lines) if _windows_line(c)]
    posix = [i for i, c in enumerate(lines) if not _windows_line(c) and not _echo_only(c)]
    late = [lines[i] for i in posix if win and i > win[0]]
    assert not late, f"{sid}: POSIX teardown after a Windows line:\n  " + "\n  ".join(late)


@pytest.mark.parametrize("cmd", [
    'Remove-Item -Recurse -Force -Path "$env:TEMP\\cortexsim-mp016"',
    "Get-Content $env:TEMP\\x | Remove-Item",
    "del /f /q %TEMP%\\x 2>nul",
    "cmd.exe /c rd /s /q x & echo done",
    'cmd.exe /c if exist "%TEMP%\\x\\" rd /s /q "%TEMP%\\x"',
    "Remove-Item (Join-Path a b)",
])
def test_the_hazard_detector_catches_each_shape(cmd):
    assert _bash_hazards(cmd), cmd


@pytest.mark.parametrize("cmd", [
    'cmd.exe /c if exist "%TEMP%\\x" rd /s /q "%TEMP%\\x"',
    'reg.exe delete "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run" /v x /f',
    "schtasks.exe /delete /tn cortexsim-x /f",
    "Remove-EventLog -LogName CortexSim-EDR004 -ErrorAction SilentlyContinue",
    "Get-Process -Name x -ErrorAction SilentlyContinue | Where-Object Path -Like '*\\x.exe' | Stop-Process -Force",
    'cmd.exe /c findstr /m /c:cortexsim-x "%TEMP%\\a.config" && rd /s /q "%TEMP%\\a"',
    "Write-Host '$env:TEMP is literal in single quotes'",
])
def test_the_hazard_detector_accepts_bash_inert_windows_lines(cmd):
    assert _bash_hazards(cmd) == []

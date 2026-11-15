"""The bash bundle's cleanup/teardown must ALWAYS run to completion.

``generate_bash`` registers ``trap cleanup EXIT`` and the template comment
promises teardown "fires even on error". But the whole script runs under
``set -euo pipefail``, and the cleanup commands are emitted raw into the
``cleanup()`` body — so the FIRST cleanup command that exits non-zero aborts the
function and every later teardown command is skipped. Real teardown commands
routinely exit non-zero (``pkill`` with no match, ``rm`` of an absent file,
``docker rm`` of a container that never started), so this leaves attack
artifacts on the customer's host after a POV and never logs "Cleanup complete".

These tests execute a real generated bundle with ``bash`` and assert every
cleanup command ran, even when an earlier one failed.
"""
from __future__ import annotations

import subprocess

import pytest

from engine.push_generator import generate_bash, resolve_target


def _run_bundle(scenario: dict) -> str:
    script = generate_bash(scenario)
    proc = subprocess.run(
        ["bash", "-c", script], text=True, capture_output=True, timeout=60
    )
    # The bundle tees everything into a /tmp log and also to stdout; capture both.
    return proc.stdout + proc.stderr


def _scenario(cleanup_commands: list[str]) -> dict:
    return {
        "scenario_id": "SIM-TEST-CLEANUP",
        "name": "cleanup completeness probe",
        "version": "1.0",
        "plane": "edr",
        "steps": [
            {"id": "step-01", "name": "noop", "identity": "direct", "command": "true"}
        ],
        "cleanup": {"commands": cleanup_commands},
    }


def test_cleanup_runs_every_command_even_after_one_fails():
    out = _run_bundle(_scenario([
        "echo CLEANUP-MARK-1 ; false",   # first teardown command exits non-zero
        "echo CLEANUP-MARK-2",           # must still run
        "echo CLEANUP-MARK-3",           # and so must this
    ]))
    assert "CLEANUP-MARK-1" in out
    assert "CLEANUP-MARK-2" in out, (
        "a failing earlier cleanup command aborted the teardown — later cleanup "
        "commands were skipped, leaving artifacts on the host"
    )
    assert "CLEANUP-MARK-3" in out
    assert "Cleanup complete" in out, (
        "the cleanup function never reached its completion log — teardown aborted "
        "mid-way under set -e"
    )


def test_cleanup_completes_when_a_bare_command_returns_nonzero():
    # The realistic shape: a bare teardown command (no `|| true`) that fails,
    # e.g. pkill with no matching process.
    out = _run_bundle(_scenario([
        "false",                          # like `pkill cortexsim` with no match
        "echo CLEANUP-AFTER-BARE-FAIL",
    ]))
    assert "CLEANUP-AFTER-BARE-FAIL" in out
    assert "Cleanup complete" in out


# ---------------------------------------------------------------------------
# RG-PUSH-01 — a Windows-only teardown line is not run by a bash bundle
# ---------------------------------------------------------------------------
#
# cleanup.commands is one list for every target. A scenario that also emits a
# Windows bundle carries Windows teardown lines, and generate_bash used to write
# every one of them into cleanup(): `command not found` in the teardown log a
# DC shows a customer, or — for a double-quoted `$env:` line under `set -u` —
# the end of the shell, with every later teardown line skipped.

_WINDOWS_ONLY = [
    'cmd.exe /c if exist "%TEMP%\\cortexsim-x" rd /s /q "%TEMP%\\cortexsim-x"',
    "schtasks.exe /delete /tn cortexsim-x /f",
    'Remove-Item -Recurse -Force -ErrorAction SilentlyContinue -Path "$env:TEMP\\cortexsim-x"',
]


def test_posix_resolution_withholds_windows_only_cleanup_lines():
    sc = _scenario(["echo CLEANUP-POSIX-1", *_WINDOWS_ONLY, "echo CLEANUP-POSIX-2"])
    res = resolve_target(sc, "posix")
    assert [c for c, _ in res.cleanup] == ["echo CLEANUP-POSIX-1", "echo CLEANUP-POSIX-2"]
    assert res.skipped_cleanup == tuple(_WINDOWS_ONLY)
    # Cleanup never gates emittability.
    assert res.emittable


def test_bash_bundle_does_not_run_a_windows_only_cleanup_line():
    out = _run_bundle(_scenario(["echo CLEANUP-POSIX-1", *_WINDOWS_ONLY, "echo CLEANUP-POSIX-2"]))
    assert "command not found" not in out, out[-800:]
    assert "unbound variable" not in out, out[-800:]
    assert "CLEANUP-POSIX-1" in out
    assert "CLEANUP-POSIX-2" in out, "a Windows teardown line stranded the POSIX teardown after it"
    assert "Cleanup complete" in out


def test_bash_bundle_names_each_withheld_line_in_a_comment():
    script = generate_bash(_scenario(["echo CLEANUP-POSIX-1", *_WINDOWS_ONLY]))
    for cmd in _WINDOWS_ONLY:
        assert f"    # skipped (Windows-only): {cmd}\n" in script
        assert f"\n    {cmd}\n" not in script


def test_a_withheld_cleanup_line_cannot_break_out_of_its_comment():
    """A withheld line is scenario text on a `#` line: a newline inside it must
    not start an uncommented line in the bundle (SEC-13)."""
    hostile = "Remove-Item C:\\x\ntouch CLEANUP-INJECTED-MARK"
    sc = _scenario(["echo CLEANUP-POSIX-1", hostile])
    assert resolve_target(sc, "posix").skipped_cleanup == (hostile,)
    script = generate_bash(sc)
    assert "    # skipped (Windows-only): Remove-Item C:\\x touch CLEANUP-INJECTED-MARK\n" in script
    assert "\ntouch CLEANUP-INJECTED-MARK" not in script


def test_a_cleanup_of_only_windows_lines_is_still_a_valid_bash_function():
    out = _run_bundle(_scenario(list(_WINDOWS_ONLY)))
    assert "syntax error" not in out, out[-800:]
    assert "command not found" not in out, out[-800:]
    assert "Cleanup complete" in out


def test_a_posix_line_that_mentions_windows_text_in_quotes_is_still_run():
    """Narrative text is not execution: a quoted `cmd.exe` does not make a
    POSIX teardown line Windows-only."""
    line = "echo 'CLEANUP-QUOTED cmd.exe /c del C:\\x'"
    sc = _scenario([line])
    assert [c for c, _ in resolve_target(sc, "posix").cleanup] == [line]
    assert "CLEANUP-QUOTED" in _run_bundle(sc)

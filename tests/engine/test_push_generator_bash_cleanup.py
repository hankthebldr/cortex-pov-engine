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

from engine.push_generator import generate_bash


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

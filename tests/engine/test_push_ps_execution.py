"""The PowerShell push bundle, EXECUTED — not grepped.

Why this file exists
--------------------
From ea5a52f (2026-08-02) until this guard landed, every PowerShell-dialect
Windows push step was a silent no-op. The harness built each step file as::

    $stepBody = "$ErrorActionPreference = 'Stop'" + [Environment]::NewLine + $Command

inside a DOUBLE-quoted string, so the harness's own preference ('Continue')
expanded in and every step file began ``Continue = 'Stop'``. PowerShell parses
that as the ``continue`` keyword, which outside a loop ends the ``-File`` child
with exit 0 before the TTP line runs — and ``Invoke-CsStep`` then logged
"completed successfully". The unit test that was meant to pin the behaviour
(``test_step_script_forces_terminating_errors``) only grepped for the
substring, so it passed with the bug in place.

A bundle that reports a TTP as executed when nothing ran is the worst shape this
product can take: the POV report reads the missing telemetry as "Cortex missed
it". So every assertion below RUNS a generated bundle under a real PowerShell
and checks what happened on disk and in the log.

Where it runs
-------------
* pwsh 7 on Linux/macOS (the bundle's ``$IsLinux``/``$IsMacOS`` branch). This
  is necessary but NOT sufficient: the product floor is Windows PowerShell 5.1,
  and 5.1-only behaviour (native stderr wrapped as NativeCommandError when the
  stream is redirected; the null ``ExitCode`` without a cached handle; cmd.exe
  command-line context) is only exercised on a Windows host. On ``win32`` this
  module drives ``powershell.exe`` (5.1) on purpose.
* Skips when no PowerShell is installed, EXCEPT when ``CI`` is set or
  ``CORTEXSIM_REQUIRE_POWERSHELL=1`` — then a missing interpreter FAILS. A
  skipped execution check in CI is indistinguishable from a passing one.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from engine import push_generator as pg

IS_WINDOWS = sys.platform == "win32"
_REQUIRE = os.environ.get("CORTEXSIM_REQUIRE_POWERSHELL") == "1" or bool(os.environ.get("CI"))


def _find_interpreter() -> str | None:
    # On Windows, drive the 5.1 floor (powershell.exe) — that is what a
    # customer host runs. Elsewhere pwsh is the only option.
    if IS_WINDOWS:
        return shutil.which("powershell.exe") or shutil.which("pwsh")
    return shutil.which("pwsh")


@pytest.fixture(scope="module")
def ps_exe() -> str:
    exe = _find_interpreter()
    if exe is None:
        if _REQUIRE:
            pytest.fail(
                "PowerShell is required here (CI or CORTEXSIM_REQUIRE_POWERSHELL=1) "
                "but neither pwsh nor powershell.exe is on PATH — the push-bundle "
                "execution guard cannot be skipped in this context"
            )
        pytest.skip("no PowerShell interpreter on PATH")
    return exe


@pytest.fixture(autouse=True)
def _adapter_catalog_loaded(repo_root: Path):
    from tools.adapter_catalog import catalog  # noqa: PLC0415

    catalog.load(str(repo_root / "tools" / "packs"))
    yield


def _native_false() -> str:
    if IS_WINDOWS:
        return "& cmd.exe /c 'exit 1'"
    exe = "/usr/bin/false" if os.path.exists("/usr/bin/false") else "/bin/false"
    return f"& {exe}"


def _native_exit(code: int) -> str:
    return f"& cmd.exe /c 'exit {code}'" if IS_WINDOWS else f"& /bin/sh -c 'exit {code}'"


def _native_stderr_ok() -> str:
    if IS_WINDOWS:
        return "& cmd.exe /c 'echo progress 1>&2'"
    return "& /bin/sh -c 'echo progress 1>&2; exit 0'"


def _win_step(step_id: str, command: str, **extra) -> dict:
    step = {
        "id": step_id,
        "name": f"{step_id} probe",
        "identity": "direct",
        "platforms": ["windows"],
        "command": "echo posix-primary-is-not-used",
        "platform_variants": {"windows": command},
        "expected_detections": [],
    }
    step.update(extra)
    return step


def _scenario(steps: list[dict], cleanup: list[str] | None = None, sid: str = "SIM-TEST-PSX") -> dict:
    return {
        "scenario_id": sid,
        "name": "push bundle execution probe",
        "version": "1.0",
        "plane": "EDR",
        "steps": steps,
        "cleanup": {"commands": cleanup or []},
        "external_tools": [],
    }


def _run_bundle(ps_exe: str, bundle: str, tmp_path: Path, extra_env: dict | None = None,
                timeout: int = 240) -> tuple[subprocess.CompletedProcess, str, float]:
    path = tmp_path / "bundle.ps1"
    path.write_text(bundle, encoding="utf-8")  # the bundle carries its own BOM char
    env = {k: v for k, v in os.environ.items() if not k.startswith("CORTEXSIM_")}
    env.update({"TEMP": str(tmp_path), "TMP": str(tmp_path)})
    env.update(extra_env or {})
    args = [ps_exe, "-NoProfile", "-NonInteractive"]
    if IS_WINDOWS:
        args += ["-ExecutionPolicy", "Bypass"]
    args += ["-File", str(path)]
    started = time.monotonic()
    proc = subprocess.run(
        args, env=env, capture_output=True, text=True, timeout=timeout,
        encoding="utf-8", errors="replace", cwd=str(tmp_path),
    )
    elapsed = time.monotonic() - started
    return proc, proc.stdout + "\n" + proc.stderr, elapsed


_RESULT_RE = re.compile(r"cortexsim-step-result step=(\S+) outcome=(\S+) exit=(\S+)")


def _results(out: str) -> dict[str, tuple[str, str]]:
    return {m.group(1): (m.group(2), m.group(3)) for m in _RESULT_RE.finditer(out)}


def _diag(proc: subprocess.CompletedProcess, out: str) -> str:
    return f"exit={proc.returncode}\n--- output (tail) ---\n{out[-6000:]}"


# ─── the guard ────────────────────────────────────────────────────────────


def test_steps_really_run_and_failures_are_reported(ps_exe, tmp_path):
    """The Gate-A guard: a step's TTP line must actually execute.

    Fails on the pre-fix harness at the very first assertion — the marker step
    never runs because its file starts ``Continue = 'Stop'``.

    Also pins the CANONICAL exit-code truth table shared with the beacon's
    Windows wrapper (agent/executor/script.go::powerShellExitWrapper):
      Write-Output -> 0 · last native exits 3 -> 3 · failing cmdlet -> 1 ·
      native writes stderr and exits 0 -> 0.
    """
    marker = tmp_path / "cortexsim-ps-exec-marker.txt"
    witness = tmp_path / "cortexsim-ps-exec-witness.txt"
    scn = _scenario(
        [
            _win_step(
                "step-01",
                "New-Item -ItemType File -Force -Path (Join-Path $env:TEMP 'cortexsim-ps-exec-marker.txt') | Out-Null\n"
                "Set-Content -Path (Join-Path $env:TEMP 'cortexsim-ps-exec-witness.txt') -Value 'ran'\n"
                "Write-Output 'MARKER-1'",
            ),
            _win_step("step-02", "Write-Output 'native-step-start'; " + _native_false()),
            _win_step("step-03", _native_exit(3)),
            _win_step("step-04", "Get-Item /nonexistent-cortexsim"),
            _win_step("step-05", _native_stderr_ok()),
        ],
        cleanup=[
            "Remove-Item -Force -ErrorAction SilentlyContinue -LiteralPath "
            "(Join-Path $env:TEMP 'cortexsim-ps-exec-marker.txt')",
        ],
    )
    proc, out, _ = _run_bundle(ps_exe, pg.generate_powershell(scn), tmp_path)
    diag = _diag(proc, out)

    # 1. The TTP line ran: the marker was printed and the step's side effect is on disk.
    assert "MARKER-1" in out, "the marker step's command never executed\n" + diag
    assert witness.exists(), "step-01 printed nothing to disk — its body did not run\n" + diag

    # 2. Failures are reported as failures, never as success.
    for sid in ("step-02", "step-03", "step-04"):
        assert f"Step {sid} completed successfully" not in out, (
            f"{sid} failed but was logged as a success — a manufactured false green\n" + diag
        )
    results = _results(out)
    assert results.get("step-01") == ("ran-ok", "0"), diag           # Write-Output -> 0
    assert results.get("step-02") == ("ran-nonzero", "1"), diag      # failing native -> its code
    assert results.get("step-03") == ("ran-nonzero", "3"), diag      # last native exits 3 -> 3
    assert results.get("step-04") == ("ran-nonzero", "1"), diag      # failing cmdlet -> 1
    assert results.get("step-05") == ("ran-ok", "0"), diag           # stderr at exit 0 -> 0
    assert "Step step-03 exited 3" in out, diag
    assert "may be expected" not in out, "the 'non-zero — may be expected' excuse is gone\n" + diag

    # 3. Cleanup ran (the finally block), and removed what the step created.
    assert not marker.exists(), "cleanup did not remove the marker file\n" + diag
    assert "Cleanup complete" in out, diag

    # 4. The bundle's own exit code says a step failed.
    assert proc.returncode == 1, "a step failed, so the bundle must exit non-zero (1)\n" + diag

    # 5. A per-step outcome table closes the run.
    assert "Step outcome summary" in out, diag


@pytest.mark.parametrize("ender", ["return", "continue", "break", "exit 0"])
def test_a_step_that_ends_before_the_epilogue_is_not_a_success(ps_exe, tmp_path, ender):
    """The epilogue is the only thing that turns a step's result into an exit
    code. A top-level `return` (or `continue`, the keyword behind the original
    no-op bug) skipped it, and `Get-Item /nonexistent; return` was reported
    ran-ok / exit 0. Reaching the epilogue is now a positive signal."""
    scn = _scenario([
        _win_step("step-01", "Write-Output 'before'; Get-Item /nonexistent-cortexsim-x; " + ender),
        _win_step("step-02", "Write-Output 'still-runs'"),
    ])
    proc, out, _ = _run_bundle(ps_exe, pg.generate_powershell(scn), tmp_path)
    diag = _diag(proc, out)
    results = _results(out)
    assert results.get("step-01", ("?",))[0] == "threw", diag
    assert "EPILOGUE_NOT_REACHED" in out, diag
    assert "Step step-01 completed successfully" not in out, diag
    assert results.get("step-02") == ("ran-ok", "0"), diag
    assert proc.returncode == 1, diag


def test_step_timeout_kills_the_step_and_the_bundle_continues(ps_exe, tmp_path):
    """PUSH-12: a hung step (an ssh tunnel, an interactive prompt) must not hang
    the bundle forever. Its process tree is killed at ``timeout_seconds`` and the
    next step and the cleanup still run."""
    scn = _scenario(
        [
            _win_step("step-01", "Write-Output 'sleeper-start'; Start-Sleep -Seconds 60", timeout_seconds=3),
            _win_step("step-02", "Write-Output 'AFTER-TIMEOUT'"),
        ],
        cleanup=["Write-Host 'cortexsim-timeout-cleanup-ran'"],
    )
    proc, out, elapsed = _run_bundle(ps_exe, pg.generate_powershell(scn), tmp_path)
    diag = _diag(proc, out)
    assert elapsed < 45, f"the 60 s sleeper was not killed at its 3 s timeout ({elapsed:.0f}s)\n" + diag
    assert "cortexsim-step-timeout step=step-01" in out, diag
    assert _results(out).get("step-01", ("", ""))[0] == "timed-out", diag
    assert "AFTER-TIMEOUT" in out, "the step after the timed-out one never ran\n" + diag
    assert "cortexsim-timeout-cleanup-ran" in out, "cleanup did not run after a timeout\n" + diag
    assert proc.returncode == 1, diag


def test_preflight_refuses_steps_it_cannot_run_honestly(ps_exe, tmp_path):
    """PUSH-05/06/07: a step whose tool, operator input or elevation is missing
    is NOT run and is reported ``not-executed`` with a reason — never run
    tool-less, never logged as a pass."""
    scn = _scenario(
        [
            _win_step("step-01", "cortexsim-no-such-tool-xyz --flag; Write-Output 'TOOLLESS-RAN'"),
            _win_step("step-02", "Write-Output ('input=' + $env:CORTEXSIM_TEST_INPUT_UNSET_XYZ)"),
            _win_step("step-03", "Write-Output ('INPUT-SEEN=' + $env:CORTEXSIM_TEST_INPUT_SET_XYZ)"),
            _win_step(
                "step-04",
                "$v = if ($env:CORTEXSIM_TEST_OPTIONAL_XYZ) { $env:CORTEXSIM_TEST_OPTIONAL_XYZ } else { 'dflt' }\n"
                "Write-Output ('OPTIONAL=' + $v)",
            ),
            _win_step("step-05", "Write-Output 'ELEVATED-STEP-RAN'", identity="root"),
        ],
    )
    bundle = pg.generate_powershell(scn)
    # The operator learns about the inputs BEFORE running anything.
    header = bundle.split("$ErrorActionPreference", 1)[0]
    assert "Required operator inputs" in header
    assert "CORTEXSIM_TEST_INPUT_UNSET_XYZ" in header
    assert "CORTEXSIM_TEST_OPTIONAL_XYZ" in header

    proc, out, _ = _run_bundle(
        ps_exe, bundle, tmp_path, extra_env={"CORTEXSIM_TEST_INPUT_SET_XYZ": "yes"},
    )
    diag = _diag(proc, out)
    assert (
        "cortexsim-step-not-executed step=step-01 reason=PREREQ_MISSING tool=cortexsim-no-such-tool-xyz"
        in out
    ), diag
    assert "TOOLLESS-RAN" not in out, "step-01 ran without its tool\n" + diag
    assert "cortexsim-step-not-executed step=step-02 reason=OPERATOR_INPUT_MISSING var=CORTEXSIM_TEST_INPUT_UNSET_XYZ" in out, diag
    assert "input=" not in out.replace("INPUT-SEEN=", ""), "step-02 ran without its input\n" + diag
    assert "INPUT-SEEN=yes" in out, diag
    assert "OPTIONAL=dflt" in out, "an input with a fallback must not block its step\n" + diag

    elevated = IS_WINDOWS or (hasattr(os, "geteuid") and os.geteuid() == 0)
    if not elevated:
        assert "cortexsim-step-not-executed step=step-05 reason=NOT_ELEVATED" in out, diag
        assert "ELEVATED-STEP-RAN" not in out, diag
        assert "no-credential-free-impersonation" not in out, (
            "an unelevated root step must say NOT_ELEVATED, not blame impersonation\n" + diag
        )

    results = _results(out)
    assert results.get("step-01", ("", ""))[0] == "not-executed", diag
    assert results.get("step-02", ("", ""))[0] == "not-executed", diag
    assert proc.returncode == 3, "a not-executed step must make the bundle exit 3\n" + diag


def test_unacknowledged_residue_refuses_before_any_step_runs(ps_exe, tmp_path):
    """PUSH-03: a bundle that cannot perform part of the scenario's teardown says
    so up front and runs nothing until the operator accepts the residue."""
    witness = tmp_path / "cortexsim-residue-witness.txt"
    scn = _scenario(
        [_win_step("step-01", "Set-Content -Path (Join-Path $env:TEMP 'cortexsim-residue-witness.txt') -Value 'x'")],
        cleanup=["rm -f /tmp/cortexsim-residue-probe", "Write-Host 'cortexsim-residue-cleanup-ran'"],
    )
    bundle = pg.generate_powershell(scn)
    assert "rm -f /tmp/cortexsim-residue-probe" in bundle.split("$ErrorActionPreference", 1)[0], (
        "the residue list must be in the header"
    )

    proc, out, _ = _run_bundle(ps_exe, bundle, tmp_path)
    diag = _diag(proc, out)
    assert proc.returncode == 2, diag
    assert "RESIDUE_NOT_ACKNOWLEDGED" in out, diag
    assert "rm -f /tmp/cortexsim-residue-probe" in out, diag
    assert not witness.exists(), "a step ran before the residue was acknowledged\n" + diag

    proc, out, _ = _run_bundle(ps_exe, bundle, tmp_path, extra_env={"CORTEXSIM_ACCEPT_RESIDUE": "1"})
    diag = _diag(proc, out)
    assert witness.exists(), diag
    assert "cortexsim-residue-cleanup-ran" in out, diag
    assert "RESIDUE" in out and "rm -f /tmp/cortexsim-residue-probe" in out, diag
    assert "Cleanup complete" not in out, (
        "teardown was incomplete, so the bundle must never print 'Cleanup complete'\n" + diag
    )
    assert proc.returncode == 0, diag


# ─── identity degradation: visible in the table and the closing summary ───
#
# Windows has no credential-free impersonation, so a step that declares
# `www-data` runs as whoever launched the bundle. The harness logged one WARN
# line before the step and then reported the step `ran-ok` in the outcome
# table with nothing beside it, so the table a DC reads at the end said five
# clean steps for SIM-EDR-005 when none ran as the account its detections key on.

_TABLE_HEADER_RE = re.compile(r"(?m)^\s+STEP\s+OUTCOME\s+EXIT\s+IDENTITY\s+DETAIL\s*$")
_IDENTITY_SUMMARY_RE = re.compile(r"cortexsim-identity-summary degraded=(\d+) ran=(\d+)")


def _table_rows(out: str) -> dict[str, str]:
    """The outcome-table rows, keyed by step id (text after the step id)."""
    tail = out.split("Step outcome summary:", 1)
    if len(tail) != 2:
        return {}
    rows: dict[str, str] = {}
    for line in tail[1].splitlines():
        m = re.match(r"^\s{4}(step-\d+)\s+(.*)$", line)
        if m:
            rows.setdefault(m.group(1), m.group(2).rstrip())
    return rows


def _identity_of(out: str) -> dict[str, str]:
    return {
        m.group(1): m.group(2)
        for m in re.finditer(r"cortexsim-step-result step=(\S+) outcome=\S+ exit=\S+ identity=(\S+)", out)
    }


def test_an_identity_degraded_step_is_marked_in_the_outcome_table(ps_exe, tmp_path):
    scn = _scenario([
        _win_step("step-01", "Write-Output 'DIRECT-RAN'"),
        _win_step("step-02", "Write-Output 'DEGRADED-RAN'", identity="www-data"),
        _win_step("step-03", "Write-Output 'DEGRADED-FAILS'; " + _native_exit(3), identity="postgres"),
        _win_step("step-04", "cortexsim-no-such-tool-xyz --flag", identity="www-data"),
    ])
    proc, out, _ = _run_bundle(ps_exe, pg.generate_powershell(scn), tmp_path)
    diag = _diag(proc, out)

    # The steps still RUN: degradation is reported, never a refusal.
    assert "DEGRADED-RAN" in out and "DEGRADED-FAILS" in out, diag

    # 1. Outcomes are what they were. A degraded step is not a new outcome.
    results = _results(out)
    assert results.get("step-01") == ("ran-ok", "0"), diag
    assert results.get("step-02") == ("ran-ok", "0"), diag
    assert results.get("step-03") == ("ran-nonzero", "3"), diag
    assert results.get("step-04", ("",))[0] == "not-executed", diag

    # 2. The machine line carries the identity mode of every step.
    assert _identity_of(out) == {
        "step-01": "direct", "step-02": "degraded", "step-03": "degraded", "step-04": "-",
    }, diag

    # 3. The table has an IDENTITY column and the degraded rows say so.
    assert _TABLE_HEADER_RE.search(out), "the outcome table has no IDENTITY column\n" + diag
    rows = _table_rows(out)
    assert set(rows) == {"step-01", "step-02", "step-03", "step-04"}, diag
    assert re.match(r"^ran-ok\s+0\s+direct\b", rows["step-01"]), rows["step-01"]
    assert re.match(r"^ran-ok\s+0\s+DEGRADED\s+declared=www-data actual=\S+", rows["step-02"]), rows["step-02"]
    assert re.match(r"^ran-nonzero\s+3\s+DEGRADED\s+declared=postgres actual=\S+", rows["step-03"]), rows["step-03"]
    # A step that never ran achieved no identity at all: not counted as degraded.
    assert re.match(r"^not-executed\s+-\s+-\s+reason=PREREQ_MISSING", rows["step-04"]), rows["step-04"]
    assert "DEGRADED" not in rows["step-01"] and "DEGRADED" not in rows["step-04"], diag

    # 4. The closing summary counts them and names step and declared identity.
    m = _IDENTITY_SUMMARY_RE.search(out)
    assert m, "no cortexsim-identity-summary line closes the run\n" + diag
    assert (m.group(1), m.group(2)) == ("2", "3"), diag
    summary = next(ln for ln in out.splitlines() if "cortexsim-identity-summary" in ln)
    assert "[WARN]" in summary, summary
    assert "step-02 (www-data)" in summary and "step-03 (postgres)" in summary, summary
    assert "step-04" not in summary and "step-01" not in summary, summary

    # 5. Exit code: a step was not executed -> 3, exactly as before.
    assert proc.returncode == 3, diag


def test_identity_degradation_does_not_change_the_exit_code(ps_exe, tmp_path):
    """Exit 0 still means every step ran and exited 0. Degradation is reported
    beside it, loudly, and is not folded into the code."""
    scn = _scenario([
        _win_step("step-01", "Write-Output 'A'", identity="www-data"),
        _win_step("step-02", "Write-Output 'B'"),
    ])
    proc, out, _ = _run_bundle(ps_exe, pg.generate_powershell(scn), tmp_path)
    diag = _diag(proc, out)
    assert proc.returncode == 0, diag
    m = _IDENTITY_SUMMARY_RE.search(out)
    assert m and (m.group(1), m.group(2)) == ("1", "2"), diag
    assert re.match(r"^ran-ok\s+0\s+DEGRADED\s+declared=www-data", _table_rows(out)["step-01"]), diag
    # Told before the first step too, not only at the end.
    start = out.split("STEP step-01", 1)[0]
    assert "Identity DEGRADED" in start and "step-01 (www-data)" in start, diag

    scn = _scenario([
        _win_step("step-01", "Write-Output 'A'", identity="www-data"),
        _win_step("step-02", _native_exit(3)),
    ])
    proc, out, _ = _run_bundle(ps_exe, pg.generate_powershell(scn), tmp_path)
    assert proc.returncode == 1, _diag(proc, out)


def test_a_run_with_no_degraded_step_says_zero(ps_exe, tmp_path):
    scn = _scenario([_win_step("step-01", "Write-Output 'A'"), _win_step("step-02", "Write-Output 'B'")])
    proc, out, _ = _run_bundle(ps_exe, pg.generate_powershell(scn), tmp_path)
    diag = _diag(proc, out)
    m = _IDENTITY_SUMMARY_RE.search(out)
    assert m and (m.group(1), m.group(2)) == ("0", "2"), diag
    summary = next(ln for ln in out.splitlines() if "cortexsim-identity-summary" in ln)
    assert "[INFO]" in summary, summary
    assert "DEGRADED" not in out.split("Step outcome summary:", 1)[1], diag
    assert proc.returncode == 0, diag


def test_an_elevated_identity_step_is_never_counted_as_degraded(ps_exe, tmp_path):
    """`root` is an elevation claim, not an impersonation claim: unelevated the
    step is not run (NOT_ELEVATED, exit 3); elevated it runs and reads
    `elevated`. Either way it is not a DEGRADED row."""
    scn = _scenario([_win_step("step-01", "Write-Output 'ROOT-STEP'", identity="root")])
    proc, out, _ = _run_bundle(ps_exe, pg.generate_powershell(scn), tmp_path)
    diag = _diag(proc, out)
    m = _IDENTITY_SUMMARY_RE.search(out)
    assert m and m.group(1) == "0", diag
    mode = _identity_of(out).get("step-01")
    if "ROOT-STEP" in out:
        assert mode == "elevated" and proc.returncode == 0, diag
    else:
        assert mode == "-" and proc.returncode == 3, diag
        assert "reason=NOT_ELEVATED" in _table_rows(out)["step-01"], diag


@pytest.mark.skipif(not IS_WINDOWS, reason="cmd.exe command-line context exists only on Windows")
def test_cmd_dialect_steps_run_under_cmd_and_propagate_their_exit_code(ps_exe, tmp_path):
    """Windows-only, and NOT yet observed: no Windows host has run this file.

    A single-line cmd step runs on the command line, a multi-line one from a
    .cmd file. Both must run every statement, expand a defined ``%NAME%`` and
    hand cmd's exit code back. (A FOR variable is no longer exercised here:
    the resolver refuses it, see TestCmdPercentContext.)"""
    scn = _scenario(
        [
            _win_step("step-01", "echo cs-one-%CORTEXSIM_CMD_PROBE% & echo cs-two"),
            _win_step("step-02", "echo cs-file-a-%CORTEXSIM_CMD_PROBE%\necho cs-file-b\n"),
            _win_step("step-03", "exit /b 5"),
        ],
    )
    proc, out, _ = _run_bundle(
        ps_exe, pg.generate_powershell(scn), tmp_path, extra_env={"CORTEXSIM_CMD_PROBE": "set"},
    )
    diag = _diag(proc, out)
    assert "cs-one-set" in out and "cs-two" in out, diag
    assert "cs-file-a-set" in out and "cs-file-b" in out, diag
    assert _results(out).get("step-03") == ("ran-nonzero", "5"), diag


def test_a_cmd_for_variable_step_is_refused_before_a_bundle_exists():
    """PUSH-11, on every OS: the shape that ran on push and died on pull (or
    the reverse) never reaches a bundle."""
    scn = _scenario([_win_step("step-01", "for /L %i in (1,1,2) do @echo cs-loop-%i")])
    with pytest.raises(pg.BundleTargetUnsatisfiable) as exc:
        pg.generate_powershell(scn)
    assert exc.value.unresolved == (("step-01", "CMD_PERCENT_CONTEXT"),)


# ─── PUSH-16: Group Policy execution policy outranks -ExecutionPolicy ─────

_POLICY_FN_RE = re.compile(r"(?ms)^function Get-CsPolicyConflict \{\n.*?^\}\n")

_POLICY_PROBE = r"""
function Row($s, $p) { [pscustomobject]@{ Scope = $s; ExecutionPolicy = $p } }
$cases = [ordered]@{
  'gpo-allsigned'   = @((Row 'MachinePolicy' 'AllSigned'), (Row 'LocalMachine' 'Unrestricted'))
  'gpo-restricted'  = @((Row 'MachinePolicy' 'Undefined'), (Row 'UserPolicy' 'Restricted'))
  'gpo-remotesigned'= @((Row 'MachinePolicy' 'RemoteSigned'), (Row 'UserPolicy' 'Undefined'))
  'local-allsigned' = @((Row 'MachinePolicy' 'Undefined'), (Row 'UserPolicy' 'Undefined'), (Row 'Process' 'Bypass'), (Row 'LocalMachine' 'AllSigned'))
  'all-undefined'   = @((Row 'MachinePolicy' 'Undefined'), (Row 'UserPolicy' 'Undefined'))
  'no-rows'         = @()
  'this-host'       = @(Get-ExecutionPolicy -List)
}
$out = [ordered]@{}
foreach ($k in $cases.Keys) { $out[$k] = [string](Get-CsPolicyConflict -Rows $cases[$k]) }
$out | ConvertTo-Json -Compress
"""


def test_the_policy_probe_names_only_a_group_policy_that_refuses_unsigned_scripts(ps_exe, tmp_path):
    """The bundle's own Get-CsPolicyConflict, lifted out verbatim and fed
    policy tables. Only MachinePolicy/UserPolicy outrank the command line, and
    only AllSigned/Restricted refuse an unsigned local script. The Windows
    branch that calls it with the real table has not been observed on a host
    where Group Policy sets one."""
    bundle = pg.generate_powershell(_scenario([_win_step("step-01", "Write-Output 'x'")]))
    m = _POLICY_FN_RE.search(bundle)
    assert m, "the bundle carries no Get-CsPolicyConflict function"
    script = tmp_path / "probe.ps1"
    script.write_text(m.group(0) + _POLICY_PROBE, encoding="utf-8-sig")
    args = [ps_exe, "-NoProfile", "-NonInteractive"]
    if IS_WINDOWS:
        args += ["-ExecutionPolicy", "Bypass"]
    proc = subprocess.run(args + ["-File", str(script)], capture_output=True, text=True,
                          timeout=120, encoding="utf-8", errors="replace")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    got = json.loads(proc.stdout.strip().splitlines()[-1])
    assert got["gpo-allsigned"] == "scope=MachinePolicy policy=AllSigned"
    assert got["gpo-restricted"] == "scope=UserPolicy policy=Restricted"
    assert got["gpo-remotesigned"] == ""
    assert got["local-allsigned"] == ""
    assert got["all-undefined"] == ""
    assert got["no-rows"] == ""
    if not IS_WINDOWS:
        assert got["this-host"] == ""


def test_a_clean_host_logs_no_policy_warning(ps_exe, tmp_path):
    scn = _scenario([_win_step("step-01", "Write-Output 'A'")])
    proc, out, _ = _run_bundle(ps_exe, pg.generate_powershell(scn), tmp_path)
    if not IS_WINDOWS:
        assert "cortexsim-policy-warning" not in out, _diag(proc, out)
    assert _results(out).get("step-01") == ("ran-ok", "0"), _diag(proc, out)


# ─── static guards that need the real parser ─────────────────────────────

_PS7_ONLY_AST = r"""
$t = $null; $e = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($env:CORTEXSIM_PS1, [ref]$t, [ref]$e)
$hits = New-Object System.Collections.ArrayList
foreach ($err in $e) { [void]$hits.Add('PARSE: line ' + $err.Extent.StartLineNumber + ': ' + $err.Message) }
$nodes = $ast.FindAll({ $true }, $true)
foreach ($n in $nodes) {
    $type = $n.GetType().Name
    if ($type -eq 'TernaryExpressionAst' -or $type -eq 'PipelineChainAst') {
        [void]$hits.Add($type + ' at line ' + $n.Extent.StartLineNumber)
    }
    if ($type -eq 'BinaryExpressionAst' -and [string]$n.Operator -eq 'QuestionQuestion') {
        [void]$hits.Add('?? at line ' + $n.Extent.StartLineNumber)
    }
    if ($type -eq 'AssignmentStatementAst' -and [string]$n.Operator -eq 'QuestionQuestionEquals') {
        [void]$hits.Add('??= at line ' + $n.Extent.StartLineNumber)
    }
    if (($type -eq 'MemberExpressionAst' -or $type -eq 'InvokeMemberExpressionAst') -and $n.NullConditional) {
        [void]$hits.Add('?. at line ' + $n.Extent.StartLineNumber)
    }
    if ($type -eq 'IndexExpressionAst' -and $n.NullConditional) {
        [void]$hits.Add('?[] at line ' + $n.Extent.StartLineNumber)
    }
    if ($type -eq 'CommandAst' -and $n.GetCommandName() -eq 'ForEach-Object') {
        foreach ($el in $n.CommandElements) {
            if ($el -is [System.Management.Automation.Language.CommandParameterAst] -and $el.ParameterName -like 'Parallel*') {
                [void]$hits.Add('ForEach-Object -Parallel at line ' + $n.Extent.StartLineNumber)
            }
        }
    }
}
$hits | ConvertTo-Json -Compress
"""


def _ps_eval(ps_exe: str, script: str, ps1_path: Path) -> str:
    proc = subprocess.run(
        [ps_exe, "-NoProfile", "-NonInteractive", "-Command", script],
        env={**os.environ, "CORTEXSIM_PS1": str(ps1_path)},
        capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc.stdout.strip()


def test_generated_harness_has_no_ps7_only_syntax(ps_exe, tmp_path, repo_root):
    """The 5.1 floor, proven by AST walk rather than by substring.

    Step and cleanup bodies travel base64-encoded, so every node in the bundle's
    AST is generator-owned harness code — anything PS7-only here is a harness
    bug that would break every bundle on Server 2016/2019/2022 and Win10/11.
    pwsh 7's parser recognises the PS7-only node types, so this check runs here.
    """
    import yaml  # noqa: PLC0415

    bundles = [pg.generate_powershell(_scenario([_win_step("step-01", "Write-Output 'x'")]))]
    for p in sorted((repo_root / "scenarios").rglob("*.yml")):
        if p.name.startswith("_"):
            continue
        doc = yaml.safe_load(p.read_text())
        if not isinstance(doc, dict) or "steps" not in doc or "scenario_id" not in doc:
            continue
        if pg.resolve_target(doc, "windows").emittable:
            bundles.append(pg.generate_powershell(doc))
    assert len(bundles) > 5
    for i, bundle in enumerate(bundles):
        path = tmp_path / f"b{i}.ps1"
        path.write_text(bundle, encoding="utf-8")
        hits = json.loads(_ps_eval(ps_exe, _PS7_ONLY_AST, path) or "[]")
        if isinstance(hits, str):
            hits = [hits]
        assert not hits, f"bundle #{i}: PS7-only / unparseable harness code: {hits}"

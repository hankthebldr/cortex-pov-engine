"""The push generators must not let a step's id/identity inject shell code.

``generate_bash`` escapes the step COMMAND before embedding it in the
``run_as '<identity>' '<cmd>' '<step_id>'`` call, but left ``identity`` and
``step_id`` unescaped. A single quote in either breaks out of its single-quoted
argument and injects a top-level command into the bundle a DC runs on a
customer host — and ``bash -n`` stays clean, so the injection is invisible to
the emittability/syntax checks. ``generate_powershell`` has the same shape in
its ``Invoke-CsStep -StepId '…' -Identity '…'`` call.

Step ids/identities come from scenario YAML, which the loader is the trust
boundary for; the generator must still not emit an injectable artifact.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

import pytest

from engine import push_generator as pg
from engine.push_generator import generate_bash, generate_powershell


def test_bash_step_id_cannot_inject_a_command():
    marker = os.path.join(tempfile.gettempdir(), "cortexsim-inject-marker-bash")
    if os.path.exists(marker):
        os.remove(marker)
    # A step id crafted to break out of the single-quoted run_as argument.
    evil_id = f"step-01'; touch {marker} #"
    scenario = {
        "scenario_id": "SIM-INJ-BASH",
        "name": "injection probe",
        "steps": [{"id": evil_id, "name": "n", "identity": "direct", "command": "true"}],
    }
    script = generate_bash(scenario)
    try:
        subprocess.run(["bash", "-c", script], text=True, capture_output=True, timeout=60)
        assert not os.path.exists(marker), (
            "an unescaped step id broke out of its run_as argument and executed "
            "injected shell in the generated bundle"
        )
    finally:
        if os.path.exists(marker):
            os.remove(marker)


def test_bash_identity_is_escaped_in_the_run_as_call():
    # Asserted at the string level, not by execution: a quote in the identity is
    # an injection vector just like the step id, but a payload that also breaks
    # run_as's argument count is neutralised by set -u/-e for the wrong reason.
    # The contract is simply that the quote never survives un-escaped.
    evil_identity = "direct'; touch /tmp/x #"
    scenario = {
        "scenario_id": "SIM-INJ-IDENT",
        "name": "injection probe",
        "steps": [{"id": "step-01", "name": "n", "identity": evil_identity, "command": "true"}],
    }
    script = generate_bash(scenario)
    run_as_line = next(l for l in script.splitlines() if l.startswith("run_as "))
    assert f"run_as '{evil_identity}'" not in run_as_line, (
        "the identity was embedded in the run_as call without escaping its quote"
    )
    # The bash single-quote escape is '\'' — the identity's quote must be so escaped.
    assert "direct'\\''; touch /tmp/x #" in run_as_line


def test_powershell_step_id_is_escaped_in_the_invoke_call():
    # A Windows-emittable scenario with a quote in the step id. The generated
    # PowerShell must not let that quote close the -StepId '…' literal: the quote
    # has to be doubled (PowerShell's single-quote escape).
    evil_id = "step-01'; Remove-Item x #"
    scenario = {
        "scenario_id": "SIM-INJ-PS",
        "name": "injection probe",
        "steps": [{
            "id": evil_id, "name": "n", "identity": "direct",
            "platforms": ["windows"],
            "platform_variants": {"windows": "Get-Process"},
        }],
    }
    script = generate_powershell(scenario)
    # The raw, un-doubled quote sequence must never appear in the invoke call.
    assert f"-StepId '{evil_id}'" not in script, (
        "the step id was interpolated into the PowerShell -StepId literal without "
        "escaping its single quote"
    )
    # The escaped form (quote doubled) must be present instead.
    assert "step-01''; Remove-Item x #" in script


# ─── SEC-13: comment-interpolated fields stay on their comment line ─────────
#
# Every value interpolated into a `#` line — scenario name, step name,
# detection description, UC/TC names, MITRE names — must not be able to END
# that line. A CR/LF in a composer draft field would otherwise start an
# uncommented line: a top-level command in the artifact a DC runs.

_HOSTILE_BREAKS = ["\n", "\r\n", "\r", "\u2028", "\x0b", "\x85"]


def _hostile(break_seq: str, cmd: str) -> dict:
    """A draft whose every comment-interpolated field tries to start a new line."""
    inj = f"{break_seq}{cmd}"
    step = {
        "id": "step-01", "name": "step" + inj, "identity": "direct",
        "mitre_technique": "T1059" + inj,
        "command": "echo 'ok'",
        "expected_detections": [{"plane": "EDR" + inj, "type": "BIOC" + inj, "description": "desc" + inj}],
    }
    return {
        "scenario_id": "SIM-SEC13-001", "name": "name" + inj, "version": "1.0" + inj,
        "plane": "EDR" + inj, "uc_ref": "UCS-X" + inj, "uc_name": "uc" + inj,
        "tc_ref": "TC-X" + inj, "tc_name": "tc" + inj,
        "mitre_tactic": "TA0002" + inj, "mitre_tactic_name": "Execution" + inj,
        "mitre_technique": "T1059" + inj, "mitre_technique_name": "Cmd" + inj,
        "threat_report": "report" + inj, "author": "me" + inj,
        "steps": [step], "cleanup": {"commands": []},
    }


def _benign() -> dict:
    return _hostile("", "")


def _column0_commands(script: str) -> int:
    return sum(1 for ln in script.split("\n") if ln and not ln[0].isspace() and not ln.startswith("#"))


@pytest.mark.parametrize("break_seq", _HOSTILE_BREAKS)
def test_bash_comment_fields_cannot_start_a_command_line(break_seq):
    """SEC-13 (bash): a CR/LF in a name, step name or detection description
    must not start an uncommented line in the bundle."""
    marker = os.path.join(tempfile.gettempdir(), "cortexsim-sec13-bash-marker")
    benign = generate_bash(_benign())
    hostile = generate_bash(_hostile(break_seq, f"touch {marker}"))
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as f:
        f.write(hostile)
        tmp = f.name
    try:
        r = subprocess.run(["bash", "-n", tmp], capture_output=True, text=True, timeout=30)
        assert r.returncode == 0, r.stderr
    finally:
        os.unlink(tmp)
    assert _column0_commands(hostile) == _column0_commands(benign), (
        "a comment-interpolated field started a new top-level command line"
    )
    assert f"\ntouch {marker}" not in hostile and f"\rtouch {marker}" not in hostile


def test_comment_safe_strips_every_line_break():
    for ch in ("\n", "\r", "\x0b", "\x0c", "\x1c", "\x85", "\u2028", "\u2029", "\x00"):
        assert pg._comment_safe(f"a{ch}b") == "a b"
    assert pg._comment_safe("a\tb") == "a\tb"  # TAB cannot end a comment line


# ─── PowerShell parser-backed guards (PUSH-10, SEC-13) ──────────────────────
#
# These claims are about how PowerShell PARSES the bundle, so they are checked
# with PowerShell's own parser (System.Management.Automation.Language.Parser),
# never by substring. Skipped when no PowerShell is installed, except under CI
# or CORTEXSIM_REQUIRE_POWERSHELL=1, where a missing parser FAILS.

_AST_PROBE = r"""
$t = $null; $e = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($env:CORTEXSIM_PS1, [ref]$t, [ref]$e)
$cmds = @($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.CommandAst] }, $true))
$inj = @()
$idents = @()
foreach ($c in $cmds) {
    $name = $c.GetCommandName()
    if ($name -eq 'Write-Output' -and $c.Extent.Text -match 'INJ') { $inj += $c.Extent.Text }
    if ($name -eq 'Invoke-CsStep') {
        $els = $c.CommandElements
        for ($i = 0; $i -lt $els.Count - 1; $i++) {
            if ($els[$i] -is [System.Management.Automation.Language.CommandParameterAst] -and $els[$i].ParameterName -eq 'Identity') {
                $idents += [string]$els[$i + 1].Value
            }
        }
    }
}
$errs = @($e | ForEach-Object { 'line ' + $_.Extent.StartLineNumber + ': ' + $_.Message })
[pscustomobject]@{ errors = $errs; end = $ast.EndBlock.Statements.Count; inj = $inj; identities = $idents } | ConvertTo-Json -Compress -Depth 4
"""


@pytest.fixture(scope="module")
def ps_exe() -> str:
    exe = (shutil.which("powershell.exe") if sys.platform == "win32" else None) or shutil.which("pwsh")
    if exe is None:
        if os.environ.get("CORTEXSIM_REQUIRE_POWERSHELL") == "1" or os.environ.get("CI"):
            pytest.fail("PowerShell is required here (CI or CORTEXSIM_REQUIRE_POWERSHELL=1)")
        pytest.skip("no PowerShell interpreter on PATH")
    return exe


def _ast(ps_exe: str, bundle: str, tmp_path) -> dict:
    path = tmp_path / "probe.ps1"
    path.write_text(bundle, encoding="utf-8")
    proc = subprocess.run(
        [ps_exe, "-NoProfile", "-NonInteractive", "-Command", _AST_PROBE],
        env={**os.environ, "CORTEXSIM_PS1": str(path)},
        capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(proc.stdout)
    for key in ("errors", "inj", "identities"):
        if data.get(key) is None:
            data[key] = []
        elif isinstance(data[key], str):
            data[key] = [data[key]]
    return data


def _win_scn(steps: list[dict], cleanup: list[str] | None = None, **top) -> dict:
    scn = {
        "scenario_id": "SIM-INJ-PS", "name": "injection probe", "version": "1.0", "plane": "EDR",
        "steps": steps, "cleanup": {"commands": cleanup or []},
    }
    scn.update(top)
    return scn


def _ps_step(step_id: str, command: str, **extra) -> dict:
    step = {"id": step_id, "name": "n", "identity": "direct", "platforms": ["windows"],
            "command": "echo posix", "platform_variants": {"windows": command},
            "expected_detections": []}
    step.update(extra)
    return step


@pytest.mark.parametrize("terminator", ["'@", "\u2019@", "\u2018@", "\u201b@"])
def test_ps_forged_here_string_terminator_cannot_inject(ps_exe, tmp_path, terminator):
    """PUSH-10 case 1: a step line `’@` followed by `Write-Output INJ` must not
    become a top-level command. PowerShell treats U+2018..U+201B as single
    quotes, so `’@` ends a literal here-string exactly as `'@` does."""
    body = f"Write-Output 'a'\n{terminator}\nWrite-Output INJ-PUSH10"
    data = _ast(ps_exe, pg.generate_powershell(_win_scn([_ps_step("step-01", body)])), tmp_path)
    assert not data["errors"], data["errors"]
    assert not data["inj"], f"author text became code: {data['inj']}"
    # And the hardened _ps_literal on its own:
    lit_bundle = "$v = " + pg._ps_literal(body) + "\n"
    data = _ast(ps_exe, lit_bundle, tmp_path)
    assert not data["errors"], data["errors"]
    assert not data["inj"], f"_ps_literal let the payload out: {data['inj']}"
    assert data["end"] == 1


def test_ps_posix_cleanup_with_a_typographic_apostrophe_still_parses(ps_exe, tmp_path):
    """PUSH-10 case 2: a POSIX-only cleanup is named (residue) in the bundle as
    a single-quoted literal; `don’t` used to close that literal and break the
    whole bundle at parse time."""
    scn = _win_scn(
        [_ps_step("step-01", "Write-Output 'x'")],
        cleanup=["rm -f /tmp/x  # don\u2019t leave this behind; Write-Output INJ-CLEANUP"],
    )
    data = _ast(ps_exe, pg.generate_powershell(scn), tmp_path)
    assert not data["errors"], data["errors"]
    assert not data["inj"], data["inj"]


@pytest.mark.parametrize("quote", ["'", "\u2019", "\u2018", "\u201a", "\u201b"])
def test_ps_identity_with_a_quote_stays_inside_its_literal(ps_exe, tmp_path, quote):
    """PUSH-10 case 3: the identity reaches Invoke-CsStep byte-for-byte, as ONE
    string argument — never closing its literal and injecting a command."""
    identity = f"o{quote}brien{quote}; Write-Output INJ-IDENT; {quote}"
    scn = _win_scn([_ps_step("step-01", "Write-Output 'x'", identity=identity)])
    data = _ast(ps_exe, pg.generate_powershell(scn), tmp_path)
    assert not data["errors"], data["errors"]
    assert not data["inj"], data["inj"]
    assert data["identities"] == [identity]


@pytest.mark.parametrize("break_seq", _HOSTILE_BREAKS)
def test_ps_comment_fields_cannot_start_a_statement(ps_exe, tmp_path, break_seq):
    """SEC-13 (PowerShell): same draft, PowerShell's parser — which ALSO treats a
    lone CR as a line break. The EndBlock statement count must match a benign
    twin's exactly, and the injected command must not exist as a command."""
    benign_scn, hostile_scn = _benign(), _hostile(break_seq, "Write-Output INJ-SEC13")
    for scn in (benign_scn, hostile_scn):
        scn["steps"][0]["platforms"] = ["windows"]
        scn["steps"][0]["platform_variants"] = {"windows": "Write-Output 'ok'"}
    benign = _ast(ps_exe, pg.generate_powershell(benign_scn), tmp_path)
    hostile = _ast(ps_exe, pg.generate_powershell(hostile_scn), tmp_path)
    assert not hostile["errors"], hostile["errors"]
    assert not hostile["inj"], hostile["inj"]
    assert hostile["end"] == benign["end"]

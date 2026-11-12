"""The windows job's ``parse`` phase must be able to go red (RG-CI-03).

``Invoke-PhaseParse`` in ``scripts/ci/windows-native-e2e.ps1`` runs every
rendered PowerShell file through the real Windows PowerShell 5.1 parser and
asserts ZERO errors. On its own that is a gate nobody has seen fail: a parser
that rejected nothing (the wrong decode, an empty read, a runner image whose
``powershell.exe`` is not 5.1 Desktop) would report the same green, and the two
deliberately bad fixtures were excluded from the render so nothing on Windows
ever looked at them.

The phase now also parses three fixtures whose verdict is known, two of which
MUST be rejected:

    good_51.ps1            clean as a file and as UTF-8 text
    bad_ps7_only.ps1       PowerShell-7-only syntax; 5.1 cannot parse it
    bad_cp1252_quote.ps1   BOM-less UTF-8 em dash in a double-quoted string:
                           the file read (ANSI code page) must fail, the UTF-8
                           text read (the ``iwr | iex`` path) must parse

WHAT THIS FILE CAN AND CANNOT SHOW. No Windows host is reachable from here, and
pwsh 7 is not Windows PowerShell 5.1. The green half of the control -- 5.1
rejects the two bad fixtures -- can only be observed in the ``windows`` job.
What pwsh 7 CAN show is the other half: it accepts PS7-only syntax and reads a
BOM-less file as UTF-8, so when the control's own code runs under pwsh it must
report exactly three failures. That is the control going red because the parser
underneath is not the product floor, which is the property it exists to have.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DRIVER = REPO / "scripts" / "ci" / "windows-native-e2e.ps1"
FIXTURES = REPO / "tests" / "ci" / "fixtures"
RENDER = REPO / "scripts" / "ci" / "render_powershell.py"

#: fixture -> (ParseFile must reject it, ParseInput must reject it) on real 5.1.
CONTRACT = {
    "good_51.ps1": (False, False),
    "bad_ps7_only.ps1": (True, True),
    "bad_cp1252_quote.ps1": (True, False),
}


def _function(name: str) -> str:
    text = DRIVER.read_text(encoding="ascii")
    m = re.search(rf"^function {re.escape(name)} \{{\n(.*?)^\}}\n", text, re.MULTILINE | re.DOTALL)
    assert m, f"{DRIVER.name} has no top-level `function {name}`"
    return m.group(1)


# ---------------------------------------------------------------------------
# Static: the phase runs the controls, on these fixtures, with this contract
# ---------------------------------------------------------------------------


def test_parse_phase_runs_the_controls_and_records_them():
    body = _function("Invoke-PhaseParse")
    assert "Invoke-CsParseControls" in body, "the parse phase never runs its negative controls"
    assert "parse-controls.json" in body, "the control verdicts are not written to the windows-proof artifact"


def test_controls_cover_exactly_the_three_fixtures_with_the_documented_contract():
    body = _function("Invoke-CsParseControls")
    rows = re.findall(
        r"@\{\s*file\s*=\s*'([^']+)';\s*file_rejected\s*=\s*\$(true|false);\s*input_rejected\s*=\s*\$(true|false)\s*\}",
        body)
    got = {name: (f == "true", i == "true") for name, f, i in rows}
    assert got == CONTRACT
    for name in CONTRACT:
        assert (FIXTURES / name).is_file(), f"control fixture missing from the tree: {name}"
    # A control that cannot find its fixture must stop the phase, not pass it.
    assert re.search(r"Test-Path -LiteralPath \$path\)\)\s*\{\s*throw", body), (
        "a missing fixture must throw: a control over zero files proves nothing")


def test_at_least_one_control_must_be_rejected():
    """The point of the block. If every row expected a clean parse it would be
    one more assertion that cannot fail."""
    assert any(file_rejected for file_rejected, _ in CONTRACT.values())
    body = _function("Invoke-CsParseControls")
    assert "$true" in body


def test_the_bad_fixtures_reach_windows_only_through_the_controls():
    """They are excluded from the render by exact path, so the main loop (which
    asserts zero errors) never sees them and the controls are their only use."""
    text = RENDER.read_text(encoding="utf-8")
    m = re.search(r"^REPO_PS1_EXCLUDE = \{\n(.*?)^\}", text, re.MULTILINE | re.DOTALL)
    assert m, "render_powershell.py no longer declares REPO_PS1_EXCLUDE as a literal dict"
    excluded = set(re.findall(r'^\s*"([^"]+)":', m.group(1), re.MULTILINE))
    assert excluded, "parsed zero exclusions -- the parser is stale"
    bad = {f"tests/ci/fixtures/{n}" for n, (rejected, _) in CONTRACT.items() if rejected}
    assert bad <= excluded, f"a must-fail fixture is rendered into the zero-error loop: {sorted(bad - excluded)}"
    assert "tests/ci/fixtures/good_51.ps1" not in excluded


def test_the_cp1252_fixture_is_what_the_control_assumes():
    raw = (FIXTURES / "bad_cp1252_quote.ps1").read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "must be BOM-less, or 5.1 reads it as UTF-8 and it parses"
    assert "—".encode("utf-8") in raw
    # 0x94 is the byte Windows-1252 decodes to U+201D, a closing double quote.
    assert raw.decode("cp1252").count("”") == 1
    # The PS7-only fixture is ASCII, so its verdict does not depend on a code page.
    assert all(b < 0x80 for b in (FIXTURES / "bad_ps7_only.ps1").read_bytes())


# ---------------------------------------------------------------------------
# Dynamic: run the control's own code under pwsh 7 and watch it go red
# ---------------------------------------------------------------------------

_HARNESS = r"""
$ErrorActionPreference = 'Stop'
$driver = $args[0]
$fixtures = $args[1]
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($driver, [ref]$tokens, [ref]$errors)
if (@($errors).Count -gt 0) { throw ('driver does not parse: ' + $errors[0].Message) }
$Phase = 'parse'
$script:Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$script:Workspace = 'unused-the-harness-passes-FixtureDir'
$script:Asserts = New-Object System.Collections.ArrayList
function Assert-Cs {
    param([bool]$Condition, [string]$What, [string]$Detail = '')
    [void]$script:Asserts.Add([ordered]@{ ok = $Condition; what = $What; detail = $Detail })
}
foreach ($name in @('Get-CsParseErrors', 'Format-CsParseError', 'Invoke-CsParseControls')) {
    $fn = $ast.Find({ param($a) ($a -is [System.Management.Automation.Language.FunctionDefinitionAst]) -and ($a.Name -eq $name) }.GetNewClosure(), $true)
    if ($null -eq $fn) { throw ('driver has no function ' + $name) }
    . ([scriptblock]::Create($fn.Extent.Text))
}
$rows = @(Invoke-CsParseControls -FixtureDir $fixtures)
$doc = [ordered]@{
    edition = [string]$PSVersionTable['PSEdition']
    rows = $rows
    asserts = $script:Asserts.ToArray()
}
ConvertTo-Json -InputObject $doc -Depth 6 -Compress
"""


@pytest.fixture(scope="module")
def pwsh() -> str:
    exe = os.environ.get("CORTEXSIM_PWSH") or shutil.which("pwsh")
    if exe is None:
        if os.environ.get("CORTEXSIM_REQUIRE_POWERSHELL") == "1":
            pytest.fail("CORTEXSIM_REQUIRE_POWERSHELL=1 but no pwsh on PATH")
        pytest.skip("pwsh not installed (set CORTEXSIM_REQUIRE_POWERSHELL=1 to make this fatal)")
    return exe


def _run_harness(pwsh_exe: str, tmp_path: Path, fixture_dir: Path) -> subprocess.CompletedProcess:
    harness = tmp_path / "harness.ps1"
    harness.write_text(_HARNESS, encoding="ascii")
    return subprocess.run(
        [pwsh_exe, "-NoProfile", "-NonInteractive", "-File", str(harness), str(DRIVER), str(fixture_dir)],
        capture_output=True, text=True, timeout=120)


def test_under_pwsh7_the_controls_go_red_because_it_is_not_the_51_parser(pwsh, tmp_path):
    proc = _run_harness(pwsh, tmp_path, FIXTURES)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    doc = json.loads(proc.stdout.strip().splitlines()[-1])
    assert doc["edition"] == "Core", "this test is about pwsh; Windows PowerShell 5.1 has its own job"

    rows = {r["file"]: r for r in doc["rows"]}
    assert set(rows) == set(CONTRACT)
    # What pwsh 7 really does with each fixture: it rejects none of them.
    for name, row in rows.items():
        assert row["parsefile_errors"] == 0 and row["parseinput_errors"] == 0, row
        assert (row["expect_file_rejected"], row["expect_input_rejected"]) == CONTRACT[name]

    # So every expectation of a REJECTION is a failed assertion, and nothing else is.
    asserts = doc["asserts"]
    assert len(asserts) == 2 * len(CONTRACT), asserts
    failed = sorted(a["what"] for a in asserts if not a["ok"])
    assert len(failed) == 3, failed
    assert sum("bad_ps7_only.ps1" in w for w in failed) == 2, failed
    assert sum("bad_cp1252_quote.ps1" in w and "ParseFile" in w for w in failed) == 1, failed
    assert all("good_51.ps1" not in w for w in failed), failed
    # A red control explains itself: what was expected, what the parser said,
    # and the code page the file read used.
    for a in asserts:
        if not a["ok"]:
            assert "expected it to be REJECTED" in a["detail"] and "ANSI cp" in a["detail"], a


def test_a_missing_control_fixture_stops_the_phase(pwsh, tmp_path):
    empty = tmp_path / "no-fixtures"
    empty.mkdir()
    proc = _run_harness(pwsh, tmp_path, empty)
    assert proc.returncode != 0, "controls over zero files must not pass"
    assert "parse control fixture is missing" in proc.stdout + proc.stderr

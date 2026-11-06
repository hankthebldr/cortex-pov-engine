"""Guard tests for the render-all-PowerShell gate (``make check-powershell``).

The gate is two halves:

* ``scripts/ci/render_powershell.py`` renders every PowerShell artifact this
  repo emits (installer variants, the Windows uninstaller, every windows push
  bundle, every PowerShell step the beacon would run, the beacon's own wrapper
  shapes, and every tracked ``*.ps1``) into one directory plus a manifest;
* ``scripts/ci/ps51-gate.ps1`` decodes each file the way Windows PowerShell 5.1
  reads it, parses it, and walks the AST for PowerShell-7-only constructs.

NECESSARY, NOT SUFFICIENT. The parser that runs here is pwsh 7, not Windows
PowerShell 5.1 (the product floor). A green gate proves the files parse under
pwsh, decode correctly under the 5.1 rules, and avoid the known PS7-only
constructs. It does not prove a single one of them executes on Windows.

These tests pin the gate's own behaviour: a guard that cannot be shown to go
red on a known-bad input proves nothing. They skip when pwsh is absent, unless
``CORTEXSIM_REQUIRE_POWERSHELL=1`` (CI sets it), in which case a missing pwsh
FAILS -- a skipped syntax gate reads exactly like a passing one.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
GATE = REPO / "scripts" / "ci" / "ps51-gate.ps1"
RENDER = REPO / "scripts" / "ci" / "render_powershell.py"

# file:line:RULE: message  -- the gate's one-line-per-finding contract.
FINDING_RE = re.compile(r"^(?P<file>[^\s:][^:]*):(?P<line>\d+):(?P<rule>[A-Z0-9_]+):\s*(?P<msg>.*)$")


def _pwsh() -> str | None:
    override = os.environ.get("CORTEXSIM_PWSH")
    if override:
        return override
    return shutil.which("pwsh")


@pytest.fixture(scope="module")
def pwsh() -> str:
    exe = _pwsh()
    if exe is None:
        if os.environ.get("CORTEXSIM_REQUIRE_POWERSHELL") == "1":
            pytest.fail("CORTEXSIM_REQUIRE_POWERSHELL=1 but no pwsh on PATH -- "
                        "the PowerShell gate cannot run, and a skipped gate reads as a pass")
        pytest.skip("pwsh not installed (set CORTEXSIM_REQUIRE_POWERSHELL=1 to make this fatal)")
    return exe


def _run_gate(pwsh_exe: str, directory: Path) -> subprocess.CompletedProcess:
    assert GATE.is_file(), f"gate script missing: {GATE}"
    return subprocess.run(
        [pwsh_exe, "-NoProfile", "-NonInteractive", "-File", str(GATE), "-Dir", str(directory)],
        capture_output=True, text=True, timeout=600,
    )


def _findings(proc: subprocess.CompletedProcess) -> set[tuple[str, int, str]]:
    out: set[tuple[str, int, str]] = set()
    for line in proc.stdout.splitlines():
        m = FINDING_RE.match(line.strip())
        if m:
            out.add((m["file"], int(m["line"]), m["rule"]))
    return out


def _expectations(fixture: Path) -> set[tuple[str, int, str]]:
    """Read the ``# expect: RULE`` annotations a fixture carries on each line."""
    want: set[tuple[str, int, str]] = set()
    text = fixture.read_bytes().decode("utf-8")
    for n, line in enumerate(text.splitlines(), start=1):
        m = re.search(r"#\s*expect:\s*([A-Z0-9_]+)\s*$", line)
        if m:
            want.add((fixture.name, n, m.group(1)))
    return want


def _stage(tmp_path: Path, *fixtures: Path) -> Path:
    d = tmp_path / "gate-in"
    d.mkdir()
    for f in fixtures:
        shutil.copyfile(f, d / f.name)
    return d


# ---------------------------------------------------------------------------
# Known-bad inputs go red, with the expected rule, at the expected line
# ---------------------------------------------------------------------------


def test_ps7_only_fixture_fails_with_exactly_the_annotated_rules(pwsh, tmp_path):
    fixture = FIXTURES / "bad_ps7_only.ps1"
    want = _expectations(fixture)
    # The three the task names must be among the annotations, or the fixture
    # drifted away from what it exists to prove.
    rules = {r for _, _, r in want}
    assert {"PS7_NULL_COALESCE", "PS7_PIPELINE_CHAIN", "PS7_PARAMETER"} <= rules

    proc = _run_gate(pwsh, _stage(tmp_path, fixture))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    got = _findings(proc)
    # Exact set: a missing finding is a hole in the gate, an extra one is a
    # false positive that would teach people to ignore it.
    assert got == want, (
        f"missing={sorted(want - got)}\nunexpected={sorted(got - want)}\n{proc.stdout}"
    )
    as_hashtable = [ln for ln in proc.stdout.splitlines() if "-AsHashtable" in ln]
    assert as_hashtable and "PS7_PARAMETER" in as_hashtable[0], proc.stdout


def test_bomless_em_dash_in_double_quotes_fails_the_cp1252_parse_only(pwsh, tmp_path):
    fixture = FIXTURES / "bad_cp1252_quote.ps1"
    raw = fixture.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "fixture must be BOM-less to exercise the cp1252 read"
    assert "—".encode("utf-8") in raw, "fixture must carry a UTF-8 em dash"

    proc = _run_gate(pwsh, _stage(tmp_path, fixture))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    rules = {r for _, _, r in _findings(proc)}
    assert "PARSE_CP1252" in rules, proc.stdout
    # The UTF-8 read of the same bytes parses: the failure is the ENCODING,
    # which is the whole point of decoding the way 5.1 does.
    assert "PARSE_UTF8" not in rules, proc.stdout


def test_the_same_em_dash_with_a_utf8_bom_passes(pwsh, tmp_path):
    """5.1 honours a BOM, so the identical text is fine once it carries one."""
    d = tmp_path / "bom"
    d.mkdir()
    body = (FIXTURES / "bad_cp1252_quote.ps1").read_bytes()
    (d / "with_bom.ps1").write_bytes(b"\xef\xbb\xbf" + body)
    proc = _run_gate(pwsh, d)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_good_51_fixture_passes(pwsh, tmp_path):
    proc = _run_gate(pwsh, _stage(tmp_path, FIXTURES / "good_51.ps1"))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _findings(proc) == set(), proc.stdout
    # The gate must say what it is NOT, every time it runs.
    assert "NOT Windows PowerShell 5.1" in proc.stdout or "Desktop" in proc.stdout, proc.stdout


@pytest.mark.parametrize("snippet", [
    "$onLinux = $IsLinux",
    "if ($IsLinux) { 'x' }",
    "if ($IsMacOS -or $IsLinux) { 'x' }",
    "if ($IsLinux -or $IsMacOS -or $IsWindows) { 'x' }",
    "if ($IsLinux -and $IsMacOS) { 'x' }",
    "if (-not ($IsLinux -or $IsMacOS)) { 'x' }",
    "if (($IsLinux -or $IsMacOS)) { 'x' }",
    "while ($IsLinux -or $IsMacOS) { break }",
    "$a = ($IsLinux -or $IsMacOS)",
    "if ($global:IsLinux -or $IsMacOS) { 'x' }",
    "Write-Output \"running on $IsMacOS\"",
], ids=lambda s: re.sub(r"\W+", "_", s)[:40])
def test_platform_variables_outside_the_one_allowlisted_shape_fail(pwsh, tmp_path, snippet):
    """Only `if ($IsLinux -or $IsMacOS)` is allowed. Allowing the variables
    wholesale would wave through `if ($IsWindows)`, which is `$null` (false) on
    5.1 -- i.e. the Windows branch is skipped ON Windows."""
    d = tmp_path / "plat"
    d.mkdir()
    (d / "snippet.ps1").write_text(snippet + "\n", encoding="ascii")
    proc = _run_gate(pwsh, d)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "PS7_AUTOMATIC_VARIABLE" in {r for _, _, r in _findings(proc)}, proc.stdout


def test_the_allowlisted_shape_alone_passes(pwsh, tmp_path):
    d = tmp_path / "ok"
    d.mkdir()
    (d / "harness.ps1").write_text(
        "if ($IsLinux -or $IsMacOS) {\n    'dry run'\n} else {\n    'windows'\n}\n", encoding="ascii")
    proc = _run_gate(pwsh, d)
    assert proc.returncode == 0, proc.stdout + proc.stderr


# ---------------------------------------------------------------------------
# Manifest contract: a zero is degraded, a non-PowerShell artifact is a failure
# ---------------------------------------------------------------------------


def _manifest(d: Path, artifacts: list[dict], *, required: list[str], errors: list[str] | None = None):
    counts: dict[str, int] = {k: 0 for k in required}
    for a in artifacts:
        counts[a["kind"]] = counts.get(a["kind"], 0) + 1
    (d / "manifest.json").write_text(json.dumps({
        "schema": "cortexsim.ps-gate.manifest/1",
        "required_kinds": required,
        "counts": counts,
        "artifacts": artifacts,
        "errors": errors or [],
        "warnings": [],
    }), encoding="utf-8")


def test_a_bash_uninstaller_fails_as_not_powershell(pwsh, tmp_path):
    d = tmp_path / "m1"
    (d / "uninstaller").mkdir(parents=True)
    (d / "uninstaller" / "windows-uninstall.ps1").write_text("#!/usr/bin/env bash\necho hi\n")
    _manifest(d, [{
        "path": "uninstaller/windows-uninstall.ps1", "kind": "uninstaller",
        "source": "GET /api/agents/install?os=windows&uninstall=1",
        "not_powershell": "windows uninstaller is not PowerShell (body starts with '#!')",
    }], required=["uninstaller"])
    proc = _run_gate(pwsh, d)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "windows uninstaller is not PowerShell" in proc.stdout


def test_an_empty_required_kind_is_degraded_not_ok(pwsh, tmp_path):
    d = tmp_path / "m2"
    d.mkdir()
    shutil.copyfile(FIXTURES / "good_51.ps1", d / "good_51.ps1")
    _manifest(d, [{"path": "good_51.ps1", "kind": "installer", "source": "fixture",
                   "not_powershell": None}], required=["installer", "bundle"])
    proc = _run_gate(pwsh, d)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "rendered 0 bundle" in proc.stdout


def test_render_errors_carried_in_the_manifest_fail_the_gate(pwsh, tmp_path):
    d = tmp_path / "m3"
    d.mkdir()
    shutil.copyfile(FIXTURES / "good_51.ps1", d / "good_51.ps1")
    _manifest(d, [{"path": "good_51.ps1", "kind": "installer", "source": "fixture",
                   "not_powershell": None}], required=["installer"],
              errors=["rendered 0 beacon-wrapper: go test failed"])
    proc = _run_gate(pwsh, d)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "rendered 0 beacon-wrapper" in proc.stdout


def test_an_empty_directory_is_degraded_not_ok(pwsh, tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    proc = _run_gate(pwsh, d)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "rendered 0" in proc.stdout


def test_an_unrecognised_manifest_schema_raises(pwsh, tmp_path):
    """Tolerance hides bugs: a manifest the gate cannot read must not be
    treated as an empty one."""
    d = tmp_path / "m4"
    d.mkdir()
    (d / "manifest.json").write_text(json.dumps({"schema": "something-else/9", "artifacts": []}))
    proc = _run_gate(pwsh, d)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "schema" in proc.stdout.lower()


# ---------------------------------------------------------------------------
# The renderer, on the real tree (no pwsh needed)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def rendered(tmp_path_factory) -> tuple[Path, dict, subprocess.CompletedProcess]:
    assert RENDER.is_file(), f"renderer missing: {RENDER}"
    out = tmp_path_factory.mktemp("ps-render")
    env = {**os.environ, "PYTHONPATH": str(REPO / "core")}
    proc = subprocess.run([sys.executable, str(RENDER), "--out", str(out)],
                          cwd=REPO, env=env, capture_output=True, text=True, timeout=900)
    manifest_path = out / "manifest.json"
    assert manifest_path.is_file(), f"no manifest written\n{proc.stdout}\n{proc.stderr}"
    return out, json.loads(manifest_path.read_text(encoding="utf-8")), proc


def test_render_on_the_real_tree_produces_installers_bundles_and_steps(rendered):
    out, manifest, proc = rendered
    assert manifest["schema"] == "cortexsim.ps-gate.manifest/1"
    counts = manifest["counts"]
    for kind in ("installer", "bundle", "step"):
        assert counts.get(kind, 0) > 0, f"rendered 0 {kind}\n{proc.stdout}\n{proc.stderr}"
    # Four installer variants: token x beacon-served.
    assert counts["installer"] == 4, counts
    for a in manifest["artifacts"]:
        assert (out / a["path"]).is_file(), a
    # Every required kind is reported, even when it is zero.
    for kind in manifest["required_kinds"]:
        assert kind in counts, kind


def test_render_flags_a_bash_uninstaller_and_only_a_bash_one(rendered):
    out, manifest, _ = rendered
    uninst = [a for a in manifest["artifacts"] if a["kind"] == "uninstaller"]
    assert len(uninst) == 1, uninst
    body = (out / uninst[0]["path"]).read_bytes()
    if body.lstrip(b"\xef\xbb\xbf").startswith(b"#!"):
        assert "windows uninstaller is not PowerShell" in (uninst[0]["not_powershell"] or "")
    else:
        assert uninst[0]["not_powershell"] is None


def test_render_never_reads_a_zero_as_ok(rendered):
    """Every required kind that rendered nothing must be an error in the
    manifest, so the gate cannot pass an empty category."""
    _, manifest, proc = rendered
    for kind in manifest["required_kinds"]:
        if manifest["counts"].get(kind, 0) == 0:
            assert any(e.startswith(f"rendered 0 {kind}") for e in manifest["errors"]), manifest["errors"]
            assert proc.returncode != 0


# ---------------------------------------------------------------------------
# Windows-only: the REAL 5.1 parser. Runs only where powershell.exe exists.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("powershell.exe") is None and shutil.which("powershell") is None,
                    reason="Windows PowerShell 5.1 (powershell.exe) only exists on Windows")
def test_gate_under_real_windows_powershell_51(tmp_path):
    exe = shutil.which("powershell.exe") or shutil.which("powershell")
    good = _run_gate(exe, _stage_into(tmp_path / "g", FIXTURES / "good_51.ps1"))
    assert good.returncode == 0, good.stdout + good.stderr
    bad = _run_gate(exe, _stage_into(tmp_path / "b", FIXTURES / "bad_ps7_only.ps1"))
    assert bad.returncode == 1, bad.stdout + bad.stderr
    quote = _run_gate(exe, _stage_into(tmp_path / "q", FIXTURES / "bad_cp1252_quote.ps1"))
    assert quote.returncode == 1, quote.stdout + quote.stderr
    assert "PARSE_CP1252" in quote.stdout


def _stage_into(d: Path, *fixtures: Path) -> Path:
    d.mkdir(parents=True)
    for f in fixtures:
        shutil.copyfile(f, d / f.name)
    return d

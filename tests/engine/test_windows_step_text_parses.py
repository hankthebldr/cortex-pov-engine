"""Every PowerShell-dialect Windows step text must parse.

The PowerShell bundle embeds each step as a literal here-string and writes it
to its own .ps1 at run time, so the bundle-level parse gate
(tests/e2e_isolated/test_tier_b_push_bundle.py::test_ps_bundle_parses_as_powershell)
parses the literal, never the step. A step whose own text does not parse is
only found on the customer's Windows endpoint. This parses the text itself:
every windows variant, every Windows-native primary and every Windows cleanup
line the resolver hands to PowerShell, corpus-wide, through the real loader.

pwsh is PowerShell 7, not the Windows PowerShell 5.1 the product targets, so a
pass here is necessary, not sufficient: 7-only syntax parses here and fails on
5.1. Nothing here ran on Windows.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from engine import push_generator as pg
from engine import scenario_loader as sl

REPO = Path(__file__).resolve().parents[2]


def _powershell_texts() -> list[dict[str, str]]:
    logging.disable(logging.CRITICAL)
    try:
        out: list[dict[str, str]] = []
        for path in sl._find_yaml_files(str(REPO / "scenarios")):
            schema, err = sl._parse_and_validate(path)
            if err or schema is None:
                continue
            d = schema.model_dump()
            sid = d["scenario_id"]
            for st in d.get("steps") or []:
                variant = (st.get("platform_variants") or {}).get("windows")
                prim = st.get("command") or ""
                cands = [("variant", variant)]
                if pg.has_strong_windows_marker(prim) and not pg.is_posix_shaped(prim):
                    cands.append(("primary", prim))
                for kind, text in cands:
                    if isinstance(text, str) and text.strip() and pg.infer_windows_shell(text) == "powershell":
                        out.append({"id": f"{sid} {st.get('id')} {kind}", "text": text})
            for i, c in enumerate((d.get("cleanup") or {}).get("commands") or [], start=1):
                if (isinstance(c, str) and pg.has_strong_windows_marker(c) and not pg.is_posix_shaped(c)
                        and pg.infer_windows_shell(c) == "powershell"):
                    out.append({"id": f"{sid} cleanup-{i:02d}", "text": c})
        return out
    finally:
        logging.disable(logging.NOTSET)


TEXTS = _powershell_texts()

_PARSE_ALL = (
    "$items = Get-Content -Raw -LiteralPath $env:CORTEXSIM_PS_TEXTS | ConvertFrom-Json; "
    "foreach ($it in $items) { $t = $null; $e = $null; "
    "[System.Management.Automation.Language.Parser]::ParseInput($it.text, [ref]$t, [ref]$e) | Out-Null; "
    "foreach ($x in $e) { Write-Output ($it.id + ' :: line ' + $x.Extent.StartLineNumber + ': ' + $x.Message) } }; "
    "Write-Output ('CXS-PARSED ' + @($items).Count)"
)


def test_the_powershell_text_set_is_not_empty():
    """A zero is degraded, not ok."""
    assert len(TEXTS) >= 100, len(TEXTS)


def _pwsh_or_skip() -> str:
    """The parser, or a skip — unless it was promised, then a FAILURE.

    Same contract as ``test_push_generator_windows.py::_powershell_or_skip``:
    under ``CI`` or ``CORTEXSIM_REQUIRE_POWERSHELL=1`` a missing interpreter
    fails the test. A skipped parse gate is indistinguishable from a passing
    one. ``pwsh`` specifically: ``_PARSE_ALL`` is run with its ``-Command``.
    """
    exe = shutil.which("pwsh")
    if exe is None:
        if os.environ.get("CORTEXSIM_REQUIRE_POWERSHELL") == "1" or os.environ.get("CI"):
            pytest.fail("PowerShell is required here (CI or CORTEXSIM_REQUIRE_POWERSHELL=1) "
                        "and pwsh is not on PATH: the per-step parse gate did not run")
        pytest.skip("pwsh not installed")
    return exe


def test_every_powershell_windows_step_text_parses():
    pwsh = _pwsh_or_skip()
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(TEXTS, f)
        path = f.name
    try:
        r = subprocess.run(
            [pwsh, "-NoProfile", "-NonInteractive", "-Command", _PARSE_ALL],
            env={**os.environ, "CORTEXSIM_PS_TEXTS": path},
            capture_output=True, text=True, timeout=120,
        )
    finally:
        Path(path).unlink(missing_ok=True)
    assert f"CXS-PARSED {len(TEXTS)}" in r.stdout, (r.stdout[-2000:], r.stderr[-2000:])
    errors = [ln for ln in r.stdout.splitlines() if " :: line " in ln]
    assert not errors, "PowerShell parse errors in Windows step text:\n  " + "\n  ".join(errors)


def test_the_parse_gate_can_fail():
    pwsh = _pwsh_or_skip()
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump([{"id": "broken", "text": "if ($x { Write-Output 'unclosed'"}], f)
        path = f.name
    try:
        r = subprocess.run(
            [pwsh, "-NoProfile", "-NonInteractive", "-Command", _PARSE_ALL],
            env={**os.environ, "CORTEXSIM_PS_TEXTS": path},
            capture_output=True, text=True, timeout=60,
        )
    finally:
        Path(path).unlink(missing_ok=True)
    assert "broken :: line 1" in r.stdout, r.stdout


# ── RG-PUSH-03: where the parser is promised, its absence is a failure ──────
#
# Both parse tests above used a plain `skipif(shutil.which("pwsh") is None)`
# decorator; they now ask `_pwsh_or_skip()`.
# Observed with pwsh hidden:
#
#     CI=1 CORTEXSIM_REQUIRE_POWERSHELL=1 pytest tests/engine/test_windows_step_text_parses.py -rs
#     1 passed, 2 skipped        (both: "pwsh not installed")
#
# while every sibling parse gate fails under the same flags
# (test_push_generator_windows.py::_powershell_or_skip). This is the only test
# that parses each step's OWN text, so a skip there read green with the gate
# not run.


@pytest.mark.parametrize("flag", ["CI", "CORTEXSIM_REQUIRE_POWERSHELL"])
@pytest.mark.parametrize("gate", ["test_every_powershell_windows_step_text_parses",
                                  "test_the_parse_gate_can_fail"])
def test_a_required_parser_that_is_missing_fails_the_gate(monkeypatch, flag, gate):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("CORTEXSIM_REQUIRE_POWERSHELL", raising=False)
    monkeypatch.setenv(flag, "1")
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: None)
    with pytest.raises(pytest.fail.Exception, match="PowerShell is required"):
        globals()[gate]()


@pytest.mark.parametrize("gate", ["test_every_powershell_windows_step_text_parses",
                                  "test_the_parse_gate_can_fail"])
def test_a_missing_parser_still_skips_where_nothing_promised_it(monkeypatch, gate):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("CORTEXSIM_REQUIRE_POWERSHELL", raising=False)
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: None)
    with pytest.raises(pytest.skip.Exception):
        globals()[gate]()

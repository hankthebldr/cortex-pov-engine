"""coverage_report.is_marker_only_step must recognise PowerShell marker shapes.

The bash marker idiom (``echo '...' > /tmp/marker``) has always counted as
marker-only. Its PowerShell twin — ``$s = Join-Path $env:TEMP 'x'; New-Item
-ItemType Directory $s | Out-Null; Set-Content -Path (Join-Path $s 'm.marker')
-Value '...'; Write-Host 'done'`` — did not, so SIM-EDR-013 / SIM-EDR-021 steps
that only write a marker file were counted as observable signal and inflated
both the high-quality-signal count and the marker-only share's denominator.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "detection_scanner" / "scripts" / "coverage_report.py"


@pytest.fixture(scope="module")
def cr():
    spec = importlib.util.spec_from_file_location("coverage_report_psmarkers", SCRIPT)
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    sys.modules["coverage_report_psmarkers"] = m
    spec.loader.exec_module(m)
    return m


@pytest.mark.parametrize("command", [
    # SIM-EDR-021 step-02 on base, verbatim shape.
    "$stage = Join-Path $env:TEMP 'cortexsim-edr-021'; Set-Content -Path (Join-Path $stage "
    "'SAFE-MODE-byovd-driver-load.marker') -Value 'marker only'; Write-Host 'cortexsim-edr-021-x'",
    # SIM-EDR-013 step-01 shape: directory + marker + host line.
    "$stage = Join-Path $env:TEMP 'cortexsim-edr-013'; New-Item -ItemType Directory -Force "
    "-Path $stage | Out-Null; Set-Content -Path (Join-Path $stage 'm.marker') -Value 'x'; "
    "Write-Host 'staged'",
    "Write-Output 'cortexsim marker'\nWrite-Host 'done'",
])
def test_powershell_marker_steps_are_marker_only(cr, command):
    assert cr.is_marker_only_step({"command": command}) is True


@pytest.mark.parametrize("command", [
    # Real benign telemetry is NOT a marker, even wrapped in marker lines.
    "$t = 'cortexsim-x'; schtasks.exe /create /tn $t /tr whoami.exe /sc once /st 23:59 /f; "
    "schtasks.exe /delete /tn $t /f; Write-Host 'done'",
    "Set-Content -Path x.marker -Value 'x'; vssadmin.exe list shadows",
    "$h = '127.0.0.1'; Invoke-WebRequest -UseBasicParsing -Uri \"http://$h/x\"",
    "$x = Join-Path $env:TEMP 'a'; Copy-Item $env:SystemRoot\\System32\\whoami.exe $x",
])
def test_steps_that_execute_something_are_not_marker_only(cr, command):
    assert cr.is_marker_only_step({"command": command}) is False


def test_the_bash_marker_rule_is_unchanged(cr):
    assert cr.is_marker_only_step({"command": "echo '[SAFE-MODE] x'; true"}) is True
    assert cr.is_marker_only_step({"command": "cat /etc/shadow"}) is False

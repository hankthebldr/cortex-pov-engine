"""The uninstall one-liners: ``GET /api/agents/install?uninstall=1``.

A DC leaving a customer site needs ONE line that takes the beacon back off the
endpoint, and an honest answer about whether it did. Two defects motivated this
file:

* ``?os=windows&uninstall=1`` returned the **bash** uninstaller with a ``.sh``
  filename. A DC on a Windows endpoint had no removal path at all; the
  docstring called ``sc.exe delete`` "documented" when nothing documented it.
* Both uninstallers reported "nothing to remove" whenever nothing was *removed*
  — including when the beacon was there and the removal failed.

The POSIX script's runtime honesty is executed in
``tests/installer/test_posix_uninstaller_honesty.py``. The Windows script cannot
execute here (no Service Control Manager), so its structure is asserted and its
parse / scoping is executed under PowerShell; ``windows_only`` tests run it on a
Windows host.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess

import pytest

from tests.api.test_windows_installer import (
    PWSH,
    SHELLS,
    _PARSE_HARNESS,
    _PS7_ONLY_HARNESS,
    _code_lines,
    _assert_native_calls_checked,
    _ps,
    _write,
    needs_powershell,
    needs_pwsh,
    windows_only,
)


@pytest.fixture
def client(make_client):
    from api.agents import router
    return make_client(router)


def _win_uninstaller(client) -> str:
    r = client.get("/api/agents/install", params={"os": "windows", "uninstall": "1"})
    assert r.status_code == 200, r.text[:300]
    # Every structural test below would pass vacuously against the bash
    # script the route used to hand Windows; refuse it here, once.
    assert not r.text.startswith("#!"), "windows was handed the POSIX uninstaller"
    return r.text


# ---------------------------------------------------------------------------
# Routing — the right script for the right OS, and no silently ignored combos
# ---------------------------------------------------------------------------


def test_windows_uninstall_is_a_powershell_script(client):
    r = client.get("/api/agents/install", params={"os": "windows", "uninstall": "1"})
    assert r.status_code == 200
    assert not r.text.startswith("#!/usr/bin/env bash"), "windows was handed the bash uninstaller"
    assert 'filename="uninstall-cortexsim-agent.ps1"' in r.headers["content-disposition"]
    assert r.headers["content-type"].startswith("text/plain")
    assert "CortexSimAgent" in r.text


@pytest.mark.parametrize("os_name", ["linux", "macos", "darwin"])
def test_posix_uninstall_is_still_bash(client, os_name):
    r = client.get("/api/agents/install", params={"os": os_name, "uninstall": "1"})
    assert r.status_code == 200
    assert r.text.startswith("#!/usr/bin/env bash")
    assert 'filename="uninstall-cortexsim-agent.sh"' in r.headers["content-disposition"]


def test_bare_uninstall_defaults_to_the_posix_script(client):
    """Back-compat: the documented `?uninstall=1 | bash` one-liner has no os=."""
    r = client.get("/api/agents/install?uninstall=1")
    assert r.status_code == 200
    assert r.text.startswith("#!/usr/bin/env bash")


@pytest.mark.parametrize("os_name", ["windows", "linux"])
def test_uninstall_with_an_install_mode_is_rejected(client, os_name):
    """`mode` selects how to INSTALL; an uninstall request carrying
    mode=foreground is a confused caller, not something to guess about."""
    r = client.get("/api/agents/install",
                   params={"os": os_name, "uninstall": "1", "mode": "foreground"})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "BAD_MODE"


def test_uninstall_with_an_unsupported_os_is_rejected(client):
    r = client.get("/api/agents/install", params={"os": "solaris", "uninstall": "1"})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "BAD_OS"


# ---------------------------------------------------------------------------
# The Windows uninstaller — bytes, parse, scope
# ---------------------------------------------------------------------------


def test_windows_uninstaller_is_pure_ascii(client):
    _win_uninstaller(client).encode("ascii")


@needs_powershell
@pytest.mark.parametrize("shell", SHELLS or ["none"])
@pytest.mark.parametrize("codepage", ["65001", "1252"])
def test_windows_uninstaller_parses_as_utf8_and_cp1252(client, tmp_path, shell, codepage):
    path = _write(tmp_path, "uninstall-cortexsim-agent.ps1", _win_uninstaller(client))
    harness = _write(tmp_path, "parse.ps1", _PARSE_HARNESS)
    p = _ps(shell, "-File", str(harness), "-Path", str(path), "-CodePage", codepage)
    assert "errors=0" in p.stdout, p.stdout + p.stderr


@needs_pwsh
def test_windows_uninstaller_uses_no_powershell_7_only_syntax(client, tmp_path):
    path = _write(tmp_path, "u.ps1", _win_uninstaller(client))
    harness = _write(tmp_path, "ps7only.ps1", _PS7_ONLY_HARNESS)
    p = _ps(PWSH, "-File", str(harness), "-Path", str(path))
    assert "parse_errors=0" in p.stdout and "hits=0" in p.stdout, p.stdout + p.stderr


@needs_powershell
@pytest.mark.skipif(os.name == "nt", reason="on Windows this would really uninstall")
@pytest.mark.parametrize("shell", SHELLS or ["none"])
def test_windows_uninstaller_under_iex_leaves_the_callers_shell_alive(client, tmp_path, shell):
    path = _write(tmp_path, "u.ps1", _win_uninstaller(client))
    cmd = (f"Invoke-Expression (Get-Content -Raw -LiteralPath '{path}'); "
           "Write-Host CALLER-STILL-ALIVE; Write-Host \"EAP=$ErrorActionPreference\"; "
           "if (Get-Command Cs-Exec -ErrorAction SilentlyContinue) {'LEAK-Cs-Exec'}; "
           "if ($Svc) {'LEAK-Svc'}")
    p = _ps(shell, "-Command", cmd)
    assert "code=UNSUPPORTED_OS" in p.stdout, p.stdout + p.stderr
    assert "CALLER-STILL-ALIVE" in p.stdout
    assert "EAP=Continue" in p.stdout
    assert "LEAK" not in p.stdout


# ---------------------------------------------------------------------------
# The Windows uninstaller — structure (the parts only a Windows host can run)
# ---------------------------------------------------------------------------


def test_windows_uninstaller_is_scoped_and_never_calls_a_bare_exit(client):
    script = _win_uninstaller(client)
    code = "\n".join(t for _, t in _code_lines(script))
    assert code.lstrip().startswith("& {")
    exits = [ln.strip() for ln in code.splitlines() if re.search(r"\bexit\b", ln)]
    assert exits == ["if ($PSCommandPath) { exit 1 } else { return }"], exits


def test_windows_uninstaller_checks_every_native_exit_code(client):
    _assert_native_calls_checked(_win_uninstaller(client), minimum=2)  # sc.exe stop + delete


def test_windows_uninstaller_handles_the_two_sc_codes_that_are_not_errors(client):
    """1060 = the service does not exist (nothing to stop or delete);
    1072 = marked for deletion, gone at the next reboot. Neither is a failure,
    and 1072 must not be reported as removed either."""
    script = _win_uninstaller(client)
    assert "1060" in script and "1072" in script
    assert "reboot" in script.lower()


def test_windows_uninstaller_stops_every_process_and_removes_every_location(client):
    script = _win_uninstaller(client)
    assert "Stop-Process" in script and "Wait-Process" in script
    assert "'cortexsim-agent'" in script
    for where in ("$env:ProgramData", "$env:LOCALAPPDATA", "GetTempPath()", "cortexsim-cache"):
        assert where in script, where
    assert "Temp\\cortexsim-cache" in script  # C:\Windows\Temp, where a SYSTEM service stages


def test_windows_uninstaller_removes_the_beacons_own_cache_and_stage_trees(client):
    """agent/beacon/artifact.go caches under /opt/cortexsim/cache first, which
    on Windows is <SystemDrive>:\\opt\\cortexsim, stages under
    %windir%\\Temp\\cortexsim, and a SYSTEM service's os.UserCacheDir is the
    systemprofile LocalAppData. The cache holds staged dual-use tools, so an
    uninstall that skipped these called a carrying host clean."""
    script = _win_uninstaller(client)
    for where in ('"$env:SystemDrive\\opt\\cortexsim"',
                  '"$env:SystemRoot\\Temp\\cortexsim"',
                  "systemprofile\\AppData\\Local\\cortexsim",
                  "$env:CORTEXSIM_CACHE_DIR", "$env:CORTEXSIM_STAGE_ROOT"):
        assert where in script, where


def test_windows_uninstaller_scratch_glob_is_the_installers_own_shape(client):
    """`cortexsim-*` under TEMP could match a directory an operator made; only
    the installer's `cortexsim-<guid N>` scratch is removed."""
    script = _win_uninstaller(client)
    assert "-match '^cortexsim-[0-9a-f]{32}$'" in script


def test_nothing_to_remove_is_reserved_for_nothing_found(client):
    """The base uninstaller printed it whenever nothing was REMOVED — so a
    failed removal read as a clean host."""
    script = _win_uninstaller(client)
    i = script.find("Cs-Say 'nothing to remove")
    assert i != -1
    guard = script[:i].rsplit("\n", 2)[-2:]
    assert any("$Found.Count -eq 0" in g for g in guard), guard
    assert "'UNINSTALL_INCOMPLETE'" in script
    assert script.find("$Left.Count -gt 0") < i, "an incomplete removal must be checked before 'nothing to remove'"


def test_windows_uninstaller_refuses_unelevated_when_the_service_exists(client):
    script = _win_uninstaller(client)
    assert "IsInRole(" in script and "'NOT_ELEVATED'" in script
    assert script.find("'NOT_ELEVATED'") < script.find("'stop', $Svc")


def test_windows_uninstaller_names_the_server_side_cleanup(client):
    script = _win_uninstaller(client)
    assert "DELETE" in script and "/api/agents/" in script


_HINT_HARNESS = r"""
param([string]$ScriptPath)
$t = $null; $e = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($ScriptPath, [ref]$t, [ref]$e)
foreach ($f in $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true)) {
  . ([scriptblock]::Create($f.Extent.Text))
}
$exe = '"C:\ProgramData\CortexSim\cortexsim-agent.exe"'
Write-Output ("HINT http  | " + (Cs-DeleteHint "$exe --server http://10.0.0.5:8888 --id win-01-a1b2c3 --interval 10"))
Write-Output ("HINT https | " + (Cs-DeleteHint "$exe --server https://simcore.corp.local --id win-01-a1b2c3 --interval 10"))
Write-Output ("HINT none  | " + (Cs-DeleteHint "$exe --interval 10"))
"""


@needs_powershell
@pytest.mark.parametrize("shell", SHELLS or ["none"])
def test_the_agent_row_hint_for_an_https_simcore_carries_the_tls_opt_in(client, tmp_path, shell):
    """The uninstaller prints the command that deletes the agent row, read from
    the service's own command line. On a 5.1 host with old TLS defaults that
    command cannot reach an https SimCore without the TLS 1.2 opt-in, so the
    hint carries it; for http it is left out; and a command line it cannot read
    yields no hint at all, never a half-built one."""
    path = _write(tmp_path, "u.ps1", _win_uninstaller(client))
    harness = _write(tmp_path, "hint.ps1", _HINT_HARNESS)
    p = _ps(shell, "-File", str(harness), "-ScriptPath", str(path))
    got = {ln.split("|", 1)[0].split()[1]: ln.split("|", 1)[1].strip()
           for ln in p.stdout.splitlines() if ln.startswith("HINT ")}
    delete = "Invoke-RestMethod -Method Delete '{}/api/agents/win-01-a1b2c3'"
    tls = ("[Net.ServicePointManager]::SecurityProtocol = "
           "[Net.ServicePointManager]::SecurityProtocol -bor 3072; ")
    assert got == {
        "http": delete.format("http://10.0.0.5:8888"),
        "https": tls + delete.format("https://simcore.corp.local"),
        "none": "",
    }, p.stdout + p.stderr


_PROFILES_HARNESS = r"""
param([string]$ScriptPath, [string]$Root)
$t = $null; $e = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($ScriptPath, [ref]$t, [ref]$e)
foreach ($f in $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true)) {
  . ([scriptblock]::Create($f.Extent.Text))
}
$ErrorActionPreference = 'Stop'
foreach ($d in @(Cs-ProfileInstalls $Root)) { Write-Output ("FOUND " + $d) }
Write-Output ("MISSING-ROOT " + @(Cs-ProfileInstalls (Join-Path $Root 'no-such-dir')).Count)
Write-Output ("EMPTY-ROOT " + @(Cs-ProfileInstalls '').Count)
"""


@needs_powershell
@pytest.mark.parametrize("shell", SHELLS or ["none"])
def test_an_elevated_uninstall_looks_in_every_profile_for_a_per_user_beacon(client, tmp_path, shell):
    """A standard user who answers the UAC prompt with an admin's credentials
    gets an elevated shell running as THAT admin, whose %LOCALAPPDATA% is not
    theirs. The uninstaller looked for the per-user beacon
    (CORTEXSIM_ALLOW_UNSUPERVISED) only under %LOCALAPPDATA%, so it killed the
    process, removed nothing else, and reported "done" with the binary and its
    logs still in the installing user's profile: the Windows twin of the POSIX
    `sudo` case. Executed here for the lookup only (pwsh on this host); the
    removal itself runs on Windows."""
    profiles = tmp_path / "Users"
    install = profiles / "dc" / "AppData" / "Local" / "CortexSim"
    cache = profiles / "dc" / "AppData" / "Local" / "Temp" / "cortexsim-cache"
    for d in (install, cache, profiles / "bystander" / "AppData" / "Local", profiles / "Public"):
        d.mkdir(parents=True)
    (install / "cortexsim-agent.exe").write_bytes(b"MZ")

    path = _write(tmp_path, "u.ps1", _win_uninstaller(client))
    harness = _write(tmp_path, "profiles.ps1", _PROFILES_HARNESS)
    p = _ps(shell, "-File", str(harness), "-ScriptPath", str(path), "-Root", str(profiles))
    found = sorted(os.path.normpath(ln.split(" ", 1)[1]) for ln in p.stdout.splitlines()
                   if ln.startswith("FOUND "))
    assert found == sorted(os.path.normpath(str(d)) for d in (install, cache)), p.stdout + p.stderr
    assert "MISSING-ROOT 0" in p.stdout and "EMPTY-ROOT 0" in p.stdout, p.stdout + p.stderr


def test_the_profile_sweep_runs_only_elevated_and_through_the_tracked_remover(client):
    script = _win_uninstaller(client)
    sweep = [ln.strip() for ln in script.splitlines()
             if "Cs-ProfileInstalls " in ln and not ln.startswith("function ")]
    assert len(sweep) == 1, sweep
    assert '"$env:SystemDrive\\Users"' in sweep[0], sweep[0]
    assert sweep[0].startswith("if ($IsAdmin)"), sweep[0]
    assert "Cs-RemovePath $d 'per-user install'" in sweep[0], (
        "a per-profile hit must be counted as found and reported if it survives: " + sweep[0])


# ---------------------------------------------------------------------------
# Windows host only
# ---------------------------------------------------------------------------


def _cortexsim_installed_here() -> bool:
    q = subprocess.run(["sc.exe", "query", "CortexSimAgent"], capture_output=True, text=True)
    pd = os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "CortexSim")
    return q.returncode != 1060 or os.path.exists(pd)


@windows_only
def test_on_a_clean_windows_host_the_uninstaller_finds_nothing_and_succeeds(client, tmp_path):
    """Run by Windows PowerShell 5.1. Skips rather than uninstalls if a real
    beacon is present, so it can never remove a DC's working install."""
    if _cortexsim_installed_here():
        pytest.skip("a CortexSim beacon is installed on this host; not removing it from a test")
    ps51 = shutil.which("powershell")
    path = _write(tmp_path, "u.ps1", _win_uninstaller(client))
    for _ in range(2):  # idempotent
        p = _ps(ps51, "-File", str(path))
        assert p.returncode == 0, p.stdout + p.stderr
        assert "nothing to remove" in p.stdout

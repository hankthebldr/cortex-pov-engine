"""Guards for the Windows one-line installer (``GET /api/agents/install?os=windows``).

What these protect, and why each one exists:

(a) **The bytes must parse on Windows PowerShell 5.1.** 5.1 reads a BOM-less
    ``.ps1`` as the ANSI code page (cp1252). A UTF-8 em-dash is ``E2 80 94``;
    byte ``0x94`` decodes to U+201D, a closing *smart quote*, which PowerShell
    treats as a string terminator. The downloaded ``install-cortexsim-agent.ps1``
    therefore failed to parse on the product's own floor. Pure ASCII is the
    only encoding-proof answer, and it is asserted on every variant.

(b) **A failure under ``iwr | iex`` must not take the operator's shell with it.**
    ``iex`` runs in the caller's session, so a bare ``exit 1`` closed the DC's
    console, and a top-level ``$ErrorActionPreference = 'Stop'`` / ``$Token``
    leaked into it.

(c) **Every value baked into the script is validated.** ``id``, ``server``,
    ``token``, ``interval`` and the Host header (the default ``server``) went
    into the script text verbatim, so ``?id=x'; Write-Host PWNED; '`` produced a
    script that ran attacker text as SYSTEM.

(d) **Windows gets a Windows uninstaller** (``?os=windows&uninstall=1`` used to
    hand back the bash script) and a ``mode`` Windows does not have is refused.

(e) **Static ordering a reviewer cannot be trusted to eyeball**: every native
    call's exit code is checked, ``OK`` is reachable only after the service is
    confirmed Running *and* the beacon checked in, and elevation is decided
    before anything is enrolled.

NOT provable here: Service Control Manager behaviour, the 5.1 runtime, icacls /
ACL semantics and ``New-Service``. The pwsh-based checks below run PowerShell 7
on a non-Windows host; they prove parsing, scoping and the HTTP error mapping,
never Windows execution. Tests marked ``windows_only`` run on a Windows host
(the windows CI job) and are skipped everywhere else.
"""
from __future__ import annotations

import http.server
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import threading
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def _powershells() -> list[str]:
    """Every PowerShell this host can run: pwsh (7.x) anywhere, and Windows
    PowerShell 5.1 (``powershell.exe``) on Windows, where it is the product floor."""
    found: list[str] = []
    for cand in (shutil.which("pwsh"), "/opt/homebrew/bin/pwsh", "/usr/local/bin/pwsh"):
        if cand and os.path.exists(cand) and cand not in found:
            found.append(cand)
    if os.name == "nt":
        ps51 = shutil.which("powershell")
        if ps51:
            found.append(ps51)
    return found


SHELLS = _powershells()
PWSH = next((s for s in SHELLS if "pwsh" in os.path.basename(s).lower()), None)
needs_powershell = pytest.mark.skipif(not SHELLS, reason="no PowerShell on this host")
needs_pwsh = pytest.mark.skipif(PWSH is None, reason="pwsh (PowerShell 7) not on this host")
windows_only = pytest.mark.skipif(os.name != "nt", reason="executes on a Windows host only")


def _ps(shell: str, *args: str, timeout: int = 120, env: dict | None = None) -> subprocess.CompletedProcess:
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    return subprocess.run([shell, "-NoProfile", "-NonInteractive", *args],
                          capture_output=True, text=True, timeout=timeout, env=full_env)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


TOKEN = "cxs_" + "a" * 43  # the shape POST /api/agents/enroll/tokens mints


@pytest.fixture
def client(make_client):
    from api.agents import router
    return make_client(router)


@pytest.fixture
def shelf(tmp_path, monkeypatch):
    """Factory: point the beacon shelf at a temp dir, with or without a windows beacon."""
    def _make(with_windows: bool) -> Path:
        d = tmp_path / ("dist-win" if with_windows else "dist-nowin")
        d.mkdir(exist_ok=True)
        (d / "cortexsim-agent-linux-amd64").write_bytes(b"\x7fELF-fake")
        if with_windows:
            (d / "cortexsim-agent-windows-amd64.exe").write_bytes(b"MZ-fake-windows")
        monkeypatch.setenv("CORTEXSIM_AGENT_DIST", str(d))
        return d
    return _make


VARIANTS = {
    "token+binary": dict(with_windows=True, token=TOKEN),
    "token+no-binary": dict(with_windows=False, token=TOKEN),
    "no-token+binary": dict(with_windows=True, token=None),
    "no-token+no-binary": dict(with_windows=False, token=None),
}


def _render(shelf, variant: str, server: str = "http://10.0.0.5:8888") -> str:
    from api.agents import _windows_installer

    spec = VARIANTS[variant]
    shelf(spec["with_windows"])
    return _windows_installer(server, "win-01", 10, spec["token"])


def _write(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.write_bytes(text.encode("utf-8"))  # exactly what a browser / iwr -OutFile saves
    return p


# ---------------------------------------------------------------------------
# (a) ASCII, and parses as UTF-8 AND as cp1252
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("variant", sorted(VARIANTS))
def test_every_installer_variant_is_pure_ascii(shelf, variant):
    script = _render(shelf, variant)
    bad = sorted({(i, ch) for i, ch in enumerate(script) if ord(ch) > 0x7F}, key=lambda t: t[0])
    assert not bad, (
        f"{len(bad)} non-ASCII characters (first: {bad[:5]}). Windows PowerShell 5.1 "
        "reads a BOM-less .ps1 as cp1252; byte 0x94 of a UTF-8 em-dash becomes a "
        "closing smart quote and ends the string literal it sits in.")
    script.encode("ascii")


_PARSE_HARNESS = r"""
param([string]$Path, [int]$CodePage)
$bytes = [IO.File]::ReadAllBytes($Path)
if ($CodePage -eq 65001) { $text = [Text.Encoding]::UTF8.GetString($bytes) }
else { $text = [Text.Encoding]::GetEncoding($CodePage).GetString($bytes) }
$t = $null; $e = $null
[void][System.Management.Automation.Language.Parser]::ParseInput($text, [ref]$t, [ref]$e)
Write-Output ("errors=" + $e.Count)
foreach ($x in $e) { Write-Output ("  line " + $x.Extent.StartLineNumber + ": " + $x.Message) }
"""


@needs_powershell
@pytest.mark.parametrize("shell", SHELLS or ["none"])
@pytest.mark.parametrize("codepage", [65001, 1252])
@pytest.mark.parametrize("variant", sorted(VARIANTS))
def test_every_installer_variant_parses_as_utf8_and_cp1252(shelf, tmp_path, shell, codepage, variant):
    path = _write(tmp_path, "install-cortexsim-agent.ps1", _render(shelf, variant))
    harness = _write(tmp_path, "parse.ps1", _PARSE_HARNESS)
    p = _ps(shell, "-File", str(harness), "-Path", str(path), "-CodePage", str(codepage))
    assert p.returncode == 0, p.stderr
    assert "errors=0" in p.stdout, f"{variant} as cp{codepage} under {shell}:\n{p.stdout}\n{p.stderr}"


# PowerShell 7 parses syntax 5.1 rejects, so "pwsh parsed it" is necessary but
# not sufficient. Walk the AST for the constructs and parameters 5.1 lacks.
_PS7_ONLY_HARNESS = r"""
param([string]$Path)
$t = $null; $e = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Path, [ref]$t, [ref]$e)
$hits = New-Object System.Collections.ArrayList
foreach ($n in $ast.FindAll({ param($x) $x.GetType().Name -in 'TernaryExpressionAst','PipelineChainAst' }, $true)) {
  [void]$hits.Add("syntax: " + $n.Extent.Text) }
foreach ($n in $ast.FindAll({ param($x) $x -is [System.Management.Automation.Language.BinaryExpressionAst] -and "$($x.Operator)" -eq 'QuestionQuestion' }, $true)) {
  [void]$hits.Add("??: " + $n.Extent.Text) }
foreach ($n in $ast.FindAll({ param($x) $x -is [System.Management.Automation.Language.AssignmentStatementAst] -and "$($x.Operator)" -eq 'QuestionQuestionEquals' }, $true)) {
  [void]$hits.Add("??=: " + $n.Extent.Text) }
foreach ($k in $t) { if ("$($k.Kind)" -in 'QuestionDot','QuestionLBracket','AndAnd','OrOr') { [void]$hits.Add("token: " + $k.Text) } }
$p7params = 'Parallel','SkipCertificateCheck','NoProxy','AllowInsecureRedirect','SslProtocol','Resume',
            'SkipHttpErrorCheck','StatusCodeVariable','AdditionalChildPath','AsHashtable','ResponseHeadersVariable'
foreach ($n in $ast.FindAll({ param($x) $x -is [System.Management.Automation.Language.CommandParameterAst] }, $true)) {
  if ($p7params -contains $n.ParameterName) { [void]$hits.Add("ps7-only parameter: " + $n.Extent.Text) } }
$p7cmds = 'Remove-Service','Get-Error','Test-Json','Join-String','Get-Uptime','ConvertFrom-Markdown'
foreach ($n in $ast.FindAll({ param($x) $x -is [System.Management.Automation.Language.CommandAst] }, $true)) {
  $name = $n.GetCommandName(); if ($name -and ($p7cmds -contains $name)) { [void]$hits.Add("ps7-only command: " + $name) } }
foreach ($n in $ast.FindAll({ param($x) $x -is [System.Management.Automation.Language.VariableExpressionAst] }, $true)) {
  if ($n.VariablePath.UserPath -in 'IsWindows','IsLinux','IsMacOS','IsCoreCLR') { [void]$hits.Add("ps6+ automatic variable: " + $n.Extent.Text) } }
$autos = 'args','input','this','_','PSItem','Matches','Error','Host','PID','PWD','HOME','PSCommandPath','LASTEXITCODE','ErrorActionPreference_'
foreach ($n in $ast.FindAll({ param($x) $x -is [System.Management.Automation.Language.AssignmentStatementAst] }, $true)) {
  $l = $n.Left
  if ($l -is [System.Management.Automation.Language.VariableExpressionAst] -and ($autos -contains $l.VariablePath.UserPath) -and -not $l.VariablePath.IsGlobal) {
    [void]$hits.Add("assigns automatic variable: " + $n.Extent.Text) } }
Write-Output ("parse_errors=" + $e.Count)
Write-Output ("hits=" + $hits.Count)
foreach ($h in $hits) { Write-Output ("  " + $h) }
"""


@needs_pwsh
@pytest.mark.parametrize("variant", sorted(VARIANTS))
def test_installer_uses_no_powershell_7_only_syntax(shelf, tmp_path, variant):
    """pwsh 7 accepts ``a ? b : c``, ``??``, ``&&`` and ``-SkipCertificateCheck``;
    Windows PowerShell 5.1 does not. ``$Args`` is assigned nowhere: it is an
    automatic variable, and the old installer clobbered it."""
    path = _write(tmp_path, "inst.ps1", _render(shelf, variant))
    harness = _write(tmp_path, "ps7only.ps1", _PS7_ONLY_HARNESS)
    p = _ps(PWSH, "-File", str(harness), "-Path", str(path))
    assert "parse_errors=0" in p.stdout, p.stdout + p.stderr
    assert "hits=0" in p.stdout, p.stdout


# ---------------------------------------------------------------------------
# (b) iex survival — the operator's shell outlives a failed install
# ---------------------------------------------------------------------------


def _iex_caller(path: Path, proto: int | None = None) -> str:
    """Run the script the way `iwr | iex` does, then print what it left behind.
    ``proto`` pre-sets the session's [Net.ServicePointManager]::SecurityProtocol
    (192 = Tls only, the shape of a 5.1 host with old defaults; 0 = SystemDefault)."""
    preset = ("" if proto is None else
              f"[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]{proto}; ")
    return (
        "$env:CORTEXSIM_TELEMETRY='0'; "
        f"{preset}"
        f"Invoke-Expression (Get-Content -Raw -LiteralPath '{path}'); "
        "Write-Host CALLER-STILL-ALIVE; "
        "Write-Host \"EAP=$ErrorActionPreference\"; "
        "Write-Host \"PP=$ProgressPreference\"; "
        "Write-Host \"SP=$([int][Net.ServicePointManager]::SecurityProtocol)\"; "
        "if ($Token) {'LEAK-Token'}; if ($Server) {'LEAK-Server'}; "
        "if (Get-Command Cs-Fail -ErrorAction SilentlyContinue) {'LEAK-Cs-Fail'}"
    )


def _failing_variants() -> list[str]:
    # With no windows beacon on the shelf the installer fails at the preflight
    # on ANY host. With one, it fails at the OS check off Windows — and would
    # install for real on Windows, so that variant is only exercised elsewhere.
    out = ["token+no-binary", "no-token+no-binary"]
    if os.name != "nt":
        out.append("token+binary")
    return out


@needs_powershell
@pytest.mark.parametrize("shell", SHELLS or ["none"])
@pytest.mark.parametrize("variant", _failing_variants())
def test_a_failed_install_under_iex_leaves_the_callers_shell_alive(shelf, tmp_path, shell, variant):
    # A closed loopback port: nothing here may reach a real host.
    script = _render(shelf, variant, server=f"http://127.0.0.1:{_free_port()}")
    path = _write(tmp_path, "inst.ps1", script)
    p = _ps(shell, "-Command", _iex_caller(path))
    out = p.stdout + p.stderr
    assert "[cortexsim] FAILED" in out, out
    assert "CALLER-STILL-ALIVE" in p.stdout, (
        "the installer killed the caller's session (a bare `exit` under iex):\n" + out)
    assert "EAP=Continue" in p.stdout, "ErrorActionPreference leaked into the caller:\n" + out
    assert "PP=Continue" in p.stdout, "ProgressPreference leaked into the caller:\n" + out
    assert "LEAK" not in p.stdout, "installer variables/functions leaked into the caller:\n" + out
    assert p.returncode == 0, out


@needs_powershell
@pytest.mark.parametrize("shell", SHELLS or ["none"])
@pytest.mark.parametrize("proto", [192, 0], ids=["legacy-Tls-only", "SystemDefault"])
def test_the_tls_opt_in_does_not_outlive_the_script(shelf, tmp_path, shell, proto):
    """Under `iwr | iex` the script runs in the operator's own session, and
    [Net.ServicePointManager]::SecurityProtocol is process-wide. The script set
    it to `current -bor 3072` and left it: a session on SystemDefault (0, where
    the OS negotiates TLS 1.2 and 1.3) came back pinned to Tls12 alone (3072),
    and one on a legacy list came back changed. The header promises the shell is
    left exactly as it was."""
    script = _render(shelf, "token+no-binary", server=f"http://127.0.0.1:{_free_port()}")
    path = _write(tmp_path, "inst.ps1", script)
    p = _ps(shell, "-Command", _iex_caller(path, proto=proto))
    out = p.stdout + p.stderr
    assert "[cortexsim] FAILED" in out and "CALLER-STILL-ALIVE" in p.stdout, out
    assert f"SP={proto}" in p.stdout, f"the session's SecurityProtocol was {proto} and is now:\n{out}"


_TLS_HARNESS = r"""
param([string]$ScriptPath)
$t = $null; $e = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($ScriptPath, [ref]$t, [ref]$e)
foreach ($f in $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true)) {
  . ([scriptblock]::Create($f.Extent.Text))
}
foreach ($start in 192, 0, 3072, 3264) {
  [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]$start
  $prev = Cs-Tls12
  Write-Output ("TLS start=$start prev=$([int]$prev) now=$([int][Net.ServicePointManager]::SecurityProtocol)")
}
"""


@needs_powershell
@pytest.mark.parametrize("shell", SHELLS or ["none"])
def test_tls12_is_added_to_a_legacy_list_and_system_default_is_left_alone(shelf, tmp_path, shell):
    """5.1 on a .NET that predates TLS 1.2-by-default lists Ssl3|Tls, which an
    https SimCore refuses: Tls12 is ADDED to an explicit list. SystemDefault (0)
    is left alone: OR-ing 3072 into it would restrict the session to TLS 1.2.
    Enum arithmetic only; whether a real 5.1 host then reaches an https SimCore
    is for the windows job."""
    path = _write(tmp_path, "inst.ps1", _render(shelf, "token+binary"))
    harness = _write(tmp_path, "tls.ps1", _TLS_HARNESS)
    p = _ps(shell, "-File", str(harness), "-ScriptPath", str(path))
    lines = [ln for ln in p.stdout.splitlines() if ln.startswith("TLS ")]
    assert lines == [
        "TLS start=192 prev=192 now=3264",     # Tls -> Tls + Tls12
        "TLS start=0 prev=0 now=0",            # SystemDefault untouched
        "TLS start=3072 prev=3072 now=3072",
        "TLS start=3264 prev=3264 now=3264",
    ], p.stdout + p.stderr


_PROGRESS_HARNESS = r"""
param([string]$Path)
$t = $null; $e = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Path, [ref]$t, [ref]$e)
$web = 'Invoke-WebRequest','iwr','Invoke-RestMethod','irm','curl','wget','Start-BitsTransfer'
$hits = @($ast.FindAll({ param($x) $x -is [System.Management.Automation.Language.CommandAst] }, $true) |
  ForEach-Object { $_.GetCommandName() } | Where-Object { $web -contains $_ })
Write-Output ("web_cmdlets=" + $hits.Count + " " + ($hits -join ','))
$pp = @($ast.FindAll({ param($x) $x -is [System.Management.Automation.Language.AssignmentStatementAst] -and $x.Left.Extent.Text -eq '$ProgressPreference' }, $true))
foreach ($a in $pp) {
  $scoped = $false; $p = $a.Parent
  while ($p) { if ($p -is [System.Management.Automation.Language.ScriptBlockExpressionAst]) { $scoped = $true }; $p = $p.Parent }
  Write-Output ("pp " + $a.Right.Extent.Text + " scoped=" + $scoped)
}
"""


@needs_pwsh
@pytest.mark.parametrize("which", ["installer", "uninstaller"])
def test_progress_rendering_is_off_inside_the_body_and_no_web_cmdlet_is_used(shelf, tmp_path, which):
    """Windows PowerShell 5.1 renders Invoke-WebRequest's progress bar per
    chunk, which slows a ~5 MB download many times over on a VDI or RDP console.
    The scripts fetch with [Net.HttpWebRequest] and set $ProgressPreference
    inside the `& { }` body, where it cannot leak into the operator's session
    (the iex test checks the caller still reads Continue). Read from the AST, so
    a command a message merely names does not count."""
    if which == "installer":
        text = _render(shelf, "token+binary")
    else:
        from api.agents import _WINDOWS_UNINSTALLER as text
    path = _write(tmp_path, "s.ps1", text)
    harness = _write(tmp_path, "pp.ps1", _PROGRESS_HARNESS)
    p = _ps(PWSH, "-File", str(harness), "-Path", str(path))
    lines = p.stdout.splitlines()
    assert "web_cmdlets=0 " in lines, p.stdout + p.stderr
    assert [ln for ln in lines if ln.startswith("pp ")] == ["pp 'SilentlyContinue' scoped=True"], p.stdout


def test_follow_up_commands_for_an_https_simcore_carry_the_tls_opt_in(shelf):
    """The script prints commands to paste later, into a fresh shell: the
    uninstall one-liner above all. On a 5.1 host with old defaults `iwr` cannot
    reach an https SimCore without the opt-in, so the command it prints must
    carry it. For plain http it is left out."""
    prefix = ("[Net.ServicePointManager]::SecurityProtocol = "
              "[Net.ServicePointManager]::SecurityProtocol -bor 3072; ")
    script = _render(shelf, "token+binary", server="https://simcore.corp.local")
    assert ("$IwrPrefix = '" + prefix + "'") in script
    printed = [ln for ln in script.splitlines()
               if "uninstall=1" in ln and "iex" in ln and not ln.lstrip().startswith("#")]
    assert len(printed) >= 3, printed     # final line, INSTALL_DIR_UNTRUSTED fix, service description
    for ln in printed:
        assert "$IwrPrefix" in ln or "$($IwrPrefix)" in ln, f"no TLS opt-in on: {ln.strip()}"
    assert "if ($Server -like 'https://*')" in script


@needs_powershell
@pytest.mark.parametrize("shell", SHELLS or ["none"])
def test_a_failed_install_run_as_a_file_still_exits_non_zero(shelf, tmp_path, shell):
    """The other half of the contract: `powershell -File install.ps1` must still
    hand a failure to whatever automation ran it."""
    script = _render(shelf, "token+no-binary", server=f"http://127.0.0.1:{_free_port()}")
    path = _write(tmp_path, "inst.ps1", script)
    p = _ps(shell, "-File", str(path), env={"CORTEXSIM_TELEMETRY": "0"})
    assert p.returncode == 1, p.stdout + p.stderr
    assert "code=WINDOWS_AGENT_UNAVAILABLE" in p.stdout


# ---------------------------------------------------------------------------
# (c) Injection — every baked value is validated server-side
# ---------------------------------------------------------------------------


INJECTIONS = [
    ({"id": "x'; Write-Host PWNED; '"}, "BAD_AGENT_ID"),
    ({"id": 'x"; touch /tmp/pwned; "'}, "BAD_AGENT_ID"),
    ({"id": "a" * 65}, "BAD_AGENT_ID"),
    ({"id": "-leading-dash"}, "BAD_AGENT_ID"),
    ({"server": "http://x$(id)"}, "BAD_SERVER"),
    ({"server": "http://x'; Write-Host P; '"}, "BAD_SERVER"),
    ({"server": "http://x`whoami`"}, "BAD_SERVER"),
    ({"server": "http://user:pw@10.0.0.5:8888"}, "BAD_SERVER"),
    ({"server": "http://10.0.0.5:8888/?a=b"}, "BAD_SERVER"),
    ({"server": "http://10.0.0.5:8888/#frag"}, "BAD_SERVER"),
    ({"server": "file:///etc/passwd"}, "BAD_SERVER"),
    ({"server": "javascript:alert(1)"}, "BAD_SERVER"),
    ({"server": "http://"}, "BAD_SERVER"),
    ({"server": "http://[::1"}, "BAD_SERVER"),
    ({"server": "http://10.0.0.5:99999"}, "BAD_SERVER"),
    ({"token": "t'+(whoami)+'"}, "BAD_TOKEN"),
    ({"token": "cxs_short"}, "BAD_TOKEN"),
    ({"token": "cxs_" + "a" * 30 + "$(id)"}, "BAD_TOKEN"),
    ({"interval": "-5"}, "BAD_INTERVAL"),
    ({"interval": "0"}, "BAD_INTERVAL"),
    ({"interval": "3601"}, "BAD_INTERVAL"),
    ({"interval": "abc"}, "BAD_INTERVAL"),
    ({"interval": "10; rm -rf /"}, "BAD_INTERVAL"),
]


@pytest.mark.parametrize("os_name", ["windows", "linux"])
@pytest.mark.parametrize("params,code", INJECTIONS,
                         ids=[f"{c}-{i}" for i, (_, c) in enumerate(INJECTIONS)])
def test_injected_installer_parameters_are_a_structured_400(client, shelf, os_name, params, code):
    shelf(True)
    r = client.get("/api/agents/install", params={"os": os_name, **params})
    assert r.status_code == 400, f"{params} was served ({r.status_code}):\n{r.text[:400]}"
    detail = r.json()["detail"]
    assert {"error", "code", "detail"} <= set(detail)
    assert detail["code"] == code


@pytest.mark.parametrize("os_name", ["windows", "linux"])
def test_an_injected_host_header_never_reaches_the_script(client, shelf, os_name):
    """With no ``?server=`` the callback URL is derived from the request, so the
    Host header is a baked value too. Starlette 1.6 (the pinned version) already
    discards a syntactically invalid Host and falls back to the socket address;
    ``_resolve_server`` re-validates whatever it is handed (next test), so the
    guarantee does not rest on one framework's behaviour."""
    shelf(True)
    r = client.get("/api/agents/install", params={"os": os_name},
                   headers={"Host": "evil.example'; Write-Host PWNED; '"})
    if r.status_code == 400:
        assert r.json()["detail"]["code"] == "BAD_SERVER"
    else:
        assert r.status_code == 200
        assert "PWNED" not in r.text, "the Host header was baked into the script"


@pytest.mark.parametrize("base_url", [
    "http://evil.example'; Write-Host P; '/",
    "http://x$(id)/",
    "http://evil.example`whoami`/",
])
def test_a_hostile_derived_default_server_is_refused(base_url):
    """The validator behind the derived default, exercised directly with the
    request shape a different ASGI server or proxy could hand it."""
    from fastapi import HTTPException

    from api.agents import _resolve_server

    class _R:
        pass

    req = _R()
    req.base_url = base_url
    with pytest.raises(HTTPException) as exc:
        _resolve_server(req, None)
    assert exc.value.status_code == 400
    assert exc.value.detail["code"] == "BAD_SERVER"
    assert "Host header" in exc.value.detail["detail"]


@pytest.mark.parametrize("params", [
    {"server": "http://10.0.0.5:8888"},
    {"server": "https://simcore.corp.local"},
    {"server": "https://simcore.corp.local/cortexsim"},
    {"server": "http://[::1]:8888"},
    {"server": "http://10.0.0.5:8888/"},
    {"id": "jumpbox-01"},
    {"id": "WIN-01.lab_a"},
    {"id": "a" * 64},
    {"interval": "1"},
    {"interval": "3600"},
    {"token": TOKEN},
])
@pytest.mark.parametrize("os_name", ["windows", "linux"])
def test_legitimate_values_are_still_served(client, shelf, os_name, params, monkeypatch):
    shelf(True)
    # A DNS name is only bakeable when this deployment's Host guard admits it
    # (RG-GUARD-05, test_agents_install_host_guard.py); this test is about the
    # shape of the value, so the deployment admits the name.
    from config import settings
    monkeypatch.setattr(settings, "CORTEXSIM_ALLOWED_HOSTS", "simcore.corp.local", raising=False)
    # And a ?server= is only bakeable when it names this SimCore (dev's
    # SERVER_NOT_THIS_SIMCORE rule, test_agents_install_server_origin.py), so
    # the deployment lists these servers as its own URLs.
    monkeypatch.setattr(settings, "CORTEXSIM_PUBLIC_URLS",
                        "http://10.0.0.5:8888,https://simcore.corp.local,"
                        "https://simcore.corp.local/cortexsim,http://[::1]:8888")
    r = client.get("/api/agents/install", params={"os": os_name, **params})
    assert r.status_code == 200, r.text[:400]


def test_a_freshly_minted_token_passes_the_installer_validator(client, shelf):
    shelf(True)
    minted = client.post("/api/agents/enroll/tokens", json={}).json()["token"]
    for os_name in ("windows", "linux"):
        r = client.get("/api/agents/install", params={"os": os_name, "token": minted})
        assert r.status_code == 200, r.text[:400]
        assert minted in r.text


def test_the_token_shape_matches_what_the_mint_endpoint_produces():
    """The validator and the mint (``'cxs_' + secrets.token_urlsafe(32)``) must
    never drift apart, or every real token would be refused."""
    from api.agents import _TOKEN_RE

    for _ in range(500):
        assert _TOKEN_RE.fullmatch("cxs_" + secrets.token_urlsafe(32))


def test_the_renderer_refuses_values_that_bypassed_the_route(shelf):
    """Defence in depth: a future caller that skips the route's validation gets
    an exception, not an injectable script."""
    from api.agents import _windows_installer

    shelf(True)
    with pytest.raises(ValueError):
        _windows_installer("http://10.0.0.5:8888", "x'; Write-Host P; '", 10, None)
    with pytest.raises(ValueError):
        _windows_installer("http://x$(id)", "win-01", 10, None)


# ---------------------------------------------------------------------------
# (d) Windows modes — no silent ignoring
# ---------------------------------------------------------------------------


def test_windows_has_no_foreground_mode_and_says_so(client, shelf):
    shelf(True)
    r = client.get("/api/agents/install", params={"os": "windows", "mode": "foreground"})
    assert r.status_code == 400, "mode=foreground on windows used to be silently ignored"
    detail = r.json()["detail"]
    assert detail["code"] == "BAD_MODE"
    assert "CORTEXSIM_ALLOW_UNSUPERVISED" in detail["detail"]


def test_windows_installer_is_served_as_a_ps1(client, shelf):
    shelf(True)
    r = client.get("/api/agents/install", params={"os": "windows", "token": TOKEN})
    assert r.status_code == 200
    assert 'filename="install-cortexsim-agent.ps1"' in r.headers["content-disposition"]
    assert not r.text.startswith("#!")


# ---------------------------------------------------------------------------
# (e) Static structure of the rendered script
# ---------------------------------------------------------------------------


def _code_lines(script: str) -> list[tuple[int, str]]:
    """(line_no, text) for every line that is not a pure comment."""
    return [(i, ln) for i, ln in enumerate(script.splitlines(), 1)
            if ln.strip() and not ln.lstrip().startswith("#")]


# A statement that RUNS sc.exe / icacls (bare, via `&`, or through Cs-Exec),
# as opposed to a message or remediation string that merely names one.
_NATIVE_CALL = re.compile(
    r"""^(?:\$\w+\s*=\s*)?(?:Cs-Exec\s+|&\s*)?['"]?(?:sc|icacls)(?:\.exe)?['"]?\s""", re.I)


def _assert_native_calls_checked(script: str, minimum: int) -> None:
    lines = _code_lines(script)
    calls = 0
    for idx, (no, text) in enumerate(lines):
        stripped = text.strip()
        if not _NATIVE_CALL.match(stripped):
            continue
        calls += 1
        follow = lines[idx + 1][1] if idx + 1 < len(lines) else ""
        assert "$LASTEXITCODE" in follow, (
            f"line {no} runs a native tool without checking $LASTEXITCODE next:\n"
            f"  {stripped}\n  next: {follow.strip()}")
        assert "| Out-Null" not in stripped, f"line {no} discards the native tool's output: {stripped}"
        # Through the helper: 5.1 under ErrorActionPreference=Stop turns a
        # redirected native stderr line into a terminating error.
        assert stripped.split("=", 1)[-1].strip().startswith("Cs-Exec "), (
            f"line {no} calls a native tool directly instead of through Cs-Exec: {stripped}")
    assert calls >= minimum, f"expected at least {minimum} sc.exe / icacls calls, found {calls}"


def test_every_native_call_is_followed_by_an_exit_code_check(shelf):
    """A native tool never throws. ``sc.exe create ... | Out-Null`` with no
    exit-code check is how a failed service creation used to read as an install."""
    _assert_native_calls_checked(_render(shelf, "token+binary"), minimum=5)


def test_sc_create_is_not_used_for_the_binary_path(shelf):
    """``sc.exe create binPath= "`"$Bin`" $Args"`` is mangled by 5.1's native
    argument quoting; ``New-Service -BinaryPathName`` is a cmdlet and is not."""
    script = _render(shelf, "token+binary")
    code = "\n".join(t for _, t in _code_lines(script))
    assert not re.search(r"(?i)\bsc(\.exe)?['\"]?\s+(@\(\s*)?['\"]?create\b", code), \
        "the service is still created with sc.exe"
    assert "New-Service" in code and "-BinaryPathName" in code


def test_ok_is_reported_only_after_running_is_confirmed_and_the_beacon_checked_in(shelf):
    script = _render(shelf, "token+binary")
    ok = [m.start() for m in re.finditer(r"Cs-Report 'install' 'OK'", script)]
    assert len(ok) == 1, f"exactly one OK report expected, found {len(ok)}"
    running = script.find("WaitForStatus('Running'")
    recheck = script.find("SERVICE_START_FAILED", running)
    sleep = script.find("Start-Sleep -Seconds 35")
    checkin = script.find("AGENT_NEVER_CHECKED_IN")
    assert -1 not in (running, recheck, sleep, checkin), (running, recheck, sleep, checkin)
    assert running < sleep < ok[0], "OK must follow the post-start SCM window"
    assert recheck < ok[0] and checkin < ok[0], "OK must follow the Running re-check and the check-in proof"
    degraded = script.find("Cs-Report 'install' 'DEGRADED_NO_SUPERVISOR'")
    assert checkin < degraded < ok[0], "the unelevated branch reports DEGRADED_NO_SUPERVISOR, never OK"


def test_elevation_is_decided_before_anything_is_enrolled(shelf):
    """An unelevated run used to enrol first and discover it could not install a
    service afterwards, leaving a phantom agent row."""
    script = _render(shelf, "token+binary")
    # Call sites, not definitions: helper functions are defined above the body
    # (PowerShell needs that), so the enrol URL appears early in the text.
    enroll_call = script.find("= Cs-Enroll")
    is_admin = script.find("IsInRole(")
    not_elevated = script.find("'NOT_ELEVATED'")
    assert -1 not in (is_admin, not_elevated, enroll_call), (is_admin, not_elevated, enroll_call)
    assert is_admin < not_elevated < enroll_call
    assert "CORTEXSIM_ALLOW_UNSUPERVISED" in script
    assert "dies at logoff and at reboot" in script


def test_the_body_is_scoped_and_never_calls_a_bare_exit(shelf):
    script = _render(shelf, "token+binary")
    code = "\n".join(t for _, t in _code_lines(script))
    assert code.lstrip().startswith("& {"), "the whole body must run inside & { ... }"
    exits = [ln.strip() for ln in code.splitlines() if re.search(r"\bexit\b", ln)]
    assert exits == ["if ($PSCommandPath) { exit 1 } else { return }"], exits
    assert 'throw "CORTEXSIM_FAILED:$Code"' in script


def test_tls12_is_enabled_for_5_1(shelf):
    """5.1 on .NET 4.x defaults to TLS 1.0/1.1, which an https SimCore refuses.
    The opt-in is the first thing the body does and the last thing it undoes;
    what it does to each starting value is executed in
    test_tls12_is_added_to_a_legacy_list_and_system_default_is_left_alone."""
    script = _render(shelf, "token+binary")
    body = script[script.index("\ntry {\n") + len("\ntry {\n"):]
    first = next(ln.strip() for ln in body.splitlines()
                 if ln.strip() and not ln.strip().startswith("#"))
    assert first == "try { $Cs.Tls = Cs-Tls12 } catch { }", first
    final = body[body.rindex("} finally {"):]
    assert "[Net.ServicePointManager]::SecurityProtocol = $Cs.Tls" in final, final


def test_every_step_has_its_own_failure_code(shelf):
    script = _render(shelf, "token+binary")
    for code in ("SERVER_UNREACHABLE", "ENROLL_DENIED", "ENROLL_FAILED", "NO_AGENT_ID",
                 "BINARY_UNAVAILABLE", "BINARY_FETCH_FAILED", "DIGEST_FETCH_FAILED",
                 "CHECKSUM_MISMATCH", "CHECKSUM_SKIPPED", "BINARY_QUARANTINED",
                 "BIN_INSTALL_FAILED", "PROCESS_START_BLOCKED", "INSTALL_DIR_UNTRUSTED",
                 "SERVICE_PENDING_DELETE", "SERVICE_START_FAILED", "AGENT_NEVER_CHECKED_IN",
                 "NOT_ELEVATED", "DEGRADED_NO_SUPERVISOR"):
        assert f"'{code}'" in script, f"{code} is never raised"
    # Quarantine is checked where AV acts: after the download AND after the copy.
    assert script.count("Cs-AssertPresent ") >= 3  # definition-free call sites: download, pre-staged, copy
    assert "check Cortex XDR/Defender alerts for cortexsim-agent.exe" in script


CORRUPTION_ONLY = "catches corruption, not tampering"


def _posix(token: str | None = TOKEN) -> str:
    from api.agents import _posix_installer

    return _posix_installer("http://10.0.0.5:8888", "jumpbox-01", 10, token, "service")


@pytest.mark.parametrize("which", ["windows", "posix"])
def test_the_checksum_is_described_as_what_it_is(shelf, which):
    """The binary and its digest come from the same SimCore over the same
    channel (plain HTTP by default), so whoever can alter one can alter both.
    The check proves the transfer was not corrupted. The scripts told the DC to
    "look for an intercepting TLS proxy" on a mismatch and printed a bare
    "checksum OK" on a match, which reads as authenticity for a binary that
    then runs as SYSTEM or root."""
    script = _render(shelf, "token+binary") if which == "windows" else _posix()
    assert "intercepting TLS proxy" not in script
    ok_lines = [ln for ln in script.splitlines() if "checksum OK" in ln]
    assert ok_lines, "no checksum OK line found"
    for ln in ok_lines:
        assert CORRUPTION_ONLY in ln, f"a match is reported without saying what it proves: {ln.strip()}"
    mismatch = [ln for ln in script.splitlines()
                if "CHECKSUM_MISMATCH" in ln and "CORTEXSIM_BIN" not in ln]
    assert mismatch
    fix = "\n".join(script.splitlines()[script.splitlines().index(mismatch[0]):][:3])
    assert CORRUPTION_ONLY in fix and "do NOT run this binary" in fix, fix


@pytest.mark.parametrize("which", ["windows", "posix"])
def test_no_script_tells_the_dc_to_put_the_token_in_the_url(shelf, which):
    """The served script's own header recommended `...install?os=windows&token=<tok>`,
    against the console and the docs: a token in the URL lands in proxy logs and
    shell history. `?token=` still WORKS; the scripts just stop advising it."""
    script = _render(shelf, "no-token+binary") if which == "windows" else _posix(None)
    assert "token=<tok>" not in script and "?token=" not in script and "&token=" not in script
    usage = "\n".join(script.splitlines()[:12])
    assert "CORTEXSIM_TOKEN='<tok>'" in usage, usage


def test_the_windows_header_names_its_differences_from_the_posix_installer(shelf):
    """It said "Same contract as the POSIX installer". It is not: no foreground
    mode, no source build, no install-prefix or log overrides."""
    script = _render(shelf, "token+binary")
    header = script[:script.index("& {")]
    assert "Same contract as the POSIX installer" not in header
    for difference in ("foreground", "CORTEXSIM_SRC", "CORTEXSIM_BIN_DIR", "CORTEXSIM_LOG"):
        assert difference in header, f"the header does not say {difference} is POSIX-only"


def test_install_dir_is_locked_down_by_sid_not_by_localised_name(shelf):
    script = _render(shelf, "token+binary")
    assert "'/inheritance:r'" in script and "'/grant:r'" in script
    for ace in ("'*S-1-5-18:(OI)(CI)F'", "'*S-1-5-32-544:(OI)(CI)F'", "'*S-1-5-32-545:(OI)(CI)RX'"):
        assert ace in script
    assert "'/setowner', '*S-1-5-32-544', '/T'" in script
    assert "GetOwner([Security.Principal.SecurityIdentifier])" in script


def test_a_fresh_install_dir_is_created_with_its_final_dacl(shelf):
    """New-Item gave the directory ProgramData's inherited ACL (Users may
    create files, CREATOR OWNER gets full control) until icacls ran, so a local
    non-admin racing the installer could plant cortexsim-agent.exe with their
    own ACE, which Copy-Item keeps: a SYSTEM binary a non-admin can rewrite.
    Static only -- the DACL itself is proven by the windows-native CI job."""
    script = _render(shelf, "token+binary")
    start = script.find("# 2. A directory no other account controls")
    end = script.find("# 3. The binary", start)
    assert start != -1 and end != -1
    block = script[start:end]
    assert "New-Item -ItemType Directory -Path $InstallDir" not in block
    assert "SetAccessRuleProtection($true, $false)" in block
    assert "[IO.Directory]::CreateDirectory($InstallDir, $sec)" in block
    assert "[IO.FileSystemAclExtensions]::Create(" in block, "PowerShell 7 (.NET Core) path"
    assert "SetOwner(" in block and "'S-1-5-32-544'" in block
    # A directory that appeared between Test-Path and CreateDirectory is not ours.
    assert block.count("GetOwner([Security.Principal.SecurityIdentifier])") >= 2
    assert block.find("CreateDirectory($InstallDir") < block.rfind("GetOwner(")


def test_existing_service_stop_waits_past_the_beacon_stop_deadline(shelf):
    """The beacon's SCM handler may hold STOP_PENDING for its 60 s stop
    deadline (agent/service_state.go svcStopDeadline); a 30 s wait failed a
    re-install over an in-flight run with SERVICE_STOP_FAILED."""
    script = _render(shelf, "token+binary")
    assert "WaitForStatus('Stopped', '00:01:30')" in script
    assert "WaitForStatus('Stopped', '00:00:30')" not in script


def test_unelevated_install_never_touches_programdata(shelf):
    script = _render(shelf, "token+binary")
    start = script.find("$InstallDir = \"$env:LOCALAPPDATA")
    assert start != -1, "the unelevated branch installs under %LOCALAPPDATA%"
    end = script.find("# -- stage: verify", start)
    assert end != -1
    unelevated = script[start:end]
    assert "ProgramData" not in unelevated
    assert "RedirectStandardOutput" in unelevated and "RedirectStandardError" in unelevated
    assert "cortexsim-agent.log" in unelevated


def test_pre_staged_binary_and_proxy_bypass_are_honoured(shelf):
    script = _render(shelf, "token+binary")
    assert "$env:CORTEXSIM_BIN" in script
    assert "CORTEXSIM_NO_PROXY" in script and "New-Object Net.WebProxy" in script


def test_the_service_log_path_is_printed_not_passed_as_a_flag(shelf):
    script = _render(shelf, "token+binary")
    assert "CortexSim\\agent.log" in script
    assert "--log-file" not in script


def test_loopback_hint_is_carried_as_a_safely_quoted_powershell_string(client_at_loopback):
    """The hint text contains single quotes; inside a single-quoted PowerShell
    literal each must be doubled or it ends the string early."""
    r = client_at_loopback.get("/api/agents/install", params={"os": "windows"})
    assert r.status_code == 200
    line = next(ln for ln in r.text.splitlines() if ln.strip().startswith("$LoopbackHint"))
    body = line.split("=", 1)[1].strip()
    assert body.startswith("'") and body.endswith("'"), line
    inner = body[1:-1]
    assert "'" not in inner.replace("''", ""), f"an undoubled quote ends the literal early: {line}"
    assert "re-fetch it from the control plane" in inner
    assert "os=windows" in inner, "the Windows hint must point at the Windows installer URL"


@needs_powershell
@pytest.mark.parametrize("shell", SHELLS or ["none"])
def test_the_loopback_variant_parses_too(client_at_loopback, tmp_path, shell):
    text = client_at_loopback.get("/api/agents/install", params={"os": "windows"}).text
    text.encode("ascii")
    path = _write(tmp_path, "inst.ps1", text)
    harness = _write(tmp_path, "parse.ps1", _PARSE_HARNESS)
    for cp in ("65001", "1252"):
        p = _ps(shell, "-File", str(harness), "-Path", str(path), "-CodePage", cp)
        assert "errors=0" in p.stdout, p.stdout + p.stderr


@pytest.fixture
def client_at_loopback(memory_db, shelf):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.agents import router
    from database import get_db

    shelf(True)
    get_db_override, _ = memory_db
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_db] = get_db_override
    return TestClient(app, base_url="http://127.0.0.1:8888")


# ---------------------------------------------------------------------------
# Behaviour of the HTTP error mapping, executed under pwsh against a stub
# ---------------------------------------------------------------------------
#
# The installer's functions are lifted out of the rendered script by the
# PowerShell AST (not by string slicing) and run against a loopback stub, so the
# mapping from "what SimCore answered" to "which code the DC reads" is executed,
# not asserted about text. pwsh 7 and 5.1 share [Net.HttpWebRequest]'s
# WebException shape, which is why the installer uses it; that sharing is the
# claim the windows CI job re-proves on 5.1.


class _Stub(http.server.BaseHTTPRequestHandler):
    """Route by first path segment: http://127.0.0.1:<port>/<case>/api/..."""

    def log_message(self, *a):
        pass

    def _send(self, code: int, body: bytes, ctype: str = "application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _case(self) -> tuple[str, str]:
        parts = self.path.split("/", 2)
        return parts[1], "/" + (parts[2] if len(parts) > 2 else "")

    def do_POST(self):
        case, rest = self._case()
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if rest.startswith("/api/agents/enroll"):
            if case == "deny":
                return self._send(403, json.dumps({"detail": {
                    "error": "Invalid or expired enrollment token", "code": "ENROLL_DENIED",
                    "detail": "mint a fresh token via POST /api/agents/enroll/tokens"}}).encode())
            if case == "boom":
                return self._send(500, json.dumps({"detail": {
                    "error": "db locked", "code": "DB_BUSY", "detail": "sqlite is locked"}}).encode())
            if case == "noid":
                return self._send(200, b'{"status":"enrolled"}')
            return self._send(200, b'{"status":"enrolled","agent_id":"win-01-a1b2c3","remaining_uses":0}')
        return self._send(200, b"{}")

    def do_GET(self):
        case, rest = self._case()
        if rest.startswith("/api/agents/binary/sha256"):
            if case == "digest500":
                return self._send(500, b'{"detail":{"error":"x","code":"X","detail":"y"}}')
            if case == "digestjunk":
                return self._send(200, b"<html>captive portal</html>", "text/html")
            return self._send(200, (("ab" * 32) + "\n").encode(), "text/plain")
        if rest.startswith("/api/agents/binary"):
            if case == "bin404":
                return self._send(404, json.dumps({"detail": {
                    "error": "No prebuilt beacon", "code": "AGENT_BINARY_UNAVAILABLE",
                    "detail": "windows/amd64 is not in /app/agent-dist"}}).encode())
            if case == "bin502":
                return self._send(502, b"bad gateway", "text/plain")
            return self._send(200, b"MZ-fake-beacon", "application/octet-stream")
        return self._send(404, b"{}")


@pytest.fixture
def stub():
    port = _free_port()
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), _Stub)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()


_MAPPING_HARNESS = r"""
param([string]$ScriptPath, [string]$Base, [string]$Closed, [string]$Work)
$t = $null; $e = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($ScriptPath, [ref]$t, [ref]$e)
foreach ($f in $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true)) {
  . ([scriptblock]::Create($f.Extent.Text))
}
$env:CORTEXSIM_TELEMETRY = '0'
$ErrorActionPreference = 'Stop'
$Token = 'cxs_' + ('a' * 43); $HostN = 'WIN-01'; $Arch = 'amd64'; $AgentId = 'win-01'
$LoopbackHint = ' LOOPBACK-HINT'
function Run($Name, [scriptblock]$Body) {
  $script:Cs = @{ Stage = 'test'; Code = ''; What = ''; Fix = ''; Work = $null }
  try { $r = & $Body; Write-Output ("RESULT " + $Name + " ok " + $r) }
  catch { Write-Output ("RESULT " + $Name + " " + $_.Exception.Message + " || " + $Cs.What + " || " + $Cs.Fix) }
}
$Server = "$Base/ok";     Run 'enroll-ok'     { Cs-Enroll }
$Server = "$Base/deny";   Run 'enroll-deny'   { Cs-Enroll }
$Server = "$Base/boom";   Run 'enroll-boom'   { Cs-Enroll }
$Server = "$Base/noid";   Run 'enroll-noid'   { Cs-Enroll }
$Server = $Closed;        Run 'enroll-closed' { Cs-Enroll }
$Server = "$Base/bin404"; Run 'bin-404'       { Cs-FetchBinary (Join-Path $Work 'a.exe') }
$Server = "$Base/bin502"; Run 'bin-502'       { Cs-FetchBinary (Join-Path $Work 'b.exe') }
$Server = $Closed;        Run 'bin-closed'    { Cs-FetchBinary (Join-Path $Work 'c.exe') }
$Server = "$Base/ok";     Run 'bin-ok'        { Cs-FetchBinary (Join-Path $Work 'd.exe'); [IO.File]::ReadAllText((Join-Path $Work 'd.exe')) }
$Server = "$Base/ok";       Run 'digest-ok'     { Cs-FetchDigest }
$Server = "$Base/digest500";  Run 'digest-500'  { Cs-FetchDigest }
$Server = "$Base/digestjunk"; Run 'digest-junk' { Cs-FetchDigest }
$Server = $Closed;            Run 'digest-closed' { Cs-FetchDigest }
"""


@needs_pwsh
def test_http_answers_map_to_the_code_that_names_the_real_problem(shelf, tmp_path, stub):
    """The old installer called every enrollment failure ENROLL_DENIED (a dead
    SimCore told the DC to mint a new token) and every download failure
    BINARY_FETCH_FAILED (a 404 shelf told them nothing)."""
    script = _render(shelf, "token+binary")
    path = _write(tmp_path, "inst.ps1", script)
    harness = _write(tmp_path, "mapping.ps1", _MAPPING_HARNESS)
    work = tmp_path / "work"
    work.mkdir()
    closed = f"http://127.0.0.1:{_free_port()}"
    p = _ps(PWSH, "-File", str(harness), "-ScriptPath", str(path), "-Base", stub,
            "-Closed", closed, "-Work", str(work))
    results = {}
    for ln in p.stdout.splitlines():
        if ln.startswith("RESULT "):
            _, name, rest = ln.split(" ", 2)
            results[name] = rest
    assert len(results) == 13, p.stdout + p.stderr

    assert results["enroll-ok"] == "ok win-01-a1b2c3"
    assert results["enroll-deny"].startswith("CORTEXSIM_FAILED:ENROLL_DENIED")
    assert "mint a fresh token" in results["enroll-deny"]
    # A 500 is not a token problem; the server's own structured detail is carried.
    assert results["enroll-boom"].startswith("CORTEXSIM_FAILED:ENROLL_FAILED")
    assert "HTTP 500" in results["enroll-boom"] and "DB_BUSY" in results["enroll-boom"]
    assert results["enroll-noid"].startswith("CORTEXSIM_FAILED:NO_AGENT_ID")
    assert results["enroll-closed"].startswith("CORTEXSIM_FAILED:SERVER_UNREACHABLE")
    assert "LOOPBACK-HINT" in results["enroll-closed"]

    assert results["bin-404"].startswith("CORTEXSIM_FAILED:BINARY_UNAVAILABLE")
    assert "AGENT_BINARY_UNAVAILABLE" in results["bin-404"]
    assert results["bin-502"].startswith("CORTEXSIM_FAILED:BINARY_FETCH_FAILED")
    assert "HTTP 502" in results["bin-502"]
    assert results["bin-closed"].startswith("CORTEXSIM_FAILED:SERVER_UNREACHABLE")
    assert results["bin-ok"].endswith("MZ-fake-beacon")

    assert results["digest-ok"] == "ok " + "ab" * 32
    assert results["digest-500"].startswith("CORTEXSIM_FAILED:DIGEST_FETCH_FAILED")
    # An unrecognised digest shape raises; it is never compared as if it were one.
    assert results["digest-junk"].startswith("CORTEXSIM_FAILED:DIGEST_FETCH_FAILED")
    assert results["digest-closed"].startswith("CORTEXSIM_FAILED:DIGEST_FETCH_FAILED")


_QUARANTINE_HARNESS = r"""
param([string]$ScriptPath, [string]$Work)
$t = $null; $e = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($ScriptPath, [ref]$t, [ref]$e)
foreach ($f in $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true)) {
  . ([scriptblock]::Create($f.Extent.Text))
}
$env:CORTEXSIM_TELEMETRY = '0'; $ErrorActionPreference = 'Stop'; $Server = 'http://127.0.0.1:9'
function Run($Name, [scriptblock]$Body) {
  $script:Cs = @{ Stage = 'test'; Code = ''; What = ''; Fix = ''; Work = $null }
  try { $r = & $Body; Write-Output ("RESULT " + $Name + " ok " + $r) }
  catch { Write-Output ("RESULT " + $Name + " " + $_.Exception.Message + " || " + $Cs.Fix) }
}
$gone = Join-Path $Work 'gone.exe'
$empty = Join-Path $Work 'empty.exe'; [IO.File]::WriteAllBytes($empty, [byte[]]@())
$good = Join-Path $Work 'good.exe'; [IO.File]::WriteAllBytes($good, [byte[]](0x4D, 0x5A))
Run 'gone'  { Cs-AssertPresent $gone 'download' }
Run 'empty' { Cs-AssertPresent $empty 'download' }
Run 'good'  { Cs-AssertPresent $good 'download' }
"""


@needs_pwsh
def test_a_binary_removed_by_endpoint_protection_is_named_as_such(shelf, tmp_path):
    """Defender / Cortex XDR remove or lock a fresh download between write and
    read. The old installer then failed inside Get-FileHash with a raw .NET
    error, or ran a 0-byte file."""
    import hashlib

    path = _write(tmp_path, "inst.ps1", _render(shelf, "token+binary"))
    harness = _write(tmp_path, "q.ps1", _QUARANTINE_HARNESS)
    work = tmp_path / "w"
    work.mkdir()
    p = _ps(PWSH, "-File", str(harness), "-ScriptPath", str(path), "-Work", str(work))
    res = {ln.split(" ", 2)[1]: ln.split(" ", 2)[2] for ln in p.stdout.splitlines() if ln.startswith("RESULT ")}
    assert len(res) == 3, p.stdout + p.stderr
    for name in ("gone", "empty"):
        assert res[name].startswith("CORTEXSIM_FAILED:BINARY_QUARANTINED"), res[name]
        assert "check Cortex XDR/Defender alerts for cortexsim-agent.exe" in res[name]
    assert res["good"] == "ok " + hashlib.sha256(b"MZ").hexdigest()


# ---------------------------------------------------------------------------
# The real script, end to end, under pwsh against a live SimCore
# ---------------------------------------------------------------------------
#
# Off Windows only $env:OS is faked (to 'Windows_NT') so the body runs past the
# OS check. Elevation cannot be faked -- WindowsIdentity throws here and the
# script reads that as "not elevated" -- so these exercise exactly the paths a
# non-admin Windows user takes: refusal before enrolling, and the opt-in
# per-user beacon. The SERVICE path (SCM, icacls, New-Service) runs only on a
# Windows host; nothing here claims it.

_POLLING_STUB = (
    "#!/bin/sh\n"
    "SERVER=\"\"; ID=\"\"\n"
    "while [ $# -gt 0 ]; do\n"
    "  case \"$1\" in --server) SERVER=\"$2\"; shift 2 ;; --id) ID=\"$2\"; shift 2 ;; *) shift ;; esac\n"
    "done\n"
    "echo \"beacon-started $SERVER $ID\"\n"
    "i=0\n"
    "while [ \"$i\" -lt 30 ]; do\n"
    "  curl -sS -m 3 \"$SERVER/api/agents/$ID/tasks\" >/dev/null 2>&1 || true\n"
    "  sleep 1; i=$((i + 1))\n"
    "done\n"
)


@pytest.fixture
def live_simcore(tmp_path):
    """The real /api/agents router over a real socket (uvicorn in a thread)."""
    import time
    import urllib.error
    import urllib.request

    import uvicorn
    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    dist = tmp_path / "agent-dist"
    dist.mkdir()
    prev = os.environ.get("CORTEXSIM_AGENT_DIST")
    os.environ["CORTEXSIM_AGENT_DIST"] = str(dist)

    from api.agents import _install_attempts, router
    from database import Base, get_db
    import models  # noqa: F401

    _install_attempts.clear()
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/win-e2e.db")
    SessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False)
    app = FastAPI()
    app.include_router(router, prefix="/api")

    async def _get_db():
        async with SessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = _get_db

    @app.on_event("startup")
    async def _tables():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"{base}/api/agents", timeout=1)
            break
        except urllib.error.HTTPError:
            break
        except Exception:
            time.sleep(0.1)
    try:
        yield base, dist
    finally:
        server.should_exit = True
        t.join(timeout=10)
        if prev is None:
            os.environ.pop("CORTEXSIM_AGENT_DIST", None)
        else:
            os.environ["CORTEXSIM_AGENT_DIST"] = prev


def _get_json(url: str):
    import urllib.request

    with urllib.request.urlopen(url, timeout=10) as r:
        return json.loads(r.read().decode())


def _get_text(url: str) -> str:
    import urllib.request

    with urllib.request.urlopen(url, timeout=10) as r:
        return r.read().decode()


def _mint(base: str) -> str:
    import urllib.request

    req = urllib.request.Request(f"{base}/api/agents/enroll/tokens", data=b"{}",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())["token"]


def _kill_reported_pid(output: str) -> None:
    import signal

    m = re.search(r"pid\s+:\s+(\d+)", output)
    if m:
        try:
            os.kill(int(m.group(1)), signal.SIGTERM)
        except OSError:
            pass


off_windows_pwsh = pytest.mark.skipif(PWSH is None or os.name == "nt",
                                      reason="drives the script under pwsh off Windows")


@off_windows_pwsh
def test_unelevated_without_opt_in_refuses_and_enrols_nothing(live_simcore, tmp_path):
    """INST-11, executed: the refusal comes BEFORE enrollment, so the console
    gains no phantom agent row and the attempt is visible in SimCore."""
    base, dist = live_simcore
    (dist / "cortexsim-agent-windows-amd64.exe").write_text(_POLLING_STUB)
    token = _mint(base)
    path = _write(tmp_path, "inst.ps1",
                  _get_text(f"{base}/api/agents/install?os=windows&token={token}&interval=1"))
    cmd = (f"Invoke-Expression (Get-Content -Raw -LiteralPath '{path}'); "
           "Write-Host CALLER-STILL-ALIVE")
    p = _ps(PWSH, "-Command", cmd, env={"OS": "Windows_NT"})
    out = p.stdout + p.stderr
    assert "code=NOT_ELEVATED" in out, out
    assert "Run as administrator" in out
    assert "CALLER-STILL-ALIVE" in p.stdout
    assert _get_json(f"{base}/api/agents")["agents"] == [], "an unelevated refusal enrolled an agent"
    attempts = _get_json(f"{base}/api/agents/install/attempts")["attempts"]
    assert attempts and attempts[0]["code"] == "NOT_ELEVATED"
    assert attempts[0]["os"] == "windows" and attempts[0]["stage"] == "detect"


@off_windows_pwsh
def test_a_failure_before_enrollment_is_not_reported_under_the_default_agent_id(live_simcore, tmp_path):
    """The route's default id is 'jumpbox-01' and the console's token URL carries
    no id, so every host that failed BEFORE enrolling reported itself as
    'jumpbox-01': unrelated hosts' NOT_ELEVATED / ENROLL_DENIED rows piled up
    under one agent id, a real one if a legacy agent has that name. With a token
    in effect the id is SimCore's to assign, so until it does there is none."""
    base, dist = live_simcore
    (dist / "cortexsim-agent-windows-amd64.exe").write_text(_POLLING_STUB)

    def _attempt(url: str, env: dict) -> dict:
        path = _write(tmp_path, "inst.ps1", _get_text(url))
        p = _ps(PWSH, "-File", str(path), env={"OS": "Windows_NT", **env})
        assert "code=NOT_ELEVATED" in p.stdout + p.stderr, p.stdout + p.stderr
        return _get_json(f"{base}/api/agents/install/attempts")["attempts"][0]

    token = _mint(base)
    in_url = _attempt(f"{base}/api/agents/install?os=windows&token={token}", {})
    assert in_url["code"] == "NOT_ELEVATED" and in_url["agent_id"] is None, in_url
    # The console's own flow: the token rides in the environment, not the URL.
    in_env = _attempt(f"{base}/api/agents/install?os=windows", {"CORTEXSIM_TOKEN": token})
    assert in_env["code"] == "NOT_ELEVATED" and in_env["agent_id"] is None, in_env
    # Negative control: a self-asserted id IS the id, and is still reported.
    legacy = _attempt(f"{base}/api/agents/install?os=windows&id=win-legacy", {})
    assert legacy["agent_id"] == "win-legacy", legacy


@off_windows_pwsh
def test_opted_in_unelevated_install_checks_in_and_is_reported_degraded_never_ok(live_simcore, tmp_path):
    """The whole per-user flow against a real SimCore: validate -> enrol ->
    pre-staged digest check -> copy -> start -> check-in proof -> telemetry.
    The final code is DEGRADED_NO_SUPERVISOR; OK is never reported for a beacon
    that dies at logoff."""
    base, dist = live_simcore
    (dist / "cortexsim-agent-windows-amd64.exe").write_text(_POLLING_STUB)
    staged = tmp_path / "staged-agent.exe"
    staged.write_text(_POLLING_STUB)
    staged.chmod(0o755)
    lad = tmp_path / "LocalAppData"
    lad.mkdir()
    token = _mint(base)
    path = _write(tmp_path, "inst.ps1",
                  _get_text(f"{base}/api/agents/install?os=windows&token={token}&interval=1"))
    p = _ps(PWSH, "-File", str(path), timeout=120,
            env={"OS": "Windows_NT", "CORTEXSIM_ALLOW_UNSUPERVISED": "1",
                 "CORTEXSIM_BIN": str(staged), "LOCALAPPDATA": str(lad)})
    out = p.stdout + p.stderr
    try:
        assert p.returncode == 0, out
        assert "checksum OK" in out and "matches SimCore" in out, out
        assert "DEGRADED" in out and "dies at logoff and at reboot" in out, out
        assert "done --" not in out, "an unsupervised beacon must not print the service success line"
        agents = _get_json(f"{base}/api/agents")["agents"]
        assert len(agents) == 1 and agents[0]["os"] == "windows"
        assert agents[0]["agent_id"] in out
        codes = [a["code"] for a in _get_json(f"{base}/api/agents/install/attempts")["attempts"]]
        assert codes[0] == "DEGRADED_NO_SUPERVISOR", codes
        assert "OK" not in codes, codes
        assert (lad / "CortexSim" / "cortexsim-agent.exe").exists()
        assert "beacon-started" in (lad / "CortexSim" / "cortexsim-agent.log").read_text()
    finally:
        _kill_reported_pid(out)


@off_windows_pwsh
def test_downloaded_beacon_is_verified_before_it_is_installed(live_simcore, tmp_path):
    """The download path: fetch -> AssertPresent -> digest -> compare -> copy.
    Off Windows the downloaded file is not executable (no +x), so the launch
    fails -- which also exercises PROCESS_START_BLOCKED's mapping. On Windows the
    same file would start; that run belongs to the windows CI job."""
    base, dist = live_simcore
    (dist / "cortexsim-agent-windows-amd64.exe").write_text(_POLLING_STUB)
    lad = tmp_path / "LocalAppData"
    lad.mkdir()
    token = _mint(base)
    path = _write(tmp_path, "inst.ps1",
                  _get_text(f"{base}/api/agents/install?os=windows&token={token}&interval=1"))
    p = _ps(PWSH, "-File", str(path), timeout=120,
            env={"OS": "Windows_NT", "CORTEXSIM_ALLOW_UNSUPERVISED": "1", "LOCALAPPDATA": str(lad)})
    out = p.stdout + p.stderr
    assert "fetching prebuilt beacon (windows/amd64)" in out, out
    assert "checksum OK" in out and CORRUPTION_ONLY in out, out
    assert (lad / "CortexSim" / "cortexsim-agent.exe").read_text() == _POLLING_STUB
    assert p.returncode == 1 and "code=PROCESS_START_BLOCKED" in out, out
    codes = [a["code"] for a in _get_json(f"{base}/api/agents/install/attempts")["attempts"]]
    assert codes[0] == "PROCESS_START_BLOCKED", codes


@off_windows_pwsh
def test_a_download_that_does_not_match_its_digest_is_never_installed(live_simcore, tmp_path):
    """Integrity, executed: SimCore's digest no longer matches the bytes served.
    That is all the check can see. Someone able to rewrite the binary can
    rewrite the digest too, so the script must not call a match authentic."""
    import hashlib

    base, dist = live_simcore
    exe = dist / "cortexsim-agent-windows-amd64.exe"
    exe.write_text(_POLLING_STUB)
    from api import agents as agents_mod

    # Poison the digest cache for the served file, as an intercepting proxy
    # rewriting the body would look from the target's side.
    st = exe.stat()
    agents_mod._sha_cache[(str(exe), st.st_mtime_ns, st.st_size)] = hashlib.sha256(b"other").hexdigest()
    lad = tmp_path / "LocalAppData"
    lad.mkdir()
    path = _write(tmp_path, "inst.ps1",
                  _get_text(f"{base}/api/agents/install?os=windows&id=win-tamper&interval=1"))
    p = _ps(PWSH, "-File", str(path), timeout=120,
            env={"OS": "Windows_NT", "CORTEXSIM_ALLOW_UNSUPERVISED": "1", "LOCALAPPDATA": str(lad)})
    out = p.stdout + p.stderr
    assert p.returncode == 1 and "code=CHECKSUM_MISMATCH" in out, out
    assert "do NOT run this binary" in out
    assert CORRUPTION_ONLY in out and "intercepting TLS proxy" not in out, out
    assert not (lad / "CortexSim" / "cortexsim-agent.exe").exists(), "a mismatched binary was installed"


# ---------------------------------------------------------------------------
# Windows-host execution (the windows CI job) — skipped elsewhere
# ---------------------------------------------------------------------------


@windows_only
def test_on_windows_an_unelevated_or_beaconless_run_fails_before_enrolling(shelf, tmp_path):
    """Executed by Windows PowerShell 5.1 on a real Windows host: the preflight
    fails, the caller survives, and nothing was installed."""
    ps51 = shutil.which("powershell")
    assert ps51, "Windows PowerShell 5.1 must exist on a Windows host"
    script = _render(shelf, "token+no-binary", server=f"http://127.0.0.1:{_free_port()}")
    path = _write(tmp_path, "inst.ps1", script)
    p = _ps(ps51, "-Command", _iex_caller(path))
    assert "code=WINDOWS_AGENT_UNAVAILABLE" in p.stdout, p.stdout + p.stderr
    assert "CALLER-STILL-ALIVE" in p.stdout
    q = subprocess.run(["sc.exe", "query", "CortexSimAgent"], capture_output=True, text=True)
    assert "1060" in q.stdout or q.returncode == 1060, "a failed preflight must not create the service"

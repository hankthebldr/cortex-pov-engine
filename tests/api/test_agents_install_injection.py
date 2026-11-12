"""GET /api/agents/install must not turn its query string into code on the target.

The installer is meant to be piped straight into an interpreter
(``curl … | bash``, ``iwr … | iex``). ``id``, ``server`` and ``token`` were
substituted raw into it: inside double quotes in bash
(``AGENT_ID="${CORTEXSIM_AGENT_ID:-@@AGENT_ID@@}"``) and single quotes in
PowerShell, so ``?id=$(touch x)`` ran ``touch x`` on whatever host executed the
script. These tests run the variable-assignment prologue of the served script
in a real bash (and pwsh where installed), which is exactly what the target does.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def client(make_client):
    from api.agents import router
    return make_client(router)


def _clean_env() -> dict[str, str]:
    """The target's environment, with no CORTEXSIM_* override set."""
    return {k: v for k, v in os.environ.items() if not k.startswith("CORTEXSIM_")}


def _bash_prologue(script: str, tmp_path: Path, env: dict[str, str] | None = None) -> list[str]:
    """Run the served bash script up to the end of its variable block and
    return SERVER / AGENT_ID / TOKEN as the target's shell resolved them."""
    lines = script.splitlines()
    end = next(i for i, line in enumerate(lines) if line.startswith("HOSTN="))
    prologue = "\n".join(lines[:end]) + '\nprintf "%s\\n" "$SERVER" "$AGENT_ID" "$TOKEN"\n'
    p = subprocess.run(["bash", "-c", prologue], capture_output=True, text=True,
                       env=env or _clean_env(), cwd=tmp_path, timeout=10)
    assert p.returncode == 0, p.stderr
    return p.stdout.splitlines()


def _pwsh_prologue(script: str, tmp_path: Path) -> list[str]:
    """Same, for the PowerShell installer: its block of $Server/$AgentId/$Token."""
    lines = script.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("$ErrorActionPreference"))
    end = next(i for i, line in enumerate(lines) if line.startswith("$Server   = $Server.TrimEnd"))
    ps1 = tmp_path / "prologue.ps1"
    ps1.write_text("\n".join(lines[start:end + 1])
                   + "\nWrite-Output $Server\nWrite-Output $AgentId\nWrite-Output $Token\n",
                   encoding="utf-8")
    p = subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", "-File", str(ps1)],
                       capture_output=True, text=True, env=_clean_env(), cwd=tmp_path,
                       timeout=60)
    assert p.returncode == 0, p.stderr
    return p.stdout.splitlines()


def _served(r) -> str:
    """What `curl -fsSL … | bash` would hand the interpreter: the body on 2xx,
    nothing on an error (curl -f writes nothing to the pipe)."""
    return r.text if r.status_code == 200 else ""


_BAD_CODE = {"id": "BAD_AGENT_ID", "token": "BAD_TOKEN", "server": "BAD_SERVER"}


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
@pytest.mark.parametrize("param,template", [
    ("id", "$(touch {m})"),
    ("token", "cxs_$(touch {m})"),
    ("server", "http://simcore.lab/$(touch {m})"),
    ("id", "box`touch {m}`"),
])
def test_query_params_never_execute_in_the_bash_installer(client, tmp_path, param, template):
    marker = tmp_path / "ran"
    r = client.get("/api/agents/install", params={"os": "linux", param: template.format(m=marker)})

    script = _served(r)
    if script:
        _bash_prologue(script, tmp_path)

    assert not marker.exists(), f"?{param}= executed on the target"
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert detail["code"] == _BAD_CODE[param]
    assert {"error", "code", "detail"} <= set(detail)


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="needs pwsh")
@pytest.mark.parametrize("param,template", [
    ("id", "box'; New-Item -ItemType File -Path '{m}' | Out-Null; '"),
    ("token", "cxs_'; New-Item -ItemType File -Path '{m}' | Out-Null; '"),
    ("server", "http://simcore.lab/'; New-Item -ItemType File -Path '{m}' | Out-Null; '"),
])
def test_query_params_never_execute_in_the_powershell_installer(client, tmp_path, param, template):
    marker = tmp_path / "ran"
    r = client.get("/api/agents/install", params={"os": "windows", param: template.format(m=marker)})

    script = _served(r)
    if script:
        _pwsh_prologue(script, tmp_path)

    assert not marker.exists(), f"?{param}= executed on the target"
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == _BAD_CODE[param]


@pytest.fixture
def public_urls(monkeypatch):
    """List servers as this SimCore's own (?server= must name this SimCore)."""
    import config

    def _set(value: str) -> None:
        monkeypatch.setattr(config.settings, "CORTEXSIM_PUBLIC_URLS", value)

    return _set


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_valid_values_reach_the_bash_installer_verbatim(client, tmp_path, public_urls):
    public_urls("https://simcore.corp.local:8443/cortexsim")
    token = "cxs_" + "A1-_" * 10 + "xyz"
    r = client.get("/api/agents/install", params={
        "os": "linux", "id": "jumpbox-01.lab", "token": token,
        "server": "https://simcore.corp.local:8443/cortexsim/",
    })
    assert r.status_code == 200, r.text
    assert _bash_prologue(r.text, tmp_path) == [
        "https://simcore.corp.local:8443/cortexsim", "jumpbox-01.lab", token,
    ]


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_env_overrides_still_win_on_the_target(client, tmp_path):
    r = client.get("/api/agents/install", params={"os": "linux", "id": "baked-id"})
    assert r.status_code == 200, r.text
    env = {**_clean_env(), "CORTEXSIM_SERVER": "http://override:8888/",
           "CORTEXSIM_AGENT_ID": "env-id", "CORTEXSIM_TOKEN": "cxs_env"}
    assert _bash_prologue(r.text, tmp_path, env=env) == ["http://override:8888", "env-id", "cxs_env"]


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="needs pwsh")
def test_valid_values_reach_the_powershell_installer_verbatim(client, tmp_path, public_urls):
    public_urls("http://[fd00::5]:8888")
    r = client.get("/api/agents/install", params={
        "os": "windows", "id": "win-box_01", "token": "cxs_tok-1",
        "server": "http://[fd00::5]:8888",
    })
    assert r.status_code == 200, r.text
    assert _pwsh_prologue(r.text, tmp_path) == ["http://[fd00::5]:8888", "win-box_01", "cxs_tok-1"]


@pytest.mark.parametrize("param,value", [
    ("id", "-rf"),                      # would read as a flag to the beacon
    ("id", "box 01"),                   # splits the systemd ExecStart line
    ("token", "not-a-token"),
    ("server", "ftp://simcore.lab"),
    ("server", "http://simcore.lab:8888/%h"),  # a systemd specifier in ExecStart
    ("server", "http://simcore.lab/<string>"),  # lands in the launchd plist
])
def test_values_outside_their_real_format_are_refused(client, param, value):
    r = client.get("/api/agents/install", params={"os": "linux", param: value})
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == _BAD_CODE[param]

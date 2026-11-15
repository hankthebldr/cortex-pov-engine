"""install.sh's success banner must not print a URL SimCore refuses (RG-GUARD-07).

The banner's first line was ``SimCore URL: http://$(hostname -f):<port>``.
install.sh starts SimCore with ``docker compose up`` and never sets
``CORTEXSIM_ALLOWED_HOSTS``; inside the container the hostname is the container
id. So unless ``.env`` happened to list this host's name, the first URL a
contributor was told to open answered ``400 HOST_NOT_ALLOWED`` as raw JSON.

The banner now prints the by-name URL only when the allowlist admits the name,
and otherwise says which name is refused and how to admit it.

install.sh ends in ``main "$@"``, so it cannot be sourced as it stands. These
tests source a copy with that last line removed and call the banner function
alone: nothing is installed and no docker runs. ``hostname`` is a stub.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO / "install.sh"

pytestmark = pytest.mark.skipif(
    os.name == "nt" or shutil.which("bash") is None,
    reason="install.sh is a bash script for POSIX hosts",
)

FQDN = "devbox07.corp.example"


@pytest.fixture
def sandbox(tmp_path):
    text = INSTALL_SH.read_text(encoding="utf-8")
    tail = 'main "$@"\n'
    assert text.endswith(tail), "install.sh no longer ends in `main \"$@\"`: update this harness"
    root = tmp_path / "checkout"
    root.mkdir()
    (root / "install-functions.sh").write_text(text[: -len(tail)], encoding="utf-8")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "hostname"
    stub.write_text(f'#!/bin/sh\ncase "$1" in -s) echo devbox07 ;; *) echo "{FQDN.upper()}" ;; esac\n')
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return root, bindir


def _banner(sandbox, env_text: str | None, shell_env: dict[str, str] | None = None) -> str:
    root, bindir = sandbox
    if env_text is not None:
        (root / ".env").write_text(env_text)
    env = {"PATH": f"{bindir}{os.pathsep}/usr/bin{os.pathsep}/bin", "HOME": str(root),
           "USER": "tester", "LANG": "C"}
    env.update(shell_env or {})
    proc = subprocess.run(
        ["bash", "-c", 'source "$1" && print_success_banner', "banner", str(root / "install-functions.sh")],
        cwd=root, env=env, capture_output=True, text=True, timeout=60,
    )
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "Installation complete" in out, out
    return out


def _named_urls(out: str) -> list[str]:
    """Lines that present http://<this host's name> as somewhere to go."""
    return [ln for ln in out.splitlines()
            if f"http://{FQDN}:" in ln.lower() and "refused" not in ln.lower()
            and "HOST_NOT_ALLOWED" not in ln]


@pytest.mark.parametrize("env_text", [
    None,                                                     # no .env at all
    "CORTEXSIM_SECRET=x\n",                                   # a pre-guard .env
    "CORTEXSIM_SECRET=x\nCORTEXSIM_ALLOWED_HOSTS=\n",         # cp .env.example .env
    "CORTEXSIM_SECRET=x\nCORTEXSIM_ALLOWED_HOSTS=other.corp.example\n",
])
def test_install_sh_banner_does_not_offer_a_name_simcore_refuses(sandbox, env_text):
    out = _banner(sandbox, env_text)
    assert not _named_urls(out), (
        f"the banner offers a URL that answers 400 HOST_NOT_ALLOWED:\n{out}"
    )
    assert "HOST_NOT_ALLOWED" in out and "CORTEXSIM_ALLOWED_HOSTS" in out, out
    assert FQDN in out.lower(), "the refused name is not shown, so it cannot be added"
    assert "http://localhost:8888" in out, "the URL that does work is gone"


@pytest.mark.parametrize("allowlist", [
    f"devbox07,{FQDN}",
    FQDN.upper(),
    "*.corp.example",
    "*",
])
def test_install_sh_banner_offers_the_name_when_the_allowlist_admits_it(sandbox, allowlist):
    out = _banner(sandbox, f"CORTEXSIM_SECRET=x\nCORTEXSIM_ALLOWED_HOSTS={allowlist}\n")
    assert _named_urls(out), out
    assert "HOST_NOT_ALLOWED" not in out, out


def test_install_sh_banner_reads_the_allowlist_from_the_shell_first(sandbox):
    """Compose takes a shell value over .env; so does the banner."""
    out = _banner(sandbox, "CORTEXSIM_SECRET=x\nCORTEXSIM_ALLOWED_HOSTS=\n",
                  shell_env={"CORTEXSIM_ALLOWED_HOSTS": FQDN})
    assert _named_urls(out), out
    refused = _banner(sandbox, f"CORTEXSIM_SECRET=x\nCORTEXSIM_ALLOWED_HOSTS={FQDN}\n",
                      shell_env={"CORTEXSIM_ALLOWED_HOSTS": ""})
    assert not _named_urls(refused) and "HOST_NOT_ALLOWED" in refused, refused

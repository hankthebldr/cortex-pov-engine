"""Tier A — static analysis of every scenario TTP script.

Phase 1 of the e2e execution methodology
(docs/design/e2e-execution-methodology.md).

Discovers every shell script under
``scenarios/*/packages/*/ttps/*.sh`` and every package-level
``scenarios/*/packages/*/run.sh`` and verifies:

  1. ``bash -n`` parses without error.
  2. ``shellcheck --severity=warning`` passes.

Each script gets its own parametrized test, so CI failures show
exactly which script failed and on what line. No central registration:
drop a new script in the right path and it's automatically covered.

This is a hard gate (no continue-on-error) — regressions here are real.
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess
from typing import List

import pytest

# ─── Script discovery ───────────────────────────────────────────────

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _discover_scripts() -> List[pathlib.Path]:
    """Return every TTP script and runner under scenarios/*/packages/."""
    pkg_root = REPO_ROOT / "scenarios"
    ttps = sorted(pkg_root.glob("*/packages/*/ttps/*.sh"))
    runners = sorted(pkg_root.glob("*/packages/*/run.sh"))
    return ttps + runners


SCRIPTS = _discover_scripts()


def _label(p: pathlib.Path) -> str:
    """Compact label for the pytest test id (no full repo prefix)."""
    try:
        rel = p.relative_to(REPO_ROOT)
    except ValueError:
        rel = p
    return str(rel)


# ─── Sanity: did discovery find anything? ───────────────────────────

def test_discovery_found_scripts():
    """The discovery glob should find at least one TTP and one runner.

    Guard against accidentally moving the scenario package tree without
    updating this test — silent zero-script discovery would let
    real regressions through unnoticed.
    """
    assert len(SCRIPTS) > 0, (
        "discovered zero shell scripts under scenarios/*/packages/ — "
        "either the package layout moved or the glob is wrong"
    )
    has_ttp = any("/ttps/" in str(p) for p in SCRIPTS)
    has_run = any(p.name == "run.sh" for p in SCRIPTS)
    assert has_ttp, "no ttps/*.sh discovered — check scenarios/*/packages/*/ttps/"
    assert has_run, "no run.sh discovered — check scenarios/*/packages/*/run.sh"


# ─── Parametrized tests ──────────────────────────────────────────────

@pytest.mark.parametrize("script", SCRIPTS, ids=_label)
def test_bash_parses(script: pathlib.Path):
    """Every script must parse as valid bash (``bash -n``).

    Catches: unbalanced quotes, missing fi/done/esac, sourcing the
    parser's bad mood. Cheap (~10ms per script).
    """
    result = subprocess.run(
        ["bash", "-n", str(script)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, (
        f"bash -n failed for {_label(script)}:\n{result.stderr.strip()}"
    )


@pytest.mark.skipif(
    shutil.which("shellcheck") is None,
    reason="shellcheck not installed (brew install shellcheck / "
           "apt-get install shellcheck)",
)
@pytest.mark.parametrize("script", SCRIPTS, ids=_label)
def test_shellcheck_passes(script: pathlib.Path):
    """Every script must pass shellcheck at severity=warning.

    Severity rationale:
      - ``error`` only is too lax — misses unquoted-var bugs
        (``rm -rf $dir`` when ``$dir`` is empty becomes ``rm -rf``)
      - ``warning`` catches the bugs without flagging stylistic
        preferences
      - ``info`` and ``style`` flag pedantic things that would force
        churn without buying safety

    Per-script false positives should be silenced with an inline
    directive:

        # shellcheck disable=SC2086  # word splitting intentional
        something $deliberately $unquoted

    Avoid file-level disables — be specific.
    """
    result = subprocess.run(
        [
            "shellcheck",
            "--severity=warning",
            "--shell=bash",
            "--format=tty",
            str(script),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"shellcheck failed for {_label(script)}:\n"
        f"{result.stdout.strip() or result.stderr.strip()}"
    )


# ─── Cross-cutting hygiene ───────────────────────────────────────────

@pytest.mark.parametrize("script", SCRIPTS, ids=_label)
def test_script_is_executable(script: pathlib.Path):
    """Every TTP and runner must have its executable bit set.

    The push-bundle generator chains scripts assuming they're +x; a
    non-executable script means ``./run.sh`` will fail at runtime with
    a confusing 'permission denied' rather than at boot.
    """
    import os
    assert os.access(script, os.X_OK), (
        f"{_label(script)} is not executable — chmod +x"
    )


@pytest.mark.parametrize("script", SCRIPTS, ids=_label)
def test_has_shebang(script: pathlib.Path):
    """Every script must start with a ``#!/usr/bin/env bash`` (or similar) shebang.

    Without it, ``./script.sh`` invokes the user's login shell — which
    might be zsh, fish, or dash, none of which interpret bashisms.
    The identity harness specifically wraps with bash; standalone
    invocations need the same guarantee.
    """
    with open(script, "rb") as f:
        first = f.readline()
    assert first.startswith(b"#!"), (
        f"{_label(script)} missing shebang on line 1"
    )
    assert b"bash" in first or b"sh" in first, (
        f"{_label(script)} shebang doesn't reference bash/sh: {first!r}"
    )

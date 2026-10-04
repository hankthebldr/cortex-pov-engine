"""Tests for scripts/check-adapter-sources.sh — the tier-2 source preflight.

The GAP-ADAPT-01 guard exists so a POV never runs with a tier-2 submodule tool
whose source tree is absent (the detection then never fires and reads in the POV
report as a Cortex miss). A ``git clone`` without ``--recursive`` still creates an
EMPTY directory at every gitlink path, so a presence check that only asks "is it
a directory?" passes an uninitialised submodule — exactly the case the pack file
says the gate must FAIL on.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check-adapter-sources.sh"


def _run(base: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ, CORTEXSIM_BASE_DIR=str(base))
    return subprocess.run(
        ["bash", str(SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
    )


def _write_tier2_pack(base: Path, source_path: str) -> None:
    packs = base / "tools" / "packs"
    packs.mkdir(parents=True, exist_ok=True)
    (packs / "test-tier2.yml").write_text(
        "adapter_id: TOOL-TEST-TIER2\n"
        "name: test-tier2\n"
        "tier: 2\n"
        "install:\n"
        f"  source_path: {source_path}\n",
        encoding="utf-8",
    )


def test_script_exists_and_is_executable():
    assert SCRIPT.exists(), f"{SCRIPT} missing"


def test_empty_tier2_submodule_dir_fails(tmp_path):
    """An EMPTY source_path dir (uninitialised submodule) must FAIL, not pass.

    Without the fix the tier-2 branch is `if [[ -d "$resolved" ]]`, which an
    empty gitlink directory satisfies, so the gate exits 0 and a POV runs with a
    tool that was never provisioned.
    """
    _write_tier2_pack(tmp_path, "sources/empty-sub")
    (tmp_path / "sources" / "empty-sub").mkdir(parents=True)  # exists, but EMPTY

    result = _run(tmp_path)
    assert result.returncode == 1, (
        "empty tier-2 submodule dir should FAIL the gate\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "TOOL-TEST-TIER2" in result.stdout + result.stderr


def test_populated_tier2_submodule_dir_passes(tmp_path):
    """A source_path dir with real content passes (guards against over-correction)."""
    _write_tier2_pack(tmp_path, "sources/real-sub")
    real = tmp_path / "sources" / "real-sub"
    real.mkdir(parents=True)
    (real / "Cargo.toml").write_text("[package]\nname = \"x\"\n", encoding="utf-8")

    result = _run(tmp_path)
    assert result.returncode == 0, (
        "populated tier-2 submodule dir should PASS the gate\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "PASS" in result.stdout

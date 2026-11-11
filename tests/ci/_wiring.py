"""Shared readers for the CI wiring guards in this directory.

A gate that exists but is not wired where the docs say it runs is a gate that
does not run. The guards read the real ``.github/workflows/*.yml`` and ask the
real Makefile (``make -n``) what it would execute, so a refactor that drops a
gate, lets one gate mask another, or re-floats an action tag turns red in the
test suite instead of on a customer-lab release.

Nothing here executes a gate. A green wiring test says the gate is in place; it
does not say the gate passed, and it says nothing about Windows or a tenant.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO / ".github" / "workflows"
CI_YML = WORKFLOWS / "ci.yml"
MAKEFILE = REPO / "Makefile"
BUILD_AGENT_DIST = REPO / "scripts" / "build-agent-dist.sh"

#: The one named opt-out for the PowerShell gate inside the make aggregates.
SKIP_VAR = "CORTEXSIM_SKIP_POWERSHELL_GATE"


def load_workflow(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def workflow_files() -> list[Path]:
    files = sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml"))
    assert files, f"no workflow files under {WORKFLOWS}"
    return files


def ci_jobs() -> dict:
    return load_workflow(CI_YML)["jobs"]


def step_index(job: dict, needle: str) -> int:
    """Index of the ONE step whose ``run``, ``name`` or ``uses`` contains needle."""
    hits = [
        i for i, s in enumerate(job.get("steps", []))
        if needle in (s.get("run") or "") or needle in (s.get("name") or "") or needle in str(s.get("uses") or "")
    ]
    assert len(hits) == 1, f"expected exactly one step matching {needle!r}, got {len(hits)}"
    return hits[0]


def condition(step: dict) -> str:
    return str(step.get("if", ""))


def require_make() -> str:
    make = shutil.which("make")
    if make is None:
        # A native GitHub runner always has make, so a skip there would read as
        # a pass. The prod image (the `backend` job) has none and does not
        # propagate CI, so it skips honestly.
        if os.environ.get("GITHUB_ACTIONS") == "true" or os.environ.get("CI") == "true":
            pytest.fail("no `make` on a CI runner -- the Makefile wiring guards cannot run")
        pytest.skip("make not installed")
    return make


def make_env() -> dict[str, str]:
    """The caller's environment without anything that would change what make runs."""
    return {k: v for k, v in os.environ.items() if k not in (SKIP_VAR, "MAKEFLAGS", "MFLAGS", "MAKELEVEL")}


def make_n(*args: str) -> str:
    """``make -n <args>`` on the real Makefile: what WOULD run, nothing executed."""
    proc = subprocess.run([require_make(), "-n", "-C", str(REPO), *args],
                          capture_output=True, text=True, timeout=120, env=make_env())
    assert proc.returncode == 0, f"make -n {' '.join(args)} failed:\n{proc.stdout}\n{proc.stderr}"
    return proc.stdout


def shipped_targets() -> set[tuple[str, str]]:
    """The (GOOS, GOARCH) pairs in scripts/build-agent-dist.sh TARGETS."""
    text = BUILD_AGENT_DIST.read_text(encoding="utf-8")
    m = re.search(r"^TARGETS=\((.*?)^\)", text, re.MULTILINE | re.DOTALL)
    assert m, f"no TARGETS=( ... ) array in {BUILD_AGENT_DIST}"
    found = {tuple(t.split("/")) for t in re.findall(r'"([a-z0-9]+/[a-z0-9]+)"', m.group(1))}
    assert found, "parsed zero targets -- the parser is stale, not the matrix"
    return found  # type: ignore[return-value]

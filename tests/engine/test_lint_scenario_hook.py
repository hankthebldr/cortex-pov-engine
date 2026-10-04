"""The .claude/hooks/lint-scenario.py edit hook must AGREE with the boot loader.

The hook's docstring promises it mirrors the loader's REJECT contract. Two
divergences made it block edits the loader accepts cleanly:

  * its plane set omitted ``DLP`` (which ``scenario_loader.VALID_PLANES`` has), so
    a loader-valid ``plane: DLP`` scenario was rejected with an ERROR; and
  * it treated a bare ``cgo_anchor`` (no step wiring ``causality``) as a declared
    spine and errored "more than one root step", while the loader's
    ``validate_causality_spine`` returns early for a zero-causality collection
    and ACCEPTS it — leaving the hook's own warning branch dead.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK = REPO_ROOT / ".claude" / "hooks" / "lint-scenario.py"
SAMPLE = REPO_ROOT / "scenarios" / "edr" / "edr-006-lsass-memory-dump.yml"


def _load_hook():
    spec = importlib.util.spec_from_file_location("lint_scenario_hook", HOOK)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_hook_planes_mirror_loader_valid_planes():
    """The hook's PLANES must equal the loader's VALID_PLANES exactly."""
    from engine.scenario_loader import VALID_PLANES  # noqa: PLC0415

    hook = _load_hook()
    assert hook.PLANES == set(VALID_PLANES), (
        "lint hook PLANES diverged from loader VALID_PLANES: "
        f"missing={set(VALID_PLANES) - hook.PLANES} extra={hook.PLANES - set(VALID_PLANES)}"
    )


def test_hook_accepts_cgo_anchor_without_step_causality(tmp_path):
    """cgo_anchor + >=2 steps, none wiring causality: loader ACCEPTS → hook must not error."""
    base = yaml.safe_load(SAMPLE.read_text(encoding="utf-8"))
    assert isinstance(base.get("cgo_anchor"), dict), "sample must carry a cgo_anchor"
    base["steps"] = base["steps"][:2]
    for step in base["steps"]:
        step.pop("causality", None)

    # The loader's own spine validator accepts this (the behaviour the hook must mirror).
    from engine.scenario_loader import validate_causality_spine  # noqa: PLC0415

    class _Caus:
        def __init__(self, parent):
            self.parent_step = parent

    class _Step:
        def __init__(self, sid):
            self.id = sid
            self.causality = None

    validate_causality_spine([_Step("s1"), _Step("s2")])  # must not raise

    crafted = tmp_path / "cgo_no_causality.yml"
    crafted.write_text(yaml.safe_dump(base, sort_keys=False), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(HOOK), str(crafted), str(REPO_ROOT)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, (
        "hook rejected a cgo-anchor-without-step-causality scenario the loader accepts\n"
        f"stdout:\n{result.stdout}"
    )
    assert "more than one root step" not in result.stdout
    # the previously-dead warning branch should now fire
    assert "no step wires a causality" in result.stdout

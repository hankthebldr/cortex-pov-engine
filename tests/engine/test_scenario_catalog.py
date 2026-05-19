"""Property tests over the full ``scenarios/`` library.

Goal: catch the "I added a new YAML scenario and broke startup" class of
regression *before* SimCore boots in front of a customer.

Each test iterates every file under ``scenarios/**/*.yml`` (excluding the
``_schema.yml`` reference and any READMEs).  Failures are reported with
filename so the offending file is obvious.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCENARIOS_DIR = REPO_ROOT / "scenarios"

# Mirror core/engine/scenario_loader._find_yaml_files so the property tests
# walk exactly what SimCore loads at boot — nothing more, nothing less.
# Sub-trees under these names hold non-scenario YAML (probe packs, EAL
# campaign templates, packaged supporting artefacts).
_SKIP_DIRNAMES = {"probes", "packages", "campaigns"}


def _scenario_files() -> list[Path]:
    files: list[Path] = []
    for root, dirs, names in __import__("os").walk(SCENARIOS_DIR):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRNAMES]
        for name in names:
            if not name.endswith(".yml"):
                continue
            if name.startswith("_"):
                continue
            files.append(Path(root) / name)
    return sorted(files)


SCENARIO_FILES = _scenario_files()


def test_repo_has_scenarios():
    """Belt-and-braces: the suite is meaningless if the glob returns zero."""
    assert SCENARIO_FILES, f"no scenario YAML found under {SCENARIOS_DIR}"


@pytest.mark.parametrize("path", SCENARIO_FILES, ids=lambda p: p.relative_to(REPO_ROOT).as_posix())
def test_scenario_parses_and_validates(path: Path):
    """Each file parses as YAML and survives Pydantic validation."""
    from engine.scenario_loader import ScenarioSchema  # noqa: PLC0415

    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    assert isinstance(raw, dict), f"top-level YAML must be a mapping in {path}"

    schema = ScenarioSchema.model_validate(raw)

    # id format SIM-{PLANE}-{NNN}
    assert re.match(r"^SIM-[A-Z_]+-\d{3,}$", schema.scenario_id), (
        f"{path}: bad id format {schema.scenario_id!r}"
    )

    # Filename should align with scenario_id (case-insensitive, dashes vs underscores tolerated)
    fname_id_part = path.stem.upper().replace("_", "-")
    sid_normalized = schema.scenario_id.upper().replace("SIM-", "")
    assert sid_normalized.replace("-", "") in fname_id_part.replace("-", ""), (
        f"{path}: filename and scenario_id mismatched"
    )


@pytest.mark.parametrize("path", SCENARIO_FILES, ids=lambda p: p.relative_to(REPO_ROOT).as_posix())
def test_scenario_has_at_least_one_step_and_one_expected_detection(path: Path):
    """A scenario with zero steps or zero expected_detections won't seed
    Result rows and therefore can't drive any detection validation."""
    from engine.scenario_loader import ScenarioSchema  # noqa: PLC0415

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    schema = ScenarioSchema.model_validate(raw)
    assert schema.steps, f"{path}: no steps"
    has_detection = any(s.expected_detections for s in schema.steps)
    assert has_detection, f"{path}: no expected_detections across steps"


@pytest.mark.parametrize("path", SCENARIO_FILES, ids=lambda p: p.relative_to(REPO_ROOT).as_posix())
def test_scenario_plane_matches_directory(path: Path):
    """``scenarios/<plane>/...`` should match the YAML's ``plane`` field
    (with the multi_plane and analytics carve-outs)."""
    from engine.scenario_loader import ScenarioSchema  # noqa: PLC0415

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    schema = ScenarioSchema.model_validate(raw)

    rel = path.relative_to(SCENARIOS_DIR)
    plane_dir = rel.parts[0]
    declared = schema.plane.upper().replace("_", "")

    if plane_dir == "multi_plane":
        # Multi-plane scenarios run on the ANALYTICS plane
        assert declared in {"ANALYTICS", "MULTIPLANE"}, (
            f"{path}: plane={declared} but lives under multi_plane/"
        )
    else:
        normalized = plane_dir.upper().replace("_", "")
        assert normalized in declared or declared in normalized, (
            f"{path}: directory={plane_dir} but plane={declared}"
        )


def test_scenario_ids_unique():
    """Two YAMLs with the same scenario_id would cause one to overwrite the
    other at load time — surface that loudly."""
    from engine.scenario_loader import ScenarioSchema  # noqa: PLC0415

    seen: dict[str, Path] = {}
    for path in SCENARIO_FILES:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        schema = ScenarioSchema.model_validate(raw)
        if schema.scenario_id in seen:
            pytest.fail(
                f"duplicate scenario_id {schema.scenario_id!r} in "
                f"{path} and {seen[schema.scenario_id]}"
            )
        seen[schema.scenario_id] = path

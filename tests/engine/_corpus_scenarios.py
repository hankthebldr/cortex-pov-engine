"""Real corpus scenarios as the dicts the routes hand to derive() and render().

Same path as the API: YAML -> ScenarioSchema -> Scenario ORM kwargs -> ``Scenario.to_dict()``.
A parse failure fails the caller; it is a different defect and must not be skipped here.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCENARIOS_DIR = REPO_ROOT / "scenarios"


def scenario_dict(path: Path | str) -> dict:
    from engine.scenario_loader import _parse_and_validate, _schema_to_orm_kwargs  # noqa: PLC0415
    from models import Scenario  # noqa: PLC0415

    schema, err = _parse_and_validate(str(path))
    assert err is None, f"{path} failed schema validation:\n{err}"
    return Scenario(**_schema_to_orm_kwargs(schema)).to_dict()


def all_scenario_dicts() -> list[dict]:
    from engine.scenario_loader import _find_yaml_files  # noqa: PLC0415

    out = [scenario_dict(p) for p in _find_yaml_files(str(SCENARIOS_DIR))]
    assert len(out) > 100, f"only {len(out)} scenarios parsed; a corpus check would pass vacuously"
    return out


def scenario_by_id(scenario_id: str) -> dict:
    from engine.scenario_loader import _find_yaml_files  # noqa: PLC0415

    for p in _find_yaml_files(str(SCENARIOS_DIR)):
        text = Path(p).read_text(encoding="utf-8")
        if f"scenario_id: {scenario_id}\n" in text or f'scenario_id: "{scenario_id}"\n' in text:
            return scenario_dict(p)
    raise AssertionError(f"scenario {scenario_id} not found under {SCENARIOS_DIR}")

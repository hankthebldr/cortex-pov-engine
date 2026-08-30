"""StepSchema.requires_interpreters — the scenario-authoring half of
docs/design/agent-runtime-dependencies.md.

Pure Pydantic model tests: no DB, no YAML file on disk. Confirms the field is
optional/back-compat (existing scenarios load unchanged), survives
model_dump() (which is what the loader persists onto Scenario.steps and what
the orchestrator hands to the beacon verbatim), and is what SIM-EDR-001
step-05 now actually declares.
"""
from __future__ import annotations

from engine.scenario_loader import StepSchema


def _base_kwargs(**overrides):
    kwargs = dict(
        id="step-05", name="n", command="cmd", identity="root",
        mitre_technique="T1003",
    )
    kwargs.update(overrides)
    return kwargs


def test_requires_interpreters_defaults_to_empty_list():
    step = StepSchema(**_base_kwargs())
    assert step.requires_interpreters == []


def test_requires_interpreters_accepts_a_declared_list():
    step = StepSchema(**_base_kwargs(requires_interpreters=["python"]))
    assert step.requires_interpreters == ["python"]


def test_requires_interpreters_survives_model_dump():
    """This is the load-bearing property: orchestrator.Task.steps is built
    from `[s.model_dump() for s in schema.steps]`, so anything not present in
    the dump never reaches the beacon."""
    step = StepSchema(**_base_kwargs(requires_interpreters=["python"]))
    dumped = step.model_dump()
    assert dumped["requires_interpreters"] == ["python"]


def test_omitting_requires_interpreters_dumps_empty_list_not_missing_key():
    step = StepSchema(**_base_kwargs())
    dumped = step.model_dump()
    assert dumped["requires_interpreters"] == []


def test_sim_edr_001_step_05_yaml_declares_the_python_dependency():
    """Guards against the declaration silently regressing out of the actual
    scenario file — the fix is only real if the corpus uses it."""
    import yaml
    from pathlib import Path

    base_dir = Path(__file__).resolve().parents[2]
    path = base_dir / "scenarios" / "edr" / "edr-001-credential-dumping.yml"
    data = yaml.safe_load(path.read_text())
    steps = {s["id"]: s for s in data["steps"]}
    assert steps["step-05"].get("requires_interpreters") == ["python"], (
        "SIM-EDR-001 step-05 downloads mimipenguin.sh, which shells out to "
        "python — it must declare requires_interpreters so the beacon can "
        "catch the gap before running the step"
    )

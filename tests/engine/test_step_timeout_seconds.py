"""A step's ``timeout_seconds`` reaches the push generator (r2-push handover).

``push_generator._step_timeout`` reads ``step["timeout_seconds"]`` to bound a
.ps1 step (default 300 s), but ``StepSchema`` had no such field, so pydantic
dropped it at load and every corpus .ps1 step ran with the default. A step that
needs longer (SIM-NDR-005 step-02 moves 1 GiB) had no way to say so.

The field is optional and omitted from the persisted step when unset, so every
scenario that does not declare it persists byte-identically.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
SOURCE = REPO / "scenarios" / "edr" / "edr-006-lsass-memory-dump.yml"


def _load_with(tmp_path, value):
    from engine.scenario_loader import _parse_and_validate, _schema_to_orm_kwargs

    doc = yaml.safe_load(SOURCE.read_text(encoding="utf-8"))
    doc["steps"][0]["timeout_seconds"] = value
    path = tmp_path / SOURCE.name
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    schema, err = _parse_and_validate(str(path))
    return (None if schema is None else _schema_to_orm_kwargs(schema)), err


def test_a_declared_timeout_survives_load_and_bounds_the_step(tmp_path):
    from engine.push_generator import _step_timeout

    kwargs, err = _load_with(tmp_path, 900)
    assert err is None, err
    first, *rest = kwargs["steps"]
    assert first["timeout_seconds"] == 900
    assert _step_timeout(first) == 900
    assert all("timeout_seconds" not in s for s in rest), "an unset timeout must not be persisted"


@pytest.mark.parametrize("bad", [0, -5, True, "fast", 86401])
def test_a_timeout_that_is_not_a_positive_bounded_int_is_refused(tmp_path, bad):
    kwargs, err = _load_with(tmp_path, bad)
    assert kwargs is None and err and "timeout_seconds" in err, (kwargs, err)

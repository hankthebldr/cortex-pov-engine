"""Unit tests for BaseSimulation contract + SimulationContext helpers."""
from __future__ import annotations

import pytest

from eal_simulator import BaseSimulation, SimulationContext
from pydantic import BaseModel


class _Params(BaseModel):
    foo: str = "bar"


class _Plugin(BaseSimulation):
    class Meta:
        name = "demo"
        params_model = _Params

    async def run(self, ctx):  # type: ignore[override]
        raise NotImplementedError


def test_metadata_required_fields():
    meta = _Plugin.metadata()
    assert meta["name"] == "demo"
    assert meta["params_schema"]["type"] == "object"
    assert meta["class"].endswith("_Plugin")


def test_metadata_missing_name():
    class Bad(BaseSimulation):
        class Meta:
            params_model = _Params

        async def run(self, ctx):  # type: ignore[override]
            raise NotImplementedError

    with pytest.raises(TypeError):
        Bad.metadata()


def test_validate_params_round_trip():
    params = _Plugin.validate_params({"foo": "baz"})
    assert isinstance(params, _Params)
    assert params.foo == "baz"


def test_simulation_run_id_format():
    rid = BaseSimulation.new_simulation_run_id()
    assert rid.startswith("cortexsim-")
    assert len(rid) > len("cortexsim-")


def test_telemetry_headers_present():
    async def _emit(_): return None

    ctx = SimulationContext(
        campaign_id="CMP-X-001",
        run_id="run-1",
        step_id="step-01",
        simulation_run_id="cortexsim-aaaaaaaa",
        dry_run=True,
        target_allowlist=["example.test"],
        emit_event=_emit,
        params=_Params(),
    )
    headers = ctx.telemetry_headers
    assert headers["X-Simulation-Run-ID"] == "cortexsim-aaaaaaaa"
    assert headers["X-Simulation-Campaign-ID"] == "CMP-X-001"
    assert headers["X-Simulation-Source"].startswith("cortexsim-eal-simulator")

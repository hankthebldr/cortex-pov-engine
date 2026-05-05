"""Campaign executor tests — happy path, safety violation, plugin errors."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from eal_simulator import Campaign, CampaignExecutor, SimulationResult
from eal_simulator.base import BaseSimulation
from eal_simulator.executor import InMemoryTaskQueue
from pydantic import BaseModel

from tests.eal_simulator.conftest import DummyPlugin


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_dry_run_completes_without_authorisation(make_executor):
    executor = make_executor()
    campaign = Campaign.model_validate({
        "campaign_id": "CMP-TEST-100",
        "name": "dry-run",
        "dry_run": True,
        "steps": [{"step_id": "step-01", "plugin": "test_dummy",
                   "params": {"target": "anything.example"}}],
    })
    state = _run(executor.execute(campaign))
    assert state.status == "complete"
    assert len(state.step_results) == 1
    assert state.step_results[0].status == "success"
    assert DummyPlugin.invocations[-1]["dry_run"] is True


def test_live_campaign_succeeds_when_target_allowed(make_executor, sample_campaign):
    state = _run(make_executor().execute(sample_campaign))
    assert state.status == "complete"
    assert state.dry_run is False
    assert DummyPlugin.invocations[-1]["target"] == "example.test"


def test_live_safety_violation_blocks_campaign(make_executor):
    executor = make_executor()
    bad = Campaign.model_validate({
        "campaign_id": "CMP-TEST-101",
        "name": "no-allowlist",
        "dry_run": True,  # Pydantic forbids dry_run=False without allowlist
        "steps": [{"step_id": "step-01", "plugin": "test_dummy",
                   "params": {"target": "evil.example.com"}}],
    })
    # Bypass the model validator to simulate a campaign that pre-existed
    # before the validator was tightened — exercise the executor's runtime
    # check.
    object.__setattr__(bad, "dry_run", False)
    object.__setattr__(bad, "simulation_authorized", False)
    state = _run(executor.execute(bad))
    assert state.status == "failed"
    assert state.error and "safety_violation" in state.error


def test_per_target_authorisation_failure_marks_step_error(make_executor):
    executor = make_executor()
    c = Campaign.model_validate({
        "campaign_id": "CMP-TEST-102",
        "name": "wrong-target",
        "dry_run": False,
        "simulation_authorized": True,
        "authorized_by": "op",
        "target_allowlist": ["example.test"],
        "steps": [{"step_id": "step-01", "plugin": "test_dummy",
                   "params": {"target": "evil.example.com"}}],
    })
    state = _run(executor.execute(c))
    assert state.step_results[0].status == "error"
    assert "safety_violation" in (state.step_results[0].error or "")


def test_unknown_plugin_step_error(isolated_registry, memory_audit):
    executor = CampaignExecutor(registry=isolated_registry, audit=memory_audit)
    c = Campaign.model_validate({
        "campaign_id": "CMP-TEST-103",
        "name": "missing-plugin",
        "dry_run": True,
        "steps": [{"step_id": "step-01", "plugin": "nope_not_there", "params": {}}],
    })
    state = _run(executor.execute(c))
    assert state.step_results[0].status == "error"
    assert "plugin_not_found" in (state.step_results[0].error or "")


def test_invalid_params_step_error(isolated_registry, memory_audit):
    executor = CampaignExecutor(registry=isolated_registry, audit=memory_audit)
    c = Campaign.model_validate({
        "campaign_id": "CMP-TEST-104",
        "name": "bad-params",
        "dry_run": True,
        "steps": [{"step_id": "step-01", "plugin": "test_dummy",
                   "params": {"iterations": -1}}],
    })
    state = _run(executor.execute(c))
    assert state.step_results[0].status == "error"
    assert "params_invalid" in (state.step_results[0].error or "")


def test_on_error_abort_stops_campaign(isolated_registry, memory_audit):
    executor = CampaignExecutor(registry=isolated_registry, audit=memory_audit)
    c = Campaign.model_validate({
        "campaign_id": "CMP-TEST-105",
        "name": "abort",
        "dry_run": True,
        "steps": [
            {"step_id": "step-01", "plugin": "missing_plugin", "params": {},
             "on_error": "abort"},
            {"step_id": "step-02", "plugin": "test_dummy",
             "params": {"target": "ok.example"}},
        ],
    })
    state = _run(executor.execute(c))
    assert state.status == "aborted"
    assert len(state.step_results) == 1


def test_in_memory_task_queue_runs_in_background(make_executor, sample_campaign):
    executor = make_executor()
    queue = InMemoryTaskQueue()

    async def _go() -> str:
        task_id = await executor.submit(sample_campaign, queue=queue)
        # Drain pending tasks.
        await asyncio.sleep(0)
        for _ in range(50):
            if not queue.task_ids():
                break
            await asyncio.sleep(0.01)
        return task_id

    tid = _run(_go())
    assert isinstance(tid, str) and tid


def test_step_result_to_dict_round_trip():
    now = datetime.now(timezone.utc)
    r = SimulationResult(
        plugin="x",
        step_id="step-01",
        status="success",
        started_at=now,
        completed_at=now,
        events_emitted=2,
        bytes_sent=128,
    )
    d = r.to_dict()
    assert d["plugin"] == "x"
    assert d["events_emitted"] == 2
    assert isinstance(d["started_at"], str)

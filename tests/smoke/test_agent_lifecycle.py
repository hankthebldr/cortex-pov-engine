"""Layer 2 smoke: pull-mode agent contract.

Stands in for the real Go beacon by speaking HTTP directly to the same
endpoints the agent uses.  Validates:

  * Agent registration (new + idempotent re-register)
  * Task polling shape — server returns {"task": null} on idle and
    {"task": {...}} when a run has been queued
  * Output streaming
  * Run completion + status transition

This is the contract the real cortexsim-agent binary depends on.  A break
here will break every lab deployment.
"""

from __future__ import annotations

import time
import uuid

import httpx
import pytest


@pytest.fixture
def agent_id() -> str:
    """Fresh agent_id per test so re-runs don't collide."""
    return f"smoke-{uuid.uuid4().hex[:8]}"


def test_agent_register_idempotent(client: httpx.Client, agent_id: str) -> None:
    payload = {
        "agent_id": agent_id,
        "hostname": "smoke-test-host",
        "os": "linux",
        "capabilities": ["bash", "python3", "docker"],
    }
    # First registration
    r1 = client.post("/api/agents/register", json=payload)
    assert r1.status_code == 200, r1.text
    assert r1.json()["status"] == "registered"

    # Second registration with same id — must succeed (idempotent)
    payload["hostname"] = "smoke-test-host-renamed"
    r2 = client.post("/api/agents/register", json=payload)
    assert r2.status_code == 200, r2.text

    # Roster reflects the rename
    listing = client.get("/api/agents").json()
    found = [a for a in listing["agents"] if a["agent_id"] == agent_id]
    assert found, f"agent {agent_id} not in roster"
    assert found[0]["hostname"] == "smoke-test-host-renamed"


def test_poll_unknown_agent_404(client: httpx.Client) -> None:
    r = client.get(f"/api/agents/no-such-agent-{uuid.uuid4().hex[:8]}/tasks")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "AGENT_NOT_FOUND"


def test_poll_idle_returns_null_task(client: httpx.Client, agent_id: str) -> None:
    client.post(
        "/api/agents/register",
        json={"agent_id": agent_id, "hostname": "h", "os": "linux", "capabilities": []},
    ).raise_for_status()

    r = client.get(f"/api/agents/{agent_id}/tasks")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "task" in body, f"server contract: must return key 'task' (got {body})"
    assert body["task"] is None, f"idle agent should get null task, got {body['task']!r}"


def test_pull_mode_full_cycle(
    client: httpx.Client, agent_id: str, known_scenario_id: str
) -> None:
    """Register agent → launch pull run targeting it → poll task → stream
    output → complete run → verify status transition + result rows.
    """
    client.post(
        "/api/agents/register",
        json={"agent_id": agent_id, "hostname": "h", "os": "linux", "capabilities": []},
    ).raise_for_status()

    launch = client.post(
        "/api/run",
        json={
            "scenario_id": known_scenario_id,
            "mode": "pull",
            "target_agent_id": agent_id,
        },
    )
    assert launch.status_code == 200, launch.text
    run_id = launch.json()["run_id"]

    # Poll until we receive a task or give up (orchestrator should enqueue
    # immediately, but allow a few ticks for slow CI).
    task = None
    deadline = time.time() + 10.0
    while time.time() < deadline:
        r = client.get(f"/api/agents/{agent_id}/tasks")
        assert r.status_code == 200, r.text
        body = r.json()
        if body.get("task"):
            task = body["task"]
            break
        time.sleep(0.5)

    assert task is not None, f"no task dispatched for run {run_id} within 10s"
    assert task["run_id"] == run_id

    # Simulate agent execution by streaming output + completing
    client.post(
        f"/api/runs/{run_id}/output",
        json={"output": "smoke-test stdout line 1\n"},
    ).raise_for_status()
    client.post(
        f"/api/runs/{run_id}/output",
        json={"output": "smoke-test stdout line 2\n"},
    ).raise_for_status()

    complete = client.post(
        f"/api/runs/{run_id}/complete",
        json={"exit_code": 0, "summary": "smoke ok"},
    )
    assert complete.status_code == 200, complete.text
    assert complete.json()["status"] == "complete"

    # Run reflects terminal state + output stitched
    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["status"] == "complete"
    assert "smoke-test stdout line 1" in (detail.get("output") or "")
    assert "smoke-test stdout line 2" in (detail.get("output") or "")
    assert "COMPLETION SUMMARY" in detail["output"]

    # Result rows still seeded — pull mode shares the auto-seed path
    results = client.get(f"/api/results/{run_id}").json()
    assert results["results"], "pull-mode run should have seeded Result rows"

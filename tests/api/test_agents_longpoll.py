"""Long-polling on GET /api/agents/{id}/tasks?wait=N.

These exist because the feature shipped with a Go-side test that proves the
BEACON sends `?wait=N` (against an httptest stub) and nothing that proves the
SERVER survives receiving it. It did not: `wait_for_task` used `asyncio.Event`,
`asyncio.wait_for` and `asyncio.TimeoutError` in a module that never imported
asyncio, so every `wait>0` request returned

    500 {"error": "Internal server error", "code": "INTERNAL_ERROR",
         "detail": "name 'asyncio' is not defined"}

`from __future__ import annotations` is why that survived review and 4828
backend tests: it turns `self._events: dict[str, asyncio.Event] = {}` into a
string annotation, so the module imports cleanly and the NameError only fires
on a request path no Python test exercised.

Writing these surfaced a SECOND defect behind the first. Orchestrator._events
cached a bare asyncio.Event per agent for the life of the process, and an
asyncio.Event binds to the first loop that awaits it, so the second test in
this file died with

    RuntimeError: <asyncio.locks.Event object ...> is bound to a different
                  event loop

uvicorn runs one loop per process, so production survived it — but it made the
whole path unreachable from TestClient, which is exactly why the missing import
above was never caught. The cache now stores (loop, event) and rebuilds the
Event when the running loop differs.

Observed, both fixes reverted independently:
  - remove `import asyncio`      -> 4 of these 7 fail (the two long-poll HTTP
    cases and both wait_for_task cases); wait=0, unknown-agent and the bounds
    case stay green because none of them reach wait_for_task.
  - restore the bare Event cache -> test_longpoll_actually_waits_before_
    reporting_idle fails with the loop-binding RuntimeError above, because it
    is the second test in the file to await the cached Event.
"""
from __future__ import annotations

import asyncio
import time

import pytest


@pytest.fixture
def client(make_client):
    from api.agents import router
    return make_client(router)


def _register(client, agent_id: str = "lp-agent-01"):
    r = client.post("/api/agents/register", json={
        "agent_id": agent_id, "hostname": "lp-host", "os": "linux",
        "capabilities": ["shell"],
    })
    assert r.status_code == 200, r.text
    return agent_id


def test_wait_zero_returns_immediately(client):
    """The pre-existing contract is unchanged: wait=0 does not block."""
    agent_id = _register(client)
    started = time.monotonic()
    r = client.get(f"/api/agents/{agent_id}/tasks?wait=0")
    elapsed = time.monotonic() - started

    assert r.status_code == 200, r.text
    assert r.json() == {"task": None}
    assert elapsed < 0.5, f"wait=0 blocked for {elapsed:.2f}s"


def test_longpoll_on_empty_queue_returns_null_not_500(client):
    """THE regression. wait>0 with an empty queue must be a normal idle poll."""
    agent_id = _register(client)
    r = client.get(f"/api/agents/{agent_id}/tasks?wait=1")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {"task": None}, body
    # Guard the specific failure mode rather than just the status code, so a
    # future 200-with-an-error-body cannot pass this test.
    assert "INTERNAL_ERROR" not in r.text
    assert "asyncio" not in r.text


def test_longpoll_actually_waits_before_reporting_idle(client):
    """A wait that returns instantly would mean the timeout path was skipped
    (or swallowed), which is indistinguishable from the old wait=0 behaviour."""
    agent_id = _register(client)
    started = time.monotonic()
    r = client.get(f"/api/agents/{agent_id}/tasks?wait=1")
    elapsed = time.monotonic() - started

    assert r.status_code == 200, r.text
    assert elapsed >= 0.9, f"wait=1 returned after only {elapsed:.2f}s — it did not wait"


def test_unknown_agent_does_not_block_for_the_wait_window(client):
    """A 404 must short-circuit: an unregistered beacon should be told to
    register immediately, not held open for the full wait window."""
    started = time.monotonic()
    r = client.get("/api/agents/nobody-here/tasks?wait=5")
    elapsed = time.monotonic() - started

    assert r.status_code == 404, r.text
    assert elapsed < 1.0, f"unknown agent blocked for {elapsed:.2f}s"


def test_wait_is_bounded_by_the_query_contract(client):
    """wait is declared ge=0, le=60; anything outside that is a 422, not a
    request that pins a worker open for an arbitrary duration."""
    agent_id = _register(client)
    assert client.get(f"/api/agents/{agent_id}/tasks?wait=61").status_code == 422
    assert client.get(f"/api/agents/{agent_id}/tasks?wait=-1").status_code == 422


@pytest.mark.asyncio
async def test_wait_for_task_wakes_early_when_a_task_arrives():
    """The point of long-polling: an enqueue during the wait must wake it in
    milliseconds, not burn the full timeout. Exercised directly on the
    orchestrator so the assertion is about the Event, not about HTTP timing."""
    from engine.orchestrator import Orchestrator, Task

    orch = Orchestrator()
    agent_id = "lp-agent-async"
    task = Task(
        task_id="t-1", run_id="r-1", scenario_id="SIM-EDR-001",
        steps=[], identity_context=None,
    )

    async def enqueue_shortly():
        await asyncio.sleep(0.05)
        orch._enqueue(agent_id, task)

    started = time.monotonic()
    await asyncio.gather(
        orch.wait_for_task(agent_id, timeout=5.0),
        enqueue_shortly(),
    )
    elapsed = time.monotonic() - started

    assert elapsed < 1.0, f"wait_for_task burned {elapsed:.2f}s instead of waking on enqueue"
    assert orch.peek_queue(agent_id) == [task]


@pytest.mark.asyncio
async def test_wait_for_task_returns_at_once_when_a_task_is_already_queued():
    """clear()-then-recheck: a task enqueued between the caller's dequeue miss
    and the wait must not be lost to the event being cleared."""
    from engine.orchestrator import Orchestrator, Task

    orch = Orchestrator()
    agent_id = "lp-agent-prequeued"
    task = Task(
        task_id="t-2", run_id="r-2", scenario_id="SIM-EDR-001",
        steps=[], identity_context=None,
    )
    orch._enqueue(agent_id, task)

    started = time.monotonic()
    await orch.wait_for_task(agent_id, timeout=5.0)
    elapsed = time.monotonic() - started

    assert elapsed < 0.5, f"wait_for_task blocked {elapsed:.2f}s with a task already queued"

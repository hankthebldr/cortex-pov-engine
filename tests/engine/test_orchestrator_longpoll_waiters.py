"""A restarted beacon's poll supersedes the one it left behind (OPS-09).

With ``--long-poll``, a beacon that restarts (an upgrade, a service restart,
the host coming back) leaves its previous ``GET /tasks?wait=N`` parked on
SimCore for up to N seconds. Every waiter for an agent shared ONE
``asyncio.Event``, which wakes waiters first-in first-out, so the next task
went to the OLDEST waiter — the handler whose socket is closed. Observed on
c852eac with two waiters and one enqueue:

    [('STALE (dead conn, older)', 'T1'), ('FRESH (restarted beacon)', None)]

``dequeue_for_agent`` had already deleted the durable row and stamped
``delivered_at``, so the run read delivered and no host ever received it.

The orchestrator's half of the fix: each waiter has its own event, a newer
poll for the same agent SUPERSEDES the older ones, and an enqueue wakes only
the current waiter. ``wait_for_task`` returns ``False`` to a superseded waiter
so the route can decline to dequeue for it.

What this does not cover is a dead waiter that was never superseded (the
beacon died and has not polled again): it is still woken, and only the route
can know its client is gone (``request.is_disconnected()``, in
``core/api/agents.py``).
"""
from __future__ import annotations

import asyncio
import time

import pytest


def _task(task_id: str):
    from engine.orchestrator import Task

    return Task(task_id=task_id, run_id=f"run-{task_id}", scenario_id="SIM-EDR-001",
                steps=[], identity_context=None)


async def _poll(orch, name: str, got: list, *, timeout: float, honour_superseded: bool):
    """What the tasks route does around the wait."""
    current = await orch.wait_for_task("a1", timeout=timeout)
    task = None
    if current is not False or not honour_superseded:
        task = orch.dequeue("a1")
    got.append((name, task.task_id if task else None, current))


@pytest.mark.asyncio
@pytest.mark.parametrize("honour_superseded", [False, True],
                         ids=["route-ignores-the-return-value", "route-honours-it"])
async def test_the_fresh_poll_gets_the_task_not_the_stale_one(honour_superseded):
    """Holds with today's route too (it ignores the return value): only the
    current waiter is woken, and the stale one finds an empty queue when its
    own timeout fires."""
    from engine.orchestrator import Orchestrator

    orch = Orchestrator()
    got: list = []
    stale = asyncio.create_task(_poll(orch, "stale", got, timeout=0.6,
                                      honour_superseded=honour_superseded))
    await asyncio.sleep(0.05)
    fresh = asyncio.create_task(_poll(orch, "fresh", got, timeout=5.0,
                                      honour_superseded=honour_superseded))
    await asyncio.sleep(0.05)

    started = time.monotonic()
    orch._enqueue("a1", _task("T1"))
    await asyncio.wait_for(fresh, timeout=2.0)
    assert time.monotonic() - started < 0.5, "the current waiter was not woken by the enqueue"
    await asyncio.wait_for(stale, timeout=2.0)

    by_name = {name: (task, current) for name, task, current in got}
    assert by_name["fresh"] == ("T1", True), got
    assert by_name["stale"][0] is None, f"the stale waiter consumed the task: {got}"
    assert by_name["stale"][1] is False, "a superseded waiter must be told so"


@pytest.mark.asyncio
async def test_a_superseded_waiter_is_never_woken_by_a_later_task():
    """T1 goes to the fresh waiter and it leaves; T2 arrives while the stale
    waiter is still parked. T2 must stay queued for the beacon's next poll."""
    from engine.orchestrator import Orchestrator

    orch = Orchestrator()
    got: list = []
    stale = asyncio.create_task(_poll(orch, "stale", got, timeout=0.8, honour_superseded=True))
    await asyncio.sleep(0.05)
    fresh = asyncio.create_task(_poll(orch, "fresh", got, timeout=5.0, honour_superseded=True))
    await asyncio.sleep(0.05)
    orch._enqueue("a1", _task("T1"))
    await asyncio.wait_for(fresh, timeout=2.0)

    orch._enqueue("a1", _task("T2"))
    await asyncio.wait_for(stale, timeout=2.0)

    assert ("stale", None, False) in got, got
    assert [t.task_id for t in orch.peek_queue("a1")] == ["T2"]


@pytest.mark.asyncio
async def test_a_lone_waiter_is_current_and_wakes_at_once():
    from engine.orchestrator import Orchestrator

    orch = Orchestrator()

    async def enqueue_shortly():
        await asyncio.sleep(0.05)
        orch._enqueue("a1", _task("T1"))

    started = time.monotonic()
    current, _ = await asyncio.gather(orch.wait_for_task("a1", timeout=5.0), enqueue_shortly())
    assert current is True
    assert time.monotonic() - started < 1.0


@pytest.mark.asyncio
async def test_a_timeout_with_nothing_queued_is_still_current():
    from engine.orchestrator import Orchestrator

    orch = Orchestrator()
    started = time.monotonic()
    assert await orch.wait_for_task("a1", timeout=0.2) is True
    assert 0.15 <= time.monotonic() - started < 1.0


@pytest.mark.asyncio
async def test_an_already_queued_task_returns_at_once():
    from engine.orchestrator import Orchestrator

    orch = Orchestrator()
    orch._enqueue("a1", _task("T1"))
    started = time.monotonic()
    assert await orch.wait_for_task("a1", timeout=5.0) is True
    assert time.monotonic() - started < 0.5


@pytest.mark.asyncio
async def test_waiters_are_forgotten_when_they_return():
    """No leak: a long-lived SimCore sees one poll after another, for ever."""
    from engine.orchestrator import Orchestrator

    orch = Orchestrator()
    for _ in range(5):
        await orch.wait_for_task("a1", timeout=0.01)
    a = asyncio.create_task(orch.wait_for_task("a1", timeout=0.2))
    b = asyncio.create_task(orch.wait_for_task("a1", timeout=0.2))
    await asyncio.gather(a, b)
    assert orch._waiters.get("a1", []) == []


@pytest.mark.asyncio
async def test_a_cancelled_waiter_is_forgotten_and_does_not_block_the_next():
    """The server cancels the handler when its client disconnects."""
    from engine.orchestrator import Orchestrator

    orch = Orchestrator()
    gone = asyncio.create_task(orch.wait_for_task("a1", timeout=30.0))
    await asyncio.sleep(0.05)
    gone.cancel()
    with pytest.raises(asyncio.CancelledError):
        await gone
    assert orch._waiters.get("a1", []) == []

    async def enqueue_shortly():
        await asyncio.sleep(0.05)
        orch._enqueue("a1", _task("T1"))

    current, _ = await asyncio.gather(orch.wait_for_task("a1", timeout=5.0), enqueue_shortly())
    assert current is True


def test_an_enqueue_survives_a_waiter_whose_loop_is_gone():
    """A waiter parked on a loop that has since closed (a torn-down worker, a
    finished TestClient) must not make the next enqueue raise."""
    from engine.orchestrator import Orchestrator

    orch = Orchestrator()
    loop = asyncio.new_event_loop()

    async def park():
        waiter = asyncio.ensure_future(orch.wait_for_task("a1", timeout=30.0))
        await asyncio.sleep(0.05)
        return waiter

    waiter = loop.run_until_complete(park())
    assert len(orch._waiters["a1"]) == 1
    waiter.cancel()
    try:
        loop.run_until_complete(waiter)
    except asyncio.CancelledError:
        pass
    # Simulate the entry surviving (cancellation that never ran its cleanup).
    from engine.orchestrator import _TaskWaiter

    orch._waiters["a1"] = [_TaskWaiter(loop=loop, event=asyncio.Event())]
    loop.close()

    orch._enqueue("a1", _task("T1"))          # must not raise
    assert [t.task_id for t in orch.peek_queue("a1")] == ["T1"]
    assert orch._waiters.get("a1", []) == []


def test_two_agents_do_not_supersede_each_other():
    from engine.orchestrator import Orchestrator

    orch = Orchestrator()

    async def go():
        a = asyncio.create_task(orch.wait_for_task("a1", timeout=0.3))
        await asyncio.sleep(0.02)
        b = asyncio.create_task(orch.wait_for_task("a2", timeout=0.3))
        return await asyncio.gather(a, b)

    assert asyncio.run(go()) == [True, True]

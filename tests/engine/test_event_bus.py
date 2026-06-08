"""Unit tests for the in-process EventBus (core/events.py).

Covers publish/subscribe fan-out semantics:
  - run-scoped subscriber receives only its run's events
  - global subscriber receives every event (all runs + run_id=None events)
  - run-scoped subscriber also receives explicitly-global (run_id=None) events
  - no-subscriber publish is a silent no-op
  - publish stamps a ``ts`` when the caller omits one and preserves an
    explicit one
  - a full (slow-consumer) queue drops events instead of raising
  - unsubscribe detaches the queue and prunes empty per-run buckets

asyncio.Queue is bound to the running loop, so every test drives the bus
through ``asyncio.run`` (a fresh loop per test).
"""
from __future__ import annotations

import asyncio

import pytest

# events.py lives under core/ which conftest puts on sys.path.
from events import EventBus, event_bus


def _run(coro):
    return asyncio.run(coro)


def test_run_scoped_subscriber_receives_only_its_run():
    async def scenario():
        bus = EventBus()
        q_a = bus.subscribe("run-a")
        q_b = bus.subscribe("run-b")

        await bus.publish("run-a", {"type": "run.status", "data": {"status": "running"}})

        # run-a queue has the event; run-b queue is empty.
        evt = q_a.get_nowait()
        assert evt["data"]["status"] == "running"
        assert q_b.empty()

    _run(scenario())


def test_global_subscriber_receives_every_event():
    async def scenario():
        bus = EventBus()
        q_global = bus.subscribe(None)

        await bus.publish("run-a", {"type": "run.output", "data": {"chunk": "x"}})
        await bus.publish("run-b", {"type": "run.output", "data": {"chunk": "y"}})
        await bus.publish(None, {"type": "agent.status", "data": {"agent_id": "a1"}})

        chunks = []
        while not q_global.empty():
            chunks.append(q_global.get_nowait())
        assert len(chunks) == 3
        types = {c["type"] for c in chunks}
        assert types == {"run.output", "agent.status"}

    _run(scenario())


def test_run_scoped_subscriber_does_not_get_other_runs():
    async def scenario():
        bus = EventBus()
        q_a = bus.subscribe("run-a")
        await bus.publish("run-b", {"type": "run.status", "data": {"status": "x"}})
        assert q_a.empty()

    _run(scenario())


def test_fanout_to_global_and_run_scoped_together():
    async def scenario():
        bus = EventBus()
        q_global = bus.subscribe(None)
        q_run = bus.subscribe("run-a")

        await bus.publish("run-a", {"type": "run.status", "data": {"status": "running"}})

        # Both the global and the run-a subscriber get the single event.
        assert q_global.get_nowait()["data"]["status"] == "running"
        assert q_run.get_nowait()["data"]["status"] == "running"

    _run(scenario())


def test_publish_with_no_subscribers_is_noop():
    async def scenario():
        bus = EventBus()
        # Must not raise even with zero subscribers.
        await bus.publish("run-a", {"type": "run.status", "data": {}})
        await bus.publish(None, {"type": "agent.status", "data": {}})
        assert bus.subscriber_count("run-a") == 0
        assert bus.subscriber_count() == 0

    _run(scenario())


def test_publish_stamps_ts_when_absent():
    async def scenario():
        bus = EventBus()
        q = bus.subscribe("run-a")
        await bus.publish("run-a", {"type": "run.status", "data": {}})
        evt = q.get_nowait()
        assert "ts" in evt and isinstance(evt["ts"], str) and evt["ts"]

    _run(scenario())


def test_publish_preserves_explicit_ts():
    async def scenario():
        bus = EventBus()
        q = bus.subscribe("run-a")
        await bus.publish(
            "run-a", {"type": "run.status", "ts": "2026-06-07T00:00:00", "data": {}}
        )
        assert q.get_nowait()["ts"] == "2026-06-07T00:00:00"

    _run(scenario())


def test_full_queue_drops_event_without_raising():
    async def scenario():
        bus = EventBus(maxsize=1)
        q = bus.subscribe("run-a")
        # First fills the queue; second must be dropped silently.
        await bus.publish("run-a", {"type": "run.status", "data": {"n": 1}})
        await bus.publish("run-a", {"type": "run.status", "data": {"n": 2}})
        assert q.qsize() == 1
        assert q.get_nowait()["data"]["n"] == 1

    _run(scenario())


def test_unsubscribe_detaches_queue():
    async def scenario():
        bus = EventBus()
        q = bus.subscribe("run-a")
        assert bus.subscriber_count("run-a") == 1
        bus.unsubscribe("run-a", q)
        assert bus.subscriber_count("run-a") == 0

        # A subsequent publish no longer reaches the detached queue.
        await bus.publish("run-a", {"type": "run.status", "data": {}})
        assert q.empty()

    _run(scenario())


def test_unsubscribe_prunes_empty_run_bucket():
    async def scenario():
        bus = EventBus()
        q = bus.subscribe("run-a")
        assert "run-a" in bus._run_subs
        bus.unsubscribe("run-a", q)
        # Empty bucket removed so abandoned run ids don't accumulate.
        assert "run-a" not in bus._run_subs

    _run(scenario())


def test_unsubscribe_is_idempotent():
    async def scenario():
        bus = EventBus()
        q = bus.subscribe(None)
        bus.unsubscribe(None, q)
        # Second unsubscribe must not raise.
        bus.unsubscribe(None, q)
        # Unsubscribing an unknown run id must not raise.
        bus.unsubscribe("never", q)

    _run(scenario())


def test_module_singleton_is_eventbus():
    assert isinstance(event_bus, EventBus)

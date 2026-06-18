"""
CortexSim — in-process async pub/sub event bus.

Powers the live run stream (SSE) without any external broker. Publishers
(API handlers + orchestrator) call :meth:`EventBus.publish`; subscribers
(the SSE endpoint) call :meth:`EventBus.subscribe` to obtain an
``asyncio.Queue`` they drain in a streaming response.

Design constraints (matching the single-worker uvicorn deployment):
  - **Single process only.** State lives in plain dicts/sets on the loop;
    a second worker would not see these events. This is intentional — the
    UI's 10s poll is the cross-process / restart-safe backstop.
  - **Bounded queues.** Each subscriber queue is size-capped; a slow
    consumer drops events rather than growing memory unboundedly. The poll
    fallback fills any gap.
  - **No-subscriber publishes are no-ops** — fanning out to zero queues is
    fine, so handlers can publish unconditionally.
  - **publish never raises** into the caller's mutation path: a full queue
    is swallowed so a bus hiccup can't 500 the underlying request.

Canonical bus event shape (see ``core/api/events.py`` for the UI translation):

    {
        "type": "run.status" | "run.output" | "result.observed" | "agent.status",
        "run_id": "<id>" | None,
        "ts": "<ISO-8601>",                  # stamped here if absent
        "data": { ...type-specific... },
    }
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime
from typing import Any, Optional


class EventBus:
    """In-process async fan-out with per-run and global subscriptions.

    A subscriber registered for a specific ``run_id`` receives only events
    for that run plus any explicitly-global event; a subscriber registered
    with ``run_id=None`` (the global stream) receives every event.
    """

    def __init__(self, maxsize: int = 1000) -> None:
        # run_id -> set of subscriber queues scoped to that run
        self._run_subs: dict[str, set[asyncio.Queue]] = defaultdict(set)
        # subscribers to the global stream (receive everything)
        self._global_subs: set[asyncio.Queue] = set()
        self._maxsize = maxsize

    async def publish(self, run_id: Optional[str], event: dict[str, Any]) -> None:
        """Fan ``event`` out to every matching subscriber queue.

        Stamps ``ts`` if the caller didn't. Global subscribers always
        receive the event; run-scoped subscribers receive it only when
        ``run_id`` matches their subscription. Full queues are skipped
        (slow-consumer protection) and never raise.
        """
        event.setdefault("ts", datetime.utcnow().isoformat())

        # Snapshot targets so concurrent (un)subscribe during iteration is safe.
        targets: set[asyncio.Queue] = set(self._global_subs)
        if run_id is not None:
            targets |= set(self._run_subs.get(run_id, set()))

        for q in targets:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Slow consumer — drop this event. The UI's poll backstop
                # reconciles state, so a dropped live frame is non-fatal.
                pass

    def subscribe(self, run_id: Optional[str]) -> asyncio.Queue:
        """Register and return a new bounded queue.

        ``run_id=None`` subscribes to the global stream (all events);
        a concrete ``run_id`` scopes the subscription to that run.
        """
        q: asyncio.Queue = asyncio.Queue(maxsize=self._maxsize)
        if run_id is None:
            self._global_subs.add(q)
        else:
            self._run_subs[run_id].add(q)
        return q

    def unsubscribe(self, run_id: Optional[str], q: asyncio.Queue) -> None:
        """Detach a previously-subscribed queue. Idempotent.

        Empties the per-run bucket from the registry once it has no
        subscribers so abandoned run ids don't accumulate forever.
        """
        if run_id is None:
            self._global_subs.discard(q)
            return
        bucket = self._run_subs.get(run_id)
        if bucket is not None:
            bucket.discard(q)
            if not bucket:
                self._run_subs.pop(run_id, None)

    def subscriber_count(self, run_id: Optional[str] = None) -> int:
        """Number of live subscribers (for the given run, or global). Test hook."""
        if run_id is None:
            return len(self._global_subs)
        return len(self._run_subs.get(run_id, set()))


# Module-level singleton — imported by the API layer + orchestrator.
event_bus = EventBus()

"""Live runtime log for pull runs: step starts and output while a step runs.

Before this, a beacon posted a step's output once, after the step ended, and
never said which command it ran, so a three-minute step was silent for three
minutes and a finished run could not show what was attempted.

The pull task now carries ``live_output`` (:func:`task_field`). A beacon that
understands it sends two more kinds of ``POST /api/runs/{id}/output`` frame
beside the final per-step frame it has always sent:

``kind: "start"``
    The step is about to run. Carries ``command``, ``identity``, ``index`` and
    ``total``. Recorded in ``step_starts`` and published as ``run.step``.
``kind: "chunk"``
    Output produced while the step runs, with a rising ``seq``. Published as
    ``run.output`` with ``live: true`` and kept in :data:`tail` until the
    step's final frame arrives.

Two rules hold for both:

* A live frame is never appended to ``Run.output``. The stored transcript is
  built only from frames with no ``kind``, so it is the same with or without
  live frames, and a beacon that cannot reach SimCore mid-step loses nothing.
* A live frame never creates or touches a ``step_outcomes`` row.
  ``StepOutcome.started_at`` means "the step had finished by then";
  ``engine.beacon_lost.pick_severed`` and the silent-row window depend on it.

The tail is process memory. It is gone after a restart and is not evidence;
``Run.output`` is.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable, Optional

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from events import event_bus
from models import Run, StepStart

logger = logging.getLogger("cortexsim.engine.live_output")

#: How often the beacon flushes buffered output, in milliseconds.
FLUSH_MS = 1000
#: A chunk is sent early once this much output is waiting.
MAX_CHUNK_BYTES = 16384
#: The beacon stops sending live chunks for a step after this many bytes, and
#: the tail keeps at most this many per step. The final frame is not affected.
MAX_STEP_BYTES = 262144
#: A recorded command is cut to this many characters.
COMMAND_MAX_CHARS = 65536

KIND_START = "start"
KIND_CHUNK = "chunk"


def task_field() -> dict[str, int]:
    """The ``live_output`` block every pull task carries.

    A beacon that predates it drops the unknown key and behaves as before.
    """
    return {"flush_ms": FLUSH_MS, "max_chunk_bytes": MAX_CHUNK_BYTES,
            "max_step_bytes": MAX_STEP_BYTES}


class LiveTail:
    """What each running step has printed so far, per run, in memory.

    Bounded three ways: :data:`MAX_STEP_BYTES` per step (the newest bytes are
    kept), ``max_steps`` open steps per run and ``max_runs`` runs (the oldest
    is dropped). A step's tail is dropped when its final frame arrives and a
    run's when the run ends.
    """

    def __init__(self, max_step_bytes: int = MAX_STEP_BYTES, max_runs: int = 64,
                 max_steps: int = 64) -> None:
        self._max_step_bytes = max_step_bytes
        self._max_runs = max_runs
        self._max_steps = max_steps
        # run_id -> {"steps": {step_id: {"text", "seq", "truncated"}},
        #            "closed": {step ids whose final frame has arrived}}.
        # Dicts keep insertion order, which is what "oldest" means below.
        self._runs: dict[str, dict[str, Any]] = {}

    def _run(self, run_id: str) -> dict[str, Any]:
        run = self._runs.get(run_id)
        if run is None:
            if len(self._runs) >= self._max_runs:
                self._runs.pop(next(iter(self._runs)))
            run = self._runs[run_id] = {"steps": {}, "closed": set()}
        return run

    def open_step(self, run_id: str, step_id: str) -> None:
        """A step is starting: its tail starts empty and accepts chunks."""
        run = self._run(run_id)
        run["closed"].discard(step_id)
        run["steps"].pop(step_id, None)

    def feed(self, run_id: str, step_id: str, text: str, seq: Optional[int]) -> bool:
        """Add a chunk. False when the step's final frame has already arrived."""
        run = self._run(run_id)
        if step_id in run["closed"]:
            return False
        steps = run["steps"]
        entry = steps.get(step_id)
        if entry is None:
            if len(steps) >= self._max_steps:
                steps.pop(next(iter(steps)))
            entry = steps[step_id] = {"text": "", "seq": None, "truncated": False}
        joined = (entry["text"] + text).encode("utf-8")
        if len(joined) > self._max_step_bytes:
            joined = joined[-self._max_step_bytes:]
            entry["truncated"] = True
        entry["text"] = joined.decode("utf-8", "ignore")
        if seq is not None:
            entry["seq"] = seq
        return True

    def close_step(self, run_id: str, step_id: str) -> None:
        """The step's final frame arrived: drop its tail and refuse late chunks.

        A run that never sent a live frame holds nothing here and stays that way.
        """
        run = self._runs.get(run_id)
        if run is not None:
            run["steps"].pop(step_id, None)
            run["closed"].add(step_id)

    def drop_run(self, run_id: str) -> None:
        """The run ended: drop everything held for it. Idempotent."""
        self._runs.pop(run_id, None)

    def view(self, run_id: str) -> list[dict[str, Any]]:
        run = self._runs.get(run_id)
        return [{"step_id": sid, **entry} for sid, entry in (run["steps"] if run else {}).items()]


#: The one tail, shared by the ``/output`` route, ``GET /api/runs/{id}`` and
#: the orchestrator's end-of-run cleanup.
tail = LiveTail()


async def _publish(run_id: str, event: dict[str, Any]) -> None:
    try:
        await event_bus.publish(run_id, event)
    except Exception:  # pragma: no cover - a bus fault must not fail the POST
        logger.exception("event_bus publish failed run_id=%s", run_id)


async def handle_frame(
    db: AsyncSession, run_id: str, body: Any, *,
    is_terminal: Callable[[Any], bool], not_found: Callable[[str], Exception],
) -> dict[str, Any]:
    """Accept one live ``/output`` frame (``body.kind`` is set).

    ``is_terminal`` and ``not_found`` are the run API's own definitions, passed
    in so there is one of each.
    """
    row = (await db.execute(
        select(Run.status, Run.completed_at, Run.open_tasks).where(Run.run_id == run_id)
    )).first()
    if row is None:
        raise not_found(run_id)
    step_id = body.step_id or ""
    reply: dict[str, Any] = {"status": "ok", "run_id": run_id, "live": True, "kind": body.kind}

    if body.kind == KIND_START:
        started_at = datetime.utcnow()
        command = (body.command or "")[:COMMAND_MAX_CHARS]
        await db.execute(
            sqlite_insert(StepStart)
            .values(run_id=run_id, step_id=step_id, command=command,
                    identity=body.identity, started_at=started_at)
            .on_conflict_do_nothing(index_elements=["run_id", "step_id"])
        )
        await db.commit()
        if not is_terminal(row):
            tail.open_step(run_id, step_id)
        await _publish(run_id, {
            "type": "run.step", "run_id": run_id,
            "data": {"step_id": step_id, "phase": "start", "command": command,
                     "identity": body.identity, "index": body.index, "total": body.total,
                     "started_at": started_at.isoformat()},
        })
        logger.info("step start run_id=%s step=%s", run_id, step_id)
        return reply

    if body.kind != KIND_CHUNK or not body.output:
        # A kind this build does not know, or an empty chunk: accepted, and
        # (because it said it is not a final frame) not stored.
        return {**reply, "dropped": True}
    # The beacon sends at most MAX_CHUNK_BYTES; anything far larger is cut to
    # what the tail would keep of it anyway.
    text = body.output[-MAX_STEP_BYTES:]
    if is_terminal(row) or not tail.feed(run_id, step_id, text, body.seq):
        # The run has ended, or this step's final frame is already stored.
        return {**reply, "dropped": True}
    await _publish(run_id, {
        "type": "run.output", "run_id": run_id,
        "data": {"step_id": step_id, "chunk": text, "live": True, "seq": body.seq},
    })
    return reply


async def run_view(db: AsyncSession, run_id: str, *, in_flight: bool) -> dict[str, Any]:
    """What ``GET /api/runs/{id}`` adds: the recorded step starts, and while
    the run is in flight the live tail."""
    rows = (await db.execute(
        select(StepStart).where(StepStart.run_id == run_id)
        .order_by(StepStart.started_at, StepStart.id))).scalars().all()
    out: dict[str, Any] = {"step_starts": [{
        "step_id": r.step_id, "command": r.command, "identity": r.identity,
        "started_at": r.started_at.isoformat() if r.started_at else None,
    } for r in rows]}
    if in_flight:
        out["live_tail"] = tail.view(run_id)
    else:
        tail.drop_run(run_id)
    return out

"""
CortexSim API — Server-Sent Events (SSE) live run stream.

Endpoints:
  GET /api/events                  — global stream (all runs + agent status)
  GET /api/runs/{run_id}/events    — single-run stream (scoped + global)

Both return ``text/event-stream`` and emit one SSE frame per event in the
exact shape the UI consumer (``ui/src/components/console/useRunEventStream.js``)
parses with ``EventSource.onmessage`` + ``JSON.parse``:

    { id, timestamp, level, stepIndex, message, synthetic }

with ``level ∈ info | warn | error | detect | step``. The canonical bus
event (``core/events.py``) carries a richer ``{type, run_id, ts, data}``
shape; :func:`_to_ui_event` is the pure translation into the UI shape so
live and poll-fallback streams render identically.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, AsyncIterator, Optional

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from events import event_bus

logger = logging.getLogger("cortexsim.api.events")

router = APIRouter(tags=["events"])

# Heartbeat cadence — a comment frame every N seconds keeps proxies from
# closing an idle stream and lets the client notice a dead connection.
_KEEPALIVE_SECONDS = 15.0


# ---------------------------------------------------------------------------
# Bus event → UI event translation (pure, unit-testable)
# ---------------------------------------------------------------------------


def _step_index(step_id: Optional[str]) -> Optional[int]:
    """Map a step id like ``"step-02"`` to its 0-based index (``1``).

    The UI renders ``s{stepIndex+1}`` so a 1-based ``step-NN`` id maps to
    ``NN - 1``. Returns ``None`` for missing / unparseable ids.
    """
    if not step_id or not isinstance(step_id, str):
        return None
    tail = step_id.rsplit("-", 1)[-1]
    try:
        n = int(tail)
    except (ValueError, TypeError):
        return None
    return max(n - 1, 0)


def _to_ui_event(bus_evt: dict[str, Any]) -> dict[str, Any]:
    """Translate a canonical bus event into the UI's flat SSE event shape.

    Always returns a dict with ``{id, timestamp, level, stepIndex,
    message, synthetic}``. ``synthetic`` is always ``False`` from the live
    endpoint — the UI's synthetic projector owns the poll-fallback path.
    """
    etype = bus_evt.get("type")
    data = bus_evt.get("data") or {}
    ts = bus_evt.get("ts")

    level = "info"
    message = ""
    step_index: Optional[int] = None

    if etype == "run.status":
        status = data.get("status", "?")
        level = "error" if status == "failed" else "info"
        step_id = data.get("step_id")
        step_index = _step_index(step_id)
        message = f"run {status}"
        if step_id:
            message += f" · {step_id}"
    elif etype == "run.output":
        level = "info"
        step_index = _step_index(data.get("step_id"))
        message = data.get("chunk", "")
    elif etype == "run.step":
        # Synthetic-style step boundary marker (see 3.3 "step" level note).
        level = "step"
        step_index = _step_index(data.get("step_id"))
        message = data.get("message", "")
    elif etype == "result.observed":
        level = "detect"
        plane = data.get("plane", "?")
        mttd = data.get("mttd_seconds")
        mttd_str = f"{mttd}s" if mttd is not None else "?"
        message = f"detection: {plane} · MTTD {mttd_str}"
    elif etype == "agent.status":
        status = data.get("status", "?")
        level = "warn" if status in ("stale", "offline") else "info"
        message = f"agent {data.get('agent_id', '?')} {status}"
    else:
        # Unknown bus type — surface it rather than dropping it silently.
        message = json.dumps(data) if data else str(etype)

    return {
        "id": str(uuid.uuid4()),
        "timestamp": ts,
        "level": level,
        "stepIndex": step_index,
        "message": message,
        "synthetic": False,
    }


# ---------------------------------------------------------------------------
# SSE generator
# ---------------------------------------------------------------------------


async def _stream(run_id: Optional[str]) -> AsyncIterator[str]:
    """Yield SSE frames for a subscription until the client disconnects.

    Sends an initial comment so ``EventSource.onopen`` fires promptly, then
    drains the subscriber queue, translating each bus event to the UI shape.
    A keepalive comment is emitted on idle so proxies keep the stream open.
    """
    q = event_bus.subscribe(run_id)
    try:
        yield ": connected\n\n"
        while True:
            try:
                bus_evt = await asyncio.wait_for(q.get(), timeout=_KEEPALIVE_SECONDS)
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
                continue
            ui_evt = _to_ui_event(bus_evt)
            yield f"id: {ui_evt['id']}\ndata: {json.dumps(ui_evt)}\n\n"
    finally:
        event_bus.unsubscribe(run_id, q)


_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",   # disable nginx proxy buffering
    "Connection": "keep-alive",
}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/runs/{run_id}/events")
async def run_events(run_id: str):
    """SSE stream scoped to a single run (plus any global events)."""
    logger.info("sse subscribe run_id=%s", run_id)
    return StreamingResponse(
        _stream(run_id),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.get("/events")
async def global_events():
    """SSE stream of every event across all runs + agent status."""
    logger.info("sse subscribe global")
    return StreamingResponse(
        _stream(None),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )

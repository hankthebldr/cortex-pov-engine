"""Unit tests for the SSE bus->UI event translation (core/api/events.py).

The SSE endpoint must emit events in the exact shape the UI consumer
(``ui/src/components/console/useRunEventStream.js``) parses:

    { id, timestamp, level, stepIndex, message, synthetic }

with ``level ∈ info | warn | error | detect | step``. These tests pin the
pure translator ``_to_ui_event`` (per-bus-type level/message/stepIndex) and
the ``_step_index`` helper, plus the SSE generator framing — without opening
a real long-lived HTTP stream (full SSE over the test client is flaky).
"""
from __future__ import annotations

import asyncio
import json

import pytest

from api.events import _step_index, _to_ui_event, _stream
from events import event_bus

_UI_LEVELS = {"info", "warn", "error", "detect", "step"}


# ---------------------------------------------------------------------------
# _step_index
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "step_id,expected",
    [
        ("step-01", 0),
        ("step-02", 1),
        ("step-10", 9),
        ("step-00", 0),     # clamped at 0, never negative
        (None, None),
        ("", None),
        ("step-foo", None),  # unparseable tail
        ("nodash", None),    # int("nodash") fails
    ],
)
def test_step_index_mapping(step_id, expected):
    assert _step_index(step_id) == expected


def test_step_index_non_string_returns_none():
    assert _step_index(42) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _to_ui_event — common envelope invariants
# ---------------------------------------------------------------------------


def test_ui_event_always_has_required_keys_and_synthetic_false():
    ui = _to_ui_event({"type": "run.status", "ts": "T", "data": {"status": "running"}})
    assert set(ui.keys()) == {"id", "timestamp", "level", "stepIndex", "message", "synthetic"}
    assert ui["synthetic"] is False
    assert ui["timestamp"] == "T"
    assert ui["level"] in _UI_LEVELS
    assert isinstance(ui["id"], str) and ui["id"]


# ---------------------------------------------------------------------------
# _to_ui_event — per bus type
# ---------------------------------------------------------------------------


def test_run_status_running_is_info():
    ui = _to_ui_event({"type": "run.status", "ts": "T", "data": {"status": "running"}})
    assert ui["level"] == "info"
    assert "run running" in ui["message"]
    assert ui["stepIndex"] is None


def test_run_status_failed_is_error():
    ui = _to_ui_event({"type": "run.status", "ts": "T", "data": {"status": "failed"}})
    assert ui["level"] == "error"
    assert "failed" in ui["message"]


def test_run_status_aborted_is_info_level():
    ui = _to_ui_event({"type": "run.status", "ts": "T", "data": {"status": "aborted"}})
    assert ui["level"] == "info"
    assert "aborted" in ui["message"]


def test_run_status_with_step_id_includes_step_in_message_and_index():
    ui = _to_ui_event(
        {"type": "run.status", "ts": "T", "data": {"status": "running", "step_id": "step-03"}}
    )
    assert ui["stepIndex"] == 2
    assert "step-03" in ui["message"]


def test_run_output_carries_chunk_and_step_index():
    ui = _to_ui_event(
        {"type": "run.output", "ts": "T", "data": {"step_id": "step-02", "chunk": "hello\n"}}
    )
    assert ui["level"] == "info"
    assert ui["message"] == "hello\n"
    assert ui["stepIndex"] == 1


def test_run_step_boundary_is_step_level():
    ui = _to_ui_event(
        {"type": "run.step", "ts": "T", "data": {"step_id": "step-02", "message": "step 2/3"}}
    )
    assert ui["level"] == "step"
    assert ui["stepIndex"] == 1
    assert ui["message"] == "step 2/3"


def test_result_observed_is_detect_with_plane_and_mttd():
    ui = _to_ui_event(
        {
            "type": "result.observed",
            "ts": "T",
            "data": {"result_id": 7, "observed": True, "plane": "EDR", "mttd_seconds": 12.3},
        }
    )
    assert ui["level"] == "detect"
    assert "EDR" in ui["message"]
    assert "12.3s" in ui["message"]


def test_result_observed_handles_missing_mttd():
    ui = _to_ui_event(
        {"type": "result.observed", "ts": "T", "data": {"plane": "NDR", "mttd_seconds": None}}
    )
    assert ui["level"] == "detect"
    assert "NDR" in ui["message"]
    assert "?" in ui["message"]


def test_agent_status_online_is_info():
    ui = _to_ui_event(
        {"type": "agent.status", "ts": "T", "data": {"agent_id": "jb-1", "status": "online"}}
    )
    assert ui["level"] == "info"
    assert "jb-1" in ui["message"]
    assert "online" in ui["message"]


@pytest.mark.parametrize("status", ["stale", "offline"])
def test_agent_status_stale_or_offline_is_warn(status):
    ui = _to_ui_event(
        {"type": "agent.status", "ts": "T", "data": {"agent_id": "jb-1", "status": status}}
    )
    assert ui["level"] == "warn"


def test_unknown_bus_type_does_not_crash_and_stays_valid_level():
    ui = _to_ui_event({"type": "mystery", "ts": "T", "data": {"x": 1}})
    assert ui["level"] in _UI_LEVELS
    # Surfaces the payload rather than dropping it silently.
    assert ui["message"]


def test_to_ui_event_tolerates_missing_data_key():
    ui = _to_ui_event({"type": "run.status", "ts": "T"})
    assert ui["level"] in _UI_LEVELS
    assert ui["synthetic"] is False


# ---------------------------------------------------------------------------
# _stream generator — framing (connected banner + a translated event frame)
# ---------------------------------------------------------------------------


def test_stream_emits_connected_then_translated_event():
    """Drive the async SSE generator directly: first yield is the connect
    comment, then a published bus event arrives as a UI-shaped data frame."""

    async def scenario():
        gen = _stream("run-sse")
        # First frame is the connect comment that triggers EventSource.onopen.
        first = await gen.__anext__()
        assert first == ": connected\n\n"

        # Publish an event for this run; the generator's next frame is it.
        await event_bus.publish(
            "run-sse",
            {"type": "run.status", "data": {"status": "running"}},
        )
        frame = await asyncio.wait_for(gen.__anext__(), timeout=2.0)

        assert frame.startswith("id: ")
        # The data: line carries valid JSON in the UI shape.
        data_line = next(
            ln for ln in frame.splitlines() if ln.startswith("data: ")
        )
        payload = json.loads(data_line[len("data: "):])
        assert payload["level"] == "info"
        assert "run running" in payload["message"]
        assert payload["synthetic"] is False

        # Closing the generator unsubscribes the queue (finally block).
        await gen.aclose()
        assert event_bus.subscriber_count("run-sse") == 0

    asyncio.run(scenario())

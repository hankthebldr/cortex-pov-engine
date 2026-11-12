"""run_timeframe: the absolute window a run's verification queries."""
from __future__ import annotations

import calendar
import time
from datetime import datetime

import pytest

from connectors.verify_window import LEAD_SECONDS, run_timeframe


def _ms(*args) -> int:
    return calendar.timegm(datetime(*args).timetuple()) * 1000


STARTED = datetime(2026, 11, 11, 10, 0, 0)
COMPLETED = datetime(2026, 11, 11, 10, 2, 0)


def test_window_spans_run_plus_correlation_window():
    tf = run_timeframe(STARTED, COMPLETED, 3600, now=datetime(2026, 11, 11, 14, 0, 0))
    assert LEAD_SECONDS == 300
    assert tf == {"from": _ms(2026, 11, 11, 9, 55), "to": _ms(2026, 11, 11, 11, 2)}


def test_to_is_clamped_to_now():
    tf = run_timeframe(STARTED, COMPLETED, 3600, now=datetime(2026, 11, 11, 10, 30, 0))
    assert tf["to"] == _ms(2026, 11, 11, 10, 30)


def test_no_completed_at_uses_start_plus_window():
    tf = run_timeframe(STARTED, None, 3600, now=datetime(2026, 11, 11, 14, 0, 0))
    assert tf["to"] == _ms(2026, 11, 11, 11, 0)


@pytest.fixture
def la_tz(monkeypatch):
    monkeypatch.setenv("TZ", "America/Los_Angeles")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def test_window_epoch_is_utc_not_host_local(la_tz):
    tf = run_timeframe(STARTED, COMPLETED, 3600, now=datetime(2026, 11, 11, 14, 0, 0))
    assert tf == {"from": _ms(2026, 11, 11, 9, 55), "to": _ms(2026, 11, 11, 11, 2)}

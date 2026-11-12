"""The absolute time window a run's Tier-2 verification queries.

Verification used to ask for "the last hour from now". A run verified two hours
after it executed then queried a window that no longer contained its events, got
zero rows, and resolved ``fail`` -- an infrastructure delay reported as a claim
about the customer's coverage. The window now belongs to the run.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from connectors.base import coerce_utc

#: Slack before ``started_at`` for clock skew and ingestion-time stamping. It
#: is also the shortest tail after the run: a 10 s correlation window leaves no
#: room for log-generation delay or skew, so ``to`` gets at least this much.
LEAD_SECONDS = 300

#: Why a push run has no window of its own. It reaches ``staged`` when its
#: bundle is generated; the bundle runs offline later and SimCore never learns
#: when, so a window anchored on staging would miss every event. A push run
#: keeps the relative lookback and records this reason instead.
PUSH_WINDOW_REASON = "push_execution_time_unknown"


def _epoch_ms(naive_utc: datetime) -> int:
    # Naive UTC -> epoch. ``naive.timestamp()`` would read it as host-local.
    return int(naive_utc.replace(tzinfo=timezone.utc).timestamp() * 1000)


def run_timeframe(started_at: datetime, completed_at: Optional[datetime],
                  window_seconds: int, *, now: datetime) -> dict[str, int]:
    """``{"from": ms, "to": ms}`` covering the run plus its correlation window
    (never less than :data:`LEAD_SECONDS` after the run), clamped to ``now``."""
    started = coerce_utc(started_at)
    end_anchor = coerce_utc(completed_at) or started
    now_utc = coerce_utc(now)
    start = started - timedelta(seconds=LEAD_SECONDS)
    tail = max(int(window_seconds), LEAD_SECONDS)
    end = min(end_anchor + timedelta(seconds=tail), now_utc)
    end = max(end, start)
    return {"from": _epoch_ms(start), "to": _epoch_ms(end)}

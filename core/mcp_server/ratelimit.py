"""Process-local token bucket over tenant-class tools (spec section 7).

The per-tenant daily ledger already bounds every path; this only caps the
burst rate of one MCP process (``CORTEXSIM_MCP_MAX_TENANT_CALLS_PER_MINUTE``).
"""
from __future__ import annotations

import math
import time
from typing import Callable


class TokenBucket:
    """``per_minute`` tokens, refilled continuously, capacity ``per_minute``."""

    def __init__(self, per_minute: int, *, clock: Callable[[], float] = time.monotonic) -> None:
        if per_minute < 1:
            raise ValueError("per_minute must be >= 1")
        self._capacity = float(per_minute)
        self._rate = per_minute / 60.0  # tokens per second
        self._clock = clock
        self._tokens = self._capacity
        self._stamp = clock()

    def acquire(self) -> float:
        """``0.0`` when a token was taken, else ``retry_after_seconds`` (ceil, at least 1)."""
        now = self._clock()
        elapsed = max(0.0, now - self._stamp)
        self._stamp = now
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return 0.0
        return float(max(1, math.ceil((1.0 - self._tokens) / self._rate)))

# core/integrations/xsiam/ledger.py
"""Process-wide, per-tenant XQL query ledger and quota circuit breaker.

The per-sweep budget in ``connectors/service.py`` is *per invocation*: it caps
one sweep at 50 queries and then dies with the call. That leaves three holes,
all of which point at the same customer-visible failure — a retry storm against
a metered resource the customer's own analysts share:

  1. ``POST /api/runs/{id}/verify`` builds a **fresh** budget on every call, so
     a UI polling loop, a shell ``while``, or an impatient DC issues 50 more
     each time.
  2. A tripped ``XsiamQuotaError`` breaker died with the request; the next call
     started clean and hit the already-429'd tenant again.
  3. Nothing bounded the total across paths — sweep, manual verify and
     assertion runs each accounted separately.

This ledger is the outer bound that does not reset per call. It is in-process
state (a module singleton keyed by integration name), which is the right scope
for a single-container SimCore and is honest about its limit: **it does not
survive a restart and does not coordinate across replicas.** A restart-proof
budget belongs in the DB, and that is a migration this change does not own.
Under-counting after a restart is the safe direction — the per-sweep budget and
the tenant's own 429 both still apply.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Optional

logger = logging.getLogger("cortexsim.xsiam.ledger")


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


def tenant_ledger_key(base_url: str) -> str:
    """THE ledger key for a tenant, derived from its base URL.

    Public because more than one surface needs it and they MUST agree. The
    health component used to snapshot the ledger by INTEGRATION NAME while the
    budget charged and tripped it by tenant HOST, so a breaker could be open
    while ``/api/health`` reported ``breaker_open: false`` with a full quota.
    """
    return str(base_url or "").split("://")[-1].split("/")[0] or "default"


class QuotaBreakerOpen(RuntimeError):
    """The breaker is open for this tenant; no path may query until it closes.

    Raised INTO the verifier's runner, which catches any runner exception and
    records ``pending`` — never ``fail``. A budget CortexSim spent is not
    evidence about the customer's detections.
    """

    def __init__(self, tenant: str, retry_after_seconds: int) -> None:
        super().__init__(
            f"XQL quota breaker open for tenant '{tenant}'; "
            f"retry in {retry_after_seconds}s"
        )
        self.tenant = tenant
        self.retry_after_seconds = retry_after_seconds


class DailyBudgetExhausted(RuntimeError):
    """The rolling-day query ceiling for this tenant is spent."""

    def __init__(self, tenant: str, limit: int) -> None:
        super().__init__(
            f"tenant '{tenant}' has spent its {limit}-query daily budget; "
            f"raise CORTEXSIM_XSIAM_MAX_QUERIES_PER_DAY or wait for the window to roll"
        )
        self.tenant = tenant
        self.limit = limit


class RequestBudgetExhausted(RuntimeError):
    """The per-minute request bucket for this tenant is empty."""

    def __init__(self, tenant: str, retry_after_seconds: int) -> None:
        super().__init__(
            f"tenant '{tenant}' request budget exhausted; "
            f"retry in {retry_after_seconds}s"
        )
        self.tenant = tenant
        self.retry_after_seconds = retry_after_seconds


@dataclass
class _TenantLedger:
    queries: list[datetime] = field(default_factory=list)
    breaker_open_until: Optional[datetime] = None
    trips: int = 0

    def prune(self, now: datetime) -> None:
        cutoff = now - timedelta(days=1)
        self.queries = [q for q in self.queries if q >= cutoff]


class TenantQuotaLedger:
    """Per-tenant rolling-day counter + breaker. Thread-safe; process-scoped."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._lock = threading.Lock()
        self._by_tenant: dict[str, _TenantLedger] = {}
        self._clock = clock
        #: tenant -> (per_minute, bucket); rebuilt when per_minute changes.
        self._buckets: dict[str, tuple[int, TokenBucket]] = {}

    # ── read ────────────────────────────────────────────────────────────

    def snapshot(self, tenant: str, *, limit: int,
                 now: Optional[datetime] = None) -> dict[str, object]:
        """Everything the DC needs to answer "have I burned their quota?"."""
        now = now or datetime.utcnow()
        with self._lock:
            led = self._by_tenant.get(tenant)
            if led is None:
                return {"tenant": tenant, "queries_last_24h": 0, "limit": limit,
                        "remaining": limit, "breaker_open": False,
                        "breaker_open_until": None, "retry_after_seconds": 0,
                        "trips": 0}
            led.prune(now)
            open_until = led.breaker_open_until
            is_open = bool(open_until and now < open_until)
            return {
                "tenant": tenant,
                "queries_last_24h": len(led.queries),
                "limit": limit,
                "remaining": max(0, limit - len(led.queries)),
                "breaker_open": is_open,
                "breaker_open_until": open_until.isoformat() if open_until else None,
                "retry_after_seconds": (int((open_until - now).total_seconds())
                                        if is_open else 0),
                "trips": led.trips,
            }

    # ── write ───────────────────────────────────────────────────────────

    def check(self, tenant: str, *, limit: int, now: Optional[datetime] = None) -> None:
        """Raise if this tenant may not be queried right now. Counts nothing."""
        now = now or datetime.utcnow()
        with self._lock:
            led = self._by_tenant.setdefault(tenant, _TenantLedger())
            if led.breaker_open_until and now < led.breaker_open_until:
                raise QuotaBreakerOpen(
                    tenant, int((led.breaker_open_until - now).total_seconds()))
            led.prune(now)
            if limit and len(led.queries) >= limit:
                raise DailyBudgetExhausted(tenant, limit)

    def charge(self, tenant: str, *, limit: int, now: Optional[datetime] = None) -> None:
        """Check-then-count one query. Call BEFORE issuing it."""
        now = now or datetime.utcnow()
        self.check(tenant, limit=limit, now=now)
        with self._lock:
            self._by_tenant.setdefault(tenant, _TenantLedger()).queries.append(now)

    def acquire_request(self, tenant: str, *, per_minute: int,
                        now: Optional[datetime] = None) -> None:
        """Take one request token for this tenant, or raise.

        Breaker first (``QuotaBreakerOpen``), then the per-minute bucket
        (``RequestBudgetExhausted``). Counts nothing against the daily query
        ledger: that is ``charge``'s job.
        """
        now = now or datetime.utcnow()
        with self._lock:
            led = self._by_tenant.get(tenant)
            if led and led.breaker_open_until and now < led.breaker_open_until:
                raise QuotaBreakerOpen(
                    tenant, int((led.breaker_open_until - now).total_seconds()))
            entry = self._buckets.get(tenant)
            if entry is None or entry[0] != per_minute:
                entry = (per_minute, TokenBucket(per_minute, clock=self._clock))
                self._buckets[tenant] = entry
            wait = entry[1].acquire()
        if wait > 0:
            raise RequestBudgetExhausted(tenant, max(1, int(math.ceil(wait))))

    def trip(self, tenant: str, *, cooldown_seconds: int,
             now: Optional[datetime] = None) -> None:
        """Open the breaker after the tenant reported a quota failure."""
        now = now or datetime.utcnow()
        with self._lock:
            led = self._by_tenant.setdefault(tenant, _TenantLedger())
            led.breaker_open_until = now + timedelta(seconds=cooldown_seconds)
            led.trips += 1
        logger.warning(
            "XQL quota breaker OPEN for tenant '%s' for %ds — every path "
            "(sweep, /verify, assertions) will stop querying and resolve pending",
            tenant, cooldown_seconds,
        )

    def reset(self, tenant: Optional[str] = None) -> None:
        """Clear state. Test hook and an operator escape hatch."""
        with self._lock:
            if tenant is None:
                self._by_tenant.clear()
                self._buckets.clear()
            else:
                self._by_tenant.pop(tenant, None)
                self._buckets.pop(tenant, None)


#: The process singleton. Import this, do not construct another — a second
#: instance is a second budget, which is the thing this module exists to stop.
ledger = TenantQuotaLedger()

"""Env-backed tunables for the measurement loop's quota discipline.

**These belong in ``core/config.py`` next to CORTEXSIM_AUTO_VERIFY_*.** They are
here because this change does not own that file. Each is read fresh from the
environment on every call rather than snapshotted at import, so a test can
monkeypatch ``os.environ`` and so an operator editing ``.env`` does not have to
reason about import order. Reading an int env var is not a hot path — the
callers are a 300-second sweep and an operator-triggered endpoint.

Every knob below exists to bound what CortexSim costs the CUSTOMER's tenant.
The verify sweep already had a budget, an attempt cap, exponential backoff and a
circuit breaker; auto-reconcile — the loop that runs by default when
CORTEXSIM_AUTO_RECONCILE is on — had none of the four, and re-POSTed every run
in a 24 h window every 300 s forever. At the default cadence that is 288
sweeps/day per run, unbounded, with the DC unaware.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger("cortexsim.connectors.tuning")

# ── auto-reconcile (F-05) ───────────────────────────────────────────────────
#: Stop polling a run after this many sweeps found nothing. A detection that has
#: not landed after N attempts is not going to; the run stays `pending` and
#: records WHY it stopped — it is never downgraded to `fail` because polling
#: stopped. Higher than the verify cap (3) because a reconcile is one cheap POST.
AUTO_RECONCILE_MAX_ATTEMPTS = ("CORTEXSIM_AUTO_RECONCILE_MAX_ATTEMPTS", 6)
#: First retry delay, doubled per attempt, capped at 4 h (same curve as verify).
AUTO_RECONCILE_BACKOFF_SECONDS = ("CORTEXSIM_AUTO_RECONCILE_BACKOFF_SECONDS", 900)
#: Hard stop on tenant POSTs in one sweep, regardless of how many runs qualify.
AUTO_RECONCILE_MAX_PULLS_PER_SWEEP = ("CORTEXSIM_AUTO_RECONCILE_MAX_PULLS_PER_SWEEP", 25)

# ── cross-call tenant budget (F-06) ─────────────────────────────────────────
#: Outer bound on XQL queries per tenant per rolling day, across EVERY path —
#: the sweep, POST /verify, and assertion runs. The per-sweep budget (50) is
#: per-invocation, so a UI polling loop or an impatient DC could issue 50 more on
#: every call with a brand-new budget. This is the ceiling that does not reset.
XSIAM_MAX_QUERIES_PER_DAY = ("CORTEXSIM_XSIAM_MAX_QUERIES_PER_DAY", 500)
#: How long a tripped quota breaker stays open before any path may query again.
XSIAM_BREAKER_COOLDOWN = ("CORTEXSIM_XSIAM_BREAKER_COOLDOWN", 900)

#: One budget unit is one ``runner(query)`` call, which is one
#: ``start_xql_query`` PLUS up to 21 ``get_query_results`` polls
#: (``client.run_xql`` defaults: max_wait=30, interval=1.5). So a 50-query sweep
#: budget permits roughly 1,100 HTTP requests. That multiplier is documented
#: rather than silently absorbed, because "we are 22x over the stated budget" is
#: the kind of surprise that gets an API key revoked mid-POV.
HTTP_CALLS_PER_QUERY_WORST_CASE = 22


def _int(spec: "tuple[str, int]", override: Optional[int] = None) -> int:
    """Resolve ``(env_name, default)``, honouring an explicit caller override.

    A malformed value logs and falls back to the default rather than raising:
    a typo in ``.env`` must not take the sweep down, and a sweep that dies is a
    sweep that silently stops measuring.
    """
    if override is not None:
        return int(override)
    name, default = spec
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        logger.warning("%s=%r is not an integer — using default %d", name, raw, default)
        return default


def reconcile_max_attempts(override: Optional[int] = None) -> int:
    return max(1, _int(AUTO_RECONCILE_MAX_ATTEMPTS, override))


def reconcile_backoff_seconds(override: Optional[int] = None) -> int:
    return max(0, _int(AUTO_RECONCILE_BACKOFF_SECONDS, override))


def reconcile_max_pulls_per_sweep(override: Optional[int] = None) -> int:
    return max(0, _int(AUTO_RECONCILE_MAX_PULLS_PER_SWEEP, override))


def xsiam_max_queries_per_day(override: Optional[int] = None) -> int:
    return max(0, _int(XSIAM_MAX_QUERIES_PER_DAY, override))


def xsiam_breaker_cooldown(override: Optional[int] = None) -> int:
    return max(0, _int(XSIAM_BREAKER_COOLDOWN, override))

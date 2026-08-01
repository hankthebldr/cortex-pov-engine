"""
CortexSim verification harness — turns "we saw it" into "it met the bar".

The measurement loop (``connectors/``) answers *whether* a seeded detection was
observed and how long it took. That yields MTTD, but it cannot answer the
question a POV readout actually turns on: **did this test case PASS its
threshold?** This module closes that.

Two layers, deliberately separated:

* **Pure scoring** — :func:`evaluate_threshold` and :func:`score_run` take
  plain data and return verdicts. No I/O, no ORM, trivially testable.
* **Verification** — :func:`verify_run` runs each ``Result.verification_xql``
  against a tenant and records ``kpi_verdict`` / ``verified_at``. The query
  runner is injected, so tests never touch the network.

The cardinal rule here is that **an unscoreable test case must never come back
``pass``**. 57 of the master index's 107 detection-backable test cases carry no
measurable threshold (``Qualitative pass`` or blank), and the corpus binds 37 of
them. A silent pass on one of those produces a green POV readout that means
nothing — strictly worse than reporting no score at all. Those resolve to
``not_applicable`` and are logged.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger("cortexsim.verifier")

# Verdict vocabulary. Shared by Result.kpi_verdict and Run.tc_verdict.
PASS = "pass"
FAIL = "fail"
PENDING = "pending"
NOT_APPLICABLE = "not_applicable"

VERDICTS = (PASS, FAIL, PENDING, NOT_APPLICABLE)

# Threshold values the index uses to mean "a human decides". Anything in here
# is unscoreable by construction.
_UNSCOREABLE = {"", "qualitative pass", "tbd", "n/a", "none"}

# Comparison operators. The index writes these in both ASCII and unicode.
_OPS: dict[str, Callable[[float, float], bool]] = {
    "<=": lambda a, b: a <= b,
    "≤": lambda a, b: a <= b,
    ">=": lambda a, b: a >= b,
    "≥": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    ">": lambda a, b: a > b,
    "=": lambda a, b: a == b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}


# ---------------------------------------------------------------------------
# Pure scoring
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ThresholdVerdict:
    """Outcome of comparing one measured value against one threshold."""

    verdict: str
    detail: str
    actual: Optional[float] = None
    expected: Optional[float] = None
    op: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "detail": self.detail,
            "actual": self.actual,
            "expected": self.expected,
            "op": self.op,
        }


def is_scoreable(threshold: Any) -> bool:
    """False when the threshold cannot be machine-evaluated.

    Accepts the structured form (``{kpi, op, value, unit}``) or the index's raw
    string form (``">60%"``, ``"Qualitative pass"``, ``""``).
    """
    if threshold is None:
        return False
    if isinstance(threshold, str):
        return threshold.strip().lower() not in _UNSCOREABLE
    if isinstance(threshold, dict):
        return threshold.get("op") in _OPS and _as_float(threshold.get("value")) is not None
    return False


def evaluate_threshold(threshold: Any, actual: Optional[float]) -> ThresholdVerdict:
    """Compare a measured value against a structured threshold.

    Returns ``not_applicable`` — never ``pass`` — when the threshold cannot be
    evaluated, and ``pending`` when there is simply nothing measured yet.
    """
    if not is_scoreable(threshold):
        return ThresholdVerdict(
            NOT_APPLICABLE,
            "threshold is not machine-evaluable (qualitative or absent)",
        )
    if not isinstance(threshold, dict):
        return ThresholdVerdict(
            NOT_APPLICABLE,
            f"threshold {threshold!r} is a raw string; structure it as "
            f"{{kpi, op, value, unit}} to score it",
        )

    op = threshold.get("op")
    expected = _as_float(threshold.get("value"))
    if actual is None:
        return ThresholdVerdict(PENDING, "no measured value yet",
                                expected=expected, op=op)

    ok = _OPS[op](actual, expected)
    unit = threshold.get("unit") or ""
    return ThresholdVerdict(
        PASS if ok else FAIL,
        f"{threshold.get('kpi') or 'value'} {actual}{unit} {op} {expected}{unit}",
        actual=actual, expected=expected, op=op,
    )


@dataclass
class RunScore:
    """Aggregate verdict for one run against its scenario's test case."""

    verdict: str
    detail: str
    weighted_pass: float = 0.0
    weighted_total: float = 0.0
    counts: dict[str, int] = field(default_factory=dict)
    primary: Optional[dict[str, Any]] = None
    unscoreable: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "detail": self.detail,
            "weighted_pass": round(self.weighted_pass, 4),
            "weighted_total": round(self.weighted_total, 4),
            "counts": dict(self.counts),
            "primary": self.primary,
            "unscoreable": list(self.unscoreable),
        }


def score_run(
    results: list[Any],
    *,
    threshold: Any = None,
    primary_kpi: Optional[str] = None,
    mttd_seconds: Optional[float] = None,
    measured_value: Optional[float] = None,
    tc_scoreable: bool = True,
) -> RunScore:
    """Aggregate per-result verdicts into one run-level test-case verdict.

    A run PASSES when every weighted result that *can* be scored passed, and the
    scenario-level threshold (if any) clears. Weighting comes from each result's
    ``kpi_contribution``; results without one weight 1.0.

    Precedence is deliberate:

    * any ``fail`` → ``fail``. One failed contribution sinks the test case.
    * else any ``pending`` → ``pending``. Do not call a run passed while
      verification is still outstanding.
    * else at least one ``pass`` → ``pass``.
    * else → ``not_applicable``. Nothing here could be scored, and saying so is
      the honest answer.

    ``measured_value`` supplies the primary KPI directly, for callers that
    measure something the engine cannot derive natively (an assertion probe
    returns a row count, a latency, a coverage percentage). It takes precedence
    over the MTTD path, which stays the default for detection scenarios.

    ``tc_scoreable`` is the index's own verdict on whether the bound test case
    carries a measurable threshold at all. When it is False a machine ``pass``
    would assert something the index never defined, so PASS is clamped to
    ``not_applicable``. FAIL is never clamped — a qualitative claim can be
    disproved even when it cannot be machine-certified.
    """
    counts: dict[str, int] = {PASS: 0, FAIL: 0, PENDING: 0, NOT_APPLICABLE: 0}
    weighted_pass = 0.0
    weighted_total = 0.0
    unscoreable: list[str] = []

    for r in results:
        verdict = getattr(r, "kpi_verdict", None)
        if verdict is None:
            continue
        counts[verdict] = counts.get(verdict, 0) + 1
        if verdict == NOT_APPLICABLE:
            label = getattr(r, "detection_id", None) or getattr(r, "expected_detection", "")
            if label:
                unscoreable.append(str(label))
            continue
        contribution = getattr(r, "kpi_contribution", None) or {}
        weight = _as_float(contribution.get("value") if isinstance(contribution, dict) else None)
        weight = weight if weight and weight > 0 else 1.0
        weighted_total += weight
        if verdict == PASS:
            weighted_pass += weight

    # Scenario-level threshold, measured against the run's primary KPI. MTTD is
    # the only KPI the engine measures natively today; anything else needs a
    # verification_xql to produce a value, which is what the per-result pass
    # above already scored.
    primary: Optional[dict[str, Any]] = None
    if threshold is not None:
        actual = measured_value
        if actual is None and _is_mttd(primary_kpi, threshold):
            actual = mttd_seconds
        tv = evaluate_threshold(threshold, actual)
        primary = tv.to_dict()
        if tv.verdict == FAIL:
            return RunScore(FAIL, f"primary KPI failed: {tv.detail}",
                            weighted_pass, weighted_total, counts, primary, unscoreable)
        if tv.verdict == PENDING:
            # A declared threshold whose KPI never got measured is an OPEN
            # question, not a pass. Falling through here is how a run reported
            # `pass` while `primary.verdict` read `pending` — a green readout
            # for a bar nobody actually cleared.
            return _clamp(RunScore(
                PENDING,
                f"primary KPI declared but not measured: {tv.detail}",
                weighted_pass, weighted_total, counts, primary, unscoreable,
            ), tc_scoreable)

    if counts.get(FAIL):
        detail = f"{counts[FAIL]} detection(s) failed verification"
    elif counts.get(PENDING):
        detail = f"{counts[PENDING]} detection(s) still pending verification"
    elif counts.get(PASS):
        detail = f"{counts[PASS]} detection(s) verified"
    elif primary and primary["verdict"] == PASS:
        return _clamp(RunScore(PASS, f"primary KPI passed: {primary['detail']}",
                               weighted_pass, weighted_total, counts, primary,
                               unscoreable), tc_scoreable)
    else:
        detail = (
            f"nothing scoreable: {counts.get(NOT_APPLICABLE, 0)} detection(s) "
            f"carry no measurable threshold"
        )
        return RunScore(NOT_APPLICABLE, detail, weighted_pass, weighted_total,
                        counts, primary, unscoreable)

    verdict = FAIL if counts.get(FAIL) else (PENDING if counts.get(PENDING) else PASS)
    return _clamp(RunScore(verdict, detail, weighted_pass, weighted_total,
                           counts, primary, unscoreable), tc_scoreable)


def _clamp(score: RunScore, tc_scoreable: bool) -> RunScore:
    """Downgrade a PASS to ``not_applicable`` when the bound test case carries
    no measurable threshold.

    The index scores 91 of its 110 POS rows — and 57 of its 107 detection-backable
    rows — as ``Qualitative pass``. Machine-certifying a pass against a bar the
    index never defined is the exact inflation this harness exists to prevent.
    FAIL is deliberately untouched: you can disprove a qualitative claim.
    """
    if tc_scoreable or score.verdict != PASS:
        return score
    score.verdict = NOT_APPLICABLE
    score.detail = (
        f"{score.detail}; bound test case carries no measurable threshold — "
        f"this is evidence, not a scored pass"
    )
    return score


# ---------------------------------------------------------------------------
# Verification against a tenant
# ---------------------------------------------------------------------------

# A query runner takes an XQL string and returns the number of rows it matched.
# Injected so tests never reach the network.
QueryRunner = Callable[[str], Awaitable[int]]


async def verify_results(
    results: list[Any],
    runner: QueryRunner,
    *,
    expected_rows_min: Callable[[Any], int] | None = None,
) -> dict[str, int]:
    """Run each result's ``verification_xql`` and record its verdict.

    Results without a ``verification_xql`` are left untouched — they are not
    this harness's to score, and stamping them would misrepresent coverage.
    A query that raises marks the result ``pending``, not ``fail``: a tenant
    outage is not a detection failure, and conflating the two would produce
    false red in a customer readout.
    """
    counts: dict[str, int] = {PASS: 0, FAIL: 0, PENDING: 0, NOT_APPLICABLE: 0, "skipped": 0}
    now = datetime.utcnow()

    for r in results:
        xql = getattr(r, "verification_xql", None)
        if not xql:
            counts["skipped"] += 1
            continue

        want = expected_rows_min(r) if expected_rows_min else _expected_rows_min(r)
        try:
            rows = await runner(xql)
        except Exception as exc:  # noqa: BLE001 — a tenant error is not a detection failure
            logger.warning(
                "verification query failed for result=%s detection=%s: %s",
                getattr(r, "id", "?"), getattr(r, "detection_id", "?"), exc,
            )
            r.kpi_verdict = PENDING
            counts[PENDING] += 1
            continue

        r.kpi_verdict = PASS if rows >= want else FAIL
        r.verified_at = now
        counts[r.kpi_verdict] += 1

    return counts


def _expected_rows_min(result: Any) -> int:
    """Row floor for a result's verification query.

    Sourced from the TTP card's ``xql_queries[].expected_rows_min`` when the
    result resolves to one, else the schema default of 1.
    """
    from engine.ttp_catalog import catalog  # noqa: PLC0415

    ttp_ref = getattr(result, "ttp_ref", None)
    raw = catalog.raw(ttp_ref) if ttp_ref else None
    if isinstance(raw, dict):
        for q in (raw.get("detections") or {}).get("xql_queries") or []:
            if isinstance(q, dict) and q.get("purpose") == "validation":
                got = q.get("expected_rows_min")
                if isinstance(got, int):
                    return got
    return 1


async def verify_run(
    db: Any,
    run: Any,
    scenario: Any,
    results: list[Any],
    runner: QueryRunner,
    *,
    expected_rows_min: Callable[[Any], int] | None = None,
    measured_value: Optional[float] = None,
    tc_scoreable: bool = True,
) -> RunScore:
    """Verify every result in a run, then score the run and persist the verdict.

    Returns the :class:`RunScore`; the caller owns the commit boundary in the
    same way ``connectors.service`` does.

    ``tc_scoreable`` should be sourced from the bound index row
    (``uctc_registry.registry.tc(scenario.tc_ref).is_scoreable``) by any caller
    that has it; the default of True preserves the historical behaviour for
    callers that do not.
    """
    counts = await verify_results(results, runner, expected_rows_min=expected_rows_min)

    mttd = [r.mttd_seconds for r in results
            if getattr(r, "mttd_seconds", None) is not None]
    score = score_run(
        results,
        threshold=getattr(scenario, "threshold", None),
        primary_kpi=getattr(scenario, "primary_kpi", None),
        mttd_seconds=(sum(mttd) / len(mttd)) if mttd else None,
        measured_value=measured_value,
        tc_scoreable=tc_scoreable,
    )

    run.tc_verdict = score.verdict
    run.tc_verdict_detail = score.to_dict()

    if score.unscoreable:
        logger.info(
            "run=%s carries %d unscoreable detection(s) — reported "
            "not_applicable, never pass: %s",
            getattr(run, "run_id", "?"), len(score.unscoreable),
            ", ".join(score.unscoreable[:5]),
        )

    logger.info(
        "run=%s verified: verdict=%s (%s) queries=%s",
        getattr(run, "run_id", "?"), score.verdict, score.detail, counts,
    )
    return score


def xsiam_query_runner(client: Any, timeframe: dict[str, Any]) -> QueryRunner:
    """Adapt an XSIAM client into a :data:`QueryRunner` returning a row count."""

    async def _run(query: str) -> int:
        reply = await client.run_xql(query, timeframe)
        if not isinstance(reply, dict):
            return 0
        total = reply.get("number_of_results")
        if isinstance(total, int):
            return total
        return len(reply.get("results") or [])

    return _run


# A scalar runner returns the query's ROWS, not just how many there were.
# ``QueryRunner`` discards every field value, so no latency, ratio or coverage
# KPI is measurable through it — assertions need the values themselves. Both
# forms are injected, so tests never reach the network.
ScalarRunner = Callable[[str], Awaitable[list[dict[str, Any]]]]


def xsiam_rows_runner(client: Any, timeframe: dict[str, Any]) -> ScalarRunner:
    """Adapt an XSIAM client into a :data:`ScalarRunner` returning result rows."""

    async def _run(query: str) -> list[dict[str, Any]]:
        reply = await client.run_xql(query, timeframe)
        if not isinstance(reply, dict):
            return []
        rows = reply.get("results") or []
        return [r for r in rows if isinstance(r, dict)]

    return _run


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _as_float(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _is_mttd(primary_kpi: Optional[str], threshold: Any) -> bool:
    """True when the scenario's primary KPI is the one the engine measures
    natively (time-to-detect), so the run's mean MTTD is the right input."""
    kpi = (primary_kpi or "")
    if isinstance(threshold, dict):
        kpi = threshold.get("kpi") or kpi
    return "mttd" in kpi.lower() or "time to detect" in kpi.lower()

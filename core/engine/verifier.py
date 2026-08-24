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

import contextlib
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


#: The floor below which a row count proves nothing. ``rows >= 0`` is true for
#: every possible tenant response, including a healthy tenant that ingests
#: nothing, an empty dataset, and a query against a dataset the customer does
#: not have. A check that cannot fail cannot pass either — this is the identical
#: rule ``assertions.py``'s A-17 enforces at load time ("this check can never
#: fail and therefore proves nothing"); the scenario verification path simply
#: never had it.
_MIN_MEANINGFUL_ROWS = 1


def _expected_rows_min(result: Any) -> int:
    """Row floor for a result's verification query.

    Sourced from the TTP card's ``xql_queries[].expected_rows_min``, matched on
    the result's OWN ``detection_id``. Matching only on ``purpose == validation``
    returned the card's FIRST validation query regardless of which detection was
    being verified — and for seven cards that first entry is an "MTTD Anchor
    Bookkeeping" query declaring ``expected_rows_min: 0``. Combined with
    ``PASS if rows >= want`` that made 14 of the 34 seeded results carrying a
    verification_xql UNFALSIFIABLE: a healthy tenant returning zero rows scored
    a machine-certified PASS with ``verified_at`` stamped, which is the precise
    claim a DC would repeat to a customer.

    A declared floor of 0 is therefore CLAMPED to 1 rather than honoured. If a
    card genuinely wants to assert absence, that is a different predicate and
    needs its own operator — it is not expressible as a row minimum.
    """
    from engine.ttp_catalog import catalog  # noqa: PLC0415

    ttp_ref = getattr(result, "ttp_ref", None)
    raw = catalog.raw(ttp_ref) if ttp_ref else None
    if not isinstance(raw, dict):
        return _MIN_MEANINGFUL_ROWS

    queries = [
        q for q in ((raw.get("detections") or {}).get("xql_queries") or [])
        if isinstance(q, dict) and q.get("purpose") == "validation"
    ]
    detection_id = getattr(result, "detection_id", None)

    chosen = None
    if detection_id:
        for q in queries:
            if q.get("detection_id") == detection_id:
                chosen = q
                break
    if chosen is None:
        # No per-detection query. Fall back to the card-level floor ONLY when the
        # card declares exactly one validation query — with several, "the first
        # one" is an arbitrary pick that silently scores one detection against
        # another's threshold.
        if len(queries) == 1:
            chosen = queries[0]

    got = chosen.get("expected_rows_min") if chosen else None
    if not isinstance(got, int):
        return _MIN_MEANINGFUL_ROWS
    if got < _MIN_MEANINGFUL_ROWS:
        logger.warning(
            "expected_rows_min=%s on %s/%s cannot fail; clamping to %s",
            got, ttp_ref, detection_id or "?", _MIN_MEANINGFUL_ROWS,
        )
        return _MIN_MEANINGFUL_ROWS
    return got


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


def tc_scoreable_for(tc_ref: Optional[str]) -> bool:
    """Whether the INDEX can score the test case a *scenario* binds.

    The sibling of ``engine.assertions.tc_scoreable``, which answers the same
    question for an ``AssertionSpec``. Both exist because the two callers hold
    different objects (a scenario row vs an assertion spec) and neither owns
    the other; the degradation rule below is the part that must stay identical.

    With no snapshot loaded there is no index claim to clamp against, so this
    returns True — the same degradation ``validate_ref`` makes when it reports
    ``unverified`` rather than rejecting. Clamping on absent data would make
    every run unpassable in a stripped deployment.

    Expect this to return False for 123 of the 162 scenarios: they bind an
    index row whose threshold is literally ``Qualitative pass``. That is the
    point — see :func:`_clamp`.
    """
    from engine.uctc_registry import registry  # noqa: PLC0415

    if not registry.loaded:
        return True
    if not tc_ref:
        return False
    tc = registry.tc(tc_ref)
    return bool(tc and tc.is_scoreable)


def xsiam_query_runner(client: Any, timeframe: dict[str, Any]) -> QueryRunner:
    """Adapt an XSIAM client into a :data:`QueryRunner` returning a row count.

    The unwrapping lives in ``integrations.xsiam.queries`` — deliberately NOT
    inlined here. This function previously carried its own copy that read
    ``len(reply.get("results") or [])``; because XSIAM returns ``results`` as
    ``{"data": [...]}``, a zero-row reply measured as **1** and
    ``verify_results`` scored ``rows(1) >= want(1)`` as **pass** — a detection
    that never fired, reported green. There is now exactly one unwrapper.

    It also does not swallow a shape it does not recognise: ``row_count``
    raises, ``verify_results`` catches any runner exception and records
    ``pending``. An unparseable reply is an open question, not a failed
    detection.
    """
    from integrations.xsiam.queries import row_count  # noqa: PLC0415

    async def _run(query: str) -> int:
        async with _tenant_budget(client):
            return row_count(await client.run_xql(query, timeframe))

    return _run


# A scalar runner returns the query's ROWS, not just how many there were.
# ``QueryRunner`` discards every field value, so no latency, ratio or coverage
# KPI is measurable through it — assertions need the values themselves. Both
# forms are injected, so tests never reach the network.
ScalarRunner = Callable[[str], Awaitable[list[dict[str, Any]]]]


def xsiam_rows_runner(client: Any, timeframe: dict[str, Any]) -> ScalarRunner:
    """Adapt an XSIAM client into a :data:`ScalarRunner` returning result rows.

    Every assertion probe (POS/PLT/AUT — the mechanism that owns 140 of the
    index's open rows) runs through here. The previous inline unwrap iterated
    the ``results`` **dict**, yielding its keys, which all failed the
    ``isinstance(r, dict)`` filter — so this returned ``[]`` unconditionally
    against a healthy tenant. ``XqlRowsProbe`` reads that as a MEASURED row
    count of 0 and evaluates it against the authored floor, so all 18 assertion
    artifacts would have reported **fail**. The A-17 guard cannot catch it: it
    proves the *evaluator* can go red, not that the *runner* can return rows.
    """
    from integrations.xsiam.queries import result_rows  # noqa: PLC0415

    async def _run(query: str) -> list[dict[str, Any]]:
        async with _tenant_budget(client):
            return result_rows(await client.run_xql(query, timeframe))

    return _run


def tenant_ledger_key(base_url: str) -> str:
    """THE ledger key for a tenant, derived from its base URL.

    Public because more than one surface needs it and they MUST agree. The
    health component used to snapshot the ledger by INTEGRATION NAME while this
    module charged and tripped it by tenant HOST, so the two looked at different
    rows: a breaker could be open, every verify degrading to pending, and
    ``/api/health`` would report ``breaker_open: false`` with a full quota. A
    diagnostic surface that cannot see the thing it exists to report is worse
    than not having it.
    """
    return str(base_url or "").split("://")[-1].split("/")[0] or "default"


def _tenant_name(client: Any) -> str:
    """A stable ledger key for a client — its tenant host.

    Derived rather than passed so EVERY caller is accounted whether or not it
    remembered to name the tenant. An unattributed query is an unbudgeted query.
    """
    return tenant_ledger_key(str(getattr(client, "_base", "") or ""))


@contextlib.asynccontextmanager
async def _tenant_budget(client: Any):
    """Charge the process-wide tenant ledger around one XQL call.

    Deliberately HERE rather than only in ``connectors.service._budgeted``:
    ``_budgeted`` wraps the sweep and the manual verify endpoint, but assertion
    runs (``/api/assertions/{id}/run``) build a runner directly and would
    otherwise be entirely unbudgeted — a third path onto the same metered
    resource with no ceiling and no breaker.

    Both exits resolve ``pending`` upstream: ``verify_results`` and the
    assertion probes catch any runner exception and never score a budget
    CortexSim spent as a detection the customer missed.
    """
    from integrations.xsiam.exceptions import XsiamQuotaError  # noqa: PLC0415
    from integrations.xsiam.ledger import ledger  # noqa: PLC0415

    from connectors import tuning  # noqa: PLC0415

    tenant = _tenant_name(client)
    ledger.charge(tenant, limit=tuning.xsiam_max_queries_per_day())
    try:
        yield
    except XsiamQuotaError:
        ledger.trip(tenant, cooldown_seconds=tuning.xsiam_breaker_cooldown())
        raise


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

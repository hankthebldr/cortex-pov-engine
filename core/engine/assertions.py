"""
CortexSim Assertion substrate — the proof mechanism for POS / PLT / AUT.

A **Scenario** proves a detection fired. That covers the index's DET/HNT rows
and nothing else. 140 of the master index's 182 unevidenced test cases are
POS (a posture finding must be *discovered*), PLT (a platform capability must be
*present*) or AUT (an automation outcome must *occur* inside a budget). None of
those is a detection, and authoring more attack scenarios for them would inflate
the coverage number without proving anything.

An **Assertion** is the artifact for those three. One artifact type, three
validation classes, one loader, one scoring path::

    author YAML  ->  validate at boot  ->  bind to the index as a real FK
                 ->  render the exact question  ->  measure it  ->  four-valued verdict

Design commitments, in order of how much they matter:

1. **An assertion that cannot fail does not load.** Every check's threshold is
   proven falsifiable *and* satisfiable at load time by running the real
   evaluator across the probe's own measurement domain (``A-17``), and the
   author's declared negative control is proven to actually evaluate ``fail``
   (``A-18``). These are structural — ``CORTEXSIM_STRICT_REFS`` does not relax
   them. This is the guard that stops the whole effort becoming coverage
   theatre, and it is why ``expected_rows_min: 0`` style tautologies cannot be
   authored here.

2. **One ref path.** ``uc_ref`` / ``tc_ref`` / ``tc_refs`` go through the same
   :func:`engine.scenario_loader.validate_index_refs` the 161 scenarios use,
   gated by the same ``CORTEXSIM_STRICT_REFS``. Codes ``A-10``/``A-11``/``A-12``
   /``A-13`` are the ``S-10``/``S-11``/``S-12``/``S-15`` semantics verbatim.

3. **One scoring path.** Checks are scored by ``verifier.evaluate_threshold``
   and aggregated by ``verifier.score_run`` — the same functions, the same
   ``pass | fail | pending | not_applicable`` vocabulary, the same
   ``tc_verdict`` column name. There is no parallel scorer.

4. **Without a tenant, nothing is green.** No integration, an unreachable
   tenant, a quota error or a dry run all resolve ``pending`` — "still owed" —
   never ``pass`` and never ``not_applicable``. ``not_applicable`` is reserved
   for claims that are unscoreable *by construction* (the index carries no
   measurable threshold) or commercially out of scope (the tenant is not
   entitled). Collapsing the two would let unproven claims disappear into a
   bucket that reads as benign.

The substrate deliberately does NOT execute stimulus. An assertion *declares*
its trigger (a scenario run, an EAL campaign, a Terraform fixture) and the
operator supplies the resulting context (nonce, run id) at evaluation time.
Wiring stimulus execution is a separate, later decision; conflating it here
would couple the proof mechanism to three subsystems it does not need.

Authoring contract: ``docs/uc_tc_mapping/assertions.md``.
"""

from __future__ import annotations

import abc
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, ClassVar, Optional

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from engine.verifier import (
    _OPS as _THRESHOLD_OPS,
    FAIL,
    NOT_APPLICABLE,
    PASS,
    PENDING,
    RunScore,
    ScalarRunner,
    evaluate_threshold,
    is_scoreable,
    score_run,
)

logger = logging.getLogger("cortexsim.assertions")

_YAML_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

VALIDATION_CLASSES = frozenset({"POS", "PLT", "AUT"})

# ``state``   — idempotent. Probe standing platform/posture state; runnable at
#               any time with no causal setup (POS, most PLT).
# ``outcome`` — a causally-raised condition must have provoked an action inside
#               a latency budget. Carries a trigger, a settle window, and a
#               precursor probe (AUT, some PLT).
KINDS = frozenset({"state", "outcome"})

STATUSES = frozenset({"active", "draft", "deprecated"})

# Where a check's structured threshold came from. Rendered differently in the
# readout so a reviewer can see which numbers the index set and which the
# author set.
THRESHOLD_SOURCES = frozenset({"index", "authored_sharpening"})

STIMULUS_KINDS = frozenset({"none", "scenario_run", "eal_campaign", "iac_fixture"})


# ---------------------------------------------------------------------------
# Failure taxonomy
#
# Stable string codes, never renumbered, each with one line of operator-
# actionable remediation. Modelled on core/eal_simulator/delivery.py so the
# product speaks one diagnostic dialect.
#
# The class a code lands in decides the verdict BEFORE the threshold is
# consulted. That ordering is the rule that keeps a tenant outage from being
# reported as a capability failure, and keeps a capability failure from being
# masked by a transport hiccup.
# ---------------------------------------------------------------------------

MEASURED = "MEASURED"

# -> pending. The claim is unproven and still owed.
PENDING_CODES = frozenset({
    "NO_TENANT_INTEGRATION",
    "TENANT_UNREACHABLE",
    "PROBE_AUTH_FAILED",
    "PROBE_QUOTA_EXHAUSTED",
    "PROBE_QUERY_FAILED",
    "PRECURSOR_MISSING",
    "POPULATION_EMPTY",
    "DRY_RUN",
    # The trigger fired but the settle budget has not elapsed yet. "Not yet" is
    # not "never": reporting OUTCOME_ABSENT here would tell a customer their
    # auto-containment failed when the engine simply asked too early. False red
    # in a readout costs as much as false green.
    "SETTLE_WINDOW_OPEN",
})

# -> not_applicable. The claim cannot be scored, by construction or by contract.
NOT_APPLICABLE_CODES = frozenset({
    "NOT_ENTITLED",
    "NO_MEASURABLE_THRESHOLD",
})

# -> fail, regardless of threshold. Positive proof of absence.
FAIL_CODES = frozenset({
    "OUTCOME_ABSENT",
})

REMEDIATION: dict[str, str] = {
    "NO_TENANT_INTEGRATION":
        "Register a read-only XSIAM tenant credential (POST /api/credentials/integrations, kind=xsiam_tenant) and re-run.",
    "TENANT_UNREACHABLE":
        "The tenant did not answer. Check network egress and the tenant base URL, then re-run — this says nothing about the platform.",
    "PROBE_AUTH_FAILED":
        "The API key was rejected (401). Re-issue a read-only key with XQL query permission and update the integration.",
    "PROBE_QUOTA_EXHAUSTED":
        "XQL quota or concurrency exhausted. Wait for the tenant's query slots to free and re-run; this is not a capability failure.",
    "PROBE_QUERY_FAILED":
        "The query did not execute — usually an unknown dataset or a field name this tenant does not carry. Confirm the schema and correct the assertion's query.",
    "PRECURSOR_MISSING":
        "The trigger condition was never raised in the tenant, so the automation had nothing to act on. Fix the DETECTION first; this is not an automation failure.",
    "POPULATION_EMPTY":
        "The population this ratio is computed over is empty or below min_population. Zero of zero is not 100% — supply more trials.",
    "OUTCOME_ABSENT":
        "The trigger was positively observed and the expected outcome never appeared inside the settle window. This is the capability failing.",
    "SETTLE_WINDOW_OPEN":
        "The trigger was observed but its settle budget has not elapsed. Re-run the assertion after the window closes — this is not a capability failure.",
    "NOT_ENTITLED":
        "The tenant profile does not carry the add-on this test case requires. A commercial gap, not a product failure — it belongs on the upsell list.",
    "NO_MEASURABLE_THRESHOLD":
        "The bound index test case carries no measurable threshold, so this is evidence rather than a scored pass.",
    "DRY_RUN":
        "Dry run — the query was rendered but no tenant was touched. Re-run with dry_run=false against a registered tenant.",
    MEASURED: "",
}


def verdict_for_code(code: str) -> Optional[str]:
    """The verdict a taxonomy code forces, or None when the threshold decides."""
    if code in NOT_APPLICABLE_CODES:
        return NOT_APPLICABLE
    if code in PENDING_CODES:
        return PENDING
    if code in FAIL_CODES:
        return FAIL
    return None


# ---------------------------------------------------------------------------
# Measurement domains
#
# A probe declares what kind of number it can physically produce. The
# falsifiability proof samples that domain, so an author cannot manufacture
# failability with a value the probe could never return (a negative row count,
# a 300% coverage).
# ---------------------------------------------------------------------------

DOMAIN_BOUNDS: dict[str, tuple[float, Optional[float]]] = {
    "count": (0.0, None),
    "seconds": (0.0, None),
    "percent": (0.0, 100.0),
    "ratio": (0.0, 1.0),
}

DOMAIN_SAMPLES: dict[str, tuple[float, ...]] = {
    "count": (0, 1, 2, 3, 5, 10, 50, 100, 1_000, 10_000),
    "seconds": (0, 0.5, 1, 15, 30, 60, 120, 300, 900, 3_600, 86_400),
    "percent": (0, 1, 10, 25, 50, 75, 90, 95, 99, 99.9, 100),
    "ratio": (0.0, 0.25, 0.5, 0.75, 1.0),
}

DOMAIN_UNITS: dict[str, str] = {
    "count": "", "seconds": "s", "percent": "%", "ratio": "",
}


def in_domain(domain: str, value: Optional[float]) -> bool:
    if value is None:
        return False
    lo, hi = DOMAIN_BOUNDS.get(domain, (None, None))
    if lo is not None and value < lo:
        return False
    if hi is not None and value > hi:
        return False
    return True


def _domain_probe_points(domain: str, threshold_value: float) -> list[float]:
    """Sample points to prove a threshold both falsifiable and satisfiable.

    The fixed samples cover the shape of the domain; the neighbourhood of the
    threshold's own value covers boundary operators (``=``, ``!=``) that a
    coarse sample would miss.
    """
    pts = list(DOMAIN_SAMPLES.get(domain, DOMAIN_SAMPLES["count"]))
    for delta in (-1.0, -0.001, 0.0, 0.001, 1.0):
        pts.append(threshold_value + delta)
    return [p for p in dict.fromkeys(pts) if in_domain(domain, p)]


# ---------------------------------------------------------------------------
# Probe contract
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProbeOutcome:
    """What one probe execution measured, or why it could not."""

    code: str
    measured_value: Optional[float] = None
    detail: str = ""
    rows: list[dict[str, Any]] = field(default_factory=list)
    attempts: list[dict[str, Any]] = field(default_factory=list)

    @property
    def remediation(self) -> str:
        return REMEDIATION.get(self.code, "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "measured_value": self.measured_value,
            "detail": self.detail,
            "rows": self.rows[:20],
            "attempts": self.attempts,
            "remediation": self.remediation,
        }


@dataclass
class ProbeContext:
    """Everything a probe needs to run. The query runner is injected, so no
    probe ever constructs a client and tests never reach the network."""

    runner: ScalarRunner
    params: dict[str, Any]
    rendered: dict[str, str]          # query-param name -> rendered query text
    context: dict[str, str]
    # The owning assertion's timing contract, when it has one. A latency probe
    # needs it to tell "the outcome never came" apart from "the budget has not
    # expired yet" — without it, every early poll reads as a capability failure.
    settle: Optional[Any] = None


class Probe(abc.ABC):
    """Read-only measurement against a tenant.

    Subclasses declare what they can measure and — mandatorily — the conditions
    under which they return a failing measurement. ``FAILURE_CONDITIONS`` is not
    documentation: an empty tuple makes every check using the probe unloadable,
    and each condition must be covered by a test that shows it produce ``fail``.
    """

    NAME: ClassVar[str]
    DOMAIN: ClassVar[str] = "count"           # default; may be per-check
    UNIT: ClassVar[str] = ""
    READ_ONLY: ClassVar[bool] = True          # no probe may ever write to a tenant
    FAILURE_CONDITIONS: ClassVar[tuple[str, ...]] = ()
    QUERY_PARAMS: ClassVar[tuple[str, ...]] = ()      # params holding query templates
    REQUIRED_PARAMS: ClassVar[tuple[str, ...]] = ()

    @classmethod
    def domain_for(cls, params: dict[str, Any]) -> str:
        return cls.DOMAIN

    @classmethod
    def unit_for(cls, params: dict[str, Any]) -> str:
        return cls.UNIT or DOMAIN_UNITS.get(cls.domain_for(params), "")

    @classmethod
    def validate_params(cls, params: dict[str, Any]) -> list[str]:
        """Return a list of param problems. Empty means the params are usable."""
        problems = [f"missing required param '{p}'"
                    for p in cls.REQUIRED_PARAMS if not params.get(p)]
        for p in cls.QUERY_PARAMS:
            val = params.get(p)
            if val is not None and not isinstance(val, str):
                problems.append(f"param '{p}' must be a query string")
        return problems

    @abc.abstractmethod
    async def execute(self, ctx: ProbeContext) -> ProbeOutcome:
        ...


PROBE_REGISTRY: dict[str, type[Probe]] = {}


def register_probe(cls: type[Probe]) -> type[Probe]:
    if not cls.FAILURE_CONDITIONS:
        raise ValueError(
            f"probe '{cls.NAME}' declares no FAILURE_CONDITIONS — a probe that "
            f"cannot produce a failing measurement proves nothing and must not "
            f"be registered"
        )
    PROBE_REGISTRY[cls.NAME] = cls
    return cls


# ---------------------------------------------------------------------------
# Probes
# ---------------------------------------------------------------------------


async def _rows(ctx: ProbeContext, key: str) -> list[dict[str, Any]]:
    return await ctx.runner(ctx.rendered[key])


@register_probe
class XqlRowsProbe(Probe):
    """How many rows the tenant returns for one question.

    The workhorse. POS discovery ("are the planted finding classes in the
    posture findings?"), PLT dataset presence ("is this source ingesting?"),
    AUT outcome presence ("did the containment record appear?").
    """

    NAME = "xql_rows"
    DOMAIN = "count"
    UNIT = "rows"
    QUERY_PARAMS = ("query",)
    REQUIRED_PARAMS = ("query",)
    FAILURE_CONDITIONS = (
        "the tenant returns fewer rows than the threshold requires "
        "(the planted item was not discovered / the source is not ingesting / "
        "the outcome never landed)",
    )

    async def execute(self, ctx: ProbeContext) -> ProbeOutcome:
        rows = await _rows(ctx, "query")
        return ProbeOutcome(MEASURED, float(len(rows)),
                            f"{len(rows)} row(s) returned", rows[:20])


@register_probe
class XqlDistinctProbe(Probe):
    """How many DISTINCT values of one field the answer spans.

    "Three heterogeneous sources landed in one queryable schema", "five
    containment domains confirmed against one incident" — claims a raw row count
    cannot make, because one chatty source would satisfy it alone.
    """

    NAME = "xql_distinct"
    DOMAIN = "count"
    UNIT = "distinct"
    QUERY_PARAMS = ("query",)
    REQUIRED_PARAMS = ("query", "distinct_key")
    FAILURE_CONDITIONS = (
        "fewer distinct values than required — e.g. only 2 of 3 declared sources "
        "normalized, or 4 of 5 containment domains confirmed",
        "the field is absent from every row, so nothing is distinguishable",
    )

    async def execute(self, ctx: ProbeContext) -> ProbeOutcome:
        rows = await _rows(ctx, "query")
        key = ctx.params["distinct_key"]
        seen = {str(r.get(key)) for r in rows if r.get(key) is not None}
        return ProbeOutcome(
            MEASURED, float(len(seen)),
            f"{len(seen)} distinct {key} value(s) across {len(rows)} row(s): "
            f"{', '.join(sorted(seen)[:10])}",
            rows[:20],
        )


@register_probe
class XqlScalarProbe(Probe):
    """A number the tenant itself computed — an SLA, a score, a duration.

    The threshold is deliberately NOT embedded in the query. A query ending
    ``| filter sla_seconds <= 300`` returns zero rows for a tenant that took
    412s, which is indistinguishable from a tenant that never responded at all,
    and the customer never learns the number. Return the value; let the
    evaluator compare it.
    """

    NAME = "xql_scalar"
    UNIT = ""
    QUERY_PARAMS = ("query",)
    REQUIRED_PARAMS = ("query", "field")
    _AGGREGATES = ("first", "min", "max", "sum", "avg", "count")
    FAILURE_CONDITIONS = (
        "the measured value misses the bar (latency over budget, score under "
        "floor, coverage below the required percentage)",
    )

    @classmethod
    def domain_for(cls, params: dict[str, Any]) -> str:
        return str(params.get("domain") or "count")

    @classmethod
    def validate_params(cls, params: dict[str, Any]) -> list[str]:
        problems = super().validate_params(params)
        agg = params.get("aggregate", "first")
        if agg not in cls._AGGREGATES:
            problems.append(f"aggregate must be one of {list(cls._AGGREGATES)}, got {agg!r}")
        domain = params.get("domain", "count")
        if domain not in DOMAIN_BOUNDS:
            problems.append(f"domain must be one of {sorted(DOMAIN_BOUNDS)}, got {domain!r}")
        return problems

    async def execute(self, ctx: ProbeContext) -> ProbeOutcome:
        rows = await _rows(ctx, "query")
        fieldname = ctx.params["field"]
        agg = ctx.params.get("aggregate", "first")
        values = [v for v in (_as_float(r.get(fieldname)) for r in rows) if v is not None]
        if agg == "count":
            return ProbeOutcome(MEASURED, float(len(values)),
                                f"count({fieldname}) = {len(values)}", rows[:20])
        if not values:
            # No value to compare. Positively distinct from "the value was bad":
            # the query ran and carried nothing, which is a query/schema problem,
            # not a capability failure.
            return ProbeOutcome(
                "PROBE_QUERY_FAILED", None,
                f"no numeric '{fieldname}' in {len(rows)} returned row(s) — "
                f"check the field name against this tenant's schema",
                rows[:20],
            )
        value = {
            "first": values[0], "min": min(values), "max": max(values),
            "sum": sum(values), "avg": sum(values) / len(values),
        }[agg]
        return ProbeOutcome(MEASURED, float(value),
                            f"{agg}({fieldname}) = {value} over {len(values)} row(s)",
                            rows[:20])


@register_probe
class XqlRatioProbe(Probe):
    """A percentage of a population the PLATFORM defined.

    The population predicate is platform-declared on purpose. The engine
    controls the input and reads the platform's own judgement of it; it never
    asserts "this input should have been handled that way", which is not
    machine-knowable and would paint a correct platform red.

    An empty population is ``pending``, never ``pass``: zero of zero is 100%,
    and reporting that as green is the purest form of the inflation this
    substrate exists to prevent.
    """

    NAME = "xql_ratio"
    DOMAIN = "percent"
    UNIT = "%"
    QUERY_PARAMS = ("numerator_query", "population_query")
    REQUIRED_PARAMS = ("numerator_query", "population_query")
    FAILURE_CONDITIONS = (
        "the achieved percentage of the platform-defined population misses the "
        "floor (too few alerts auto-dispositioned, too few findings discovered)",
    )

    @classmethod
    def validate_params(cls, params: dict[str, Any]) -> list[str]:
        problems = super().validate_params(params)
        minpop = params.get("min_population", 1)
        if not isinstance(minpop, int) or minpop < 1:
            problems.append("min_population must be an integer >= 1 — a ratio over an "
                            "empty population is not a measurement")
        return problems

    async def execute(self, ctx: ProbeContext) -> ProbeOutcome:
        population = await _rows(ctx, "population_query")
        minpop = int(ctx.params.get("min_population", 1))
        if len(population) < minpop:
            return ProbeOutcome(
                "POPULATION_EMPTY", None,
                f"population is {len(population)} row(s), below the declared "
                f"min_population of {minpop}",
                population[:20],
            )
        numerator = await _rows(ctx, "numerator_query")
        pct = len(numerator) / len(population) * 100.0
        return ProbeOutcome(
            MEASURED, round(pct, 4),
            f"{len(numerator)} of {len(population)} = {pct:.1f}%",
            numerator[:20],
        )


@register_probe
class XqlLatencyProbe(Probe):
    """Elapsed time between a trigger and an outcome, both read from the
    PLATFORM's own records.

    Never wall-clock. ``now() - run.started_at`` folds agent dispatch, emitter
    POST latency, collector buffering and ingest delay into the platform's
    response time and over-reports it by minutes, manufacturing false red
    against a platform that actually met the bar.

    The precursor probe is the load-bearing part. You may only report ``fail``
    when you can positively prove the trigger existed and the outcome did not.
    No precursor rows -> ``pending`` (``PRECURSOR_MISSING``), because the failure
    is then a detection failure, not an automation failure.
    """

    NAME = "xql_latency"
    DOMAIN = "seconds"
    UNIT = "s"
    QUERY_PARAMS = ("precursor_query", "outcome_query")
    REQUIRED_PARAMS = ("precursor_query", "outcome_query",
                       "precursor_ts_field", "outcome_ts_field")
    FAILURE_CONDITIONS = (
        "the trigger was positively observed and the outcome never appeared "
        "(OUTCOME_ABSENT) — the automation did not fire",
        "the outcome appeared but later than the budget allows — the loop "
        "closed, it was late, and the measured number is reported",
    )

    async def execute(self, ctx: ProbeContext) -> ProbeOutcome:
        precursor = await _rows(ctx, "precursor_query")
        p_field = ctx.params["precursor_ts_field"]
        p_times = sorted(t for t in (_as_epoch_seconds(r.get(p_field)) for r in precursor)
                         if t is not None)
        if not p_times:
            return ProbeOutcome(
                "PRECURSOR_MISSING", None,
                f"no trigger record carrying '{p_field}' — the automation was "
                f"never given anything to act on",
                precursor[:20],
            )

        outcome = await _rows(ctx, "outcome_query")
        o_field = ctx.params["outcome_ts_field"]
        o_times = sorted(t for t in (_as_epoch_seconds(r.get(o_field)) for r in outcome)
                         if t is not None)
        if not o_times:
            early = _settle_still_open(ctx, p_times[0])
            if early is not None:
                return early
            return ProbeOutcome(
                "OUTCOME_ABSENT", None,
                f"trigger observed at {_iso(p_times[0])} but no outcome record "
                f"carrying '{o_field}' appeared",
                precursor[:20],
            )

        # Only an outcome that FOLLOWS the trigger measures this loop. Clamping a
        # negative elapsed to zero would report a stale outcome record — one from
        # an earlier run inside the same lookback window — as an instantaneous
        # response, which is the best possible score for the one shape that
        # proves nothing. An outcome that all predates the trigger means nothing
        # followed it, and that is the capability failing.
        after = [t for t in o_times if t >= p_times[0]]
        if not after:
            early = _settle_still_open(ctx, p_times[0])
            if early is not None:
                return early
            return ProbeOutcome(
                "OUTCOME_ABSENT", None,
                f"trigger observed at {_iso(p_times[0])} but every '{o_field}' "
                f"record predates it (latest {_iso(o_times[-1])}) — nothing "
                f"followed this trigger",
                outcome[:20],
            )

        elapsed = after[0] - p_times[0]
        return ProbeOutcome(
            MEASURED, round(elapsed, 3),
            f"trigger {_iso(p_times[0])} -> outcome {_iso(after[0])} = {elapsed:.1f}s",
            outcome[:20],
        )


def _settle_still_open(ctx: "ProbeContext", trigger_ts: float) -> Optional["ProbeOutcome"]:
    """``SETTLE_WINDOW_OPEN`` while the trigger's budget has not yet expired.

    An outcome probe that reports OUTCOME_ABSENT the instant it finds no outcome
    row cannot tell "the automation never ran" from "we asked too early" — and
    the second one is common, because platform ingest lag alone is measured in
    tens of seconds. Returning a hard FAIL there tells a customer their
    containment is broken when it is merely in flight.

    The bar is ``expect_by_seconds`` when the artifact declares one (that is the
    SLA being asserted), else ``max_wait_seconds``. Returns None once the budget
    has genuinely elapsed, at which point absence IS the capability failing.
    """
    settle = getattr(ctx, "settle", None)
    if settle is None:
        return None
    budget = getattr(settle, "expect_by_seconds", None) or getattr(
        settle, "max_wait_seconds", None)
    if not budget:
        return None
    elapsed = datetime.now(timezone.utc).timestamp() - trigger_ts
    if elapsed >= budget:
        return None
    return ProbeOutcome(
        "SETTLE_WINDOW_OPEN", None,
        f"trigger observed at {_iso(trigger_ts)}; {elapsed:.0f}s of the "
        f"{budget}s settle budget elapsed — no outcome yet, but the window "
        f"is still open",
    )


# ---------------------------------------------------------------------------
# Authoring schema
# ---------------------------------------------------------------------------

_ASSERTION_ID_RE = re.compile(r"^(POS|PLT|AUT)-[A-Z0-9]+-\d{3}$")
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z0-9_.]+)\s*\}\}")
# Anything substituted into a query text must survive XQL string quoting
# untouched. The whitelist IS the injection guard.
_CONTEXT_VALUE_RE = re.compile(r"^[A-Za-z0-9_.:@/\-]{1,200}$")
_BUILTIN_CONTEXT = ("nonce", "run_id", "lookback_seconds")


class ThresholdSpec(BaseModel):
    """The bar one check is measured against. Structured, never a raw string —
    ``evaluate_threshold`` correctly refuses raw strings, and the index only
    carries them as prose."""

    kpi: str
    op: str
    value: float
    unit: str = ""
    source: str = "authored_sharpening"
    rationale: Optional[str] = None

    @field_validator("op")
    @classmethod
    def _v_op(cls, v: str) -> str:
        if v not in _THRESHOLD_OPS:
            raise ValueError(f"op must be one of {sorted(_THRESHOLD_OPS)}, got '{v}'")
        return v

    @field_validator("source")
    @classmethod
    def _v_source(cls, v: str) -> str:
        if v not in THRESHOLD_SOURCES:
            raise ValueError(f"threshold.source must be one of {sorted(THRESHOLD_SOURCES)}")
        return v

    @model_validator(mode="after")
    def _v_rationale(self) -> "ThresholdSpec":
        # Sharpening a qualitative index row into a number is legitimate when
        # the thing measured is objectively countable. Doing it silently is not.
        if self.source == "authored_sharpening" and not (self.rationale or "").strip():
            raise ValueError(
                "threshold.source 'authored_sharpening' requires a rationale — "
                "a number the index did not set must justify itself"
            )
        return self

    def as_dict(self) -> dict[str, Any]:
        return {"kpi": self.kpi, "op": self.op, "value": self.value, "unit": self.unit}


class NegativeControlSpec(BaseModel):
    """The named condition under which this check goes red, plus the measurement
    that condition produces. Proven at load (``A-18``) by feeding it to the real
    evaluator and requiring ``fail``."""

    description: str
    measured_value: float

    @field_validator("description")
    @classmethod
    def _v(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("negative_control.description must not be blank")
        return v


class CheckSpec(BaseModel):
    check_id: str
    title: str
    probe: str
    params: dict[str, Any] = Field(default_factory=dict)
    threshold: ThresholdSpec
    negative_control: NegativeControlSpec
    weight: float = 1.0
    primary: bool = False


class StimulusSpec(BaseModel):
    """What must have happened before the assertion is evaluated.

    Declarative only — the substrate does not run it. ``detail`` is rendered in
    the readout so a DC knows what to do first.
    """

    kind: str = "none"
    scenario_id: Optional[str] = None
    campaign_id: Optional[str] = None
    iac_module: Optional[str] = None
    detail: Optional[str] = None

    @field_validator("kind")
    @classmethod
    def _v(cls, v: str) -> str:
        if v not in STIMULUS_KINDS:
            raise ValueError(f"stimulus.kind must be one of {sorted(STIMULUS_KINDS)}")
        return v


class SettleSpec(BaseModel):
    """Timing contract for an ``outcome`` assertion."""

    # Platform log-ingest + index latency. Nothing is readable before this, and
    # querying earlier burns one of the tenant's scarce XQL slots on a zero that
    # means nothing.
    ingest_floor_seconds: int = 60
    poll_interval_seconds: int = 15
    max_wait_seconds: int = 600
    # The bar. An assertion that stops polling before the bar expires
    # manufactures false red, which A-22 rejects.
    expect_by_seconds: Optional[int] = None


class AssertionSpec(BaseModel):
    assertion_id: str
    name: str
    version: str = "1.0"
    status: str = "active"

    validation_class: str
    kind: str = "state"
    plane: Optional[str] = None

    uc_ref: str
    tc_ref: str
    tc_refs: list[str] = Field(default_factory=list)
    uc_name: Optional[str] = None
    tc_name: Optional[str] = None

    # MANDATORY, non-empty (A-19). What this assertion does NOT prove.
    scope_limitations: str

    primary_kpi: Optional[str] = None
    methodology_family: Optional[str] = None
    moat_tier: Optional[str] = None
    success_criteria: Optional[str] = None

    stimulus: StimulusSpec = Field(default_factory=StimulusSpec)
    settle: Optional[SettleSpec] = None
    # Names the author may use as {{...}} in queries, beyond the builtins.
    context_vars: list[str] = Field(default_factory=list)

    checks: list[CheckSpec]
    tags: list[str] = Field(default_factory=list)
    author: Optional[str] = None

    @field_validator("assertion_id")
    @classmethod
    def _v_id(cls, v: str) -> str:
        if not _ASSERTION_ID_RE.match(v):
            raise ValueError(
                f"assertion_id must match {_ASSERTION_ID_RE.pattern} "
                f"(e.g. 'POS-CSPM-001', 'PLT-IR-008', 'AUT-AEPS-002'), got '{v}'"
            )
        return v

    @field_validator("validation_class")
    @classmethod
    def _v_class(cls, v: str) -> str:
        if v not in VALIDATION_CLASSES:
            raise ValueError(f"validation_class must be one of {sorted(VALIDATION_CLASSES)}")
        return v

    @field_validator("kind")
    @classmethod
    def _v_kind(cls, v: str) -> str:
        if v not in KINDS:
            raise ValueError(f"kind must be one of {sorted(KINDS)}")
        return v

    @field_validator("status")
    @classmethod
    def _v_status(cls, v: str) -> str:
        if v not in STATUSES:
            raise ValueError(f"status must be one of {sorted(STATUSES)}")
        return v

    @field_validator("checks")
    @classmethod
    def _v_checks(cls, v: list[CheckSpec]) -> list[CheckSpec]:
        if not v:
            raise ValueError("an assertion with no checks asserts nothing")
        return v

    @model_validator(mode="after")
    def _v_after(self) -> "AssertionSpec":
        # tc_refs defaults to [tc_ref] and always contains it — identical to the
        # scenario contract, so a reader of either field sees the same binding.
        refs = list(dict.fromkeys(self.tc_refs))
        if self.tc_ref and self.tc_ref not in refs:
            refs.insert(0, self.tc_ref)
        object.__setattr__(self, "tc_refs", refs)

        if self.assertion_id.split("-")[0] != self.validation_class:
            raise ValueError(
                f"assertion_id prefix '{self.assertion_id.split('-')[0]}' "
                f"disagrees with validation_class '{self.validation_class}'"
            )
        if self.kind == "outcome" and self.settle is None:
            raise ValueError(
                "an 'outcome' assertion must declare a settle block — it asserts "
                "that something happened inside a budget, and the budget is the claim"
            )
        return self

    @property
    def primary_check(self) -> CheckSpec:
        return next((c for c in self.checks if c.primary), self.checks[0])


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


class AssertionRenderError(Exception):
    """A query template could not be safely rendered from the supplied context."""


def placeholders(text: str) -> set[str]:
    return set(_PLACEHOLDER_RE.findall(text or ""))


def allowed_placeholders(spec: AssertionSpec) -> set[str]:
    return set(_BUILTIN_CONTEXT) | set(spec.context_vars)


def render_query(template: str, context: dict[str, str]) -> str:
    """Substitute ``{{name}}`` placeholders from ``context``.

    Values are whitelist-validated before substitution. A run-scoped nonce is
    the difference between "a containment happened" and "OUR containment
    happened", so it lands inside query text — and anything landing inside
    query text has to be incapable of changing the query's shape.
    """
    missing = sorted(p for p in placeholders(template) if p not in context)
    if missing:
        raise AssertionRenderError(
            f"query template needs context value(s) {missing} that were not supplied"
        )

    def _sub(m: re.Match[str]) -> str:
        name = m.group(1)
        value = str(context[name])
        if not _CONTEXT_VALUE_RE.match(value):
            raise AssertionRenderError(
                f"context value for '{name}' contains characters that are not "
                f"safe to substitute into a query: {value!r}"
            )
        return value

    return _PLACEHOLDER_RE.sub(_sub, template)


def render_check(check: CheckSpec, context: dict[str, str]) -> dict[str, str]:
    """Render every query param of one check. Returns ``{param: query_text}``."""
    probe_cls = PROBE_REGISTRY[check.probe]
    out: dict[str, str] = {}
    for name in probe_cls.QUERY_PARAMS:
        raw = check.params.get(name)
        if isinstance(raw, str):
            out[name] = render_query(raw, context)
    return out


# ---------------------------------------------------------------------------
# Evaluation — one check, one verdict
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CheckVerdict:
    verdict: str
    detail: str
    code: str
    measured_value: Optional[float] = None
    expected: Optional[float] = None
    op: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "detail": self.detail,
            "code": self.code,
            "measured_value": self.measured_value,
            "expected": self.expected,
            "op": self.op,
            "remediation": REMEDIATION.get(self.code, ""),
        }


def evaluate_check(check: CheckSpec, outcome: ProbeOutcome) -> CheckVerdict:
    """Score one probe outcome against one check's threshold.

    Precedence, and the ordering is the point:

    1. a taxonomy code that forces a verdict wins — a quota error is never
       reported as a threshold miss, and a threshold miss is never masked by a
       transport hiccup;
    2. an unscoreable threshold is ``not_applicable``, never ``pass``;
    3. otherwise the existing :func:`verifier.evaluate_threshold` decides.

    Note this is the CHECK verdict, and it stays a real pass/fail even when the
    bound index test case is qualitative. The "evidence, not a scored pass"
    clamp is applied once, at run level, by ``verifier.score_run``.
    """
    forced = verdict_for_code(outcome.code)
    if forced is not None:
        return CheckVerdict(forced, outcome.detail or outcome.code, outcome.code,
                            outcome.measured_value)

    threshold = check.threshold.as_dict()
    if not is_scoreable(threshold):
        return CheckVerdict(NOT_APPLICABLE,
                            "threshold is not machine-evaluable",
                            "NO_MEASURABLE_THRESHOLD", outcome.measured_value)

    tv = evaluate_threshold(threshold, outcome.measured_value)
    detail = tv.detail if not outcome.detail else f"{outcome.detail}; {tv.detail}"
    return CheckVerdict(tv.verdict, detail, outcome.code,
                        tv.actual, tv.expected, tv.op)


# ---------------------------------------------------------------------------
# The falsifiability proof (A-17 / A-18)
#
# This is the guard the whole mechanism turns on, so it is executed rather than
# asserted: the loader builds ProbeOutcomes across the probe's own measurement
# domain, pushes them through the REAL evaluator, and requires the check to
# produce both a fail and a pass. A check that cannot go red proves nothing; a
# check that cannot go green manufactures red.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FalsifiabilityProof:
    can_fail: bool
    can_pass: bool
    failing_value: Optional[float] = None
    passing_value: Optional[float] = None

    @property
    def ok(self) -> bool:
        return self.can_fail and self.can_pass


def prove_falsifiable(check: CheckSpec) -> FalsifiabilityProof:
    """Prove the check's threshold both falsifiable and satisfiable."""
    probe_cls = PROBE_REGISTRY[check.probe]
    domain = probe_cls.domain_for(check.params)
    failing: Optional[float] = None
    passing: Optional[float] = None

    for value in _domain_probe_points(domain, check.threshold.value):
        verdict = evaluate_check(check, ProbeOutcome(MEASURED, value)).verdict
        if verdict == FAIL and failing is None:
            failing = value
        elif verdict == PASS and passing is None:
            passing = value
        if failing is not None and passing is not None:
            break

    return FalsifiabilityProof(failing is not None, passing is not None, failing, passing)


def prove_negative_control(check: CheckSpec) -> tuple[bool, str]:
    """Prove the author's declared negative control actually evaluates ``fail``
    and is a measurement the probe could physically produce."""
    probe_cls = PROBE_REGISTRY[check.probe]
    domain = probe_cls.domain_for(check.params)
    value = check.negative_control.measured_value

    if not in_domain(domain, value):
        lo, hi = DOMAIN_BOUNDS[domain]
        return False, (
            f"negative_control.measured_value {value} is outside the '{domain}' "
            f"domain [{lo}, {hi if hi is not None else '∞'}] that probe "
            f"'{check.probe}' can produce — a control the probe could never "
            f"return does not prove the check can fail"
        )

    verdict = evaluate_check(check, ProbeOutcome(MEASURED, value)).verdict
    if verdict != FAIL:
        return False, (
            f"negative_control measured_value {value} evaluates '{verdict}', "
            f"not 'fail', against threshold {_threshold_label(check)}"
        )
    return True, ""


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LoadDiagnostic:
    code: str
    detail: str
    source: str
    severity: str = "error"       # error | warning

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "detail": self.detail,
                "source": self.source, "severity": self.severity}


# Codes gated by CORTEXSIM_STRICT_REFS — exactly the index-binding family, and
# for exactly the reason the scenario loader gates S-10..S-12/S-15: strictness
# is about whether the index snapshot is trustworthy in this deployment.
_STRICT_GATED = frozenset({"A-10", "A-11", "A-12", "A-13", "A-14", "A-15", "A-16"})

# Structural codes. NEVER gated. An artifact that cannot run, cannot fail, or
# hides what it does not prove is unsound regardless of index availability, and
# letting an env var wave it through would defeat the entire guard.
_STRUCTURAL = frozenset({"A-01", "A-02", "A-03", "A-17", "A-18", "A-19",
                         "A-20", "A-21", "A-22", "A-24"})


def _threshold_label(check: CheckSpec) -> str:
    """``>= 9 resources`` — the bar as a reader would say it out loud."""
    return f"{check.threshold.op} {check.threshold.value} {check.threshold.unit}".rstrip()


def _validate_spec(spec: AssertionSpec, source: str) -> list[LoadDiagnostic]:
    """Everything checkable about one already-parsed assertion."""
    diags: list[LoadDiagnostic] = []

    # ---- A-19: scope limitations -----------------------------------------
    if not (spec.scope_limitations or "").strip():
        diags.append(LoadDiagnostic(
            "A-19", f"{spec.assertion_id}: scope_limitations is blank — every "
            f"assertion must state what it does NOT prove", source))

    # ---- A-03 / A-20 / A-24 / A-21 / A-17 / A-18: per check --------------
    seen_ids: set[str] = set()
    allowed = allowed_placeholders(spec)
    for check in spec.checks:
        label = f"{spec.assertion_id}/{check.check_id}"

        if check.check_id in seen_ids:
            diags.append(LoadDiagnostic(
                "A-03", f"{label}: duplicate check_id", source))
            continue
        seen_ids.add(check.check_id)

        probe_cls = PROBE_REGISTRY.get(check.probe)
        if probe_cls is None:
            diags.append(LoadDiagnostic(
                "A-20", f"{label}: unknown probe '{check.probe}' — known probes "
                f"are {sorted(PROBE_REGISTRY)}", source))
            continue

        problems = probe_cls.validate_params(check.params)
        if problems:
            diags.append(LoadDiagnostic(
                "A-24", f"{label}: probe '{check.probe}' params invalid: "
                f"{'; '.join(problems)}", source))
            continue

        # A-21 — only declared placeholders may appear in query text.
        for pname in probe_cls.QUERY_PARAMS:
            raw = check.params.get(pname)
            if not isinstance(raw, str):
                continue
            unknown = sorted(placeholders(raw) - allowed)
            if unknown:
                diags.append(LoadDiagnostic(
                    "A-21", f"{label}: query param '{pname}' uses undeclared "
                    f"placeholder(s) {unknown}; declare them in context_vars or "
                    f"use one of {sorted(_BUILTIN_CONTEXT)}", source))

        # A-17 — the guard. Proven by execution, not by inspection.
        bar = _threshold_label(check)
        proof = prove_falsifiable(check)
        if not proof.can_fail:
            diags.append(LoadDiagnostic(
                "A-17", f"{label}: threshold {bar} "
                f"is satisfied by EVERY value probe '{check.probe}' can produce "
                f"over its '{probe_cls.domain_for(check.params)}' domain — this "
                f"check can never fail and therefore proves nothing", source))
        if not proof.can_pass:
            diags.append(LoadDiagnostic(
                "A-17", f"{label}: threshold {bar} "
                f"is failed by EVERY value probe '{check.probe}' can produce — "
                f"this check manufactures red and can never be satisfied", source))

        # A-18 — the author's own stated failure mode must actually go red.
        ok, why = prove_negative_control(check)
        if not ok:
            diags.append(LoadDiagnostic("A-18", f"{label}: {why}", source))

    # ---- A-22: the settle window must outlast the bar it measures --------
    if spec.kind == "outcome" and spec.settle is not None:
        s = spec.settle
        if s.expect_by_seconds is not None:
            floor = s.expect_by_seconds + s.ingest_floor_seconds
            if s.max_wait_seconds < floor:
                diags.append(LoadDiagnostic(
                    "A-22", f"{spec.assertion_id}: settle.max_wait_seconds "
                    f"({s.max_wait_seconds}) is shorter than expect_by "
                    f"({s.expect_by_seconds}) + ingest_floor "
                    f"({s.ingest_floor_seconds}) = {floor}s — the probe would "
                    f"stop looking before the budget expires and report false red",
                    source))
            if s.expect_by_seconds <= 0:
                diags.append(LoadDiagnostic(
                    "A-22", f"{spec.assertion_id}: settle.expect_by_seconds must "
                    f"be > 0", source))

    diags.extend(_validate_index_binding(spec, source))
    return diags


def _validate_index_binding(spec: AssertionSpec, source: str) -> list[LoadDiagnostic]:
    """A-10..A-16 / A-23 — the artifact's binding to the master UC/TC index."""
    from engine.scenario_loader import validate_index_refs  # noqa: PLC0415

    diags: list[LoadDiagnostic] = []

    tc, ref_errors = validate_index_refs(
        uc_ref=spec.uc_ref, tc_ref=spec.tc_ref, tc_refs=spec.tc_refs,
        artifact_id=f"assertion={spec.assertion_id}", source=source,
        codes=("A-10", "A-11", "A-12", "A-13"),
    )
    for detail in ref_errors:
        # validate_index_refs prefixes the code onto the message so the scenario
        # loader (which returns a bare string) carries it. LoadDiagnostic already
        # has a code field, so strip it rather than printing "A-12 A-12 ...".
        code, _, rest = detail.partition(" ")
        diags.append(LoadDiagnostic(code, rest or detail, source))
    if tc is None:
        return diags

    # A-14 — the inverted S-14. A scenario binding a POS row is a warning
    # because a detection cannot satisfy it; an ASSERTION binding a DET row is
    # an error, because the index expects authored detection content there and
    # an assertion would claim credit for work that was never done.
    if tc.validation_class and tc.validation_class != spec.validation_class:
        diags.append(LoadDiagnostic(
            "A-14", f"assertion={spec.assertion_id} is declared "
            f"validation_class={spec.validation_class} but index tc={tc.tc_id} "
            f"carries validation_class={tc.validation_class} — bind this test "
            f"case with a {tc.validation_class} artifact (from {source})", source))

    # A-16 — you may not claim the index set a number it did not set.
    for check in spec.checks:
        if check.threshold.source != "index":
            continue
        if not is_scoreable(tc.threshold):
            diags.append(LoadDiagnostic(
                "A-16", f"{spec.assertion_id}/{check.check_id}: "
                f"threshold.source is 'index' but tc={tc.tc_id} carries "
                f"threshold={tc.threshold!r}, which is not machine-evaluable. "
                f"Use source 'authored_sharpening' with a rationale.", source))

    # A-15 — a sharpened threshold may be stricter than the index's, never looser.
    for check in spec.checks:
        contradiction = _threshold_contradiction(tc.threshold, check.threshold)
        if contradiction:
            diags.append(LoadDiagnostic(
                "A-15", f"{spec.assertion_id}/{check.check_id}: {contradiction} "
                f"(index tc={tc.tc_id} threshold={tc.threshold!r}) (from {source})",
                source))

    # A-23 — positioning drift. Advisory, exactly like S-13.
    if spec.moat_tier and tc.differentiation_tier and \
            spec.moat_tier != tc.differentiation_tier:
        diags.append(LoadDiagnostic(
            "A-23", f"assertion={spec.assertion_id} declares moat_tier="
            f"{spec.moat_tier} but index tc={tc.tc_id} carries "
            f"differentiation_tier={tc.differentiation_tier}", source, "warning"))

    return diags


# The index writes thresholds as prose. Parse conservatively: an explicit
# operator, or a "within N minutes" phrasing. Anything else is left uncompared
# rather than guessed, because a wrong guess would reject a correct assertion.
_RAW_THRESHOLD_RE = re.compile(
    r"(?P<within>within\s+)?(?P<op><=|>=|<|>|=|≤|≥)?\s*"
    r"(?P<value>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>%|percent|ms|s\b|sec\b|secs\b|second|seconds|min\b|minute|minutes|hour|hours)?",
    re.IGNORECASE,
)

_UNIT_TO_SECONDS = {"ms": 0.001, "s": 1, "sec": 1, "secs": 1, "second": 1,
                    "seconds": 1, "min": 60, "minute": 60, "minutes": 60,
                    "hour": 3600, "hours": 3600}


def _normalize_threshold(op: str, value: float, unit: str) -> Optional[tuple[str, float, str]]:
    """Reduce a threshold to ``(op, value, family)`` where family is
    ``seconds`` | ``percent``, or None when it is not comparable."""
    u = (unit or "").strip().lower()
    if u in ("%", "percent"):
        return op, value, "percent"
    if u in _UNIT_TO_SECONDS:
        return op, value * _UNIT_TO_SECONDS[u], "seconds"
    return None


def _threshold_contradiction(raw: str, authored: ThresholdSpec) -> Optional[str]:
    """Non-empty when the authored threshold is LOOSER than the index's."""
    if not raw or not is_scoreable(raw):
        return None
    m = _RAW_THRESHOLD_RE.search(raw)
    if not m:
        return None
    op = m.group("op") or ("<=" if m.group("within") else "")
    if not op:
        return None
    idx = _normalize_threshold(op, float(m.group("value")), m.group("unit") or "")
    got = _normalize_threshold(authored.op, authored.value, authored.unit)
    if idx is None or got is None or idx[2] != got[2]:
        return None

    i_op, i_val, _ = idx
    a_op, a_val, _ = got
    lower_is_better = i_op in ("<", "<=", "≤")
    higher_is_better = i_op in (">", ">=", "≥")

    if lower_is_better:
        if a_op not in ("<", "<=", "≤", "=", "=="):
            return (f"index requires '{i_op}' but the assertion authors '{a_op}', "
                    f"which does not constrain the same direction")
        if a_val > i_val:
            return (f"authored bar {a_op} {authored.value}{authored.unit} is LOOSER "
                    f"than the index's {i_op} {m.group('value')}{m.group('unit') or ''}")
    elif higher_is_better:
        if a_op not in (">", ">=", "≥", "=", "=="):
            return (f"index requires '{i_op}' but the assertion authors '{a_op}', "
                    f"which does not constrain the same direction")
        if a_val < i_val:
            return (f"authored bar {a_op} {authored.value}{authored.unit} is LOOSER "
                    f"than the index's {i_op} {m.group('value')}{m.group('unit') or ''}")
    return None


def parse_assertion_file(path: str) -> tuple[Optional[AssertionSpec], Optional[str]]:
    """Parse + schema-validate one YAML file. Returns ``(spec, error)``."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.load(fh, Loader=_YAML_LOADER)
    except Exception as exc:  # noqa: BLE001
        return None, f"YAML parse error: {exc}"
    if not isinstance(raw, dict):
        return None, "YAML root is not a mapping"
    try:
        return AssertionSpec(**raw), None
    except ValidationError as exc:
        return None, f"Schema validation failed:\n{exc}"


def validate_assertion(spec: AssertionSpec, source: str = "<memory>") -> list[LoadDiagnostic]:
    """Public entry point for the full diagnostic set of one parsed assertion."""
    return _validate_spec(spec, source)


def is_rejection(diag: LoadDiagnostic, *, strict: bool) -> bool:
    """Whether a diagnostic prevents the assertion from loading."""
    if diag.severity != "error":
        return False
    if diag.code in _STRUCTURAL:
        return True
    if diag.code in _STRICT_GATED:
        return strict
    return True


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


def default_assertions_dir(base_dir: str) -> str:
    """Convention: ``<base>/assertions``, sibling of ``scenarios/``."""
    return os.path.join(base_dir, "assertions")


class AssertionCatalog:
    """Process-level view of the authored assertion corpus.

    Mirrors ``ttp_catalog`` / ``adapter_catalog`` / ``uctc_registry``: a pure
    read path, loaded once, re-loadable for tests. Loads lazily on first access
    so a deployment with no ``assertions/`` directory boots exactly as before.
    """

    def __init__(self) -> None:
        self._by_id: dict[str, AssertionSpec] = {}
        self._rejected: list[dict[str, Any]] = []
        self._warnings: list[dict[str, Any]] = []
        self._loaded_from: Optional[str] = None

    # ---- lifecycle -----------------------------------------------------

    def load(self, assertions_dir: str, *, strict: Optional[bool] = None) -> int:
        from config import settings  # noqa: PLC0415

        if strict is None:
            strict = bool(getattr(settings, "CORTEXSIM_STRICT_REFS", False))

        self._by_id.clear()
        self._rejected.clear()
        self._warnings.clear()
        self._loaded_from = assertions_dir

        if not os.path.isdir(assertions_dir):
            logger.info("No assertions directory at %s — assertion corpus is empty",
                        assertions_dir)
            return 0

        for path in _find_assertion_files(assertions_dir):
            spec, error = parse_assertion_file(path)
            if error or spec is None:
                self._reject(path, [LoadDiagnostic("A-01", error or "unparseable", path)])
                continue
            if spec.assertion_id in self._by_id:
                self._reject(path, [LoadDiagnostic(
                    "A-02", f"duplicate assertion_id '{spec.assertion_id}'", path)])
                continue

            diags = _validate_spec(spec, path)
            blocking = [d for d in diags if is_rejection(d, strict=strict)]
            blocking_ids = {id(d) for d in blocking}
            advisory = [d for d in diags if id(d) not in blocking_ids]
            for d in advisory:
                logger.warning("%s %s (from %s)", d.code, d.detail, d.source)
                self._warnings.append(d.to_dict())
            if blocking:
                self._reject(path, blocking)
                continue

            self._by_id[spec.assertion_id] = spec

        logger.info(
            "Assertion catalog loaded: %d assertion(s), %d rejected, %d advisory "
            "(strict_refs=%s) from %s",
            len(self._by_id), len(self._rejected), len(self._warnings),
            strict, assertions_dir,
        )
        return len(self._by_id)

    def ensure_loaded(self) -> "AssertionCatalog":
        """Load from the conventional directory the first time anything asks."""
        if self._loaded_from is None:
            from config import settings  # noqa: PLC0415
            self.load(default_assertions_dir(settings.CORTEXSIM_BASE_DIR))
        return self

    def _reject(self, path: str, diags: list[LoadDiagnostic]) -> None:
        for d in diags:
            logger.error("REJECTED %s: %s %s", path, d.code, d.detail)
        self._rejected.append({"source": path, "diagnostics": [d.to_dict() for d in diags]})

    # ---- lookups -------------------------------------------------------

    def get(self, assertion_id: Optional[str]) -> Optional[AssertionSpec]:
        return self._by_id.get(assertion_id) if assertion_id else None

    def all(self) -> list[AssertionSpec]:
        return list(self._by_id.values())

    def by_test_case(self, tc_id: str) -> list[AssertionSpec]:
        return [a for a in self._by_id.values() if tc_id in a.tc_refs]

    @property
    def rejected(self) -> list[dict[str, Any]]:
        return list(self._rejected)

    @property
    def warnings(self) -> list[dict[str, Any]]:
        return list(self._warnings)

    @property
    def loaded(self) -> bool:
        return self._loaded_from is not None

    def count(self) -> int:
        return len(self._by_id)


catalog = AssertionCatalog()


def _find_assertion_files(assertions_dir: str) -> list[str]:
    found: list[str] = []
    for root, _dirs, files in os.walk(assertions_dir):
        for fname in files:
            if fname.endswith((".yml", ".yaml")) and not fname.startswith("_"):
                found.append(os.path.join(root, fname))
    return sorted(found)


# ---------------------------------------------------------------------------
# Index metadata + entitlements
# ---------------------------------------------------------------------------


def index_meta(spec: AssertionSpec) -> dict[str, Any]:
    """The bound test case's own words, snapshotted for the readout.

    Copied, never re-authored: an authored duplicate of index-owned data is the
    drift this whole join exists to remove.
    """
    from engine.uctc_registry import registry  # noqa: PLC0415

    tc = registry.tc(spec.tc_ref)
    if tc is None:
        return {"index_loaded": registry.loaded, "tc_resolved": False}
    return {
        "index_loaded": True,
        "tc_resolved": True,
        "tc_id": tc.tc_id,
        "title": tc.title,
        "validation_class": tc.validation_class,
        "primary_kpi": tc.primary_kpi,
        "threshold_raw": tc.threshold,
        "measurement_method": tc.measurement_method,
        "success_criteria": tc.success_criteria,
        "detection_source": tc.detection_source,
        "differentiation_tier": tc.differentiation_tier,
        "priority": tc.priority,
        "is_scoreable": tc.is_scoreable,
    }


def tc_scoreable(spec: AssertionSpec) -> bool:
    """Whether the INDEX can score the bound test case.

    False for 91 of the index's 110 POS rows. A machine ``pass`` on one of those
    asserts something the index never defined, so ``score_run`` clamps it.

    With no snapshot loaded there is no index claim to clamp against, so this
    returns True — the same degradation ``validate_ref`` makes when it reports
    ``unverified`` rather than rejecting. Clamping on absent data would silently
    make every assertion unpassable in a stripped deployment.
    """
    from engine.uctc_registry import registry  # noqa: PLC0415

    if not registry.loaded:
        return True
    tc = registry.tc(spec.tc_ref)
    return bool(tc and tc.is_scoreable)


def entitlements(spec: AssertionSpec) -> tuple[list[str], list[str]]:
    from engine.uctc_registry import registry  # noqa: PLC0415

    return registry.entitlements_for_many(spec.tc_refs or [spec.tc_ref])


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


@dataclass
class CheckExecution:
    check: CheckSpec
    rendered: dict[str, str]
    outcome: ProbeOutcome
    verdict: CheckVerdict

    def to_dict(self) -> dict[str, Any]:
        probe_cls = PROBE_REGISTRY[self.check.probe]
        return {
            "check_id": self.check.check_id,
            "title": self.check.title,
            "probe": self.check.probe,
            "queries": dict(self.rendered),
            "threshold": {**self.check.threshold.as_dict(),
                          "source": self.check.threshold.source,
                          "rationale": self.check.threshold.rationale},
            "negative_control": self.check.negative_control.description,
            "failure_conditions": list(probe_cls.FAILURE_CONDITIONS),
            "measured_value": self.outcome.measured_value,
            "measured_unit": probe_cls.unit_for(self.check.params),
            "taxonomy_code": self.outcome.code,
            "remediation": self.outcome.remediation,
            "sample_rows": self.outcome.rows[:20],
            **self.verdict.to_dict(),
        }


@dataclass
class AssertionOutcome:
    assertion_id: str
    score: RunScore
    reason: Optional[str]
    checks: list[CheckExecution]
    context: dict[str, str] = field(default_factory=dict)

    @property
    def verdict(self) -> str:
        return self.score.verdict

    def to_dict(self) -> dict[str, Any]:
        return {
            "assertion_id": self.assertion_id,
            "tc_verdict": self.score.verdict,
            "tc_verdict_detail": self.score.to_dict(),
            "reason": self.reason,
            "context": dict(self.context),
            "checks": [c.to_dict() for c in self.checks],
        }


@dataclass
class _ScoreRow:
    """Duck-typed stand-in ``score_run`` reads through ``getattr``. Using the
    same field names as ``Result`` is why no parallel scorer is needed."""

    kpi_verdict: str
    kpi_contribution: Optional[dict[str, Any]]
    detection_id: str
    expected_detection: str = ""


async def _execute_check(
    check: CheckSpec, rendered: dict[str, str], runner: ScalarRunner,
    context: dict[str, str], settle: Optional[Any] = None,
) -> ProbeOutcome:
    """Run one probe, mapping tenant/transport failures onto taxonomy codes.

    A tenant error is never a capability failure. Conflating them produces false
    red in a customer readout, which is worse than reporting nothing.
    """
    probe = PROBE_REGISTRY[check.probe]()
    ctx = ProbeContext(runner=runner, params=check.params,
                       rendered=rendered, context=context, settle=settle)
    try:
        return await probe.execute(ctx)
    except Exception as exc:  # noqa: BLE001 — every failure becomes a taxonomy code
        code = _classify_exception(exc)
        logger.warning("probe %s failed for check %s: %s (%s)",
                       check.probe, check.check_id, exc, code)
        return ProbeOutcome(code, None, f"{type(exc).__name__}: {exc}")


def _classify_exception(exc: BaseException) -> str:
    name = type(exc).__name__
    if name == "XsiamAuthError":
        return "PROBE_AUTH_FAILED"
    if name == "XsiamQuotaError":
        return "PROBE_QUOTA_EXHAUSTED"
    if name in ("XsiamQueryError", "XsiamConfigError"):
        return "PROBE_QUERY_FAILED"
    if isinstance(exc, AssertionRenderError):
        return "PROBE_QUERY_FAILED"
    return "TENANT_UNREACHABLE"


async def run_assertion(
    spec: AssertionSpec,
    *,
    runner: Optional[ScalarRunner] = None,
    context: Optional[dict[str, str]] = None,
    entitled_addons: Optional[list[str]] = None,
    dry_run: bool = False,
    scoreable: Optional[bool] = None,
) -> AssertionOutcome:
    """Evaluate one assertion and score it through the existing verifier.

    ``runner`` is the injected tenant query path. ``None`` means no tenant is
    wired, and every check resolves ``pending`` — a DC with nothing configured
    must never see green.

    ``entitled_addons`` is the tenant's declared entitlement profile. Supplying
    it lets a missing add-on resolve ``not_applicable`` (a commercial fact, and
    an upsell) instead of ``fail``. Omitting it is treated as entitled, so
    silence can never buy a benign verdict.

    ``scoreable`` overrides the index's own is_scoreable verdict for the bound
    test case; leave it None to resolve it from the registry.
    """
    ctx = dict(context or {})
    ctx.setdefault("lookback_seconds", "3600")

    forced: Optional[str] = None
    if dry_run:
        forced = "DRY_RUN"
    elif runner is None:
        forced = "NO_TENANT_INTEGRATION"
    elif entitled_addons is not None:
        _base, required = entitlements(spec)
        missing = [a for a in required if a not in entitled_addons]
        if missing:
            forced = "NOT_ENTITLED"

    executions: list[CheckExecution] = []
    for check in spec.checks:
        try:
            rendered = render_check(check, ctx)
        except AssertionRenderError as exc:
            outcome = ProbeOutcome("PROBE_QUERY_FAILED", None, str(exc))
            executions.append(CheckExecution(check, {}, outcome,
                                             evaluate_check(check, outcome)))
            continue

        if forced is not None:
            outcome = ProbeOutcome(forced, None, REMEDIATION.get(forced, forced))
        else:
            assert runner is not None  # narrowed by the branch above
            outcome = await _execute_check(check, rendered, runner, ctx,
                                           settle=spec.settle)
        executions.append(CheckExecution(check, rendered, outcome,
                                         evaluate_check(check, outcome)))

    # score_run reads a result's weight out of kpi_contribution["value"], so
    # hand it a weight-shaped dict and carry the real bar alongside.
    rows = [
        _ScoreRow(
            kpi_verdict=e.verdict.verdict,
            kpi_contribution={**e.check.threshold.as_dict(),
                              "value": e.check.weight,
                              "threshold_value": e.check.threshold.value},
            detection_id=f"{spec.assertion_id}:{e.check.check_id}",
            expected_detection=e.check.title,
        )
        for e in executions
    ]

    # The run-level primary threshold is only handed to score_run when the
    # primary check produced a REAL measurement. Otherwise score_run would
    # evaluate the bar against None, resolve `pending`, and mask a check that
    # positively failed — turning a proven red into an open question.
    primary = next((e for e in executions if e.check.primary),
                   executions[0] if executions else None)
    measured = primary is not None and primary.outcome.code == MEASURED
    score = score_run(
        rows,
        threshold=primary.check.threshold.as_dict() if measured else None,
        primary_kpi=primary.check.threshold.kpi if measured else None,
        measured_value=primary.outcome.measured_value if measured else None,
        tc_scoreable=tc_scoreable(spec) if scoreable is None else scoreable,
    )

    return AssertionOutcome(spec.assertion_id, score,
                            _reason_for(score, executions), executions, ctx)


def _reason_for(score: RunScore, executions: list[CheckExecution]) -> Optional[str]:
    """A machine-readable why for the non-terminal verdicts.

    ``pending`` and ``not_applicable`` are not interchangeable and the readout
    has to say which one it is: "still owed" versus "unscoreable by
    construction". Collapsing them lets unproven claims read as benign.
    """
    if score.verdict == PASS:
        return None
    if score.verdict == FAIL:
        codes = [e.outcome.code for e in executions if e.verdict.verdict == FAIL]
        return codes[0].lower() if codes and codes[0] != MEASURED else "threshold_missed"
    relevant = [e.outcome.code for e in executions
                if e.verdict.verdict == score.verdict and e.outcome.code != MEASURED]
    if relevant:
        return relevant[0].lower()
    if score.verdict == NOT_APPLICABLE:
        return "tc_unscoreable"
    return "not_measured"


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


async def upsert_assertions(db: Any, specs: list[AssertionSpec],
                            sources: Optional[dict[str, str]] = None) -> int:
    """Upsert validated specs into the ``assertions`` table, keyed on id."""
    from sqlalchemy import select  # noqa: PLC0415
    from models import Assertion  # noqa: PLC0415

    sources = sources or {}
    existing = {a.assertion_id: a
                for a in (await db.execute(select(Assertion))).scalars().all()}

    for spec in specs:
        base, addons = entitlements(spec)
        payload = {
            "assertion_id": spec.assertion_id,
            "name": spec.name,
            "version": spec.version,
            "status": spec.status,
            "validation_class": spec.validation_class,
            "kind": spec.kind,
            "plane": spec.plane,
            "uc_ref": spec.uc_ref,
            "tc_ref": spec.tc_ref,
            "tc_refs": spec.tc_refs,
            "uc_name": spec.uc_name,
            "tc_name": spec.tc_name,
            "index_meta": index_meta(spec),
            "tc_scoreable": tc_scoreable(spec),
            "scope_limitations": spec.scope_limitations,
            "primary_kpi": spec.primary_kpi or spec.primary_check.threshold.kpi,
            "threshold": spec.primary_check.threshold.as_dict(),
            "methodology_family": spec.methodology_family,
            "moat_tier": spec.moat_tier,
            "success_criteria": spec.success_criteria,
            "required_base_platform": base,
            "required_addons": addons,
            "spec": spec.model_dump(mode="json"),
            "tags": spec.tags,
            "author": spec.author,
            "source_file": sources.get(spec.assertion_id),
        }
        row = existing.get(spec.assertion_id)
        if row is None:
            db.add(Assertion(**payload, created_at=datetime.utcnow()))
        else:
            for k, v in payload.items():
                setattr(row, k, v)

    await db.commit()
    return len(specs)


async def persist_outcome(
    db: Any, outcome: AssertionOutcome, *, run_id: str,
    tenant: Optional[str] = None, trigger_run_id: Optional[str] = None,
) -> Any:
    """Write one evaluation to ``assertion_runs`` + ``assertion_checks``."""
    from models import AssertionCheck, AssertionRun  # noqa: PLC0415

    now = datetime.utcnow()
    run = AssertionRun(
        run_id=run_id,
        assertion_id=outcome.assertion_id,
        status="complete",
        tenant=tenant,
        trigger_run_id=trigger_run_id,
        context=outcome.context,
        tc_verdict=outcome.score.verdict,
        tc_verdict_detail=outcome.score.to_dict(),
        reason=outcome.reason,
        started_at=now,
        completed_at=now,
    )
    db.add(run)

    for e in outcome.checks:
        probe_cls = PROBE_REGISTRY[e.check.probe]
        db.add(AssertionCheck(
            run_id=run_id,
            check_id=e.check.check_id,
            title=e.check.title,
            probe=e.check.probe,
            verification_xql="\n\n".join(
                f"-- {name}\n{q}" for name, q in sorted(e.rendered.items())
            ) or None,
            measured_value=e.outcome.measured_value,
            measured_unit=probe_cls.unit_for(e.check.params),
            taxonomy_code=e.outcome.code,
            remediation=e.outcome.remediation or None,
            detail=e.verdict.detail,
            negative_control=e.check.negative_control.description,
            kpi_contribution={**e.check.threshold.as_dict(),
                              "source": e.check.threshold.source},
            kpi_verdict=e.verdict.verdict,
            verified_at=now if e.outcome.code == MEASURED else None,
            sample_rows=e.outcome.rows[:20],
            detection_id=f"{outcome.assertion_id}:{e.check.check_id}",
        ))

    await db.commit()
    return run


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _as_float(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _as_epoch_seconds(v: Any) -> Optional[float]:
    """Coerce a tenant timestamp to epoch seconds.

    XSIAM writes ``_time`` as epoch milliseconds; correlation payloads often
    carry ISO-8601. Accept both rather than forcing every author to normalize
    in XQL, where a mistake is invisible until it silently reports 1970.
    """
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        val = float(v)
        # Anything past ~5138-11-16 in seconds is far more plausibly millis.
        return val / 1000.0 if val > 1e11 else val
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        if s.isdigit():
            return _as_epoch_seconds(int(s))
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    return None


def _iso(epoch_seconds: float) -> str:
    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc).isoformat()

"""Behavioural tests for the shipped AUT assertion corpus (``assertions/aut/``).

``tests/engine/test_assertions.py`` proves the *substrate* is sound. This module
proves the three AUT *artifacts* are sound, which is a different claim and the
one the anti-inflation rule actually cares about: an artifact can satisfy every
schema guard and still be a claim nobody can disprove.

So each shipped check is driven through the REAL evaluator on tenant shapes a DC
would recognise, and the assertions here are mostly negative:

* no tenant, a dry run, or a missing entitlement is never ``pass``;
* a trigger that never fired is ``pending``, never red — that is a DETECTION
  failure and painting the automation red for it is wrong;
* a trigger that fired with no response IS red — that is the capability failing;
* a ratio over an empty population is ``pending``, never a 100 % green;
* a threshold never lives inside a query, so a late tenant reports its NUMBER
  instead of being indistinguishable from a tenant that did nothing.
"""
from __future__ import annotations

import os

import pytest

AUT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "assertions", "aut",
)

AEPS_001 = "AUT-AEPS-001"
AEPS_002 = "AUT-AEPS-002"
APB_002 = "AUT-APB-002"
ALL_AUT = (AEPS_001, AEPS_002, APB_002)

# Epoch SECONDS (the probes compare plain seconds; the artifacts convert XSIAM's
# microsecond ``_time`` in the tenant's own arithmetic rather than guessing).
# A fixed epoch in the PAST (2023-11-14). It must stay in the past: the latency
# probe compares the trigger against wall-clock to decide whether the settle
# budget has expired, so a future-dated fixture would read as "the window is
# still open" forever and the OUTCOME_ABSENT path could never be exercised.
T0 = 1_700_000_000


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def loaded_registry():
    """Load the committed index snapshot into the singleton the loader reads.

    Without this the ref checks degrade to ``unverified`` and every binding
    assertion below would pass vacuously against an empty registry — the exact
    false green this file exists to prevent. So the count is asserted.
    """
    from engine.uctc_registry import registry  # noqa: PLC0415

    index_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "docs", "uc_tc_mapping", "_v2.2-source")
    registry.load(index_dir)
    assert len(registry.all_test_cases()) == 266
    return registry


@pytest.fixture(scope="module")
def aut_specs(loaded_registry):
    """Every AUT artifact, parsed and fully validated exactly as boot does."""
    from engine.assertions import parse_assertion_file, validate_assertion  # noqa: PLC0415

    specs = {}
    for fname in sorted(os.listdir(AUT_DIR)):
        if not fname.endswith((".yml", ".yaml")) or fname.startswith("_"):
            continue
        path = os.path.join(AUT_DIR, fname)
        spec, error = parse_assertion_file(path)
        assert error is None, f"{fname}: {error}"
        specs[spec.assertion_id] = (spec, validate_assertion(spec, path))
    return specs


def marker_runner(*pairs):
    """A ScalarRunner that dispatches on distinctive substrings of the query.

    Keyed on content rather than call order so a test states which QUESTION it
    is answering — reordering checks in an artifact then cannot silently swap a
    test's fixtures onto the wrong probe.
    """

    async def _run(query: str):
        for marker, rows in pairs:
            if marker in query:
                return list(rows)
        return []

    return _run


async def run(spec, runner=None, *, context=None, **kw):
    from engine.assertions import run_assertion  # noqa: PLC0415

    ctx = {"target_host": "host-pov-01", "lookback_seconds": "3600"}
    ctx.update(context or {})
    return await run_assertion(spec, runner=runner, context=ctx, **kw)


def check(outcome, check_id):
    return next(e for e in outcome.checks if e.check.check_id == check_id)


# ---------------------------------------------------------------------------
# Corpus-level guards
# ---------------------------------------------------------------------------


def test_aut_corpus_is_exactly_the_three_provable_rows(aut_specs):
    """AUT is 3 of 6, deliberately. TC-APB-01/03/04 are refused, not pending.

    APB-01's premise is a property of the CUSTOMER's playbook; APB-03 needs a
    human to judge generated-playbook quality; APB-04 needs a live integration
    deliberately broken, which is a destructive write. Binding any of them would
    move the number without proving anything.
    """
    assert sorted(aut_specs) == sorted(ALL_AUT)
    assert {s.tc_ref for s, _ in aut_specs.values()} == {
        "TC-AEPS-01", "TC-AEPS-02", "TC-APB-02"}


def test_every_aut_artifact_loads_clean_under_strict_refs(aut_specs):
    from engine.assertions import is_rejection  # noqa: PLC0415

    for aid, (_spec, diags) in aut_specs.items():
        blocking = [d.to_dict() for d in diags if is_rejection(d, strict=True)]
        assert blocking == [], f"{aid} would be rejected at boot: {blocking}"
        assert [d.to_dict() for d in diags] == [], f"{aid} raised advisories: {diags}"


def test_every_aut_binding_is_an_aut_index_row(aut_specs):
    """A-14 inverted: an assertion claiming a DET row steals credit for
    detection content nobody authored. Assert the class agreement directly
    rather than trusting the loader's diagnostic to have run."""
    from engine.uctc_registry import registry  # noqa: PLC0415

    assert registry.loaded, "the index snapshot must be present for this to mean anything"
    for aid, (spec, _diags) in aut_specs.items():
        for ref in spec.tc_refs:
            tc = registry.tc(ref)
            assert tc is not None, f"{aid}: {ref} is not in the index"
            assert tc.validation_class == "AUT", f"{aid}: {ref} is {tc.validation_class}"


def test_every_check_can_both_fail_and_pass(aut_specs):
    from engine.assertions import prove_falsifiable  # noqa: PLC0415

    for aid, (spec, _d) in aut_specs.items():
        for c in spec.checks:
            proof = prove_falsifiable(c)
            assert proof.can_fail, f"{aid}/{c.check_id} can never fail"
            assert proof.can_pass, f"{aid}/{c.check_id} can never pass"


def test_every_declared_negative_control_really_evaluates_fail(aut_specs):
    """The author's own stated failure mode, pushed through the real evaluator."""
    from engine.assertions import MEASURED, ProbeOutcome, evaluate_check  # noqa: PLC0415

    for aid, (spec, _d) in aut_specs.items():
        for c in spec.checks:
            value = c.negative_control.measured_value
            verdict = evaluate_check(c, ProbeOutcome(MEASURED, value)).verdict
            assert verdict == "fail", (
                f"{aid}/{c.check_id}: negative control {value} evaluated {verdict}")


def test_no_threshold_is_embedded_in_a_query(aut_specs):
    """The TTP-2026-0143 defect, guarded structurally.

    ``| filter sla_seconds <= 300`` returns zero rows for a tenant that took
    412 s — indistinguishable from a tenant that never responded, and the
    customer never learns the number. The bar belongs in the threshold.
    """
    import re  # noqa: PLC0415

    from engine.assertions import PROBE_REGISTRY  # noqa: PLC0415

    for aid, (spec, _d) in aut_specs.items():
        for c in spec.checks:
            bar = c.threshold.value
            literals = {str(bar)}
            if float(bar).is_integer():
                literals.add(str(int(bar)))
            pattern = re.compile(
                r"(?:<=|>=|<|>|≤|≥)\s*(?:" + "|".join(re.escape(x) for x in literals) + r")\b")
            for pname in PROBE_REGISTRY[c.probe].QUERY_PARAMS:
                for line in (c.params.get(pname) or "").splitlines():
                    assert not pattern.search(line), (
                        f"{aid}/{c.check_id}.{pname} compares against the "
                        f"threshold value in the query: {line.strip()!r}")


def test_every_outcome_assertion_polls_past_its_own_budget(aut_specs):
    """A-22 restated on the shipped artifacts: an assertion that stops looking
    before the bar expires manufactures false red."""
    for aid, (spec, _d) in aut_specs.items():
        if spec.kind != "outcome":
            continue
        s = spec.settle
        assert s is not None and s.expect_by_seconds
        assert s.max_wait_seconds >= s.expect_by_seconds + s.ingest_floor_seconds, aid
        # An outcome assertion's timing claim must come from the latency probe,
        # whose precursor query is what makes `fail` legitimate.
        assert any(c.probe == "xql_latency" and c.primary for c in spec.checks), aid


# ---------------------------------------------------------------------------
# Rule 2 — without a tenant, nothing is green
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("aid", ALL_AUT)
async def test_no_tenant_is_pending_not_green_and_not_benign(aut_specs, aid):
    spec, _ = aut_specs[aid]
    outcome = await run(spec, runner=None)
    assert outcome.verdict == "pending"
    # `pending` is "still owed". `not_applicable` reads as benign and would let
    # an unproven autonomy claim disappear.
    assert outcome.reason == "no_tenant_integration"


@pytest.mark.asyncio
@pytest.mark.parametrize("aid", ALL_AUT)
async def test_dry_run_renders_the_question_without_answering_it(aut_specs, aid):
    spec, _ = aut_specs[aid]
    outcome = await run(spec, runner=marker_runner(), dry_run=True)
    assert outcome.verdict == "pending"
    assert outcome.reason == "dry_run"
    for e in outcome.checks:
        assert e.rendered, "the exact query must still be rendered offline"
        for q in e.rendered.values():
            assert "{{" not in q and "host-pov-01" in q


@pytest.mark.asyncio
@pytest.mark.parametrize("aid", [AEPS_001, AEPS_002])
async def test_missing_addon_is_not_applicable_not_fail(aut_specs, aid):
    """A commercial gap is an upsell line, not a product failure.

    Only the two AEPS rows are gated: the index puts them behind Agentic
    Endpoint Security (AES), so a tenant without it must not be painted red for
    a capability it never bought.
    """
    spec, _ = aut_specs[aid]
    outcome = await run(spec, runner=marker_runner(), entitled_addons=["Nothing At All"])
    assert outcome.verdict == "not_applicable"
    assert outcome.reason == "not_entitled"


@pytest.mark.asyncio
async def test_apb002_has_no_capability_addon_so_it_is_always_judged(aut_specs):
    """TC-APB-02 is licensed through Extended Compute Units — metered capacity,
    not a capability gate — so the index leaves it with no add-on to be missing.

    Recorded because it is a live trap: 'declare no add-ons' must never become a
    way to buy a benign ``not_applicable``. With nothing gating it, an empty
    tenant resolves ``pending`` — still owed — and a tenant that acted on a
    guess resolves ``fail``.
    """
    from engine.assertions import entitlements  # noqa: PLC0415

    spec, _ = aut_specs[APB_002]
    _base, addons = entitlements(spec)
    assert addons == []
    outcome = await run(spec, runner=marker_runner(), entitled_addons=["Nothing At All"])
    assert outcome.verdict == "pending"


@pytest.mark.asyncio
@pytest.mark.parametrize("aid", ALL_AUT)
async def test_silence_on_entitlements_never_buys_a_benign_verdict(aut_specs, aid):
    """Omitting the tenant profile is treated as entitled, so absence is judged."""
    spec, _ = aut_specs[aid]
    outcome = await run(spec, runner=marker_runner())     # every probe returns []
    assert outcome.verdict != "not_applicable"


@pytest.mark.asyncio
@pytest.mark.parametrize("aid", ALL_AUT)
async def test_a_hostile_context_value_cannot_reshape_a_query(aut_specs, aid):
    spec, _ = aut_specs[aid]
    outcome = await run(
        spec, runner=marker_runner(),
        context={"target_host": 'x" or 1=1 | fields *'},
    )
    assert outcome.verdict == "pending"
    assert outcome.reason == "probe_query_failed"


# ---------------------------------------------------------------------------
# AUT-AEPS-002 — the outcome/latency shape
# ---------------------------------------------------------------------------


def _aeps2_runner(*, precursor, outcome, contained=(), complete=(),
                  high_risk=(), gated=()):
    # Ordered most-specific-first: several of these queries share substrings.
    return marker_runner(
        ("contained_epoch_seconds by incident_id", outcome),
        ("alert_epoch_seconds by incident_id", precursor),
        ('contains "quarantine-file"', complete),
        ('automation_actions contains "isolate-endpoint"', contained),
        ("approval_state in (", gated),
        ('action_risk = "high"', high_risk),
    )


PRECURSOR = [{"incident_id": "INC-1", "alert_epoch_seconds": T0}]
CONTAINED = [{"incident_id": "INC-1", "automation_actions":
              "isolate-endpoint,kill-process,quarantine-file"}]
HIGH_RISK = [{"incident_id": "INC-1", "automation_action": "isolate-endpoint",
              "action_risk": "high"}]
GATED = [{"incident_id": "INC-1", "automation_action": "isolate-endpoint",
          "approval_state": "approved", "approver": "soc-lead@corp.local"}]


def _aeps2_healthy(seconds_to_contain):
    """A tenant where everything worked, parameterised only on the latency."""
    return _aeps2_runner(
        precursor=PRECURSOR,
        outcome=[{"incident_id": "INC-1",
                  "contained_epoch_seconds": T0 + seconds_to_contain}],
        contained=CONTAINED, complete=CONTAINED,
        high_risk=HIGH_RISK, gated=GATED)


@pytest.mark.asyncio
async def test_aeps2_trigger_never_raised_is_pending_not_red(aut_specs):
    """The single most important row of the verdict matrix.

    You may not fail an automation for not responding to an alert that was never
    raised. The failure is then a DETECTION failure, and it belongs in the
    scenario's Result rows, not in the automation's readout.

    Note the runner returns NOTHING for any query — the shape of a tenant where
    the trigger scenario never landed. Every check must resolve pending; a
    non-primary check that went red here would re-introduce the false red
    through the side door, which is why chk-02 and chk-03 are ratios.
    """
    spec, _ = aut_specs[AEPS_002]
    outcome = await run(spec, _aeps2_runner(precursor=[], outcome=[]))
    assert check(outcome, "chk-01").outcome.code == "PRECURSOR_MISSING"
    assert check(outcome, "chk-02").outcome.code == "POPULATION_EMPTY"
    assert check(outcome, "chk-03").outcome.code == "POPULATION_EMPTY"
    assert outcome.verdict == "pending"
    assert outcome.reason == "precursor_missing"


@pytest.mark.asyncio
async def test_aeps2_trigger_proven_and_no_containment_is_red(aut_specs):
    """The ONLY legitimate 'the automation did not fire', and it is gated on
    positive proof that the trigger landed."""
    spec, _ = aut_specs[AEPS_002]
    outcome = await run(spec, _aeps2_runner(precursor=PRECURSOR, outcome=[]))
    assert check(outcome, "chk-01").outcome.code == "OUTCOME_ABSENT"
    assert outcome.verdict == "fail"


@pytest.mark.asyncio
async def test_aeps2_contained_inside_the_budget_passes_and_reports_the_number(aut_specs):
    spec, _ = aut_specs[AEPS_002]
    outcome = await run(spec, _aeps2_healthy(240))
    assert outcome.verdict == "pass"
    assert check(outcome, "chk-01").outcome.measured_value == pytest.approx(240.0)


@pytest.mark.asyncio
async def test_aeps2_late_containment_fails_with_the_measured_latency(aut_specs):
    """"612 s against a 300 s budget — the loop closed, it was late" is a
    completely different customer conversation from "nothing happened"."""
    spec, _ = aut_specs[AEPS_002]
    outcome = await run(spec, _aeps2_healthy(612))
    assert outcome.verdict == "fail"
    c = check(outcome, "chk-01")
    assert c.outcome.code == "MEASURED"          # measured, not absent
    assert c.outcome.measured_value == pytest.approx(612.0)
    assert "612" in c.verdict.detail


@pytest.mark.asyncio
async def test_aeps2_incomplete_containment_is_red_even_when_it_was_fast(aut_specs):
    """Isolating fast but never quarantining the dropped binary leaves the
    payload on disk for the moment isolation lifts. Speed does not redeem it."""
    spec, _ = aut_specs[AEPS_002]
    outcome = await run(spec, _aeps2_runner(
        precursor=PRECURSOR,
        outcome=[{"incident_id": "INC-1", "contained_epoch_seconds": T0 + 90}],
        contained=CONTAINED, complete=[],          # isolated + killed, never quarantined
        high_risk=HIGH_RISK, gated=GATED))
    assert check(outcome, "chk-01").verdict.verdict == "pass"
    assert check(outcome, "chk-02").outcome.measured_value == pytest.approx(0.0)
    assert outcome.verdict == "fail"


@pytest.mark.asyncio
async def test_aeps2_one_partial_containment_among_several_is_still_red(aut_specs):
    """The bar is absolute: 2 of 3 hosts fully contained is a containment gap,
    not a 67 % success."""
    spec, _ = aut_specs[AEPS_002]
    three = [{"incident_id": f"INC-{i}"} for i in range(3)]
    outcome = await run(spec, _aeps2_runner(
        precursor=PRECURSOR,
        outcome=[{"incident_id": "INC-1", "contained_epoch_seconds": T0 + 90}],
        contained=three, complete=three[:2],
        high_risk=HIGH_RISK, gated=GATED))
    assert check(outcome, "chk-02").outcome.measured_value == pytest.approx(66.6667, rel=1e-3)
    assert outcome.verdict == "fail"


@pytest.mark.asyncio
async def test_aeps2_ungated_high_risk_action_is_red(aut_specs):
    spec, _ = aut_specs[AEPS_002]
    outcome = await run(spec, _aeps2_runner(
        precursor=PRECURSOR,
        outcome=[{"incident_id": "INC-1", "contained_epoch_seconds": T0 + 90}],
        contained=CONTAINED, complete=CONTAINED,
        high_risk=HIGH_RISK, gated=[]))
    assert check(outcome, "chk-03").outcome.measured_value == pytest.approx(0.0)
    assert outcome.verdict == "fail"


@pytest.mark.asyncio
async def test_aeps2_agent_never_reached_for_a_high_risk_action_is_pending(aut_specs):
    """An approval gate nobody exercised is unproven, not proven. A count-based
    'zero ungated actions' check would have called this green."""
    spec, _ = aut_specs[AEPS_002]
    outcome = await run(spec, _aeps2_runner(
        precursor=PRECURSOR,
        outcome=[{"incident_id": "INC-1", "contained_epoch_seconds": T0 + 90}],
        contained=CONTAINED, complete=CONTAINED,
        high_risk=[], gated=[]))
    assert check(outcome, "chk-03").outcome.code == "POPULATION_EMPTY"
    assert check(outcome, "chk-03").verdict.verdict == "pending"
    assert outcome.verdict == "pending"


@pytest.mark.asyncio
async def test_aeps2_tenant_error_is_pending_never_a_capability_failure(aut_specs):
    spec, _ = aut_specs[AEPS_002]

    async def boom(_query):
        raise TimeoutError("tenant did not answer")

    outcome = await run(spec, boom)
    assert outcome.verdict == "pending"
    assert all(e.outcome.code == "TENANT_UNREACHABLE" for e in outcome.checks)


# ---------------------------------------------------------------------------
# AUT-AEPS-001 — the ratio shape
# ---------------------------------------------------------------------------


def _aeps1_runner(*, population, numerator, dispositions=None, traced=None):
    """``dispositions`` defaults to ``numerator`` — chk-02's population IS the
    set chk-01 counted as autonomous — and ``traced`` to all of them."""
    dispositions = numerator if dispositions is None else dispositions
    traced = dispositions if traced is None else traced
    return marker_runner(
        ("triage_rationale != null", traced),
        ("fields incident_id, triage_disposition, triage_rationale", dispositions),
        ("triage_disposition in (", numerator),
        ("alert_name, triage_actor_type", population),
    )


def _incidents(n, **extra):
    return [{"incident_id": f"INC-{i}", **extra} for i in range(n)]


@pytest.mark.asyncio
async def test_aeps1_zero_of_zero_is_pending_not_a_hundred_percent(aut_specs):
    """The purest form of the inflation this substrate exists to prevent —
    and no check may go red either, because nothing was ever asked of the agent.
    """
    spec, _ = aut_specs[AEPS_001]
    outcome = await run(spec, _aeps1_runner(population=[], numerator=[]))
    assert check(outcome, "chk-01").outcome.code == "POPULATION_EMPTY"
    assert check(outcome, "chk-02").outcome.code == "POPULATION_EMPTY"
    assert outcome.verdict == "pending"


@pytest.mark.asyncio
async def test_aeps1_population_below_the_declared_floor_is_pending(aut_specs):
    """4 alerts cannot establish a >90 % rate; min_population refuses to try."""
    spec, _ = aut_specs[AEPS_001]
    outcome = await run(spec, _aeps1_runner(
        population=_incidents(4), numerator=_incidents(4)))
    assert check(outcome, "chk-01").outcome.code == "POPULATION_EMPTY"
    assert outcome.verdict == "pending"


@pytest.mark.asyncio
async def test_aeps1_analysts_still_shadowing_the_agent_is_red(aut_specs):
    spec, _ = aut_specs[AEPS_001]
    outcome = await run(spec, _aeps1_runner(
        population=_incidents(10), numerator=_incidents(6)))
    assert check(outcome, "chk-01").outcome.measured_value == pytest.approx(60.0)
    assert outcome.verdict == "fail"


@pytest.mark.asyncio
async def test_aeps1_full_autonomy_with_reasoning_traces_passes(aut_specs):
    spec, _ = aut_specs[AEPS_001]
    outcome = await run(spec, _aeps1_runner(
        population=_incidents(10), numerator=_incidents(10)))
    assert check(outcome, "chk-01").outcome.measured_value == pytest.approx(100.0)
    assert outcome.verdict == "pass"


@pytest.mark.asyncio
async def test_aeps1_blanket_auto_close_with_no_reasoning_is_red(aut_specs):
    """100 % 'automation' with zero investigation behind it is suppression, not
    triage. chk-02 is the only thing standing between the two."""
    spec, _ = aut_specs[AEPS_001]
    outcome = await run(spec, _aeps1_runner(
        population=_incidents(10), numerator=_incidents(10), traced=[]))
    assert check(outcome, "chk-01").verdict.verdict == "pass"
    assert check(outcome, "chk-02").outcome.measured_value == pytest.approx(0.0)
    assert outcome.verdict == "fail"


@pytest.mark.asyncio
async def test_aeps1_exactly_at_the_bar_fails_because_the_index_says_strictly_greater(aut_specs):
    spec, _ = aut_specs[AEPS_001]
    outcome = await run(spec, _aeps1_runner(
        population=_incidents(10), numerator=_incidents(9)))
    assert check(outcome, "chk-01").outcome.measured_value == pytest.approx(90.0)
    assert outcome.verdict == "fail"


# ---------------------------------------------------------------------------
# AUT-APB-002 — the refusal shape
# ---------------------------------------------------------------------------


def _apb2_runner(*, low_conf, escalated_clean, escalated, with_trace):
    return marker_runner(
        ("agent_reasoning_trace != null", with_trace),
        ("fields incident_id, agent_reasoning_trace", escalated),
        ("agent_action_count = 0", escalated_clean),
        ("agent_confidence_band, agent_action_count", low_conf),
    )


@pytest.mark.asyncio
async def test_apb2_platform_judged_nothing_low_confidence_is_pending(aut_specs):
    """The engine supplies ambiguous input; it cannot force the platform's
    confidence verdict. An empty low-confidence set is 'still owed', not a pass —
    and emphatically not a claim that graceful degradation works."""
    spec, _ = aut_specs[APB_002]
    outcome = await run(spec, _apb2_runner(
        low_conf=[], escalated_clean=[], escalated=[], with_trace=[]))
    assert check(outcome, "chk-01").outcome.code == "POPULATION_EMPTY"
    assert outcome.verdict == "pending"


@pytest.mark.asyncio
async def test_apb2_one_autonomous_action_on_a_guess_is_red(aut_specs):
    spec, _ = aut_specs[APB_002]
    outcome = await run(spec, _apb2_runner(
        low_conf=_incidents(3), escalated_clean=_incidents(2),
        escalated=_incidents(3), with_trace=_incidents(3)))
    assert check(outcome, "chk-01").outcome.measured_value == pytest.approx(66.6667, rel=1e-3)
    assert outcome.verdict == "fail"


@pytest.mark.asyncio
async def test_apb2_halt_and_escalate_with_traces_passes(aut_specs):
    spec, _ = aut_specs[APB_002]
    outcome = await run(spec, _apb2_runner(
        low_conf=_incidents(4), escalated_clean=_incidents(4),
        escalated=_incidents(4), with_trace=_incidents(4)))
    assert outcome.verdict == "pass"


@pytest.mark.asyncio
async def test_apb2_escalation_without_a_reasoning_trace_is_red(aut_specs):
    """An escalation with no reasoning is a ticket, not a hand-over — the
    analyst restarts the investigation from zero."""
    spec, _ = aut_specs[APB_002]
    outcome = await run(spec, _apb2_runner(
        low_conf=_incidents(4), escalated_clean=_incidents(4),
        escalated=_incidents(4), with_trace=_incidents(2)))
    assert check(outcome, "chk-01").verdict.verdict == "pass"
    assert check(outcome, "chk-02").outcome.measured_value == pytest.approx(50.0)
    assert outcome.verdict == "fail"


# ---------------------------------------------------------------------------
# The scoreability contract — a pass the index cannot score is not a pass
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_three_bound_rows_carry_a_measurable_index_threshold(aut_specs):
    """These three are scoreable by the index, so a green here is a real green.

    Recorded as a test because the moment one of them is edited to a
    'Qualitative pass' row, ``score_run`` must clamp the pass to
    ``not_applicable`` and the coverage readout has to change with it.
    """
    from engine.assertions import tc_scoreable  # noqa: PLC0415

    for aid, (spec, _d) in aut_specs.items():
        assert tc_scoreable(spec) is True, aid


@pytest.mark.asyncio
async def test_an_unscoreable_index_row_clamps_pass_to_evidence(aut_specs):
    """The clamp, exercised on a real artifact: same tenant data, same checks,
    but a bound row the index never set a number for cannot be machine-certified
    as a pass. FAIL is never clamped — you can disprove a qualitative claim."""
    spec, _ = aut_specs[AEPS_001]
    green = _aeps1_runner(population=_incidents(10), numerator=_incidents(10))
    assert (await run(spec, green, scoreable=True)).verdict == "pass"

    clamped = await run(
        spec, _aeps1_runner(population=_incidents(10), numerator=_incidents(10)),
        scoreable=False,
    )
    assert clamped.verdict == "not_applicable"
    assert "evidence, not a scored pass" in clamped.score.detail

    still_red = await run(
        spec, _aeps1_runner(population=_incidents(10), numerator=_incidents(6)),
        scoreable=False,
    )
    assert still_red.verdict == "fail"


@pytest.mark.asyncio
async def test_absent_outcome_inside_the_settle_budget_is_pending_not_red(aut_specs):
    """"Not yet" is not "never".

    The latency probe holds the trigger timestamp and the artifact declares a
    settle budget, so an absent outcome inside that budget means the engine
    asked too early — platform ingest lag alone is tens of seconds. Reporting
    OUTCOME_ABSENT there tells a customer their auto-containment is broken while
    it is still in flight, and false red in a readout costs as much as false
    green.

    Same tenant shape as the red test above; the ONLY difference is that the
    trigger just happened.
    """
    import time  # noqa: PLC0415

    spec, _ = aut_specs[AEPS_002]
    just_now = time.time() - 5          # 5s into a 300s expect_by budget
    recent = [{"incident_id": "INC-1", "alert_epoch_seconds": just_now}]

    outcome = await run(spec, _aeps2_runner(precursor=recent, outcome=[]))
    assert check(outcome, "chk-01").outcome.code == "SETTLE_WINDOW_OPEN"
    assert outcome.verdict == "pending"


@pytest.mark.asyncio
async def test_absent_outcome_after_the_budget_expires_is_red(aut_specs):
    """The other side of the same guard — once the budget has genuinely
    elapsed, absence IS the capability failing and must go red."""
    import time  # noqa: PLC0415

    spec, _ = aut_specs[AEPS_002]
    long_ago = time.time() - 3600       # well past the 300s expect_by budget
    stale = [{"incident_id": "INC-1", "alert_epoch_seconds": long_ago}]

    outcome = await run(spec, _aeps2_runner(precursor=stale, outcome=[]))
    assert check(outcome, "chk-01").outcome.code == "OUTCOME_ABSENT"
    assert outcome.verdict == "fail"

"""Behavioural tests for the shipped POS assertion pack (``assertions/pos/``).

``tests/engine/test_assertions.py`` proves the substrate is sound. This module
proves the shipped POS *artifacts* are sound, which is the claim the
anti-inflation rule cares about: an artifact can satisfy every schema guard and
still be a claim nobody can disprove.

The load-bearing tests here are negative, and two of them are the reason the
file exists:

* **A POS assertion must never go GREEN without a tenant.** No integration, a
  dry run, an unreachable tenant, a 401, a quota error — all ``pending``
  ("still owed"), never ``pass`` and never ``not_applicable`` ("unscoreable by
  construction"). Collapsing those two would let 92 open POS gaps disappear into
  a bucket that reads as benign.

The Terraform-fixtured artifacts (POS-CSPM/DSPM/AISP/ASM) and their
planted-arity and tag-join tests moved to ``feature/scenarios-cloud-posture``
with ``infra/`` on 2026-11-15. The artifacts left here are stimulated by a
scenario run or an EAL campaign.
"""
from __future__ import annotations

import os
import re

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
POS_DIR = os.path.join(REPO_ROOT, "assertions", "pos")
SCENARIOS = os.path.join(REPO_ROOT, "scenarios")

ERV_002 = "POS-ERV-002"
ERV_004 = "POS-ERV-004"
AGTX_008 = "POS-AGTX-008"

ALL_POS = (ERV_002, ERV_004, AGTX_008)

# The index rows each artifact binds. Asserted explicitly so a re-key of the
# pack cannot silently move a binding onto a different test case.
EXPECTED_BINDINGS = {
    ERV_002: ("UCS-ERV-01", "TC-ERV-02"),
    ERV_004: ("UCS-ERV-02", "TC-ERV-04"),
    AGTX_008: ("UCS-AGTX-04", "TC-AGTX-08"),
}

# Measurable threshold rows in the live index.
SCOREABLE = {AGTX_008}

# Context every artifact needs to render. The behaviourally-stimulated POS
# artifacts (scenario_run / eal_campaign) bind their own context. Without an
# entry here their queries cannot render and every outcome resolves
# PROBE_QUERY_FAILED, which masks the very codes the anti-inflation tests below
# are asserting.
CONTEXT = {
    "target_host": "cortexsim-lab-node-01",
    "run_tag": "cortexsim-run-3f9a1c",
    "canary": "cxs3f9a1c",
}


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def index_loaded():
    """Load the UC/TC snapshot the way boot does.

    Without it every index-binding assertion in this module degrades to
    ``unverified`` and silently skips — which would leave the pack's whole
    reason for existing (a validated FK into the master index) untested.
    """
    from engine.uctc_registry import default_index_dir, registry  # noqa: PLC0415

    if not registry.loaded:
        registry.load(default_index_dir(REPO_ROOT))
    return registry


@pytest.fixture(scope="module")
def pos_specs(index_loaded):
    """Every POS artifact, parsed and fully validated exactly as boot does."""
    from engine.assertions import parse_assertion_file, validate_assertion  # noqa: PLC0415

    specs = {}
    for fname in sorted(os.listdir(POS_DIR)):
        if not fname.endswith((".yml", ".yaml")) or fname.startswith("_"):
            continue
        path = os.path.join(POS_DIR, fname)
        spec, error = parse_assertion_file(path)
        assert error is None, f"{fname}: {error}"
        specs[spec.assertion_id] = (spec, validate_assertion(spec, path))
    return specs


def spec_of(pos_specs, assertion_id):
    assert assertion_id in pos_specs, f"{assertion_id} is not in {sorted(pos_specs)}"
    return pos_specs[assertion_id][0]


def constant_runner(rows):
    """A tenant that answers every query with the same rows."""

    async def _run(_query):
        return list(rows)

    return _run


def raising_runner(exc):
    async def _run(_query):
        raise exc

    return _run


def queries_of(spec):
    from engine.assertions import PROBE_REGISTRY  # noqa: PLC0415

    out = []
    for check in spec.checks:
        for name in PROBE_REGISTRY[check.probe].QUERY_PARAMS:
            raw = check.params.get(name)
            if isinstance(raw, str):
                out.append((check, name, raw))
    return out


async def evaluate(spec, **kwargs):
    from engine.assertions import run_assertion  # noqa: PLC0415

    kwargs.setdefault("context", dict(CONTEXT))
    return await run_assertion(spec, **kwargs)


# ---------------------------------------------------------------------------
# The pack loads, binds, and is complete
# ---------------------------------------------------------------------------


def test_pack_contains_exactly_the_shipped_artifacts(pos_specs):
    assert sorted(pos_specs) == sorted(ALL_POS)


def test_every_artifact_loads_clean_under_strict_refs(pos_specs):
    """No diagnostic of any severity. A POS artifact that only loads because
    CORTEXSIM_STRICT_REFS is off is an artifact bound to an index row nobody
    verified."""
    from engine.assertions import is_rejection  # noqa: PLC0415

    for aid, (_spec, diags) in sorted(pos_specs.items()):
        blocking = [d.to_dict() for d in diags if is_rejection(d, strict=True)]
        assert not blocking, f"{aid} would be rejected at boot: {blocking}"
        assert not diags, f"{aid} carries advisory diagnostics: {[d.to_dict() for d in diags]}"


def test_bindings_are_the_declared_index_rows(pos_specs):
    for aid, (uc_ref, tc_ref) in EXPECTED_BINDINGS.items():
        spec = spec_of(pos_specs, aid)
        assert (spec.uc_ref, spec.tc_ref) == (uc_ref, tc_ref)
        assert spec.tc_refs == [tc_ref], (
            f"{aid} claims evidence for {spec.tc_refs}; this pack binds one row "
            f"per artifact so a partial proof cannot be counted twice"
        )


def test_every_artifact_binds_a_pos_row_in_the_live_index(pos_specs):
    """A-14 inverted: an assertion on a DET row would claim credit for detection
    content nobody authored. Proven against the registry, not the YAML."""
    from engine.uctc_registry import registry  # noqa: PLC0415

    if not registry.loaded:
        pytest.skip("UC/TC index snapshot absent — FK validation degraded to advisory")
    for aid in ALL_POS:
        spec = spec_of(pos_specs, aid)
        tc = registry.tc(spec.tc_ref)
        assert tc is not None, f"{aid}: {spec.tc_ref} is not in the index"
        assert tc.validation_class == "POS", (
            f"{aid} binds {tc.tc_id}, which the index classes "
            f"{tc.validation_class!r} — not a posture assertion"
        )


def test_scoreable_split_matches_the_index(pos_specs):
    """The honest coverage story: only AGTX-008 can produce a scored pass."""
    from engine.assertions import tc_scoreable  # noqa: PLC0415
    from engine.uctc_registry import registry  # noqa: PLC0415

    if not registry.loaded:
        pytest.skip("UC/TC index snapshot absent")
    actual = {aid for aid in ALL_POS if tc_scoreable(spec_of(pos_specs, aid))}
    assert actual == SCOREABLE


def test_every_artifact_declares_what_it_does_not_prove(pos_specs):
    """A-19 in spirit rather than letter: a scope block that is present but
    empty of the word "NOT" is a box-tick, and a partial proof that hides its
    edges is a false claim."""
    for aid in ALL_POS:
        spec = spec_of(pos_specs, aid)
        assert len(spec.scope_limitations) > 200, f"{aid}: scope_limitations is a stub"
        # The emphasised NOT is the signal — a scope block that never shouts a
        # limitation is a box-tick. Sentence-initial capitalisation is not, so
        # match [Dd]oes while keeping NOT case-sensitive.
        assert re.search(r"\b[Dd]oes NOT\b", spec.scope_limitations), (
            f"{aid}: scope_limitations never states a thing this does not prove"
        )


def test_every_artifact_plants_the_state_it_measures(pos_specs):
    """A POS assertion whose stimulus the engine does not control is asking a
    question about somebody else's cloud account.

    The original form of this guard required `iac_fixture` outright, because
    every POS artifact then shipping planted a misconfiguration with Terraform.
    The principle was never the mechanism — it is that the ENGINE must own the
    ground truth before it reads a posture back. A scenario run the engine
    executed satisfies that as completely as a fixture it applied; `none` does
    not, and that is what must stay unreachable. So the rule is now stated as
    the principle, with the fixture-specific obligations still enforced in full
    wherever a fixture IS the stimulus."""
    engine_controlled = {"iac_fixture", "scenario_run", "eal_campaign"}
    for aid in ALL_POS:
        spec = spec_of(pos_specs, aid)
        assert spec.stimulus.kind in engine_controlled, (
            f"{aid}: stimulus.kind={spec.stimulus.kind} — a POS assertion must "
            f"plant or generate the state it measures, not ask an uncontrolled tenant"
        )
        assert spec.stimulus.detail, f"{aid}: stimulus carries no operator detail"
        if spec.stimulus.kind == "scenario_run":
            # The scenario must actually be in the corpus, or the artifact names
            # a stimulus nobody can run — the scenario_run equivalent of naming
            # a Terraform module that was never written.
            sid = spec.stimulus.scenario_id or ""
            assert sid, f"{aid}: scenario_run stimulus names no scenario_id"
            found = any(sid in open(os.path.join(dirpath, f)).read()
                        for dirpath, _d, files in os.walk(SCENARIOS)
                        for f in files if f.endswith(".yml"))
            assert found, f"{aid} depends on scenario {sid!r}, which is not in the corpus"


# ---------------------------------------------------------------------------
# THE ANTI-INFLATION TESTS
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_tenant_is_pending_never_green(pos_specs):
    """``pending`` means "still owed"; ``not_applicable`` means "unscoreable by
    construction". A DC with nothing configured must see the first."""
    for aid in ALL_POS:
        spec = spec_of(pos_specs, aid)
        outcome = await evaluate(spec, runner=None)
        assert outcome.verdict == "pending", f"{aid}: {outcome.verdict}"
        assert outcome.reason == "no_tenant_integration"


@pytest.mark.asyncio
async def test_dry_run_is_pending_and_still_renders_the_question(pos_specs):
    """The offline value of the pack: the exact query is printed beside the
    planted item, with a verdict that is honestly still owed."""
    for aid in ALL_POS:
        spec = spec_of(pos_specs, aid)
        outcome = await evaluate(spec, runner=constant_runner([]), dry_run=True)
        assert outcome.verdict == "pending"
        assert outcome.reason == "dry_run"
        for execution in outcome.checks:
            assert execution.rendered, f"{aid}/{execution.check.check_id} rendered nothing"
            for text in execution.rendered.values():
                assert "{{" not in text, f"{aid}: unsubstituted placeholder in {text!r}"


@pytest.mark.asyncio
async def test_tenant_and_transport_errors_are_pending_never_fail(pos_specs):
    """A tenant outage is never a posture failure. Reporting one as red is a
    false negative in a customer readout, which is worse than reporting nothing.
    An unknown dataset lands here too — which is exactly what happens on the
    first live run if `cortex_cloud_posture` is not this tenant's real name."""
    # The real client errors: the classifier maps by class, so a look-alike
    # with the same name is not the error the runner raises.
    from integrations.xsiam.exceptions import (  # noqa: PLC0415
        XsiamAuthError,
        XsiamQueryError,
        XsiamQuotaError,
    )

    cases = {
        "probe_auth_failed": XsiamAuthError("401"),
        "probe_quota_exhausted": XsiamQuotaError("429"),
        "probe_query_failed": XsiamQueryError("unknown dataset cortex_cloud_posture"),
        "tenant_unreachable": ConnectionError("connection refused"),
    }
    for aid in ALL_POS:
        spec = spec_of(pos_specs, aid)
        for reason, exc in cases.items():
            outcome = await evaluate(spec, runner=raising_runner(exc))
            assert outcome.verdict == "pending", f"{aid}/{reason}: {outcome.verdict}"
            assert outcome.reason == reason


@pytest.mark.asyncio
async def test_missing_context_value_is_pending_not_a_silent_bad_query(pos_specs):
    """Every artifact needs operator-supplied context. Forgetting it must not
    run a half-rendered query."""
    for aid in ALL_POS:
        spec = spec_of(pos_specs, aid)
        outcome = await evaluate(spec, runner=constant_runner([]), context={})
        assert outcome.verdict == "pending", f"{aid}: {outcome.verdict}"
        assert outcome.reason == "probe_query_failed"


@pytest.mark.asyncio
async def test_injection_shaped_context_value_is_refused(pos_specs):
    """Context lands inside query text, so anything that could reshape the query
    is refused before substitution — pending, never a silent bad query."""
    spec = spec_of(pos_specs, ERV_002)
    outcome = await evaluate(
        spec, runner=constant_runner([]),
        context={**CONTEXT, "target_host": 'host-01" or true or "'},
    )
    assert outcome.verdict == "pending"
    assert outcome.reason == "probe_query_failed"


# ---------------------------------------------------------------------------
# Authoring hygiene the contract asks for
# ---------------------------------------------------------------------------


def test_no_threshold_is_embedded_in_a_query(pos_specs):
    """The probe computes the measurement; the evaluator compares it. A query
    that aggregates its own distinct_key and filters the result returns nothing
    for a tenant that fell short, which is indistinguishable from a tenant that
    was never scanned — and the customer never learns the number."""
    for aid in ALL_POS:
        spec = spec_of(pos_specs, aid)
        for check, _name, query in queries_of(spec):
            key = check.params.get("distinct_key")
            if not key:
                continue
            assert f"count_distinct({key})" not in query, (
                f"{aid}/{check.check_id} computes its own measurement in XQL"
            )


def test_every_check_can_fail_and_can_pass(pos_specs):
    """A-17, re-run here over the shipped artifacts rather than trusted from the
    loader: a check satisfied by every value its probe can return proves
    nothing, and a check failed by every value manufactures red."""
    from engine.assertions import prove_falsifiable, prove_negative_control  # noqa: PLC0415

    for aid in ALL_POS:
        spec = spec_of(pos_specs, aid)
        for check in spec.checks:
            proof = prove_falsifiable(check)
            assert proof.can_fail, f"{aid}/{check.check_id} can never fail"
            assert proof.can_pass, f"{aid}/{check.check_id} can never pass"
            ok, why = prove_negative_control(check)
            assert ok, f"{aid}/{check.check_id}: {why}"


def test_negative_controls_describe_a_failure_a_customer_would_recognise(pos_specs):
    for aid in ALL_POS:
        spec = spec_of(pos_specs, aid)
        for check in spec.checks:
            desc = check.negative_control.description
            assert len(desc) > 60, f"{aid}/{check.check_id}: negative control is a stub"


def test_sharpened_thresholds_carry_a_rationale(pos_specs):
    """A number the index did not set must justify itself out loud."""
    for aid in ALL_POS:
        spec = spec_of(pos_specs, aid)
        for check in spec.checks:
            if check.threshold.source == "authored_sharpening":
                assert (check.threshold.rationale or "").strip(), (
                    f"{aid}/{check.check_id} sharpens without a rationale"
                )
                assert "Qualitative pass" in (check.threshold.rationale or ""), (
                    f"{aid}/{check.check_id}: the rationale should name the index "
                    f"threshold it is sharpening"
                )
            else:
                assert check.threshold.source == "index"
                assert aid in SCOREABLE, (
                    f"{aid} claims threshold.source=index on a row the index does "
                    f"not score"
                )


def test_queries_are_time_bounded(pos_specs):
    """An unbounded posture query can prove today's claim with last month's
    findings — including findings from a lab that has since been destroyed."""
    for aid in ALL_POS:
        for _c, _n, query in queries_of(spec_of(pos_specs, aid)):
            assert "to_timestamp(current_time() -" in query, (
                f"{aid}: query is not time-bounded:\n{query}"
            )

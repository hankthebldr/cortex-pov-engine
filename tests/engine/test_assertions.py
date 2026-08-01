"""Tests for the assertion substrate — the POS / PLT / AUT proof mechanism.

The load-bearing properties are all negative, and they are the reason this
module exists:

* an assertion that CANNOT FAIL must not load (A-17 / A-18);
* an assertion evaluated without a tenant must never come back ``pass``;
* a tenant/transport error must never be reported as a capability failure;
* every probe's declared FAILURE_CONDITIONS must actually produce ``fail``.

Everything here runs offline. The tenant query path is injected.
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def base_spec(**overrides):
    """A minimal, valid PLT assertion. Overrides shallow-merge at the top level."""
    spec = {
        "assertion_id": "PLT-IR-001",
        "name": "Broker VM heterogeneous ingest",
        "validation_class": "PLT",
        "kind": "state",
        "uc_ref": "UCS-IR-04",
        "tc_ref": "TC-IR-08",
        "scope_limitations": (
            "Proves three sources land and normalize. Does NOT prove the records "
            "traversed a Broker VM specifically."
        ),
        "checks": [
            {
                "check_id": "chk-01",
                "title": "Three heterogeneous sources normalized",
                "probe": "xql_distinct",
                "primary": True,
                "params": {
                    "query": 'dataset in (a, b, c) | filter x contains "{{nonce}}"',
                    "distinct_key": "_dataset",
                },
                "threshold": {
                    "kpi": "sources normalized", "op": ">=", "value": 3,
                    "source": "authored_sharpening",
                    "rationale": "'3+ heterogeneous sources' is objectively countable",
                },
                "negative_control": {
                    "description": "only two of the three sources normalize",
                    "measured_value": 2,
                },
            }
        ],
    }
    spec.update(overrides)
    return spec


def parse(**overrides):
    from engine.assertions import AssertionSpec  # noqa: PLC0415

    return AssertionSpec(**base_spec(**overrides))


def codes(diags):
    return sorted({d.code for d in diags})


def rows_runner(*batches):
    """Return a ScalarRunner yielding each batch in call order."""
    calls = list(batches)

    async def _run(_query):
        return calls.pop(0) if calls else []

    return _run


def raising_runner(exc):
    async def _run(_query):
        raise exc

    return _run


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_a_valid_assertion_parses_and_validates_clean():
    from engine.assertions import validate_assertion  # noqa: PLC0415

    spec = parse()
    structural = [d for d in validate_assertion(spec)
                  if d.code in ("A-17", "A-18", "A-19", "A-20", "A-21", "A-24")]
    assert structural == []
    assert spec.tc_refs == ["TC-IR-08"]


def test_assertion_id_prefix_must_match_validation_class():
    from pydantic import ValidationError  # noqa: PLC0415
    from engine.assertions import AssertionSpec  # noqa: PLC0415

    with pytest.raises(ValidationError, match="disagrees with validation_class"):
        AssertionSpec(**base_spec(assertion_id="POS-IR-001"))


def test_outcome_kind_requires_a_settle_block():
    from pydantic import ValidationError  # noqa: PLC0415
    from engine.assertions import AssertionSpec  # noqa: PLC0415

    with pytest.raises(ValidationError, match="settle block"):
        AssertionSpec(**base_spec(kind="outcome"))


def test_sharpened_threshold_requires_a_rationale():
    """Inventing a number the index did not set is legitimate. Doing it
    silently is not."""
    from pydantic import ValidationError  # noqa: PLC0415
    from engine.assertions import AssertionSpec  # noqa: PLC0415

    body = base_spec()
    body["checks"][0]["threshold"].pop("rationale")
    with pytest.raises(ValidationError, match="requires a rationale"):
        AssertionSpec(**body)


def test_an_assertion_with_no_checks_asserts_nothing():
    from pydantic import ValidationError  # noqa: PLC0415
    from engine.assertions import AssertionSpec  # noqa: PLC0415

    with pytest.raises(ValidationError, match="asserts nothing"):
        AssertionSpec(**base_spec(checks=[]))


# ---------------------------------------------------------------------------
# A-17 — the falsifiability guard. The single most important test file here.
# ---------------------------------------------------------------------------


def test_a17_rejects_a_threshold_no_measurement_can_fail():
    """`>= 0` on a row count is satisfied by every value a count probe can
    return. 61 corpus queries carry exactly this shape today."""
    from engine.assertions import validate_assertion  # noqa: PLC0415

    body = base_spec()
    body["checks"][0]["threshold"].update({"op": ">=", "value": 0})
    body["checks"][0]["negative_control"]["measured_value"] = 0
    diags = validate_assertion(parse(**{"checks": body["checks"]}))
    assert "A-17" in codes(diags)
    assert any("can never fail" in d.detail for d in diags if d.code == "A-17")


def test_a17_rejects_a_threshold_no_measurement_can_satisfy():
    """A check that can only go red manufactures false red."""
    from engine.assertions import validate_assertion  # noqa: PLC0415

    body = base_spec()
    body["checks"][0]["threshold"].update({"op": "<", "value": 0})
    diags = validate_assertion(parse(**{"checks": body["checks"]}))
    assert "A-17" in codes(diags)
    assert any("can never be satisfied" in d.detail for d in diags if d.code == "A-17")


def test_a17_is_proven_by_executing_the_real_evaluator():
    from engine.assertions import prove_falsifiable  # noqa: PLC0415

    proof = prove_falsifiable(parse().checks[0])
    assert proof.ok
    assert proof.failing_value is not None and proof.failing_value < 3
    assert proof.passing_value is not None and proof.passing_value >= 3


def test_a17_is_structural_and_strict_refs_cannot_wave_it_through():
    from engine.assertions import LoadDiagnostic, is_rejection  # noqa: PLC0415

    d = LoadDiagnostic("A-17", "cannot fail", "x.yml")
    assert is_rejection(d, strict=True)
    assert is_rejection(d, strict=False)


def test_index_ref_codes_are_gated_by_strict_refs_exactly_like_scenarios():
    from engine.assertions import LoadDiagnostic, is_rejection  # noqa: PLC0415

    d = LoadDiagnostic("A-10", "dangling tc_ref", "x.yml")
    assert is_rejection(d, strict=True)
    assert not is_rejection(d, strict=False)


# ---------------------------------------------------------------------------
# A-18 — the author's own stated failure mode must actually go red
# ---------------------------------------------------------------------------


def test_a18_rejects_a_negative_control_that_does_not_actually_fail():
    from engine.assertions import validate_assertion  # noqa: PLC0415

    body = base_spec()
    body["checks"][0]["negative_control"]["measured_value"] = 5  # >= 3, so it passes
    diags = validate_assertion(parse(**{"checks": body["checks"]}))
    assert "A-18" in codes(diags)
    assert any("not 'fail'" in d.detail for d in diags if d.code == "A-18")


def test_a18_rejects_a_negative_control_outside_the_probe_domain():
    """A row count cannot be -1, so declaring one does not prove failability."""
    from engine.assertions import validate_assertion  # noqa: PLC0415

    body = base_spec()
    body["checks"][0]["negative_control"]["measured_value"] = -1
    diags = validate_assertion(parse(**{"checks": body["checks"]}))
    assert "A-18" in codes(diags)
    assert any("outside the 'count' domain" in d.detail for d in diags if d.code == "A-18")


# ---------------------------------------------------------------------------
# A-19 / A-20 / A-21 / A-22 / A-24
# ---------------------------------------------------------------------------


def test_a19_rejects_a_blank_scope_limitations():
    from engine.assertions import validate_assertion  # noqa: PLC0415

    diags = validate_assertion(parse(scope_limitations="   "))
    assert "A-19" in codes(diags)


def test_a20_rejects_an_unknown_probe():
    from engine.assertions import validate_assertion  # noqa: PLC0415

    body = base_spec()
    body["checks"][0]["probe"] = "telepathy"
    diags = validate_assertion(parse(**{"checks": body["checks"]}))
    assert "A-20" in codes(diags)


def test_a21_rejects_an_undeclared_template_placeholder():
    from engine.assertions import validate_assertion  # noqa: PLC0415

    body = base_spec()
    body["checks"][0]["params"]["query"] = 'dataset = a | filter u = "{{secret_var}}"'
    diags = validate_assertion(parse(**{"checks": body["checks"]}))
    assert "A-21" in codes(diags)


def test_a21_accepts_a_declared_context_var():
    from engine.assertions import validate_assertion  # noqa: PLC0415

    body = base_spec()
    body["checks"][0]["params"]["query"] = 'dataset = a | filter u = "{{canary}}"'
    diags = validate_assertion(parse(checks=body["checks"], context_vars=["canary"]))
    assert "A-21" not in codes(diags)


def test_a22_rejects_a_settle_window_shorter_than_the_bar_it_measures():
    from engine.assertions import validate_assertion  # noqa: PLC0415

    spec = parse(kind="outcome", settle={
        "expect_by_seconds": 300, "ingest_floor_seconds": 120,
        "max_wait_seconds": 200,
    })
    diags = validate_assertion(spec)
    assert "A-22" in codes(diags)
    assert any("false red" in d.detail for d in diags if d.code == "A-22")


def test_a24_rejects_probe_params_that_probe_cannot_use():
    from engine.assertions import validate_assertion  # noqa: PLC0415

    body = base_spec()
    body["checks"][0]["params"].pop("distinct_key")
    diags = validate_assertion(parse(**{"checks": body["checks"]}))
    assert "A-24" in codes(diags)


def test_a03_rejects_a_duplicate_check_id():
    from engine.assertions import validate_assertion  # noqa: PLC0415

    body = base_spec()
    body["checks"].append(dict(body["checks"][0]))
    diags = validate_assertion(parse(**{"checks": body["checks"]}))
    assert "A-03" in codes(diags)


# ---------------------------------------------------------------------------
# A-15 — a sharpened bar may be stricter than the index's, never looser
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw,op,value,unit,contradicts", [
    ("<60 second", "<", 90, "s", True),      # slower than the index allows
    ("<60 second", "<", 30, "s", False),     # stricter — fine
    ("within 15 min", "<=", 1800, "s", True),
    ("within 15 min", "<=", 600, "s", False),
    (">95%", ">=", 90, "%", True),
    (">95%", ">=", 99, "%", False),
    ("Qualitative pass", ">=", 3, "", False),  # nothing to compare against
    ("", ">=", 3, "", False),
])
def test_threshold_contradiction_detection(raw, op, value, unit, contradicts):
    from engine.assertions import ThresholdSpec, _threshold_contradiction  # noqa: PLC0415

    t = ThresholdSpec(kpi="k", op=op, value=value, unit=unit,
                      source="authored_sharpening", rationale="r")
    assert bool(_threshold_contradiction(raw, t)) is contradicts


# ---------------------------------------------------------------------------
# Rendering + the injection guard
# ---------------------------------------------------------------------------


def test_render_substitutes_declared_placeholders():
    from engine.assertions import render_query  # noqa: PLC0415

    out = render_query('filter u = "{{nonce}}"', {"nonce": "a3f19c2b"})
    assert out == 'filter u = "a3f19c2b"'


def test_render_refuses_a_value_that_could_reshape_the_query():
    from engine.assertions import AssertionRenderError, render_query  # noqa: PLC0415

    with pytest.raises(AssertionRenderError, match="not.*safe to substitute"):
        render_query('filter u = "{{nonce}}"', {"nonce": 'x" or 1=1 | limit 9999 //'})


def test_render_refuses_a_missing_context_value():
    from engine.assertions import AssertionRenderError, render_query  # noqa: PLC0415

    with pytest.raises(AssertionRenderError, match="not supplied"):
        render_query('filter u = "{{nonce}}"', {})


# ---------------------------------------------------------------------------
# Verdicts — the honest matrix
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_tenant_is_pending_never_pass():
    """A DC with nothing wired must not see green."""
    from engine.assertions import PENDING, run_assertion  # noqa: PLC0415

    out = await run_assertion(parse(), runner=None, context={"nonce": "abc"},
                              scoreable=True)
    assert out.verdict == PENDING
    assert out.reason == "no_tenant_integration"
    assert all(c.verdict.verdict == PENDING for c in out.checks)


@pytest.mark.asyncio
async def test_dry_run_is_pending_and_still_renders_the_exact_question():
    from engine.assertions import PENDING, run_assertion  # noqa: PLC0415

    out = await run_assertion(parse(), runner=None, context={"nonce": "abc"},
                              dry_run=True, scoreable=True)
    assert out.verdict == PENDING
    assert out.reason == "dry_run"
    assert 'contains "abc"' in out.checks[0].rendered["query"]


@pytest.mark.asyncio
async def test_a_real_measurement_that_clears_the_bar_passes():
    from engine.assertions import PASS, run_assertion  # noqa: PLC0415

    runner = rows_runner([{"_dataset": "a"}, {"_dataset": "b"}, {"_dataset": "c"}])
    out = await run_assertion(parse(), runner=runner, context={"nonce": "abc"},
                              scoreable=True)
    assert out.verdict == PASS
    assert out.checks[0].outcome.measured_value == 3.0


@pytest.mark.asyncio
async def test_a_real_measurement_that_misses_the_bar_fails():
    from engine.assertions import FAIL, run_assertion  # noqa: PLC0415

    runner = rows_runner([{"_dataset": "a"}, {"_dataset": "b"}])
    out = await run_assertion(parse(), runner=runner, context={"nonce": "abc"},
                              scoreable=True)
    assert out.verdict == FAIL
    assert out.checks[0].outcome.measured_value == 2.0


@pytest.mark.asyncio
async def test_a_tenant_error_is_pending_not_a_capability_failure():
    """Conflating a tenant outage with a capability failure produces false red
    in a customer readout."""
    from engine.assertions import PENDING, run_assertion  # noqa: PLC0415

    out = await run_assertion(parse(), runner=raising_runner(RuntimeError("503")),
                              context={"nonce": "abc"}, scoreable=True)
    assert out.verdict == PENDING
    assert out.checks[0].outcome.code == "TENANT_UNREACHABLE"


@pytest.mark.asyncio
async def test_a_quota_error_is_pending_not_a_threshold_miss():
    from engine.assertions import PENDING, run_assertion  # noqa: PLC0415
    from integrations.xsiam.exceptions import XsiamQuotaError  # noqa: PLC0415

    out = await run_assertion(parse(), runner=raising_runner(XsiamQuotaError("429")),
                              context={"nonce": "abc"}, scoreable=True)
    assert out.verdict == PENDING
    assert out.checks[0].outcome.code == "PROBE_QUOTA_EXHAUSTED"


@pytest.mark.asyncio
async def test_a_missing_entitlement_is_not_applicable_not_fail(monkeypatch):
    """A SKU the customer never bought is a commercial gap, not a red mark."""
    from engine import assertions as ae  # noqa: PLC0415

    monkeypatch.setattr(ae, "entitlements", lambda _s: ([], ["Agentic Endpoint Security (AES)"]))
    out = await ae.run_assertion(parse(), runner=rows_runner([]),
                                 context={"nonce": "abc"}, entitled_addons=[],
                                 scoreable=True)
    assert out.verdict == ae.NOT_APPLICABLE
    assert out.reason == "not_entitled"


@pytest.mark.asyncio
async def test_declaring_no_entitlements_is_treated_as_entitled():
    """Silence must never buy a benign verdict — that would be a global mute."""
    from engine.assertions import FAIL, run_assertion  # noqa: PLC0415

    out = await run_assertion(parse(), runner=rows_runner([]),
                              context={"nonce": "abc"}, entitled_addons=None,
                              scoreable=True)
    assert out.verdict == FAIL


@pytest.mark.asyncio
async def test_an_unscoreable_index_test_case_clamps_pass_to_not_applicable(monkeypatch):
    from engine import assertions as ae  # noqa: PLC0415

    monkeypatch.setattr(ae, "tc_scoreable", lambda _s: False)
    runner = rows_runner([{"_dataset": "a"}, {"_dataset": "b"}, {"_dataset": "c"}])
    out = await ae.run_assertion(parse(), runner=runner, context={"nonce": "abc"})
    assert out.verdict == ae.NOT_APPLICABLE
    assert "evidence, not a scored pass" in out.score.detail


@pytest.mark.asyncio
async def test_an_unscoreable_index_test_case_can_still_FAIL(monkeypatch):
    """You cannot machine-certify a qualitative claim passed. You can disprove
    it — and that is the entire value of binding these test cases."""
    from engine import assertions as ae  # noqa: PLC0415

    monkeypatch.setattr(ae, "tc_scoreable", lambda _s: False)
    out = await ae.run_assertion(parse(), runner=rows_runner([]),
                                 context={"nonce": "abc"})
    assert out.verdict == ae.FAIL


# ---------------------------------------------------------------------------
# Probes — every declared FAILURE_CONDITION must actually produce fail
# ---------------------------------------------------------------------------


def test_every_registered_probe_declares_failure_conditions():
    from engine.assertions import PROBE_REGISTRY  # noqa: PLC0415

    for name, cls in PROBE_REGISTRY.items():
        assert cls.FAILURE_CONDITIONS, f"probe {name} declares no failure conditions"
        assert cls.READ_ONLY, f"probe {name} is not read-only"


def test_registering_a_probe_that_cannot_fail_raises():
    from engine.assertions import Probe, register_probe  # noqa: PLC0415

    class Useless(Probe):
        NAME = "useless"
        FAILURE_CONDITIONS = ()

        async def execute(self, ctx):  # pragma: no cover - never registered
            raise NotImplementedError

    with pytest.raises(ValueError, match="cannot produce a failing measurement"):
        register_probe(Useless)


@pytest.mark.asyncio
async def test_xql_rows_probe_measures_row_count():
    from engine.assertions import ProbeContext, XqlRowsProbe  # noqa: PLC0415

    ctx = ProbeContext(runner=rows_runner([{"a": 1}, {"a": 2}]),
                       params={}, rendered={"query": "q"}, context={})
    out = await XqlRowsProbe().execute(ctx)
    assert out.measured_value == 2.0


@pytest.mark.asyncio
async def test_xql_scalar_probe_reports_a_value_not_a_filtered_row_count():
    """A query ending `| filter sla <= 300` returns zero rows for a tenant that
    took 412s — indistinguishable from a tenant that never responded, and the
    customer never learns the number."""
    from engine.assertions import ProbeContext, XqlScalarProbe  # noqa: PLC0415

    ctx = ProbeContext(
        runner=rows_runner([{"sla_seconds": 412}]),
        params={"field": "sla_seconds", "aggregate": "first", "domain": "seconds"},
        rendered={"query": "q"}, context={})
    out = await XqlScalarProbe().execute(ctx)
    assert out.measured_value == 412.0


@pytest.mark.asyncio
async def test_xql_ratio_probe_refuses_to_call_zero_of_zero_a_hundred_percent():
    from engine.assertions import ProbeContext, XqlRatioProbe  # noqa: PLC0415

    ctx = ProbeContext(
        runner=rows_runner([], []),
        params={"min_population": 10},
        rendered={"population_query": "p", "numerator_query": "n"}, context={})
    out = await XqlRatioProbe().execute(ctx)
    assert out.code == "POPULATION_EMPTY"
    assert out.measured_value is None


@pytest.mark.asyncio
async def test_xql_ratio_probe_measures_a_percentage():
    from engine.assertions import MEASURED, ProbeContext, XqlRatioProbe  # noqa: PLC0415

    ctx = ProbeContext(
        runner=rows_runner([{"i": n} for n in range(10)], [{"i": n} for n in range(9)]),
        params={"min_population": 5},
        rendered={"population_query": "p", "numerator_query": "n"}, context={})
    out = await XqlRatioProbe().execute(ctx)
    assert out.code == MEASURED
    assert out.measured_value == pytest.approx(90.0)


@pytest.mark.asyncio
async def test_xql_latency_without_a_precursor_is_pending_not_fail():
    """You may not fail an automation for not responding to an alert that was
    never raised. That is a DETECTION failure."""
    from engine.assertions import ProbeContext, XqlLatencyProbe  # noqa: PLC0415

    ctx = ProbeContext(
        runner=rows_runner([], []),
        params={"precursor_ts_field": "alert_ts", "outcome_ts_field": "contained_ts"},
        rendered={"precursor_query": "p", "outcome_query": "o"}, context={})
    out = await XqlLatencyProbe().execute(ctx)
    assert out.code == "PRECURSOR_MISSING"


@pytest.mark.asyncio
async def test_xql_latency_with_a_precursor_and_no_outcome_is_a_real_fail():
    from engine.assertions import FAIL, ProbeContext, XqlLatencyProbe, verdict_for_code  # noqa: PLC0415

    ctx = ProbeContext(
        runner=rows_runner([{"alert_ts": 1_700_000_000}], []),
        params={"precursor_ts_field": "alert_ts", "outcome_ts_field": "contained_ts"},
        rendered={"precursor_query": "p", "outcome_query": "o"}, context={})
    out = await XqlLatencyProbe().execute(ctx)
    assert out.code == "OUTCOME_ABSENT"
    assert verdict_for_code(out.code) == FAIL


@pytest.mark.asyncio
async def test_xql_latency_measures_the_platforms_own_clock():
    from engine.assertions import MEASURED, ProbeContext, XqlLatencyProbe  # noqa: PLC0415

    ctx = ProbeContext(
        runner=rows_runner([{"alert_ts": 1_700_000_000}],
                           [{"contained_ts": 1_700_000_412}]),
        params={"precursor_ts_field": "alert_ts", "outcome_ts_field": "contained_ts"},
        rendered={"precursor_query": "p", "outcome_query": "o"}, context={})
    out = await XqlLatencyProbe().execute(ctx)
    assert out.code == MEASURED
    assert out.measured_value == pytest.approx(412.0)


@pytest.mark.asyncio
async def test_xql_latency_will_not_score_an_outcome_that_predates_its_trigger():
    """A stale containment record inside the same lookback window is not an
    instantaneous response. Clamping the negative elapsed to zero would give the
    one shape that proves nothing the best possible score."""
    from engine.assertions import ProbeContext, XqlLatencyProbe  # noqa: PLC0415

    ctx = ProbeContext(
        runner=rows_runner([{"alert_ts": 1_700_000_000}],
                           [{"contained_ts": 1_699_999_000}]),
        params={"precursor_ts_field": "alert_ts", "outcome_ts_field": "contained_ts"},
        rendered={"precursor_query": "p", "outcome_query": "o"}, context={})
    out = await XqlLatencyProbe().execute(ctx)
    assert out.code == "OUTCOME_ABSENT"
    assert out.measured_value is None


@pytest.mark.asyncio
async def test_xql_latency_ignores_a_stale_outcome_and_measures_the_real_one():
    from engine.assertions import MEASURED, ProbeContext, XqlLatencyProbe  # noqa: PLC0415

    ctx = ProbeContext(
        runner=rows_runner([{"alert_ts": 1_700_000_000}],
                           [{"contained_ts": 1_699_999_000},
                            {"contained_ts": 1_700_000_120}]),
        params={"precursor_ts_field": "alert_ts", "outcome_ts_field": "contained_ts"},
        rendered={"precursor_query": "p", "outcome_query": "o"}, context={})
    out = await XqlLatencyProbe().execute(ctx)
    assert out.code == MEASURED
    assert out.measured_value == pytest.approx(120.0)


@pytest.mark.parametrize("raw,expected", [
    (1_700_000_000, 1_700_000_000.0),
    (1_700_000_000_000, 1_700_000_000.0),        # epoch millis
    ("2023-11-14T22:13:20+00:00", 1_700_000_000.0),
    ("2023-11-14T22:13:20Z", 1_700_000_000.0),
    ("not a time", None),
    (None, None),
])
def test_timestamp_coercion_accepts_what_tenants_actually_emit(raw, expected):
    from engine.assertions import _as_epoch_seconds  # noqa: PLC0415

    assert _as_epoch_seconds(raw) == expected


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


def test_catalog_is_empty_and_harmless_when_there_is_no_assertions_dir(tmp_path):
    from engine.assertions import AssertionCatalog  # noqa: PLC0415

    cat = AssertionCatalog()
    assert cat.load(str(tmp_path / "nope")) == 0
    assert cat.all() == []
    assert cat.rejected == []


def test_catalog_loads_a_valid_file_and_rejects_an_unfalsifiable_one(tmp_path):
    import yaml  # noqa: PLC0415
    from engine.assertions import AssertionCatalog  # noqa: PLC0415

    good = base_spec()
    bad = base_spec(assertion_id="PLT-IR-002")
    bad["checks"][0]["threshold"].update({"op": ">=", "value": 0})
    bad["checks"][0]["negative_control"]["measured_value"] = 0

    (tmp_path / "good.yml").write_text(yaml.safe_dump(good), encoding="utf-8")
    (tmp_path / "bad.yml").write_text(yaml.safe_dump(bad), encoding="utf-8")

    cat = AssertionCatalog()
    # strict=False proves the guard is NOT relaxable by the strict-refs switch.
    assert cat.load(str(tmp_path), strict=False) == 1
    assert cat.get("PLT-IR-001") is not None
    assert cat.get("PLT-IR-002") is None
    assert any(d["code"] == "A-17"
               for r in cat.rejected for d in r["diagnostics"])


def test_catalog_rejects_a_duplicate_assertion_id(tmp_path):
    import yaml  # noqa: PLC0415
    from engine.assertions import AssertionCatalog  # noqa: PLC0415

    (tmp_path / "a.yml").write_text(yaml.safe_dump(base_spec()), encoding="utf-8")
    (tmp_path / "b.yml").write_text(yaml.safe_dump(base_spec()), encoding="utf-8")

    cat = AssertionCatalog()
    assert cat.load(str(tmp_path), strict=False) == 1
    assert any(d["code"] == "A-02" for r in cat.rejected for d in r["diagnostics"])


def test_catalog_skips_underscore_prefixed_files(tmp_path):
    import yaml  # noqa: PLC0415
    from engine.assertions import AssertionCatalog  # noqa: PLC0415

    (tmp_path / "_schema.yml").write_text(yaml.safe_dump({"nope": 1}), encoding="utf-8")
    cat = AssertionCatalog()
    assert cat.load(str(tmp_path), strict=False) == 0
    assert cat.rejected == []


# ---------------------------------------------------------------------------
# The authoring contract must not drift from the code
# ---------------------------------------------------------------------------


def test_the_worked_example_in_the_authoring_doc_actually_loads(repo_root):
    """The content agents have the doc and this module and nothing else. If the
    example in docs/uc_tc_mapping/assertions.md stops validating, every artifact
    copied from it is dead on arrival."""
    import re  # noqa: PLC0415

    import yaml  # noqa: PLC0415

    from engine.assertions import AssertionSpec, validate_assertion  # noqa: PLC0415
    from engine.uctc_registry import default_index_dir, registry  # noqa: PLC0415

    registry.load(default_index_dir(str(repo_root)))

    doc = (repo_root / "docs" / "uc_tc_mapping" / "assertions.md").read_text(encoding="utf-8")
    block = re.search(r"```yaml\n(.*?)\n```", doc, re.S)
    assert block, "the authoring doc no longer carries a worked YAML example"

    spec = AssertionSpec(**yaml.safe_load(block.group(1)))
    diags = [d for d in validate_assertion(spec, "assertions.md") if d.severity == "error"]
    assert diags == [], [d.to_dict() for d in diags]
    assert spec.assertion_id == "PLT-IR-008"


def test_the_docs_diagnostic_table_lists_every_code_the_loader_can_emit(repo_root):
    """A code the loader emits but the doc never names is a rejection an author
    cannot act on."""
    import re  # noqa: PLC0415

    from engine.assertions import _STRICT_GATED, _STRUCTURAL  # noqa: PLC0415

    doc = (repo_root / "docs" / "uc_tc_mapping" / "assertions.md").read_text(encoding="utf-8")
    documented = set(re.findall(r"`(A-\d{2})`", doc))
    emitted = set(_STRUCTURAL) | set(_STRICT_GATED) | {"A-23"}
    assert emitted - documented == set()

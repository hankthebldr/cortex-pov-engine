"""Behavioural tests for the K8s-native POS assertion pack (``assertions/pos/k8s/``).

This pack is DELIBERATELY separate from ``tests/assertions/test_pos_assertions.py``.
That file's invariants encode an AWS-Terraform fixture model — every artifact must
name an ``iac_module`` that exists under ``infra/modules/aws`` and its stimulus must
say ``terraform apply``. The K8s pack is fixtured differently: the *generated
manifest* is the fixture, planted by ``kubectl apply`` of ``?format=k8s``, so it
cannot and must not satisfy those AWS-only guards. It lives in ``assertions/pos/k8s/``
so the real (recursive ``os.walk``) catalog loads it while that file's
(non-recursive ``os.listdir``) roster does not scan it.

The load-bearing claims here are the same anti-inflation ones the AWS pack makes,
proven against the real evaluator:

* An empty tenant is ``fail``, never green — an admission controller that denied
  nothing, or a posture engine that found nothing, is a real failure.
* One short of the fixture's arity is ``fail`` — the bar is the planted count.
* No tenant / dry run / transport error is ``pending``, never ``pass`` and never a
  benign ``not_applicable``.
* Both are bound to a live POS row in the index and load clean under strict refs.
* POS-KSPM-003 is the only NET-NEW binding; POS-CSPM-004 re-binds an already-authored
  row (breadth, not coverage) and says so in its own scope block.
"""

import os

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
K8S_POS_DIR = os.path.join(REPO_ROOT, "assertions", "pos", "k8s")

KSPM_003 = "POS-KSPM-003"
CSPM_004 = "POS-CSPM-004"
ALL_K8S_POS = (KSPM_003, CSPM_004)

# The index rows each artifact binds — asserted explicitly so a re-key cannot
# silently slide a binding onto a different test case.
EXPECTED_BINDINGS = {
    KSPM_003: ("UCS-KSPM-03", "TC-KSPM-03"),
    CSPM_004: ("UCS-CSPM-02", "TC-CSPM-02"),
}

# TC-KSPM-03 carries no scenario binding and no assertion until this pass — the
# only NET-NEW binding. TC-CSPM-02 is already authored by POS-CSPM-001, so
# POS-CSPM-004 adds breadth and moves the coverage number by ZERO.
NET_NEW_TC = {"TC-KSPM-03"}
ALREADY_AUTHORED_TC = {"TC-CSPM-02"}

CONTEXT = {}


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def index_loaded():
    from engine.uctc_registry import default_index_dir, registry  # noqa: PLC0415

    if not registry.loaded:
        registry.load(default_index_dir(REPO_ROOT))
    return registry


@pytest.fixture(scope="module")
def k8s_specs(index_loaded):
    """Every K8s POS artifact, parsed and fully validated exactly as boot does."""
    from engine.assertions import parse_assertion_file, validate_assertion  # noqa: PLC0415

    specs = {}
    for fname in sorted(os.listdir(K8S_POS_DIR)):
        if not fname.endswith((".yml", ".yaml")) or fname.startswith("_"):
            continue
        path = os.path.join(K8S_POS_DIR, fname)
        spec, error = parse_assertion_file(path)
        assert error is None, f"{fname}: {error}"
        # validate_assertion returns non-fatal diagnostics; A-01/A-17/A-18 etc.
        # would already have surfaced as a parse/validate error or empty spec.
        specs[spec.assertion_id] = (spec, validate_assertion(spec, path))
    return specs


def spec_of(k8s_specs, assertion_id):
    assert assertion_id in k8s_specs, f"{assertion_id} not in {sorted(k8s_specs)}"
    return k8s_specs[assertion_id][0]


def constant_runner(rows):
    async def _run(_query):
        return list(rows)

    return _run


async def evaluate(spec, **kwargs):
    from engine.assertions import run_assertion  # noqa: PLC0415

    kwargs.setdefault("context", dict(CONTEXT))
    return await run_assertion(spec, **kwargs)


# ---------------------------------------------------------------------------
# The pack loads, binds, and is complete
# ---------------------------------------------------------------------------


def test_pack_contains_exactly_the_two_shipped_artifacts(k8s_specs):
    assert sorted(k8s_specs) == sorted(ALL_K8S_POS)


def test_the_real_catalog_loads_both_and_rejects_neither():
    """The recursive os.walk catalog (boot path) must load both and put neither
    in rejected[] — a guard nobody can see is not a guard."""
    from engine.assertions import AssertionCatalog  # noqa: PLC0415
    from engine.uctc_registry import default_index_dir, registry  # noqa: PLC0415

    if not registry.loaded:
        registry.load(default_index_dir(REPO_ROOT))
    cat = AssertionCatalog()
    cat.load(os.path.join(REPO_ROOT, "assertions"), strict=True)
    rejected_sources = " ".join(r["source"] for r in cat.rejected)
    for aid in ALL_K8S_POS:
        assert aid not in rejected_sources, f"{aid} appears in rejected[]"
        assert cat.get(aid) is not None, f"{aid} did not load into the catalog"


def test_bindings_are_the_declared_index_rows(k8s_specs):
    for aid, (uc_ref, tc_ref) in EXPECTED_BINDINGS.items():
        spec = spec_of(k8s_specs, aid)
        assert spec.uc_ref == uc_ref, f"{aid}: uc_ref={spec.uc_ref}"
        assert spec.tc_ref == tc_ref, f"{aid}: tc_ref={spec.tc_ref}"


def test_every_artifact_binds_a_live_pos_row(k8s_specs):
    """A-14 inverted: an assertion on a DET row would claim credit for detection
    content nobody authored. Proven against the registry, not the YAML."""
    from engine.uctc_registry import registry  # noqa: PLC0415

    if not registry.loaded:
        pytest.skip("UC/TC index snapshot absent — FK validation degraded to advisory")
    for aid in ALL_K8S_POS:
        spec = spec_of(k8s_specs, aid)
        tc = registry.tc(spec.tc_ref)
        assert tc is not None, f"{aid}: {spec.tc_ref} is not in the index"
        assert tc.validation_class == "POS", (
            f"{aid} binds {tc.tc_id}, which the index classes {tc.validation_class!r}"
        )


def test_kspm_03_is_the_only_net_new_binding(k8s_specs):
    """The honest coverage split: exactly one of the two is a net-new TC binding;
    the other re-binds a row POS-CSPM-001 already authored and must say so."""
    bound = {spec_of(k8s_specs, aid).tc_ref for aid in ALL_K8S_POS}
    assert NET_NEW_TC <= bound
    assert ALREADY_AUTHORED_TC <= bound
    breadth = spec_of(k8s_specs, CSPM_004)
    low = breadth.scope_limitations.lower()
    assert "breadth" in low and ("coverage" in low), (
        "POS-CSPM-004 must state in its own scope block that it is breadth, not coverage"
    )


# ---------------------------------------------------------------------------
# This pack does NOT use the AWS-Terraform fixture model
# ---------------------------------------------------------------------------


def test_fixture_is_generated_manifests_not_a_terraform_module(k8s_specs):
    """The differentiator from the AWS POS pack: the fixture is a generated K8s
    manifest applied with kubectl, so there is no iac_module and no 'terraform
    apply'. Asserted so nobody 'fixes' these by pointing them at a TF module."""
    for aid in ALL_K8S_POS:
        spec = spec_of(k8s_specs, aid)
        assert spec.stimulus.kind == "iac_fixture", f"{aid}: {spec.stimulus.kind}"
        assert not spec.stimulus.iac_module, (
            f"{aid} names an iac_module {spec.stimulus.iac_module!r}; the K8s pack is "
            f"fixtured by a generated manifest, not a Terraform module"
        )
        detail = spec.stimulus.detail or ""
        assert "kubectl apply" in detail and "format=k8s" in detail, (
            f"{aid}: stimulus must name the kubectl-apply / format=k8s delivery"
        )
        assert "terraform apply" not in detail


def test_join_key_is_ours_never_the_products_vocabulary(k8s_specs):
    """The queries key on cortexsim.io/… (KSPM-03: our namespace prefix; CSPM-04:
    our posture-finding label), never on a guessed Cortex/webhook classification
    name that returns zero rows against a correct platform."""
    kspm = spec_of(k8s_specs, KSPM_003)
    kspm_q = kspm.primary_check.params["query"]
    assert 'objectRef.namespace contains "cortexsim-"' in kspm_q
    assert "responseStatus.code = 403" in kspm_q

    cspm = spec_of(k8s_specs, CSPM_004)
    cspm_q = cspm.primary_check.params["query"]
    assert 'cortexsim.io/posture-finding' in cspm_q


def test_kspm_03_asserts_both_directions(k8s_specs):
    """A refuse-is-pass check that only measures refusals rewards a controller
    that blocks everything. chk-02 asserts the compliant control is ADMITTED."""
    spec = spec_of(k8s_specs, KSPM_003)
    assert len(spec.checks) >= 2
    admit = spec.checks[1]
    q = admit.params["query"]
    assert "cortexsim-sim-cdr-001" in q, "the compliant control is SIM-CDR-001"
    assert "in (200, 201)" in q, "chk-02 must key on ADMITTED (200/201), not denied"


# ---------------------------------------------------------------------------
# The anti-inflation tests — the reason this file exists
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_tenant_is_fail_not_pass(k8s_specs):
    """A reachable dataset that carries no denials / no findings is a FAILURE, not
    a benign green. The single most important test in the file."""
    for aid in ALL_K8S_POS:
        spec = spec_of(k8s_specs, aid)
        outcome = await evaluate(spec, runner=constant_runner([]))
        assert outcome.verdict == "fail", (
            f"{aid} returned {outcome.verdict!r} against a tenant that found nothing"
        )
        assert outcome.checks[0].outcome.measured_value == 0


@pytest.mark.asyncio
async def test_partial_discovery_is_fail(k8s_specs):
    """One short of the fixture's arity is red — the bar is the planted count."""
    for aid in ALL_K8S_POS:
        spec = spec_of(k8s_specs, aid)
        check = spec.primary_check
        key = check.params["distinct_key"]
        short = int(check.threshold.value) - 1
        rows = [{key: f"value-{i}"} for i in range(short)]
        outcome = await evaluate(spec, runner=constant_runner(rows))
        assert outcome.verdict == "fail", (
            f"{aid} passed with {short} of {int(check.threshold.value)} discovered"
        )


@pytest.mark.asyncio
async def test_exactly_the_arity_clears_the_primary_check(k8s_specs):
    """Exactly the planted count makes the primary check's kpi_verdict pass — the
    other half of A-17 (it must be satisfiable, not only falsifiable). The
    run-level verdict clamps to not_applicable because both index rows are
    'Qualitative pass', which is the honest POS readout."""
    for aid in ALL_K8S_POS:
        spec = spec_of(k8s_specs, aid)
        check = spec.primary_check
        key = check.params["distinct_key"]
        rows = [{key: f"value-{i}"} for i in range(int(check.threshold.value))]
        outcome = await evaluate(spec, runner=constant_runner(rows))
        primary = next(c for c in outcome.checks if c.check.check_id == check.check_id)
        assert primary.verdict.verdict == "pass", (
            f"{aid}: exactly {int(check.threshold.value)} did not clear the bar"
        )


@pytest.mark.asyncio
async def test_no_tenant_is_pending_never_green(k8s_specs):
    for aid in ALL_K8S_POS:
        spec = spec_of(k8s_specs, aid)
        outcome = await evaluate(spec, runner=None)
        assert outcome.verdict == "pending", f"{aid}: {outcome.verdict!r} without a tenant"


@pytest.mark.asyncio
async def test_dry_run_is_pending(k8s_specs):
    for aid in ALL_K8S_POS:
        spec = spec_of(k8s_specs, aid)
        outcome = await evaluate(spec, dry_run=True)
        assert outcome.verdict == "pending"


@pytest.mark.asyncio
async def test_transport_error_is_pending_never_fail(k8s_specs):
    """A tenant/transport error is never a capability failure — that would be
    false red in a customer readout."""

    def raising_runner():
        async def _run(_query):
            raise ConnectionError("tenant unreachable")

        return _run

    for aid in ALL_K8S_POS:
        spec = spec_of(k8s_specs, aid)
        outcome = await evaluate(spec, runner=raising_runner())
        assert outcome.verdict == "pending", f"{aid}: {outcome.verdict!r} on a transport error"


# ---------------------------------------------------------------------------
# Query hygiene
# ---------------------------------------------------------------------------


def test_queries_are_time_bounded(k8s_specs):
    """A posture query with no time bound reads all of history — yesterday's
    apply proves today's run."""
    for aid in ALL_K8S_POS:
        spec = spec_of(k8s_specs, aid)
        for check in spec.checks:
            q = check.params.get("query", "")
            assert "{{lookback_seconds}}" in q, f"{aid}/{check.check_id}: unbounded query"


def test_no_kpi_threshold_is_embedded_in_a_query(k8s_specs):
    """The KPI comparison is the evaluator's job. Selecting the denial population
    with responseStatus.code = 403 is a population filter, not a KPI threshold on
    the distinct-count the check measures."""
    for aid in ALL_K8S_POS:
        spec = spec_of(k8s_specs, aid)
        for check in spec.checks:
            q = check.params.get("query", "")
            # the measured KPI is a count; it must not be pre-filtered to the bar
            assert f"<= {int(check.threshold.value)}" not in q
            assert f">= {int(check.threshold.value)}" not in q or "denials >= 1" in q


def test_negative_controls_are_declared_and_evaluate_fail(k8s_specs):
    """A-18: the declared negative control's measured_value really evaluates fail,
    proven through the same real path the loader uses at boot."""
    from engine.assertions import prove_negative_control  # noqa: PLC0415

    for aid in ALL_K8S_POS:
        spec = spec_of(k8s_specs, aid)
        for check in spec.checks:
            nc = check.negative_control
            assert nc is not None and nc.description, (
                f"{aid}/{check.check_id}: no negative_control"
            )
            ok, why = prove_negative_control(check)
            assert ok, f"{aid}/{check.check_id}: negative_control does not evaluate fail — {why}"

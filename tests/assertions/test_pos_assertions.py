"""Behavioural tests for the shipped POS assertion pack (``assertions/pos/``).

``tests/engine/test_assertions.py`` proves the substrate is sound. This module
proves the eleven POS *artifacts* are sound, which is the claim the
anti-inflation rule cares about: an artifact can satisfy every schema guard and
still be a claim nobody can disprove.

The load-bearing tests here are negative, and two of them are the reason the
file exists:

* **A POS assertion must go RED against a tenant whose posture engine found
  nothing.** An empty answer from a reachable dataset is a discovery failure, and
  a check that reads it as green proves nothing. Every shipped check is driven
  through the real evaluator on an empty tenant and must come back ``fail``.
* **A POS assertion must never go GREEN without a tenant.** No integration, a
  dry run, an unreachable tenant, a 401, a quota error — all ``pending``
  ("still owed"), never ``pass`` and never ``not_applicable`` ("unscoreable by
  construction"). Collapsing those two would let 92 open POS gaps disappear into
  a bucket that reads as benign.

Plus the anti-drift lock the substrate does not carry: every planted-finding tag
key and tag value a query joins on is read back out of the module's own
``main.tf``, and every threshold is reconciled against the fixture's real arity.
Rename a tag in Terraform, or delete a planted resource, and these fail — which
is the only thing standing between "9 planted findings discovered" and a query
asking about a resource that no longer exists.
"""
from __future__ import annotations

import os
import re

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
POS_DIR = os.path.join(REPO_ROOT, "assertions", "pos")
MODULES = os.path.join(REPO_ROOT, "infra", "modules", "aws")

CSPM_001 = "POS-CSPM-001"
CSPM_002 = "POS-CSPM-002"
CSPM_003 = "POS-CSPM-003"
DSPM_001 = "POS-DSPM-001"
DSPM_002 = "POS-DSPM-002"
AISP_001 = "POS-AISP-001"
AISP_002 = "POS-AISP-002"
AISP_003 = "POS-AISP-003"
AISP_005 = "POS-AISP-005"
ASM_001 = "POS-ASM-001"
ASM_002 = "POS-ASM-002"

ALL_POS = (
    CSPM_001, CSPM_002, CSPM_003, DSPM_001, DSPM_002,
    AISP_001, AISP_002, AISP_003, AISP_005, ASM_001, ASM_002,
)

# The index rows each artifact binds. Asserted explicitly so a re-key of the
# pack cannot silently move a binding onto a different test case.
EXPECTED_BINDINGS = {
    CSPM_001: ("UCS-CSPM-02", "TC-CSPM-02"),
    CSPM_002: ("UCS-CSPM-04", "TC-CSPM-04"),
    CSPM_003: ("UCS-CSPM-01", "TC-CSPM-01"),
    DSPM_001: ("UCS-DSPM-01", "TC-DSPM-01"),
    DSPM_002: ("UCS-DSPM-03", "TC-DSPM-03"),
    AISP_001: ("UCS-AISP-01", "TC-AISP-01"),
    AISP_002: ("UCS-AISP-02", "TC-AISP-02"),
    AISP_003: ("UCS-AISP-03", "TC-AISP-03"),
    AISP_005: ("UCS-AISP-05", "TC-AISP-05"),
    ASM_001: ("UCS-ASM-03", "TC-ASM-05"),
    ASM_002: ("UCS-ASM-04", "TC-ASM-06"),
}

# TC-CSPM-01 is the ONLY first-slice POS row the index gives a measurable
# threshold ("within 15 min"). Every other row is "Qualitative pass", so a
# machine PASS is clamped to not_applicable — "evidence, not a scored pass".
# This split is the honest coverage story and it is asserted, not narrated.
SCOREABLE = {CSPM_003}

# Context every artifact needs to render. Values are shaped like the real
# `terraform output` they come from.
CONTEXT = {
    "planted_resource_id": "cortexsim-cspm-public-3f9a1c",
    "asm_target_ip": "203.0.113.44",
    "asm_website_bucket": "cortexsim-asm-website-3f9a1c",
}

T0 = 1_800_000_000


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


def sequence_runner(*batches):
    """A tenant that answers each successive query with the next batch."""
    calls = list(batches)

    async def _run(_query):
        return calls.pop(0) if calls else []

    return _run


def raising_runner(exc):
    async def _run(_query):
        raise exc

    return _run


def satisfying_rows(spec):
    """Rows a healthy tenant would return for this artifact's primary check —
    exactly enough distinct values to clear the authored bar."""
    check = spec.primary_check
    if check.probe == "xql_latency":
        # precursor then outcome, five minutes apart: well inside the 900 s bar.
        return None
    key = check.params["distinct_key"]
    return [{key: f"value-{i}"} for i in range(int(check.threshold.value))]


def tf_source(module):
    with open(os.path.join(MODULES, module, "main.tf"), encoding="utf-8") as fh:
        return fh.read()


def planted_tag_values(module, tag_key):
    """Every ``CortexSim*Finding = "<slug>"`` value the module's Terraform sets."""
    return set(re.findall(rf'{tag_key}\s*=\s*"([a-z0-9-]+)"', tf_source(module)))


def tag_filter_values(query):
    """Tag values a query joins on — only ``filter tags contains "..."`` stages.

    Scoped deliberately: ``filter resource_type contains "sagemaker"`` is a
    resource-type predicate, not a planted-tag join, and reconciling it against
    Terraform tag values would be comparing two different things.
    """
    return set(re.findall(r'filter\s+tags\s+contains\s+"([A-Za-z0-9-]+)"', query))


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
    """The honest coverage story: one of eleven can produce a scored pass."""
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
        assert "does NOT" in spec.scope_limitations, (
            f"{aid}: scope_limitations never states a thing this does not prove"
        )


def test_every_artifact_names_the_iac_fixture_it_depends_on(pos_specs):
    """A POS assertion without a planted fixture is asking a question about
    somebody else's cloud account."""
    for aid in ALL_POS:
        spec = spec_of(pos_specs, aid)
        assert spec.stimulus.kind == "iac_fixture", f"{aid}: stimulus.kind={spec.stimulus.kind}"
        module = (spec.stimulus.iac_module or "").split("/")[-1]
        assert os.path.isdir(os.path.join(MODULES, module)), (
            f"{aid} depends on infra module {spec.stimulus.iac_module!r}, which does not exist"
        )
        assert spec.stimulus.detail and "terraform apply" in spec.stimulus.detail


# ---------------------------------------------------------------------------
# THE ANTI-INFLATION TESTS
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_tenant_is_fail_not_pass(pos_specs):
    """A reachable posture dataset that carries none of the planted findings is
    a DISCOVERY FAILURE. This is the single most important test in the file: an
    assertion that reads an empty answer as green proves nothing at all."""
    for aid in ALL_POS:
        spec = spec_of(pos_specs, aid)
        if spec.primary_check.probe == "xql_latency":
            continue  # covered by the latency-specific tests below
        outcome = await evaluate(spec, runner=constant_runner([]))
        assert outcome.verdict == "fail", (
            f"{aid} returned {outcome.verdict!r} against a tenant that discovered "
            f"nothing: {outcome.score.detail}"
        )
        assert outcome.checks[0].outcome.measured_value == 0


@pytest.mark.asyncio
async def test_partial_discovery_is_fail(pos_specs):
    """One short of the fixture's arity is red. The bar is the planted count; a
    threshold that forgives a miss converts a reportable gap into a green tick."""
    for aid in ALL_POS:
        spec = spec_of(pos_specs, aid)
        check = spec.primary_check
        if check.probe == "xql_latency":
            continue
        key = check.params["distinct_key"]
        short = int(check.threshold.value) - 1
        rows = [{key: f"value-{i}"} for i in range(short)]
        outcome = await evaluate(spec, runner=constant_runner(rows))
        assert outcome.verdict == "fail", (
            f"{aid} passed with {short} of {int(check.threshold.value)} "
            f"{check.threshold.unit} discovered"
        )


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

    class XsiamAuthError(Exception):
        pass

    class XsiamQuotaError(Exception):
        pass

    class XsiamQueryError(Exception):
        pass

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
    """The two ASM artifacts and the latency artifact need operator-supplied
    context. Forgetting it must not run a half-rendered query."""
    for aid in (ASM_001, ASM_002, CSPM_003):
        spec = spec_of(pos_specs, aid)
        outcome = await evaluate(spec, runner=constant_runner([]), context={})
        assert outcome.verdict == "pending", f"{aid}: {outcome.verdict}"
        assert outcome.reason == "probe_query_failed"


@pytest.mark.asyncio
async def test_injection_shaped_context_value_is_refused(pos_specs):
    """Context lands inside query text, so anything that could reshape the query
    is refused before substitution — pending, never a silent bad query."""
    spec = spec_of(pos_specs, ASM_002)
    outcome = await evaluate(
        spec, runner=constant_runner([]),
        context={"asm_target_ip": '1.1.1.1" or true or "'},
    )
    assert outcome.verdict == "pending"
    assert outcome.reason == "probe_query_failed"


@pytest.mark.asyncio
async def test_satisfied_tenant_reports_evidence_not_a_scored_pass(pos_specs):
    """The qualitative clamp, asserted rather than narrated. Per-check verdicts
    stay a real ``pass`` — something was genuinely measured — while the run-level
    verdict clamps to ``not_applicable`` for the ten rows the index scores
    "Qualitative pass". A coverage number that hides this split overstates what
    was proven."""
    from engine.uctc_registry import registry  # noqa: PLC0415

    if not registry.loaded:
        pytest.skip("UC/TC index snapshot absent")
    for aid in ALL_POS:
        spec = spec_of(pos_specs, aid)
        if spec.primary_check.probe == "xql_latency":
            continue
        outcome = await evaluate(spec, runner=constant_runner(satisfying_rows(spec)))
        assert outcome.checks[0].verdict.verdict == "pass", f"{aid}: check did not pass"
        assert outcome.verdict == "not_applicable", (
            f"{aid} reported {outcome.verdict!r}; the index carries no measurable "
            f"threshold for {spec.tc_ref}, so a machine pass would assert "
            f"something the index never defined"
        )
        assert outcome.reason == "tc_unscoreable"


@pytest.mark.asyncio
async def test_not_entitled_is_not_applicable_and_silence_is_not_benign(pos_specs):
    """A missing add-on is a commercial gap and belongs on the upsell list, not
    on the failure list. But an OMITTED entitlement profile must never buy that
    benign verdict."""
    from engine.assertions import entitlements  # noqa: PLC0415

    spec = spec_of(pos_specs, CSPM_001)
    _base, required = entitlements(spec)
    if not required:
        pytest.skip("index snapshot carries no add-on requirement for TC-CSPM-02")
    outcome = await evaluate(spec, runner=constant_runner([]), entitled_addons=[])
    assert outcome.verdict == "not_applicable"
    assert outcome.reason == "not_entitled"

    # Omitted profile: treated as entitled, so the empty tenant is still red.
    outcome = await evaluate(spec, runner=constant_runner([]))
    assert outcome.verdict == "fail"


# ---------------------------------------------------------------------------
# The latency artifact — the one that can produce a real scored pass
# ---------------------------------------------------------------------------


def _audit_row(ts):
    return {"_time": ts, "event_name": "CreateBucket"}


def _posture_row(ts):
    return {"_time": ts, "resource_id": CONTEXT["planted_resource_id"]}


@pytest.mark.asyncio
async def test_discovery_latency_inside_the_bar_is_a_real_scored_pass(pos_specs):
    spec = spec_of(pos_specs, CSPM_003)
    runner = sequence_runner([_audit_row(T0)], [_posture_row(T0 + 300)])
    outcome = await evaluate(spec, runner=runner)
    assert outcome.checks[0].outcome.measured_value == 300
    assert outcome.verdict == "pass", (
        f"TC-CSPM-01 is the one first-slice POS row with a measurable threshold; "
        f"a cleared bar must not be clamped. Got {outcome.verdict}: {outcome.score.detail}"
    )


@pytest.mark.asyncio
async def test_discovery_latency_over_the_bar_is_fail_and_reports_the_number(pos_specs):
    """1800 s against a 900 s budget is a completely different conversation from
    "nothing happened" — which is why the threshold is not inside the query."""
    spec = spec_of(pos_specs, CSPM_003)
    runner = sequence_runner([_audit_row(T0)], [_posture_row(T0 + 1800)])
    outcome = await evaluate(spec, runner=runner)
    assert outcome.verdict == "fail"
    assert outcome.checks[0].outcome.measured_value == 1800


@pytest.mark.asyncio
async def test_no_provisioning_record_is_pending_not_fail(pos_specs):
    """No CloudTrail event for the resource means the control plane is not
    ingested. That is an INGESTION gap; blaming the posture engine's discovery
    latency for it reports the wrong product red."""
    spec = spec_of(pos_specs, CSPM_003)
    runner = sequence_runner([], [_posture_row(T0 + 300)])
    outcome = await evaluate(spec, runner=runner)
    assert outcome.verdict == "pending"
    assert outcome.reason == "precursor_missing"


@pytest.mark.asyncio
async def test_provisioned_but_never_discovered_is_fail(pos_specs):
    """The precursor is positively proven and the asset never appears. This is
    the capability failing, and it is the only case where absence is red."""
    spec = spec_of(pos_specs, CSPM_003)
    runner = sequence_runner([_audit_row(T0)], [])
    outcome = await evaluate(spec, runner=runner)
    assert outcome.verdict == "fail"
    assert outcome.checks[0].outcome.code == "OUTCOME_ABSENT"


# ---------------------------------------------------------------------------
# The anti-drift lock — the fixture is the ground truth, not the query
# ---------------------------------------------------------------------------


def test_cspm_queries_join_on_tags_terraform_actually_writes(pos_specs):
    """Every ``CortexSimCSPMFinding`` slug a query filters on must exist in the
    cspm module's Terraform. Rename a tag there and this fails, instead of the
    query silently asking about a resource class that no longer exists."""
    planted = planted_tag_values("cspm", "CortexSimCSPMFinding")
    assert len(planted) == 9, f"cspm plants {len(planted)} classes, expected 9: {sorted(planted)}"
    for aid in (CSPM_001, CSPM_002, CSPM_003, DSPM_001, DSPM_002):
        spec = spec_of(pos_specs, aid)
        for _check, _name, query in queries_of(spec):
            for slug in tag_filter_values(query) - {"CortexSimCSPMFinding"}:
                assert slug in planted, (
                    f"{aid} filters on tag value {slug!r}, which infra/modules/aws/"
                    f"cspm/main.tf does not plant"
                )


def test_aispm_queries_join_on_tags_terraform_actually_writes(pos_specs):
    planted = planted_tag_values("ai-spm", "CortexSimAISPMFinding")
    for aid in (AISP_001, AISP_002, AISP_003, AISP_005):
        spec = spec_of(pos_specs, aid)
        for _check, _name, query in queries_of(spec):
            for slug in tag_filter_values(query) - {"CortexSimAISPMFinding"}:
                assert slug in planted, (
                    f"{aid} filters on tag value {slug!r}, which infra/modules/aws/"
                    f"ai-spm/main.tf does not plant"
                )


def test_tag_keys_used_by_queries_exist_in_terraform(pos_specs):
    """The tag KEY is the join column for CSPM and AI-SPM. It is written by our
    Terraform, never by the product, which is why the join survives a change in
    the product's own finding vocabulary."""
    for module, key, artifacts in (
        ("cspm", "CortexSimCSPMFinding", (CSPM_001, CSPM_002, DSPM_001)),
        ("ai-spm", "CortexSimAISPMFinding", (AISP_001, AISP_002)),
    ):
        assert key in tf_source(module)
        for aid in artifacts:
            spec = spec_of(pos_specs, aid)
            assert any(key in q for _c, _n, q in queries_of(spec)), (
                f"{aid} does not join on {key}"
            )


def test_thresholds_equal_the_fixtures_real_arity(pos_specs):
    """Every bar is the planted count read back out of Terraform. A bar chosen
    to be reachable rather than to be true is the failure this pack exists to
    avoid, so it is reconciled here rather than trusted."""
    cspm = tf_source("cspm")
    aispm = tf_source("ai-spm")
    asm = tf_source("asm")

    # 9 tagged CSPM resources across 6 distinct AWS resource types.
    cspm_tagged = re.findall(r'resource "(\w+)" "\w+" \{', cspm)
    assert len(planted_tag_values("cspm", "CortexSimCSPMFinding")) == 9
    assert spec_of(pos_specs, CSPM_001).primary_check.threshold.value == 9
    cspm_types = {
        "aws_s3_bucket", "aws_security_group", "aws_iam_role",
        "aws_iam_user", "aws_ebs_volume", "aws_cloudtrail",
    }
    assert cspm_types <= set(cspm_tagged)
    assert spec_of(pos_specs, CSPM_002).primary_check.threshold.value == len(cspm_types)

    # 3 tagged S3 buckets — the untagged CloudTrail log bucket is not counted.
    s3_tagged = len(re.findall(
        r'resource "aws_s3_bucket" "\w+" \{(?:[^}]|\}[^\n])*?CortexSimCSPMFinding', cspm))
    assert s3_tagged == 3, f"cspm tags {s3_tagged} S3 buckets, expected 3"
    assert spec_of(pos_specs, DSPM_001).primary_check.threshold.value == 3

    # 6 AI-SPM resource-level tags (the two S3 OBJECT tags are deliberately
    # excluded — a resource inventory does not enumerate object tags).
    object_tag_values = set(re.findall(
        r'resource "aws_s3_object" "\w+" \{(?:[^}]|\}[^\n])*?'
        r'CortexSimAISPMFinding\s*=\s*"([a-z0-9-]+)"', aispm))
    resource_level = planted_tag_values("ai-spm", "CortexSimAISPMFinding") - object_tag_values
    optional = {"shadow-ai-on-unmanaged-gpu"}  # enable_shadow_gpu defaults false
    assert len(resource_level - optional) == 6, sorted(resource_level)
    assert spec_of(pos_specs, AISP_001).primary_check.threshold.value == 6

    # 3 SageMaker-typed resources.
    sagemaker = len(re.findall(r'resource "aws_sagemaker_\w+" "\w+"', aispm))
    assert sagemaker == 3, f"ai-spm declares {sagemaker} sagemaker resources"
    assert spec_of(pos_specs, AISP_002).primary_check.threshold.value == 3

    # 6 world-open ingress ports on the asm host (port 22 is closed).
    ports = {int(p) for p in re.findall(r"from_port\s*=\s*(\d+)", asm)} - {0}
    assert ports == {80, 443, 2222, 6379, 9001, 9200}, sorted(ports)
    assert spec_of(pos_specs, ASM_002).primary_check.threshold.value == len(ports)
    for port in ports:
        assert str(port) in queries_of(spec_of(pos_specs, ASM_002))[0][2]


def test_object_level_tags_are_never_a_threshold(pos_specs):
    """``insecure-model-serialization-pickle`` and ``training-data-pii-content``
    are S3 OBJECT tags. A posture inventory enumerates resources, so a threshold
    counting them would go red against a correct platform. TC-AISP-04 was dropped
    for exactly this reason and no query may reach for them."""
    forbidden = {"insecure-model-serialization-pickle", "training-data-pii-content"}
    for aid in ALL_POS:
        for _c, _n, query in queries_of(spec_of(pos_specs, aid)):
            for slug in forbidden:
                assert slug not in query, f"{aid} joins on object-level tag {slug!r}"


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


def test_asm_lookback_is_a_full_discovery_day(pos_specs):
    """Regression guard on a deliberate inconsistency. The ASM artifacts hardcode
    an 86400 s window instead of the 3600 s ``lookback_seconds`` builtin the
    other nine use, because external discovery runs daily: a one-hour window
    against a once-a-day scanner reports zero and paints a correct platform red.
    'Fixing' this for consistency would manufacture that red."""
    for aid in (ASM_001, ASM_002):
        for _c, _n, query in queries_of(spec_of(pos_specs, aid)):
            assert "current_time() - 86400" in query
            assert "lookback_seconds" not in query


def test_queries_are_time_bounded(pos_specs):
    """An unbounded posture query can prove today's claim with last month's
    findings — including findings from a lab that has since been destroyed."""
    for aid in ALL_POS:
        for _c, _n, query in queries_of(spec_of(pos_specs, aid)):
            assert "to_timestamp(current_time() -" in query, (
                f"{aid}: query is not time-bounded:\n{query}"
            )

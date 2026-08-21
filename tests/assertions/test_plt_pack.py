"""Behavioural tests for the shipped PLT assertion pack (``assertions/plt/``).

The pack's headline mechanism is an **ingestion round-trip**: emit a
uniquely-marked record through a real EAL emitter, then query the tenant to
prove it was ingested AND normalized. So these tests do not hand the probes
hand-written fixture rows. They:

1. run the REAL emitters through the REAL analytics-emitter driver and capture
   the REAL bytes each POST carried;
2. load those exact records into an in-memory lake;
3. run the artifact's OWN authored XQL against that lake and score it through
   the REAL ``evaluate_check`` / ``score_run``.

That is what makes the failure taxonomy testable rather than asserted. Three
tenant states that a DC must be able to tell apart are exercised end to end:

    raw hit + native hit   -> ingested AND normalized
    raw hit + native MISS  -> ingested, NOT normalized   (parser / modeling gap)
    raw MISS + native MISS -> never landed               (collector gap)

and three that must never be green: no tenant, a dry run, and a tenant that
errors.

``_Lake`` is a deliberately small XQL *subset* interpreter — enough for the
three query shapes this pack authors, and nothing more. It is a test double for
a tenant, not an XQL engine; the honest claim it supports is "the authored
queries select the records the emitters actually produced", which is exactly
the claim a round-trip needs and cannot get from fixture rows written by hand.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any, Optional

import pytest

PLT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "assertions", "plt",
)

IR_008 = "PLT-IR-008"
ERV_001 = "PLT-ERV-001"
ITDR_006 = "PLT-ITDR-006"
XDL_001 = "PLT-XDL-001"
ALL_PLT = (IR_008, ERV_001, ITDR_006, XDL_001)

TOKEN = "csim-9f2a41c0d3e7"
COLLECTOR = "https://collector.cortexsim-canary.invalid/logs/v1/event"


# ---------------------------------------------------------------------------
# A tenant made out of the bytes the emitters actually sent
# ---------------------------------------------------------------------------


class _Recorder:
    def __init__(self) -> None:
        self.bodies: list[bytes] = []

    async def post(self, url: str, *, headers=None, content=None):
        self.bodies.append(
            content if isinstance(content, (bytes, bytearray)) else str(content).encode()
        )

        class _R:
            status_code = 202

        return _R()

    async def aclose(self) -> None:
        return None


def emit(plugin_name: str, **params: Any) -> list[dict[str, Any]]:
    """Run one real campaign step; return the records the collector received."""
    from eal_simulator import AuditLogger, Campaign, CampaignExecutor  # noqa: PLC0415
    from eal_simulator import analytics_emitter as spine  # noqa: PLC0415

    rec = _Recorder()
    original = spine.AnalyticsLogEmitter._build_client
    spine.AnalyticsLogEmitter._build_client = lambda self, p: rec  # type: ignore[assignment]
    try:
        campaign = Campaign.model_validate({
            "campaign_id": "CMP-PLT-001",
            "name": "plt round-trip",
            "dry_run": False,
            "simulation_authorized": True,
            "authorized_by": "tester",
            "target_allowlist": ["collector.cortexsim-canary.invalid"],
            "steps": [{
                "step_id": "step-01",
                "plugin": plugin_name,
                "params": {
                    "collector_url": COLLECTOR, "sleep_seconds": 0.0, **params,
                },
            }],
        })
        state = asyncio.run(CampaignExecutor(audit=AuditLogger()).execute(campaign))
        assert state.step_results[0].status in ("success", "partial")
    finally:
        spine.AnalyticsLogEmitter._build_client = original  # type: ignore[assignment]
    return [json.loads(b.decode()) for b in rec.bodies]


_DATASETS_RE = re.compile(r"dataset\s+in\s*\(([^)]*)\)", re.I)
_FILTER_RE = re.compile(r"^\s*\|\s*filter\s+(.*)$", re.I)
_COMP_BY_RE = re.compile(r"\|\s*comp\s+count\(\)\s+as\s+(\w+)\s+by\s+(\w+)", re.I)
_LIMIT_RE = re.compile(r"\|\s*limit\s+(\d+)", re.I)
_TERM_RE = re.compile(r'([A-Za-z_][\w.]*)\s+contains\s+"([^"]*)"')


class _Lake:
    """dataset -> records, queried by the subset of XQL this pack authors.

    Field resolution deliberately models what a NORMALIZING parser does: lift a
    leaf out of whatever nesting the vendor wrapped it in. ``_raw_log`` is the
    whole record body, which is what makes 'the bytes landed but the parser did
    not map them' representable at all.
    """

    def __init__(self, contents: Optional[dict[str, list[dict]]] = None) -> None:
        self.contents: dict[str, list[dict]] = {}
        self.calls: list[str] = []
        for ds, rows in (contents or {}).items():
            self.add(ds, rows)

    def add(self, dataset: str, records: list[dict]) -> "_Lake":
        self.contents.setdefault(dataset, []).extend(records)
        return self

    # -- field access ---------------------------------------------------

    @staticmethod
    def _leaves(node: Any, name: str, out: list[str]) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if k == name and isinstance(v, (str, int, float)):
                    out.append(str(v))
                _Lake._leaves(v, name, out)
        elif isinstance(node, list):
            for item in node:
                _Lake._leaves(item, name, out)

    def _values(self, record: dict, dataset: str, field: str) -> list[str]:
        if field == "_raw_log":
            return [json.dumps(record)]
        if field == "_dataset":
            return [dataset]
        out: list[str] = []
        self._leaves(record, field.split(".")[-1], out)
        return out

    # -- boolean predicate over `FIELD contains "LIT"` -------------------

    def _predicate(self, expr: str, record: dict, dataset: str) -> bool:
        tokens = re.findall(r'\(|\)|\band\b|\bor\b|[A-Za-z_][\w.]*\s+contains\s+"[^"]*"',
                            expr, re.I)
        pos = 0

        def parse_or() -> bool:
            nonlocal pos
            val = parse_and()
            while pos < len(tokens) and tokens[pos].lower() == "or":
                pos += 1
                val = parse_and() or val
            return val

        def parse_and() -> bool:
            nonlocal pos
            val = parse_atom()
            while pos < len(tokens) and tokens[pos].lower() == "and":
                pos += 1
                val = parse_atom() and val
            return val

        def parse_atom() -> bool:
            nonlocal pos
            tok = tokens[pos]
            if tok == "(":
                pos += 1
                val = parse_or()
                pos += 1                       # consume ')'
                return val
            pos += 1
            m = _TERM_RE.match(tok)
            assert m, f"unparsed term: {tok!r}"
            field, literal = m.group(1), m.group(2)
            return any(literal in v for v in self._values(record, dataset, field))

        return parse_or() if tokens else True

    # -- the runner the probes are handed --------------------------------

    async def __call__(self, query: str) -> list[dict[str, Any]]:
        self.calls.append(query)

        m = _DATASETS_RE.search(query)
        assert m, f"query names no dataset set: {query}"
        names = [n.strip() for n in m.group(1).split(",") if n.strip()]

        filters = [
            fm.group(1).strip()
            for line in query.splitlines()
            for fm in [_FILTER_RE.match(line)] if fm
        ]
        # A filter can wrap onto continuation lines; glue anything that is not
        # itself a new pipeline stage onto the filter it belongs to.
        glued: list[str] = []
        current: Optional[str] = None
        for line in query.splitlines():
            fm = _FILTER_RE.match(line)
            if fm:
                if current is not None:
                    glued.append(current)
                current = fm.group(1).strip()
            elif current is not None and not line.lstrip().startswith("|"):
                current += " " + line.strip()
            elif current is not None:
                glued.append(current)
                current = None
        if current is not None:
            glued.append(current)
        filters = glued or filters

        rows: list[dict[str, Any]] = []
        for ds in names:
            for record in self.contents.get(ds, []):
                if all(self._predicate(f, record, ds) for f in filters):
                    rows.append({"_dataset": ds, **record})

        lm = _LIMIT_RE.search(query)
        if lm:
            rows = rows[: int(lm.group(1))]

        cm = _COMP_BY_RE.search(query)
        if cm:
            alias, key = cm.group(1), cm.group(2)
            counts: dict[str, int] = {}
            for r in rows:
                counts[str(r.get(key))] = counts.get(str(r.get(key)), 0) + 1
            return [{key: k, alias: v} for k, v in sorted(counts.items())]
        return rows


def strip_native_plant(records: list[dict], plugin_cls) -> list[dict]:
    """A tenant whose parser did NOT map this source's own fields.

    The raw body still carries the canary — the bytes are in the lake — but
    every native field the assertion queries on is back to its pre-canary value.
    This is the state the two-plant design exists to make visible.
    """
    out = []
    for rec in records:
        clone = json.loads(json.dumps(rec))
        for path in plugin_cls.CANARY_FIELDS:
            node: Any = clone
            parts = path.split(".")
            for part in parts[:-1]:
                node = node.get(part) if isinstance(node, dict) else None
                if node is None:
                    break
            if isinstance(node, dict) and parts[-1] in node:
                node[parts[-1]] = "unmapped"
        out.append(clone)
    return out


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def index_snapshot():
    """Load the real UC/TC snapshot for this module.

    Without it ``tc_scoreable`` degrades to True and the qualitative clamp
    silently stops firing — which would make three of these four artifacts look
    like scored passes in the test suite and only in the test suite. The clamp
    is the pack's central honesty property, so it gets exercised for real.
    """
    from engine.uctc_registry import default_index_dir, registry  # noqa: PLC0415
    from config import settings  # noqa: PLC0415

    if not registry.loaded:
        registry.load(default_index_dir(settings.CORTEXSIM_BASE_DIR))
    assert registry.loaded, "no UC/TC index snapshot — the clamp cannot be tested"
    return registry


@pytest.fixture(scope="module")
def plt_specs():
    """Every PLT artifact, parsed and validated exactly as boot does."""
    from engine.assertions import parse_assertion_file, validate_assertion  # noqa: PLC0415

    specs = {}
    for fname in sorted(os.listdir(PLT_DIR)):
        if not fname.endswith((".yml", ".yaml")) or fname.startswith("_"):
            continue
        path = os.path.join(PLT_DIR, fname)
        spec, error = parse_assertion_file(path)
        assert error is None, f"{fname}: {error}"
        specs[spec.assertion_id] = (spec, validate_assertion(spec, path))
    return specs


@pytest.fixture(scope="module")
def stale_records():
    """Last run's records: same emitter, a DIFFERENT canary."""
    return emit("k8s_audit_emitter", event_pattern="pod_exec",
                canary_token="csim-0000deadbeef")


@pytest.fixture(scope="module")
def emitted():
    """The real records each source family put on the wire, canary planted."""
    return {
        "kubernetes_audit_logs": emit(
            "k8s_audit_emitter", event_pattern="pod_exec", canary_token=TOKEN),
        "msft_o365_audit": emit(
            "m365_activity_emitter", event_pattern="mailbox_enumeration_by_app",
            burst_count=6, canary_token=TOKEN),
        "panw_ngfw_traffic_raw": emit(
            "ngfw_eal_emitter", event_pattern="large_upload_https",
            iterations=2, canary_token=TOKEN),
        "cloud_audit_logs": emit(
            "cloud_audit_emitter", provider="aws", event_pattern="secrets_dump",
            canary_token=TOKEN),
        "msft_windows_security": emit(
            "ad_windows_emitter", event_pattern="kerberoast_weak_ticket",
            canary_token=TOKEN),
        "okta_sso": emit(
            "idp_signin_emulator", provider="okta",
            event_pattern="brute_force_lockout", canary_token=TOKEN),
        "msft_azure_ad_signin": emit(
            "idp_signin_emulator", provider="microsoft",
            event_pattern="brute_force_lockout", canary_token=TOKEN),
    }


async def run(spec, runner=None, **kw):
    from engine.assertions import run_assertion  # noqa: PLC0415

    ctx = {"canary": TOKEN, "lookback_seconds": "3600"}
    ctx.update(kw.pop("context", None) or {})
    return await run_assertion(spec, runner=runner, context=ctx, **kw)


def check(outcome, check_id):
    return next(e for e in outcome.checks if e.check.check_id == check_id)


def lake_for(spec_id: str, emitted: dict) -> _Lake:
    """Only the datasets that assertion's stimulus actually emits."""
    wanted = {
        IR_008: ["kubernetes_audit_logs", "msft_o365_audit", "panw_ngfw_traffic_raw"],
        ERV_001: ["kubernetes_audit_logs", "msft_o365_audit", "panw_ngfw_traffic_raw",
                  "cloud_audit_logs", "msft_windows_security", "okta_sso"],
        ITDR_006: ["msft_windows_security", "msft_azure_ad_signin", "okta_sso"],
        XDL_001: ["msft_o365_audit", "panw_ngfw_traffic_raw"],
    }[spec_id]
    return _Lake({ds: emitted[ds] for ds in wanted})


# ---------------------------------------------------------------------------
# Pack-level guards — the artifacts, not the substrate
# ---------------------------------------------------------------------------


def test_pack_is_exactly_the_four_provable_rows(plt_specs):
    """The index carries 43 PLT rows. Four are bound here. The other 39 are an
    honest gap, and inflating this list is the failure mode that matters."""
    assert sorted(plt_specs) == sorted(ALL_PLT)


def test_every_artifact_loads_clean_under_strict_refs(plt_specs):
    from engine.assertions import is_rejection  # noqa: PLC0415

    for aid, (_spec, diags) in plt_specs.items():
        blocking = [d.to_dict() for d in diags if is_rejection(d, strict=True)]
        assert not blocking, f"{aid}: {blocking}"


def test_every_binding_is_a_PLT_index_row(plt_specs):
    """An assertion binding a DET row would claim credit for detection content
    nobody authored. Assert the class agreement directly, not via the loader."""
    from engine.uctc_registry import registry  # noqa: PLC0415

    for aid, (spec, _d) in plt_specs.items():
        for ref in spec.tc_refs:
            tc = registry.tc(ref)
            assert tc is not None, f"{aid}: {ref} is not in the index"
            assert tc.validation_class == "PLT", f"{aid}: {ref} is {tc.validation_class}"


def test_every_check_can_both_fail_and_pass(plt_specs):
    from engine.assertions import prove_falsifiable  # noqa: PLC0415

    for aid, (spec, _d) in plt_specs.items():
        for c in spec.checks:
            proof = prove_falsifiable(c)
            assert proof.can_fail, f"{aid}/{c.check_id} can never fail"
            assert proof.can_pass, f"{aid}/{c.check_id} can never pass"


def test_every_negative_control_really_evaluates_fail(plt_specs):
    from engine.assertions import prove_negative_control  # noqa: PLC0415

    for aid, (spec, _d) in plt_specs.items():
        for c in spec.checks:
            ok, why = prove_negative_control(c)
            assert ok, f"{aid}/{c.check_id}: {why}"


def test_no_threshold_is_embedded_in_a_query(plt_specs):
    """A query ending `| filter n >= 3` returns nothing for a tenant at 2, which
    is indistinguishable from a tenant at 0 — and the customer never learns the
    number. The bar lives in the artifact."""
    banned = re.compile(r"filter\s+[\w.]+\s*(>=|<=|>|<)\s*\d", re.I)
    for aid, (spec, _d) in plt_specs.items():
        for c in spec.checks:
            for name, raw in c.params.items():
                if isinstance(raw, str):
                    assert not banned.search(raw), f"{aid}/{c.check_id}.{name}"


def test_every_artifact_names_something_it_does_not_prove(plt_specs):
    for aid, (spec, _d) in plt_specs.items():
        text = spec.scope_limitations.lower()
        assert "does not" in text or "not prove" in text, aid
        assert len(spec.scope_limitations) > 200, f"{aid}: scope_limitations is a token gesture"


# ---------------------------------------------------------------------------
# Nothing is green without a tenant
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("aid", ALL_PLT)
async def test_no_tenant_is_pending_not_benign(plt_specs, aid):
    spec, _d = plt_specs[aid]
    outcome = await run(spec, runner=None)
    assert outcome.verdict == "pending"
    assert outcome.reason == "no_tenant_integration"
    assert all(e.outcome.code == "NO_TENANT_INTEGRATION" for e in outcome.checks)


@pytest.mark.asyncio
@pytest.mark.parametrize("aid", ALL_PLT)
async def test_dry_run_renders_the_question_without_answering_it(plt_specs, aid, emitted):
    spec, _d = plt_specs[aid]
    lake = lake_for(aid, emitted)
    outcome = await run(spec, runner=lake, dry_run=True)
    assert outcome.verdict == "pending"
    assert not lake.calls, "a dry run touched the tenant"
    for e in outcome.checks:
        for q in e.rendered.values():
            assert TOKEN in q and "{{" not in q


@pytest.mark.asyncio
@pytest.mark.parametrize("aid", ALL_PLT)
async def test_tenant_error_is_pending_never_a_capability_failure(plt_specs, aid):
    spec, _d = plt_specs[aid]

    async def boom(_query: str):
        raise ConnectionError("tenant unreachable")

    outcome = await run(spec, runner=boom)
    assert outcome.verdict == "pending"
    assert all(e.outcome.code == "TENANT_UNREACHABLE" for e in outcome.checks)


@pytest.mark.asyncio
@pytest.mark.parametrize("aid", ALL_PLT)
async def test_a_hostile_context_value_cannot_reshape_a_query(plt_specs, aid):
    spec, _d = plt_specs[aid]
    lake = _Lake()
    outcome = await run(
        spec, runner=lake,
        context={"canary": 'x" or _raw_log contains "'},
    )
    assert outcome.verdict == "pending"
    assert all(e.outcome.code == "PROBE_QUERY_FAILED" for e in outcome.checks)
    assert not lake.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("aid", ALL_PLT)
async def test_missing_addon_is_not_applicable_not_fail(plt_specs, aid, emitted):
    spec, _d = plt_specs[aid]
    outcome = await run(spec, runner=lake_for(aid, emitted),
                        entitled_addons=["Nothing At All"])
    assert outcome.verdict in ("not_applicable", "pass")
    if outcome.verdict == "not_applicable":
        assert all(e.outcome.code == "NOT_ENTITLED" for e in outcome.checks)


# ---------------------------------------------------------------------------
# The round-trip, end to end, on real emitted bytes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ir008_ingested_and_normalized(plt_specs, emitted):
    """Both plants readable in all three datasets -> the claim holds."""
    spec, _d = plt_specs[IR_008]
    outcome = await run(spec, runner=lake_for(IR_008, emitted))

    assert check(outcome, "chk-01").verdict.verdict == "pass"
    assert check(outcome, "chk-01").outcome.measured_value == 3.0
    assert check(outcome, "chk-02").verdict.verdict == "pass"
    assert check(outcome, "chk-02").outcome.measured_value == 3.0
    # TC-IR-08 is a "Qualitative pass" index row, so a machine pass is clamped
    # to evidence. Reporting this as a scored pass would be the inflation the
    # whole substrate exists to prevent.
    assert outcome.verdict == "not_applicable"
    assert "no measurable threshold" in outcome.score.detail


@pytest.mark.asyncio
async def test_ir008_ingested_but_not_normalized_is_red_and_says_which(plt_specs, emitted):
    """The highest-value diagnostic in the mechanism: the bytes are in the lake
    and the parser is missing. Raw stays at 3, normalized drops to 2."""
    from eal_simulator.plugins.ngfw_eal_emitter import NgfwEalEmitter  # noqa: PLC0415

    spec, _d = plt_specs[IR_008]
    lake = _Lake({
        "kubernetes_audit_logs": emitted["kubernetes_audit_logs"],
        "msft_o365_audit": emitted["msft_o365_audit"],
        "panw_ngfw_traffic_raw": strip_native_plant(
            emitted["panw_ngfw_traffic_raw"], NgfwEalEmitter),
    })
    outcome = await run(spec, runner=lake)

    assert check(outcome, "chk-01").outcome.measured_value == 2.0
    assert check(outcome, "chk-01").verdict.verdict == "fail"
    # ...while the raw discriminator still sees all three. That pair IS the
    # readout "the bytes arrived, the modeling rule is missing".
    assert check(outcome, "chk-02").outcome.measured_value == 3.0
    assert check(outcome, "chk-02").verdict.verdict == "pass"
    assert outcome.verdict == "fail"      # fail is never clamped


@pytest.mark.asyncio
async def test_ir008_never_landed_is_red_on_both_checks(plt_specs, emitted):
    """The collector-side failure: nothing reached the lake at all. Both checks
    go red, which is a different remediation from the parser gap above."""
    spec, _d = plt_specs[IR_008]
    lake = _Lake({
        "kubernetes_audit_logs": emitted["kubernetes_audit_logs"],
        "msft_o365_audit": [], "panw_ngfw_traffic_raw": [],
    })
    outcome = await run(spec, runner=lake)

    assert check(outcome, "chk-01").outcome.measured_value == 1.0
    assert check(outcome, "chk-02").outcome.measured_value == 1.0
    assert outcome.verdict == "fail"


@pytest.mark.asyncio
async def test_ir008_yesterdays_records_do_not_prove_todays_run(plt_specs, stale_records):
    """A per-assertion token would let a stale record satisfy a fresh run. The
    canary is minted per evaluation, so a lake full of LAST run's records reads
    as nothing landed."""
    spec, _d = plt_specs[IR_008]
    stale = _Lake({"kubernetes_audit_logs": stale_records})
    outcome = await run(spec, runner=stale)
    assert check(outcome, "chk-01").outcome.measured_value == 0.0
    assert outcome.verdict == "fail"


@pytest.mark.asyncio
async def test_erv001_partial_onboarding_is_red_with_the_number(plt_specs, emitted):
    spec, _d = plt_specs[ERV_001]
    full = await run(spec, runner=lake_for(ERV_001, emitted))
    assert full.checks[0].outcome.measured_value == 6.0
    assert full.verdict == "not_applicable"          # qualitative index row

    partial = _Lake({ds: emitted[ds] for ds in (
        "msft_o365_audit", "panw_ngfw_traffic_raw",
        "msft_windows_security", "okta_sso")})
    outcome = await run(spec, runner=partial)
    assert check(outcome, "chk-01").outcome.measured_value == 4.0
    assert outcome.verdict == "fail"
    assert "4.0" in check(outcome, "chk-01").verdict.detail


@pytest.mark.asyncio
async def test_itdr006_one_principal_resolves_across_three_providers(plt_specs, emitted):
    from eal_simulator.analytics_emitter import canary_bindings  # noqa: PLC0415

    spec, _d = plt_specs[ITDR_006]
    lake = lake_for(ITDR_006, emitted)
    outcome = await run(spec, runner=lake)

    assert check(outcome, "chk-01").outcome.measured_value == 3.0
    assert check(outcome, "chk-01").verdict.verdict == "pass"
    assert check(outcome, "chk-02").verdict.verdict == "pass"

    # The claim only means anything because it is ONE string in three different
    # providers' own principal fields.
    principal = canary_bindings(TOKEN)["principal"]
    for ds in ("msft_windows_security", "msft_azure_ad_signin", "okta_sso"):
        blob = json.dumps(lake.contents[ds])
        assert principal in blob, f"{ds} does not carry the shared principal"


@pytest.mark.asyncio
async def test_itdr006_hybrid_estate_missing_on_prem_ad_is_red(plt_specs, emitted):
    """The negative control, run for real: Okta and Entra are onboarded, the
    on-prem AD forwarder is not."""
    spec, _d = plt_specs[ITDR_006]
    lake = _Lake({
        "msft_windows_security": [],
        "msft_azure_ad_signin": emitted["msft_azure_ad_signin"],
        "okta_sso": emitted["okta_sso"],
    })
    outcome = await run(spec, runner=lake)
    assert check(outcome, "chk-01").outcome.measured_value == 2.0
    assert outcome.verdict == "fail"


@pytest.mark.asyncio
async def test_xdl001_full_fidelity_is_a_real_scored_pass(plt_specs, emitted):
    """The one binding in the pack whose index row carries a number, so its
    pass is NOT clamped. This is what a machine-certified PLT pass looks like."""
    spec, _d = plt_specs[XDL_001]
    outcome = await run(spec, runner=lake_for(XDL_001, emitted))

    assert check(outcome, "chk-01").outcome.measured_value == 100.0
    assert outcome.verdict == "pass"
    assert outcome.score.primary["expected"] == 95.0


@pytest.mark.asyncio
async def test_xdl001_one_dropped_column_sinks_fidelity_below_the_bar(plt_specs, emitted):
    """A parser that maps user and device_name but drops endpoint_device_name —
    the column that makes an EAL record more than a traffic log."""
    spec, _d = plt_specs[XDL_001]
    ngfw = json.loads(json.dumps(emitted["panw_ngfw_traffic_raw"]))
    for rec in ngfw:
        rec["endpoint_device_name"] = "WKS-CORTEXSIM-0007"     # canary stripped

    lake = _Lake({
        "msft_o365_audit": emitted["msft_o365_audit"],
        "panw_ngfw_traffic_raw": ngfw,
    })
    outcome = await run(spec, runner=lake)

    measured = check(outcome, "chk-01").outcome.measured_value
    assert 0 < measured < 95, measured
    assert check(outcome, "chk-01").verdict.verdict == "fail"
    assert outcome.verdict == "fail"
    # The customer gets the NUMBER, not just a red light.
    assert str(round(measured, 1)) in check(outcome, "chk-01").verdict.detail


@pytest.mark.asyncio
async def test_xdl001_tiny_population_is_pending_not_a_100_percent_green(plt_specs, emitted):
    """Zero-of-zero is 100%, and so is one-of-one. min_population turns a
    population too small to mean anything into an open question."""
    spec, _d = plt_specs[XDL_001]
    lake = _Lake({
        "msft_o365_audit": emitted["msft_o365_audit"][:1],
        "panw_ngfw_traffic_raw": [],
    })
    outcome = await run(spec, runner=lake)

    assert check(outcome, "chk-01").outcome.code == "POPULATION_EMPTY"
    assert check(outcome, "chk-01").verdict.verdict == "pending"
    # chk-02's absolute floor is what turns the same tenant state into a stated
    # red rather than leaving the whole run merely "pending".
    assert check(outcome, "chk-02").verdict.verdict == "fail"
    assert outcome.verdict == "fail"


@pytest.mark.asyncio
async def test_xdl001_empty_lake_is_never_a_pass(plt_specs):
    spec, _d = plt_specs[XDL_001]
    outcome = await run(spec, runner=_Lake({
        "msft_o365_audit": [], "panw_ngfw_traffic_raw": [],
    }))
    assert outcome.verdict != "pass"
    assert check(outcome, "chk-01").outcome.code == "POPULATION_EMPTY"


# ---------------------------------------------------------------------------
# The test double itself has to be trustworthy
# ---------------------------------------------------------------------------


class TestLakeDouble:
    def test_and_is_not_or(self):
        lake = _Lake({"d": [{"a": "yes", "b": "no"}]})
        assert asyncio.run(lake('dataset in (d)\n| filter a contains "yes" and b contains "yes"')) == []
        assert len(asyncio.run(lake('dataset in (d)\n| filter a contains "yes" or b contains "yes"'))) == 1

    def test_parenthesised_or_of_ands(self):
        q = ('dataset in (d)\n| filter (a contains "1" and b contains "2")\n'
             '      or (c contains "3" and e contains "4")')
        assert len(asyncio.run(_Lake({"d": [{"a": "1", "b": "2"}]})(q))) == 1
        assert asyncio.run(_Lake({"d": [{"a": "1", "b": "x"}]})(q)) == []
        assert len(asyncio.run(_Lake({"d": [{"c": "3", "e": "4"}]})(q))) == 1

    def test_raw_log_sees_the_whole_body_including_nested(self):
        lake = _Lake({"d": [{"deep": {"k": "TOKEN"}}]})
        assert len(asyncio.run(lake('dataset in (d)\n| filter _raw_log contains "TOKEN"'))) == 1

    def test_nested_leaf_is_reachable_by_leaf_name(self):
        lake = _Lake({"d": [{"user": {"username": "sa-TOKEN"}}]})
        assert len(asyncio.run(lake('dataset in (d)\n| filter username contains "TOKEN"'))) == 1

    def test_comp_count_by_dataset_collapses_to_one_row_per_dataset(self):
        lake = _Lake({"a": [{"x": "1"}, {"x": "1"}], "b": [{"x": "1"}]})
        rows = asyncio.run(lake(
            'dataset in (a, b)\n| filter x contains "1"\n| comp count() as n by _dataset'))
        assert rows == [{"_dataset": "a", "n": 2}, {"_dataset": "b", "n": 1}]

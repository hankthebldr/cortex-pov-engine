"""Tests for license gating — a POV must not propose what the prospect cannot buy.

The deliverable is the *blocked* side: the delta between what a tenant owns and
what the catalog needs is an upsell list generated from the POV rather than
assembled by hand, and every capability in it carries a real part number.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def registry():
    from pathlib import Path  # noqa: PLC0415

    from engine.uctc_registry import UcTcRegistry  # noqa: PLC0415

    repo = Path(__file__).resolve().parent.parent.parent
    reg = UcTcRegistry()
    reg.load(str(repo / "docs" / "uc_tc_mapping" / "_v2.2-source"))
    return reg


class FakeScenario:
    def __init__(self, sid, base, addons, plane="EDR"):
        self.scenario_id = sid
        self.name = sid
        self.plane = plane
        self.tc_ref = "TC-EDR-03"
        self.moat_tier = "MOAT"
        self.required_base_platform = base
        self.required_addons = addons


# ---------------------------------------------------------------------------
# Entitlement derivation
# ---------------------------------------------------------------------------


def test_entitlements_resolve_through_the_use_case(registry):
    base, addons = registry.entitlements_for("TC-CDR-01")
    assert "Cortex Cloud" in base
    assert "Cloud Runtime" in addons


def test_capacity_skus_are_not_capability_gates(registry):
    """UC-NDR lists 'Data Ingestion — GB/day' as an add-on, but its own
    min_license_path is bare NG-SIEM — ingestion is metered sizing, not a gate.
    Treating it as one would block every NDR scenario for every tenant."""
    from engine.uctc_registry import CAPACITY_SKUS  # noqa: PLC0415

    _, addons = registry.entitlements_for("TC-NDR-03")
    assert addons == []
    assert "Data Ingestion — GB/day" in CAPACITY_SKUS


def test_unknown_test_case_yields_no_entitlement(registry):
    assert registry.entitlements_for("TC-NOPE-99") == ([], [])


def test_evidence_set_requires_the_union(registry):
    """A scenario is runnable only if the tenant can license everything it
    claims to prove."""
    base, addons = registry.entitlements_for_many(["TC-CDR-01", "TC-AES-01"])
    assert "Cloud Runtime" in addons and "Email Security" in addons


def test_every_addon_resolves_to_a_real_part_number(registry):
    """The upsell list is only useful if each line can be quoted."""
    seen = set()
    for tc in registry.all_test_cases():
        _, addons = registry.entitlements_for(tc.tc_id)
        seen.update(addons)
    assert seen, "expected the index to name add-ons"
    for cap in seen:
        assert registry.sku(cap) is not None, f"{cap!r} has no SKU catalog entry"


# ---------------------------------------------------------------------------
# Scoping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scope_splits_runnable_from_blocked():
    from api.pov import ScopeRequest, scope_pov  # noqa: PLC0415

    scenarios = [
        FakeScenario("S1", ["Cortex XSIAM"], []),
        FakeScenario("S2", ["Cortex XSIAM"], ["Forensics"]),
    ]
    out = await scope_pov(ScopeRequest(profile="ng-siem-bare"), _FakeDb(scenarios))
    assert out["runnable_count"] == 1
    assert out["blocked_count"] == 1
    assert out["blocked"][0]["missing_addons"] == ["Forensics"]


@pytest.mark.asyncio
async def test_base_platform_is_satisfied_by_owning_any_listed_one():
    """The index names the platforms a use case can land on, not a set the
    tenant must own in full."""
    from api.pov import ScopeRequest, scope_pov  # noqa: PLC0415

    s = [FakeScenario("S1", ["Cortex XDR", "Cortex XSIAM"], [])]
    out = await scope_pov(ScopeRequest(base_platform=["Cortex XSIAM"]), _FakeDb(s))
    assert out["runnable_count"] == 1


@pytest.mark.asyncio
async def test_missing_base_platform_blocks():
    from api.pov import ScopeRequest, scope_pov  # noqa: PLC0415

    s = [FakeScenario("S1", ["Cortex Cloud"], [])]
    out = await scope_pov(ScopeRequest(profile="premium"), _FakeDb(s))
    assert out["blocked_count"] == 1
    assert out["blocked"][0]["missing_base_platform"] == ["Cortex Cloud"]


@pytest.mark.asyncio
async def test_upsell_ranks_by_how_many_scenarios_it_unblocks():
    from api.pov import ScopeRequest, scope_pov  # noqa: PLC0415

    s = [
        FakeScenario("S1", ["Cortex XSIAM"], ["Forensics"]),
        FakeScenario("S2", ["Cortex XSIAM"], ["Forensics"]),
        FakeScenario("S3", ["Cortex XSIAM"], ["ITDR"]),
    ]
    out = await scope_pov(ScopeRequest(profile="ng-siem-bare"), _FakeDb(s))
    assert out["upsell"][0]["capability"] == "Forensics"
    assert out["upsell"][0]["unblocks_scenarios"] == 2


@pytest.mark.asyncio
async def test_upsell_carries_a_quotable_part_number(registry, monkeypatch):
    import engine.uctc_registry as mod  # noqa: PLC0415
    from api import pov  # noqa: PLC0415
    from api.pov import ScopeRequest, scope_pov  # noqa: PLC0415

    monkeypatch.setattr(mod, "registry", registry)
    monkeypatch.setattr(pov, "registry", registry)
    s = [FakeScenario("S1", ["Cortex XSIAM"], ["Forensics"])]
    out = await scope_pov(ScopeRequest(profile="ng-siem-bare"), _FakeDb(s))
    assert out["upsell"][0]["sku_part_number"] == "PAN-XSIAM-FRNS"
    assert out["upsell"][0]["meter"] == "per endpoint/yr"


@pytest.mark.asyncio
async def test_explicit_addons_are_additive_to_a_profile():
    """So an operator can model 'Enterprise plus ITDR' without restating the tier."""
    from api.pov import ScopeRequest, scope_pov  # noqa: PLC0415

    s = [FakeScenario("S1", ["Cortex XSIAM"], ["Endpoint Protection", "ITDR"])]
    blocked = await scope_pov(ScopeRequest(profile="enterprise"), _FakeDb(s))
    assert blocked["runnable_count"] == 0
    ok = await scope_pov(ScopeRequest(profile="enterprise", addons=["ITDR"]), _FakeDb(s))
    assert ok["runnable_count"] == 1


@pytest.mark.asyncio
async def test_plane_filter_scopes_the_catalog():
    from api.pov import ScopeRequest, scope_pov  # noqa: PLC0415

    db = _FakeDb([FakeScenario("S1", ["Cortex XSIAM"], [], plane="NDR"),
                  FakeScenario("S2", ["Cortex XSIAM"], [], plane="EDR")])
    out = await scope_pov(ScopeRequest(profile="premium", planes=["ndr"]), db)
    assert "'NDR'" in db.last_sql, "plane filter must reach SQL, not be applied in Python"
    assert out["total"] == 1
    assert out["runnable"][0]["plane"] == "NDR"


@pytest.mark.asyncio
async def test_three_canned_profiles_exist():
    from api.pov import CANNED_PROFILES, list_profiles  # noqa: PLC0415

    out = await list_profiles()
    assert set(out["profiles"]) == {"ng-siem-bare", "enterprise", "premium"}
    # Price-book inclusion, not docs inclusion: Enterprise bundles Endpoint
    # Protection; Premium adds XTI, ASM, and extended retention.
    assert "Endpoint Protection" in CANNED_PROFILES["enterprise"]["addons"]
    assert {"XTI", "ASM"} <= set(CANNED_PROFILES["premium"]["addons"])


# ---------------------------------------------------------------------------
# Catalog filter
# ---------------------------------------------------------------------------


def test_entitlement_filter_matches_the_scope_rule():
    from api.scenarios import _scenario_is_licensable  # noqa: PLC0415

    ok = FakeScenario("S1", ["Cortex XSIAM"], ["Endpoint Protection"])
    no = FakeScenario("S2", ["Cortex XSIAM"], ["Forensics"])
    assert _scenario_is_licensable(ok, "enterprise") is True
    assert _scenario_is_licensable(no, "enterprise") is False


def test_unknown_profile_hides_nothing():
    """Better to show the full catalog than to silently empty it behind a typo."""
    from api.scenarios import _scenario_is_licensable  # noqa: PLC0415

    s = FakeScenario("S1", ["Cortex Cloud"], ["Forensics"])
    assert _scenario_is_licensable(s, "typo-profile") is True


# ---------------------------------------------------------------------------
# Minimal async-DB stand-in
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeDb:
    """Async-session stand-in. Compiles the statement with literal binds so a
    test can assert the plane filter reached SQL, without a real database."""

    def __init__(self, rows):
        self._rows = rows
        self.last_sql = ""

    async def execute(self, stmt):
        self.last_sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        rows = [r for r in self._rows
                if f"'{r.plane}'" in self.last_sql or "WHERE" not in self.last_sql]
        return _FakeResult(rows)

"""Tests for the master UC/TC index registry and the scenario-ref FK it enforces.

Covers:

* The registry loads the committed v2.2 snapshot and exposes 266 test cases
  across 49 use cases, fused with their detection-spec rows.
* ``validate_ref`` distinguishes ok / dangling_tc / dangling_uc / fk_mismatch,
  and accepts both the UCS and UC forms of ``uc_ref`` (the corpus uses both).
* A missing snapshot degrades to ``unverified`` rather than raising, so a
  stripped deployment still boots.
* ``CORTEXSIM_STRICT_REFS`` gates whether S-10/S-11/S-12 warn or reject, and
  S-13/S-14 stay advisory in both modes.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INDEX_DIR = REPO_ROOT / "docs" / "uc_tc_mapping" / "_v2.2-source"


@pytest.fixture
def registry():
    """Fresh registry loaded from the committed v2.2 snapshot."""
    from engine.uctc_registry import UcTcRegistry  # noqa: PLC0415

    reg = UcTcRegistry()
    reg.load(str(INDEX_DIR))
    return reg


@pytest.fixture
def empty_registry(tmp_path):
    """Registry pointed at a directory with no snapshot."""
    from engine.uctc_registry import UcTcRegistry  # noqa: PLC0415

    reg = UcTcRegistry()
    reg.load(str(tmp_path / "does-not-exist"))
    return reg


# ---------------------------------------------------------------------------
# Load path
# ---------------------------------------------------------------------------


def test_loads_committed_v22_snapshot(registry):
    """The in-repo snapshot is the counted ground truth for the FY27 index."""
    assert registry.loaded is True
    assert len(registry.all_test_cases()) == 266
    assert len(registry.all_use_cases()) == 49
    assert len(registry.all_skus()) == 38
    assert registry.version == "2.2"


def test_test_case_fuses_detection_spec_row(registry):
    """The TC row and its detection-spec row must land on one object — the
    measurement contract comes from the index, the validation class from
    the spec, and the loader needs both to reason about a scenario."""
    tc = registry.tc("TC-IR-01")
    assert tc is not None
    # from tc_index_v2.2.csv
    assert tc.uc_id == "UC-IR"
    assert tc.ucs_id == "UCS-IR-01"
    assert tc.primary_kpi == "Triage Automation Rate"
    assert tc.threshold == ">60%"
    assert tc.description  # v2.2 re-authored every description
    # from detection_spec_v2.2.csv
    assert tc.validation_class == "DET"
    assert tc.priority == "P1"
    assert tc.base_platform


def test_use_case_fuses_product_addon_row(registry):
    """Phase 3 license gating reads the SKU path off the UC."""
    uc = registry.uc("UC-CDR")
    assert uc is not None
    assert uc.use_case == "Cloud Detection & Response"
    assert uc.base_platform
    assert "PAN-" in uc.addon_skus


def test_ucs_groups_its_test_cases(registry):
    """UCS is the scenario-group namespace between UC and TC."""
    group = registry.ucs("UCS-IR-01")
    assert len(group) >= 2
    assert all(tc.ucs_id == "UCS-IR-01" for tc in group)
    assert {tc.tc_id for tc in group} >= {"TC-IR-01", "TC-IR-02"}


def test_sku_lookup_returns_part_number(registry):
    sku = registry.sku("Cloud Runtime")
    assert sku is not None
    assert sku.sku_part_number.startswith("PAN-")


def test_known_uc_refs_spans_both_namespaces(registry):
    """Scenarios in the corpus write uc_ref as either UCS-* or UC-*, so both
    must be legal FK targets."""
    known = registry.known_uc_refs()
    assert "UCS-IR-01" in known
    assert "UC-IR" in known
    assert len(known) > 200


# ---------------------------------------------------------------------------
# Scoreability
# ---------------------------------------------------------------------------


def test_qualitative_threshold_is_not_scoreable(registry):
    """A TC whose threshold is 'Qualitative pass' cannot be machine-scored.
    The verifier must emit not_applicable for these, never pass."""
    unscoreable = registry.unscoreable()
    assert unscoreable, "expected the index to carry unscoreable DET/HNT rows"
    assert all(not tc.is_scoreable for tc in unscoreable)
    assert all(tc.needs_detection_content for tc in unscoreable)


def test_measurable_threshold_is_scoreable(registry):
    assert registry.tc("TC-IR-01").is_scoreable is True


def test_posture_class_needs_no_detection_content(registry):
    """POS/PLT/AUT rows are assertions, not detections — Phase 4 must not
    author detection cards for them."""
    pos = [tc for tc in registry.all_test_cases() if tc.validation_class == "POS"]
    assert pos, "expected POS-class rows in the index"
    assert all(not tc.needs_detection_content for tc in pos)


# ---------------------------------------------------------------------------
# validate_ref
# ---------------------------------------------------------------------------


def test_validate_ref_ok_with_ucs_form(registry):
    v = registry.validate_ref("UCS-IR-01", "TC-IR-01")
    assert v.status == "ok"
    assert v.ok is True
    assert v.is_error is False
    assert v.test_case.tc_id == "TC-IR-01"


def test_validate_ref_ok_with_uc_form(registry):
    """UC-IR is the parent of UCS-IR-01 — also a legal ref."""
    assert registry.validate_ref("UC-IR", "TC-IR-01").status == "ok"


def test_validate_ref_dangling_tc(registry):
    """S-10 — the dominant failure in the pre-crosswalk corpus."""
    v = registry.validate_ref("UCS-IR-01", "TC-NOPE-99")
    assert v.status == "dangling_tc"
    assert v.is_error is True
    assert "does not exist" in v.detail
    assert v.test_case is None


def test_validate_ref_dangling_uc(registry):
    """S-11 — tc_ref resolves but uc_ref names no group in the index."""
    v = registry.validate_ref("UCS-NOPE-99", "TC-IR-01")
    assert v.status == "dangling_uc"
    assert v.is_error is True
    assert v.test_case is not None  # the TC did resolve


def test_validate_ref_fk_mismatch(registry):
    """S-12 — the silent-corruption case. Both ids exist, but the tc_ref
    belongs to a different use case than the uc_ref claims."""
    tc = registry.tc("TC-IR-01")
    other = next(
        t for t in registry.all_test_cases()
        if t.ucs_id and t.ucs_id != tc.ucs_id and t.uc_id != tc.uc_id
    )
    v = registry.validate_ref(other.ucs_id, "TC-IR-01")
    assert v.status == "fk_mismatch"
    assert v.is_error is True
    assert "belongs to" in v.detail


def test_validate_ref_unverified_without_snapshot(empty_registry):
    """No snapshot must not mean 'everything is broken' — it means the check
    could not run. Boot proceeds."""
    v = empty_registry.validate_ref("UCS-IR-01", "TC-IR-01")
    assert v.status == "unverified"
    assert v.ok is True
    assert v.is_error is False


def test_empty_registry_loads_zero_not_raises(empty_registry):
    assert empty_registry.loaded is False
    assert empty_registry.all_test_cases() == []


# ---------------------------------------------------------------------------
# Loader integration — S-10..S-14
# ---------------------------------------------------------------------------


def _schema(**over):
    """Minimal valid ScenarioSchema with overridable UC/TC refs."""
    from engine.scenario_loader import ScenarioSchema  # noqa: PLC0415

    base = {
        "scenario_id": "SIM-TEST-001",
        "name": "ref-validation fixture",
        "version": "1.0",
        "status": "active",
        "plane": "EDR",
        "detection_types": ["BIOC"],
        "uc_ref": "UCS-IR-01",
        "tc_ref": "TC-IR-01",
        "uc_name": "Incident Response",
        "tc_name": "Analyst Fatigue",
        "mitre_tactic": "TA0006",
        "mitre_tactic_name": "Credential Access",
        "mitre_technique": "T1003",
        "mitre_technique_name": "OS Credential Dumping",
        "execution_identity": {"default": "www-data", "options": ["www-data"]},
        "push_supported": True,
        "pull_supported": True,
        "steps": [
            {"id": "s1", "name": "one", "command": "true", "identity": "www-data",
             "mitre_technique": "T1003",
             "expected_detections": [{"type": "BIOC", "name": "x", "plane": "EDR",
                                      "description": "d", "detection_id": "d1"}]},
            {"id": "s2", "name": "two", "command": "true", "identity": "www-data",
             "mitre_technique": "T1003",
             "expected_detections": [{"type": "BIOC", "name": "y", "plane": "EDR",
                                      "description": "d", "detection_id": "d2"}]},
        ],
    }
    base.update(over)
    return ScenarioSchema(**base)


@pytest.fixture
def loaded_singleton(registry, monkeypatch):
    """Point the module singleton the loader reads at the test registry."""
    import engine.uctc_registry as mod  # noqa: PLC0415

    monkeypatch.setattr(mod, "registry", registry)
    return registry


def test_loader_accepts_valid_ref(loaded_singleton):
    from engine.scenario_loader import _check_uctc_refs  # noqa: PLC0415

    assert _check_uctc_refs(_schema(), "test.yml") is None


def test_loader_warns_on_dangling_ref_by_default(loaded_singleton, caplog):
    """Default mode: a broken ref is loud but does not block boot — the
    crosswalk lands incrementally and 123 of 161 scenarios dangle today."""
    from engine.scenario_loader import _check_uctc_refs  # noqa: PLC0415

    with caplog.at_level(logging.WARNING):
        err = _check_uctc_refs(_schema(tc_ref="TC-NOPE-99"), "test.yml")
    assert err is None
    assert "S-10" in caplog.text
    assert "advisory" in caplog.text


def test_loader_rejects_dangling_ref_under_strict(loaded_singleton, monkeypatch):
    from config import settings  # noqa: PLC0415
    from engine.scenario_loader import _check_uctc_refs  # noqa: PLC0415

    monkeypatch.setattr(settings, "CORTEXSIM_STRICT_REFS", True)
    err = _check_uctc_refs(_schema(tc_ref="TC-NOPE-99"), "test.yml")
    assert err is not None
    assert "S-10" in err


def test_loader_rejects_fk_mismatch_under_strict(loaded_singleton, monkeypatch, registry):
    from config import settings  # noqa: PLC0415
    from engine.scenario_loader import _check_uctc_refs  # noqa: PLC0415

    tc = registry.tc("TC-IR-01")
    other = next(
        t for t in registry.all_test_cases()
        if t.ucs_id and t.ucs_id != tc.ucs_id and t.uc_id != tc.uc_id
    )
    monkeypatch.setattr(settings, "CORTEXSIM_STRICT_REFS", True)
    err = _check_uctc_refs(_schema(uc_ref=other.ucs_id), "test.yml")
    assert err is not None
    assert "S-12" in err


def test_s13_tier_disagreement_is_advisory_even_under_strict(
    loaded_singleton, monkeypatch, caplog, registry
):
    """Positioning drift is a content-review problem, not a boot blocker."""
    from config import settings  # noqa: PLC0415
    from engine.scenario_loader import _check_uctc_refs  # noqa: PLC0415

    tc = registry.tc("TC-IR-01")
    wrong = "PARITY" if tc.differentiation_tier != "PARITY" else "MOAT"
    monkeypatch.setattr(settings, "CORTEXSIM_STRICT_REFS", True)
    with caplog.at_level(logging.WARNING):
        err = _check_uctc_refs(_schema(moat_tier=wrong), "test.yml")
    assert err is None
    assert "S-13" in caplog.text


def test_s14_posture_class_binding_is_advisory(loaded_singleton, monkeypatch, caplog, registry):
    """Binding a detection scenario to a POS/PLT/AUT test case is a modelling
    smell — the TC is scored on posture state, not on a fired detection."""
    from config import settings  # noqa: PLC0415
    from engine.scenario_loader import _check_uctc_refs  # noqa: PLC0415

    pos = next(t for t in registry.all_test_cases() if t.validation_class == "POS")
    monkeypatch.setattr(settings, "CORTEXSIM_STRICT_REFS", True)
    with caplog.at_level(logging.WARNING):
        err = _check_uctc_refs(
            _schema(uc_ref=pos.ucs_id, tc_ref=pos.tc_id), "test.yml"
        )
    assert err is None
    assert "S-14" in caplog.text


def test_loader_does_not_reject_when_snapshot_absent(empty_registry, monkeypatch):
    """Strict mode plus no snapshot must still boot — otherwise a stripped
    image becomes unbootable for a reason the operator cannot see."""
    import engine.uctc_registry as mod  # noqa: PLC0415
    from config import settings  # noqa: PLC0415
    from engine.scenario_loader import _check_uctc_refs  # noqa: PLC0415

    monkeypatch.setattr(mod, "registry", empty_registry)
    monkeypatch.setattr(settings, "CORTEXSIM_STRICT_REFS", True)
    assert _check_uctc_refs(_schema(tc_ref="TC-NOPE-99"), "test.yml") is None

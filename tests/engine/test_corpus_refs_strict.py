"""The enforcement path itself, run against the real corpus.

Every other UC/TC test exercises the *machinery* — validate_ref on synthetic
refs, _check_uctc_refs on a fixture scenario. None of them walks the actual 169
scenario YAMLs through the loader with the registry loaded and strict mode on,
which means a scenario re-keyed to a dangling ref would ship green: the loader
would reject it at boot, but no test and no CI job would ever see that happen.

This closes that. It is the gate that makes CORTEXSIM_STRICT_REFS meaningful —
if it fails, `python -m core.main` would refuse to load that scenario in
production.
"""
from __future__ import annotations

from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCENARIOS_DIR = REPO_ROOT / "scenarios"
INDEX_DIR = REPO_ROOT / "docs" / "uc_tc_mapping" / "_v2.2-source"


@pytest.fixture(scope="module")
def loaded_registry():
    """Load the committed snapshot into the module singleton the loader reads.

    Module-scoped and asserting the count, because a silently-empty registry
    would make every assertion below pass vacuously — which is the exact
    false-green this file exists to prevent.
    """
    from engine.uctc_registry import registry  # noqa: PLC0415

    registry.load(str(INDEX_DIR))
    assert len(registry.all_test_cases()) == 266, (
        "the v2.2 snapshot did not load — every ref check below would pass "
        "vacuously against an empty registry"
    )
    return registry


@pytest.fixture(scope="module")
def parsed(loaded_registry):
    """Every scenario in the corpus, parsed. Rejects a parse failure outright —
    that is a different defect and should not be silently skipped here."""
    from engine.scenario_loader import _find_yaml_files, _parse_and_validate  # noqa: PLC0415

    out = []
    for path in _find_yaml_files(str(SCENARIOS_DIR)):
        schema, err = _parse_and_validate(path)
        assert err is None, f"{path} failed schema validation:\n{err}"
        out.append((schema, path))
    return out


def test_the_corpus_is_the_expected_size(parsed):
    """A sudden drop would make the ref checks below trivially pass."""
    assert len(parsed) == 169


def test_every_scenario_passes_strict_ref_validation(parsed, monkeypatch):
    """The gate: with CORTEXSIM_STRICT_REFS on, no scenario is rejected.

    A failure here means the named scenario would NOT load in production.
    """
    from config import settings  # noqa: PLC0415
    from engine.scenario_loader import _check_uctc_refs  # noqa: PLC0415

    monkeypatch.setattr(settings, "CORTEXSIM_STRICT_REFS", True)
    rejections = []
    for schema, path in parsed:
        err = _check_uctc_refs(schema, path)
        if err:
            rejections.append(err)

    assert not rejections, (
        f"{len(rejections)} scenario(s) would be REJECTED at boot under "
        f"CORTEXSIM_STRICT_REFS:\n" + "\n".join(rejections)
    )


def test_every_tc_ref_resolves_and_parents_agree(parsed, loaded_registry):
    """S-10/S-11/S-12 restated as a direct assertion, so a failure names the
    offending scenario rather than surfacing as a rejection count."""
    for schema, _ in parsed:
        for ref in schema.tc_refs:
            assert loaded_registry.tc(ref) is not None, (
                f"{schema.scenario_id} references {ref}, which is not in the "
                f"v{loaded_registry.version} index"
            )
        primary = loaded_registry.tc(schema.tc_ref)
        assert schema.uc_ref in (primary.ucs_id, primary.uc_id), (
            f"{schema.scenario_id} declares uc_ref={schema.uc_ref} but "
            f"{schema.tc_ref} belongs to {primary.ucs_id} / {primary.uc_id}"
        )


def test_primary_ref_is_always_first_in_the_evidence_set(parsed):
    """tc_refs[0] drives uc_ref, the derived entitlements and the report header,
    so the two fields drifting apart would be silently wrong everywhere."""
    for schema, _ in parsed:
        assert schema.tc_refs, f"{schema.scenario_id} has an empty tc_refs"
        assert schema.tc_refs[0] == schema.tc_ref, (
            f"{schema.scenario_id}: tc_ref={schema.tc_ref} but "
            f"tc_refs[0]={schema.tc_refs[0]}"
        )
        assert len(set(schema.tc_refs)) == len(schema.tc_refs), (
            f"{schema.scenario_id} has duplicate entries in tc_refs"
        )


def test_declared_payload_exists_in_the_index(parsed, loaded_registry):
    """S-16 as a hard assertion. Advisory at boot because a net-new payload is
    legitimate content for v2.3 — but the current corpus declares none, and a
    new dangling one should be a deliberate decision, not a silent typo."""
    for schema, _ in parsed:
        if not schema.pov_scenario_id:
            continue
        assert loaded_registry.payload(schema.pov_scenario_id) is not None, (
            f"{schema.scenario_id} declares pov_scenario_id="
            f"{schema.pov_scenario_id}, which names no payload in the index"
        )


def test_no_endpoint_plane_scenario_demands_a_cloud_sku(parsed, loaded_registry):
    """Regression guard for the over-binding the alignment audit found.

    required_addons is unioned over every tc_ref, so one aspirational
    cloud-scoped ref on an endpoint scenario made it demand Cortex Cloud +
    Cloud Runtime — a POV-scoping error that would quote the wrong part numbers.

    EDR and NDR scenarios emit endpoint-process and NGFW telemetry; neither can
    produce a cloud_audit_logs or container_events record, so neither should
    ever require a cloud *runtime* SKU. ANALYTICS is deliberately excluded — a
    multi-plane scenario may genuinely span cloud.

    Three scenarios are allowed through. Their refs are honest; the mismatch is
    upstream, in the index's UC-level product mapping:

    * SIM-EDR-014 / SIM-EDR-020 bind TC-WAAS-04, "Agent-Based Threat Detection
      (Runtime) — Cortex Agent detects web application attacks". That is the
      XDR agent catching a web shell, but UC-WAAS is filed under Cortex Cloud.
    * SIM-EDR-018 binds TC-CITH-07, AI-powered alert stitching — a core XSIAM
      capability filed under the Cortex-Cloud-mapped UC-CITH.

    Dropping those refs to make the SKU tidy would be the force-fit this whole
    effort exists to avoid, so they stay and the index gap is documented in
    docs/uc_tc_mapping/index-gaps-v2.2.md instead.
    """
    KNOWN_INDEX_MAPPING_GAP = {"SIM-EDR-014", "SIM-EDR-018", "SIM-EDR-020"}

    offenders = []
    for schema, _ in parsed:
        if schema.plane not in ("EDR", "NDR"):
            continue
        if schema.scenario_id in KNOWN_INDEX_MAPPING_GAP:
            continue
        _, addons = loaded_registry.entitlements_for_many(schema.tc_refs)
        cloud = [a for a in addons if a in ("Cloud Runtime", "Cloud Posture")]
        if cloud:
            offenders.append(f"{schema.scenario_id} ({schema.plane}) -> {cloud} "
                             f"via {schema.tc_refs}")
    assert not offenders, (
        "endpoint/network scenarios requiring a cloud SKU:\n" + "\n".join(offenders)
    )

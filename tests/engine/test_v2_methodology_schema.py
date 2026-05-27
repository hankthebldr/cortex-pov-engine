"""Schema-level tests for the v2.0 methodology extension.

Verifies that:
- The new ``AI_SPM`` plane is accepted by the Pydantic validator.
- The optional v2.0 KPI / methodology fields (validation_methodology,
  methodology_family, primary_kpi, threshold, success_criteria, moat_tier)
  load when present and don't break when absent.
- F2-specific fields (correlation_window_seconds, required_planes_in_incident,
  stitching_key) accept and reject as expected.
- The reference implementation ``sim-aispm-001-ai-asset-discovery.yml``
  parses cleanly with all v2.0 fields populated.
- Invalid enum values for methodology_family and moat_tier are rejected.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

# Ensure ``core/`` is importable so ``from engine.scenario_loader`` works.
import sys
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "core"))

from engine.scenario_loader import (  # noqa: E402
    KpiThreshold,
    ScenarioSchema,
    _parse_and_validate,
)


# Minimal valid scenario payload — every required field present, no v2.0 extras.
_BASE_SCENARIO = {
    "scenario_id": "SIM-TEST-001",
    "name": "test scenario",
    "version": "1.0",
    "status": "active",
    "plane": "EDR",
    "detection_types": ["BIOC"],
    "uc_ref": "UCS-TEST-01",
    "tc_ref": "TC-TEST-01",
    "uc_name": "Test UC",
    "tc_name": "Test TC",
    "mitre_tactic": "TA0001",
    "mitre_tactic_name": "Initial Access",
    "mitre_technique": "T1190",
    "mitre_technique_name": "Exploit Public-Facing Application",
    "execution_identity": {"default": "root", "options": ["root"]},
    "push_supported": True,
    "pull_supported": True,
    "steps": [
        {
            "id": "step-01",
            "name": "noop",
            "command": "true",
            "identity": "root",
            "mitre_technique": "T1190",
            "expected_detections": [],
        }
    ],
}


def test_ai_spm_plane_accepted() -> None:
    """AI_SPM is the new plane introduced by the v2.0 methodology pass."""
    payload = {**_BASE_SCENARIO, "plane": "AI_SPM"}
    schema = ScenarioSchema(**payload)
    assert schema.plane == "AI_SPM"


def test_v2_fields_optional_when_absent() -> None:
    """Scenarios without v2.0 fields still validate — back-compat guarantee."""
    schema = ScenarioSchema(**_BASE_SCENARIO)
    assert schema.validation_methodology is None
    assert schema.methodology_family is None
    assert schema.primary_kpi is None
    assert schema.threshold is None
    assert schema.success_criteria is None
    assert schema.moat_tier is None
    assert schema.correlation_window_seconds is None
    assert schema.required_planes_in_incident == []
    assert schema.stitching_key is None


def test_v2_fields_loaded_when_present() -> None:
    """When the YAML supplies v2.0 fields, they round-trip through the schema."""
    payload = {
        **_BASE_SCENARIO,
        "validation_methodology": "Causality Graph Stitching",
        "methodology_family": "F2",
        "primary_kpi": "Cross-Source Correlation Rate",
        "threshold": {"kpi": "Cross-Source Correlation Rate", "op": "≥", "value": 80, "unit": "%"},
        "success_criteria": "Single incident with all 3 planes represented.",
        "moat_tier": "MOAT",
        "correlation_window_seconds": 60,
        "required_planes_in_incident": ["EDR", "NDR", "ITDR"],
        "stitching_key": "src_host",
    }
    schema = ScenarioSchema(**payload)
    assert schema.methodology_family == "F2"
    assert schema.moat_tier == "MOAT"
    assert isinstance(schema.threshold, KpiThreshold)
    assert schema.threshold.value == 80
    assert schema.required_planes_in_incident == ["EDR", "NDR", "ITDR"]


@pytest.mark.parametrize("bad_family", ["F0", "F11", "G2", "f2", ""])
def test_methodology_family_enum_rejects_bad_values(bad_family: str) -> None:
    payload = {**_BASE_SCENARIO, "methodology_family": bad_family}
    with pytest.raises(Exception):
        ScenarioSchema(**payload)


@pytest.mark.parametrize("bad_tier", ["moat", "GOLD", "S-Tier", ""])
def test_moat_tier_enum_rejects_bad_values(bad_tier: str) -> None:
    payload = {**_BASE_SCENARIO, "moat_tier": bad_tier}
    with pytest.raises(Exception):
        ScenarioSchema(**payload)


def test_per_detection_verification_xql_and_kpi_contribution() -> None:
    """The new per-detection fields (verification_xql, kpi_contribution) load."""
    payload = {
        **_BASE_SCENARIO,
        "steps": [
            {
                "id": "step-01",
                "name": "with detection",
                "command": "true",
                "identity": "root",
                "mitre_technique": "T1190",
                "expected_detections": [
                    {
                        "plane": "EDR",
                        "type": "BIOC",
                        "description": "test detection",
                        "verification_xql": "dataset = xdr_data | comp count() as n",
                        "kpi_contribution": {
                            "kpi": "MTTD",
                            "op": "≤",
                            "value": 60,
                            "unit": "seconds",
                        },
                    }
                ],
            }
        ],
    }
    schema = ScenarioSchema(**payload)
    det = schema.steps[0].expected_detections[0]
    assert det.verification_xql is not None
    assert det.kpi_contribution is not None
    assert det.kpi_contribution.kpi == "MTTD"
    assert det.kpi_contribution.value == 60


def test_reference_aispm_scenario_parses_cleanly() -> None:
    """The reference SIM-AISPM-001 scenario validates end-to-end —
    catches drift between docs/methodology and the actual YAML."""
    path = REPO_ROOT / "scenarios" / "ai_spm" / "sim-aispm-001-ai-asset-discovery.yml"
    assert path.exists(), f"reference scenario missing at {path}"
    schema, err = _parse_and_validate(str(path))
    assert err is None, f"reference scenario failed validation:\n{err}"
    assert schema is not None
    assert schema.scenario_id == "SIM-AISPM-001"
    assert schema.plane == "AI_SPM"
    assert schema.moat_tier == "MOAT"
    assert schema.methodology_family == "F3"
    assert schema.primary_kpi == "Asset Discovery Coverage"
    assert schema.threshold is not None and schema.threshold.value == 100
    assert schema.tc_ref == "TC-AISP-01"
    # Sanity: at least one step has an expected_detection with verification_xql.
    has_xql = any(
        det.verification_xql
        for step in schema.steps
        for det in step.expected_detections
    )
    assert has_xql, "expected at least one verification_xql in the reference scenario"


def test_invalid_plane_still_rejected() -> None:
    """Sanity: adding AI_SPM didn't accidentally widen the enum."""
    payload = {**_BASE_SCENARIO, "plane": "NOT_A_REAL_PLANE"}
    with pytest.raises(Exception):
        ScenarioSchema(**payload)

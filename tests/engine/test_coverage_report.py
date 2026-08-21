"""Tests for detection_scanner/scripts/coverage_report.py.

Drives the analyzer's pure ``compute_report`` over a TINY SYNTHETIC corpus in
tmp_path (never the live tree, so the assertions stay stable as the real corpus
grows). Loads the script via importlib exactly as test_export_artifacts does, and
keeps to stdlib + pyyaml so it runs host-side without the app deps.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "detection_scanner" / "scripts" / "coverage_report.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("coverage_report", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["coverage_report"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def module():
    return _load_module()


def _write_scenario(path: Path, *, sid, plane, tactic, tactic_name, technique,
                    family, detections):
    steps = [{
        "id": "step-01",
        "name": "synthetic step",
        "mitre_technique": technique,
        "expected_detections": [
            {"plane": plane, "type": t, "description": f"{t} synthetic"}
            for t in detections
        ],
    }]
    doc = {
        "scenario_id": sid,
        "name": sid,
        "status": "active",
        "plane": plane,
        "mitre_tactic": tactic,
        "mitre_tactic_name": tactic_name,
        "mitre_technique": technique,
        "methodology_family": family,
        "steps": steps,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")


def _write_card(path: Path, *, ttp_id, bioc_name):
    doc = {
        "id": ttp_id,
        "status": "active",
        "identity": {"name": bioc_name},
        "mitre_attack": {
            "techniques": [{
                "technique_id": "T1003",
                "subtechnique_id": "T1003.001",
                "tactic_ids": ["TA0006"],
                "tactic_names": ["Credential Access"],
            }],
        },
        "detections": {
            "biocs": [{
                "name": bioc_name,
                "logic": "dataset = xdr_data\n| filter event_type = ENUM.PROCESS",
                "mitre_technique_ids": ["T1003.001"],
                "severity": "high",
            }],
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")


@pytest.fixture
def corpus(tmp_path):
    scen = tmp_path / "scenarios"
    ttps = tmp_path / "ttps"

    # EDR: 3 scenarios (family F1, tactic TA0006) -> meets the plane floor (3),
    # so EDR is NOT sub-floor. Mostly BIOC/XQL + exactly ONE ABIOC across them.
    _write_scenario(scen / "edr" / "e1.yml", sid="SIM-EDR-901", plane="EDR",
                    tactic="TA0006", tactic_name="Credential Access",
                    technique="T1003", family="F1", detections=["BIOC", "XQL"])
    _write_scenario(scen / "edr" / "e2.yml", sid="SIM-EDR-902", plane="EDR",
                    tactic="TA0006", tactic_name="Credential Access",
                    technique="T1003", family="F1",
                    detections=["BIOC", "XQL", "ABIOC"])
    _write_scenario(scen / "edr" / "e3.yml", sid="SIM-EDR-903", plane="EDR",
                    tactic="TA0006", tactic_name="Credential Access",
                    technique="T1003", family="F1", detections=["BIOC", "XQL"])
    # CSPM: 1 scenario (family F4, tactic TA0007) -> sub-floor at plane_floor 3.
    _write_scenario(scen / "cspm" / "c1.yml", sid="SIM-CSPM-901", plane="CSPM",
                    tactic="TA0007", tactic_name="Discovery",
                    technique="T1046", family="F4", detections=["XQL", "XQL"])

    # Two near-duplicate cards: same base T1003, same dataset xdr_data, near
    # names -> exactly one overlap pair.
    _write_card(ttps / "TTP-2026-9001.json", ttp_id="TTP-2026-9001",
                bioc_name="LSASS Handle Open Sensitive")
    _write_card(ttps / "TTP-2026-9002.json", ttp_id="TTP-2026-9002",
                bioc_name="LSASS Handle Open With Sensitive Access")

    return scen, ttps


def test_plane_floor_sub_floor_and_deficit(module, corpus):
    """DL-05: CSPM is sub-floor (1<3, deficit 2); EDR (2 scenarios) is not."""
    scen, ttps = corpus
    result = module.compute_report(str(scen), str(ttps), module.default_config())

    assert "CSPM" in result["planes"]["sub_floor"]
    assert "EDR" not in result["planes"]["sub_floor"]
    cspm = next(r for r in result["planes"]["counts"] if r["plane"] == "CSPM")
    assert cspm["deficit"] == 2
    assert cspm["below_floor"] is True


def test_detection_mix_shares_and_abioc_below_target(module, corpus):
    """DL-01: step shares sum to ~1.0 and ABIOC+Analytics is below the 0.15 default."""
    scen, ttps = corpus
    result = module.compute_report(str(scen), str(ttps), module.default_config())

    shares = result["detection_mix"]["step_detections"]["shares"]
    assert sum(shares.values()) == pytest.approx(1.0)
    assert result["detection_mix"]["abioc_analytics_below_target"] is True
    # 1 ABIOC out of 9 step-detections = ~0.111 < 0.15.
    assert result["detection_mix"]["step_detections"]["total"] == 9


def test_overlap_finds_single_near_duplicate_pair(module, corpus):
    """Near-duplicate finder: exactly one pair on base T1003 / dataset xdr_data."""
    scen, ttps = corpus
    result = module.compute_report(str(scen), str(ttps), module.default_config())

    pairs = result["overlap"]["pairs"]
    assert len(pairs) == 1
    pair = pairs[0]
    assert pair["base_technique"] == "T1003"
    assert pair["dataset"] == "xdr_data"
    assert pair["similarity"] >= 0.60


def test_methodology_empty_families_and_exit_code_contract(module, corpus):
    """DL-06 + exit-code contract without spawning a subprocess."""
    scen, ttps = corpus
    result = module.compute_report(str(scen), str(ttps), module.default_config())

    empty = set(result["methodology"]["empty_families"])
    assert "F6" in empty
    # Only F1 (2 scenarios) and F4 (1) are populated; every other family empty.
    assert empty == {"F2", "F3", "F5", "F6", "F7", "F8", "F9", "F10"}

    breaches = result["strict_breaches"]
    assert breaches  # sub-floor plane + shallow tactics + empty families + det-mix
    # The pure (strict, breaches) -> code helper: WARN-only 0, --strict 1.
    assert module._exit_code(False, breaches) == 0
    assert module._exit_code(True, breaches) == 1
    # compute_report bakes the default (non-strict) exit code into the dict.
    assert result["exit_code"] == 0


def test_malformed_scenario_is_skipped_not_fatal(module, corpus):
    """A scenario that fails yaml.safe_load is WARN+skipped, not counted, no raise."""
    scen, ttps = corpus
    bad = scen / "edr" / "broken.yml"
    bad.write_text("name: [unterminated\n:::\n  bad", encoding="utf-8")

    result = module.compute_report(str(scen), str(ttps), module.default_config())
    # Still only the 4 well-formed active scenarios (e1, e2, e3, c1).
    assert result["meta"]["scenario_count"] == 4
    assert any("broken.yml" in w for w in result["_warnings"])

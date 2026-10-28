"""SIM-KOI-009 shape guard: the executable definition of "done" for the rewrite.

Written RED against the intake scaffold; Task 4 is finished when this passes.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from engine.scenario_loader import validate_causality_spine
from engine.ttp_catalog import TtpCatalog

REPO_ROOT = Path(__file__).resolve().parents[2]
SCENARIO = REPO_ROOT / "scenarios" / "koi" / "sim-koi-009-glassworm-agentic-endpoint-takeover.yml"
CARD = REPO_ROOT / "detection_scanner" / "ttps" / "TTP-2026-0197-sim-koi-009.json"
HOOK = REPO_ROOT / ".claude" / "hooks" / "lint-scenario.py"


def _as_spine(steps):
    """validate_causality_spine reads ``.id`` / ``.causality.parent_step`` attributes
    (the StepSchema shape), not dict keys; expose just those for the raw YAML steps."""
    out = []
    for s in steps:
        c = s.get("causality")
        out.append(SimpleNamespace(
            id=s["id"],
            causality=SimpleNamespace(parent_step=c["parent_step"]) if c else None,
        ))
    return out


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture
def scenario():
    return yaml.safe_load(SCENARIO.read_text(encoding="utf-8"))


@pytest.fixture
def card():
    return json.loads(CARD.read_text(encoding="utf-8"))


@pytest.fixture
def catalog():
    c = TtpCatalog()
    c.load(str(REPO_ROOT / "detection_scanner" / "ttps"))
    return c


@pytest.fixture
def coverage_report():
    path = REPO_ROOT / "detection_scanner" / "scripts" / "coverage_report.py"
    spec = importlib.util.spec_from_file_location("coverage_report", path)
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    sys.modules["coverage_report"] = m
    spec.loader.exec_module(m)
    return m


def test_status_active_and_ids(scenario, card):
    assert scenario["status"] == "active" and card["status"] == "active"
    assert scenario["scenario_id"] == "SIM-KOI-009" and card["id"] == "TTP-2026-0197"

def test_six_steps_all_macos_direct(scenario):
    steps = scenario["steps"]
    assert [s["id"] for s in steps] == [f"step-0{i}" for i in range(1, 7)]
    assert all(s["platforms"] == ["macos"] for s in steps)
    assert all(s["identity"] == "direct" for s in steps)
    assert scenario["execution_identity"] == {"default": "direct", "options": ["direct"]}

def test_zero_marker_only_steps(scenario, coverage_report):
    assert [s["id"] for s in scenario["steps"] if coverage_report.is_marker_only_step(s)] == []

def test_no_exit_masking(scenario):
    for s in scenario["steps"]:
        for bad in ("|| true", "|| echo", "; true", "2>/dev/null ||"):
            assert bad not in s["command"], (s["id"], bad)

def test_seven_step_detections_one_correlation(scenario):
    per_step = [len(s["expected_detections"]) for s in scenario["steps"]]
    assert per_step == [1, 1, 1, 1, 2, 1]
    dets = [d for s in scenario["steps"] for d in s["expected_detections"]]
    assert len(dets) == 7
    assert [d["detection_id"] for d in dets if d["type"] == "Correlation"] == ["CR-KOI-0009"]
    assert scenario["steps"][-1]["expected_detections"][-1]["detection_id"] == "CR-KOI-0009"
    assert all(d["ttp_ref"] == "TTP-2026-0197" for d in dets)
    assert all(d.get("outcome", "detect") == "detect" for d in dets)


def test_no_declared_expectation_is_a_hunting_only_query(scenario):
    """The two egress queries cannot fire in the documented lab (no sink, no
    firewall in path, and step-06 never sets CORTEXSIM_C2_URL), so they are
    ``purpose: hunt`` card content and must not be declared as expectations —
    declaring them scores a tenant whose detections all worked as a miss."""
    hunting_only = {
        "xql-koi-009-archive-sent-from-ide-lineage-to-non-marketplace-host",
        "xql-koi-009-ngfw-session-from-ide-host-to-non-marketplace-destination",
    }
    declared = {d["detection_id"] for s in scenario["steps"] for d in s["expected_detections"]}
    assert declared.isdisjoint(hunting_only), declared & hunting_only
    assert not any(d["plane"] == "NDR" for s in scenario["steps"] for d in s["expected_detections"])


def test_step01_guards_the_platform(scenario):
    """Linux/root runs must fail loudly: the push bundle's header claims Ubuntu
    and root, and without the guard every step fails while the bundle exits 0."""
    cmd = scenario["steps"][0]["command"]
    assert "uname -s" in cmd and "Darwin" in cmd
    assert "id -u" in cmd
    assert "exit 2" in cmd
    assert cmd.index("exit 2") < cmd.index("SRC="), "platform guard must precede SRC="

def test_every_detection_id_resolves_to_the_card(scenario, catalog):
    for s in scenario["steps"]:
        for d in s["expected_detections"]:
            assert catalog.find("TTP-2026-0197", d["detection_id"]) is not None, d["detection_id"]

def test_no_indicators_block(scenario):
    assert "indicators" not in scenario

def test_causality_spine_is_connected_and_matches_spec(scenario):
    validate_causality_spine(_as_spine(scenario["steps"]))  # raises on forward/self refs or two roots
    by_id = {s["id"]: s for s in scenario["steps"]}
    assert "causality" not in by_id["step-01"]
    assert by_id["step-02"]["causality"] == {"parent_step": "step-01", "pivot": "shared_entity"}
    for sid in ("step-03", "step-04", "step-05"):
        assert by_id[sid]["causality"] == {"parent_step": "step-02", "pivot": "process_lineage"}
    assert by_id["step-06"]["causality"] == {"parent_step": "step-05", "pivot": "network_session"}
    assert scenario["cgo_anchor"]["primary_username"] == "developer"

def test_step01_references_the_pack_component(scenario):
    assert "sources/cortex-malicious-agentic-pack/vscode/prettier-pro-canary" in scenario["steps"][0]["command"]

def test_cleanup_removes_everything_it_created(scenario):
    joined = "\n".join(scenario["cleanup"]["commands"])
    for needle in ("launchctl bootout", "LaunchAgents", "delete-keychain",
                   "--uninstall-extension", "CORTEXSIM"):
        assert needle in joined, needle

def test_refs_and_methodology(scenario):
    assert scenario["uc_ref"] == "UCS-SCA-01" and scenario["tc_ref"] == "TC-SCA-01"
    assert scenario["tc_refs"] == ["TC-SCA-01", "TC-SSCAN-01"]
    assert scenario["pov_scenario_id"] == "POV-SC-012"
    assert scenario["methodology_family"] == "F1" and scenario["primary_kpi"] == "Detection Accuracy"
    assert scenario["threshold"] == {"kpi": "Detection Accuracy", "op": "≥", "value": 90, "unit": "%"}
    assert scenario["mitre_technique"] == "T1195"
    assert {t["technique"] for t in scenario["additional_techniques"]} == {
        "T1059.002", "T1543.001", "T1555.001", "T1552.001", "T1074.001", "T1560", "T1041"}

def test_lint_hook_says_ok(repo_root):
    out = subprocess.run([sys.executable, str(HOOK), str(SCENARIO), str(repo_root)],
                         capture_output=True, text=True)
    assert out.returncode == 0 and "OK" in out.stdout, out.stdout + out.stderr

"""Unit tests for core/engine/report_generator.py — Phase 8 POV artifacts.

The generator is pure: dict inputs → string / dict / bytes outputs. No DB,
no FastAPI dependency. Tests assert the shape matches the worked
example under ``lab_cortex_analytics_pov/``.
"""
from __future__ import annotations

import csv
import io
import json
import tarfile

import pytest

from engine import report_generator as rg


# ---------------------------------------------------------------------------
# Fixtures — synthetic run / scenario / results that mirror the lab shape
# ---------------------------------------------------------------------------


def _run():
    return {
        "run_id": "11111111-2222-3333-4444-555555555555",
        "scenario_id": "SIM-NDR-001",
        "mode": "pull",
        "status": "complete",
        "started_at": "2026-05-12T09:00:00+00:00",
        "completed_at": "2026-05-12T09:05:00+00:00",
    }


def _scenario():
    return {
        "scenario_id": "SIM-NDR-001",
        "name": "C2 Beacon Callback — NGFW Validation",
        "plane": "NDR",
        "mitre_tactic": "TA0011",
        "mitre_tactic_name": "Command and Control",
        "mitre_technique": "T1071.001",
        "mitre_technique_name": "Application Layer Protocol: Web Protocols",
        "additional_techniques": [
            {"technique": "T1568", "name": "Dynamic Resolution"},
        ],
        "threat_report": "Unit42 - Cobalt Strike Beacon Trends",
        "steps": [
            {"id": "step-01", "mitre_technique": "T1071.001"},
            {"id": "step-02", "mitre_technique": "T1568"},
        ],
    }


def _results(observed_count=2, missed_count=1, pending_count=1):
    out = []
    rid = 1
    for _ in range(observed_count):
        out.append({
            "id": rid, "run_id": "x", "step_id": "step-01",
            "plane": "NDR",
            "signal_type": "Analytics",
            "expected_detection": "Repetitive HTTP beacon to known-bad IOC",
            "observed": True,
            "observed_at": "2026-05-12T09:03:00+00:00",
            "executed_at": "2026-05-12T09:02:30+00:00",
            "mttd_seconds": 30.0,
            "mitre_technique": "T1071.001",
        })
        rid += 1
    for _ in range(missed_count):
        out.append({
            "id": rid, "run_id": "x", "step_id": "step-02",
            "plane": "NDR",
            "signal_type": "BIOC",
            "expected_detection": "DNS tunnelling anomaly",
            "observed": False,
            "observed_at": "2026-05-12T09:04:00+00:00",  # marked missed
            "executed_at": "2026-05-12T09:02:30+00:00",
            "mttd_seconds": None,
            "mitre_technique": "T1568",
        })
        rid += 1
    for _ in range(pending_count):
        out.append({
            "id": rid, "run_id": "x", "step_id": "step-03",
            "plane": "ANALYTICS",
            "signal_type": "Correlation",
            "expected_detection": "XSIAM stitch — beacon + DNS into one incident",
            "observed": False,
            "observed_at": None,
            "executed_at": "2026-05-12T09:02:30+00:00",
            "mttd_seconds": None,
            "mitre_technique": None,
        })
        rid += 1
    return out


# ---------------------------------------------------------------------------
# Detection matrix
# ---------------------------------------------------------------------------


class TestDetectionMatrix:
    def test_row_per_result(self):
        rows = rg.build_detection_matrix(_run(), _scenario(), _results(2, 1, 1))
        assert len(rows) == 4

    def test_observed_row_marked_enabled(self):
        rows = rg.build_detection_matrix(_run(), _scenario(), _results(1, 0, 0))
        assert rows[0].status == "ENABLED"
        assert rows[0].severity == "High"

    def test_missed_row_marked_disabled(self):
        rows = rg.build_detection_matrix(_run(), _scenario(), _results(0, 1, 0))
        assert rows[0].status == "DISABLED"

    def test_pending_row_marked_pending(self):
        rows = rg.build_detection_matrix(_run(), _scenario(), _results(0, 0, 1))
        assert rows[0].status == "PENDING"

    def test_correlation_alert_type_severity_is_critical(self):
        rows = rg.build_detection_matrix(_run(), _scenario(), _results(0, 0, 1))
        # Pending correlation → severity still Critical per the lab convention.
        assert rows[0].alert_type == "Correlation"
        assert rows[0].severity == "Critical"

    def test_csv_header_matches_lab_example(self):
        rows = rg.build_detection_matrix(_run(), _scenario(), _results())
        out = rg.render_detection_matrix_csv(rows)
        reader = csv.reader(io.StringIO(out))
        header = next(reader)
        assert header == ["Alert Name", "Source(s)", "Status",
                          "Alert Type", "ATT&CK TID", "Severity"]

    def test_csv_renders_n_rows(self):
        rows = rg.build_detection_matrix(_run(), _scenario(), _results(2, 1, 1))
        out = rg.render_detection_matrix_csv(rows)
        # 1 header + 4 data rows = 5 lines (trailing newline included).
        lines = [l for l in out.strip().splitlines() if l.strip()]
        assert len(lines) == 5


# ---------------------------------------------------------------------------
# ATT&CK Navigator layer
# ---------------------------------------------------------------------------


class TestNavigatorLayer:
    def test_layer_shape_matches_v45(self):
        layer = rg.render_attack_navigator_layer(_run(), _scenario(), _results())
        assert layer["versions"]["layer"] == "4.5"
        assert layer["domain"] == "enterprise-attack"
        assert "techniques" in layer
        assert isinstance(layer["techniques"], list)

    def test_observed_technique_red_missed_grey(self):
        layer = rg.render_attack_navigator_layer(_run(), _scenario(),
                                                  _results(observed_count=1, missed_count=1, pending_count=0))
        by_id = {t["techniqueID"]: t for t in layer["techniques"]}
        assert by_id["T1071.001"]["color"] == "#e60d0d"
        assert by_id["T1568"]["color"] == "#919191"

    def test_scenario_techniques_included_even_when_no_result_observed(self):
        # No results at all — every scenario technique should still appear
        # in the layer (so the customer sees the full scope, not a partial).
        layer = rg.render_attack_navigator_layer(_run(), _scenario(), [])
        ids = {t["techniqueID"] for t in layer["techniques"]}
        assert {"T1071.001", "T1568"} <= ids

    def test_layer_name_carries_scenario_name(self):
        layer = rg.render_attack_navigator_layer(_run(), _scenario(), _results())
        assert "C2 Beacon" in layer["name"]

    def test_layer_round_trips_through_json(self):
        layer = rg.render_attack_navigator_layer(_run(), _scenario(), _results())
        encoded = json.dumps(layer)
        decoded = json.loads(encoded)
        assert decoded == layer

    def test_comment_carries_plane_and_signal_type(self):
        layer = rg.render_attack_navigator_layer(_run(), _scenario(), _results(1, 0, 0))
        by_id = {t["techniqueID"]: t for t in layer["techniques"]}
        assert "DETECTED" in by_id["T1071.001"]["comment"]
        assert "NDR" in by_id["T1071.001"]["comment"]


# ---------------------------------------------------------------------------
# Executive summary markdown
# ---------------------------------------------------------------------------


class TestExecSummary:
    def test_includes_coverage_pct(self):
        md = rg.render_exec_summary_markdown(_run(), _scenario(),
                                              _results(2, 1, 1))
        assert "## Key Findings" in md
        assert "50.0%" in md  # 2 / 4 observed

    def test_includes_mttd_when_observations_exist(self):
        md = rg.render_exec_summary_markdown(_run(), _scenario(),
                                              _results(2, 0, 0))
        assert "MTTD" in md
        assert "30.0s" in md

    def test_omits_mttd_section_when_no_observations(self):
        md = rg.render_exec_summary_markdown(_run(), _scenario(),
                                              _results(0, 0, 2))
        assert "MTTD" not in md

    def test_includes_scenario_name_and_plane(self):
        md = rg.render_exec_summary_markdown(_run(), _scenario(),
                                              _results(1, 0, 0))
        assert "C2 Beacon" in md
        assert "`NDR`" in md

    def test_strong_coverage_verdict(self):
        md = rg.render_exec_summary_markdown(_run(), _scenario(),
                                              _results(4, 0, 0))
        assert "strong Cortex detection coverage" in md

    def test_weak_coverage_verdict(self):
        md = rg.render_exec_summary_markdown(_run(), _scenario(),
                                              _results(0, 4, 0))
        assert "coverage gap" in md


# ---------------------------------------------------------------------------
# Bundle (tar.gz)
# ---------------------------------------------------------------------------


class TestBundle:
    def test_bundle_contains_three_artifacts(self):
        blob = rg.build_bundle(_run(), _scenario(), _results(2, 1, 1))
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
            names = sorted(tar.getnames())
        assert names == [
            "attack_navigator_layer.json",
            "detection_matrix.csv",
            "pov_narrative/exec_summary.md",
        ]

    def test_bundle_csv_content_valid(self):
        blob = rg.build_bundle(_run(), _scenario(), _results(1, 0, 0))
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
            csv_member = tar.extractfile("detection_matrix.csv")
            content = csv_member.read().decode("utf-8")
        assert "Alert Name" in content.splitlines()[0]

    def test_bundle_navigator_content_valid_json(self):
        blob = rg.build_bundle(_run(), _scenario(), _results(1, 0, 0))
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
            nav_member = tar.extractfile("attack_navigator_layer.json")
            data = json.loads(nav_member.read())
        assert data["versions"]["layer"] == "4.5"
        assert any(t["techniqueID"] == "T1071.001" for t in data["techniques"])

    def test_bundle_md_includes_summary_section(self):
        blob = rg.build_bundle(_run(), _scenario(), _results(1, 0, 0))
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
            md_member = tar.extractfile("pov_narrative/exec_summary.md")
            md = md_member.read().decode("utf-8")
        assert "## Key Findings" in md

    def test_bundle_with_no_results_still_produces_bundle(self):
        # Empty-results edge case (a launched run with no expected_detections).
        blob = rg.build_bundle(_run(), _scenario(), [])
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
            md = tar.extractfile("pov_narrative/exec_summary.md").read().decode("utf-8")
        assert "No expected detections" in md


# ---------------------------------------------------------------------------
# Resilience
# ---------------------------------------------------------------------------


class TestResilience:
    def test_no_scenario_still_renders(self):
        rows = rg.build_detection_matrix(_run(), None, _results(1, 0, 0))
        assert len(rows) == 1
        layer = rg.render_attack_navigator_layer(_run(), None, _results(1, 0, 0))
        assert "techniques" in layer
        md = rg.render_exec_summary_markdown(_run(), None, _results(1, 0, 0))
        assert "Executive Summary" in md

    def test_result_missing_technique_lands_in_csv_with_dash(self):
        results = _results(0, 0, 1)
        results[0]["mitre_technique"] = None
        rows = rg.build_detection_matrix(_run(), _scenario(), results)
        assert rows[0].attack_tid in ("T1071.001", "—")  # falls back to scenario primary

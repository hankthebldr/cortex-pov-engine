"""Unit tests for core/engine/efficacy_scorecard.py — POV efficacy scorecard.

The scorecard builder is pure: ``Result.to_dict()``-shaped dicts in →
frozen dataclass + markdown/HTML out. No DB, no FastAPI. These tests assert
the coverage math, MTTD percentiles, the three breakdowns (plane /
detection_type / product), and that both renderers emit branded output.
"""
from __future__ import annotations

import pytest

from engine import efficacy_scorecard as es


# ---------------------------------------------------------------------------
# Fixtures — synthetic Result.to_dict() rows
# ---------------------------------------------------------------------------


def _result(**over):
    base = {
        "id": 1,
        "run_id": "run-1",
        "step_id": "step-01",
        "step_name": "Do the thing",
        "plane": "EDR",
        "signal_type": "BIOC",
        "detection_kind": "bioc",
        "expected_detection": "Credential dump BIOC",
        "observed": False,
        "observed_at": None,
        "mttd_seconds": None,
        "mitre_technique": "T1003",
    }
    base.update(over)
    return base


def _results():
    return [
        # detected, MTTD 30s, EDR / BIOC
        _result(id=1, plane="EDR", detection_kind="bioc", observed=True,
                observed_at="2026-01-01T00:00:30", mttd_seconds=30.0),
        # detected, MTTD 90s, CDR / ABIOC
        _result(id=2, plane="CDR", signal_type="Analytics", detection_kind="abioc",
                observed=True, observed_at="2026-01-01T00:01:30", mttd_seconds=90.0),
        # detected, MTTD 300s, NDR / Correlation
        _result(id=3, plane="NDR", detection_kind="correlation", observed=True,
                observed_at="2026-01-01T00:05:00", mttd_seconds=300.0),
        # missed (observed_at set, observed False), EDR / XQL
        _result(id=4, plane="EDR", detection_kind="xql", observed=False,
                observed_at="2026-01-01T00:00:10", mttd_seconds=None),
        # pending, ASM / IOC
        _result(id=5, plane="ASM", detection_kind="ioc", observed=False,
                observed_at=None, mttd_seconds=None),
    ]


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def test_normalize_detection_type_prefers_kind():
    assert es.normalize_detection_type(_result(detection_kind="abioc",
                                                signal_type="Analytics")) == "ABIOC"


def test_normalize_detection_type_falls_back_to_signal():
    assert es.normalize_detection_type(
        {"signal_type": "Analytics", "detection_kind": None}) == "Analytics"


def test_normalize_detection_type_unknown_is_other():
    assert es.normalize_detection_type(
        {"signal_type": "Wut", "detection_kind": None}) == "Other"


def test_product_for_plane_mapping_and_default():
    assert es.product_for_plane("EDR") == "Cortex XDR"
    assert es.product_for_plane("cdr") == "Cortex Cloud"
    assert es.product_for_plane("browser") == "Prisma Browser"
    assert es.product_for_plane("nonexistent") == "Cortex XSIAM"


# ---------------------------------------------------------------------------
# Coverage + counts
# ---------------------------------------------------------------------------


def test_coverage_counts_and_pct():
    sc = es.build_efficacy_scorecard(_results())
    assert sc.coverage.detected == 3
    assert sc.coverage.missed == 1
    assert sc.coverage.pending == 1
    assert sc.coverage.expected == 5
    assert sc.coverage.pct == 60.0


def test_empty_results_zero_coverage():
    sc = es.build_efficacy_scorecard([])
    assert sc.coverage.expected == 0
    assert sc.coverage.pct == 0.0
    assert sc.mttd.count == 0
    assert sc.mttd.median_seconds is None
    assert sc.run_count == 0


# ---------------------------------------------------------------------------
# MTTD distribution
# ---------------------------------------------------------------------------


def test_mttd_min_median_p90_max():
    sc = es.build_efficacy_scorecard(_results())
    assert sc.mttd.count == 3
    assert sc.mttd.min_seconds == 30.0
    assert sc.mttd.max_seconds == 300.0
    # median of [30, 90, 300] = 90
    assert sc.mttd.median_seconds == 90.0
    # p90 via linear interpolation on [30,90,300]: rank=0.9*2=1.8 →
    # 90 + 0.8*(300-90) = 258.0
    assert sc.mttd.p90_seconds == 258.0


def test_percentile_single_value():
    assert es._percentile([42.0], 90) == 42.0


def test_percentile_empty():
    assert es._percentile([], 50) is None


# ---------------------------------------------------------------------------
# Breakdowns
# ---------------------------------------------------------------------------


def test_breakdown_by_plane():
    sc = es.build_efficacy_scorecard(_results())
    by_plane = {r.label: r for r in sc.by_plane}
    assert set(by_plane) == {"EDR", "CDR", "NDR", "ASM"}
    # EDR has 1 detected + 1 missed = 50%
    assert by_plane["EDR"].detected == 1
    assert by_plane["EDR"].missed == 1
    assert by_plane["EDR"].pct == 50.0


def test_breakdown_by_detection_type_uses_vocab():
    sc = es.build_efficacy_scorecard(_results())
    labels = {r.label for r in sc.by_detection_type}
    assert labels == {"BIOC", "ABIOC", "Correlation", "XQL", "IOC"}


def test_breakdown_by_product_collapses_planes():
    sc = es.build_efficacy_scorecard(_results())
    by_product = {r.label: r for r in sc.by_product}
    # EDR → Cortex XDR (2 rows), CDR → Cortex Cloud, NDR → Cortex XSIAM,
    # ASM → Cortex ASM
    assert by_product["Cortex XDR"].expected == 2
    assert by_product["Cortex Cloud"].expected == 1
    assert "Cortex ASM" in by_product


def test_breakdown_sorted_gap_first():
    sc = es.build_efficacy_scorecard(_results())
    # First plane row should be the one with the most missed+pending.
    first = sc.by_plane[0]
    assert first.missed + first.pending >= sc.by_plane[-1].missed + sc.by_plane[-1].pending


# ---------------------------------------------------------------------------
# Multi-run / portfolio
# ---------------------------------------------------------------------------


def test_multi_run_run_count_and_ids():
    sc = es.build_efficacy_scorecard(
        _results(), run_ids=["run-1", "run-2", "run-1"])
    # de-duped, order preserved
    assert sc.run_ids == ("run-1", "run-2")
    assert sc.run_count == 2


def test_single_run_default_count():
    sc = es.build_efficacy_scorecard(_results())
    assert sc.run_count == 1


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def test_to_dict_round_trip_shape():
    sc = es.build_efficacy_scorecard(_results(), run_ids=["run-1"])
    d = sc.to_dict()
    assert d["coverage"]["pct"] == 60.0
    assert d["mttd"]["median_seconds"] == 90.0
    assert isinstance(d["by_plane"], list)
    assert d["by_plane"][0]["label"]
    assert d["run_ids"] == ["run-1"]


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def test_render_markdown_contains_branding_and_stats():
    md = es.render_markdown(es.build_efficacy_scorecard(_results()))
    assert "# Cortex POV Efficacy Scorecard" in md
    assert "60.0% coverage" in md
    assert "Coverage by Cortex Product" in md
    assert "Coverage by Detection Type" in md
    assert "Mean Time to Detect" in md
    assert "CortexSim" in md


def test_render_markdown_no_mttd_when_none():
    rows = [_result(observed=False, observed_at=None, mttd_seconds=None)]
    md = es.render_markdown(es.build_efficacy_scorecard(rows))
    assert "_No evidence-backed MTTD measured" in md


def test_render_html_is_self_contained_and_branded():
    hd = es.render_html(es.build_efficacy_scorecard(_results()))
    assert "<style>" in hd
    assert es._CORTEX_NAVY in hd
    assert es._CORTEX_TEAL in hd
    assert "Coverage" in hd
    # escapes labels
    assert "Cortex XDR" in hd


def test_render_html_escapes_labels():
    rows = [_result(plane="<script>", detection_kind=None, signal_type="<x>")]
    hd = es.render_html(es.build_efficacy_scorecard(rows))
    assert "<script>" not in hd
    assert "&lt;script&gt;" in hd


def test_format_mttd_buckets():
    assert es.format_mttd(None) == "—"
    assert es.format_mttd(30) == "30s"
    assert es.format_mttd(90) == "1m 30s"
    assert es.format_mttd(3661) == "1h 1m"


def test_scorecard_reports_the_test_case_verdict_not_just_coverage():
    """Coverage and verdict answer different questions and can disagree.

    A run can observe every seeded detection — 100% coverage — and still FAIL
    its test case by blowing the MTTD threshold. A CISO one-pager that reports
    only coverage invites "100% covered" to be read as "passed", which the data
    does not support.
    """
    from engine.efficacy_scorecard import build_efficacy_scorecard  # noqa: PLC0415

    rows = [{"observed": True, "plane": "EDR", "signal_type": "BIOC",
             "mttd_seconds": 900.0}]
    sc = build_efficacy_scorecard(rows, run_ids=["r1"], tc_verdicts=["fail"])

    assert sc.coverage.pct == 100.0          # every expected detection observed
    assert sc.verdicts.failed == 1           # and yet the test case failed
    assert sc.verdicts.pass_pct == 0.0
    assert sc.to_dict()["verdicts"]["failed"] == 1


def test_a_run_with_no_verdict_is_unscored_never_passed():
    from engine.efficacy_scorecard import build_efficacy_scorecard  # noqa: PLC0415

    sc = build_efficacy_scorecard([{"observed": True, "plane": "EDR"}],
                                  run_ids=["r1"], tc_verdicts=[None])
    assert sc.verdicts.unscored == 1
    assert sc.verdicts.passed == 0


def test_pass_pct_is_none_when_nothing_was_scoreable():
    """0% would read as "everything failed"; the truth is "nothing could be
    scored", which is a different message entirely."""
    from engine.efficacy_scorecard import build_efficacy_scorecard  # noqa: PLC0415

    sc = build_efficacy_scorecard([{"observed": True, "plane": "EDR"}],
                                  run_ids=["r1"], tc_verdicts=["not_applicable"])
    assert sc.verdicts.pass_pct is None
    assert sc.verdicts.not_applicable == 1

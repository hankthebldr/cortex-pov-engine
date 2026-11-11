"""B4 — a case sealed by the response layer withholds the correlation FAIL.

When the SOC Framework sealed one case before the chain finished, the run's
alerts were split across cases by the framework, not by the detection engine.
A correlation FAIL caused by that split is withheld as ``pending``. A PASS is
never touched, and no other KPI family reads ``merge_class``.
"""
from __future__ import annotations

from types import SimpleNamespace

from connectors.measures import SEALED_SPLIT_REASON, build_measurement

CORR = "Cross-Source Correlation Rate"
THRESH_80 = {"kpi": CORR, "op": "≥", "value": 80, "unit": "%"}
ACC_T = {"kpi": "Detection Accuracy", "op": "≥", "value": 80, "unit": "%"}


def _r(observed=True):
    return SimpleNamespace(observed=observed, executed_at=object())


RESULTS = [_r(True), _r(True)]


def test_sealed_split_withholds_correlation_fail():
    value, rec = build_measurement(CORR, THRESH_80, RESULTS,
        {"basis": "reconcile", "alert_incidents": {"a1": "A", "a2": "B"},
         "merge_class": "SPLIT_SEALED"})
    assert value is None and rec["withheld"] == SEALED_SPLIT_REASON
    assert rec["scored"] is None


def test_sealed_split_never_touches_a_pass():
    # alerts all in one case: the value passes; merge_class forced to SPLIT_SEALED
    value, rec = build_measurement(CORR, THRESH_80, RESULTS,
        {"basis": "reconcile", "alert_incidents": {"a1": "A", "a2": "A"},
         "merge_class": "SPLIT_SEALED"})
    assert value == 100.0 and "withheld" not in rec


def test_native_split_fail_stands():
    value, rec = build_measurement(CORR, THRESH_80, RESULTS,
        {"basis": "reconcile", "alert_incidents": {"a1": "A", "a2": "B"},
         "merge_class": "SPLIT_NATIVE"})
    assert value == 0.0 and rec["scored"] == 0.0 and "withheld" not in rec


def test_absent_merge_class_leaves_correlation_fail_standing():
    # No ledger was built (no case ids on the alerts, or no by-id case read):
    # the key is absent and must behave exactly like any non-SPLIT_SEALED value.
    value, rec = build_measurement(CORR, THRESH_80, RESULTS,
        {"basis": "reconcile", "alert_incidents": {"a1": "A", "a2": "B"}})
    assert value == 0.0 and rec["scored"] == 0.0 and "withheld" not in rec


def test_accuracy_family_ignores_merge_class():
    ctx_without = {"basis": "reconcile", "unscoped": False, "truncated": False,
                   "alert_incidents": {}}
    a = build_measurement("Detection Accuracy", ACC_T, RESULTS, ctx_without)
    b = build_measurement("Detection Accuracy", ACC_T, RESULTS,
                          {**ctx_without, "merge_class": "SPLIT_SEALED"})
    assert a == b

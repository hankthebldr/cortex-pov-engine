"""Tests for the pure Detection Storyline assembler (Detection Proof Layer).

The builder is pure (no DB / HTTP), so these tests exercise it entirely over
dict fixtures shaped like ``Result.to_dict`` / ``Run.to_dict`` /
``scenario.steps`` plus a lightweight ORM stub to prove ``.to_dict()``
normalization.

Covers:
* kill-chain ordering follows scenario step order + intra-step seed order
* status classification: detected / pending / missed (run-status aware)
* staged (push) run keeps un-observed detections ``pending``, not ``missed``
* coverage % and MTTD p50/p90 distribution over detected entries only
* observed sub-object built from a reconciled alert AND from a bare row
* evidence_refs extraction from list-of-dicts or bare-id list
* ORM-object inputs (via ``.to_dict()``) and orphaned-step defence
* empty-results summary is all-zero with null MTTD
"""

from __future__ import annotations

import pytest

from engine.detection_storyline import (
    STATUS_DETECTED,
    STATUS_MISSED,
    STATUS_PENDING,
    _percentile,
    build_storyline,
    build_summary,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _result(
    rid,
    step_id,
    *,
    step_name="",
    plane="EDR",
    signal_type="BIOC",
    description="expected detection",
    detection_id=None,
    ttp_ref="TTP-2026-0032",
    mitre_technique="T1003.008",
    observed=False,
    observed_at=None,
    mttd_seconds=None,
    detection_kind=None,
    detection_severity=None,
    evidence=None,
    **extra,
):
    """Build a ``Result.to_dict``-shaped mapping."""
    return {
        **extra,
        "id": rid,
        "run_id": "run-1",
        "step_id": step_id,
        "step_name": step_name,
        "plane": plane,
        "signal_type": signal_type,
        "expected_detection": description,
        "detection_id": detection_id or f"det-{rid}",
        "ttp_ref": ttp_ref,
        "mitre_technique": mitre_technique,
        "observed": observed,
        "observed_at": observed_at,
        "mttd_seconds": mttd_seconds,
        "detection_kind": detection_kind,
        "detection_severity": detection_severity,
        "evidence": evidence or [],
    }


@pytest.fixture
def steps():
    return [
        {"id": "step-01", "name": "Enumerate users", "mitre_technique": "T1087.001"},
        {"id": "step-02", "name": "Read shadow", "mitre_technique": "T1003.008"},
    ]


@pytest.fixture
def running_run():
    return {"run_id": "run-1", "scenario_id": "SIM-EDR-001", "status": "running"}


@pytest.fixture
def complete_run():
    return {"run_id": "run-1", "scenario_id": "SIM-EDR-001", "status": "complete"}


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------


def test_ordering_follows_step_then_seed_order(steps, running_run):
    # step-02 has two detections; feed results out of order to prove sorting.
    results = [
        _result(30, "step-02", detection_id="d-b", signal_type="XQL"),
        _result(10, "step-01", detection_id="d-a"),
        _result(20, "step-02", detection_id="d-shadow"),
    ]
    story = build_storyline(running_run, results, steps)
    ordered_ids = [e["expected_detection"]["detection_id"] for e in story["entries"]]
    # step-01 first, then step-02 in the order the results were supplied.
    assert ordered_ids == ["d-a", "d-b", "d-shadow"]
    # Grouped steps mirror the flat order.
    assert [g["step_id"] for g in story["steps"]] == ["step-01", "step-02"]
    assert len(story["steps"][1]["detections"]) == 2
    assert story["entries"][0]["step_index"] == 0
    assert story["entries"][2]["step_index"] == 1


# ---------------------------------------------------------------------------
# Status classification
# ---------------------------------------------------------------------------


def test_pending_while_run_inflight(steps, running_run):
    results = [_result(10, "step-01"), _result(20, "step-02")]
    story = build_storyline(running_run, results, steps)
    assert all(e["status"] == STATUS_PENDING for e in story["entries"])
    assert story["summary"]["coverage_pct"] == 0.0
    assert story["summary"]["pending"] == 2
    assert story["summary"]["missed"] == 0


def test_unjudged_row_on_a_complete_run_is_pending_not_missed(steps, complete_run):
    """R4: a terminal run no longer turns an outcome-less row into a gap; only
    the engine's ``outcome == "missed"`` is a miss."""
    results = [
        _result(10, "step-01", observed=True, observed_at="2026-06-17T14:00:05", mttd_seconds=5.0),
        _result(20, "step-02"),  # never reconciled, run complete → awaiting
        _result(30, "step-02", outcome="missed"),  # the engine judged a miss
    ]
    story = build_storyline(complete_run, results, steps)
    statuses = {e["expected_detection"]["detection_id"]: e["status"] for e in story["entries"]}
    assert statuses["det-10"] == STATUS_DETECTED
    assert statuses["det-20"] == STATUS_PENDING
    assert statuses["det-30"] == STATUS_MISSED
    summ = story["summary"]
    assert summ["detected"] == 1
    assert summ["missed"] == 1
    assert summ["pending"] == 1
    assert summ["coverage_pct"] == 33.3


def test_staged_run_keeps_unobserved_pending(steps):
    staged = {"run_id": "run-1", "scenario_id": "SIM-EDR-001", "status": "staged"}
    results = [_result(10, "step-01"), _result(20, "step-02")]
    story = build_storyline(staged, results, steps)
    # Push-mode 'staged' is proof-pending, not a 0%-missed gap.
    assert all(e["status"] == STATUS_PENDING for e in story["entries"])
    assert story["summary"]["missed"] == 0


def test_observed_flag_without_timestamp_still_detected(steps, running_run):
    results = [_result(10, "step-01", observed=True)]
    story = build_storyline(running_run, results, steps)
    assert story["entries"][0]["status"] == STATUS_DETECTED


# ---------------------------------------------------------------------------
# Observed sub-object
# ---------------------------------------------------------------------------


def test_observed_none_until_detected(steps, running_run):
    results = [_result(10, "step-01")]
    story = build_storyline(running_run, results, steps)
    assert story["entries"][0]["observed"] is None
    assert story["entries"][0]["mttd_seconds"] is None


def test_observed_from_reconciled_alert(steps, running_run):
    results = [
        _result(
            10, "step-01",
            observed=True, observed_at="2026-06-17T14:03:22", mttd_seconds=12.0,
        )
    ]
    observations = {
        10: {
            "name": "LSASS Credential Dump BIOC",
            "external_id": "ALERT-991",
            "techniques": ["T1003.001"],
            "observed_at": "2026-06-17T14:03:22",
            "severity": "critical",
            "host": "web-01",
        }
    }
    story = build_storyline(running_run, results, steps, observations=observations)
    obs = story["entries"][0]["observed"]
    assert obs["alert_name"] == "LSASS Credential Dump BIOC"
    assert obs["alert_id"] == "ALERT-991"
    assert obs["technique"] == "T1003.001"
    assert obs["severity"] == "critical"
    assert obs["host"] == "web-01"
    assert story["entries"][0]["mttd_seconds"] == 12.0


def test_observed_from_bare_row_when_no_observation(steps, running_run):
    # Human-checkbox path: observed_at set, no alert evidence supplied.
    results = [
        _result(
            10, "step-01",
            observed=True, observed_at="2026-06-17T14:03:22",
            mitre_technique="T1003.008",
        )
    ]
    story = build_storyline(running_run, results, steps)
    obs = story["entries"][0]["observed"]
    assert obs is not None
    assert obs["alert_name"] is None
    assert obs["alert_id"] is None
    # Falls back to the Result's own technique.
    assert obs["technique"] == "T1003.008"
    assert obs["observed_at"] == "2026-06-17T14:03:22"


# ---------------------------------------------------------------------------
# Detection type / evidence
# ---------------------------------------------------------------------------


def test_detection_type_prefers_signal_type_then_kind(steps, running_run):
    results = [
        _result(10, "step-01", signal_type="ABIOC"),
        _result(20, "step-02", signal_type=None, detection_kind="correlation"),
    ]
    story = build_storyline(running_run, results, steps)
    types = [e["expected_detection"]["type"] for e in story["entries"]]
    assert types == ["ABIOC", "CORRELATION"]


def test_evidence_refs_from_dicts_and_bare_ids(steps, running_run):
    results = [
        _result(10, "step-01", evidence=[{"id": 5, "kind": "alert_json"}, {"id": 6}]),
        _result(20, "step-02", evidence=[7, 8]),
    ]
    story = build_storyline(running_run, results, steps)
    assert story["entries"][0]["evidence_refs"] == [5, 6]
    assert story["entries"][1]["evidence_refs"] == [7, 8]


# ---------------------------------------------------------------------------
# ORM normalization + orphan defence
# ---------------------------------------------------------------------------


class _FakeResult:
    """Minimal ORM-like row exposing to_dict()."""

    def __init__(self, data):
        self._data = data

    def to_dict(self):
        return dict(self._data)


def test_accepts_orm_objects_via_to_dict(steps, running_run):
    results = [_FakeResult(_result(10, "step-01"))]
    story = build_storyline(running_run, results, steps)
    assert story["entries"][0]["step_id"] == "step-01"
    assert story["summary"]["total"] == 1


def test_orphaned_step_results_are_not_dropped(steps, complete_run):
    # step-99 has no matching scenario step — must still appear.
    results = [
        _result(10, "step-01", observed=True, observed_at="t", mttd_seconds=3.0),
        _result(99, "step-99", step_name="Rogue step"),
    ]
    story = build_storyline(complete_run, results, steps)
    step_ids = [g["step_id"] for g in story["steps"]]
    assert "step-99" in step_ids
    assert story["summary"]["total"] == 2
    # Orphan step inherits its result's step_name and appends after real steps.
    orphan = [g for g in story["steps"] if g["step_id"] == "step-99"][0]
    assert orphan["step_name"] == "Rogue step"
    assert orphan["step_index"] == len(steps)


# ---------------------------------------------------------------------------
# Summary + percentile math
# ---------------------------------------------------------------------------


def test_empty_summary_is_zeroed():
    summ = build_summary([])
    assert summ == {
        "total": 0,
        "detected": 0,
        "pending": 0,
        "missed": 0,
        "prevented": 0,
        "not_reached": 0,
        "not_executed": 0,
        "denominator": 0,
        "coverage_pct": 0.0,
        "mttd": {"count": 0, "min": None, "median": None, "p90": None, "max": None},
    }


def test_mttd_distribution_over_detected_only(steps, complete_run):
    # Ten detected entries with MTTD 1..10 across two steps.
    results = []
    for i in range(1, 11):
        step = "step-01" if i <= 5 else "step-02"
        results.append(
            _result(i, step, observed=True, observed_at="t", mttd_seconds=float(i))
        )
    # Add a missed one — must NOT enter the MTTD distribution.
    results.append(_result(99, "step-02", outcome="missed"))
    story = build_storyline(complete_run, results, steps)
    mttd = story["summary"]["mttd"]
    assert mttd["count"] == 10
    assert mttd["min"] == 1.0
    assert mttd["max"] == 10.0
    assert mttd["median"] == 5.5   # linear interpolation of 1..10
    assert mttd["p90"] == 9.1
    assert story["summary"]["detected"] == 10
    assert story["summary"]["missed"] == 1


def test_percentile_helper_edges():
    assert _percentile([], 50) is None
    assert _percentile([42.0], 90) == 42.0
    assert _percentile([1.0, 2.0, 3.0], 50) == 2.0
    assert _percentile([1.0, 2.0, 3.0, 4.0], 0) == 1.0
    assert _percentile([1.0, 2.0, 3.0, 4.0], 100) == 4.0


def test_top_level_shape(steps, running_run):
    results = [_result(10, "step-01")]
    story = build_storyline(running_run, results, steps)
    assert story["run_id"] == "run-1"
    assert story["scenario_id"] == "SIM-EDR-001"
    assert story["run_status"] == "running"
    assert set(story.keys()) == {
        "run_id", "scenario_id", "run_status", "steps", "entries", "summary",
    }


def test_none_run_defaults_to_pending(steps):
    results = [_result(10, "step-01")]
    story = build_storyline(None, results, steps)
    assert story["run_status"] == STATUS_PENDING
    assert story["entries"][0]["status"] == STATUS_PENDING


def test_entry_status_prefers_outcome_states():
    from engine.detection_storyline import (
        STATUS_NOT_EXECUTED, STATUS_NOT_REACHED, STATUS_PREVENTED, _entry_status,
    )
    # Outcome wins even when the legacy flags say otherwise.
    assert _entry_status(_result(1, "s", observed=True, outcome="prevented"), "complete") == STATUS_PREVENTED
    assert _entry_status(_result(2, "s", outcome="not_reached"), "complete") == STATUS_NOT_REACHED
    assert _entry_status(_result(3, "s", outcome="not_executed"), "complete") == STATUS_NOT_EXECUTED
    # Reasoned pending stays pending on a terminal run (not a manufactured miss).
    pend = _result(4, "s", outcome="pending", outcome_reason="ANALYTICS_ACTIVATION_PERIOD")
    assert _entry_status(pend, "complete") == STATUS_PENDING
    # R4: an un-reasoned pending stays pending; an outcome-less unvalidated
    # row is awaiting reconcile, not a miss, even on a terminal run.
    assert _entry_status(_result(5, "s", outcome="pending"), "complete") == STATUS_PENDING
    assert _entry_status(_result(6, "s"), "complete") == STATUS_PENDING
    assert _entry_status(_result(7, "s", observed=True, outcome="pending",
                                 outcome_reason="PREVENTION_ALERT_WITHOUT_TERMINATION"),
                         "complete") == STATUS_PENDING
    assert _entry_status(_result(8, "s", outcome="missed"), "complete") == STATUS_MISSED


def test_storyline_entries_and_summary_carry_outcome_fields():
    res = [
        _result(1, "step-01", outcome="prevented", prevention_module="btp",
                alert_action="BLOCKED", alert_outcome="prevented"),
        _result(2, "step-02", outcome="not_reached"),
        _result(3, "step-03", outcome="not_executed"),
        _result(4, "step-04", outcome="pending", outcome_reason="DEDUP_WINDOW"),
        _result(5, "step-05", observed=True, observed_at="2026-01-01T00:00:00"),
    ]
    sl = build_storyline({"run_id": "r", "scenario_id": "S", "status": "complete"}, res, [])
    e0 = sl["entries"][0]
    assert (e0["outcome"], e0["prevention_module"], e0["alert_action"], e0["alert_outcome"]) == (
        "prevented", "btp", "BLOCKED", "prevented")
    s = sl["summary"]
    assert (s["prevented"], s["not_reached"], s["not_executed"]) == (1, 1, 1)
    assert s["total"] == 5
    assert s["denominator"] == 2  # prevented + detected; not_*/soak-pending leave


def test_coverage_pct_uses_honest_denominator():
    res = [
        _result(1, "step-01", observed=True, observed_at="2026-01-01T00:00:00"),
        _result(2, "step-02", outcome="prevented"),
        _result(3, "step-03", outcome="not_reached"),
        _result(4, "step-04", outcome="pending", outcome_reason="ANALYTICS_ACTIVATION_PERIOD"),
    ]
    s = build_storyline({"run_id": "r", "scenario_id": "S", "status": "complete"}, res, [])["summary"]
    assert (s["total"], s["denominator"], s["coverage_pct"]) == (4, 2, 100.0)
    only_unreached = build_storyline({"run_id": "r", "scenario_id": "S", "status": "complete"},
                                     [_result(1, "s", outcome="not_reached")], [])["summary"]
    assert (only_unreached["denominator"], only_unreached["coverage_pct"]) == (0, 0.0)

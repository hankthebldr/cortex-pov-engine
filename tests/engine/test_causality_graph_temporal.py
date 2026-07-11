"""Tests for the CG-04 temporal-proximity edge on the pure Causality Graph
assembler.

``correlation_window_seconds`` was a declared-but-unused scenario field: the
storyline computed only a per-detection MTTD, never the proximity *between* two
catches. ``build_causality_graph`` now emits a CONFIRMED ``temporal`` edge for
every pair of DETECTED alert nodes whose ``observed_at`` timestamps fall within
the scenario's raw declared window.

These exercise the REAL builder (no stub of the subject), entirely over dict
fixtures shaped like ``Result.to_dict`` / ``Run.to_dict`` / ``scenario.steps``.

Covers:
* two detections inside the window → one CONFIRMED temporal edge carrying the delta
* two detections outside the window → no temporal edge
* the raw ``correlation_window_seconds`` — not the stitch-key fallback — governs it
  (a scenario with only ``stitching_key`` gets no temporal edge)
* pairwise fan-out across three co-fired detections
* a pair already joined by a stronger evidence edge (shared_entity) is not
  duplicated by a temporal edge
* undetected / pending detections are ignored (no firing timestamp to correlate)
* temporal edges never distort the delegated coverage summary (projection invariant)
"""

from __future__ import annotations

import pytest

from engine.causality_graph import (
    EDGE_SHARED_ENTITY,
    EDGE_TEMPORAL,
    STATE_CONFIRMED,
    build_causality_graph,
)
from engine.detection_storyline import build_storyline, build_summary


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _result(
    rid,
    step_id,
    *,
    plane="EDR",
    signal_type="BIOC",
    observed=None,
    observed_at=None,
    mttd_seconds=None,
    control_layer="prevention",
    mitre_technique="T1059.004",
    **extra,
):
    # A detection counts as DETECTED only when ``observed`` is truthy; default it
    # from the presence of a firing timestamp so callers pass one field.
    if observed is None:
        observed = observed_at is not None
    row = {
        "id": rid,
        "run_id": "run-1",
        "step_id": step_id,
        "step_name": "",
        "plane": plane,
        "signal_type": signal_type,
        "expected_detection": "expected detection",
        "detection_id": f"det-{rid}",
        "ttp_ref": "TTP-2026-0090",
        "mitre_technique": mitre_technique,
        "observed": observed,
        "observed_at": observed_at,
        "mttd_seconds": mttd_seconds,
        "detection_kind": signal_type.lower(),
        "detection_severity": None,
        "control_layer": control_layer,
        "asset_ref": None,
        "evidence": [],
    }
    row.update(extra)
    return row


def _step(sid, command="curl http://x"):
    # ``root`` identity → direct spawn (no wrapper node), keeps the graph minimal.
    return {"id": sid, "name": sid, "command": command, "identity": "root"}


@pytest.fixture
def run():
    return {"run_id": "run-1", "scenario_id": "SIM-TEMP", "status": "complete"}


def _temporal(graph):
    return [e for e in graph["edges"] if e["kind"] == EDGE_TEMPORAL]


# ---------------------------------------------------------------------------
# Core behaviour
# ---------------------------------------------------------------------------


def test_temporal_edge_confirmed_within_window(run):
    steps = [_step("s1"), _step("s2")]
    results = [
        _result(1, "s1", observed_at="2026-07-10T10:00:00", mttd_seconds=3),
        _result(2, "s2", observed_at="2026-07-10T10:00:04", mttd_seconds=7),
    ]
    meta = {"correlation_window_seconds": 10}
    graph = build_causality_graph(run, results, steps, scenario_meta=meta)

    temporal = _temporal(graph)
    assert len(temporal) == 1
    edge = temporal[0]
    assert edge["state"] == STATE_CONFIRMED
    assert edge["delta_seconds"] == 4.0
    # Wired between the two alert nodes, not the structural process spine.
    assert {edge["source"], edge["target"]} == {"alert:1", "alert:2"}


def test_no_temporal_edge_outside_window(run):
    steps = [_step("s1"), _step("s2")]
    results = [
        _result(1, "s1", observed_at="2026-07-10T10:00:00"),
        _result(2, "s2", observed_at="2026-07-10T10:00:30"),
    ]
    meta = {"correlation_window_seconds": 10}
    graph = build_causality_graph(run, results, steps, scenario_meta=meta)
    assert _temporal(graph) == []


def test_temporal_edge_at_exact_window_boundary(run):
    steps = [_step("s1"), _step("s2")]
    results = [
        _result(1, "s1", observed_at="2026-07-10T10:00:00"),
        _result(2, "s2", observed_at="2026-07-10T10:00:10"),
    ]
    meta = {"correlation_window_seconds": 10}
    graph = build_causality_graph(run, results, steps, scenario_meta=meta)
    # delta == window is inclusive (<=).
    temporal = _temporal(graph)
    assert len(temporal) == 1
    assert temporal[0]["delta_seconds"] == 10.0


def test_no_temporal_edge_when_window_undeclared(run):
    steps = [_step("s1"), _step("s2")]
    results = [
        _result(1, "s1", observed_at="2026-07-10T10:00:00"),
        _result(2, "s2", observed_at="2026-07-10T10:00:04"),
    ]
    # No scenario_meta at all → no window → no temporal edges even in proximity.
    graph = build_causality_graph(run, results, steps)
    assert _temporal(graph) == []


def test_stitch_key_fallback_does_not_manufacture_a_window(run):
    # ``window`` falls back to the default stitch window when a stitching_key is
    # present, but the RAW ``correlation_window_seconds`` governs temporal edges —
    # so declaring only a stitching_key must NOT create temporal edges.
    steps = [_step("s1"), _step("s2")]
    results = [
        _result(1, "s1", observed_at="2026-07-10T10:00:00"),
        _result(2, "s2", observed_at="2026-07-10T10:00:04"),
    ]
    meta = {"stitching_key": "src_host"}  # no correlation_window_seconds
    graph = build_causality_graph(run, results, steps, scenario_meta=meta)
    assert _temporal(graph) == []


def test_temporal_edges_pairwise_across_detected_nodes(run):
    steps = [_step("s1"), _step("s2"), _step("s3")]
    results = [
        _result(1, "s1", observed_at="2026-07-10T10:00:00"),
        _result(2, "s2", observed_at="2026-07-10T10:00:03"),
        _result(3, "s3", observed_at="2026-07-10T10:00:06"),
    ]
    meta = {"correlation_window_seconds": 10}
    graph = build_causality_graph(run, results, steps, scenario_meta=meta)
    temporal = _temporal(graph)
    # C(3,2) = 3 pairs, all within the 10s window.
    assert len(temporal) == 3
    assert all(e["state"] == STATE_CONFIRMED for e in temporal)
    pairs = {frozenset((e["source"], e["target"])) for e in temporal}
    assert pairs == {
        frozenset(("alert:1", "alert:2")),
        frozenset(("alert:1", "alert:3")),
        frozenset(("alert:2", "alert:3")),
    }


def test_temporal_prunes_far_pair_only(run):
    steps = [_step("s1"), _step("s2"), _step("s3")]
    results = [
        _result(1, "s1", observed_at="2026-07-10T10:00:00"),
        _result(2, "s2", observed_at="2026-07-10T10:00:05"),
        _result(3, "s3", observed_at="2026-07-10T10:00:40"),  # far from both
    ]
    meta = {"correlation_window_seconds": 10}
    graph = build_causality_graph(run, results, steps, scenario_meta=meta)
    pairs = {frozenset((e["source"], e["target"])) for e in _temporal(graph)}
    # Only the 1↔2 pair is within window; 40s-out node joins nothing.
    assert pairs == {frozenset(("alert:1", "alert:2"))}


# ---------------------------------------------------------------------------
# Non-duplication + detected-only gating
# ---------------------------------------------------------------------------


def test_temporal_skips_pair_already_joined_by_shared_entity(run):
    steps = [_step("s1"), _step("s2")]
    results = [
        _result(1, "s1", plane="EDR", observed_at="2026-07-10T10:00:00",
                account="svc-app"),
        _result(2, "s2", plane="ITDR", signal_type="Analytics",
                observed_at="2026-07-10T10:00:04", account="svc-app"),
    ]
    meta = {"correlation_window_seconds": 10}
    graph = build_causality_graph(run, results, steps, scenario_meta=meta)
    shared = [e for e in graph["edges"] if e["kind"] == EDGE_SHARED_ENTITY]
    assert len(shared) == 1  # the stronger edge is drawn
    # ...and temporal does not duplicate the same node pair.
    assert _temporal(graph) == []


def test_temporal_ignores_undetected_nodes(run):
    steps = [_step("s1"), _step("s2")]
    results = [
        _result(1, "s1", observed_at="2026-07-10T10:00:00"),   # detected
        _result(2, "s2", observed=False, observed_at=None),    # pending, never fired
    ]
    meta = {"correlation_window_seconds": 10}
    graph = build_causality_graph(run, results, steps, scenario_meta=meta)
    assert _temporal(graph) == []


def test_reviewed_unfired_detection_is_not_temporally_correlated(run):
    # observed_at set WITHOUT observed → reviewed-but-unfired = missed, not a
    # catch; it has no firing to correlate temporally.
    steps = [_step("s1"), _step("s2")]
    results = [
        _result(1, "s1", observed_at="2026-07-10T10:00:00"),            # detected
        _result(2, "s2", observed=False, observed_at="2026-07-10T10:00:04"),  # missed
    ]
    meta = {"correlation_window_seconds": 10}
    graph = build_causality_graph(run, results, steps, scenario_meta=meta)
    assert _temporal(graph) == []


# ---------------------------------------------------------------------------
# Projection invariant — temporal edges never touch the delegated summary
# ---------------------------------------------------------------------------


def test_temporal_edges_do_not_alter_coverage_summary(run):
    steps = [_step("s1"), _step("s2")]
    results = [
        _result(1, "s1", observed_at="2026-07-10T10:00:00", mttd_seconds=3),
        _result(2, "s2", observed_at="2026-07-10T10:00:04", mttd_seconds=7),
    ]
    meta = {"correlation_window_seconds": 10}
    graph = build_causality_graph(run, results, steps, scenario_meta=meta)
    story = build_storyline(run, results, steps)
    expected = build_summary(story["entries"])
    for key in ("total", "detected", "pending", "missed", "coverage_pct", "mttd"):
        assert graph["summary"][key] == expected[key]
    # The temporal edge is counted structurally, and shows up in the rollup.
    assert graph["causality_summary"]["edges_by_kind"].get(EDGE_TEMPORAL) == 1
    assert graph["causality_summary"]["edges_by_state"][STATE_CONFIRMED] >= 1

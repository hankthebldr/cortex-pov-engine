"""Graph carries the prevention-efficacy states: SEVERED edges, not_reached
nodes, outcome fields on alert nodes, and the runtime_control bucket rename."""
from __future__ import annotations

from engine.causality_graph import (
    STATE_SEVERED,
    _control_layer,
    build_causality_graph,
)


def _res(rid, sid, **kw):
    row = {
        "id": rid, "run_id": "r", "step_id": sid, "step_name": "", "plane": "EDR",
        "signal_type": "BIOC", "expected_detection": "d", "detection_id": f"det-{rid}",
        "mitre_technique": "T1059", "observed": False, "observed_at": None,
        "mttd_seconds": None, "detection_kind": "bioc", "evidence": [],
    }
    row.update(kw)
    return row


STEPS = [
    {"id": "step-01", "name": "a", "command": "id", "identity": "root"},
    {"id": "step-02", "name": "b", "command": "whoami", "identity": "root"},
    {"id": "step-03", "name": "c", "command": "uname", "identity": "root"},
]


def test_severed_sequence_edge_and_not_reached_nodes():
    run = {"run_id": "r", "scenario_id": "S", "status": "complete", "severed_at_step": "step-02"}
    results = [
        _res(1, "step-01", observed=True, observed_at="2026-01-01T00:00:00"),
        _res(2, "step-02", outcome="prevented", prevention_module="btp", alert_action="BLOCKED"),
        _res(3, "step-03", outcome="not_reached"),
    ]
    g = build_causality_graph(run, results, STEPS)
    seq = {(e["source"], e["target"]): e for e in g["edges"] if e["kind"] == "sequence"}
    assert seq[("proc:r:step-02", "proc:r:step-03")]["state"] == STATE_SEVERED
    assert seq[("proc:r:step-01", "proc:r:step-02")]["state"] != STATE_SEVERED
    nodes = {n["id"]: n for n in g["nodes"]}
    assert nodes["proc:r:step-03"]["status"] == "not_reached"
    assert "status" not in nodes["proc:r:step-02"]
    alert = nodes["alert:2"]
    assert (alert["outcome"], alert["prevention_module"], alert["alert_action"]) == (
        "prevented", "btp", "BLOCKED")
    assert g["summary"]["severed_at_step"] == "step-02"


def test_no_severed_step_leaves_graph_unchanged():
    run = {"run_id": "r", "scenario_id": "S", "status": "complete"}
    g = build_causality_graph(run, [_res(1, "step-01")], STEPS[:1])
    assert g["summary"]["severed_at_step"] is None
    assert all(e["state"] != STATE_SEVERED for e in g["edges"])


def test_control_layer_renamed_runtime_control():
    assert _control_layer({"control_layer": "prevention"}) == "runtime_control"
    assert _control_layer({"control_layer": "runtime_control"}) == "runtime_control"
    assert _control_layer({"control_layer": "exposure"}) == "exposure"
    assert _control_layer({"plane": "EDR"}) == "runtime_control"
    run = {"run_id": "r", "scenario_id": "S", "status": "complete"}
    g = build_causality_graph(run, [_res(1, "step-01")], STEPS[:1])
    assert "runtime_control" in g["causality_summary"]["by_control_layer"]
    assert "prevention" not in g["causality_summary"]["by_control_layer"]

"""Tests for the pure Causality Graph assembler (Detection Proof Layer, 3rd
projection).

The builder is pure (no DB / HTTP), so these tests exercise it entirely over
dict fixtures shaped like ``Result.to_dict`` / ``Run.to_dict`` / ``scenario.steps``.

Covers:
* the graph is a projection: ``summary`` == storyline ``build_summary`` (same
  coverage/MTTD denominator — timeline and graph can never disagree)
* CGO root + per-step process nodes + wrapper node for service-account steps
* process_lineage spine (cgo → wrapper → process) from identity-harness
  resolution; direct identity skips the wrapper
* head-binary parsing skips echo/loop noise and picks the real action
* alert nodes promoted verbatim, each attached to its step node
* sequence backbone over runtime steps
* exposure → exploit → impact edges on a shared asset, with the dual-control
  state machine (EXPECTED → CONFIRMED, half-edge verdict)
* cross-plane endpoint↔network stitch (CONFIRMED in-window, BROKEN out-of-window)
* network_session 5-tuple + shared_entity edges
* causality_summary: chain completeness, broken-stitch list, by_control_layer
* ORM-object inputs via ``.to_dict()`` and empty-input safety
"""

from __future__ import annotations

import pytest

from engine.causality_graph import (
    EDGE_ENDPOINT_NETWORK_STITCH,
    EDGE_EXPLOIT_IMPACT,
    EDGE_EXPOSURE_EXPLOIT,
    EDGE_NETWORK_SESSION,
    EDGE_PROCESS_LINEAGE,
    EDGE_SEQUENCE,
    EDGE_SHARED_ENTITY,
    NODE_ALERT,
    NODE_CGO,
    NODE_EXPOSURE,
    NODE_PROCESS,
    NODE_WRAPPER,
    STATE_BROKEN,
    STATE_CONFIRMED,
    STATE_EXPECTED,
    build_causality_graph,
    head_binary,
    plane_role,
    resolve_identity_mode,
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
    description="expected detection",
    detection_id=None,
    control_layer=None,
    asset_ref=None,
    observed=False,
    observed_at=None,
    mttd_seconds=None,
    mitre_technique="T1059.004",
    **extra,
):
    row = {
        "id": rid,
        "run_id": "run-1",
        "step_id": step_id,
        "step_name": "",
        "plane": plane,
        "signal_type": signal_type,
        "expected_detection": description,
        "detection_id": detection_id or f"det-{rid}",
        "ttp_ref": "TTP-2026-0090",
        "mitre_technique": mitre_technique,
        "observed": observed,
        "observed_at": observed_at,
        "mttd_seconds": mttd_seconds,
        "detection_kind": signal_type.lower(),
        "detection_severity": None,
        "control_layer": control_layer,
        "asset_ref": asset_ref,
        "evidence": [],
    }
    row.update(extra)
    return row


@pytest.fixture
def run():
    return {"run_id": "run-1", "scenario_id": "SIM-MP-007", "status": "complete"}


@pytest.fixture
def dual_control_steps():
    return [
        {"id": "step-01", "name": "STAGE exposure", "mitre_technique": "T1595",
         "command": "echo 'posture finding only — no execution'", "identity": "www-data"},
        {"id": "step-02", "name": "EXPLOIT", "mitre_technique": "T1190",
         "command": "echo 'web rce'; bash -c 'id; whoami'", "identity": "www-data"},
        {"id": "step-03", "name": "IMPACT beacon", "mitre_technique": "T1041",
         "command": "for i in 1 2 3; do curl -s http://c2 -o /dev/null; sleep 1; done",
         "identity": "www-data"},
    ]


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_resolve_identity_mode():
    assert resolve_identity_mode("root") == {"mode": "direct", "wrapper": None}
    assert resolve_identity_mode("") == {"mode": "direct", "wrapper": None}
    assert resolve_identity_mode(None) == {"mode": "direct", "wrapper": None}
    assert resolve_identity_mode("www-data") == {"mode": "runuser", "wrapper": "runuser"}
    # Unknown username → best-effort runuser (matches the harness fallback arm).
    assert resolve_identity_mode("svc-weird")["mode"] == "runuser"


def test_head_binary_skips_noise():
    assert head_binary("echo 'banner'; bash -c 'id; whoami'") == "bash"
    assert head_binary(
        "echo 'x';\nfor i in 1 2 3; do\n  curl -s http://c2 -o /dev/null;\n  sleep 1;\ndone"
    ) == "curl"
    assert head_binary("redis-cli -h 10.0.0.5 -p 6379 KEYS '*'") == "redis-cli"
    assert head_binary(None) is None
    # All-noise falls back to the first token.
    assert head_binary("echo hi") == "echo"


def test_plane_role():
    assert plane_role("EDR") == "endpoint"
    assert plane_role("ndr") == "network"
    assert plane_role("ASM") == "exposure"
    assert plane_role("ANALYTICS") == "correlation"
    assert plane_role("mystery") == "other"


# ---------------------------------------------------------------------------
# Structural graph
# ---------------------------------------------------------------------------


def test_graph_has_cgo_root_and_process_spine(run, dual_control_steps):
    results = [
        _result(1, "step-01", plane="ASM", signal_type="Analytics", control_layer="exposure",
                asset_ref="dmz-web-01"),
        _result(2, "step-02", plane="EDR", signal_type="BIOC", control_layer="prevention",
                asset_ref="dmz-web-01"),
        _result(3, "step-03", plane="NDR", signal_type="XQL", control_layer="prevention",
                asset_ref="dmz-web-01"),
    ]
    graph = build_causality_graph(run, results, dual_control_steps)

    kinds = [n["kind"] for n in graph["nodes"]]
    assert kinds.count(NODE_CGO) == 1
    # step-01 is a pure exposure step → exposure node, not a process node.
    assert kinds.count(NODE_EXPOSURE) == 1
    # step-02 + step-03 are runtime → process nodes.
    assert kinds.count(NODE_PROCESS) == 2
    # www-data runtime steps get a runuser wrapper each.
    assert kinds.count(NODE_WRAPPER) == 2
    assert kinds.count(NODE_ALERT) == 3

    cgo = next(n for n in graph["nodes"] if n["kind"] == NODE_CGO)
    assert cgo["id"] == "cgo:run-1"
    # os_pids are a monotonic per-run counter.
    pids = [n["os_pid"] for n in graph["nodes"] if "os_pid" in n]
    assert pids == sorted(pids)
    assert len(set(pids)) == len(pids)


def test_process_lineage_wrapper_chain(run, dual_control_steps):
    results = [_result(2, "step-02", control_layer="prevention", asset_ref="a")]
    graph = build_causality_graph(run, [results[0]], [dual_control_steps[1]])
    lineage = [e for e in graph["edges"] if e["kind"] == EDGE_PROCESS_LINEAGE]
    # cgo → wrapper, wrapper → process
    assert len(lineage) == 2
    cgo_edge = next(e for e in lineage if e["source"] == "cgo:run-1")
    wrap_id = cgo_edge["target"]
    assert wrap_id.startswith("wrap:")
    assert any(e["source"] == wrap_id and e["target"].startswith("proc:") for e in lineage)


def test_direct_identity_skips_wrapper(run):
    steps = [{"id": "step-01", "name": "root op", "command": "cat /etc/shadow",
              "identity": "root"}]
    results = [_result(1, "step-01", control_layer="prevention")]
    graph = build_causality_graph(run, results, steps)
    assert not any(n["kind"] == NODE_WRAPPER for n in graph["nodes"])
    lineage = [e for e in graph["edges"] if e["kind"] == EDGE_PROCESS_LINEAGE]
    assert len(lineage) == 1
    assert lineage[0]["source"] == "cgo:run-1"
    assert lineage[0]["target"].startswith("proc:")


def test_alert_nodes_promoted_and_attached(run, dual_control_steps):
    results = [
        _result(2, "step-02", control_layer="prevention", asset_ref="a",
                observed=True, observed_at="2026-07-10T10:00:00", mttd_seconds=12),
    ]
    graph = build_causality_graph(run, results, [dual_control_steps[1]])
    alert = next(n for n in graph["nodes"] if n["kind"] == NODE_ALERT)
    assert alert["id"] == "alert:2"
    assert alert["status"] == "detected"
    assert alert["mttd_seconds"] == 12
    # attach edge from the step's process node to the alert.
    attach = [e for e in graph["edges"] if e["kind"] == "detection_attach"]
    assert attach and attach[0]["target"] == "alert:2"
    assert attach[0]["state"] == STATE_CONFIRMED


def test_sequence_backbone(run, dual_control_steps):
    results = [
        _result(2, "step-02", control_layer="prevention", asset_ref="a"),
        _result(3, "step-03", plane="NDR", signal_type="XQL", control_layer="prevention",
                asset_ref="a"),
    ]
    graph = build_causality_graph(run, results, dual_control_steps[1:])
    seq = [e for e in graph["edges"] if e["kind"] == EDGE_SEQUENCE]
    assert len(seq) == 1
    assert seq[0]["source"].startswith("proc:") and seq[0]["target"].startswith("proc:")


# ---------------------------------------------------------------------------
# Projection invariant
# ---------------------------------------------------------------------------


def test_summary_is_delegated_to_storyline(run, dual_control_steps):
    results = [
        _result(1, "step-01", plane="ASM", signal_type="Analytics", control_layer="exposure",
                asset_ref="dmz-web-01", observed=True, observed_at="2026-07-10T10:00:00",
                mttd_seconds=5),
        _result(2, "step-02", control_layer="prevention", asset_ref="dmz-web-01"),
        _result(3, "step-03", plane="NDR", signal_type="XQL", control_layer="prevention",
                asset_ref="dmz-web-01"),
    ]
    graph = build_causality_graph(run, results, dual_control_steps)
    story = build_storyline(run, results, dual_control_steps)
    expected = build_summary(story["entries"])
    # Every coverage/MTTD key from the storyline summary is byte-identical.
    for key in ("total", "detected", "pending", "missed", "coverage_pct", "mttd"):
        assert graph["summary"][key] == expected[key]
    # Graph adds its own structural counts on top.
    assert graph["summary"]["node_count"] == len(graph["nodes"])
    assert graph["summary"]["edge_count"] == len(graph["edges"])
    assert graph["summary"]["critical_path"][0] == "cgo:run-1"


# ---------------------------------------------------------------------------
# exposure → exploit → impact (dual-control)
# ---------------------------------------------------------------------------


def test_exposure_exploit_impact_edges(run, dual_control_steps):
    meta = {
        "stitching_key": "asset_id",
        "correlation_window_seconds": 120,
        "required_planes_in_incident": ["ASM", "EDR", "NDR"],
        "causality": {"asset_ref": "dmz-web-01"},
    }
    results = [
        _result(1, "step-01", plane="ASM", signal_type="Analytics", control_layer="exposure",
                asset_ref="dmz-web-01", observed=True, observed_at="2026-07-10T10:00:00"),
        _result(2, "step-02", control_layer="prevention", asset_ref="dmz-web-01",
                observed=True, observed_at="2026-07-10T10:00:30"),
        _result(3, "step-03", plane="NDR", signal_type="XQL", control_layer="prevention",
                asset_ref="dmz-web-01", observed=True, observed_at="2026-07-10T10:01:00"),
    ]
    graph = build_causality_graph(run, results, dual_control_steps, scenario_meta=meta)
    expo_exploit = [e for e in graph["edges"] if e["kind"] == EDGE_EXPOSURE_EXPLOIT]
    impact = [e for e in graph["edges"] if e["kind"] == EDGE_EXPLOIT_IMPACT]
    assert len(expo_exploit) == 1
    # both exposure finding AND runtime block fired within window → CONFIRMED.
    assert expo_exploit[0]["state"] == STATE_CONFIRMED
    assert expo_exploit[0]["source"].startswith("expo:")
    assert len(impact) == 1

    cs = graph["causality_summary"]
    assert cs["chain_completeness_pct"] == 100.0
    assert cs["by_control_layer"]["exposure"]["detected"] == 1
    assert cs["by_control_layer"]["prevention"]["detected"] == 2
    assert "Dual-control CONFIRMED" in cs["dual_control_verdict"]


def test_exposure_only_half_edge_verdict(run, dual_control_steps):
    meta = {"stitching_key": "asset_id", "correlation_window_seconds": 120,
            "required_planes_in_incident": ["ASM", "EDR"],
            "causality": {"asset_ref": "dmz-web-01"}}
    results = [
        _result(1, "step-01", plane="ASM", signal_type="Analytics", control_layer="exposure",
                asset_ref="dmz-web-01", observed=True, observed_at="2026-07-10T10:00:00"),
        # runtime exploit NOT observed → prevention-only miss.
        _result(2, "step-02", control_layer="prevention", asset_ref="dmz-web-01"),
    ]
    graph = build_causality_graph(run, results, dual_control_steps[:2], scenario_meta=meta)
    expo_exploit = [e for e in graph["edges"] if e["kind"] == EDGE_EXPOSURE_EXPLOIT]
    assert expo_exploit[0]["state"] == STATE_EXPECTED  # exploit end never fired
    assert "Exposure-only gap" in graph["causality_summary"]["dual_control_verdict"]


# ---------------------------------------------------------------------------
# Cross-plane evidence edges
# ---------------------------------------------------------------------------


def test_endpoint_network_stitch_confirmed_in_window(run):
    steps = [
        {"id": "s1", "name": "endpoint", "command": "redis-cli PING", "identity": "www-data"},
        {"id": "s2", "name": "network", "command": "echo net", "identity": "www-data"},
    ]
    results = [
        _result(1, "s1", plane="EDR", signal_type="BIOC", control_layer="prevention",
                observed=True, observed_at="2026-07-10T10:00:00", host="dmz-web-01"),
        _result(2, "s2", plane="NDR", signal_type="XQL", control_layer="prevention",
                observed=True, observed_at="2026-07-10T10:00:10", host="dmz-web-01"),
    ]
    meta = {"stitching_key": "src_host", "correlation_window_seconds": 60}
    graph = build_causality_graph(run, results, steps, scenario_meta=meta)
    stitch = [e for e in graph["edges"] if e["kind"] == EDGE_ENDPOINT_NETWORK_STITCH]
    assert len(stitch) == 1
    assert stitch[0]["state"] == STATE_CONFIRMED
    assert graph["causality_summary"]["stitched_incident"] is True


def test_endpoint_network_stitch_broken_out_of_window(run):
    steps = [
        {"id": "s1", "name": "endpoint", "command": "redis-cli PING", "identity": "www-data"},
        {"id": "s2", "name": "network", "command": "echo net", "identity": "www-data"},
    ]
    results = [
        _result(1, "s1", plane="EDR", signal_type="BIOC", control_layer="prevention",
                observed=True, observed_at="2026-07-10T10:00:00", host="dmz-web-01"),
        _result(2, "s2", plane="NDR", signal_type="XQL", control_layer="prevention",
                observed=True, observed_at="2026-07-10T10:05:00", host="dmz-web-01"),
    ]
    meta = {"stitching_key": "src_host", "correlation_window_seconds": 60}
    graph = build_causality_graph(run, results, steps, scenario_meta=meta)
    stitch = [e for e in graph["edges"] if e["kind"] == EDGE_ENDPOINT_NETWORK_STITCH]
    assert stitch[0]["state"] == STATE_BROKEN
    broken = graph["causality_summary"]["broken_stitches"]
    assert broken and broken[0]["kind"] == EDGE_ENDPOINT_NETWORK_STITCH


def test_network_session_five_tuple(run):
    steps = [
        {"id": "s1", "name": "a", "command": "echo a", "identity": "www-data"},
        {"id": "s2", "name": "b", "command": "echo b", "identity": "www-data"},
    ]
    tup = dict(src_ip="10.0.0.5", src_port="44001", dst_ip="10.0.0.9",
               dst_port="6379", protocol="tcp")
    results = [
        _result(1, "s1", plane="NDR", signal_type="XQL", control_layer="prevention", **tup),
        _result(2, "s2", plane="NDR", signal_type="XQL", control_layer="prevention", **tup),
    ]
    graph = build_causality_graph(run, results, steps)
    sess = [e for e in graph["edges"] if e["kind"] == EDGE_NETWORK_SESSION]
    assert len(sess) == 1


def test_shared_entity_edge(run):
    steps = [
        {"id": "s1", "name": "a", "command": "echo a", "identity": "www-data"},
        {"id": "s2", "name": "b", "command": "echo b", "identity": "www-data"},
    ]
    results = [
        _result(1, "s1", plane="EDR", signal_type="BIOC", control_layer="prevention",
                account="svc-app"),
        _result(2, "s2", plane="ITDR", signal_type="Analytics", control_layer="prevention",
                account="svc-app"),
    ]
    graph = build_causality_graph(run, results, steps)
    shared = [e for e in graph["edges"] if e["kind"] == EDGE_SHARED_ENTITY]
    assert len(shared) == 1
    assert "svc-app" in shared[0]["rationale"]


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------


def test_orm_object_inputs_via_to_dict(run, dual_control_steps):
    class _Row:
        def __init__(self, d):
            self._d = d

        def to_dict(self):
            return self._d

    results = [_Row(_result(2, "step-02", control_layer="prevention", asset_ref="a"))]
    graph = build_causality_graph(run, results, dual_control_steps[1:2])
    assert any(n["kind"] == NODE_ALERT for n in graph["nodes"])
    assert any(n["kind"] == NODE_PROCESS for n in graph["nodes"])


def test_empty_inputs_are_safe():
    graph = build_causality_graph(None, [], [])
    # Only the synthetic CGO root; no edges; zeroed summary.
    assert [n["kind"] for n in graph["nodes"]] == [NODE_CGO]
    assert graph["edges"] == []
    assert graph["summary"]["total"] == 0
    assert graph["summary"]["coverage_pct"] == 0.0
    assert graph["causality_summary"]["chain_completeness_pct"] is None


def test_missing_entity_columns_keep_edges_expected(run):
    # No 5-tuple / host on the rows → no evidence edges, structural spine only.
    steps = [{"id": "s1", "name": "a", "command": "curl http://x", "identity": "www-data"}]
    results = [_result(1, "s1", plane="EDR", signal_type="BIOC", control_layer="prevention")]
    graph = build_causality_graph(run, results, steps)
    assert not any(e["kind"] == EDGE_ENDPOINT_NETWORK_STITCH for e in graph["edges"])
    lineage = [e for e in graph["edges"] if e["kind"] == EDGE_PROCESS_LINEAGE]
    assert lineage  # spine still present


# ---------------------------------------------------------------------------
# Causality contract: cgo_anchor + connected process_lineage spine
# ---------------------------------------------------------------------------


def test_cgo_anchor_labels_root(run):
    steps = [{"id": "step-01", "name": "a", "command": "cat /etc/shadow", "identity": "root"}]
    results = [_result(1, "step-01", control_layer="prevention")]
    meta = {"cgo_anchor": {"image_name": "apache2", "primary_username": "www-data"}}
    graph = build_causality_graph(run, results, steps, scenario_meta=meta)
    cgo = next(n for n in graph["nodes"] if n["kind"] == NODE_CGO)
    assert cgo["image_name"] == "apache2"
    assert cgo["primary_username"] == "www-data"
    assert cgo["label"] == "Causality Group Owner - apache2"


def test_process_lineage_forms_connected_chain(run):
    # Three direct-identity (root → no wrapper) steps wired step-01→02→03 by
    # process_lineage: the spine must be a CHAIN, with only ONE cgo-sourced
    # process_lineage edge (the root), NOT a star off the CGO.
    steps = [
        {"id": "step-01", "name": "s1", "command": "cat /etc/passwd", "identity": "root"},
        {"id": "step-02", "name": "s2", "command": "cat /etc/shadow", "identity": "root",
         "causality": {"parent_step": "step-01", "pivot": "process_lineage"}},
        {"id": "step-03", "name": "s3", "command": "find / -name id_rsa", "identity": "root",
         "causality": {"parent_step": "step-02", "pivot": "process_lineage"}},
    ]
    results = [
        _result(1, "step-01", control_layer="prevention"),
        _result(2, "step-02", control_layer="prevention"),
        _result(3, "step-03", control_layer="prevention"),
    ]
    meta = {"cgo_anchor": {"image_name": "apache2", "primary_username": "www-data"}}
    graph = build_causality_graph(run, results, steps, scenario_meta=meta)

    lineage = [e for e in graph["edges"] if e["kind"] == EDGE_PROCESS_LINEAGE]
    cgo_id = "cgo:run-1"
    p1, p2, p3 = "proc:run-1:step-01", "proc:run-1:step-02", "proc:run-1:step-03"
    pairs = {(e["source"], e["target"]) for e in lineage}
    # exactly one process_lineage edge originates from the CGO (the root step).
    assert sum(1 for e in lineage if e["source"] == cgo_id) == 1
    assert (cgo_id, p1) in pairs
    # the rest is a real parent→child chain, NOT cgo→every-step.
    assert (p1, p2) in pairs
    assert (p2, p3) in pairs
    assert (cgo_id, p2) not in pairs
    assert (cgo_id, p3) not in pairs


def test_non_process_pivot_emits_typed_edge(run):
    # A network_session pivot: the child still hangs off the CGO via
    # process_lineage, PLUS a typed network_session edge parent→child.
    steps = [
        {"id": "step-01", "name": "s1", "command": "curl http://c2", "identity": "root"},
        {"id": "step-02", "name": "s2", "command": "curl http://c2", "identity": "root",
         "causality": {"parent_step": "step-01", "pivot": "network_session"}},
    ]
    results = [
        _result(1, "step-01", plane="NDR", signal_type="XQL", control_layer="prevention"),
        _result(2, "step-02", plane="NDR", signal_type="XQL", control_layer="prevention"),
    ]
    graph = build_causality_graph(run, results, steps)
    cgo_id = "cgo:run-1"
    p1, p2 = "proc:run-1:step-01", "proc:run-1:step-02"
    lineage = {(e["source"], e["target"]) for e in graph["edges"]
               if e["kind"] == EDGE_PROCESS_LINEAGE}
    # child still rooted at CGO (non-process pivot does not re-parent lineage).
    assert (cgo_id, p2) in lineage
    # ...and the declared network_session pivot edge is present parent→child.
    netsess = [e for e in graph["edges"]
               if e["kind"] == EDGE_NETWORK_SESSION and e["source"] == p1 and e["target"] == p2]
    assert len(netsess) == 1
    assert "declared network_session pivot step-01→step-02" in netsess[0]["rationale"]


def test_no_contract_preserves_star_and_default_cgo(run, dual_control_steps):
    # No cgo_anchor + no causality → default CGO label + legacy star: every
    # process node's process_lineage root is the CGO (or its wrapper), never a
    # sibling process node.
    results = [
        _result(2, "step-02", control_layer="prevention", asset_ref="a"),
        _result(3, "step-03", plane="NDR", signal_type="XQL", control_layer="prevention",
                asset_ref="a"),
    ]
    graph = build_causality_graph(run, results, dual_control_steps[1:])
    cgo = next(n for n in graph["nodes"] if n["kind"] == NODE_CGO)
    assert cgo["image_name"] == "cortexsim-agent"
    assert cgo["primary_username"] == "root"
    assert cgo["label"] == "Causality Group Owner"
    # No proc→proc process_lineage edge exists (star, not chain). www-data steps
    # use a runuser wrapper, so lineage roots are cgo or wrap nodes only.
    proc_ids = {n["id"] for n in graph["nodes"] if n["kind"] == NODE_PROCESS}
    for e in graph["edges"]:
        if e["kind"] == EDGE_PROCESS_LINEAGE:
            assert e["source"] not in proc_ids

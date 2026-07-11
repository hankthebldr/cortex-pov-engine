"""
CortexSim Causality Graph — pure assembler for the Detection Proof Layer's
THIRD projection of the seeded-Result denominator.

Where ``detection_storyline`` renders the run as a flat, ordered kill-chain
(timeline face) and ``efficacy_scorecard`` rolls it into an exec one-pager
(scorecard face), this module renders the SAME seeded detections as a typed
**causality DAG** — the Cortex "Causality View" look-alike that proves, per run,
``service-account process → triggered behavioral alert → real MTTD`` and, across
planes, ``exposure Cortex should have caught at rest → runtime exploit only a
BIOC/ABIOC severs``.

The graph is a projection, **never** a new source of counts. Coverage / MTTD are
delegated wholesale to ``detection_storyline.build_summary`` over the exact same
entries the timeline uses, so the timeline, the scorecard and the graph can
never report divergent coverage for one run (the recurring failure mode this
codebase already guards against with shared ``_status`` logic).

Design rules (mirror ``engine.detection_storyline`` / ``engine.efficacy_scorecard``
EXACTLY):

* **Pure.** No DB, no HTTP, no ORM session. Inputs are dicts (``Result.to_dict``
  / ``Run.to_dict`` / ``scenario.steps`` shape) OR ORM objects exposing
  ``.to_dict()`` — coerced through the shared ``_as_dict``. Fully unit-testable
  and directly API-servable.
* **Deterministic.** Node ids are stable (``cgo:{run}``, ``proc:{run}:{step}``,
  ``wrap:{run}:{step}``, ``expo:{run}:{asset}``, ``alert:{result_id}``); os_pids
  are a monotonic per-run counter seeded from a fixed base; edge order follows
  node order. The same inputs always yield the same graph.
* **Offline-first, reconciled-upgraded.** Structural edges (process_lineage,
  sequence, exposure_exploit, exploit_impact) are derived from the ALWAYS-offline
  step spec (identity-harness resolution + command head-binary + scenario order +
  control_layer / asset_ref) as ``EXPECTED``. Cross-plane evidence edges
  (network_session, endpoint_network_stitch, temporal, shared_entity) are
  UPGRADED to ``CONFIRMED`` when the matcher observed both ends inside the
  correlation window, or flagged ``BROKEN`` when both ends fired OUTSIDE the
  window (a demonstrable stitch / enrichment gap worth surfacing to the DC).

Node/edge payloads are JSON-serializable plain dicts so a React ``<svg>`` view
(``ui/src/components/CausalityGraph.jsx``) can lay out a left-rooted DAG:
CGO on the left, per-step process chain, alert chips hanging off nodes colour-
coded by status, exposure findings on a left rail wired by dotted
``exposure_exploit`` edges.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Optional

from engine.detection_storyline import (
    STATUS_DETECTED,
    _as_dict,
    build_storyline,
    build_summary,
)

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

# Node discriminators.
NODE_CGO = "cgo"
NODE_PROCESS = "process"
NODE_WRAPPER = "wrapper"
NODE_ALERT = "alert"
NODE_EXPOSURE = "exposure"

# Edge kinds. The seven CANONICAL typed edges the graph derives, plus two
# structural helpers (``sequence`` = the always-offline kill-chain backbone;
# ``detection_attach`` = wires each promoted alert node onto the process /
# exposure node whose event triggered it so no alert floats).
EDGE_PROCESS_LINEAGE = "process_lineage"
EDGE_NETWORK_SESSION = "network_session"
EDGE_ENDPOINT_NETWORK_STITCH = "endpoint_network_stitch"
EDGE_TEMPORAL = "temporal"
EDGE_SHARED_ENTITY = "shared_entity"
EDGE_EXPOSURE_EXPLOIT = "exposure_exploit"
EDGE_EXPLOIT_IMPACT = "exploit_impact"
EDGE_SEQUENCE = "sequence"
EDGE_DETECTION_ATTACH = "detection_attach"

EDGE_KINDS: tuple[str, ...] = (
    EDGE_PROCESS_LINEAGE,
    EDGE_NETWORK_SESSION,
    EDGE_ENDPOINT_NETWORK_STITCH,
    EDGE_TEMPORAL,
    EDGE_SHARED_ENTITY,
    EDGE_EXPOSURE_EXPLOIT,
    EDGE_EXPLOIT_IMPACT,
    EDGE_SEQUENCE,
    EDGE_DETECTION_ATTACH,
)

# Edge lifecycle. EXPECTED = spec-declared, unconfirmed. CONFIRMED = both ends
# observed within window. BROKEN = both ends observed but OUTSIDE window.
STATE_EXPECTED = "EXPECTED"
STATE_CONFIRMED = "CONFIRMED"
STATE_BROKEN = "BROKEN"

# Plane → causality lane / role. Endpoint planes carry the CGO process spine;
# network is the far end of the hero cross-plane stitch; exposure planes emit
# posture findings on assets (left rail, no runtime execution).
_ENDPOINT_PLANES = frozenset({"edr", "cdr", "koi"})
_NETWORK_PLANES = frozenset({"ndr"})
_IDENTITY_PLANES = frozenset({"itdr"})
_CORRELATION_PLANES = frozenset({"analytics"})
_EXPOSURE_PLANES = frozenset({"asm", "cspm", "ai_spm"})

# Command head-binary parsing skips pure logging / control noise so the process
# node reads as the meaningful action, not the scenario's ``echo`` banner.
_HEAD_SKIP = frozenset({
    "echo", "printf", "log", "true", "false", "sleep", ":", "cd", "export",
})
_SHELL_KEYWORDS = frozenset({
    "for", "while", "do", "done", "if", "then", "fi", "else", "elif", "case",
    "esac", "function", "in",
})

# Fallback stitch window (seconds) used ONLY to render EXPECTED/CONFIRMED — a
# ``BROKEN`` verdict is never emitted unless the scenario explicitly declares a
# ``correlation_window_seconds`` (so we never fabricate a stitch failure).
_DEFAULT_STITCH_WINDOW = 120

_PID_BASE = 1000


# ---------------------------------------------------------------------------
# Small pure helpers
# ---------------------------------------------------------------------------


def _lower(v: Any) -> str:
    return (v or "").strip().lower() if isinstance(v, str) else ""


def plane_role(plane: Optional[str]) -> str:
    """Classify a plane into a causality lane role."""
    p = _lower(plane)
    if p in _ENDPOINT_PLANES:
        return "endpoint"
    if p in _NETWORK_PLANES:
        return "network"
    if p in _IDENTITY_PLANES:
        return "identity"
    if p in _EXPOSURE_PLANES:
        return "exposure"
    if p in _CORRELATION_PLANES:
        return "correlation"
    return "other"


def resolve_identity_mode(identity: Optional[str]) -> dict[str, Optional[str]]:
    """Resolve a step ``identity`` username to its execution wrapper.

    Mirrors ``push_generator``'s ``run_as`` case / the Go beacon's
    ``ResolveIdentity`` off the ONE canonical spec (``spec/identity_harness.json``
    via ``engine.identity_spec``) so the modeled process-lineage chain is
    identical to what XDR actually observes on the endpoint:

      * direct identities (``root`` / ``container-runtime`` / ``direct``) and an
        empty identity run with NO wrapper (``mode='direct'``, ``wrapper=None``).
      * service accounts (``www-data`` / ``postgres`` / …) AND unknown usernames
        resolve through ``runuser`` (the cleanest causality wrapper, matching the
        harness's preferred arm).
    """
    from engine.identity_spec import direct_identities  # noqa: PLC0415

    name = (identity or "").strip()
    if not name or name in set(direct_identities()):
        return {"mode": "direct", "wrapper": None}
    # Service accounts + unknown usernames both resolve to runuser (the harness's
    # preferred arm and the unknown-identity best-effort path).
    return {"mode": "runuser", "wrapper": "runuser"}


def head_binary(command: Optional[str]) -> Optional[str]:
    """First MEANINGFUL executable token of a step command.

    Splits the command into statements (on newlines / ``;`` / ``&&`` / ``||``),
    skips logging + shell-control noise, and returns the first real binary —
    e.g. ``curl`` from a beacon loop, ``bash`` from a spawned shell — falling
    back to the very first token when everything is noise. Deterministic and
    quote-naive (good enough for a modeled node label; the full command line is
    carried separately on the node).
    """
    if not command:
        return None
    statements = re.split(r"[\n;]|&&|\|\|", command)
    fallback: Optional[str] = None
    for stmt in statements:
        stmt = stmt.strip()
        if not stmt or stmt.startswith("#"):
            continue
        tokens = stmt.split()
        if not tokens:
            continue
        head = tokens[0]
        # Peel leading VAR=val env assignments.
        idx = 0
        while idx < len(tokens) and "=" in tokens[idx] and not tokens[idx].startswith("-"):
            idx += 1
        head = tokens[idx] if idx < len(tokens) else head
        if head in _SHELL_KEYWORDS:
            # A ``for``/``while`` loop header carries only loop metadata (the loop
            # variable + ``in`` + the value list); the real binary lives in the
            # ``do`` body. Skip to just past ``do`` (when present in this same
            # statement), else drop the whole header statement.
            rest: list[str] = []
            if head in ("for", "while", "until") and "do" in tokens[idx:]:
                rest = tokens[tokens.index("do", idx) + 1:]
            elif head in ("do", "then", "else"):
                rest = tokens[idx + 1:]
            for tok in rest:
                if tok in _SHELL_KEYWORDS or "=" in tok:
                    continue
                if fallback is None:
                    fallback = tok
                if tok not in _HEAD_SKIP:
                    return tok
            continue
        if fallback is None:
            fallback = head
        if head not in _HEAD_SKIP:
            return head
    return fallback


def _parse_ts(value: Any) -> Optional[datetime]:
    """Best-effort ISO-8601 → naive-comparable datetime; ``None`` on failure."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.replace(tzinfo=None)


def _control_layer(result: dict[str, Any]) -> str:
    """``exposure`` | ``prevention`` for one seeded detection.

    Prefers the scenario-declared ``control_layer`` (persisted onto the Result by
    the loader), falling back to the plane role so legacy rows still classify:
    exposure planes (ASM / CSPM / AI-SPM) are posture / at-rest controls, every
    other plane is a runtime prevention control.
    """
    cl = _lower(result.get("control_layer"))
    if cl in ("exposure", "prevention"):
        return cl
    return "exposure" if plane_role(result.get("plane")) == "exposure" else "prevention"


def _asset_ref(result: dict[str, Any], observation: Optional[dict[str, Any]]) -> Optional[str]:
    obs = observation or {}
    return (
        result.get("asset_ref")
        or obs.get("asset_ref")
        or result.get("asset_id")
        or obs.get("host")
        or None
    )


def _entities(result: dict[str, Any], observation: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Extract the join entities used to derive network / shared-entity edges.

    Reads from the reconciled ObservedAlert projection FIRST (real firing
    evidence) then the Result row. Every key is optional — when a column is
    absent the corresponding edge simply stays ``EXPECTED`` rather than raising.
    """
    obs = observation or {}

    def pick(*keys: str) -> Any:
        for src in (obs, result):
            for k in keys:
                v = src.get(k)
                if v not in (None, ""):
                    return v
        return None

    return {
        "host": pick("host", "src_host", "agent_hostname"),
        "src_ip": pick("src_ip", "source_ip", "action_local_ip"),
        "dst_ip": pick("dst_ip", "dest_ip", "action_remote_ip"),
        "src_port": pick("src_port", "source_port"),
        "dst_port": pick("dst_port", "dest_port", "action_remote_port"),
        "protocol": pick("protocol", "proto"),
        "container_id": pick("container_id"),
        "account": pick("account", "actor_effective_user_name"),
    }


def _five_tuple(ent: dict[str, Any]) -> Optional[tuple]:
    keys = ("src_ip", "src_port", "dst_ip", "dst_port", "protocol")
    if all(ent.get(k) not in (None, "") for k in keys):
        return tuple(ent[k] for k in keys)
    return None


def _stitch_value(ent: dict[str, Any], stitching_key: Optional[str]) -> Any:
    """Resolve the scenario's declared stitch key to a concrete entity value."""
    key = _lower(stitching_key)
    if key in ("5-tuple", "five_tuple", "5_tuple"):
        return _five_tuple(ent)
    if key in ("container_id", "container"):
        return ent.get("container_id")
    if key in ("user_principal", "account", "user"):
        return ent.get("account")
    # asset_id / src_host / host / anything else → host is the natural anchor.
    return ent.get("host")


def _edge_state(
    a_obs: bool,
    b_obs: bool,
    a_time: Optional[datetime],
    b_time: Optional[datetime],
    window: Optional[float],
) -> str:
    """The EXPECTED → CONFIRMED | BROKEN verdict for an evidence edge."""
    if not (a_obs and b_obs):
        return STATE_EXPECTED
    if window is not None and a_time is not None and b_time is not None:
        if abs((a_time - b_time).total_seconds()) > window:
            return STATE_BROKEN
    return STATE_CONFIRMED


# ---------------------------------------------------------------------------
# Public builder
# ---------------------------------------------------------------------------


def build_causality_graph(
    run: Any,
    results: list[Any],
    scenario_steps: list[Any],
    *,
    observations: Optional[dict[Any, dict[str, Any]]] = None,
    correlation: Optional[dict[str, Any]] = None,
    scenario_meta: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Assemble the typed causality DAG for one run.

    Parameters
    ----------
    run, results, scenario_steps, observations:
        Identical contract to ``detection_storyline.build_storyline`` — this
        builder calls it internally so coverage / MTTD and per-detection status
        are byte-identical to the timeline and scorecard.
    correlation:
        Optional matcher output (reserved for richer per-edge evidence; the
        current builder derives CONFIRMED/BROKEN straight from the observed
        alert projection so it is usable offline). Tolerated and passed through.
    scenario_meta:
        Optional stitch contract parsed from the scenario:
        ``{stitching_key, correlation_window_seconds, required_planes_in_incident,
        causality:{asset_ref|exposure_exploit:[...]}}``. Absent for scenarios
        that declare no causality block — the graph still renders the offline
        lineage / sequence spine.

    Returns
    -------
    dict
        ``{run_id, scenario_id, run_status, nodes[], edges[], summary,
        causality_summary}`` — fully JSON-serializable.
    """
    meta = scenario_meta or {}
    obs_map = observations or {}

    # 1. Delegate to the storyline builder — SINGLE source of coverage/MTTD +
    #    per-detection status. Its entries become our alert nodes verbatim.
    storyline = build_storyline(run, results, scenario_steps, observations=obs_map)
    entries = storyline["entries"]
    run_id = storyline["run_id"]
    scenario_id = storyline["scenario_id"]
    run_status = storyline["run_status"]

    # 2. Index the raw result dicts (for control_layer / asset_ref / 5-tuple
    #    entity columns the storyline entry does not carry through).
    results_by_id: dict[Any, dict[str, Any]] = {}
    for r in results or []:
        rd = _as_dict(r)
        results_by_id[rd.get("id")] = rd

    window = meta.get("correlation_window_seconds")
    if window is None and meta:
        window = _DEFAULT_STITCH_WINDOW if meta.get("stitching_key") else None
    stitching_key = meta.get("stitching_key")

    cgo_id = f"cgo:{run_id}"
    causality_id = cgo_id
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    pid_counter = _PID_BASE

    # --- CGO root -----------------------------------------------------------
    nodes.append({
        "id": cgo_id,
        "kind": NODE_CGO,
        "label": "Causality Group Owner",
        "image_name": "cortexsim-agent",
        "primary_username": "root",
        "os_pid": pid_counter,
        "causality_id": causality_id,
        "step_index": -1,
    })
    pid_counter += 1

    # --- Per-step process / exposure + wrapper nodes ------------------------
    # Group entries by step (preserving storyline order) and decide, per step,
    # whether it is a runtime step (→ process node) or a pure exposure step
    # (→ exposure node on the asset).
    step_order: list[Any] = []
    entries_by_step: dict[Any, list[dict[str, Any]]] = {}
    for e in entries:
        sid = e.get("step_id")
        if sid not in entries_by_step:
            entries_by_step[sid] = []
            step_order.append(sid)
        entries_by_step[sid].append(e)

    step_dicts = {_as_dict(s).get("id"): _as_dict(s) for s in (scenario_steps or [])}

    # Track, per step, the primary structural node + its observed rollup so
    # lineage / sequence / exposure edges can reference them.
    step_node_id: dict[Any, str] = {}
    step_observed: dict[Any, bool] = {}
    step_observed_at: dict[Any, Optional[datetime]] = {}
    step_asset: dict[Any, Optional[str]] = {}
    step_index_of: dict[Any, int] = {}
    exposure_node_by_asset: dict[str, str] = {}
    runtime_steps: list[Any] = []

    for order_idx, sid in enumerate(step_order):
        group = entries_by_step[sid]
        step_spec = step_dicts.get(sid, {})
        idx = group[0].get("step_index", order_idx)
        step_index_of[sid] = idx

        # Observed rollup for the step: detected iff any of its alerts fired;
        # observed_at is the earliest firing among detected alerts.
        detected_times: list[datetime] = []
        control_layers: set[str] = set()
        asset: Optional[str] = None
        for e in group:
            rd = results_by_id.get(e.get("result_id"), {})
            control_layers.add(_control_layer(rd))
            asset = asset or _asset_ref(rd, obs_map.get(e.get("result_id")))
            if e["status"] == STATUS_DETECTED and e.get("observed"):
                ts = _parse_ts(e["observed"].get("observed_at"))
                if ts is not None:
                    detected_times.append(ts)
        step_detected = any(e["status"] == STATUS_DETECTED for e in group)
        step_observed[sid] = step_detected
        step_observed_at[sid] = min(detected_times) if detected_times else None
        step_asset[sid] = asset

        is_exposure_step = control_layers == {"exposure"}

        if is_exposure_step:
            asset_key = asset or f"step:{sid}"
            node_id = exposure_node_by_asset.get(asset_key)
            if node_id is None:
                node_id = f"expo:{run_id}:{asset_key}"
                nodes.append({
                    "id": node_id,
                    "kind": NODE_EXPOSURE,
                    "label": f"Exposure — {asset_key}",
                    "asset_ref": asset,
                    "control_layer": "exposure",
                    "plane": group[0]["expected_detection"].get("plane"),
                    "step_id": sid,
                    "step_index": idx,
                    "observed": step_detected,
                })
                exposure_node_by_asset[asset_key] = node_id
            step_node_id[sid] = node_id
        else:
            command = step_spec.get("command")
            identity = step_spec.get("identity")
            resolved = resolve_identity_mode(identity)
            node_id = f"proc:{run_id}:{sid}"
            binary = head_binary(command) or "process"
            nodes.append({
                "id": node_id,
                "kind": NODE_PROCESS,
                "label": binary,
                "image_name": binary,
                "command_line": command,
                "primary_username": identity,
                "os_pid": pid_counter,
                "causality_id": causality_id,
                "mitre_technique": group[0].get("mitre_technique") or step_spec.get("mitre_technique"),
                "asset_ref": asset,
                "control_layer": "prevention",
                "step_id": sid,
                "step_index": idx,
                "observed": step_detected,
            })
            pid_counter += 1
            step_node_id[sid] = node_id
            runtime_steps.append(sid)

            # Wrapper node + process_lineage spine (cgo → wrapper → process, or
            # cgo → process for a direct identity).
            if resolved["mode"] != "direct" and resolved["wrapper"]:
                wrap_id = f"wrap:{run_id}:{sid}"
                nodes.append({
                    "id": wrap_id,
                    "kind": NODE_WRAPPER,
                    "label": resolved["wrapper"],
                    "image_name": resolved["wrapper"],
                    "primary_username": identity,
                    "os_pid": pid_counter,
                    "causality_id": causality_id,
                    "step_id": sid,
                    "step_index": idx,
                })
                pid_counter += 1
                edges.append(_edge(cgo_id, wrap_id, EDGE_PROCESS_LINEAGE, STATE_EXPECTED,
                                   f"agent shell owns impersonation wrapper '{resolved['wrapper']}'"))
                edges.append(_edge(wrap_id, node_id, EDGE_PROCESS_LINEAGE,
                                   STATE_CONFIRMED if step_detected else STATE_EXPECTED,
                                   f"{resolved['wrapper']} -l {identity} spawns {binary}"))
            else:
                edges.append(_edge(cgo_id, node_id, EDGE_PROCESS_LINEAGE,
                                   STATE_CONFIRMED if step_detected else STATE_EXPECTED,
                                   f"agent shell directly spawns {binary} as {identity or 'root'}"))

    # --- Alert nodes (storyline entries promoted verbatim) + attach edges ---
    for e in entries:
        rid = e.get("result_id")
        alert_id = f"alert:{rid}" if rid is not None else f"alert:{run_id}:{e.get('step_id')}:{len(nodes)}"
        rd = results_by_id.get(rid, {})
        ent = _entities(rd, obs_map.get(rid))
        nodes.append({
            "id": alert_id,
            "kind": NODE_ALERT,
            "label": e["expected_detection"].get("description") or e["expected_detection"].get("detection_id") or "detection",
            "plane": e["expected_detection"].get("plane"),
            "plane_role": plane_role(e["expected_detection"].get("plane")),
            "detection_type": e["expected_detection"].get("type"),
            "detection_id": e["expected_detection"].get("detection_id"),
            "status": e["status"],
            "mttd_seconds": e.get("mttd_seconds"),
            "control_layer": _control_layer(rd),
            "asset_ref": _asset_ref(rd, obs_map.get(rid)),
            "mitre_technique": e.get("mitre_technique"),
            "result_id": rid,
            "step_id": e.get("step_id"),
            "step_index": e.get("step_index"),
            "observed": bool(e.get("observed")),
            "observed_at": e["observed"].get("observed_at") if e.get("observed") else None,
            "entities": ent,
        })
        parent = step_node_id.get(e.get("step_id"))
        if parent:
            edges.append(_edge(parent, alert_id, EDGE_DETECTION_ATTACH,
                               STATE_CONFIRMED if e["status"] == STATUS_DETECTED else STATE_EXPECTED,
                               f"{e['expected_detection'].get('type') or 'detection'} alert fires on this node"))

    # --- Sequence backbone (offline kill-chain) -----------------------------
    for prev, cur in zip(runtime_steps, runtime_steps[1:]):
        src, dst = step_node_id[prev], step_node_id[cur]
        # Sequence is the attack-progression backbone, not a temporal-stitch
        # claim — it is never ``BROKEN`` for a wide inter-step gap (window=None).
        edges.append(_edge(src, dst, EDGE_SEQUENCE,
                           _edge_state(step_observed[prev], step_observed[cur],
                                       None, None, None),
                           f"scenario step order {step_index_of[prev]}→{step_index_of[cur]}"))

    # --- exposure_exploit + exploit_impact ----------------------------------
    _emit_exposure_edges(
        edges, run_id, meta, exposure_node_by_asset, runtime_steps, step_node_id,
        step_asset, step_observed, step_observed_at, step_index_of, window,
    )

    # --- Cross-plane evidence edges over alert nodes ------------------------
    alert_nodes = [n for n in nodes if n["kind"] == NODE_ALERT]
    _emit_evidence_edges(edges, alert_nodes, stitching_key, window)

    # --- Summaries ----------------------------------------------------------
    summary = build_summary(entries)  # delegated — identical to timeline/scorecard
    summary["node_count"] = len(nodes)
    summary["edge_count"] = len(edges)
    summary["critical_path"] = _critical_path(
        cgo_id, exposure_node_by_asset, runtime_steps, step_node_id, step_index_of
    )

    causality_summary = _causality_summary(
        nodes, edges, entries, results_by_id, obs_map, meta,
    )

    return {
        "run_id": run_id,
        "scenario_id": scenario_id,
        "run_status": run_status,
        "nodes": nodes,
        "edges": edges,
        "summary": summary,
        "causality_summary": causality_summary,
    }


# ---------------------------------------------------------------------------
# Edge derivation helpers
# ---------------------------------------------------------------------------


def _edge(source: str, target: str, kind: str, state: str, rationale: str,
          **extra: Any) -> dict[str, Any]:
    edge = {
        "source": source,
        "target": target,
        "kind": kind,
        "state": state,
        "rationale": rationale,
    }
    edge.update(extra)
    return edge


def _emit_exposure_edges(
    edges, run_id, meta, exposure_node_by_asset, runtime_steps, step_node_id,
    step_asset, step_observed, step_observed_at, step_index_of, window,
):
    """exposure→exploit→impact: the code-to-cloud dual-control edges.

    For each asset that carries BOTH an exposure (posture) node and one or more
    runtime process nodes, wire the exposure finding to the EARLIEST exploit on
    that asset (``exposure_exploit``) and the earliest exploit to the LATEST
    runtime node (``exploit_impact``). The exposure_exploit edge reaches
    ``CONFIRMED`` only when BOTH the at-rest exposure finding AND the runtime
    block fired — a half-edge is the demonstrable point-tool blind spot.
    """
    # Map asset → runtime step ids (ordered by step index).
    runtime_by_asset: dict[str, list[Any]] = {}
    for sid in runtime_steps:
        asset = step_asset.get(sid)
        if asset:
            runtime_by_asset.setdefault(asset, []).append(sid)
    for asset in runtime_by_asset:
        runtime_by_asset[asset].sort(key=lambda s: step_index_of.get(s, 0))

    # Declared assets from the causality block widen matching (asset key may not
    # equal the observed host string).
    declared_assets: set[str] = set()
    caus = meta.get("causality") or {}
    if isinstance(caus.get("asset_ref"), str):
        declared_assets.add(caus["asset_ref"])
    for item in caus.get("exposure_exploit") or []:
        if isinstance(item, dict) and item.get("asset_ref"):
            declared_assets.add(item["asset_ref"])
        elif isinstance(item, str):
            declared_assets.add(item)

    for asset_key, expo_id in exposure_node_by_asset.items():
        exploit_steps = runtime_by_asset.get(asset_key)
        if not exploit_steps and declared_assets:
            # Fall back to any runtime asset when the exposure asset key differs
            # from the runtime host but both are declared on the causality block.
            for cand, steps in runtime_by_asset.items():
                if cand in declared_assets or asset_key in declared_assets:
                    exploit_steps = steps
                    break
        if not exploit_steps:
            continue
        exploit_sid = exploit_steps[0]
        exploit_id = step_node_id[exploit_sid]
        # The exposure node's observed flag is carried on step_observed keyed by
        # its own step; recover it via the exposure node id's step is not stored
        # here, so treat exposure as observed if any of its group detected — the
        # node already carries ``observed``. We approximate with the exploit's
        # confirmation AND require the exposure to be present (it always is).
        expo_observed = _exposure_observed(expo_id, edges)
        state = _edge_state(
            expo_observed, step_observed[exploit_sid],
            None, step_observed_at[exploit_sid], window,
        )
        edges.append(_edge(
            expo_id, exploit_id, EDGE_EXPOSURE_EXPLOIT, state,
            f"asset {asset_key}: posture exposure abused by runtime exploit",
            asset_ref=asset_key,
        ))
        # exploit → impact (latest runtime node on the asset, if distinct).
        impact_sid = exploit_steps[-1]
        if impact_sid != exploit_sid:
            impact_id = step_node_id[impact_sid]
            edges.append(_edge(
                exploit_id, impact_id, EDGE_EXPLOIT_IMPACT,
                _edge_state(step_observed[exploit_sid], step_observed[impact_sid],
                            step_observed_at[exploit_sid], step_observed_at[impact_sid], window),
                f"asset {asset_key}: exploit escalates to impact",
                asset_ref=asset_key,
            ))


def _exposure_observed(expo_id: str, edges: list[dict[str, Any]]) -> bool:
    """Whether the exposure node's attached alert fired (via its attach edge)."""
    for e in edges:
        if e["kind"] == EDGE_DETECTION_ATTACH and e["source"] == expo_id:
            if e["state"] == STATE_CONFIRMED:
                return True
    return False


def _emit_evidence_edges(edges, alert_nodes, stitching_key, window):
    """network_session, endpoint_network_stitch, temporal, shared_entity.

    Pairwise over alert nodes in stable order. Each edge's state is derived from
    whether BOTH alerts fired and (when a window is declared) whether their
    firing times fall inside it.
    """
    n = len(alert_nodes)
    for i in range(n):
        a = alert_nodes[i]
        a_ent = a.get("entities") or {}
        a_tuple = _five_tuple(a_ent)
        a_stitch = _stitch_value(a_ent, stitching_key)
        a_obs = a.get("observed", False)
        a_time = _parse_ts(a.get("observed_at"))
        a_role = a.get("plane_role")
        for j in range(i + 1, n):
            b = alert_nodes[j]
            b_ent = b.get("entities") or {}
            b_obs = b.get("observed", False)
            b_time = _parse_ts(b.get("observed_at"))
            b_role = b.get("plane_role")
            state = _edge_state(a_obs, b_obs, a_time, b_time, window)

            # network_session — two detections sharing a full 5-tuple.
            b_tuple = _five_tuple(b_ent)
            if a_tuple is not None and a_tuple == b_tuple:
                edges.append(_edge(a["id"], b["id"], EDGE_NETWORK_SESSION, state,
                                   "shared 5-tuple (src/dst ip+port+proto)"))
                continue

            # endpoint_network_stitch — the hero cross-plane edge: an endpoint
            # node and a network node sharing the scenario's stitch key.
            roles = {a_role, b_role}
            b_stitch = _stitch_value(b_ent, stitching_key)
            if (
                roles == {"endpoint", "network"}
                and a_stitch is not None
                and a_stitch == b_stitch
            ):
                edges.append(_edge(a["id"], b["id"], EDGE_ENDPOINT_NETWORK_STITCH, state,
                                   f"endpoint↔network stitch on {stitching_key or 'host'}={a_stitch}"))
                continue

            # shared_entity — any shared host / container / account value.
            shared = _shared_entity(a_ent, b_ent)
            if shared is not None:
                edges.append(_edge(a["id"], b["id"], EDGE_SHARED_ENTITY, state,
                                   f"shared {shared[0]}={shared[1]}"))
                continue

            # temporal — co-fired within the correlation window (only meaningful
            # once both observed and a window is declared).
            if a_obs and b_obs and window is not None and a_time and b_time:
                if abs((a_time - b_time).total_seconds()) <= window:
                    edges.append(_edge(a["id"], b["id"], EDGE_TEMPORAL, STATE_CONFIRMED,
                                       f"co-fired within {window}s window"))


def _shared_entity(a_ent: dict[str, Any], b_ent: dict[str, Any]) -> Optional[tuple]:
    for key in ("host", "container_id", "account"):
        av, bv = a_ent.get(key), b_ent.get(key)
        if av not in (None, "") and av == bv:
            return (key, av)
    return None


def _critical_path(cgo_id, exposure_node_by_asset, runtime_steps, step_node_id, step_index_of):
    """The kill-chain spine node ids: CGO → exposure findings → runtime steps."""
    path = [cgo_id]
    path.extend(sorted(exposure_node_by_asset.values()))
    path.extend(
        step_node_id[sid]
        for sid in sorted(runtime_steps, key=lambda s: step_index_of.get(s, 0))
    )
    return path


def _causality_summary(nodes, edges, entries, results_by_id, obs_map, meta):
    """Graph-level rollup: edge counts by kind/state, chain completeness,
    dual-control coverage, and the broken-stitch list the DC report flags."""
    edges_by_kind: dict[str, int] = {}
    edges_by_state: dict[str, int] = {STATE_EXPECTED: 0, STATE_CONFIRMED: 0, STATE_BROKEN: 0}
    for e in edges:
        edges_by_kind[e["kind"]] = edges_by_kind.get(e["kind"], 0) + 1
        edges_by_state[e["state"]] = edges_by_state.get(e["state"], 0) + 1

    # A broken STITCH is a cross-plane enrichment gap (the high-value POV
    # evidence) — not a wide gap on the structural spine. Only the evidence edge
    # kinds qualify.
    _stitch_kinds = frozenset({
        EDGE_ENDPOINT_NETWORK_STITCH, EDGE_NETWORK_SESSION, EDGE_TEMPORAL,
        EDGE_SHARED_ENTITY,
    })
    broken_stitches = [
        {"source": e["source"], "target": e["target"], "kind": e["kind"],
         "rationale": e["rationale"]}
        for e in edges
        if e["state"] == STATE_BROKEN and e["kind"] in _stitch_kinds
    ]
    stitched = any(
        e["kind"] == EDGE_ENDPOINT_NETWORK_STITCH and e["state"] == STATE_CONFIRMED
        for e in edges
    )

    # Distinct planes with at least one DETECTED alert (the causality-chain
    # completeness numerator).
    detected_planes: set[str] = set()
    for e in entries:
        if e["status"] == STATUS_DETECTED:
            p = _lower(e["expected_detection"].get("plane"))
            if p:
                detected_planes.add(p)

    required = [_lower(p) for p in (meta.get("required_planes_in_incident") or []) if p]
    if required:
        hit = sum(1 for p in required if p in detected_planes)
        completeness = round(hit / len(required) * 100, 1)
    else:
        completeness = None

    # Dual-control coverage: detected vs expected by control layer.
    dual: dict[str, dict[str, int]] = {
        "exposure": {"detected": 0, "expected": 0},
        "prevention": {"detected": 0, "expected": 0},
    }
    for e in entries:
        rd = results_by_id.get(e.get("result_id"), {})
        cl = _control_layer(rd)
        bucket = dual.setdefault(cl, {"detected": 0, "expected": 0})
        bucket["expected"] += 1
        if e["status"] == STATUS_DETECTED:
            bucket["detected"] += 1

    dual_control_verdict = _dual_control_verdict(dual)

    return {
        "node_count": len(nodes),
        "edge_count": len(edges),
        "edges_by_kind": edges_by_kind,
        "edges_by_state": edges_by_state,
        "planes_detected": sorted(detected_planes),
        "required_planes": required,
        "chain_completeness_pct": completeness,
        "stitched_incident": stitched,
        "broken_stitches": broken_stitches,
        "by_control_layer": dual,
        "dual_control_verdict": dual_control_verdict,
    }


def _dual_control_verdict(dual: dict[str, dict[str, int]]) -> str:
    """The code-to-cloud verdict string for the POV report."""
    expo = dual.get("exposure", {})
    prev = dual.get("prevention", {})
    expo_hit = expo.get("detected", 0) > 0
    prev_hit = prev.get("detected", 0) > 0
    has_expo = expo.get("expected", 0) > 0
    has_prev = prev.get("expected", 0) > 0
    if not (has_expo and has_prev):
        return "Single-control scenario — dual-control verdict not applicable."
    if expo_hit and prev_hit:
        return ("Dual-control CONFIRMED — the platform both shrank the attack "
                "surface at rest AND severed the exploit at runtime.")
    if prev_hit and not expo_hit:
        return ("Prevention-only gap — you blocked it but never shrank the "
                "surface. A point EDR stops today's exploit; the exposure recurs.")
    if expo_hit and not prev_hit:
        return ("Exposure-only gap — you flagged it but could not stop it. A "
                "posture tool names the weakness; nothing severed the exploit.")
    return ("Both controls missed — neither the exposure finding nor the runtime "
            "block fired for this asset.")

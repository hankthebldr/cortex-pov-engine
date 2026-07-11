/**
 * CortexSim — Causality Graph API client.
 *
 * Standalone module (does NOT edit ../api/client.js) that fetches the per-run
 * Causality Graph — the THIRD projection of the seeded-Result denominator, a
 * peer to the linear Detection Storyline. Backed by
 * GET /api/runs/{id}/causality (core/api/causality.py →
 * core/engine/causality_graph.py → build_causality_graph).
 *
 * The graph promotes every storyline entry to an `alert` node verbatim,
 * synthesizes one Causality-Group-Owner (`cgo`) root per run + `process`
 * nodes from identity-harness resolution, and derives the seven typed edge
 * kinds (process_lineage · network_session · endpoint_network_stitch ·
 * temporal · shared_entity · sequence · exposure_exploit) with an
 * EXPECTED → CONFIRMED | BROKEN state machine. Coverage/MTTD are delegated to
 * detection_storyline.build_summary server-side so the timeline, scorecard and
 * graph can never disagree.
 *
 * ── Server contract this client normalizes ────────────────────────────────
 * The endpoint wraps its payload as `{ causality_graph: {...} }` where the
 * inner object is:
 *   {
 *     run_id, scenario_id, run_status,
 *     nodes: [{ id, kind, label, sublabel?, lane?, stage?, status?,
 *               detection_type?, plane?, control_layer?, mttd_seconds?,
 *               detection_id?, result_id?, mitre_technique?, entity? }],
 *     edges: [{ id?, source, target, kind, state, label? }],
 *     summary:           { total, detected, pending, missed, coverage_pct, mttd:{...} },
 *     causality_summary: { nodes, edges, stitched_incidents,
 *                          chain_completeness_pct, broken_stitches[], edges_by_type{} }
 *   }
 *   node.kind  ∈ cgo | process | wrapper | alert | exposure | event
 *   edge.kind  ∈ process_lineage | network_session | endpoint_network_stitch |
 *                temporal | shared_entity | sequence | exposure_exploit
 *   edge.state ∈ expected | confirmed | broken
 *
 * Error shape mirrors storyline.js / client.js: FastAPI returns {detail} where
 * detail may be a plain string OR a structured {error, code, detail} object.
 */

const BASE_URL = typeof window !== 'undefined' ? window.location.origin : ''

// Cortex-native lanes. Endpoint on top, network on the bottom — the two lanes
// whose cross-lane `endpoint_network_stitch` edge is the hero proof. Exposure
// (ASM/CSPM/AI-SPM posture) forms the left rail; correlation is the terminal
// join lane. Identity/cloud sit between so ITDR/cloud_app scenarios still lay
// out coherently.
export const LANE_ORDER = [
  'exposure',
  'endpoint',
  'identity',
  'cloud',
  'network',
  'correlation',
]

// Fallback lane inference from a detection plane when the builder did not tag
// a node with an explicit lane (defensive — explicit `lane` always wins).
const PLANE_LANE = {
  edr: 'endpoint',
  cdr: 'endpoint',
  koi: 'endpoint',
  browser: 'endpoint',
  ndr: 'network',
  itdr: 'identity',
  email: 'identity',
  cloud_app: 'cloud',
  airs: 'cloud',
  ai_access: 'cloud',
  cspm: 'exposure',
  asm: 'exposure',
  ai_spm: 'exposure',
  analytics: 'correlation',
  tim: 'correlation',
}

// Default lane per node kind when neither an explicit lane nor a plane resolves.
const KIND_LANE = {
  cgo: 'endpoint',
  process: 'endpoint',
  wrapper: 'endpoint',
  exposure: 'exposure',
  event: 'endpoint',
  alert: 'endpoint',
}

// The three kill-chain stages the backdrop lays out left→right. control_layer
// `exposure` is always STAGE; correlation terminals land in IMPACT; everything
// else defaults to EXPLOIT unless the builder said otherwise.
function stageFor(n, lane) {
  if (n.stage) return String(n.stage).toLowerCase()
  const cl = (n.control_layer || '').toLowerCase()
  if (cl === 'exposure') return 'stage'
  if (lane === 'exposure') return 'stage'
  if (lane === 'correlation') return 'impact'
  return 'exploit'
}

function laneFor(n) {
  if (n.lane) return String(n.lane).toLowerCase()
  const plane = (n.plane || '').toLowerCase()
  if (PLANE_LANE[plane]) return PLANE_LANE[plane]
  return KIND_LANE[(n.kind || '').toLowerCase()] || 'endpoint'
}

/**
 * Normalize the builder graph into the shape CausalityGraph.jsx lays out.
 * Every derived attribute (lane, stage) falls back gracefully so a sparse
 * offline graph (EXPECTED edges only, no observed entity columns) still
 * renders. Explicit builder fields always win over inference.
 *
 * @param {Object} g inner `causality_graph` object (envelope already stripped)
 * @returns {Object} normalized graph view-model
 */
export function normalizeCausality(g) {
  const graph = g || {}
  const summary = graph.summary || {}
  const cs = graph.causality_summary || {}

  const nodes = (graph.nodes || []).map((n) => {
    const kind = String(n.kind || n.node_type || 'process').toLowerCase()
    const lane = laneFor({ ...n, kind })
    const stage = stageFor(n, lane)
    return {
      id: n.id,
      kind,
      lane,
      stage,
      label: n.label || n.name || n.image_name || n.detection_id || n.id,
      sublabel:
        n.sublabel ||
        n.primary_username ||
        n.command_line ||
        n.detection_id ||
        n.entity ||
        null,
      status: n.status || null,
      detection_type: n.detection_type || null,
      plane: n.plane || null,
      control_layer: n.control_layer || null,
      mttd_seconds: n.mttd_seconds != null ? n.mttd_seconds : null,
      detection_id: n.detection_id || null,
      result_id: n.result_id != null ? n.result_id : null,
      mitre_technique: n.mitre_technique || null,
      entity: n.entity || null,
      // explicit layout override from the builder, if any
      column: n.column != null ? n.column : null,
    }
  })

  const edges = (graph.edges || []).map((e, i) => ({
    id: e.id || `edge-${i}`,
    source: e.source != null ? e.source : e.from,
    target: e.target != null ? e.target : e.to,
    kind: String(e.kind || e.type || 'sequence').toLowerCase(),
    state: String(e.state || e.edge_state || e.status || 'expected').toLowerCase(),
    label: e.label || null,
    evidence: e.evidence || null,
  }))

  return {
    run_id: graph.run_id,
    scenario_id: graph.scenario_id,
    run_status: graph.run_status,
    nodes,
    edges,
    summary: {
      total: summary.total || 0,
      detected: summary.detected || 0,
      pending: summary.pending || 0,
      missed: summary.missed || 0,
      coverage_pct: summary.coverage_pct != null ? summary.coverage_pct : 0,
      mttd: summary.mttd || {},
    },
    causality: {
      nodes: cs.nodes != null ? cs.nodes : nodes.length,
      edges: cs.edges != null ? cs.edges : edges.length,
      stitched_incidents: cs.stitched_incidents || 0,
      chain_completeness_pct:
        cs.chain_completeness_pct != null ? cs.chain_completeness_pct : null,
      broken_stitches: cs.broken_stitches || [],
      edges_by_type: cs.edges_by_type || {},
    },
  }
}

/**
 * GET /api/runs/:runId/causality
 *
 * Fetches the per-run Causality Graph and returns the normalized view-model.
 * The endpoint wraps its payload as `{ causality_graph: ... }`; we unwrap it
 * here so callers read top-level keys (graph.nodes, graph.edges, graph.summary).
 *
 * @param {string} runId
 * @returns {Promise<Object>}
 */
export async function getCausality(runId) {
  const res = await fetch(
    `${BASE_URL}/api/runs/${encodeURIComponent(runId)}/causality`,
    { headers: { Accept: 'application/json' } },
  )

  if (!res.ok) {
    let message = `HTTP ${res.status} — ${res.statusText}`
    try {
      const body = await res.json()
      if (body && body.detail && typeof body.detail === 'object') {
        const d = body.detail
        const code = d.code ? ` [${d.code}]` : ''
        message = `${d.error || d.detail || message}${code}`
      } else if (body) {
        message = body.detail || body.error || body.message || message
      }
    } catch {
      /* body was not JSON — keep the HTTP status message */
    }
    throw new Error(message)
  }

  const body = await res.json()
  return normalizeCausality(body.causality_graph || body)
}

/**
 * URL of the scoped per-run SSE stream. The graph folds the SAME stream the
 * DetectionStoryline does so a reconcile flips an edge EXPECTED→CONFIRMED (or
 * exposes a BROKEN stitch) the instant it lands (falls back to interval
 * polling when SSE is unavailable).
 *
 * @param {string} runId
 * @returns {string}
 */
export function causalityEventsUrl(runId) {
  return `${BASE_URL}/api/runs/${encodeURIComponent(runId)}/events`
}

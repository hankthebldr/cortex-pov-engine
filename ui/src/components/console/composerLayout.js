/**
 * composerLayout.js — geometry for the Composer's two lenses.
 *
 * No React, no fetch, no randomness. Same convention (and the same reason) as
 * `composerDraft.js`, `runStatus.js` and `healthModel.js`: the canvas render and
 * the layout tests must share ONE source of truth for where a node sits and
 * which port an edge leaves from, or a spine that reads "connected" in a test
 * can draw "disconnected" on screen.
 *
 * THE HONESTY RULE
 * ----------------
 * The Run lens carries the REAL causality graph's edge **state**
 * (`EXPECTED | CONFIRMED | BROKEN`) straight through — this module NEVER
 * synthesizes a state and never upgrades a BROKEN stitch to CONFIRMED. A stitch
 * that fired outside the correlation window is BROKEN, and the DC must see it
 * that way, because the alternative is a POV readout that claims a detection
 * the tenant never actually correlated.
 */

/** Frozen layout constants the canvas uses to size the SVG. */
export const LAYOUT = Object.freeze({
  nodeW: 240,
  nodeH: 96,
  gapY: 40,
  gapX: 220,
  padX: 32,
  padY: 32,
  startH: 56,
  endH: 56,
})

// ─── Design lens ──────────────────────────────────────────────────────────────

const START_ID = 'start'
const END_ID = 'end'

/**
 * Derive the causality edge list from a draft's steps.
 *
 * Order-stable and deterministic:
 *  - a step WITH a `causalityParent` yields one edge parent → step, whose `kind`
 *    is that step's `causalityPivot` (defaulting to `process_lineage`);
 *  - a step WITHOUT one roots from the START anchor (`kind: 'root'`);
 *  - the LAST leaf (a step no other step parents) links to the END anchor
 *    (`kind: 'terminal'`), so the chain always has a visible terminus.
 *
 * @param {Array} steps  draft steps (camelCase, from composerDraft)
 * @returns {Array<{id:string, source:string, target:string, kind:string}>}
 */
export function spineEdges(steps) {
  const list = Array.isArray(steps) ? steps : []
  const ids = new Set(list.map((s) => s.id))
  const parented = new Set()
  const edges = []

  for (const s of list) {
    if (s.causalityParent && ids.has(s.causalityParent)) {
      parented.add(s.id)
      edges.push({
        id: `${s.causalityParent}->${s.id}`,
        source: s.causalityParent,
        target: s.id,
        kind: s.causalityPivot || 'process_lineage',
      })
    } else {
      edges.push({ id: `${START_ID}->${s.id}`, source: START_ID, target: s.id, kind: 'root' })
    }
  }

  // A leaf is a step nothing else parents. The LAST leaf (array order) is the
  // chain's terminus and links to END.
  const parents = new Set(list.map((s) => s.causalityParent).filter(Boolean))
  const leaves = list.filter((s) => !parents.has(s.id))
  if (leaves.length) {
    const last = leaves[leaves.length - 1]
    edges.push({ id: `${last.id}->${END_ID}`, source: last.id, target: END_ID, kind: 'terminal' })
  }

  return edges
}

/**
 * Lay a draft out as a vertical spine with START/END anchors.
 *
 * The step array is topologically ordered (the loader forbids forward refs, and
 * `setCausalityParent` refuses to author one), so a simple top-down stack is
 * both deterministic and spine-faithful. Edges carry resolved port coordinates
 * (bottom-centre of source → top-centre of target) so the canvas draws SVG paths
 * with no geometry of its own.
 *
 * @param {Object} draft
 * @param {Object} [opts]
 * @param {'vertical'} [opts.orientation]
 * @param {Object} [opts.spacing]  defaults to LAYOUT
 * @returns {{nodes:Array, edges:Array, bounds:{width:number,height:number}}}
 */
export function layoutChain(draft, { orientation = 'vertical', spacing = LAYOUT } = {}) {
  const L = spacing || LAYOUT
  const steps = (draft && Array.isArray(draft.steps)) ? draft.steps : []
  const x = L.padX
  const nodes = []
  const byId = new Map()

  let y = L.padY
  const startNode = { id: START_ID, kind: 'start', x, y, w: L.nodeW, h: L.startH }
  nodes.push(startNode)
  byId.set(START_ID, startNode)
  y += L.startH + L.gapY

  for (const step of steps) {
    const node = { id: step.id, kind: 'step', x, y, w: L.nodeW, h: L.nodeH, step }
    nodes.push(node)
    byId.set(step.id, node)
    y += L.nodeH + L.gapY
  }

  const endNode = { id: END_ID, kind: 'end', x, y, w: L.nodeW, h: L.endH }
  nodes.push(endNode)
  byId.set(END_ID, endNode)

  const edges = spineEdges(steps).map((e) => {
    const src = byId.get(e.source)
    const dst = byId.get(e.target)
    return {
      ...e,
      from: src ? { x: src.x + src.w / 2, y: src.y + src.h } : null,
      to: dst ? { x: dst.x + dst.w / 2, y: dst.y } : null,
    }
  })

  const bounds = { width: L.padX * 2 + L.nodeW, height: y + L.endH + L.padY }
  return { nodes, edges, bounds, orientation }
}

// ─── Run lens ─────────────────────────────────────────────────────────────────

// Column order for the real causality graph. CGO on the left, the process spine
// in the middle, alerts hanging off to the right — mirrors the builder's own
// "CGO on the left, per-step process chain, alert chips" description.
const _KIND_COLUMN = { cgo: 0, exposure: 1, process: 1, wrapper: 2, alert: 3 }

// The cross-plane stitch edge kinds. A BROKEN edge of one of these kinds is what
// makes a step's run-status BROKEN — a detection-attach or sequence edge is not
// a stitch and does not carry that verdict.
const STITCH_KINDS = new Set([
  'network_session',
  'endpoint_network_stitch',
  'shared_entity',
  'exposure_exploit',
  'exploit_impact',
  'temporal',
])

/**
 * Lay out the REAL `build_causality_graph` dict for the Run lens.
 *
 * Deterministic column-by-kind, row-by-appearance placement. Edge `state` and
 * `kind` (and `rationale`) pass straight through onto the laid-out edges — this
 * function NEVER invents or upgrades a state. Node/edge order is preserved.
 *
 * @param {Object} graph  {nodes:[{id,kind,...}], edges:[{source,target,kind,state,rationale}], ...}
 * @param {Object} [opts]
 * @returns {{nodes:Array, edges:Array, bounds:{width:number,height:number}}}
 */
export function layoutCausalityGraph(graph, { spacing = LAYOUT } = {}) {
  const L = spacing || LAYOUT
  const rawNodes = (graph && Array.isArray(graph.nodes)) ? graph.nodes : []
  const rawEdges = (graph && Array.isArray(graph.edges)) ? graph.edges : []

  const rowByColumn = new Map()
  const byId = new Map()
  const nodes = rawNodes.map((n) => {
    const col = _KIND_COLUMN[n.kind] ?? 4
    const row = rowByColumn.get(col) ?? 0
    rowByColumn.set(col, row + 1)
    const x = L.padX + col * (L.nodeW + L.gapX)
    const y = L.padY + row * (L.nodeH + L.gapY)
    const laid = { id: n.id, kind: n.kind, x, y, w: L.nodeW, h: L.nodeH, node: n }
    byId.set(n.id, laid)
    return laid
  })

  const edges = rawEdges.map((e, i) => {
    const src = byId.get(e.source)
    const dst = byId.get(e.target)
    return {
      id: e.id || `edge-${i}`,
      source: e.source,
      target: e.target,
      kind: e.kind,
      state: e.state, // passed through verbatim — never synthesized
      rationale: e.rationale ?? null,
      from: src ? { x: src.x + src.w, y: src.y + src.h / 2 } : null,
      to: dst ? { x: dst.x, y: dst.y + dst.h / 2 } : null,
    }
  })

  let maxRight = L.padX
  let maxBottom = L.padY
  for (const n of nodes) {
    if (n.x + n.w > maxRight) maxRight = n.x + n.w
    if (n.y + n.h > maxBottom) maxBottom = n.y + n.h
  }
  const bounds = { width: maxRight + L.padX, height: maxBottom + L.padY }
  return { nodes, edges, bounds }
}

/** Pull the step id out of a `proc:{run_id}:{step_id}` process-node id. */
function _stepIdFromProcNode(nodeId) {
  if (typeof nodeId !== 'string') return null
  const parts = nodeId.split(':')
  if (parts.length < 3 || parts[0] !== 'proc') return null
  // run_id never carries the step id's slug; step id is the final segment.
  return parts[parts.length - 1]
}

/**
 * Map each design step to its run status, read from the REAL graph.
 *
 * Matches the process-node id shape `proc:{run_id}:{stepId}`, then inspects the
 * edges touching that node. Verdict precedence is BROKEN → CONFIRMED → EXPECTED,
 * and `hasBrokenStitch` is true when any TOUCHING stitch edge is BROKEN — so a
 * step whose only stitch fired outside the window reports `BROKEN`, never a
 * silently upgraded `CONFIRMED`.
 *
 * @param {Object} graph
 * @returns {{[stepId:string]: {state:string, nodeId:string, hasBrokenStitch:boolean}}}
 */
export function causalityStepStates(graph) {
  const rawNodes = (graph && Array.isArray(graph.nodes)) ? graph.nodes : []
  const rawEdges = (graph && Array.isArray(graph.edges)) ? graph.edges : []
  if (!rawNodes.length) return {}

  const out = {}
  for (const n of rawNodes) {
    if (n.kind !== 'process') continue
    const stepId = _stepIdFromProcNode(n.id)
    if (!stepId) continue

    const touching = rawEdges.filter((e) => e.source === n.id || e.target === n.id)
    const states = new Set(touching.map((e) => e.state))
    const hasBrokenStitch = touching.some(
      (e) => e.state === 'BROKEN' && STITCH_KINDS.has(e.kind),
    )

    let state = 'EXPECTED'
    if (states.has('BROKEN')) state = 'BROKEN'
    else if (states.has('CONFIRMED')) state = 'CONFIRMED'

    out[stepId] = { state, nodeId: n.id, hasBrokenStitch }
  }
  return out
}

// ─── Convenience: the plain spine (task-facing helper) ────────────────────────

/**
 * The minimal spine projection: step nodes with coordinates and the parent→child
 * edge list as `[parentId, childId, pivot]` tuples (START/END anchors excluded).
 * A thin wrapper over `layoutChain`/`spineEdges` so there is ONE geometry source.
 *
 * @param {Array} steps
 * @returns {{nodes:Array<{id,x,y,w,h}>, edges:Array<[string,string,string]>}}
 */
export function layoutSpine(steps) {
  const list = Array.isArray(steps) ? steps : []
  const { nodes } = layoutChain({ steps: list })
  const stepNodes = nodes
    .filter((n) => n.kind === 'step')
    .map(({ id, x, y, w, h }) => ({ id, x, y, w, h }))
  const edges = spineEdges(list)
    .filter((e) => e.source !== START_ID && e.target !== END_ID)
    .map((e) => [e.source, e.target, e.kind])
  return { nodes: stepNodes, edges }
}

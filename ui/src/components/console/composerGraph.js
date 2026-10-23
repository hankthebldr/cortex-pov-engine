/**
 * composerGraph.js — the React Flow node/edge model for Composer MODE.
 *
 * No React, no fetch, no randomness (no `Date.now`/`Math.random`): the full-
 * screen canvas and its tests read node topology from ONE place, so a graph that
 * reads "connected" in a test cannot draw "disconnected" on screen. Same
 * convention as `composerDraft.js` / `composerLayout.js` / `composerSpine.js`.
 *
 * WHAT CHANGED FROM THE WINDOWED LAYOUT (`composerLayout.layoutChain`)
 * -------------------------------------------------------------------
 * In the windowed Composer, START (the CGO anchor) and END (teardown/proof)
 * were plain DOM cards OUTSIDE React Flow, joined by static dashed connectors,
 * and only steps were nodes. The chain visibly broke into three floating
 * blocks, cards overlapped (a 136px stride under 180px cards), and the flow ran
 * top-to-bottom.
 *
 * Composer mode makes the CGO and the Proof terminal FIRST-CLASS React Flow
 * nodes, lays the chain out left-to-right in topological columns (n8n's
 * direction), and spaces cards so they never overlap. This is the honest node
 * set the data model already supports:
 *   - `cgo`   — ONE trigger node, derived from `draft.cgo_anchor`. It OWNS the
 *               chain; every root step descends from it. Not deletable.
 *   - `step`  — one per draft step (`steps[]`).
 *   - `proof` — ONE terminal node, the teardown/proof readout. Not deletable.
 * and the honest edge set:
 *   - `root`      — cgo → each parentless step. DERIVED (the step's absence of a
 *                   `causalityParent` IS the edge); not deletable/selectable.
 *   - `causality` — parent step → child step. The one PERSISTED edge
 *                   (`steps[child].causality {parent_step, pivot}`); carries the
 *                   pivot; deletable and reconnectable.
 *   - `terminal`  — each leaf step → proof. DERIVED; not deletable/selectable.
 *
 * POSITIONS. `draft.layout` ({stepId:{x,y}}) overrides a STEP's computed
 * position — the only positions the backend persists (`composer_layout` prunes
 * every non-step key). The cgo and proof nodes are derived each build from the
 * step columns and are NEVER persisted, so the n8n look needs no new schema
 * field. A draft that has never been dragged lays out deterministically.
 *
 * HONESTY. This module never invents a run state. Run-lens state is read from
 * the REAL causality graph (`causalityStates`, built by `composerLayout
 * .causalityStepStates`) and passed straight onto a step node's data; a missing
 * entry defaults to `EXPECTED`, never a fabricated `CONFIRMED`.
 */

import { effectiveChannel, spineProblems } from './composerDraft.js'
import { laneOf } from './composerLanes.js'

/** Reserved node ids for the two derived anchors. A real step can never take
 *  these (step ids are `step-NN`), so they cannot collide. */
export const CGO_ID = '__cgo__'
export const PROOF_ID = '__proof__'

/** Frozen geometry for the LTR layered layout, in flow-coordinate pixels. */
export const GRAPH_LAYOUT = Object.freeze({
  nodeW: 220,   // card width (the CSS card is sized to match — no overlap)
  colW: 280,    // horizontal pitch between columns (card + a 60px edge run)
  rowH: 140,    // vertical pitch between siblings sharing a column
  padX: 56,
  padY: 48,
  anchorW: 180, // cgo / proof are a touch narrower than a step card
})

/** Snap grid (flow px) for a dropped node. Shared by the visual `snapGrid`
 *  prop and the commit-path rounding in the canvas. */
export const SNAP = 8

// ─── Colour maps (single source for canvas + graph) ──────────────────────────
// CATEGORICAL plane palette: 16 planes need 16 distinguishable hues and there is
// no semantic token for "NDR blue vs ITDR amber". Steel is the fallback. Run
// state and node kind ARE semantic, so they are theme tokens, not fixed hex.
export const PLANE_TINT = {
  EDR: '#00C0E8', CDR: '#12b886', NDR: '#4c6ef5', ITDR: '#f59f00',
  CLOUD_APP: '#7048e8', ANALYTICS: '#e64980', AI_ACCESS: '#ae3ec9',
  AIRS: '#9c36b5', AI_SPM: '#845ef7', BROWSER: '#0ca678', KOI: '#e8590c',
  ASM: '#1098ad', CSPM: '#2f9e44', TIM: '#f76707', EMAIL: '#495057',
  DLP: '#c2255c',
}

export const STATE_TINT = {
  CONFIRMED: 'var(--cortex-success, #2f9e44)',
  BROKEN: 'var(--cortex-danger, #e03131)',
  EXPECTED: 'var(--cortex-steel, #6B7E8E)',
}

/** Tint a step by its first detection's plane, else the draft's plane. */
export function stepPlane(step, draft) {
  return step?.detections?.[0]?.plane || draft?.plane || null
}

// ─── Topology helpers ─────────────────────────────────────────────────────────

/** A step's parent id, but only when it names a step that is actually present —
 *  a dangling parent ref (step deleted out from under it) reads as a root. */
function resolvedParent(step, ids) {
  const p = step?.causalityParent
  return p && ids.has(p) ? p : null
}

/**
 * Longest-path depth of each step from the CGO: a root step is depth 1, its
 * child depth 2, and so on. The CGO is depth 0, the Proof terminal one past the
 * deepest step. Pure and deterministic — ties break by array order via the
 * column stacking below, not here.
 *
 * @returns {{depthById: Map<string,number>, maxDepth: number}}
 */
export function stepDepths(steps) {
  const list = Array.isArray(steps) ? steps : []
  const ids = new Set(list.map((s) => s.id))
  const byId = new Map(list.map((s) => [s.id, s]))
  const depthById = new Map()

  const depthOf = (id, seen) => {
    if (depthById.has(id)) return depthById.get(id)
    // Guard against a cycle (canConnect forbids one at connect time, but a
    // hand-fed array could carry one) — treat a revisit as a root.
    if (seen.has(id)) return 1
    seen.add(id)
    const parent = resolvedParent(byId.get(id), ids)
    const d = parent ? depthOf(parent, seen) + 1 : 1
    depthById.set(id, d)
    return d
  }

  let maxDepth = 0
  for (const s of list) {
    const d = depthOf(s.id, new Set())
    if (d > maxDepth) maxDepth = d
  }
  return { depthById, maxDepth }
}

/**
 * Reparent-on-delete. Removing step `removedId` re-attaches each of its direct
 * children to the removed step's OWN parent (null → the child becomes a root
 * hanging off the CGO), keeping each child's pivot, then drops the step.
 *
 * This is the fix for the long-standing `removeStep` dangling-reference bug:
 * the old filter left children pointing at a parent that no longer exists,
 * which the strict loader rejects. Returns the SAME array reference when
 * `removedId` is not present (the no-op-returns-same-ref contract every op in
 * `composerDraft.js` follows).
 *
 * @param {Array} steps
 * @param {string} removedId
 * @returns {Array}
 */
export function reparentOnDelete(steps, removedId) {
  const list = Array.isArray(steps) ? steps : []
  const removed = list.find((s) => s.id === removedId)
  if (!removed) return steps
  const grandparent = removed.causalityParent || null
  return list
    .filter((s) => s.id !== removedId)
    .map((s) => {
      if (s.causalityParent !== removedId) return s
      return { ...s, causalityParent: grandparent }
    })
}

// ─── Graph build ──────────────────────────────────────────────────────────────

/**
 * Build the React Flow `{ nodes, edges }` for Composer mode.
 *
 * PURE: it attaches NO callbacks — the canvas maps over `nodes` to inject
 * `onSelect`/`onRemove`/… into each node's `data`. Everything else (positions,
 * tints, badges, pivots, run state, selection flag) is computed here so the
 * tests can assert the topology without mounting React Flow.
 *
 * @param {Object} draft              merged draft ({cgo, plane, teardown, layout, …})
 * @param {Array}  steps              draft steps (camelCase)
 * @param {Object} [opts]
 * @param {string} [opts.selectedId]  the selected step id (→ node.selected)
 * @param {boolean}[opts.runLens]     true in the Run lens (badges + read-only)
 * @param {Object} [opts.causalityStates] {stepId:{state}} from the real graph
 * @param {Object} [opts.laneOverrides]   {stepId:lane} for the door badge
 * @param {Object} [opts.layout]      {stepId:{x,y}} stored positions (default draft.layout)
 * @param {Object} [opts.geometry]    defaults to GRAPH_LAYOUT
 * @returns {{nodes:Array, edges:Array, bounds:{width:number,height:number}}}
 */
export function buildGraph(draft = {}, steps = [], opts = {}) {
  const {
    selectedId = null,
    runLens = false,
    causalityStates = null,
    laneOverrides = null,
    geometry = GRAPH_LAYOUT,
  } = opts
  const layout = opts.layout !== undefined ? opts.layout : (draft.layout || null)
  const G = geometry || GRAPH_LAYOUT
  const list = Array.isArray(steps) ? steps : []
  const ids = new Set(list.map((s) => s.id))

  const { depthById, maxDepth } = stepDepths(list)
  // Steps the strict loader would reject as a second root / dangling parent —
  // flagged on the card and on their trigger edge (validateDraft names them).
  const { offChain, danglingParents } = spineProblems(list)
  const flagged = new Set([...offChain, ...danglingParents])

  // Column stacking: index each step within its depth column, in array order.
  const rowInColumn = new Map() // depth → next row index
  const computedPos = new Map() // stepId → {x,y}
  const colCount = new Map()    // depth → count (for centring the anchors)
  for (const s of list) {
    const d = depthById.get(s.id) || 1
    const row = rowInColumn.get(d) ?? 0
    rowInColumn.set(d, row + 1)
    colCount.set(d, (colCount.get(d) || 0) + 1)
    computedPos.set(s.id, {
      x: G.padX + d * G.colW,
      y: G.padY + row * G.rowH,
    })
  }

  // A step's effective position: a stored drag overrides the computed slot.
  const posOf = (s) => {
    const stored = layout && layout[s.id]
    if (stored && typeof stored.x === 'number' && typeof stored.y === 'number') {
      return { x: stored.x, y: stored.y }
    }
    return computedPos.get(s.id) || { x: G.padX, y: G.padY }
  }

  // Vertical centre of the first and last step columns, so the cgo/proof
  // anchors sit level with the chain rather than pinned to the top.
  const columnCentreY = (depth) => {
    const rows = colCount.get(depth) || 1
    return G.padY + ((rows - 1) * G.rowH) / 2
  }
  const cgoY = list.length ? columnCentreY(1) : G.padY
  const proofDepth = maxDepth + 1
  const proofY = list.length ? columnCentreY(maxDepth || 1) : G.padY

  const nodes = []

  // CGO trigger node (always present once there is a chain to own).
  nodes.push({
    id: CGO_ID,
    type: 'cgo',
    position: { x: G.padX, y: cgoY },
    deletable: false,
    selectable: false,
    draggable: false,
    data: {
      kind: 'cgo',
      cgo: draft.cgo || null,
      runLens,
    },
  })

  for (const s of list) {
    const i = list.indexOf(s)
    const plane = stepPlane(s, draft)
    const channel = effectiveChannel(s)
    const pos = posOf(s)
    nodes.push({
      id: s.id,
      type: 'step',
      position: pos,
      dragHandle: '.chain-node__grip',
      deletable: !runLens,
      data: {
        kind: 'step',
        step: s,
        order: i + 1,
        selected: selectedId === s.id,
        tint: PLANE_TINT[plane] || 'var(--cortex-steel, #6B7E8E)',
        runState: runLens ? (causalityStates?.[s.id]?.state || 'EXPECTED') : null,
        runLens,
        // Door badge — the ingestion lane this step launches through. Shown on
        // every lens now (the windowed canvas only showed it on the Lanes
        // lens); it is a real, honest property of the step either way.
        door: laneOf(s, laneOverrides, draft.plane),
        channelKind: channel === 'eal' ? 'eal' : (s.target ? 'target' : null),
        offChain: flagged.has(s.id),
      },
    })
  }

  // Proof terminal node.
  nodes.push({
    id: PROOF_ID,
    type: 'proof',
    position: { x: G.padX + proofDepth * G.colW, y: proofY },
    deletable: false,
    selectable: false,
    draggable: false,
    data: {
      kind: 'proof',
      teardown: Array.isArray(draft.teardown) ? draft.teardown : [],
      runLens,
    },
  })

  // ── Edges ──
  const edges = []
  const parents = new Set(list.map((s) => resolvedParent(s, ids)).filter(Boolean))

  for (const s of list) {
    const parent = resolvedParent(s, ids)
    if (parent) {
      // The one PERSISTED edge. Keep `label` + `style.strokeDasharray` on the
      // edge object (not only in data) so the pivotEdges contract holds: a
      // non-process_lineage pivot reads as a distinct, dashed, labelled edge.
      const pivot = s.causalityPivot || 'process_lineage'
      const typed = pivot !== 'process_lineage'
      edges.push({
        id: `${parent}->${s.id}`,
        source: parent,
        target: s.id,
        sourceHandle: 'out',
        targetHandle: 'in',
        type: 'causality',
        deletable: !runLens,
        reconnectable: runLens ? false : 'target',
        label: typed ? pivot : undefined,
        data: { pivot, runLens },
        // Neutral bright stroke for the process spine; the signal ink for a
        // typed (cross-entity) pivot. Never `--ac`: in the console tokens the
        // brand accent is FILL-ONLY (cortex-tokens.css), not a line colour.
        style: {
          stroke: typed ? 'var(--ink-signal, #00C0E8)' : 'var(--tx2, #C7C7C7)',
          strokeWidth: 1.5,
          ...(typed ? { strokeDasharray: '5 4' } : {}),
        },
      })
    } else {
      // Derived cgo → root. The step's lack of a parent IS this edge; it can
      // never be the thing a DC deletes.
      edges.push({
        id: `${CGO_ID}->${s.id}`,
        source: CGO_ID,
        target: s.id,
        sourceHandle: 'out',
        targetHandle: 'in',
        type: 'derived',
        deletable: false,
        selectable: false,
        focusable: false,
        data: { kind: 'root', runLens, offChain: flagged.has(s.id) },
      })
    }
  }

  // Derived leaf → proof for every step nothing else parents.
  for (const s of list) {
    if (parents.has(s.id)) continue
    edges.push({
      id: `${s.id}->${PROOF_ID}`,
      source: s.id,
      target: PROOF_ID,
      sourceHandle: 'out',
      targetHandle: 'in',
      type: 'derived',
      deletable: false,
      selectable: false,
      focusable: false,
      data: { kind: 'terminal', runLens },
    })
  }

  // Bounds — recomputed over every node so a dragged-out node stays reachable.
  let maxRight = G.padX + G.nodeW
  let maxBottom = G.padY + G.rowH
  for (const n of nodes) {
    const w = n.type === 'step' ? G.nodeW : G.anchorW
    if (n.position.x + w > maxRight) maxRight = n.position.x + w
    if (n.position.y + G.rowH > maxBottom) maxBottom = n.position.y + G.rowH
  }
  const bounds = { width: maxRight + G.padX, height: maxBottom + G.padY }

  return { nodes, edges, bounds }
}

/**
 * composerLanes.js — the Composer's swimlane model. Pure; no React.
 *
 * WHAT A LANE IS
 * A lane is a step's SIGNAL PATH: the mechanism that produces its evidence
 * (`composerSignal.signalOf`). ENDPOINT RUNTIME, LOG INJECTION, LIVE TRAFFIC,
 * CLOUD CONTROL PLANE and NO ACTION, plus a LAUNCH band above, the CORTEX
 * ENGINE band (tenant content, never a step) and a PROOF band below. The
 * Composer, the execution timeline and the Runs topology band by these keys,
 * so a step sits in the same lane on the design canvas and in the live view.
 *
 * It used to be derived from the PLANE (NDR → "Broker VM relay", CDR →
 * "Cloud"). The plane names the product that observes the signal, not how the
 * step produces it, and on the 2026-11-10 corpus that put 427 of 796 steps
 * behind a door they are not launched through.
 *
 * ONE `laneOf()` FOR EVERY CONSUMER, and it takes no override. A lane is read
 * from what the step does, so a drag cannot move a step to another lane: the
 * canvas snaps the card back and says why. Change the command, or the channel
 * in the Inspector.
 *
 * Spec: docs/superpowers/specs/2026-11-10-composer-doors-design.md
 */
import { COMPOSER_LANES, LANE_BANDS } from './povdata/corpus.js'
import { signalOf, SIGNAL_PATHS } from './composerSignal.js'
import { LAYOUT } from './composerLayout.js'

/** Lane keys, in band order. */
export const LANE_KEYS = COMPOSER_LANES.map((l) => l[0])

/** Lanes a STEP may occupy: the five signal paths. Not LAUNCH or PROOF (the
 *  chain's own terminals) and not ENG (tenant content). */
export const STEP_LANES = [...SIGNAL_PATHS]

/** The band geometry, keyed. `top`/`h` are flow-space pixels. */
export const BANDS = Object.fromEntries(LANE_BANDS.map((b) => [b.key, b]))

/**
 * Which lane a step sits in: its signal path. A step whose path cannot be read
 * is ENDPOINT (so it stays visible) and its badge says why; see `signalOf`.
 * Takes no override and ignores any further argument.
 *
 * @param {Object} step  Composer step model or corpus/API step
 * @returns {string} a key from STEP_LANES
 */
export function laneOf(step) {
  return signalOf(step).path
}

/**
 * The lane whose band contains a flow-space y, for snapping a dropped node.
 * Clamps to the nearest STEP lane — a node dragged into LAUNCH or PROOF lands
 * in the adjacent real lane rather than becoming a terminal.
 */
export function laneAtY(y) {
  const cy = y + LAYOUT.nodeH / 2
  let best = null
  let bestDist = Infinity
  for (const key of STEP_LANES) {
    const b = BANDS[key]
    const inside = cy >= b.top && cy < b.top + b.h
    if (inside) return key
    const d = Math.min(Math.abs(cy - b.top), Math.abs(cy - (b.top + b.h)))
    if (d < bestDist) { bestDist = d; best = key }
  }
  return best || 'ENDPOINT'
}

// Column geometry: wide enough for the node plus the edge run between them.
export const LANE_COL_X = 40
export const LANE_COL_PITCH = LAYOUT.nodeW + 72

/**
 * Lay the chain out as swimlanes: x by execution order, y by lane band.
 *
 * Returns the same `{ nodes, edges, bounds }` shape as `layoutChain`, so the
 * canvas can swap layouts without re-deriving edges (which come from
 * `spineEdges` in both cases and are drawn by React Flow from node positions).
 * START and END are NOT emitted — the Design lens omits them from React Flow
 * too, and the LAUNCH / PROOF bands say the same thing a terminal node would.
 *
 * @param {Object[]} steps
 * @param {Object} [overrides]
 * @param {string|null} [draftPlane]
 */
export function layoutLanes(steps = []) {
  const nodes = steps.map((step, i) => {
    const signal = signalOf(step)
    const lane = signal.path
    const band = BANDS[lane] || BANDS.ENDPOINT
    return {
      id: step.id,
      kind: 'step',
      lane,
      x: LANE_COL_X + i * LANE_COL_PITCH,
      y: band.nodeY,
      w: LAYOUT.nodeW,
      h: LAYOUT.nodeH,
      step,
      signal,
    }
  })
  const cols = Math.max(1, steps.length)
  const totalH = LANE_BANDS.reduce((a, b) => a + b.h, 0)
  return {
    nodes,
    bounds: {
      width: LANE_COL_X * 2 + cols * LANE_COL_PITCH,
      height: totalH,
    },
    lanes: LANE_BANDS.map((b) => ({
      ...b,
      count: nodes.filter((n) => n.lane === b.key).length,
    })),
  }
}

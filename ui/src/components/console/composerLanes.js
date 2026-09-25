/**
 * composerLanes.js — the Composer's swimlane model. Pure; no React.
 *
 * WHAT A LANE IS
 * A lane is the ingestion DOOR a step launches through — the same five doors
 * the Runs topology bands by (`LANE_CATALOG`), plus a LAUNCH band above and a
 * PROOF band below. Where a node sits on the design canvas therefore means the
 * same thing as where it will appear in the live topology, and the Composer,
 * the execution timeline and the Runs surface stop disagreeing about which
 * door a step is behind.
 *
 * ONE `laneOf()` FOR EVERY CONSUMER.
 * The execution timeline used to carry its own inline heuristic for this, and
 * the canvas was about to grow a second one. Two derivations of "which lane is
 * this step in" is exactly the class of drift this console has been removing
 * everywhere else, so it is one function here and nowhere else.
 *
 * WHAT DRAGGING INTO ANOTHER LANE ACTUALLY CHANGES
 * The design describes the lane as the retargeting control: drag a node into
 * NETWORK and its door badge changes to BVM. The repo's step model has two
 * fields that decide a door — `channel` ('agent' | 'eal') and `plane` — and
 * they map onto lanes unevenly:
 *
 *   DATA STREAMS  ⇄  channel 'eal'      — a real, honest data change
 *   ENDPOINT      ⇄  channel 'agent'    — likewise
 *   CLOUD / NETWORK / ANALYTICS          — derived from plane
 *
 * Silently rewriting a step's PLANE on a drag would be wrong: the plane is
 * what its expected detections were authored against, and a NDR step does not
 * become a CDR step because someone moved a card. So a lane override is stored
 * as its own presentation-tier field on the draft (`laneOverrides`, beside
 * `layout`), the door badge follows it, and ONLY the two channel-backed lanes
 * write through to the step. That is the retargeting the data model actually
 * supports, done honestly; the rest is recorded intent the inspector shows.
 */
import { COMPOSER_LANES, LANE_BANDS } from './povdata/corpus.js'
import { effectiveChannel } from './composerDraft.js'
import { LAYOUT } from './composerLayout.js'

/** Lane keys, in band order. */
export const LANE_KEYS = COMPOSER_LANES.map((l) => l[0])

/** Lanes a STEP may occupy — LAUNCH and PROOF are the chain's own terminals. */
export const STEP_LANES = LANE_KEYS.filter((k) => k !== 'LAUNCH' && k !== 'PROOF')

/** The band geometry, keyed. `top`/`h` are flow-space pixels. */
export const BANDS = Object.fromEntries(LANE_BANDS.map((b) => [b.key, b]))

/** Plane → door, for the lanes that are plane-derived. */
const PLANE_LANE = {
  CDR: 'CC', CSPM: 'CC', CLOUD_APP: 'CC', ASM: 'CC',
  NDR: 'BVM',
  ANALYTICS: 'ENG', TIM: 'ENG',
  EMAIL: 'DC', DLP: 'DC',
}

/**
 * Which lane a step launches through.
 *
 * @param {Object} step
 * @param {Object} [overrides]  `{ [stepId]: laneKey }` from the draft
 * @param {string|null} [draftPlane]  the draft's plane, used when the step has none
 * @returns {string} a key from STEP_LANES
 */
export function laneOf(step, overrides = null, draftPlane = null) {
  if (!step) return 'AGT'
  const o = overrides && overrides[step.id]
  if (o && STEP_LANES.includes(o)) return o
  // Channel is the strongest signal: an eal step IS a data-stream emitter
  // regardless of the plane it targets.
  if (effectiveChannel(step) === 'eal') return 'DC'
  const plane = step.plane || (step.detections && step.detections[0] && step.detections[0].plane) || draftPlane
  return PLANE_LANE[plane] || 'AGT'
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
  return best || 'AGT'
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
export function layoutLanes(steps = [], overrides = null, draftPlane = null) {
  const nodes = steps.map((step, i) => {
    const lane = laneOf(step, overrides, draftPlane)
    const band = BANDS[lane] || BANDS.AGT
    return {
      id: step.id,
      kind: 'step',
      lane,
      x: LANE_COL_X + i * LANE_COL_PITCH,
      y: band.nodeY,
      w: LAYOUT.nodeW,
      h: LAYOUT.nodeH,
      step,
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

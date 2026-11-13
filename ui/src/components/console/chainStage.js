/**
 * chainStage.js — the pure model behind ChainStage (design v26 §4.1).
 *
 * No React, no fetch, no randomness. ChainStage draws the same chain in three
 * places — the Composer (editable), Run detail (live) and a scenario's sheet
 * (preview) — so where a card sits and which edges join it is computed ONCE,
 * here, from the modules that already own those answers:
 *
 *   lanes   `composerLanes.layoutLanes` — a step's lane is its signal path
 *           (`composerSignal.signalOf`), and the band geometry is LANE_BANDS;
 *   edges   `composerGraph.buildGraph` — the declared causality spine, the
 *           trigger into each root and each leaf into the proof;
 *   tints   `composerGraph.PLANE_TINT` by the step's plane.
 *
 * HONESTY
 *   - A chain with no steps is EMPTY. Nothing is drawn in its place; the
 *     prototype's "generic chain from step count" is not ported (§7).
 *   - Steps with no declared parent are NOT joined to one another. Order is
 *     the column; an edge is a causality the scenario declares, never a
 *     sequence this module infers.
 *   - Live state is the caller's. A step it has not reported reads Waiting —
 *     never a detection.
 */
import { layoutLanes, STEP_LANES } from './composerLanes.js'
import { buildGraph, CGO_ID, PROOF_ID, PLANE_TINT, stepPlane } from './composerGraph.js'
import { signalBadge } from './composerSignal.js'
import { COMPOSER_LANES } from './povdata/corpus.js'

/** Lane key → the band label and sub-label ('WIRE' → 'LIVE TRAFFIC'). */
const LANE_LABEL = Object.fromEntries(COMPOSER_LANES.map(([key, label]) => [key, label]))
const LANE_SUB = Object.fromEntries(COMPOSER_LANES.map(([key, , sub]) => [key, sub]))

/** The door badge's title: the path in words, the plugin, where it runs; or
 *  why the path could not be read. One wording for every lens and stage. */
export function doorTitle(sig) {
  if (!sig) return ''
  if (sig.unplaced) return `signal path not read: ${sig.reason}`
  const where = sig.runsOn === 'SIM' ? 'runs in SimCore\'s own process' : 'runs on the beacon'
  const plugin = sig.plugins?.length ? ` · plugin ${sig.plugins.join(', ')}` : ''
  return `${LANE_LABEL[sig.path] || sig.path} · ${LANE_SUB[sig.path] || ''}${plugin} · ${where}`
}

/** §6: the stage scrolls inside its own box and never scales below this. */
export const MIN_STAGE_SCALE = 0.62

/** Horizontal geometry, in stage pixels. The lane label sits in the gutter. */
export const STAGE_GEOMETRY = Object.freeze({
  gutter: 136,   // lane label column
  pitch: 212,    // column pitch: a card plus the edge run to the next
  cardW: 196,
  pad: 24,       // right padding after the last column
  inset: 5,      // a card's top inside its band (LANE_BANDS.nodeY)
})

export const TRIGGER_ID = 'trigger'
export const PROOF_STAGE_ID = 'proof'

/**
 * The six live card states (§4.6). `status` is the StatusIcon tone the pip
 * wears; the word is what the card says. Green is earned only by Detected.
 */
export const LIVE_STATES = Object.freeze({
  detected:    { status: 'up',      word: 'Detected' },
  broken:      { status: 'down',    word: 'Broken' },
  waiting:     { status: 'unknown', word: 'Waiting' },
  not_seen:    { status: 'unknown', word: 'Not seen' },
  ran:         { status: 'off',     word: 'Ran — not read back' },
  did_not_run: { status: 'off',     word: 'Did not run' },
})

const LIVE_ORDER = ['detected', 'broken', 'waiting', 'not_seen', 'ran', 'did_not_run']

/** A step the caller did not report on is waiting for its report. */
export function liveStateOf(states, stepId) {
  const k = states && states[stepId]
  return LIVE_STATES[k] ? k : 'waiting'
}

/** Fit a stage of `stageWidth` into `boxWidth`: never above 1, never below MIN. */
export function fitScale(boxWidth, stageWidth) {
  if (!(stageWidth > 0) || !(boxWidth > 0)) return MIN_STAGE_SCALE
  return clampScale(boxWidth / stageWidth, 1)
}

/** Clamp a requested scale to [MIN_STAGE_SCALE, max], two decimals. */
export function clampScale(scale, max = 2) {
  const s = Number.isFinite(scale) ? scale : 1
  return Math.round(Math.min(max, Math.max(MIN_STAGE_SCALE, s)) * 100) / 100
}

function edgePath(from, to) {
  const dx = Math.max(30, Math.abs(to.x - from.x) / 2)
  return `M ${from.x} ${from.y} C ${from.x + dx} ${from.y} ${to.x - dx} ${to.y} ${to.x} ${to.y}`
}

/**
 * Lay a chain out on the stage.
 *
 * @param {Array}  steps   Composer step model (`composerDraft.normalizeStep`)
 * @param {Object} [opts]
 * @param {boolean}[opts.compact]  draw only LAUNCH, the lanes in use, ENG when
 *                                 there are rules, and PROOF (sheet, Run detail);
 *                                 false draws all eight (the Composer)
 * @param {string} [opts.cgo]      the Causality Group Owner (the trigger's label)
 * @param {string} [opts.plane]    the chain's plane, the tint fallback
 * @param {Array}  [opts.rules]    tenant rules `[{key,label,sub}]` for the ENG band
 * @returns {{empty:boolean, lanes:Array, cards:Array, rules:Array, edges:Array,
 *            width:number, height:number, summary:string}}
 */
export function buildChainStage(steps = [], opts = {}) {
  const list = Array.isArray(steps) ? steps.filter(Boolean) : []
  const { compact = false, cgo = null, plane = null } = opts
  const rules = Array.isArray(opts.rules) ? opts.rules : []
  const G = STAGE_GEOMETRY

  if (!list.length) {
    return { empty: true, lanes: [], cards: [], rules: [], edges: [], width: 0, height: 0, summary: '' }
  }

  const laid = layoutLanes(list)
  const laneOfStep = new Map(laid.nodes.map((n) => [n.id, n]))
  const used = new Set(laid.nodes.map((n) => n.lane))

  // Which bands are drawn, re-stacked top to bottom in lane order.
  const keep = (b) => !compact
    || b.key === 'LAUNCH' || b.key === 'PROOF'
    || (STEP_LANES.includes(b.key) && used.has(b.key))
    || (b.key === 'ENG' && rules.length > 0)
  let y = 0
  const lanes = laid.lanes.filter(keep).map((b) => {
    const band = {
      key: b.key, label: b.label, sub: b.sub, fill: b.fill, h: b.h,
      top: y, count: b.count, terminal: !STEP_LANES.includes(b.key),
    }
    y += b.h
    return band
  })
  const height = y
  const bandBy = new Map(lanes.map((b) => [b.key, b]))
  const place = (laneKey, col) => {
    const b = bandBy.get(laneKey)
    return { x: G.gutter + col * G.pitch, y: b.top + G.inset, h: b.h - G.inset * 2 }
  }

  const n = list.length
  const cards = []
  const trigger = { id: TRIGGER_ID, kind: 'trigger', lane: 'LAUNCH', col: 0, label: cgo || 'cortexsim-agent', ...place('LAUNCH', 0) }
  cards.push(trigger)
  list.forEach((s, i) => {
    const ln = laneOfStep.get(s.id)
    const p = stepPlane(s, { plane })
    cards.push({
      id: s.id,
      kind: 'step',
      step: s,
      order: i + 1,
      lane: ln.lane,
      col: i + 1,
      plane: p,
      tint: PLANE_TINT[p] || null,
      signal: ln.signal,
      badge: signalBadge(ln.signal),
      ...place(ln.lane, i + 1),
    })
  })
  const ruleCards = rules.map((r, k) => ({
    key: r.key, label: r.label || r.key, sub: r.sub || '', lane: 'ENG', col: n + 1 + k,
    ...place('ENG', n + 1 + k),
  }))
  const proofCol = n + 1 + ruleCards.length
  cards.push({ id: PROOF_STAGE_ID, kind: 'proof', lane: 'PROOF', col: proofCol, ...place('PROOF', proofCol) })

  const byId = new Map(cards.map((c) => [c.id, c]))
  const stageId = (id) => (id === CGO_ID ? TRIGGER_ID : id === PROOF_ID ? PROOF_STAGE_ID : id)
  const { edges: graphEdges } = buildGraph({ cgo, plane }, list)
  const edges = graphEdges
    .filter((e) => e.type === 'causality' || e.type === 'derived')
    .map((e) => {
      const source = stageId(e.source)
      const target = stageId(e.target)
      const a = byId.get(source)
      const b = byId.get(target)
      const from = { x: a.x + G.cardW, y: a.y + a.h / 2 }
      const to = { x: b.x, y: b.y + b.h / 2 }
      const kind = e.type === 'causality' ? 'causality' : (e.data?.kind || 'root')
      const pivot = e.type === 'causality' ? (e.data?.pivot || 'process_lineage') : null
      return {
        id: `${source}->${target}`,
        source,
        target,
        kind,
        pivot,
        typed: !!pivot && pivot !== 'process_lineage',
        d: edgePath(from, to),
      }
    })

  const links = edges.filter((e) => e.kind === 'causality').length
  const paths = STEP_LANES.filter((k) => used.has(k)).length
  const summary = `${n} step${n === 1 ? '' : 's'} through ${paths} signal path${paths === 1 ? '' : 's'}`
    + (links ? ` · ${links} causality link${links === 1 ? '' : 's'}` : ' · no declared spine — order is the column')

  return {
    empty: false,
    lanes,
    cards,
    rules: ruleCards,
    edges,
    width: G.gutter + (proofCol + 1) * G.pitch + G.pad - (G.pitch - G.cardW),
    height,
    summary,
  }
}

/** Count live states for the legend, in the design's order, nonzero only. */
export function liveLegend(steps = [], states = {}) {
  const counts = {}
  for (const s of steps) {
    const k = liveStateOf(states, s.id)
    counts[k] = (counts[k] || 0) + 1
  }
  return LIVE_ORDER.filter((k) => counts[k]).map((k) => ({ key: k, count: counts[k], word: LIVE_STATES[k].word }))
}


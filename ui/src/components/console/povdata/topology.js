/**
 * The data-driven Runs topology layout engine.
 *
 * SEED CATALOG — carried over verbatim from the design prototype
 * (`POVengine Console.dc.html`, Claude Design project "Cortex POV Engine UI
 * Redesign"; see docs/design/DESIGN-SYNC.md).
 *
 * These are the authored corpus values the design was drawn against. Where
 * SimCore serves the same shape over the API, the surface prefers the API and
 * falls back to this; where it does not serve it yet, this IS the model, and
 * its shape is what the endpoint should eventually return. Do not "improve"
 * values here to make a screen look better — they are the numbers the design
 * conversation settled on, and several of them (12 components / 4 ready / 6
 * partial / 2 missing, 21 of 34 stream shapes, 6 of 9 datasets readable) are
 * quoted on more than one surface precisely so those surfaces cannot disagree.
 * Every tally is DERIVED from these rows, never restated as a literal.
 */
import { RUN_LOG, RUN_SHAPES, LANE_CATALOG } from './corpus.js'


export function topology(state) {
  const lr = RUN_LOG.find((r) => r.id === state.runOpen) || RUN_LOG[0]
  const run = RUN_SHAPES[lr.shapeKey] || RUN_SHAPES['4ee09860']
  const AC = '#00CC66', EXP = '#4A4A4A', BRK = '#FDAC96'
  const stroke = { confirmed: AC, expected: EXP, broken: BRK }

  // 1. lanes present in this run, ordered so flow reads top-down: a lane
  // that originates the causality sits above the lanes it feeds.
  const firstCol = {}
  run.steps.forEach((s) => {
    firstCol[s.lane] = Math.min(firstCol[s.lane] === undefined ? 99 : firstCol[s.lane], s.col)
  })
  // Ingestion lanes sort by where flow enters them; the Engine is derived
  // signal, so it stays the terminal band however early its first alert fires.
  const rank = (l) => (l[0] === 'ENG' ? 99 : firstCol[l[0]])
  const lanes = LANE_CATALOG
    .filter((l) => run.steps.some((s) => s.lane === l[0]))
    .sort((a, b) => (rank(a) - rank(b))
      || (LANE_CATALOG.indexOf(a) - LANE_CATALOG.indexOf(b)))
  const PAD = 14, LANE_GAP = 6, ROW_H = 46, HEAD = 26
  const laneRows = {}
  lanes.forEach(([key]) => {
    laneRows[key] = Math.max(1, new Set(run.steps.filter((s) => s.lane === key).map((s) => s.row || 0)).size)
  })
  let y = PAD
  const laneBox = {}
  const laneOut = lanes.map(([key, label, sub, tint]) => {
    const h = HEAD + laneRows[key] * ROW_H
    const box = { y, h, top: y + HEAD, tint }
    laneBox[key] = box
    const o = {
      key, label, sub, y, h, ty: y + 15, ty2: y + 25,
      fill: tint.replace('COL', '').length ? tint : tint,
    }
    y += h + LANE_GAP
    return o
  })
  const totalH = y + PAD - LANE_GAP

  // 2. node geometry — column pitch from the widest label in the run
  const COL_X = 176, COL_W = 196, NH = 38
  const nodes = {}
  const nodeOut = run.steps.map((s) => {
    const box = laneBox[s.lane]
    const w = Math.max(118, Math.min(COL_W - 16, s.label.length * 6.6 + 26))
    const x = COL_X + s.col * COL_W
    const ny = box.top + (s.row || 0) * ROW_H + 3
    const n = {
      id: s.id, x, y: ny, w, kind: s.kind, label: s.label,
      stroke: stroke[s.state], tint: box.tint,
      tx: x + 11, ty1: ny + 14, ty2: ny + 29,
    }
    nodes[s.id] = n
    return n
  })

  // 3. edges from declared causality
  const edges = []
  run.steps.forEach((s) => {
    (s.from || []).forEach((pid) => {
      const a = nodes[pid], b = nodes[s.id]
      if (!a || !b) return
      const sameRow = Math.abs(a.y - b.y) < 6
      const st = s.state === 'broken' ? BRK : s.state === 'expected' ? EXP : stroke[s.state]
      const dash = s.state === 'expected' ? '5 4' : 'none'
      const sw = s.state === 'confirmed' || s.state === 'broken' ? 2 : 1.4
      if (sameRow) {
        const f = { x: a.x + a.w, y: a.y + 19 }, t = { x: b.x, y: b.y + 19 }
        const dx = Math.max(24, (t.x - f.x) / 2)
        edges.push({ d: `M ${f.x} ${f.y} C ${f.x + dx} ${f.y} ${t.x - dx} ${t.y} ${t.x} ${t.y}`, stroke: st, dash, sw })
      } else {
        const f = { x: a.x + a.w / 2, y: a.y + (b.y > a.y ? NH : 0) }
        const t = { x: b.x + b.w / 2, y: b.y + (b.y > a.y ? 0 : NH) }
        const dy = Math.max(16, Math.abs(t.y - f.y) / 2) * (b.y > a.y ? 1 : -1)
        edges.push({ d: `M ${f.x} ${f.y} C ${f.x} ${f.y + dy} ${t.x} ${t.y - dy} ${t.x} ${t.y}`, stroke: st, dash, sw })
      }
    })
  })

  // 4. the live head — last confirmed node feeding something unfinished
  const head = run.steps.filter((s) => s.state === 'confirmed').pop()
  const hn = head ? nodes[head.id] : null
  const cols = Math.max(...run.steps.map((s) => s.col)) + 1
  return {
    nodes: nodeOut, edges, lanes: laneOut,
    w: COL_X + cols * COL_W + PAD, h: totalH,
    pulseX: hn ? hn.x + hn.w : -40, pulseY: hn ? hn.y + 19 : -40,
    // The pulse marks a live head, so it follows the run record's state.
    live: lr.state === 'RUNNING',
  }
}

/**
 * composerLanes — the Composer's swimlane model.
 *
 * The rules pinned here are the ones a drift would silently break: `laneOf`
 * is the ONE derivation of "which door is this step behind" (the timeline,
 * the canvas and the Runs topology all ask it), a drag into another lane
 * writes through to the step ONLY for the two channel-backed lanes, and the
 * lane layout keeps execution order on x and door on y so the canvas and the
 * live topology agree about where a step sits.
 */
import { describe, it, expect } from 'vitest'
import {
  BANDS,
  LANE_COL_PITCH,
  LANE_COL_X,
  LANE_KEYS,
  STEP_LANES,
  laneAtY,
  laneOf,
  layoutLanes,
} from '../console/composerLanes.js'
import { LANE_BANDS } from '../console/povdata/corpus.js'
import { LAYOUT } from '../console/composerLayout.js'
import {
  emptyDraft, setStepLane, setStepChannel, draftToApi, draftFromScenario,
} from '../console/composerDraft.js'

const step = (id, over = {}) => ({
  id, name: id, command: '', identity: '', technique: null, platforms: [],
  causalityParent: null, causalityPivot: null, detections: [], channel: null,
  target: null, eal: null, ...over,
})

describe('lane vocabulary', () => {
  it('STEP_LANES is the five ingestion doors, without the LAUNCH / PROOF terminals', () => {
    expect(LANE_KEYS).toEqual(['LAUNCH', 'AGT', 'CC', 'BVM', 'DC', 'ENG', 'PROOF'])
    expect(STEP_LANES).toEqual(['AGT', 'CC', 'BVM', 'DC', 'ENG'])
  })

  it('BANDS is keyed from LANE_BANDS verbatim', () => {
    for (const b of LANE_BANDS) expect(BANDS[b.key]).toBe(b)
  })
})

describe('laneOf — one derivation for every consumer', () => {
  it('an eal step launches through DATA STREAMS regardless of its plane', () => {
    expect(laneOf(step('s', { channel: 'eal', plane: 'NDR' }))).toBe('DC')
  })

  it('plane-derived doors: NDR → NETWORK, CDR/CSPM → CLOUD, ANALYTICS → ENGINE', () => {
    expect(laneOf(step('s', { plane: 'NDR' }))).toBe('BVM')
    expect(laneOf(step('s', { plane: 'CDR' }))).toBe('CC')
    expect(laneOf(step('s', { plane: 'CSPM' }))).toBe('CC')
    expect(laneOf(step('s', { plane: 'ANALYTICS' }))).toBe('ENG')
  })

  it('falls back to the first detection plane, then the draft plane, then ENDPOINT', () => {
    expect(laneOf(step('s', { detections: [{ plane: 'NDR' }] }))).toBe('BVM')
    expect(laneOf(step('s'), null, 'CDR')).toBe('CC')
    expect(laneOf(step('s', { plane: 'EDR' }))).toBe('AGT')
    expect(laneOf(step('s'))).toBe('AGT')
    expect(laneOf(null)).toBe('AGT')
  })

  it('a draft override wins over every plane derivation, but only for a real step lane', () => {
    const s = step('s', { plane: 'NDR' })
    expect(laneOf(s, { s: 'ENG' })).toBe('ENG')
    expect(laneOf(s, { s: 'PROOF' })).toBe('BVM')
    expect(laneOf(s, { s: 'NOPE' })).toBe('BVM')
    expect(laneOf(s, { other: 'ENG' })).toBe('BVM')
  })

  // Review finding: the override was checked BEFORE the channel, so a lane
  // left over from an earlier drag could badge a step behind a door it does
  // not launch through.
  it('an eal step is DATA STREAMS whatever the override says', () => {
    const s = step('s', { channel: 'eal', plane: 'NDR' })
    for (const o of ['AGT', 'CC', 'BVM', 'ENG']) {
      expect(laneOf(s, { s: o })).toBe('DC')
    }
  })

  it('an agent step ignores a DC override — DATA STREAMS is reached by channel only', () => {
    expect(laneOf(step('s', { plane: 'NDR' }), { s: 'DC' })).toBe('BVM')
    expect(laneOf(step('s'), { s: 'DC' })).toBe('AGT')
  })

  it('plane-derived overrides (CC/BVM/ENG/AGT) on an agent step still apply', () => {
    const s = step('s', { plane: 'NDR' })
    expect(laneOf(s, { s: 'CC' })).toBe('CC')
    expect(laneOf(s, { s: 'ENG' })).toBe('ENG')
    expect(laneOf(s, { s: 'AGT' })).toBe('AGT')
    expect(laneOf(step('s', { plane: 'EDR' }), { s: 'BVM' })).toBe('BVM')
  })

  it('a channel changed after a drag moves the badge with it', () => {
    // Drag into DATA STREAMS (writes channel eal), then the inspector sets the
    // channel back to agent without a re-drag: the 'DC' override is now stale.
    const d = setStepLane({ ...emptyDraft(), steps: [step('step-01', { plane: 'NDR' })] }, 'step-01', 'DC')
    const back = setStepChannel(d.steps, 'step-01', 'agent')
    expect(d.laneOverrides).toEqual({ 'step-01': 'DC' })
    expect(laneOf(back[0], d.laneOverrides)).toBe('BVM')
    // And the reverse: an ENG override on a step later switched to eal.
    const eng = setStepLane({ ...emptyDraft(), steps: [step('step-01')] }, 'step-01', 'ENG')
    const toEal = setStepChannel(eng.steps, 'step-01', 'eal')
    expect(laneOf(toEal[0], eng.laneOverrides)).toBe('DC')
  })
})

describe('laneAtY — where a dropped node lands', () => {
  it('snaps by the node centre to the band that contains it', () => {
    for (const key of STEP_LANES) {
      const b = BANDS[key]
      expect(laneAtY(b.nodeY)).toBe(key)
    }
  })

  it('a node dragged into a terminal band lands in the adjacent real lane', () => {
    // LAUNCH sits above ENDPOINT; PROOF sits below ANALYTICS.
    expect(laneAtY(BANDS.LAUNCH.top - LAYOUT.nodeH)).toBe('AGT')
    expect(laneAtY(BANDS.PROOF.top + BANDS.PROOF.h + 40)).toBe('ENG')
  })
})

describe('layoutLanes — x by order, y by door', () => {
  const steps = [
    step('step-01', { plane: 'EDR' }),
    step('step-02', { channel: 'eal' }),
    step('step-03', { plane: 'NDR' }),
  ]

  it('places each step in its lane band at its execution column', () => {
    const { nodes } = layoutLanes(steps)
    expect(nodes.map((n) => n.id)).toEqual(['step-01', 'step-02', 'step-03'])
    expect(nodes.map((n) => n.lane)).toEqual(['AGT', 'DC', 'BVM'])
    nodes.forEach((n, i) => {
      expect(n.x).toBe(LANE_COL_X + i * LANE_COL_PITCH)
      expect(n.y).toBe(BANDS[n.lane].nodeY)
      expect(n.w).toBe(LAYOUT.nodeW)
      expect(n.h).toBe(LAYOUT.nodeH)
      expect(n.kind).toBe('step')
    })
  })

  it('honours overrides and tallies per-lane counts', () => {
    const { nodes, lanes } = layoutLanes(steps, { 'step-01': 'ENG' })
    expect(nodes[0].lane).toBe('ENG')
    const count = Object.fromEntries(lanes.map((l) => [l.key, l.count]))
    expect(count).toEqual({ LAUNCH: 0, AGT: 0, CC: 0, BVM: 1, DC: 1, ENG: 1, PROOF: 0 })
  })

  it('bounds span every band and every column, and never collapse on an empty chain', () => {
    const totalH = LANE_BANDS.reduce((a, b) => a + b.h, 0)
    expect(layoutLanes(steps).bounds).toEqual({
      width: LANE_COL_X * 2 + 3 * LANE_COL_PITCH,
      height: totalH,
    })
    expect(layoutLanes([]).bounds.width).toBe(LANE_COL_X * 2 + LANE_COL_PITCH)
    expect(layoutLanes([]).nodes).toEqual([])
  })
})

describe('setStepLane — what a drag actually changes', () => {
  const draft = () => ({ ...emptyDraft(), steps: [step('step-01', { plane: 'NDR' })] })

  it('DATA STREAMS writes through to channel eal; ENDPOINT writes back to agent', () => {
    const d1 = setStepLane(draft(), 'step-01', 'DC')
    expect(d1.steps[0].channel).toBe('eal')
    expect(d1.laneOverrides).toEqual({ 'step-01': 'DC' })
    expect(laneOf(d1.steps[0], d1.laneOverrides)).toBe('DC')

    const d2 = setStepLane(d1, 'step-01', 'AGT')
    expect(d2.steps[0].channel).toBeNull()
    expect(d2.steps[0].eal).toBeNull()
    expect(d2.laneOverrides).toEqual({ 'step-01': 'AGT' })
  })

  it('a plane-derived lane records intent without rewriting the step plane', () => {
    const d = setStepLane(draft(), 'step-01', 'CC')
    expect(d.steps[0].plane).toBe('NDR')
    expect(d.steps[0].channel).toBeNull()
    expect(d.laneOverrides).toEqual({ 'step-01': 'CC' })
    expect(laneOf(d.steps[0], d.laneOverrides)).toBe('CC')
  })

  it('is a no-op (same reference) for an unknown step or an unchanged lane', () => {
    const d = draft()
    expect(setStepLane(d, 'nope', 'CC')).toBe(d)
    const d2 = setStepLane(d, 'step-01', 'CC')
    expect(setStepLane(d2, 'step-01', 'CC')).toBe(d2)
  })

  it('round-trips through the API body as composer_lanes', () => {
    const d = setStepLane(draft(), 'step-01', 'ENG')
    const body = draftToApi(d)
    expect(body.composer_lanes).toEqual({ 'step-01': 'ENG' })
    expect(draftFromScenario({ steps: [], composer_lanes: body.composer_lanes }).laneOverrides)
      .toEqual({ 'step-01': 'ENG' })
    expect(draftToApi(draft()).composer_lanes).toBeUndefined()
  })
})

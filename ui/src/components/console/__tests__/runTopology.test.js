/**
 * runTopology — the model behind the run Topology tab.
 *
 * Pins: lanes come from `composerLanes.laneOf` and nothing else; a step with
 * no derivable door is unplaced, never defaulted to ENDPOINT; lanes can be
 * rebuilt from Result rows when the scenario is gone; the Engine terminal is
 * a view onto rows, not a second count; a failed run never yields a miss.
 */
import { describe, it, expect } from 'vitest'
import { buildTopologyModel, toLaneStep, doorKnown, isEngineRow } from '../runTopology.js'
import { laneOf, STEP_LANES } from '../composerLanes.js'

const SCENARIO = {
  scenario_id: 'SIM-MP-001',
  plane: 'ANALYTICS',
  steps: [
    { id: 'step-01', name: 'beacon', identity: 'www-data', mitre_technique: 'T1071.001',
      expected_detections: [{ plane: 'EDR', type: 'BIOC', ttp_ref: 'TTP-1' }] },
    { id: 'step-02', name: 'ngfw session', mitre_technique: 'T1071.001',
      expected_detections: [{ plane: 'NDR', type: 'XQL', ttp_ref: 'TTP-1' }] },
    { id: 'step-03', name: 'cloud', expected_detections: [{ plane: 'CDR', type: 'XQL' }] },
    { id: 'step-04', name: 'emit', channel: 'eal', expected_detections: [{ plane: 'EDR', type: 'XQL' }] },
    { id: 'step-05', name: 'stitch', expected_detections: [{ plane: 'ANALYTICS', type: 'Correlation' }] },
    { id: 'step-06', name: 'no detections', expected_detections: [] },
  ],
}

const RESULTS = [
  { id: 1, step_id: 'step-01', plane: 'EDR', signal_type: 'BIOC', outcome: 'detected', observed: true },
  { id: 2, step_id: 'step-02', plane: 'NDR', signal_type: 'XQL', outcome: 'missed', observed: false },
  { id: 3, step_id: 'step-03', plane: 'CDR', signal_type: 'XQL', outcome: null, observed: false },
  { id: 4, step_id: 'step-04', plane: 'EDR', signal_type: 'XQL', outcome: 'not_reached' },
  { id: 5, step_id: 'step-05', plane: 'ANALYTICS', signal_type: 'Correlation', outcome: null, observed: true },
]

const RUN = {
  run_id: 'r1', status: 'complete', tc_verdict: 'fail',
  output: '=== STEP 1/6 · step-01 · T1071.001 · identity=www-data ===\n--- exit_code=0 duration=1.2s ---\n',
  step_outcomes: [
    { step_id: 'step-01', termination: 'exited', rc: 0 },
    { step_id: 'step-02', termination: 'severed', rc: 137 },
  ],
}

describe('toLaneStep / doorKnown', () => {
  it('maps expected_detections onto the Composer name laneOf reads', () => {
    expect(toLaneStep({ id: 'a', expected_detections: [{ plane: 'NDR' }] }).detections).toEqual([{ plane: 'NDR' }])
    expect(toLaneStep({ id: 'a', detections: [{ plane: 'CDR' }] }).detections).toEqual([{ plane: 'CDR' }])
    expect(toLaneStep(null)).toBeNull()
  })
  it('knows a door from channel, plane, a detection plane, or the scenario plane — nothing else', () => {
    expect(doorKnown({ channel: 'eal' })).toBe(true)
    expect(doorKnown({ plane: 'EDR' })).toBe(true)
    expect(doorKnown({ detections: [{ plane: 'NDR' }] })).toBe(true)
    expect(doorKnown({ id: 'x', detections: [] }, 'EDR')).toBe(true)
    expect(doorKnown({ id: 'x', detections: [] })).toBe(false)
  })
})

describe('isEngineRow', () => {
  it('is Correlation / Analytics / ABIOC by type, or ANALYTICS by plane', () => {
    expect(isEngineRow({ signal_type: 'Correlation', plane: 'EDR' })).toBe(true)
    expect(isEngineRow({ signal_type: 'ABIOC' })).toBe(true)
    expect(isEngineRow({ signal_type: 'XQL', plane: 'ANALYTICS' })).toBe(true)
    expect(isEngineRow({ signal_type: 'BIOC', plane: 'EDR' })).toBe(false)
  })
})

describe('buildTopologyModel', () => {
  it('derives every lane through laneOf and nothing else', () => {
    const m = buildTopologyModel({ scenario: SCENARIO, run: RUN, results: RESULTS })
    expect(m.stepsFrom).toBe('scenario')
    const laneById = Object.fromEntries(m.columns.map((c) => [c.id, c.lane]))
    // the oracle IS laneOf — the model may not disagree with it
    for (const s of SCENARIO.steps) {
      expect(laneById[s.id]).toBe(laneOf(toLaneStep(s), null, SCENARIO.plane))
    }
    expect(laneById).toEqual({
      'step-01': 'AGT', 'step-02': 'BVM', 'step-03': 'CC', 'step-04': 'DC', 'step-05': 'ENG',
      // no detections of its own → the scenario's plane (ANALYTICS) → ENG
      'step-06': 'ENG',
    })
    expect(m.lanes.map((l) => l.key)).toEqual(STEP_LANES)
    expect(m.lanes.every((l) => !l.empty)).toBe(true)
    expect(m.unplaced).toEqual([])
  })

  it('flags a lane with no step as empty — and that is not a clean lane', () => {
    const sc = { ...SCENARIO, plane: 'EDR', steps: SCENARIO.steps.slice(0, 1) }
    const m = buildTopologyModel({ scenario: sc, run: RUN, results: RESULTS.slice(0, 1) })
    const by = Object.fromEntries(m.lanes.map((l) => [l.key, l]))
    expect(by.AGT.empty).toBe(false)
    expect(by.AGT.tone).toBe('hit')
    for (const k of ['CC', 'BVM', 'DC', 'ENG']) {
      expect(by[k].empty).toBe(true)
      expect(by[k].tone).toBe('empty')
    }
  })

  it('never defaults an unknowable door into ENDPOINT — it is unplaced with a reason', () => {
    const sc = { scenario_id: 'X', plane: null, steps: [{ id: 'step-01', name: 'mystery', expected_detections: [] }] }
    const m = buildTopologyModel({ scenario: sc, run: { status: 'complete' }, results: [] })
    expect(m.columns).toEqual([])
    expect(m.unplaced).toHaveLength(1)
    expect(m.unplaced[0]).toMatchObject({ id: 'step-01', lane: null, reason: expect.stringMatching(/no channel and no plane/) })
    expect(m.lanes.every((l) => l.empty)).toBe(true)
  })

  it('rebuilds lanes from the Result rows when the scenario is gone', () => {
    const m = buildTopologyModel({ scenario: null, run: RUN, results: RESULTS })
    expect(m.stepsFrom).toBe('run')
    const laneById = Object.fromEntries(m.columns.map((c) => [c.id, c.lane]))
    // step-04 was an EAL step in the scenario, but a Result row only knows its
    // plane (EDR): without the scenario the honest answer is the plane's door.
    expect(laneById).toEqual({ 'step-01': 'AGT', 'step-02': 'BVM', 'step-03': 'CC', 'step-04': 'AGT', 'step-05': 'ENG' })
    // transcript order first, then outcomes, then rows
    expect(m.columns.map((c) => c.id)).toEqual(['step-01', 'step-02', 'step-03', 'step-04', 'step-05'])
  })

  it('joins the host record onto each cell: rc, severed, identity from the transcript', () => {
    const m = buildTopologyModel({ scenario: SCENARIO, run: RUN, results: RESULTS })
    const by = Object.fromEntries(m.columns.map((c) => [c.id, c]))
    expect(by['step-01']).toMatchObject({ status: 'ok', rc: 0, identity: 'www-data', duration: '1.2s' })
    expect(by['step-02']).toMatchObject({ status: 'severed', rc: 137 })
    expect(by['step-03'].status).toBe('not_reached')
  })

  it('tones a cell from its rows; a step with no expectation is none, not pending', () => {
    const m = buildTopologyModel({ scenario: SCENARIO, run: RUN, results: RESULTS })
    const by = Object.fromEntries(m.columns.map((c) => [c.id, c]))
    expect(by['step-01']).toMatchObject({ tone: 'hit', hits: 1, denominator: 1 })
    expect(by['step-02']).toMatchObject({ tone: 'miss', hits: 0, denominator: 1 })
    expect(by['step-03'].tone).toBe('pending')
    expect(by['step-04']).toMatchObject({ tone: 'excluded', denominator: 0 })
    expect(by['step-06']).toMatchObject({ tone: 'none', rows: [] })
    expect(m.lanes.find((l) => l.key === 'BVM').tone).toBe('miss')
  })

  it('on a FAILED run no cell and no lane reads miss', () => {
    const m = buildTopologyModel({ scenario: SCENARIO, run: { ...RUN, status: 'failed' }, results: RESULTS })
    expect(m.proven).toBe(false)
    expect(m.columns.some((c) => c.tone === 'miss')).toBe(false)
    expect(m.lanes.some((l) => l.tone === 'miss')).toBe(false)
    expect(m.engine.tone).not.toBe('miss')
  })

  it('pins the Engine terminal with the correlation-class rows and the run verdict, as a view not a second count', () => {
    const m = buildTopologyModel({ scenario: SCENARIO, run: RUN, results: RESULTS })
    expect(m.engine).toMatchObject({ key: 'ENG', tcVerdict: 'fail', empty: false, hits: 1, denominator: 1 })
    expect(m.engine.rows.map((r) => r.id)).toEqual([5])
    // the same row is counted ONCE in the page totals
    expect(m.totals).toEqual({ steps: 6, rows: 5, hits: 2, denominator: 4 })
    // and the terminal exists even when nothing fires in the engine
    const m2 = buildTopologyModel({ scenario: SCENARIO, run: { status: 'complete' }, results: RESULTS.slice(0, 2) })
    expect(m2.engine).toMatchObject({ empty: true, tcVerdict: null, tone: 'empty' })
  })

  it('lists where the chain crossed between doors, in execution order', () => {
    const m = buildTopologyModel({ scenario: SCENARIO, run: RUN, results: RESULTS })
    expect(m.crossings).toEqual([
      { from: 'AGT', to: 'BVM', at: 'step-02' },
      { from: 'BVM', to: 'CC', at: 'step-03' },
      { from: 'CC', to: 'DC', at: 'step-04' },
      { from: 'DC', to: 'ENG', at: 'step-05' },
    ])
  })

  it('accepts pre-built step rows from the live hook instead of rebuilding them', () => {
    const stepRows = [{ id: 'step-01', index: 1, status: 'running', rc: null, identity: 'nobody' }]
    const sc = { ...SCENARIO, steps: SCENARIO.steps.slice(0, 1) }
    const m = buildTopologyModel({ scenario: sc, run: { status: 'running' }, results: [], stepRows })
    expect(m.columns[0]).toMatchObject({ status: 'running', identity: 'nobody' })
  })

  it('is empty, not wrong, with nothing', () => {
    const m = buildTopologyModel({})
    expect(m.columns).toEqual([])
    expect(m.lanes.every((l) => l.empty)).toBe(true)
    expect(m.engine.empty).toBe(true)
    expect(m.totals).toEqual({ steps: 0, rows: 0, hits: 0, denominator: 0 })
  })
})

describe('buildTopologyModel — push bundles', () => {
  it('reads every cell of a staged push run as staged, and the run as unproven', () => {
    const m = buildTopologyModel({ scenario: SCENARIO, run: { status: 'staged', mode: 'push' }, results: RESULTS })
    expect(m.proven).toBe(false)
    expect(m.columns).toHaveLength(6)
    expect(m.columns.every((c) => c.status === 'staged')).toBe(true)
    expect(m.columns.some((c) => c.tone === 'miss')).toBe(false)
  })
})

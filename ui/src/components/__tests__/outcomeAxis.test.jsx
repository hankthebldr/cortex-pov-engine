/**
 * Final review C1 — every console surface reads the outcome axis.
 *
 * `_seed_results` writes `observed=false` on every row, so a view that maps
 * `observed === false` to "Missed" without reading `outcome` tells a customer
 * Cortex missed a step that never ran (`not_reached`), a step whose tool was
 * absent (`not_executed`) and an Analytics detector still in its activation
 * period — and a block-mode `DETECTED_NOT_PREVENTED` fail reads ✅ Detected.
 * One fixture run, six outcomes, every view.
 */
import React from 'react'
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, waitFor, renderHook, fireEvent } from '@testing-library/react'
import EvidenceView from '../console/EvidenceView.jsx'
import DetectionDrawer from '../console/DetectionDrawer.jsx'
import NarrativeTimeline from '../console/NarrativeTimeline.jsx'
import useTimelineData from '../console/useTimelineData.js'
import MultiRunCompare from '../console/MultiRunCompare.jsx'
import DetectionStoryline from '../DetectionStoryline.jsx'
import { installRoutes } from '../../test/mockFetch.js'
import { isInDenominator } from '../../outcomes.js'

void React

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

// One row per outcome, each on its own step, shaped like Result.to_dict().
const ROW = (id, step, outcome, reason, observed, expected = 'detect', sig = 'BIOC') => ({
  id, step_id: step, mitre_technique: `T100${id}`, plane: 'EDR', signal_type: sig,
  detection_type: sig, expected_detection: `det-${outcome}${reason ? '-' + reason : ''}`,
  expected_outcome: expected, outcome, outcome_reason: reason, observed,
  observed_at: observed ? '2026-10-26T10:00:05' : null, mttd_seconds: observed ? 5 : null,
})
const RESULTS = [
  ROW(1, 'step-01', 'prevented', null, true, 'prevent'),
  ROW(2, 'step-02', 'detected', null, true),
  ROW(3, 'step-03', 'detected', 'DETECTED_NOT_PREVENTED', true, 'prevent'),
  ROW(4, 'step-04', 'missed', null, false),
  ROW(5, 'step-05', 'not_reached', 'UPSTREAM_SEVERED', false),
  ROW(6, 'step-06', 'pending', 'ANALYTICS_ACTIVATION_PERIOD', false, 'detect', 'Analytics'),
]
// What /api/results now returns for that run (core/api/results.py).
const COVERAGE = {
  observed: 3, total: 6, denominator: 4, observed_in_denominator: 3, prevented: 1,
  excluded: { not_reached: 1, not_executed: 0, soak_pending: 1 }, pct: 75.0, by_type: {},
}
const PAYLOAD = { run_id: 'run-ax', results: RESULTS, total: 6, coverage: COVERAGE }
// A stable empty list: NarrativeTimeline's arc effect keys on the `stitches`
// identity, so a fresh default `[]` per render would re-run it forever.
const NO_STITCHES = []

function statusOf(scope, text) {
  const row = Array.from(scope.querySelectorAll('.scorecard__row'))
    .find((r) => r.textContent.includes(text))
  return row.querySelector('.scorecard__status').textContent
}

describe('EvidenceView reads the outcome axis', () => {
  it('a not_reached / soak-pending row never renders "Missed"; DNP is never "Detected"', async () => {
    installRoutes({ 'GET /api/results/run-ax': PAYLOAD })
    const { container } = render(
      <EvidenceView lastRun={{ runId: 'run-ax', scenarioId: 'SIM-EDR-001', status: 'complete' }} />)
    await waitFor(() => expect(screen.getByText('det-missed')).toBeInTheDocument())
    expect(statusOf(container, 'det-not_reached')).not.toMatch(/Missed/)
    expect(statusOf(container, 'det-not_reached')).toMatch(/Not reached/)
    expect(statusOf(container, 'det-pending-ANALYTICS')).toMatch(/Pending/)
    expect(statusOf(container, 'det-prevented')).toMatch(/Prevented/)
    expect(statusOf(container, 'det-detected-DETECTED_NOT_PREVENTED')).toMatch(/Detected, not prevented/)
    const labels = Array.from(container.querySelectorAll('.scorecard__status')).map((s) => s.textContent)
    expect(labels.filter((t) => /^Missed$/.test(t.trim()))).toHaveLength(1)
  })

  it('the coverage KPI is the server denominator, with the exclusions named', async () => {
    installRoutes({ 'GET /api/results/run-ax': PAYLOAD })
    render(<EvidenceView lastRun={{ runId: 'run-ax', scenarioId: 'SIM-EDR-001', status: 'complete' }} />)
    await waitFor(() => expect(screen.getByText('det-missed')).toBeInTheDocument())
    expect(document.querySelector('.kpi__value--detected').textContent).toBe('75%')
    expect(document.body.textContent).toMatch(/3 \/ 4 detections confirmed/)
    expect(document.body.textContent).toMatch(/2 excluded/)
  })
})

describe('DetectionDrawer reads the outcome axis', () => {
  it('a not_reached row reads "Not reached", never "Missed"', () => {
    const row = { id: 5, tid: 'T1005', plane: 'EDR', detectionType: 'BIOC', alert: 'x',
      observed: false, outcome: 'not_reached', outcome_reason: 'UPSTREAM_SEVERED', notes: '' }
    render(<DetectionDrawer row={row} open onClose={() => {}} />)
    const pane = document.querySelector('.detection-drawer')
    expect(pane.textContent).not.toMatch(/Missed/)
    expect(pane.textContent).toMatch(/Not reached/)
  })
})

describe('useTimelineData / NarrativeTimeline read the outcome axis', () => {
  it('a not_reached detection is not a "missed" frame', async () => {
    installRoutes({
      'GET /api/scenarios/SIM-AX': {
        scenario_id: 'SIM-AX',
        steps: RESULTS.map((r) => ({ id: r.step_id, name: r.step_id,
          expected_detections: [{ plane: 'EDR', type: r.detection_type, description: r.expected_detection }] })),
      },
      'GET /api/runs/run-ax': { run_id: 'run-ax', status: 'complete' },
      'GET /api/results/run-ax': PAYLOAD,
    })
    const { result } = renderHook(() => useTimelineData('SIM-AX', 'run-ax'))
    await waitFor(() => expect(result.current.frames.length).toBe(6))
    const statuses = result.current.frames.map((f) => f.detections[0].status)
    expect(statuses).toEqual(['prevented', 'detected', 'detected_not_prevented', 'missed',
      'not_reached', 'pending'])
    const { container } = render(<NarrativeTimeline frames={result.current.frames} stitches={NO_STITCHES} />)
    expect(container.querySelectorAll('.tl-det--missed')).toHaveLength(1)
    expect(container.querySelectorAll('.tl-det--not-reached')).toHaveLength(1)
  })
})

describe('MultiRunCompare reads the outcome axis', () => {
  it('a not_reached cell is not "Missed" and is not flagged as a regression', async () => {
    const before = { results: [ROW(1, 'step-05', 'detected', null, true)],
      coverage: { ...COVERAGE, denominator: 1, observed_in_denominator: 1, pct: 100.0 } }
    const after = { results: [ROW(1, 'step-05', 'not_reached', 'UPSTREAM_SEVERED', false)],
      coverage: { ...COVERAGE, denominator: 0, observed_in_denominator: 0, pct: 0.0 } }
    after.results[0].expected_detection = before.results[0].expected_detection
    installRoutes({
      'GET /api/runs': [{ id: 'r-1', scenario_id: 'SIM-AX', status: 'complete' },
                        { id: 'r-2', scenario_id: 'SIM-AX', status: 'complete' }],
      'GET /api/results/r-1': before,
      'GET /api/results/r-2': after,
    })
    const { container } = render(<MultiRunCompare />)
    await waitFor(() => expect(container.querySelectorAll('.multirun__run-pill').length).toBe(2))
    const pills = container.querySelectorAll('.multirun__run-pill')
    fireEvent.click(pills[0])
    fireEvent.click(pills[1])
    await waitFor(() => expect(container.querySelectorAll('.multirun__cell').length).toBe(2))
    const cells = container.querySelectorAll('.multirun__cell')
    expect(cells[1].getAttribute('title')).not.toMatch(/Missed/)
    expect(cells[1].getAttribute('title')).toMatch(/Not reached/)
    expect(container.querySelector('td .multirun__flag--reg')).toBeNull()
  })
})

describe('DetectionStoryline renders a block-mode detected-not-prevented as a fail (M8)', () => {
  it('never shows ✅ Detected for DETECTED_NOT_PREVENTED', async () => {
    const raw = { detection_storyline: {
      run_id: 'run-ax', scenario_id: 'SIM-AX', run_status: 'complete',
      steps: [{ step_id: 's1', step_index: 0, step_name: 'one', detections: [{
        step_id: 's1', step_index: 0, status: 'detected', outcome: 'detected',
        outcome_reason: 'DETECTED_NOT_PREVENTED', mttd_seconds: 5, evidence_refs: [],
        expected_detection: { type: 'BIOC', plane: 'EDR', description: 'dnp' },
        observed: { alert_id: 'a-1', observed_at: '2026-10-26T10:00:05' },
      }] }],
      summary: { total: 1, detected: 1, prevented: 0, denominator: 1, coverage_pct: 100, mttd: { count: 0 } },
    } }
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, status: 200, statusText: 'OK', json: async () => raw })))
    render(<DetectionStoryline runId="run-ax" />)
    await screen.findByText('Detection Storyline')
    const pill = document.querySelector('.storyline-pill').textContent
    expect(pill).not.toMatch(/✅/)
    expect(pill).toMatch(/Detected, not prevented/)
  })
})

// ─── Residual R4: an unjudged row renders as awaiting reconcile ─────────────
// The live run a04023ce…: step-01's detect row was seeded at launch and never
// reconciled (`outcome` null, `observed` false). Chromium showed ❌ MISSED on
// the Evidence tab and "No alert reconciled — detection gap" on the Storyline.
describe('R4: an outcome-less unvalidated row is awaiting reconcile, never missed', () => {
  const UNJUDGED = ROW(7, 'step-07', null, null, false)
  const R4_PAYLOAD = {
    run_id: 'run-r4', results: [UNJUDGED], total: 1,
    coverage: { observed: 0, total: 1, denominator: 1, observed_in_denominator: 0, prevented: 0,
      excluded: { not_reached: 0, not_executed: 0, soak_pending: 0 }, pct: 0.0, by_type: {} },
  }

  it('Evidence: the row reads "Awaiting reconcile", not "Missed"', async () => {
    installRoutes({ 'GET /api/results/run-r4': R4_PAYLOAD })
    const { container } = render(
      <EvidenceView lastRun={{ runId: 'run-r4', scenarioId: 'SIM-EDR-001', status: 'complete' }} />)
    await waitFor(() => expect(screen.getByText('det-null')).toBeInTheDocument())
    expect(statusOf(container, 'det-null')).not.toMatch(/Missed/)
    expect(statusOf(container, 'det-null')).toMatch(/Awaiting reconcile/)
    expect(container.querySelector('.scorecard__row').getAttribute('data-status')).toBe('pending')
  })

  it('DetectionDrawer: "Awaiting reconcile", not "Missed"', () => {
    const row = { id: 7, tid: 'T1007', plane: 'EDR', detectionType: 'BIOC', alert: 'x',
      observed: false, outcome: null, outcome_reason: null, notes: '' }
    render(<DetectionDrawer row={row} open onClose={() => {}} />)
    const status = document.querySelector('.detection-drawer__status-row .scorecard__status')
    expect(status.textContent).not.toMatch(/Missed/)
    expect(status.textContent).toMatch(/Awaiting reconcile/)
  })

  it('timeline: the detection is a pending frame, not a missed one', async () => {
    installRoutes({
      'GET /api/scenarios/SIM-R4': {
        scenario_id: 'SIM-R4',
        steps: [{ id: 'step-07', name: 'step-07',
          expected_detections: [{ plane: 'EDR', type: 'BIOC', description: 'det-null' }] }],
      },
      'GET /api/runs/run-r4': { run_id: 'run-r4', status: 'complete' },
      'GET /api/results/run-r4': R4_PAYLOAD,
    })
    const { result } = renderHook(() => useTimelineData('SIM-R4', 'run-r4'))
    await waitFor(() => expect(result.current.frames.length).toBe(1))
    expect(result.current.frames[0].detections[0].status).toBe('pending')
    const { container } = render(<NarrativeTimeline frames={result.current.frames} stitches={NO_STITCHES} />)
    expect(container.querySelectorAll('.tl-det--missed')).toHaveLength(0)
    expect(container.querySelectorAll('.tl-det--pending')).toHaveLength(1)
  })

  it('Storyline: no "detection gap" copy and no ❌ for an outcome-less entry', async () => {
    // The status a pre-fix server sends for that row on a complete run.
    const raw = { detection_storyline: {
      run_id: 'run-r4', scenario_id: 'SIM-R4', run_status: 'complete',
      steps: [{ step_id: 's1', step_index: 0, step_name: 'one', detections: [{
        step_id: 's1', step_index: 0, status: 'missed', outcome: null,
        outcome_reason: null, mttd_seconds: null, evidence_refs: [],
        expected_detection: { type: 'BIOC', plane: 'EDR', description: 'unjudged' },
        observed: null,
      }] }],
      summary: { total: 1, detected: 0, prevented: 0, denominator: 1, coverage_pct: 0, mttd: { count: 0 } },
    } }
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, status: 200, statusText: 'OK', json: async () => raw })))
    render(<DetectionStoryline runId="run-r4" />)
    await screen.findByText('Detection Storyline')
    const det = document.querySelector('[data-testid="storyline-detection"]')
    expect(det.textContent).not.toMatch(/detection gap/)
    expect(det.textContent).toMatch(/Awaiting reconcile/)
    const pill = document.querySelector('.storyline-pill').textContent
    expect(pill).not.toMatch(/❌|Missed/)
  })

  it('Storyline: an engine-decided miss keeps its gap copy', async () => {
    const raw = { detection_storyline: {
      run_id: 'run-r4', scenario_id: 'SIM-R4', run_status: 'complete',
      steps: [{ step_id: 's1', step_index: 0, step_name: 'one', detections: [{
        step_id: 's1', step_index: 0, status: 'missed', outcome: 'missed',
        outcome_reason: null, mttd_seconds: null, evidence_refs: [],
        expected_detection: { type: 'BIOC', plane: 'EDR', description: 'gap' },
        observed: null,
      }] }],
      summary: { total: 1, detected: 0, prevented: 0, denominator: 1, coverage_pct: 0, mttd: { count: 0 } },
    } }
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, status: 200, statusText: 'OK', json: async () => raw })))
    render(<DetectionStoryline runId="run-r4" />)
    await screen.findByText('Detection Storyline')
    expect(document.querySelector('[data-testid="storyline-detection"]').textContent).toMatch(/detection gap/)
  })
})

// ─── Residual R1: the XSIAM STITCH tile uses the one denominator rule ───────
// The live run a04023ce… read "0 / 3": its denominator counted the
// not_reached row. Rows that never ran (and soak-pending rows) leave it.
describe('R1: the XSIAM STITCH denominator excludes rows that never ran or are soaking', () => {
  const stitchTile = () => Array.from(document.querySelectorAll('.kpi'))
    .find((k) => k.querySelector('.kpi__label').textContent === 'XSIAM Stitch')

  it('round 3: no analytics-plane row in the denominator renders "—", never "0 / 0"', async () => {
    installRoutes({ 'GET /api/results/run-ax': PAYLOAD })
    render(<EvidenceView lastRun={{ runId: 'run-ax', scenarioId: 'SIM-EDR-001', status: 'complete' }} />)
    await waitFor(() => expect(screen.getByText('det-missed')).toBeInTheDocument())
    expect(stitchTile().querySelector('.kpi__value').textContent).toBe('—')
    expect(stitchTile().querySelector('.kpi__suffix')).toBeNull()
  })

  it('round 3: the denominator is ANALYTICS-plane rows in the denominator', async () => {
    const A = (id, outcome, reason, observed) => ({ ...ROW(id, `step-a${id}`, outcome, reason, observed), plane: 'ANALYTICS' })
    const results = [
      ...RESULTS,
      A(11, 'detected', null, true),
      A(12, null, null, false),
      A(13, 'not_reached', 'UPSTREAM_SEVERED', false),
      A(14, 'pending', 'DEDUP_WINDOW', false),
    ]
    installRoutes({ 'GET /api/results/run-ax': { ...PAYLOAD, results, total: results.length } })
    render(<EvidenceView lastRun={{ runId: 'run-ax', scenarioId: 'SIM-EDR-001', status: 'complete' }} />)
    await waitFor(() => expect(screen.getByText('det-missed')).toBeInTheDocument())
    expect(stitchTile().querySelector('.kpi__value').textContent).toBe('1 / 2')
  })

  it('isInDenominator mirrors engine.outcome.is_in_denominator', () => {
    expect(isInDenominator('not_reached', 'UPSTREAM_SEVERED')).toBe(false)
    expect(isInDenominator('not_executed', 'TOOL_ABSENT')).toBe(false)
    expect(isInDenominator('pending', 'ANALYTICS_ACTIVATION_PERIOD')).toBe(false)
    expect(isInDenominator('pending', 'DEDUP_WINDOW')).toBe(false)
    expect(isInDenominator('pending', 'PREVENTION_ALERT_WITHOUT_TERMINATION')).toBe(true)
    expect(isInDenominator(null, null)).toBe(true)
    expect(isInDenominator('missed', null)).toBe(true)
  })
})

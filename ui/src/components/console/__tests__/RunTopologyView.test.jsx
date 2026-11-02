/**
 * RunTopologyView — the Topology tab rendered from live records.
 *
 * Pins: five lane bands in door order; cells sit in the lane `laneOf`
 * derives; an empty lane is dimmed and says so; the Engine terminal is
 * always present and carries the verdict; a missing scenario degrades to
 * row-derived lanes and says so; a failed run never shows a miss; a failed
 * results fetch says "could not load", never 0.
 */
import React from 'react'
import { describe, it, expect, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup, within } from '@testing-library/react'
import { installRoutes } from '../../../test/mockFetch.js'
import RunTopologyView from '../RunTopologyView.jsx'
import { STEP_LANES } from '../composerLanes.js'

afterEach(cleanup)

const SCENARIO = {
  scenario_id: 'SIM-MP-001', plane: 'ANALYTICS',
  steps: [
    { id: 'step-01', name: 'beacon', identity: 'www-data', expected_detections: [{ plane: 'EDR', type: 'BIOC' }] },
    { id: 'step-02', name: 'ngfw session', expected_detections: [{ plane: 'NDR', type: 'XQL' }] },
    { id: 'step-03', name: 'stitch', expected_detections: [{ plane: 'ANALYTICS', type: 'Correlation' }] },
  ],
}

const RESULTS = {
  results: [
    { id: 1, run_id: 'r1', step_id: 'step-01', plane: 'EDR', signal_type: 'BIOC', outcome: 'detected', observed: true },
    { id: 2, run_id: 'r1', step_id: 'step-02', plane: 'NDR', signal_type: 'XQL', outcome: 'missed', observed: false },
    { id: 3, run_id: 'r1', step_id: 'step-03', plane: 'ANALYTICS', signal_type: 'Correlation', detection_name: 'C2 ↔ process stitch', outcome: null, observed: false },
  ],
  total: 3,
}

function runRecord(status = 'complete', extra = {}) {
  return {
    run_id: 'r1', scenario_id: 'SIM-MP-001', status, tc_verdict: status === 'complete' ? 'fail' : null,
    output: '=== STEP 1/3 · step-01 · T1071.001 · identity=www-data ===\n--- exit_code=0 duration=1.2s ---\n',
    step_outcomes: [{ step_id: 'step-01', termination: 'exited', rc: 0 }],
    ...extra,
  }
}

function routes(status = 'complete', extra = {}) {
  return installRoutes({
    'GET /api/runs/r1': runRecord(status),
    'GET /api/results/r1': RESULTS,
    'GET /api/scenarios/SIM-MP-001': SCENARIO,
    ...extra,
  })
}

describe('RunTopologyView', () => {
  it('draws the five doors in order, places cells by laneOf, dims the empty lanes, pins the Engine', async () => {
    routes()
    render(<RunTopologyView runId="r1" run={{ run_id: 'r1', scenario_id: 'SIM-MP-001', status: 'complete' }} />)
    await waitFor(() => expect(screen.getByTestId('run-topology')).toHaveAttribute('data-steps-from', 'scenario'))
    const lanes = screen.getAllByTestId('topo-lane')
    expect(lanes.map((l) => l.dataset.lane)).toEqual(STEP_LANES)
    const byLane = Object.fromEntries(lanes.map((l) => [l.dataset.lane, l]))
    expect(within(byLane.AGT).getByTestId('topo-cell')).toHaveAttribute('data-step-id', 'step-01')
    expect(within(byLane.BVM).getByTestId('topo-cell')).toHaveAttribute('data-step-id', 'step-02')
    expect(within(byLane.ENG).getByTestId('topo-cell')).toHaveAttribute('data-step-id', 'step-03')
    for (const k of ['CC', 'DC']) {
      expect(byLane[k]).toHaveAttribute('data-empty', 'true')
      expect(byLane[k].className).toContain('run-topo__lane--empty')
      expect(byLane[k]).toHaveTextContent('no step launched through this door')
    }
    // the host record is on the cell
    const c1 = within(byLane.AGT).getByTestId('topo-cell')
    expect(c1).toHaveAttribute('data-status', 'ok')
    expect(c1).toHaveTextContent('rc=0')
    expect(c1).toHaveTextContent('identity=www-data')
    expect(within(byLane.BVM).getByTestId('topo-cell')).toHaveAttribute('data-tone', 'miss')
    // the Engine terminal
    const eng = screen.getByTestId('topo-engine')
    expect(eng).toHaveAttribute('data-empty', 'false')
    expect(screen.getByTestId('topo-verdict')).toHaveTextContent('fail')
    expect(within(eng).getAllByTestId('topo-engine-row')).toHaveLength(1)
    expect(within(eng).getByTestId('topo-engine-row')).toHaveTextContent('C2 ↔ process stitch')
    expect(screen.getByTestId('topo-crossings')).toHaveTextContent('AGT → BVM at step-02')
    expect(screen.getByTestId('topo-totals')).toHaveTextContent('tenant-verified: 0')
    expect(screen.getByTestId('run-topology')).toHaveTextContent('Authored is not proven.')
  })

  it('keeps the Engine terminal even when nothing fires there', async () => {
    routes('complete', {
      'GET /api/results/r1': { results: RESULTS.results.slice(0, 2), total: 2 },
      'GET /api/scenarios/SIM-MP-001': { ...SCENARIO, plane: 'EDR', steps: SCENARIO.steps.slice(0, 2) },
    })
    render(<RunTopologyView runId="r1" />)
    await waitFor(() => expect(screen.getByTestId('topo-engine')).toBeInTheDocument())
    expect(screen.getByTestId('topo-engine')).toHaveAttribute('data-empty', 'true')
    expect(screen.getByTestId('topo-engine')).toHaveTextContent(/no Correlation/)
    expect(screen.queryAllByTestId('topo-engine-row')).toHaveLength(0)
  })

  it('degrades to row-derived lanes when the scenario cannot be loaded, and says so', async () => {
    routes('complete', {
      'GET /api/scenarios/SIM-MP-001': () => new Response(JSON.stringify({ error: 'not found', code: 'NOT_FOUND' }), { status: 404 }),
    })
    render(<RunTopologyView runId="r1" />)
    await waitFor(() => expect(screen.getByTestId('topo-scenario-note')).toBeInTheDocument())
    expect(screen.getByTestId('run-topology')).toHaveAttribute('data-steps-from', 'run')
    const lanes = Object.fromEntries(screen.getAllByTestId('topo-lane').map((l) => [l.dataset.lane, l]))
    expect(within(lanes.AGT).getByTestId('topo-cell')).toHaveAttribute('data-step-id', 'step-01')
    expect(within(lanes.BVM).getByTestId('topo-cell')).toHaveAttribute('data-step-id', 'step-02')
    expect(within(lanes.ENG).getByTestId('topo-cell')).toHaveAttribute('data-step-id', 'step-03')
  })

  it('on a FAILED run no cell, lane or engine reads miss, and the page says why', async () => {
    routes('failed')
    render(<RunTopologyView runId="r1" />)
    await waitFor(() => expect(screen.getByTestId('topo-unproven')).toBeInTheDocument())
    expect(screen.getByTestId('run-topology')).toHaveAttribute('data-proven', 'false')
    expect(screen.getAllByTestId('topo-cell').some((c) => c.dataset.tone === 'miss')).toBe(false)
    expect(screen.getAllByTestId('topo-lane').some((l) => l.dataset.tone === 'miss')).toBe(false)
    expect(screen.getByTestId('topo-engine').dataset.tone).not.toBe('miss')
    expect(screen.getByTestId('topo-verdict')).toHaveTextContent('not scored')
  })

  it('says "could not load" on a failed results fetch — never 0', async () => {
    routes('complete', {
      'GET /api/results/r1': () => new Response(JSON.stringify({ error: 'boom', code: 'INTERNAL' }), { status: 500 }),
    })
    render(<RunTopologyView runId="r1" />)
    await waitFor(() => expect(screen.getByTestId('topo-error')).toBeInTheDocument())
    expect(screen.queryByTestId('topo-lane')).toBeNull()
  })
})

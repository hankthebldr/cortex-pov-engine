/**
 * RunTopologyView — the Topology tab rendered from live records.
 *
 * Pins: five lane bands, one per signal path; cells sit in the lane `laneOf`
 * derives from what each step does; an empty lane is dimmed and says so; the
 * Engine terminal is always present and carries the verdict; a missing
 * scenario places no step and says why, but a scenario still loading draws no
 * board at all; a failed run never shows a miss; a failed results fetch says
 * "could not load", never 0.
 */
import React from 'react'
import { describe, it, expect, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup, within, act } from '@testing-library/react'
import { installRoutes } from '../../../test/mockFetch.js'
import RunTopologyView from '../RunTopologyView.jsx'
import { STEP_LANES } from '../composerLanes.js'

afterEach(cleanup)

const SCENARIO = {
  scenario_id: 'SIM-MP-001', plane: 'ANALYTICS',
  steps: [
    { id: 'step-01', name: 'beacon', identity: 'www-data', command: 'curl -s http://c2.lab.test/beacon',
      expected_detections: [{ plane: 'EDR', type: 'BIOC' }] },
    { id: 'step-02', name: 'ngfw session',
      command: "cat > /tmp/c.yml <<'YML'\nsteps:\n  - plugin: ngfw_eal_emitter\nYML\npython3 -m scripts.eal_simulator.cli run /tmp/c.yml",
      expected_detections: [{ plane: 'NDR', type: 'XQL' }] },
    { id: 'step-03', name: 'stitch', command: 'echo stitch', expected_detections: [{ plane: 'ANALYTICS', type: 'Correlation' }] },
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
  it('draws the five signal paths in order, places cells by laneOf, dims the empty lanes, pins the Engine', async () => {
    routes()
    render(<RunTopologyView runId="r1" run={{ run_id: 'r1', scenario_id: 'SIM-MP-001', status: 'complete' }} />)
    await waitFor(() => expect(screen.getByTestId('run-topology')).toHaveAttribute('data-steps-from', 'scenario'))
    const lanes = screen.getAllByTestId('topo-lane')
    expect(lanes.map((l) => l.dataset.lane)).toEqual(STEP_LANES)
    const byLane = Object.fromEntries(lanes.map((l) => [l.dataset.lane, l]))
    expect(within(byLane.ENDPOINT).getByTestId('topo-cell')).toHaveAttribute('data-step-id', 'step-01')
    expect(within(byLane.COLLECTOR).getByTestId('topo-cell')).toHaveAttribute('data-step-id', 'step-02')
    expect(within(byLane.NONE).getByTestId('topo-cell')).toHaveAttribute('data-step-id', 'step-03')
    for (const k of ['WIRE', 'CLOUD']) {
      expect(byLane[k]).toHaveAttribute('data-empty', 'true')
      expect(byLane[k].className).toContain('run-topo__lane--empty')
      expect(byLane[k]).toHaveTextContent('no step took this path')
    }
    expect(screen.getByTestId('run-topology')).toHaveTextContent('3 of 5 paths used')
    // the host record is on the cell
    const c1 = within(byLane.ENDPOINT).getByTestId('topo-cell')
    expect(c1).toHaveAttribute('data-status', 'ok')
    expect(c1).toHaveTextContent('rc=0')
    expect(c1).toHaveTextContent('identity=www-data')
    expect(within(byLane.COLLECTOR).getByTestId('topo-cell')).toHaveAttribute('data-tone', 'miss')
    // the Engine terminal
    const eng = screen.getByTestId('topo-engine')
    expect(eng).toHaveAttribute('data-empty', 'false')
    expect(screen.getByTestId('topo-verdict')).toHaveTextContent('fail')
    expect(within(eng).getAllByTestId('topo-engine-row')).toHaveLength(1)
    expect(within(eng).getByTestId('topo-engine-row')).toHaveTextContent('C2 ↔ process stitch')
    expect(screen.getByTestId('topo-crossings')).toHaveTextContent('ENDPOINT → COLLECTOR at step-02')
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

  it('places no step when the scenario cannot be loaded, lists each one, and says why', async () => {
    routes('complete', {
      'GET /api/scenarios/SIM-MP-001': () => new Response(JSON.stringify({ error: 'not found', code: 'NOT_FOUND' }), { status: 404 }),
    })
    render(<RunTopologyView runId="r1" />)
    await waitFor(() => expect(screen.getByTestId('topo-scenario-note')).toBeInTheDocument())
    expect(screen.getByTestId('run-topology')).toHaveAttribute('data-steps-from', 'run')
    expect(screen.getByTestId('topo-scenario-note')).toHaveTextContent(/signal path is read from each step's command/)
    expect(screen.queryAllByTestId('topo-cell')).toHaveLength(0)
    const rows = screen.getAllByTestId('topo-unplaced-row')
    expect(rows.map((r) => r.textContent)).toEqual([
      expect.stringMatching(/^step-01 · scenario not loaded/),
      expect.stringMatching(/^step-02 · scenario not loaded/),
      expect.stringMatching(/^step-03 · scenario not loaded/),
    ])
    // the Engine still shows the correlation row: results are not lost
    expect(within(screen.getByTestId('topo-engine')).getAllByTestId('topo-engine-row')).toHaveLength(1)
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

  it('holds the board while the scenario request is in flight — loading is never drawn as "not loaded"', async () => {
    let releaseScenario
    const scenarioGate = new Promise((resolve) => { releaseScenario = resolve })
    let scenarioAsked = false
    routes('failed', {
      'GET /api/scenarios/SIM-MP-001': async () => { scenarioAsked = true; await scenarioGate; return SCENARIO },
    })
    render(<RunTopologyView runId="r1" />)
    // The run record named its scenario and that request is out. Give the run
    // and results responses time to land while the scenario has not.
    await waitFor(() => expect(scenarioAsked).toBe(true))
    await act(async () => { await new Promise((r) => setTimeout(r, 50)) })
    expect(screen.queryByTestId('topo-scenario-note')).toBeNull()
    expect(screen.queryByTestId('topo-unplaced')).toBeNull()
    expect(screen.queryByTestId('run-topology')).toBeNull()
    expect(screen.getByRole('status')).toHaveTextContent('loading topology')
    // The scenario lands: every step is placed, and on a failed run none is a miss.
    releaseScenario()
    await waitFor(() => expect(screen.getByTestId('run-topology')).toHaveAttribute('data-steps-from', 'scenario'))
    expect(screen.getAllByTestId('topo-cell')).toHaveLength(3)
    expect(screen.getAllByTestId('topo-cell').some((c) => c.dataset.tone === 'miss')).toBe(false)
    expect(screen.getByTestId('topo-unproven')).toBeInTheDocument()
  })

  it('says "could not load" on a failed results fetch — never 0', async () => {
    routes('complete', {
      'GET /api/results/r1': () => new Response(JSON.stringify({ error: 'boom', code: 'INTERNAL' }), { status: 500 }),
    })
    render(<RunTopologyView runId="r1" />)
    await waitFor(() => expect(screen.getByTestId('topo-error')).toBeInTheDocument())
    expect(screen.queryByTestId('topo-lane')).toBeNull()
  })

  it('on a STAGED push run every cell reads staged — not queued, not not-reached — and nothing calls it unproven', async () => {
    routes('staged', {
      'GET /api/runs/r1': { ...runRecord('staged'), mode: 'push', output: '', step_outcomes: [], tc_verdict: null },
    })
    render(<RunTopologyView runId="r1" />)
    await waitFor(() => expect(screen.getAllByTestId('topo-cell').length).toBe(3))
    const cells = screen.getAllByTestId('topo-cell')
    expect(cells.every((c) => c.dataset.status === 'staged')).toBe(true)
    expect(cells[0]).toHaveTextContent('staged')
    expect(cells[0]).not.toHaveTextContent('queued')
    expect(screen.queryByTestId('topo-unproven')).toBeNull()
    expect(screen.getByTestId('run-topology')).toHaveAttribute('data-proven', 'false')
  })
})

/**
 * UI-09, the part round 1 left open: the Live tab's own capture region.
 *
 * Round 1 put the "did not run as authored" banners at the top of Run detail.
 * The Live tab's Screenshot button captures `.narrative__capture` only — the
 * timeline, with none of those banners — and the footer calls that PNG "a POV
 * artifact". So the one picture a DC drops into a slide deck showed a Windows
 * run whose every step ran as the beacon account exactly like a clean run.
 *
 * The caveats are now also rendered INSIDE the capture region, so the image
 * carries them. jsdom only: html-to-image is not exercised here, so what is
 * pinned is that the text is inside the node the screenshot captures.
 */
import React from 'react'
import { describe, it, expect } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { installRoutes } from '../../test/mockFetch.js'
import InflightView, { runCaveatLines } from '../console/InflightView.jsx'
import RunDetailView from '../console/RunDetailView.jsx'

void React

const OUTPUT = [
  '!! PLATFORM VARIANT: step=step-02 ran its windows variant; its expected_detections were authored against the primary command and are unverified for this OS',
  '=== STEP 1/3 · step-01 · T1003 · identity=Administrator ===',
  '!! IDENTITY NOT HONOURED: windows has no unattended impersonation; ran as direct',
  'dumped',
  '--- exit_code=0 duration=1.2s ---',
  '=== STEP 2/3 · step-02 · T1059.001 · identity=direct ===',
  'ok',
  '--- exit_code=0 duration=300ms ---',
  '!! STEP NOT RUN: PLATFORM_UNSATISFIABLE step=step-03 declared=linux host=windows',
].join('\n')

const RUN = {
  run_id: 'run-win-1', scenario_id: 'SIM-EDR-013', status: 'complete', mode: 'pull',
  target: 'win-01', output: OUTPUT,
}
const SCENARIO = {
  scenario_id: 'SIM-EDR-013', name: 'Windows credential access', plane: 'EDR',
  mitre_tactic: 'TA0006', mitre_technique: 'T1003',
  steps: [{ id: 'step-01', name: 'dump', expected_detections: [] }],
}

function routes() {
  installRoutes({
    'GET /api/runs/run-win-1': RUN,
    'GET /api/scenarios/SIM-EDR-013': SCENARIO,
    'GET /api/results': { results: [] },
    'GET /api/runs/run-win-1/results': { results: [] },
  })
}

describe('runCaveatLines — one sentence per way a run did not run as authored', () => {
  it('counts identity, variant, not-run and launch warnings', () => {
    const lines = runCaveatLines({
      identity: [{ id: 'step-01' }], total: 3,
      variant: [{ id: 'step-02' }], notRun: [{ id: 'step-03' }],
    }, [{ code: 'TARGET_AGENT_OS_UNKNOWN', text: 'x' }])
    expect(lines).toEqual([
      '1 of 3 steps ran as the beacon account, not the authored identity',
      '1 step ran a platform variant, not the authored command',
      '1 step never ran on this host (PLATFORM_UNSATISFIABLE)',
      'launch warning: TARGET_AGENT_OS_UNKNOWN',
    ])
  })

  it('a clean run has none', () => {
    expect(runCaveatLines({ identity: [], total: 2, variant: [], notRun: [] }, [])).toEqual([])
    expect(runCaveatLines(null, null)).toEqual([])
  })
})

describe('InflightView — the screenshot region carries the caveats', () => {
  it('renders them inside .narrative__capture', async () => {
    routes()
    render(
      <InflightView
        activeRun={null}
        lastRun={{ runId: 'run-win-1', scenarioId: 'SIM-EDR-013', status: 'complete' }}
        caveats={['1 of 3 steps ran as the beacon account, not the authored identity']}
      />,
    )
    const block = await screen.findByTestId('narrative-caveats')
    expect(block.closest('.narrative__capture')).not.toBeNull()
    expect(block.textContent).toMatch(/ran as the beacon account/)
    expect(block.textContent).toMatch(/not a coverage measurement/i)
  })

  it('renders nothing extra for a clean run', async () => {
    routes()
    render(<InflightView activeRun={null} lastRun={{ runId: 'run-win-1', scenarioId: 'SIM-EDR-013', status: 'complete' }} />)
    await waitFor(() => expect(document.querySelector('.narrative__capture')).not.toBeNull())
    expect(screen.queryByTestId('narrative-caveats')).toBeNull()
  })
})

describe('RunDetailView → Live tab', () => {
  it('hands the run\'s own degradations to the capture region', async () => {
    routes()
    render(<RunDetailView runId="run-win-1" run={RUN} subTab="live" />)
    const block = await screen.findByTestId('narrative-caveats')
    expect(block.closest('.narrative__capture')).not.toBeNull()
    // The frame header says 1/3: the total is the scenario's, not the frames seen.
    expect(block.textContent).toMatch(/1 of 3 steps ran as the beacon account/)
    expect(block.textContent).toMatch(/1 step ran a platform variant/)
    expect(block.textContent).toMatch(/1 step never ran on this host/)
  })
})

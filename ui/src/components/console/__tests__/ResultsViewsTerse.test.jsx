/**
 * Results › Techniques and Topology say the verdict once (the verdict line at
 * the top of Results owns it), carry no status boilerplate in their totals,
 * and keep each explanation to one line with its detail in a tooltip.
 */
import React from 'react'
import { describe, it, expect, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup } from '@testing-library/react'
import { installRoutes } from '../../../test/mockFetch.js'
import RunTechniquesView from '../RunTechniquesView.jsx'
import RunTopologyView from '../RunTopologyView.jsx'

void React

afterEach(cleanup)

const CARD = {
  ttp_id: 'TTP-2026-0032',
  mitre_attack: { techniques: [{ technique_id: 'T1003', subtechnique_id: 'T1003.008', name: 'Shadow', tactic_ids: ['TA0006'] }] },
}
const SCENARIO = {
  scenario_id: 'SIM-EDR-001', plane: 'EDR',
  steps: [{ id: 'step-01', name: 'read', identity: 'root', command: 'cat /etc/shadow', expected_detections: [{ plane: 'EDR', type: 'BIOC' }] }],
}
const RESULTS = {
  results: [
    { id: 1, run_id: 'r1', step_id: 'step-01', plane: 'EDR', signal_type: 'BIOC', ttp_ref: 'TTP-2026-0032', mitre_technique: 'T1003.008', outcome: 'missed', observed: false },
  ],
  total: 1,
}
const RUN = {
  run_id: 'r1', scenario_id: 'SIM-EDR-001', status: 'failed', tc_verdict: 'fail',
  tc_verdict_detail: { reason: 'not_executed', detail: 'nothing scored' },
  output: '', step_outcomes: [],
}
const routes = () => installRoutes({
  'GET /api/runs/r1': RUN,
  'GET /api/results/r1': RESULTS,
  'GET /api/ttps/TTP-2026-0032': CARD,
  'GET /api/scenarios/SIM-EDR-001': SCENARIO,
})

/** Every explanatory line a view draws: one line, a few words. */
function sentences(root, selector) {
  return [...root.querySelectorAll(selector)].map((el) => el.textContent.trim()).filter(Boolean)
}

describe('Results › Techniques', () => {
  it('does not repeat the run verdict or print a status line in its totals', async () => {
    routes()
    render(<RunTechniquesView runId="r1" run={RUN} />)
    const view = await screen.findByTestId('run-techniques')
    expect(screen.queryByTestId('technique-verdict')).toBeNull()
    expect(view.textContent).not.toMatch(/run verdict/i)
    expect(view.textContent).not.toMatch(/tenant-verified/)
    expect(screen.getByTestId('technique-totals')).toHaveTextContent('0 of 1 detected')
  })

  it('keeps each explanation to one short line, with the detail in a tooltip', async () => {
    routes()
    render(<RunTechniquesView runId="r1" run={RUN} />)
    const view = await screen.findByTestId('run-techniques')
    const lines = sentences(view, '.run-tech__sentence, .run-tech__unproven, .run-tech__foot span')
    expect(lines.length).toBeGreaterThan(0)
    for (const line of lines) {
      expect(line.split(/\s+/).length).toBeLessThanOrEqual(8)
      expect(line).not.toMatch(/\. \S/)   // one sentence
    }
    expect(view.querySelector('.run-tech__sentence')).toHaveAttribute('title')
    expect(screen.getByTestId('technique-unproven')).toHaveAttribute('title')
  })
})

describe('Results › Topology', () => {
  it('does not repeat the run verdict or print a status line in its totals or footer', async () => {
    routes()
    render(<RunTopologyView runId="r1" run={RUN} />)
    await waitFor(() => expect(screen.getByTestId('run-topology')).toBeInTheDocument())
    const view = screen.getByTestId('run-topology')
    expect(screen.queryByTestId('topo-verdict')).toBeNull()
    expect(view.textContent).not.toMatch(/run verdict/i)
    expect(view.textContent).not.toMatch(/tenant-verified/)
    expect(view.textContent).not.toMatch(/composerLanes/)
    expect(screen.getByTestId('topo-totals')).toHaveTextContent('0 of 1 detected')
  })

  it('keeps each explanation to one short line, with the detail in a tooltip', async () => {
    routes()
    render(<RunTopologyView runId="r1" run={RUN} />)
    await waitFor(() => expect(screen.getByTestId('run-topology')).toBeInTheDocument())
    const view = screen.getByTestId('run-topology')
    const lines = sentences(view, '.run-topo__sentence, .run-topo__unproven, .run-topo__note, .run-topo__foot > span.mono')
    expect(lines.length).toBeGreaterThan(0)
    for (const line of lines) {
      expect(line.split(/\s+/).length).toBeLessThanOrEqual(9)
      expect(line).not.toMatch(/\. \S/)
    }
    expect(view.querySelector('.run-topo__sentence')).toHaveAttribute('title')
  })
})

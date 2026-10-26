/**
 * Smoke + interaction tests for MultiRunCompare.
 */
import React from 'react'
import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import MultiRunCompare from '../console/MultiRunCompare.jsx'
import { installRoutes } from '../../test/mockFetch.js'

void React

const fixtureRuns = [
  { id: 'r-before', scenario_id: 'SIM-MP-004', status: 'complete' },
  { id: 'r-after',  scenario_id: 'SIM-MP-004', status: 'complete' },
  { id: 'r-third',  scenario_id: 'SIM-EDR-001', status: 'running' },
]

const resultsBefore = {
  results: [
    { id: 1, mitre_technique: 'T1552.001', plane: 'EDR', detection_type: 'BIOC',
      expected_description: 'AKIA grep',          observed: true,  mttd_seconds: 38 },
    { id: 2, mitre_technique: 'T1078.004', plane: 'CDR', detection_type: 'Analytics',
      expected_description: 'sts:GetCallerIdentity', observed: false, mttd_seconds: null },
  ],
  // The server's coverage (core/api/results.py) — the column KPI renders it.
  coverage: { denominator: 2, observed_in_denominator: 1, pct: 50.0 },
}

const resultsAfter = {
  results: [
    { id: 11, mitre_technique: 'T1552.001', plane: 'EDR', detection_type: 'BIOC',
      expected_description: 'AKIA grep',          observed: true,  mttd_seconds: 28 },
    { id: 12, mitre_technique: 'T1078.004', plane: 'CDR', detection_type: 'Analytics',
      expected_description: 'sts:GetCallerIdentity', observed: true, mttd_seconds: 55 },
  ],
  coverage: { denominator: 2, observed_in_denominator: 2, pct: 100.0 },
}

describe('<MultiRunCompare />', () => {
  it('shows the picker with available runs', async () => {
    installRoutes({ 'GET /api/runs': fixtureRuns })
    render(<MultiRunCompare />)
    await waitFor(() => {
      // The run-pill button uses slice(0,10) of the id as its visible label
      const labels = Array.from(document.querySelectorAll('.multirun__run-pill-id'))
        .map((el) => el.textContent)
      expect(labels).toContain('r-before')
    })
  })

  it('prompts for at least 2 runs before showing the matrix', async () => {
    installRoutes({ 'GET /api/runs': fixtureRuns })
    render(<MultiRunCompare />)
    await waitFor(() => {
      expect(screen.getByText(/pick 2.4 runs above/i)).toBeInTheDocument()
    })
  })

  it('renders the comparison matrix when 2+ runs are picked', async () => {
    installRoutes({
      'GET /api/runs': fixtureRuns,
      'GET /api/results/r-before': resultsBefore,
      'GET /api/results/r-after':  resultsAfter,
    })
    const { container } = render(<MultiRunCompare />)
    await waitFor(() => {
      expect(document.querySelector('.multirun__run-pill')).toBeTruthy()
    })
    const pills = container.querySelectorAll('.multirun__run-pill')
    fireEvent.click(pills[0])
    fireEvent.click(pills[1])
    await waitFor(() => {
      expect(screen.getByText('T1552.001')).toBeInTheDocument()
    })
  })

  it('flags a regression when an earlier run detected and a later did not', async () => {
    // Swap: r-before detected both; r-after missed one — that's a regression
    const regressionBefore = {
      results: [
        { id: 1, mitre_technique: 'T1552.001', plane: 'EDR',
          expected_description: 'AKIA grep', observed: true },
        { id: 2, mitre_technique: 'T1078.004', plane: 'CDR',
          expected_description: 'sts cred',   observed: true },
      ],
    }
    const regressionAfter = {
      results: [
        { id: 11, mitre_technique: 'T1552.001', plane: 'EDR',
          expected_description: 'AKIA grep', observed: true },
        { id: 12, mitre_technique: 'T1078.004', plane: 'CDR',
          expected_description: 'sts cred',   observed: false },
      ],
    }
    installRoutes({
      'GET /api/runs': fixtureRuns,
      'GET /api/results/r-before': regressionBefore,
      'GET /api/results/r-after':  regressionAfter,
    })
    const { container } = render(<MultiRunCompare />)
    await waitFor(() => {
      expect(document.querySelector('.multirun__run-pill')).toBeTruthy()
    })
    const pills = container.querySelectorAll('.multirun__run-pill')
    fireEvent.click(pills[0])
    fireEvent.click(pills[1])
    await waitFor(() => {
      expect(document.querySelector('.multirun__flag--reg')).toBeTruthy()
    })
  })

  it('caps the picked set at 4 runs', async () => {
    const manyRuns = Array.from({ length: 6 }, (_, i) => ({
      id: `r-${i}`, scenario_id: 'SIM-EDR-001', status: 'complete',
    }))
    installRoutes({
      'GET /api/runs': manyRuns,
      'GET /api/results/r-0': { results: [] },
      'GET /api/results/r-1': { results: [] },
      'GET /api/results/r-2': { results: [] },
      'GET /api/results/r-3': { results: [] },
    })
    const { container } = render(<MultiRunCompare />)
    await waitFor(() => {
      expect(document.querySelectorAll('.multirun__run-pill').length).toBeGreaterThan(0)
    })
    const pills = container.querySelectorAll('.multirun__run-pill')
    // Pick 5 — only first 4 should activate
    for (let i = 0; i < 5; i++) fireEvent.click(pills[i])
    const picked = container.querySelectorAll('.multirun__run-pill.is-picked')
    expect(picked.length).toBe(4)
  })

  it('shows coverage deltas between adjacent columns', async () => {
    installRoutes({
      'GET /api/runs': fixtureRuns,
      'GET /api/results/r-before': resultsBefore,  // 1/2 = 50%
      'GET /api/results/r-after':  resultsAfter,   // 2/2 = 100%
    })
    const { container } = render(<MultiRunCompare />)
    await waitFor(() => {
      expect(document.querySelector('.multirun__run-pill')).toBeTruthy()
    })
    const pills = container.querySelectorAll('.multirun__run-pill')
    fireEvent.click(pills[0])
    fireEvent.click(pills[1])
    await waitFor(() => {
      // After (100%) - Before (50%) = +50 pts
      const delta = document.querySelector('.multirun__kpi-delta--up')
      expect(delta).toBeTruthy()
      expect(delta.textContent).toMatch(/50 pts/)
    })
  })
})

describe('<MultiRunCompare /> prevention delta', () => {
  const pairRuns = [
    { run_id: 'r-report', scenario_id: 'SIM-EDR-001', status: 'complete', agent_profile_mode: 'report', paired_with_run_id: null },
    { run_id: 'r-block', scenario_id: 'SIM-EDR-001', status: 'complete', agent_profile_mode: 'block', paired_with_run_id: 'r-report' },
    { run_id: 'r-solo', scenario_id: 'SIM-EDR-001', status: 'complete' },
  ]
  const PAIR = {
    runs: [pairRuns[0], pairRuns[1]],
    delta: {
      report_run_id: 'r-report', block_run_id: 'r-block', severed_at_step: 'step-02',
      steps: [
        { step_id: 'step-01', step_name: 'dump', detections: [
          { detection_id: 'bioc-1', signal_type: 'BIOC', prevention_module: 'BTP',
            report: { outcome: 'detected', mttd_seconds: 38 },
            block: { outcome: 'prevented', outcome_reason: 'blocked', mttd_seconds: 4 } },
        ] },
        { step_id: 'step-02', step_name: 'exfil', detections: [
          { detection_id: 'bioc-2', signal_type: 'XQL', prevention_module: null,
            report: { outcome: 'detected', mttd_seconds: 50 }, block: null },
        ] },
      ],
    },
  }

  it('renders the delta table for two runs that are a pair', async () => {
    const seen = []
    installRoutes({
      'GET /api/runs': pairRuns,
      'GET /api/runs/:id/pair': (url) => { seen.push(url); return PAIR },
      'GET /api/results/r-report': { results: [] },
      'GET /api/results/r-block': { results: [] },
    })
    const { container } = render(<MultiRunCompare />)
    await waitFor(() => expect(container.querySelectorAll('.multirun__run-pill').length).toBe(3))
    const pills = container.querySelectorAll('.multirun__run-pill')
    fireEvent.click(pills[0])
    fireEvent.click(pills[1])
    const table = await screen.findByTestId('pair-delta-table')
    const heads = Array.from(table.querySelectorAll('th')).map((h) => h.textContent)
    expect(heads).toEqual(['Step', 'Detection', 'Type', 'Module', 'Report-mode', 'Block-mode'])
    const rows = table.querySelectorAll('tbody tr')
    expect(rows).toHaveLength(2)
    expect(rows[0].textContent).toContain('prevented')
    expect(rows[0].textContent).toContain('blocked')
    // missing side and missing module render as an em dash, never "null"
    expect(rows[1].textContent).toContain('—')
    expect(rows[1].textContent).not.toMatch(/null/)
    expect(seen.length).toBeGreaterThan(0)
  })

  it('leaves the plain comparison alone when the two runs are not a pair', async () => {
    installRoutes({
      'GET /api/runs': pairRuns,
      'GET /api/results/r-report': { results: [] },
      'GET /api/results/r-solo': { results: [] },
    })
    const { container } = render(<MultiRunCompare />)
    await waitFor(() => expect(container.querySelectorAll('.multirun__run-pill').length).toBe(3))
    const pills = container.querySelectorAll('.multirun__run-pill')
    fireEvent.click(pills[0])
    fireEvent.click(pills[2])
    await waitFor(() => expect(container.querySelector('.multirun__matrix')).toBeTruthy())
    expect(screen.queryByTestId('pair-delta-table')).toBeNull()
  })

  async function pickPair(container) {
    await waitFor(() => expect(container.querySelectorAll('.multirun__run-pill').length).toBe(3))
    const pills = container.querySelectorAll('.multirun__run-pill')
    fireEvent.click(pills[0])
    fireEvent.click(pills[1])
  }

  it('says so when the delta has no rows', async () => {
    installRoutes({
      'GET /api/runs': pairRuns,
      'GET /api/runs/:id/pair': { runs: [], delta: { steps: [] } },
      'GET /api/results/r-report': { results: [] },
      'GET /api/results/r-block': { results: [] },
    })
    const { container } = render(<MultiRunCompare />)
    await pickPair(container)
    expect(await screen.findByText('No delta rows — the pair has no comparable detections.')).toBeInTheDocument()
    expect(screen.queryByTestId('pair-delta-table')).toBeNull()
  })

  it('shows the structured detail of a pair 409', async () => {
    installRoutes({
      'GET /api/runs': pairRuns,
      'GET /api/runs/:id/pair': new Response(JSON.stringify({
        detail: { error: 'Run not paired', code: 'RUN_NOT_PAIRED', detail: 'partner run was deleted' },
      }), { status: 409, headers: { 'content-type': 'application/json' } }),
      'GET /api/results/r-report': { results: [] },
      'GET /api/results/r-block': { results: [] },
    })
    const { container } = render(<MultiRunCompare />)
    await pickPair(container)
    const err = await screen.findByTestId('pair-delta-error')
    expect(err.textContent).toMatch(/partner run was deleted/)
    expect(err.textContent).toMatch(/RUN_NOT_PAIRED/)
  })
})

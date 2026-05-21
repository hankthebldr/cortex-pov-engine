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
  { id: 'r-before', scenario_id: 'SIM-MP-004', status: 'completed' },
  { id: 'r-after',  scenario_id: 'SIM-MP-004', status: 'completed' },
  { id: 'r-third',  scenario_id: 'SIM-EDR-001', status: 'running' },
]

const resultsBefore = {
  results: [
    { id: 1, mitre_technique: 'T1552.001', plane: 'EDR', detection_type: 'BIOC',
      expected_description: 'AKIA grep',          observed: true,  mttd_seconds: 38 },
    { id: 2, mitre_technique: 'T1078.004', plane: 'CDR', detection_type: 'Analytics',
      expected_description: 'sts:GetCallerIdentity', observed: false, mttd_seconds: null },
  ],
}

const resultsAfter = {
  results: [
    { id: 11, mitre_technique: 'T1552.001', plane: 'EDR', detection_type: 'BIOC',
      expected_description: 'AKIA grep',          observed: true,  mttd_seconds: 28 },
    { id: 12, mitre_technique: 'T1078.004', plane: 'CDR', detection_type: 'Analytics',
      expected_description: 'sts:GetCallerIdentity', observed: true, mttd_seconds: 55 },
  ],
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
      id: `r-${i}`, scenario_id: 'SIM-EDR-001', status: 'completed',
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

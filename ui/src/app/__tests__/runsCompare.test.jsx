/**
 * The Runs header and its Compare page (design change C2).
 *
 * The header carries a secondary "Compare runs" beside the primary. The
 * design draws the primary as "New run"; the closed verb set has Run, which
 * opens the New POV run page — where Scenarios and ⌘K start every launch.
 * Compare's "Open results" goes to that run's Results.
 */
import React from 'react'
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, within, waitFor, cleanup } from '@testing-library/react'
import { installRoutes } from '../../test/mockFetch.js'
import { EnvironmentContext, DEFAULT_ENV } from '../../context/EnvironmentContext.jsx'
import { getDestination } from '../destinations.jsx'
import { VERBS } from '../../components/console/kit/index.js'

void React

const RunsSurface = getDestination('runs').Component

afterEach(cleanup)

const READ = {
  run_id: 'run-read-1', scenario_id: 'SIM-EDR-001', status: 'complete', mode: 'pull',
  started_at: '2026-11-13T17:00:00', completed_at: '2026-11-13T17:01:00', delivered_at: '2026-11-13T17:00:03',
  tc_verdict_detail: { source: 'reconcile:xsiam:lab' },
}
const OTHER = { ...READ, run_id: 'run-read-2', started_at: '2026-11-13T18:00:00' }

function mount(params, { onNavigate = vi.fn(), setParams = vi.fn() } = {}) {
  render(
    <EnvironmentContext.Provider value={{ ...DEFAULT_ENV, runs: [READ, OTHER] }}>
      <RunsSurface params={params} setParams={setParams} onNavigate={onNavigate} onAbortRun={() => {}} />
    </EnvironmentContext.Provider>,
  )
  return { onNavigate, setParams }
}

describe('Runs header', () => {
  it('has a secondary Compare runs beside a primary Run that opens the New POV run page', () => {
    installRoutes({})
    const { onNavigate, setParams } = mount({})
    const compare = screen.getByRole('button', { name: 'Compare runs' })
    const runBtn = screen.getByRole('button', { name: 'Run' })
    expect(compare).toHaveClass('kit-btn--secondary')
    expect(runBtn).toHaveClass('kit-btn--primary')
    expect(VERBS).toContain('Run')
    // Secondary first, primary last — the design's order.
    expect(compare.compareDocumentPosition(runBtn) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()

    fireEvent.click(runBtn)
    expect(onNavigate).toHaveBeenCalledWith('guided')
    fireEvent.click(compare)
    expect(setParams).toHaveBeenCalledWith({ compare: '1' }, { replace: true })
  })
})

describe('Runs · Compare page', () => {
  it('is titled Compare runs, goes back to all runs, and Open results opens that run’s Results', async () => {
    installRoutes({
      'GET /api/runs': [READ, OTHER],
      'GET /api/results/run-read-1': { results: [], coverage: null },
      'GET /api/results/run-read-2': { results: [], coverage: null },
    })
    const { onNavigate, setParams } = mount({ compare: '1' })
    expect(screen.getByRole('heading', { level: 1, name: 'Compare runs' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '← All runs' }))
    expect(setParams).toHaveBeenCalledWith({ compare: null }, { replace: true })

    await waitFor(() => expect(screen.getAllByTestId('run-compare-chip')).toHaveLength(2))
    for (const c of screen.getAllByTestId('run-compare-chip')) fireEvent.click(c)
    const head = await waitFor(() => screen.getAllByTestId('run-compare-head').find((h) => h.dataset.runId === 'run-read-1'))
    fireEvent.click(within(head).getByRole('button', { name: 'Open results' }))
    expect(onNavigate).toHaveBeenCalledWith('proof', { run: 'run-read-1' })
  })
})

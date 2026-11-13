/**
 * Results always names the run it belongs to (design v26 §4.6, §7).
 *
 *   - It opens with a "Result of" bar: which scenario, which run of it, where
 *     it ran, and what it was read back from.
 *   - A switcher moves between runs that have been read back.
 *   - Results never appear without a run, and never for a run that has not
 *     been read back: that run gets the bar, the reason and Read back instead.
 */
import React from 'react'
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, within, cleanup } from '@testing-library/react'
import { installRoutes } from '../../test/mockFetch.js'
import { EnvironmentContext, DEFAULT_ENV } from '../../context/EnvironmentContext.jsx'
import { getDestination } from '../destinations.jsx'

void React

const ProofSurface = getDestination('proof').Component

const common = {
  mode: 'pull',
  target: 'agent-1',
  completed_at: '2026-11-13T17:01:00',
  delivered_at: '2026-11-13T17:00:03',
  output_bytes: 900,
  expected_detections: 10,
}
const READ_NEWER = {
  ...common, run_id: 'run-read-2', scenario_id: 'SIM-EDR-003', status: 'complete',
  started_at: '2026-11-13T18:00:00', detected_count: 4,
  tc_verdict_detail: { source: 'reconcile:xsiam:lab', scored_at: '2026-11-13T18:05:00' },
}
const READ_OLDER = {
  ...common, run_id: 'run-read-1', scenario_id: 'SIM-EDR-001', status: 'complete',
  started_at: '2026-11-13T16:00:00', detected_count: 2,
  tc_verdict_detail: { source: 'reconcile:xsiam:lab', scored_at: '2026-11-13T16:05:00' },
}
const UNREAD = {
  ...common, run_id: 'run-unread-3', scenario_id: 'SIM-CDR-001', status: 'complete',
  started_at: '2026-11-13T19:00:00', detected_count: 0,
  tc_verdict_detail: { source: 'complete' },
}

function mount({ runs, params = {}, setParams = () => {}, onNavigate = () => {} }) {
  installRoutes({
    'GET /api/results/:id': { results: [] },
  })
  return render(
    <EnvironmentContext.Provider value={{ ...DEFAULT_ENV, runs }}>
      <ProofSurface params={params} setParams={setParams} onNavigate={onNavigate} />
    </EnvironmentContext.Provider>,
  )
}

afterEach(cleanup)

describe('Results › Result of', () => {
  it('opens on the newest read-back run, named in a Result of bar', async () => {
    mount({ runs: [UNREAD, READ_NEWER, READ_OLDER] })
    const bar = await screen.findByRole('region', { name: /result of/i })
    expect(bar).toHaveTextContent('SIM-EDR-003')
    expect(bar).toHaveTextContent('run-read')
    expect(bar).toHaveTextContent(/read back from xsiam:lab/i)
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Report')
  })

  it('switches between read-back runs only, through the URL', async () => {
    const setParams = vi.fn()
    mount({ runs: [UNREAD, READ_NEWER, READ_OLDER], setParams })
    const pick = await screen.findByRole('combobox', { name: /result of run/i })
    const options = within(pick).getAllByRole('option').map((o) => o.value)
    expect(options).toEqual(['run-read-2', 'run-read-1'])
    fireEvent.change(pick, { target: { value: 'run-read-1' } })
    expect(setParams).toHaveBeenCalledWith({ run: 'run-read-1' })
  })

  it('a run that has not been read back gets its bar and Read back — no results', async () => {
    mount({ runs: [UNREAD, READ_NEWER], params: { run: 'run-unread-3' } })
    const bar = await screen.findByRole('region', { name: /result of/i })
    expect(bar).toHaveTextContent('SIM-CDR-001')
    expect(screen.getByText(/has not been read back/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Read back' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /validate all/i })).toBeNull()
    expect(screen.queryByText(/coverage/i)).toBeNull()
  })

  it('with runs but none read back, explains that and links to Runs', async () => {
    mount({ runs: [UNREAD] })
    expect(await screen.findByText(/must be read back/i)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Runs' })).toHaveAttribute('href', '#/runs')
    expect(screen.queryByRole('region', { name: /result of/i })).toBeNull()
  })

  it('with no runs at all, says so and offers Run', async () => {
    const onNavigate = vi.fn()
    mount({ runs: [], onNavigate })
    expect(await screen.findByText(/no runs yet/i)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Run' }))
    expect(onNavigate).toHaveBeenCalledWith('library')
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1)
  })
})

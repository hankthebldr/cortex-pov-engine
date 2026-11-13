/**
 * Read back from the Runs list (design v26 §4.6).
 *
 * Read back is the one way a finished run gets results: it asks SimCore to
 * read the tenant for this run (POST /api/runs/{id}/reconcile). Without a
 * tenant credential that read cannot happen, and the console must say so —
 * the run stays unread with the reason beside it, never a fake "ready".
 */
import React from 'react'
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, within, waitFor, cleanup } from '@testing-library/react'
import { installRoutes } from '../../test/mockFetch.js'
import { EnvironmentContext, DEFAULT_ENV } from '../../context/EnvironmentContext.jsx'
import { getDestination } from '../destinations.jsx'

void React

const RunsSurface = getDestination('runs').Component

const UNREAD_RUN = {
  run_id: 'run-done-1',
  scenario_id: 'SIM-EDR-001',
  status: 'complete',
  mode: 'pull',
  target: 'agent-1',
  started_at: '2026-11-13T17:00:00',
  completed_at: '2026-11-13T17:01:00',
  delivered_at: '2026-11-13T17:00:03',
  output_bytes: 900,
  expected_detections: 10,
  detected_count: 0,
  tc_verdict: 'pending',
  tc_verdict_detail: { source: 'complete' },
}

function mount(routes, env = {}) {
  const calls = installRoutes(routes)
  const refreshRuns = vi.fn()
  render(
    <EnvironmentContext.Provider value={{ ...DEFAULT_ENV, runs: [UNREAD_RUN], refreshRuns, ...env }}>
      <RunsSurface params={{}} setParams={() => {}} onNavigate={() => {}} onAbortRun={() => {}} />
    </EnvironmentContext.Provider>,
  )
  return { calls, refreshRuns }
}

const posts = (calls) => calls.mock.calls
  .filter(([, init]) => (init?.method || 'GET').toUpperCase() === 'POST')
  .map(([url]) => new URL(url, 'http://localhost'))

afterEach(cleanup)

describe('Read back on the Runs list', () => {
  it('reads the tenant for that run once, showing Reading… while it is in flight', async () => {
    let answer
    const pending = new Promise((resolve) => { answer = resolve })
    const { calls, refreshRuns } = mount({
      'POST /api/runs/:id/reconcile': () => pending,
    })
    const row = screen.getByTestId('run-row')
    fireEvent.click(within(row).getByRole('button', { name: 'Read back' }))

    const busy = await within(row).findByRole('button', { name: 'Reading…' })
    expect(busy).toBeDisabled()
    fireEvent.click(busy)
    const sent = posts(calls)
    expect(sent).toHaveLength(1)
    expect(sent[0].pathname).toBe('/api/runs/run-done-1/reconcile')
    expect(sent[0].searchParams.get('connector')).toBe('xsiam')

    answer({ source: 'xsiam:lab', observed: 2, denominator: 10, queries_issued: 1, pulled: 4 })
    await waitFor(() => expect(refreshRuns).toHaveBeenCalled())
  })

  it('without a tenant credential the run stays unread, with the reason, and Read back stays on offer', async () => {
    mount({
      'POST /api/runs/:id/reconcile': () => new Response(JSON.stringify({
        detail: { error: "configure an integration of kind 'xsiam' or 'xsiam_tenant' first", code: 'NO_INTEGRATION', detail: null },
      }), { status: 400, headers: { 'content-type': 'application/json' } }),
    })
    const row = screen.getByTestId('run-row')
    fireEvent.click(within(row).getByRole('button', { name: 'Read back' }))

    await within(row).findByText(/no tenant credential is configured/i)
    expect(within(row).getByRole('button', { name: 'Read back' })).toBeEnabled()
    expect(within(row).queryByRole('button', { name: 'Open results' })).toBeNull()
    expect(within(row).getByTestId('run-detections')).toHaveTextContent('not read back')
  })
})

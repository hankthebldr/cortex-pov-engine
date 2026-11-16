/**
 * Results › Tenant evidence: the case panel and the tenant evidence panel
 * (they were on the run's page while the detection views were its tabs).
 *
 *   - a reviewed run shows ONE case panel, embedded in tenant evidence;
 *   - the Review that can change a verdict sits beside it, behind a confirm,
 *     and it is the only Review on the tab;
 *   - a run that has not been reviewed shows tenant evidence read once from
 *     the stored snapshot (GET), never by a POST on mount.
 */
import React from 'react'
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react'
import ResultsView from '../ResultsView.jsx'
import { installRoutes } from '../../../test/mockFetch.js'

void React

afterEach(() => { cleanup(); vi.restoreAllMocks() })

const BASE = {
  run_id: 'r1', scenario_id: 'SIM-EDR-001', status: 'complete', mode: 'pull', target: 'agent-1',
  started_at: '2026-11-13T16:00:00', completed_at: '2026-11-13T16:01:00',
  delivered_at: '2026-11-13T16:00:03', output_bytes: 900, expected_detections: 4, detected_count: 2,
}
const READ = { ...BASE, tc_verdict_detail: { source: 'reconcile:xsiam:lab', scored_at: '2026-11-13T16:05:00' } }
const UNREAD = { ...BASE, tc_verdict_detail: { source: 'complete' } }
const NOT_READ = {
  run_id: 'r1', status: 'pending', code: 'TENANT_EVIDENCE_NOT_READ',
  snapshot: null, history: [], case_ledger: null,
}

const routes = () => installRoutes({
  'GET /api/runs/r1': { ...BASE, output: '' },
  'GET /api/results/r1': { run_id: 'r1', total: 0, results: [] },
  'GET /api/runs/r1/tenant-evidence': NOT_READ,
})
const seen = (h) => h.mock.calls.map(([input, init = {}]) => {
  const url = typeof input === 'string' ? input : input.url
  return `${(init.method || 'GET').toUpperCase()} ${new URL(url, 'http://localhost').pathname}`
})
const mount = (run) => render(<ResultsView run={run} readBackRuns={[READ]} tab="tenant" />)

describe('Results › Tenant evidence', () => {
  it('renders the case panel once', async () => {
    routes()
    mount(READ)
    await screen.findByTestId('tenant-evidence')
    await waitFor(() => expect(screen.getAllByTestId('run-case')).toHaveLength(1))
  })

  it('offers Review beside the case panel, behind a confirm', async () => {
    const h = routes()
    mount(READ)
    const panel = await screen.findByTestId('run-case')
    const button = panel.querySelector('button')
    expect(button).toHaveTextContent('Review')
    fireEvent.click(button)
    expect(screen.getByRole('dialog')).toHaveTextContent('Refreshing tenant evidence never does.')
    expect(seen(h).filter((c) => c.startsWith('POST '))).toEqual([])
  })

  it('a reviewed run has exactly one Review on the tab', async () => {
    routes()
    mount(READ)
    await screen.findByTestId('tenant-evidence')
    await waitFor(() => expect(screen.getByTestId('tenant-evidence')).toHaveTextContent('Refresh reads it once'))
    expect(screen.getAllByRole('button', { name: 'Review' })).toHaveLength(1)
  })

  it('a run that has not been reviewed shows tenant evidence: one GET, no POST on mount', async () => {
    const h = routes()
    mount(UNREAD)
    await screen.findByTestId('tenant-evidence')
    await waitFor(() => expect(seen(h)).toContain('GET /api/runs/r1/tenant-evidence'))
    expect(seen(h).filter((c) => c === 'GET /api/runs/r1/tenant-evidence')).toHaveLength(1)
    expect(seen(h).filter((c) => c.startsWith('POST '))).toEqual([])
    // Its one Review is the notice above the tabs, not a second one in the panel.
    expect(screen.getAllByRole('button', { name: 'Review' })).toHaveLength(1)
  })
})

/**
 * Run detail › case panel and tenant evidence (Task 11).
 *
 *   - a read-back run shows ONE case panel (the evidence tab's panel is
 *     embedded in tenant evidence; the run page's own is the one that stays);
 *   - the Read back that can change a verdict sits behind a confirm there;
 *   - an unread run shows tenant evidence, read once from the stored snapshot
 *     (GET), never by a POST on mount.
 */
import React from 'react'
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react'
import RunDetailView from '../RunDetailView.jsx'
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

describe('RunDetailView', () => {
  it('RunDetailView renders the case panel once', async () => {
    routes()
    render(<RunDetailView runId="r1" run={READ} subTab="evidence" />)
    await screen.findByTestId('tenant-evidence')
    expect(screen.getAllByTestId('run-case')).toHaveLength(1)
  })

  it('the run page offers Read back beside its case panel, behind a confirm', async () => {
    const h = routes()
    render(<RunDetailView runId="r1" run={READ} subTab="live" />)
    const panel = await screen.findByTestId('run-case')
    const button = panel.querySelector('button')
    expect(button).toHaveTextContent('Read back')
    fireEvent.click(button)
    expect(screen.getByRole('dialog')).toHaveTextContent('Refreshing tenant evidence never does.')
    expect(seen(h).filter((c) => c.startsWith('POST '))).toEqual([])
  })

  it('a READY run on the evidence tab has exactly one Read back', async () => {
    routes()
    render(<RunDetailView runId="r1" run={READ} subTab="evidence" />)
    await screen.findByTestId('tenant-evidence')
    await waitFor(() => expect(screen.getByTestId('tenant-evidence')).toHaveTextContent('Refresh reads it once'))
    expect(screen.getAllByRole('button', { name: 'Read back' })).toHaveLength(1)
  })

  it('an unread run shows tenant evidence: one GET, no POST on mount', async () => {
    const h = routes()
    render(<RunDetailView runId="r1" run={UNREAD} subTab="evidence" />)
    await screen.findByText('Tenant evidence')
    await waitFor(() => expect(seen(h)).toContain('GET /api/runs/r1/tenant-evidence'))
    expect(seen(h).filter((c) => c === 'GET /api/runs/r1/tenant-evidence')).toHaveLength(1)
    expect(seen(h).filter((c) => c.startsWith('POST '))).toEqual([])
  })
})

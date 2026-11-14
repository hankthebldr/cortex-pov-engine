/**
 * Results for a run that has not been read back still shows tenant evidence:
 * it is most useful before Read back, and it is display only. Opening the page
 * reads the stored snapshot once (GET) and never sends a POST.
 */
import React from 'react'
import { it, expect, afterEach, vi } from 'vitest'
import { render, screen, waitFor, cleanup } from '@testing-library/react'
import { installRoutes } from '../../test/mockFetch.js'
import { EnvironmentContext, DEFAULT_ENV } from '../../context/EnvironmentContext.jsx'
import { getDestination } from '../destinations.jsx'

void React

afterEach(() => { cleanup(); vi.restoreAllMocks() })

const ProofSurface = getDestination('proof').Component
const UNREAD = {
  run_id: 'run-unread-3', scenario_id: 'SIM-CDR-001', status: 'complete', mode: 'pull',
  target: 'agent-1', started_at: '2026-11-13T19:00:00', completed_at: '2026-11-13T19:01:00',
  delivered_at: '2026-11-13T19:00:03', output_bytes: 900, expected_detections: 10, detected_count: 0,
  tc_verdict_detail: { source: 'complete' },
}

it('an unread run on Results renders Tenant evidence with one GET and zero POSTs', async () => {
  const h = installRoutes({
    'GET /api/runs/run-unread-3/tenant-evidence': {
      run_id: 'run-unread-3', status: 'pending', code: 'TENANT_EVIDENCE_NOT_READ',
      snapshot: null, history: [], case_ledger: null,
    },
  })
  render(
    <EnvironmentContext.Provider value={{ ...DEFAULT_ENV, runs: [UNREAD] }}>
      <ProofSurface params={{ run: 'run-unread-3' }} setParams={() => {}} onNavigate={() => {}} />
    </EnvironmentContext.Provider>,
  )
  await screen.findByText('Tenant evidence')
  const calls = h.mock.calls.map(([i, init = {}]) => {
    const url = typeof i === 'string' ? i : i.url
    return `${(init.method || 'GET').toUpperCase()} ${new URL(url, 'http://localhost').pathname}`
  })
  await waitFor(() => expect(calls).toContain('GET /api/runs/run-unread-3/tenant-evidence'))
  expect(calls.filter((c) => c === 'GET /api/runs/run-unread-3/tenant-evidence')).toHaveLength(1)
  expect(calls.filter((c) => c.startsWith('POST '))).toEqual([])
})

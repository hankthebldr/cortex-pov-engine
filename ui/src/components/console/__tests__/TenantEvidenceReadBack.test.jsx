/**
 * One Read back per run on screen, wherever the case panel lands (Task 11 fix
 * round 1), and the onDone contract: after a confirmed Read back the panel
 * reloads the stored read once, then calls onScored.
 */
import React from 'react'
import { describe, it, expect, vi, afterEach } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import TenantEvidencePanel from '../TenantEvidencePanel.jsx'
import ReadBackButton from '../ReadBackButton.jsx'
import { installRoutes } from '../../../test/mockFetch.js'

void React

afterEach(() => { cleanup(); vi.restoreAllMocks() })

const LEDGER = (over = {}) => ({
  read_at: '2026-11-13T16:00:00', basis: 'alert_incident_ids', merge_class: 'ONE_CASE',
  merge_reason: 'one case', code: 'CASE_LEDGER_OK', shared_with_other_runs: false,
  cases: [{ case_id: '900', status: 'new', closed_by: 'open', sealed: false,
    issues_from_run: 1, issues_other: 0 }],
  alerts: [], ...over,
})
const PAYLOAD = {
  read_at: '2026-11-13T20:31:05', window: ['2026-11-13T19:55:00', '2026-11-13T20:30:00'],
  queries_issued: 1, code: 'OK', status: 'ok', issues: null, cases: null, endpoint: null, agreement: null,
}
const stored = (ledger) => ({
  run_id: 'r1', status: 'ok', code: 'OK', history: [], case_ledger: ledger,
  snapshot: { id: 1, run_id: 'r1', payload: PAYLOAD },
})
const NOT_READ = { run_id: 'r1', status: 'pending', code: 'TENANT_EVIDENCE_NOT_READ', snapshot: null, history: [], case_ledger: null }
const action = ({ onDone }) => <ReadBackButton runId="r1" onDone={onDone} />
const buttons = () => screen.queryAllByRole('button', { name: 'Read back' })

describe('exactly one Read back per run on Results', () => {
  for (const [name, ledger] of [
    ['no ledger', null],
    ['NO_CASE_YET', LEDGER({ merge_class: 'NO_CASE_YET', cases: [] })],
    ['a populated ledger', LEDGER()],
  ]) {
    it(`${name}`, async () => {
      installRoutes({ 'GET /api/runs/r1/tenant-evidence': stored(ledger) })
      render(<TenantEvidencePanel runId="r1" reconcileAction={action} />)
      await screen.findByTestId('run-case')
      expect(buttons()).toHaveLength(1)
    })
  }

  it('none while the first GET is pending, then one inside the case panel', async () => {
    let release
    const gate = new Promise((r) => { release = r })
    installRoutes({ 'GET /api/runs/r1/tenant-evidence': async () => { await gate; return stored(null) } })
    render(<TenantEvidencePanel runId="r1" reconcileAction={action} />)
    expect(buttons()).toHaveLength(0)
    release()
    await screen.findByTestId('run-case')
    expect(buttons()).toHaveLength(1)
    expect(screen.getByTestId('run-case').contains(buttons()[0])).toBe(true)
  })

  it('one in the head when the case panel is not embedded', async () => {
    installRoutes({ 'GET /api/runs/r1/tenant-evidence': NOT_READ })
    render(<TenantEvidencePanel runId="r1" showCaseLedger={false} reconcileAction={action} />)
    expect(buttons()).toHaveLength(1)
    await screen.findByText('Refresh reads it once; nothing is read automatically.')
    expect(buttons()).toHaveLength(1)
  })

  it('a plain node is accepted as the action', async () => {
    installRoutes({ 'GET /api/runs/r1/tenant-evidence': stored(LEDGER()) })
    render(<TenantEvidencePanel runId="r1" reconcileAction={<button type="button">Read back</button>} />)
    await screen.findByTestId('run-case')
    expect(buttons()).toHaveLength(1)
  })
})

describe('onDone', () => {
  it('reloads the stored read once, then calls onScored', async () => {
    const order = []
    installRoutes({
      'GET /api/runs/r1/tenant-evidence': () => { order.push('GET'); return stored(LEDGER()) },
      'POST /api/runs/r1/reconcile': () => { order.push('POST'); return { observed: 1, denominator: 1, queries_issued: 1 } },
    })
    const onScored = vi.fn(() => order.push('scored'))
    render(<TenantEvidencePanel runId="r1" onScored={onScored} reconcileAction={action} />)
    await screen.findByTestId('run-case')
    fireEvent.click(buttons()[0])
    fireEvent.click(screen.getByRole('dialog').querySelector('.btn--primary'))
    await waitFor(() => expect(onScored).toHaveBeenCalledTimes(1))
    expect(order).toEqual(['GET', 'POST', 'GET', 'scored'])
  })

  it('does nothing when the panel unmounted before the read resolved', async () => {
    let release
    const gate = new Promise((r) => { release = r })
    const h = installRoutes({
      'GET /api/runs/r1/tenant-evidence': stored(LEDGER()),
      'POST /api/runs/r1/reconcile': async () => { await gate; return { observed: 1, denominator: 1 } },
    })
    const onScored = vi.fn()
    const { unmount } = render(<TenantEvidencePanel runId="r1" onScored={onScored} reconcileAction={action} />)
    await screen.findByTestId('run-case')
    fireEvent.click(buttons()[0])
    fireEvent.click(screen.getByRole('dialog').querySelector('.btn--primary'))
    await waitFor(() => expect(h.mock.calls.some(([, i]) => i && i.method === 'POST')).toBe(true))
    unmount()
    const before = h.mock.calls.length
    release()
    await new Promise((r) => setTimeout(r, 20))
    expect(h.mock.calls.length).toBe(before)
    expect(onScored).not.toHaveBeenCalled()
  })
})

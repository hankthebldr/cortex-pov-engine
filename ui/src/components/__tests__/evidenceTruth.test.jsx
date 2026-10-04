/**
 * Proof & Export is the surface a POV readout is built from. Three ways it
 * used to say something untrue:
 *
 *  1. It read the run STATUS from a different run than the one it showed —
 *     the provider's activeRun carried no status, so an in-flight run inherited
 *     the previous run's "failed" and was declared "signal never generated".
 *  2. A failed /api/results fetch rendered as "Coverage 0 % · 0 / 0" and
 *     "no results yet — ingestion typically takes 30–120s".
 *  3. Switching runs while the previous run's fetch was in flight let the
 *     older answer land last, putting run A's scorecard under run B's header.
 */
import React from 'react'
import { describe, it, expect } from 'vitest'
import { render, screen, waitFor, renderHook, act } from '@testing-library/react'
import { EnvironmentProvider } from '../../context/EnvironmentContext.jsx'
import { getDestination } from '../../app/destinations.jsx'
import EvidenceView from '../console/EvidenceView.jsx'
import useResultsData from '../console/useResultsData.js'
import { installRoutes } from '../../test/mockFetch.js'

void React

const result = (id, over = {}) => ({
  id, plane: 'EDR', detection_type: 'BIOC', mitre_technique: 'T1003',
  expected_detection: `det-${id}`, observed: null, ...over,
})

describe('Proof & Export — status belongs to the run on screen', () => {
  it('an in-flight run is not declared "run failed" because the PREVIOUS run failed', async () => {
    installRoutes({
      'GET /api/runs': { runs: [
        { run_id: 'run-A', scenario_id: 'SIM-EDR-001', status: 'running', started_at: '2026-10-04T12:00:00' },
        { run_id: 'run-B', scenario_id: 'SIM-EDR-002', status: 'failed', started_at: '2026-10-04T11:00:00' },
      ] },
      'GET /api/results/run-A': { results: [result(1), result(2, { observed: true, mttd_seconds: 40 })] },
      'GET /api/scenarios': { scenarios: [] },
      'GET /api/agents': { agents: [] },
      'GET /api/health': { status: 'ok' },
    })
    const Proof = getDestination('proof').Component
    render(<EnvironmentProvider runPollMs={60_000}><Proof params={{}} /></EnvironmentProvider>)

    await waitFor(() => expect(screen.getByText('run-A')).toBeInTheDocument())
    await waitFor(() => expect(screen.getByText('det-1')).toBeInTheDocument())
    expect(document.body.textContent).not.toMatch(/run failed/)
    expect(document.querySelector('.kpi__value--detected').textContent).toBe('50%')
    // Validate-all is not refused on the grounds of another run's failure.
    expect(screen.getByRole('button', { name: /Validate all/ })).not.toBeDisabled()
  })
})

describe('Proof & Export — a failed fetch is not an empty scorecard', () => {
  it('renders an error state, never "0 %" or "no results yet", when /api/results fails', async () => {
    installRoutes({
      'GET /api/results/run-X': () => new Response(
        JSON.stringify({ detail: { error: 'database is locked', code: 'DB_LOCKED' } }),
        { status: 503, headers: { 'content-type': 'application/json' } },
      ),
    })
    render(<EvidenceView lastRun={{ runId: 'run-X', scenarioId: 'SIM-EDR-001', status: 'complete' }} />)
    await waitFor(() => expect(screen.getByTestId('evidence-load-error')).toBeInTheDocument())
    expect(screen.getByTestId('evidence-load-error').textContent).toContain('database is locked')
    expect(document.body.textContent).not.toMatch(/0\s*%/)
    expect(document.body.textContent).not.toMatch(/no results yet/)
  })

  it('a run with nothing seeded reads "—", not a measured 0 % coverage', async () => {
    installRoutes({ 'GET /api/results/run-E': { results: [] } })
    render(<EvidenceView lastRun={{ runId: 'run-E', scenarioId: 'SIM-EDR-001', status: 'complete' }} />)
    await waitFor(() => expect(screen.getByText('no detections seeded for this run')).toBeInTheDocument())
    expect(document.querySelector('.kpi__value--detected')).toBeNull()
  })
})

describe('useResultsData — only the newest answer may land', () => {
  it('a slow answer for the previous run never replaces the current run\'s rows', async () => {
    let releaseA
    installRoutes({
      'GET /api/results/run-A': () => new Promise((resolve) => { releaseA = () => resolve({ results: [result(1, { expected_detection: 'from-A' })] }) }),
      'GET /api/results/run-B': { results: [result(2, { expected_detection: 'from-B' })] },
    })
    const { result: hook, rerender } = renderHook(({ id }) => useResultsData(id), { initialProps: { id: 'run-A' } })
    rerender({ id: 'run-B' })
    await waitFor(() => expect(hook.current.rows.map((r) => r.alert)).toEqual(['from-B']))
    await act(async () => { releaseA() })
    expect(hook.current.rows.map((r) => r.alert)).toEqual(['from-B'])
  })
})

/**
 * Abort reaches the confirm dialog from the Runs surface.
 *
 * The live strip on Runs was mounted without `onAbort`, so its Abort button
 * called `undefined()` and threw: a DC watching a run go wrong could not stop
 * it from the page that shows it. The shell owns the confirm dialog and hands
 * every surface `onAbortRun`; these tests click Abort the way a DC would and
 * check that the request reaches it — from the run list's live strip and from
 * the live run's own page.
 */
import React from 'react'
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import { installRoutes } from '../../test/mockFetch.js'
import { EnvironmentContext, DEFAULT_ENV } from '../../context/EnvironmentContext.jsx'
import { getDestination } from '../destinations.jsx'

void React

const RunsSurface = getDestination('runs').Component

const LIVE = {
  runId: 'run-live-1',
  scenarioId: 'SIM-EDR-001',
  step: 2,
  totalSteps: 7,
  elapsed: 42,
  detected: 0,
  total: 11,
}
const RUNS = [
  { run_id: 'run-live-1', scenario_id: 'SIM-EDR-001', status: 'running', started_at: '2026-11-12T21:00:00Z' },
  { run_id: 'run-done-2', scenario_id: 'SIM-EDR-003', status: 'completed', started_at: '2026-11-12T20:00:00Z' },
]

function mount(params, onAbortRun) {
  installRoutes({})
  return render(
    <EnvironmentContext.Provider value={{ ...DEFAULT_ENV, runs: RUNS, activeRun: LIVE }}>
      <RunsSurface params={params} setParams={() => {}} onNavigate={() => {}} onAbortRun={onAbortRun} />
    </EnvironmentContext.Provider>,
  )
}

afterEach(() => {
  cleanup()
  try { window.localStorage.clear() } catch { /* storage unavailable */ }
})

describe('Abort on the Runs surface', () => {
  it('the live strip on the run list asks the shell to confirm the abort', () => {
    const onAbortRun = vi.fn()
    mount({}, onAbortRun)
    fireEvent.click(screen.getByRole('button', { name: /^abort$/i }))
    expect(onAbortRun).toHaveBeenCalledTimes(1)
  })

  it('the live run’s own page offers Abort and asks the shell to confirm it', async () => {
    const onAbortRun = vi.fn()
    mount({ run: 'run-live-1' }, onAbortRun)
    fireEvent.click(await screen.findByRole('button', { name: /abort run/i }))
    expect(onAbortRun).toHaveBeenCalledTimes(1)
  })

  it('a finished run’s page offers no Abort', async () => {
    mount({ run: 'run-done-2' }, vi.fn())
    await screen.findByRole('button', { name: /all runs/i })
    expect(screen.queryByRole('button', { name: /abort/i })).not.toBeInTheDocument()
  })
})

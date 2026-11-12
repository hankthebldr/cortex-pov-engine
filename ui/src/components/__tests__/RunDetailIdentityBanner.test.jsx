/**
 * UI-09 — a run whose steps did not run as authored says so at the top of the
 * run, before any proof surface invites a coverage reading.
 *
 * The beacon writes three markers into `Run.output` that change what a missed
 * detection MEANS:
 *   !! IDENTITY NOT HONOURED: …                       (agent/beacon/client.go)
 *   !! PLATFORM VARIANT: step=<id> ran its <os> variant …   (orchestrator)
 *   !! STEP NOT RUN: PLATFORM_UNSATISFIABLE step=<id> …     (beacon)
 * Before this guard none of them reached the Run Detail header: on a Windows
 * beacon (where EVERY non-direct identity collapses to `direct`) the run read
 * like any other, and a lineage-keyed miss read as the customer's gap.
 *
 * OPS-06 — the console's 10 s run poll now asks for `fields=summary`, which
 * drops `output`. So the detail view must fetch GET /api/runs/{id} itself and
 * must not mistake a summary row's missing output for "no output captured".
 */
import React from 'react'
import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { installRoutes } from '../../test/mockFetch.js'
import RunDetailView from '../console/RunDetailView.jsx'
import { EnvironmentProvider } from '../../context/EnvironmentContext.jsx'

void React

const OUTPUT = [
  '=== STEP 1/3 · step-01 · T1003 · identity=Administrator ===',
  '!! IDENTITY NOT HONOURED: windows has no unattended impersonation; ran as direct',
  'dumped',
  '--- exit_code=0 duration=1.2s ---',
  '=== STEP 2/3 · step-02 · T1059.001 · identity=Administrator ===',
  'ok',
  '--- exit_code=0 duration=300ms ---',
  '=== STEP 3/3 · step-03 · T1547 · identity=svc-backup ===',
  '!! IDENTITY NOT HONOURED: windows has no unattended impersonation; ran as direct',
  'persisted',
  '--- exit_code=0 duration=2s ---',
].join('\n')

const RUN = {
  run_id: 'run-win-1', scenario_id: 'SIM-EDR-013', status: 'complete', mode: 'pull',
  target: 'win-01', output: OUTPUT,
}

const banner = () => screen.findByTestId('run-identity-banner')

describe('<RunDetailView /> identity banner', () => {
  it('counts the steps that ran as the beacon account and lists them', async () => {
    installRoutes({ 'GET /api/runs/run-win-1': RUN })
    render(<RunDetailView runId="run-win-1" run={RUN} subTab="live" />)
    const b = await banner()
    expect(b).toHaveAttribute('role', 'status')
    expect(b).toHaveTextContent(
      '2 of 3 steps ran as the beacon account, not the authored identity — lineage-keyed '
      + 'misses on these steps are not a coverage measurement',
    )
    expect(b).toHaveTextContent('step-01')
    expect(b).toHaveTextContent('step-03')
    expect(b).not.toHaveTextContent('step-02')
  })

  it('renders no identity banner when every step ran as authored', async () => {
    const clean = { ...RUN, output: OUTPUT.replace(/^!! IDENTITY NOT HONOURED:.*$/gm, '') }
    installRoutes({ 'GET /api/runs/run-win-1': clean })
    render(<RunDetailView runId="run-win-1" run={clean} subTab="live" />)
    await waitFor(() => expect(screen.getByText(/All runs/)).toBeInTheDocument())
    expect(screen.queryByTestId('run-identity-banner')).not.toBeInTheDocument()
  })
})

describe('<RunDetailView /> platform banners', () => {
  it('flags steps that ran a platform variant: expected detections unverified for this OS', async () => {
    const out = [
      '!! PLATFORM VARIANT: step=step-02 ran its windows variant; its expected_detections were '
        + 'authored against the primary command and are unverified for this OS',
      OUTPUT,
    ].join('\n')
    const run = { ...RUN, output: out }
    installRoutes({ 'GET /api/runs/run-win-1': run })
    render(<RunDetailView runId="run-win-1" run={run} subTab="live" />)
    const b = await screen.findByTestId('run-platform-variant-banner')
    expect(b).toHaveTextContent(/expected detections unverified for this OS/)
    expect(b).toHaveTextContent('step-02')
  })

  it('flags steps the host refused to run: PLATFORM_UNSATISFIABLE', async () => {
    const out = [
      OUTPUT,
      '!! STEP NOT RUN: PLATFORM_UNSATISFIABLE step=step-04 declared=linux host=windows',
    ].join('\n')
    const run = { ...RUN, output: out }
    installRoutes({ 'GET /api/runs/run-win-1': run })
    render(<RunDetailView runId="run-win-1" run={run} subTab="live" />)
    const b = await screen.findByTestId('run-platform-notrun-banner')
    expect(b).toHaveTextContent('PLATFORM_UNSATISFIABLE')
    expect(b).toHaveTextContent('step-04')
    expect(b).toHaveTextContent(/never ran/i)
  })
})

describe('<RunDetailView /> with a summary-shaped list row (OPS-06)', () => {
  const SUMMARY_ROW = {
    run_id: 'run-win-1', scenario_id: 'SIM-EDR-013', status: 'complete', mode: 'pull',
    target: 'win-01', output_bytes: OUTPUT.length, output_tail: OUTPUT.slice(-60),
  }

  it('fetches the run record for the full output the banners need', async () => {
    const getRun = vi.fn(() => RUN)
    installRoutes({ 'GET /api/runs/run-win-1': getRun })
    render(<RunDetailView runId="run-win-1" run={SUMMARY_ROW} subTab="live" />)
    const b = await banner()
    expect(b).toHaveTextContent('2 of 3 steps')
    expect(getRun).toHaveBeenCalled()
  })

  it('an older SimCore\'s polled row, when it has grown past the fetched record, wins', async () => {
    // Full-shape list (no fields=summary support): the record was fetched when
    // the run had reached step 2; the 10 s poll has since delivered step 3.
    const atStep2 = OUTPUT.slice(0, OUTPUT.indexOf('=== STEP 3/3'))
    installRoutes({ 'GET /api/runs/run-win-1': { ...RUN, output: atStep2 } })
    const { rerender } = render(
      <RunDetailView runId="run-win-1" run={{ ...RUN, output: atStep2 }} subTab="live" />,
    )
    expect(await banner()).toHaveTextContent('1 of 3 steps')
    rerender(<RunDetailView runId="run-win-1" run={RUN} subTab="live" />)
    await waitFor(() => expect(screen.getByTestId('run-identity-banner')).toHaveTextContent('2 of 3 steps'))
  })

  it('a failed run shows the fetched tail, not "no output captured"', async () => {
    const failedRow = { ...SUMMARY_ROW, status: 'failed', output_tail: '' }
    const failed = {
      ...RUN, status: 'failed',
      output: 'step-01 start\nrunuser: may not be used by non-root users\n--- exit_code=1',
    }
    installRoutes({ 'GET /api/runs/run-win-1': failed })
    render(<RunDetailView runId="run-win-1" run={failedRow} subTab="live" />)
    const fb = await screen.findByTestId('run-failure-banner')
    await waitFor(() => expect(fb).toHaveTextContent(/may not be used by non-root users/))
    expect(fb).not.toHaveTextContent(/no output captured/i)
  })
})

describe('EnvironmentProvider run poll (OPS-06)', () => {
  it('asks for the summary projection, not every run\'s whole output', async () => {
    const calls = []
    installRoutes({
      'GET /api/runs': (url) => { calls.push(url); return { runs: [], total: 0 } },
      'GET /api/scenarios': { scenarios: [], total: 0 },
      'GET /api/agents': { agents: [], total: 0 },
      'GET /api/xsiam/tenants': { tenants: [] },
      'GET /api/health': { status: 'ok' },
    })
    render(<EnvironmentProvider runPollMs={60_000}><div>child</div></EnvironmentProvider>)
    await waitFor(() => expect(calls.length).toBeGreaterThan(0))
    const qs = new URL(calls[0], 'http://localhost').searchParams
    expect(qs.get('fields')).toBe('summary')
  })
})

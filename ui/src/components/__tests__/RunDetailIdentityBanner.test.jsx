/**
 * UI-09 — a run whose steps did not run as authored says so before any
 * detection view invites a coverage reading.
 *
 * The beacon writes three markers into `Run.output` that change what a missed
 * detection MEANS:
 *   !! IDENTITY NOT HONOURED: …                       (agent/beacon/client.go)
 *   !! PLATFORM VARIANT: step=<id> ran its <os> variant …   (orchestrator)
 *   !! STEP NOT RUN: PLATFORM_UNSATISFIABLE step=<id> …     (beacon)
 * On a Windows beacon (where EVERY non-direct identity collapses to `direct`)
 * the run would otherwise read like any other, and a lineage-keyed miss would
 * read as the customer's gap. The detection views are Results' tabs, so the
 * banners are Results' caveats (RunCaveats); the run's own page states the
 * same facts step by step in its runtime log.
 *
 * OPS-06 — the console's 10 s run poll asks for `fields=summary`, which drops
 * `output`. So the run's page must fetch GET /api/runs/{id} itself and must
 * not mistake a summary row's missing output for "nothing ran".
 */
import React from 'react'
import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import { installRoutes } from '../../test/mockFetch.js'
import RunDetailView from '../console/RunDetailView.jsx'
import RunCaveats from '../console/RunCaveats.jsx'
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

const caveats = (output) => render(<RunCaveats runId="run-win-1" run={RUN} output={output} />)

describe('<RunCaveats /> identity banner', () => {
  it('counts the steps that ran as the beacon account and lists them', () => {
    caveats(OUTPUT)
    const b = screen.getByTestId('run-identity-banner')
    expect(b).toHaveAttribute('role', 'status')
    expect(b).toHaveTextContent(
      '2 of 3 steps ran as the beacon account, not the authored identity — lineage-keyed '
      + 'misses on these steps are not a coverage measurement',
    )
    expect(b).toHaveTextContent('step-01')
    expect(b).toHaveTextContent('step-03')
    expect(b).not.toHaveTextContent('step-02')
  })

  it('is one collapsed line until opened: Read with care, and how many caveats', () => {
    caveats(OUTPUT)
    const box = screen.getByTestId('run-caveats')
    expect(box.tagName).toBe('DETAILS')
    expect(box.open).toBe(false)
    const summary = box.querySelector('summary')
    expect(within(summary).getByRole('img', { name: 'Read with care' })).toBeInTheDocument()
    expect(summary).toHaveTextContent('1 caveat')
    expect(screen.getByTestId('run-identity-banner')).not.toBeVisible()
  })

  it('renders nothing when every step ran as authored', () => {
    const { container } = caveats(OUTPUT.replace(/^!! IDENTITY NOT HONOURED:.*$/gm, ''))
    expect(container).toBeEmptyDOMElement()
  })
})

describe('<RunCaveats /> platform banners', () => {
  it('flags steps that ran a platform variant: expected detections unverified for this OS', () => {
    caveats([
      '!! PLATFORM VARIANT: step=step-02 ran its windows variant; its expected_detections were '
        + 'authored against the primary command and are unverified for this OS',
      OUTPUT,
    ].join('\n'))
    const b = screen.getByTestId('run-platform-variant-banner')
    expect(b).toHaveTextContent(/expected detections unverified for this OS/)
    expect(b).toHaveTextContent('step-02')
    expect(screen.getByTestId('run-caveats').querySelector('summary')).toHaveTextContent('2 caveats')
  })

  it('flags steps the host refused to run: PLATFORM_UNSATISFIABLE', () => {
    caveats([
      OUTPUT,
      '!! STEP NOT RUN: PLATFORM_UNSATISFIABLE step=step-04 declared=linux host=windows',
    ].join('\n'))
    const b = screen.getByTestId('run-platform-notrun-banner')
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
  const blocks = () => screen.queryAllByTestId('runtime-step')

  it('fetches the run record for the full output the runtime log needs', async () => {
    const getRun = vi.fn(() => RUN)
    installRoutes({ 'GET /api/runs/run-win-1': getRun })
    render(<RunDetailView runId="run-win-1" run={SUMMARY_ROW} />)
    await waitFor(() => expect(blocks()).toHaveLength(3))
    expect(getRun).toHaveBeenCalled()
    expect(blocks()[0]).toHaveTextContent('dumped')
    // The step ran as the agent's own account, and the log says which one it asked for.
    expect(blocks()[0]).toHaveTextContent('as the agent account · not Administrator')
    expect(blocks()[1]).toHaveTextContent('as Administrator')
  })

  it('an older SimCore\'s polled row, when it has grown past the fetched record, wins', async () => {
    // Full-shape list (no fields=summary support): the record was fetched when
    // the run had reached step 2; the 10 s poll has since delivered step 3.
    const atStep2 = OUTPUT.slice(0, OUTPUT.indexOf('=== STEP 3/3'))
    installRoutes({ 'GET /api/runs/run-win-1': { ...RUN, output: atStep2 } })
    const { rerender } = render(<RunDetailView runId="run-win-1" run={{ ...RUN, output: atStep2 }} />)
    await waitFor(() => expect(blocks()).toHaveLength(2))
    rerender(<RunDetailView runId="run-win-1" run={RUN} />)
    await waitFor(() => expect(blocks()).toHaveLength(3))
    expect(blocks()[2]).toHaveTextContent('persisted')
  })

  it('a failed run shows the fetched output, not an empty log', async () => {
    const failedRow = { ...SUMMARY_ROW, status: 'failed', output_tail: '' }
    const failed = {
      ...RUN, status: 'failed',
      output: 'step-01 start\nrunuser: may not be used by non-root users\n--- exit_code=1',
    }
    installRoutes({ 'GET /api/runs/run-win-1': failed })
    render(<RunDetailView runId="run-win-1" run={failedRow} />)
    const note = await screen.findByTestId('runtime-note')
    expect(note).toHaveTextContent(/may not be used by non-root users/)
    expect(screen.queryByTestId('runtime-log-empty')).toBeNull()
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

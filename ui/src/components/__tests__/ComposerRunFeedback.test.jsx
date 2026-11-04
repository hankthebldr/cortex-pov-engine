/**
 * ComposerView — live-run feedback inside the Composer.
 *
 * Pins the two behaviours the UX review (§A finding 6) asked for:
 *   - a launch from the canvas is FOLLOWED in the workstream's Active run
 *     pane (auto-switched, per-step rows tailed from the run record) instead
 *     of bouncing the DC to the Runs page;
 *   - History lists every run (not the first 8), scoped to the origin
 *     scenario with an `all runs` toggle, and a row click inspects that run
 *     in place.
 */
import React from 'react'
import { describe, it, expect, beforeAll, beforeEach, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { installRoutes } from '../../test/mockFetch.js'
import { EnvironmentProvider } from '../../context/EnvironmentContext.jsx'
import ComposerView from '../console/ComposerView.jsx'

beforeAll(() => {
  window.ResizeObserver = window.ResizeObserver || class {
    observe() {} unobserve() {} disconnect() {}
  }
  Object.defineProperty(HTMLElement.prototype, 'offsetWidth', { configurable: true, value: 1200 })
  Object.defineProperty(HTMLElement.prototype, 'offsetHeight', { configurable: true, value: 800 })
})

const SCENARIO = {
  scenario_id: 'SIM-EDR-001',
  name: 'Credential Dumping — /etc/shadow',
  plane: 'EDR',
  uc_ref: 'UCS-EDR-02',
  tc_ref: 'TC-EDR-03',
  cgo_anchor: { image_name: 'apache2', primary_username: 'www-data' },
  execution_identity: { default: 'www-data', options: ['www-data', 'root'] },
  pull_supported: true,
  push_supported: true,
  cleanup: { commands: [] },
  steps: [
    {
      id: 'step-01', name: 'Read /etc/passwd', command: 'cat /etc/passwd', identity: 'www-data',
      mitre_technique: 'T1087.001',
      expected_detections: [{ plane: 'EDR', type: 'XQL', description: 'passwd read', ttp_ref: 'TTP-2026-0032', detection_id: 'xql-1' }],
    },
    {
      id: 'step-02', name: 'Read /etc/shadow', command: 'cat /etc/shadow', identity: 'www-data',
      mitre_technique: 'T1003.008', causality: { parent_step: 'step-01', pivot: 'process_lineage' },
      expected_detections: [],
    },
  ],
}

const TRANSCRIPT = [
  '=== STEP 1/2 · step-01 · T1087.001 · identity=www-data ===',
  'root:x:0:0:root:/root:/bin/bash',
  '--- exit_code=0 duration=1.234s ---',
  '=== STEP 2/2 · step-02 · T1003.008 · identity=www-data ===',
  '!! IDENTITY NOT HONOURED: www-data is nologin; ran as direct',
  '',
].join('\n')

const NEW_RUN = {
  run_id: 'r-new', scenario_id: 'SIM-EDR-001', status: 'running', mode: 'pull', target: 'a1',
  started_at: '2026-11-02T22:07:46', completed_at: null, output: TRANSCRIPT,
  step_outcomes: [
    { step_id: 'step-01', termination: 'exited', signal: null, rc: 0, reason: null, started_at: '2026-11-02T22:07:47', recorded_at: '2026-11-02T22:07:48' },
    { step_id: 'step-02', termination: null, signal: null, rc: null, reason: null, started_at: '2026-11-02T22:07:49', recorded_at: '2026-11-02T22:07:49' },
  ],
}

function routes(extra = {}) {
  return installRoutes({
    'GET /api/health': { status: 'ok', components: {} },
    'GET /api/scenarios': { scenarios: [SCENARIO] },
    'GET /api/runs': { runs: [] },
    'GET /api/agents': [{ id: 'a1', hostname: 'web-prod-01', os: 'linux', status: 'online' }],
    'GET /api/credentials/integrations': [],
    'GET /api/shelf/payloads': { payloads: [] },
    'GET /api/shelf/artifacts': { artifacts: [] },
    'GET /api/scenarios/SIM-EDR-001': SCENARIO,
    ...extra,
  })
}

function mount(params = {}, props = {}) {
  return render(
    <EnvironmentProvider>
      <ComposerView params={params} onNavigate={() => {}} {...props} />
    </EnvironmentProvider>,
  )
}

beforeEach(() => { window.localStorage.clear() })

describe('ComposerView — a canvas launch is followed in the Active run pane', () => {
  it('auto-switches the workstream to Active run and tails the step rows, without leaving the Composer', async () => {
    const user = userEvent.setup()
    const onNavigate = vi.fn()
    let launched = false
    routes({
      'POST /api/run': () => {
        launched = true
        return { run_id: 'r-new', scenario_id: 'SIM-EDR-001', status: 'pending', mode: 'pull' }
      },
      'GET /api/runs': () => ({
        runs: launched ? [{ ...NEW_RUN, expected_detections: 1, detected_count: 0 }] : [],
      }),
      'GET /api/runs/r-new': NEW_RUN,
    })
    mount({ from: 'SIM-EDR-001' }, { onNavigate })
    await waitFor(() => expect(screen.getByTestId('chain-step-step-02')).toBeInTheDocument())

    await user.click(screen.getByTestId('composer-preflight'))
    await waitFor(() => expect(screen.getByTestId('composer-launch')).not.toBeDisabled())
    await user.click(screen.getByTestId('composer-launch'))

    // The dock switched itself to Active run …
    await waitFor(() => expect(screen.getByTestId('ws-tab-active')).toHaveAttribute('aria-selected', 'true'))
    // … and the DC is still on the canvas.
    expect(onNavigate).not.toHaveBeenCalledWith('runs', expect.anything())
    expect(screen.getByTestId('composer-chain')).toBeInTheDocument()

    const active = await screen.findByTestId('ws-active')
    expect(active).toHaveAttribute('data-run-id', 'r-new')
    await waitFor(() => expect(screen.getAllByTestId('ws-step-row')).toHaveLength(2))
    const [first, second] = screen.getAllByTestId('ws-step-row')
    expect(first).toHaveAttribute('data-step-id', 'step-01')
    expect(first).toHaveAttribute('data-status', 'ok')
    expect(first).toHaveTextContent('www-data')
    expect(first).toHaveTextContent('1.234s')
    expect(second).toHaveAttribute('data-status', 'running')
    // The beacon's degradation marker is surfaced on the row, verbatim in the title.
    expect(within(second).getByText(/not honoured/i)).toBeInTheDocument()
    expect(active).toHaveTextContent('tenant-verified: 0')
  })
})

describe('ComposerView — History', () => {
  const RUNS = [
    { run_id: 'h1', scenario_id: 'SIM-EDR-001', status: 'complete', mode: 'pull', target: 'a1', started_at: '2026-11-02T22:00:00', completed_at: '2026-11-02T22:00:05', expected_detections: 1, detected_count: 1 },
    { run_id: 'h2', scenario_id: 'SIM-EDR-008', status: 'failed', mode: 'pull', target: 'a1', started_at: '2026-11-02T21:00:00', expected_detections: 3, detected_count: 0 },
    ...Array.from({ length: 10 }, (_, i) => ({
      run_id: `h-old-${i}`, scenario_id: 'SIM-EDR-001', status: 'failed', mode: 'pull', target: 'a1',
      started_at: '2026-11-01T10:00:00', expected_detections: 1, detected_count: 0,
    })),
  ]

  it('lists every run of the origin scenario, toggles to all runs, and follows a row in place', async () => {
    const user = userEvent.setup()
    routes({
      'GET /api/runs': { runs: RUNS },
      'GET /api/runs/h2': { ...RUNS[1], output: '', step_outcomes: [] },
    })
    mount({ from: 'SIM-EDR-001' })
    await waitFor(() => expect(screen.getByTestId('chain-step-step-02')).toBeInTheDocument())
    await user.click(screen.getByTestId('ws-tab-history'))

    // Scoped to the origin scenario: 11 of 12, and all 11 listed (not 8).
    await waitFor(() => expect(screen.getAllByTestId('ws-history-row')).toHaveLength(11))
    expect(screen.getByTestId('ws-history')).toHaveTextContent('11 of 12 runs')

    await user.click(screen.getByTestId('ws-history-scope'))
    await waitFor(() => expect(screen.getAllByTestId('ws-history-row')).toHaveLength(12))

    // Inspect in place: the row's inspect control follows the run in Active.
    const h2 = screen.getAllByTestId('ws-history-row').find((el) => el.dataset.runId === 'h2')
    await user.click(within(h2).getByTitle('Inspect in the Active run pane'))
    await waitFor(() => expect(screen.getByTestId('ws-tab-active')).toHaveAttribute('aria-selected', 'true'))
    expect(await screen.findByTestId('ws-active')).toHaveAttribute('data-run-id', 'h2')
    expect(screen.getByTestId('ws-active-status')).toHaveTextContent('failed')
  })
})

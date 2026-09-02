import React from 'react'
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import '@testing-library/jest-dom'
import { installRoutes } from '../../test/mockFetch.js'
import LaunchView from '../console/LaunchView.jsx'
import TelemetryStrip from '../console/TelemetryStrip.jsx'

/**
 * I6 — `FirstUseHint`/`useFirstUseHint` shipped with zero production
 * consumers (6 tests, real CSS, but nothing in the app ever rendered one).
 * That is the exact pattern this whole workstream exists to correct
 * (`HelpOverlay.onTour` was authored, tested, and dead in the product).
 *
 * Spec §5 names five consequential controls: Arm · Launch · Abort ·
 * Reconcile · Export POV. This wires the two most consequential — Launch
 * (LaunchView.jsx) and Abort (TelemetryStrip.jsx) — and proves the §5
 * contract end-to-end: the hint clears when the control is USED, never
 * when its own dismiss (×) is clicked.
 */

const SCENARIO = {
  scenario_id: 'SIM-EDR-022',
  name: 'Shelf-staged privesc enumeration',
  plane: 'EDR',
  push_supported: true,
  pull_supported: true,
  execution_identity: { default: 'www-data', options: ['www-data', 'root'] },
  external_tools: [],
  steps: [{ id: 'step-01', identity: 'www-data', platforms: ['linux'] }],
  primary_kpi: 'MTTD',
  threshold: { kpi: 'MTTD', op: '<=', value: 300 },
}

const AGENT = {
  agent_id: 'jumpbox-01', hostname: 'ip-10-0-1-24', os: 'linux',
  capabilities: ['shell', 'identity-harness', 'artifact-fetch'],
  status: 'online', last_seen_age_seconds: 2,
}

const ROUTES = {
  'GET /api/tools/adapters': { adapters: [], total: 0 },
  'GET /api/agents': { agents: [AGENT], total: 1 },
  'GET /api/credentials/integrations': { integrations: [] },
  'GET /api/shelf/payloads': { payloads: [], dist_dir: '/app/payloads', total: 0, declared: [] },
  'GET /api/shelf/artifacts': { artifacts: null },
  'POST /api/run': { run_id: 'r-1', scenario_id: 'SIM-EDR-022', mode: 'pull', status: 'running' },
}

const TARGET = { kind: 'agent', id: 'jumpbox-01', label: 'jumpbox-01' }

// Same stub pattern as the onboarding hook tests — this jsdom environment
// does not always implement window.localStorage out of the box.
function makeStorageStub() {
  const m = new Map()
  return {
    get length() { return m.size },
    key: (i) => Array.from(m.keys())[i] ?? null,
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => { m.set(k, String(v)) },
    removeItem: (k) => { m.delete(k) },
    clear: () => { m.clear() },
  }
}
if (!window.localStorage) {
  Object.defineProperty(window, 'localStorage', {
    value: makeStorageStub(), writable: true, configurable: true,
  })
}

beforeEach(() => {
  installRoutes(ROUTES)
  window.localStorage.clear()
})
afterEach(() => { vi.restoreAllMocks() })

describe('Launch first-use hint (I6)', () => {
  it('shows the hint on a fresh profile', async () => {
    render(<LaunchView scenario={SCENARIO} selectedTarget={TARGET} />)
    await waitFor(() => expect(screen.getByRole('button', { name: /Launch run/i })).toBeInTheDocument())
    expect(screen.getByRole('note')).toHaveTextContent(/Launch fires the armed scenario/i)
  })

  it('clears PERMANENTLY when Launch is USED, not merely clicked-and-forgotten', async () => {
    render(<LaunchView scenario={SCENARIO} selectedTarget={TARGET} />)
    const launchBtn = await screen.findByRole('button', { name: /Launch run/i })
    expect(screen.getByRole('note')).toBeInTheDocument()

    // findByRole returns the Launch button even while it is still DISABLED:
    // `disabled = launch.launching || blockers.length > 0`, and `blockers`
    // settle asynchronously as the preflight resolves fetched agent/target
    // data. fireEvent.click on a disabled button is a no-op, so clicking before
    // it enables leaves handleLaunch — and the synchronous launchHint.onUse()
    // that clears the hint — never called. That was the CI-only flake (the hint
    // stayed and the button was already enabled again by the time waitFor gave
    // up). Wait for the button to be enabled, THEN click; the clear is then
    // synchronous and the default waitFor is ample.
    await waitFor(() => expect(launchBtn).toBeEnabled())
    fireEvent.click(launchBtn)

    await waitFor(() => expect(screen.queryByRole('note')).not.toBeInTheDocument())
    expect(window.localStorage.getItem('cortexsim.onboarding.hint.launch')).toBe('true')

    // A remount (e.g. navigating away and back) must not resurrect it.
    const { unmount } = render(<LaunchView scenario={SCENARIO} selectedTarget={TARGET} />)
    await waitFor(() => expect(screen.queryAllByRole('note')).toHaveLength(0))
    unmount()
  })

  it('dismissing the hint (×) hides it for this mount but does NOT mark it used', async () => {
    render(<LaunchView scenario={SCENARIO} selectedTarget={TARGET} />)
    await screen.findByRole('button', { name: /Launch run/i })
    const dismiss = screen.getByLabelText('Dismiss hint')
    fireEvent.click(dismiss)

    expect(screen.queryByRole('note')).not.toBeInTheDocument()
    // NOT persisted — spec §5: "cleared ... when the control is USED, not
    // when the bubble is dismissed."
    expect(window.localStorage.getItem('cortexsim.onboarding.hint.launch')).toBeNull()
  })
})

describe('Abort first-use hint (I6)', () => {
  const RUN = { scenarioId: 'SIM-EDR-022', step: 2, totalSteps: 5, elapsed: 42, detected: 1, total: 3 }

  it('shows the hint on a fresh profile', () => {
    render(<TelemetryStrip run={RUN} onAbort={() => {}} />)
    expect(screen.getByRole('note')).toHaveTextContent(/Abort stops the run/i)
  })

  it('clears PERMANENTLY when Abort is USED', () => {
    const onAbort = vi.fn()
    render(<TelemetryStrip run={RUN} onAbort={onAbort} />)
    fireEvent.click(screen.getByRole('button', { name: /abort/i }))

    expect(onAbort).toHaveBeenCalled()
    expect(screen.queryByRole('note')).not.toBeInTheDocument()
    expect(window.localStorage.getItem('cortexsim.onboarding.hint.abort')).toBe('true')
  })

  it('dismissing the hint (×) does NOT mark it used', () => {
    render(<TelemetryStrip run={RUN} onAbort={() => {}} />)
    fireEvent.click(screen.getByLabelText('Dismiss hint'))

    expect(screen.queryByRole('note')).not.toBeInTheDocument()
    expect(window.localStorage.getItem('cortexsim.onboarding.hint.abort')).toBeNull()
  })
})

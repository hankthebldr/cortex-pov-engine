/**
 * Header — this POVengine's status, its live KPIs and the Running pill
 * (design v26 §4.5).
 *
 * Every value in the header comes from SimCore: the status and uptime from
 * GET /api/health, the KPIs from the run and agent lists the console already
 * polls. Nothing is a sample. The tests mount the whole console against a
 * mocked SimCore and read the header the way a DC would.
 */
import React from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, within, fireEvent, waitFor } from '@testing-library/react'
import { installRoutes } from '../../test/mockFetch.js'

void React

// The surfaces under the header are not under test here.
vi.mock('../console/GetStartedView.jsx', () => ({
  default: function StartStub() { return <div data-testid="start-stub"><h1>Get started</h1></div> },
}))
vi.mock('../console/ReadinessView.jsx', () => ({
  default: function ReadinessStub() { return <div data-testid="readiness-stub" /> },
}))

const UP_3D_4H = 3 * 86400 + 4 * 3600 + 7 * 60

function health(overrides = {}) {
  return {
    status: 'ok',
    version: '1.1.1',
    started_at: '2026-11-10T07:00:00Z',
    uptime_seconds: UP_3D_4H,
    components: {},
    degraded_components: [],
    not_checked: [],
    ...overrides,
  }
}

const NOW = new Date().toISOString()
const LONG_AGO = '2026-01-01T00:00:00Z'

const RUNS_WITH_LIVE = [
  { run_id: 'run-live-1', scenario_id: 'SIM-EDR-001', status: 'running', current_step: 3, total_steps: 7, started_at: NOW },
  { run_id: 'run-done-2', scenario_id: 'SIM-EDR-003', status: 'complete', started_at: LONG_AGO },
  { run_id: 'run-abrt-3', scenario_id: 'SIM-CDR-001', status: 'aborted', started_at: LONG_AGO },
]
const RUNS_NONE_LIVE = RUNS_WITH_LIVE.slice(1)

const AGENTS = [
  { agent_id: 'a-1', hostname: 'jumpbox-1', status: 'online', last_seen: NOW },
  { agent_id: 'a-2', hostname: 'jumpbox-2', status: 'offline', last_seen: LONG_AGO },
]

function serve({ body = health(), runs = RUNS_WITH_LIVE, agents = AGENTS } = {}) {
  installRoutes({
    'GET /api/health': body,
    'GET /api/runs': { runs, total: runs.length },
    'GET /api/agents': agents,
  })
}

let AppConsole
beforeEach(async () => {
  window.localStorage.clear()
  window.sessionStorage.clear()
  window.location.hash = '#/start'
  AppConsole = (await import('../../AppConsole.jsx')).default
})
afterEach(() => {
  window.location.hash = ''
})

async function instanceButton() {
  return screen.findByRole('button', { name: /^POVengine/ })
}

describe('Header — this POVengine instance', () => {
  it('states the instance status as an icon and a word, with its uptime from /api/health', async () => {
    serve()
    render(<AppConsole />)
    const btn = await instanceButton()
    await waitFor(() => expect(btn).toHaveTextContent('3d 4h'))
    // Status is never colour alone: a glyph with the word as its name, and the word.
    expect(within(btn).getByRole('img', { name: 'Up' })).toBeInTheDocument()
    expect(btn).toHaveTextContent(/Up/)
  })

  it('shows the live KPIs SimCore reports: running, agents online, runs, issues', async () => {
    serve()
    render(<AppConsole />)
    const btn = await instanceButton()
    await waitFor(() => expect(btn).toHaveTextContent(/1\s*running/))
    expect(btn).toHaveTextContent(/1\/2\s*agents online/)
    expect(btn).toHaveTextContent(/3\s*runs/)
    expect(btn).toHaveTextContent(/0\s*issues/)
  })

  it('counts a degraded component as an issue and reads Degraded, not Up', async () => {
    serve({
      body: health({
        status: 'degraded',
        components: { api_auth: { status: 'degraded', code: 'API_UNAUTHENTICATED', detail: 'Fix: set a key' } },
        degraded_components: ['api_auth'],
      }),
    })
    render(<AppConsole />)
    const btn = await instanceButton()
    await waitFor(() => expect(within(btn).getByRole('img', { name: 'Degraded' })).toBeInTheDocument())
    expect(btn).toHaveTextContent(/1\s*issue/)
  })

  it('reads Down with no uptime or KPIs when SimCore does not answer', async () => {
    installRoutes({}) // every route 404s, /api/health included
    render(<AppConsole />)
    const btn = await instanceButton()
    await waitFor(() => expect(within(btn).getByRole('img', { name: 'Down' })).toBeInTheDocument())
    expect(btn).not.toHaveTextContent(/running|agents|issues/)
  })

  it('opens the Launch gate until the POVengine component page exists', async () => {
    serve()
    render(<AppConsole />)
    fireEvent.click(await instanceButton())
    await waitFor(() => expect(window.location.hash).toBe('#/preflight'))
  })
})

describe('Header — the Running pill', () => {
  it('is shown while a run is live and opens that run’s detail', async () => {
    serve()
    render(<AppConsole />)
    const pill = await screen.findByRole('button', { name: /^Running SIM-EDR-001/ })
    expect(pill).toHaveTextContent(/step 3 of 7/)
    fireEvent.click(pill)
    await waitFor(() => expect(window.location.hash).toMatch(/^#\/runs\?/))
    expect(window.location.hash).toContain('run=run-live-1')
    expect(window.location.hash).toContain('tab=live')
  })

  it('is not rendered when no run is live', async () => {
    serve({ runs: RUNS_NONE_LIVE })
    render(<AppConsole />)
    const btn = await instanceButton()
    await waitFor(() => expect(btn).toHaveTextContent(/2\s*runs/))
    expect(screen.queryByRole('button', { name: /^Running/ })).not.toBeInTheDocument()
    expect(screen.queryByTestId('header-run-view')).not.toBeInTheDocument()
  })
})

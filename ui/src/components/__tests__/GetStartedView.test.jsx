/**
 * GetStartedView — the console's front door.
 *
 * Pinned: it opens on the first unfinished REQUIRED step, every "done" comes
 * from provider state, the self-confirmed steps say so, and the simulate step
 * hands off to the guided launch with a real scenario id.
 */
import React from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { installRoutes } from '../../test/mockFetch.js'
import { EnvironmentContext, DEFAULT_ENV } from '../../context/EnvironmentContext.jsx'
import { buildHealthModel } from '../console/readiness/healthModel.js'
import GetStartedView from '../console/GetStartedView.jsx'

void React

function mount(env = {}, onNavigate = vi.fn()) {
  render(
    <EnvironmentContext.Provider value={{ ...DEFAULT_ENV, refreshAgents: vi.fn(), ...env }}>
      <GetStartedView onNavigate={onNavigate} />
    </EnvironmentContext.Provider>,
  )
  return onNavigate
}

const ONLINE = { agent_id: 'local-jumpbox', hostname: 'vm', os: 'linux', status: 'online' }

describe('GetStartedView', () => {
  beforeEach(() => {
    window.localStorage.clear()
    installRoutes({ 'GET /api/tenants': [], 'GET /api/xsiam/tenants': [] })
  })

  it('opens on the agent step for an empty instance, with one primary action', () => {
    mount()
    expect(screen.getByText('0 of 3 required steps done')).toBeInTheDocument()
    expect(screen.getByTestId('gs-step-agent').querySelector('.gs-step__body')).not.toBeNull()
    expect(screen.getByTestId('gs-agent-generate')).toHaveClass('pov-btn--primary')
  })

  it('marks the agent done from provider state and moves on to the tenant', () => {
    mount({ agents: [ONLINE] })
    expect(screen.getByTestId('gs-step-agent')).toHaveAttribute('data-done', 'true')
    expect(screen.getByTestId('gs-step-tenant').querySelector('.gs-step__body')).not.toBeNull()
    expect(screen.getByText('1 of 3 required steps done')).toBeInTheDocument()
  })

  it('says a confirmed step was confirmed, not verified', () => {
    mount({ agents: [ONLINE] })
    fireEvent.click(screen.getByTestId('gs-step-bvm').querySelector('.gs-step__row'))
    fireEvent.click(screen.getByRole('button', { name: /I've set this up/i }))
    expect(screen.getByTestId('gs-step-bvm')).toHaveAttribute('data-done', 'true')
    expect(screen.getByText(/POVengine cannot check this one/)).toBeInTheDocument()
  })

  it('hands the suggested first run to the guided launch', () => {
    const onNavigate = mount({
      agents: [ONLINE],
      tenants: [{ name: 'lab' }],
      agent: ONLINE,
      scenarios: [{ scenario_id: 'SIM-CDR-001', plane: 'CDR', name: 'Cloud' }, { scenario_id: 'SIM-EDR-001', plane: 'EDR', name: 'Credential Dumping' }],
    })
    fireEvent.click(screen.getByTestId('gs-simulate-launch'))
    expect(onNavigate).toHaveBeenCalledWith('guided', { arm: 'SIM-EDR-001' })
  })

  it('an agent the installer only enrolled does not finish the step (UI-05)', () => {
    const t = new Date(Date.now() - 3000).toISOString().replace('Z', '')
    mount({ agents: [{ agent_id: 'jb-1a2b3c', hostname: 'jb', os: 'linux', status: 'online', registered_at: t, last_seen: t, last_seen_age_seconds: 3 }] })
    expect(screen.getByTestId('gs-step-agent')).toHaveAttribute('data-done', 'false')
    expect(screen.getByTestId('gs-step-agent').textContent).toMatch(/1 enrolled — waiting for first beacon poll/)
  })

  it('surfaces a failed first run with a way into it', () => {
    const onNavigate = mount({
      agents: [ONLINE],
      tenants: [{ name: 'lab' }],
      runs: [{ run_id: 'r-9', status: 'failed' }],
    })
    expect(screen.getByTestId('gs-simulate-failed')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /open the failed run/i }))
    expect(onNavigate).toHaveBeenCalledWith('runs', { run: 'r-9', tab: 'live' })
  })
})

// ── Agent step: per-target beacon availability, the Windows command, failures ─
const TOKEN = { id: 1, token: 'cxs_GsT0kenAbCdEfGhIjKlMnOpQrStUvWxYz', created_at: '2026-11-05T10:00:00', expires_at: '2026-11-05T11:00:00', max_uses: 1, used_count: 0, remaining_uses: 1, revoked: false }
const ORIGIN = window.location.origin
const TLS = '[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072; '

// The server's shape: `missing[]` is computed against <goos>/amd64 only;
// `available[]` is everything on the shelf. An empty dist has neither.
const MATRIX = ['darwin/amd64', 'darwin/arm64', 'linux/amd64', 'linux/arm64', 'windows/amd64']
function healthMissing(missing, { alsoAbsent = [] } = {}) {
  const empty = missing.length === 3
  return buildHealthModel({
    status: 'degraded',
    components: {
      agent_binaries: {
        status: 'degraded',
        code: empty ? 'AGENT_DIST_EMPTY' : 'AGENT_DIST_INCOMPLETE',
        detail: `missing ${missing.join(', ')}`,
        missing,
        available: empty ? [] : MATRIX.filter((t) => !missing.includes(t) && !alsoAbsent.includes(t)),
      },
    },
  })
}

function agentRoutes(attempts = []) {
  return installRoutes({
    'GET /api/tenants': [],
    'GET /api/xsiam/tenants': [],
    'POST /api/agents/enroll/tokens': TOKEN,
    'GET /api/agents/install/attempts': { attempts, total: attempts.length, retained_max: 100 },
  })
}

const pickOs = (label) => fireEvent.click(screen.getByRole('radio', { name: label }))
const agentStep = () => screen.getByTestId('gs-step-agent')

describe('GetStartedView — agent step reads which beacons ship, per target (UI-16)', () => {
  beforeEach(() => { window.localStorage.clear(); agentRoutes() })

  it('Windows missing: the PowerShell installer will refuse — not "needs Go"', () => {
    mount({ healthModel: healthMissing(['windows/amd64']) })
    pickOs('Windows')
    expect(agentStep().textContent).toMatch(/the PowerShell installer will refuse \(WINDOWS_AGENT_UNAVAILABLE\)/)
    expect(agentStep().textContent).not.toMatch(/needs Go/)
  })

  it('Windows missing says nothing about the Linux target the DC actually picked', () => {
    mount({ healthModel: healthMissing(['windows/amd64']) })
    expect(agentStep().textContent).not.toMatch(/build from source/)
    expect(agentStep().textContent).not.toMatch(/WINDOWS_AGENT_UNAVAILABLE/)
  })

  it('a POSIX target missing keeps the source-build message', () => {
    mount({ healthModel: healthMissing(['darwin/amd64', 'linux/amd64', 'windows/amd64']) })
    expect(agentStep().textContent).toMatch(/build from source/)
    expect(agentStep().textContent).toMatch(/Go/)
  })

  it('macOS on an amd64-only shelf: says Apple-silicon hosts are not served, Intel ones are', () => {
    mount({ healthModel: healthMissing([], { alsoAbsent: ['darwin/arm64', 'linux/arm64'] }) })
    pickOs('macOS')
    const gap = screen.getByTestId('gs-posix-partial')
    expect(gap.textContent).toContain('darwin/arm64')
    expect(gap.textContent).toMatch(/Apple silicon/i)
    expect(gap.textContent).toContain('darwin/amd64')
    expect(gap.textContent).toMatch(/build from source/)
    expect(screen.queryByTestId('gs-posix-missing')).toBeNull()
  })

  it('a full matrix raises no architecture notice', () => {
    mount({ healthModel: healthMissing([]) })
    pickOs('macOS')
    expect(screen.queryByTestId('gs-posix-partial')).toBeNull()
    expect(screen.queryByTestId('gs-posix-missing')).toBeNull()
  })
})

describe('GetStartedView — the Windows command (INST-12 / UI-11)', () => {
  beforeEach(() => { window.localStorage.clear(); agentRoutes() })

  it('opts into TLS 1.2 before iwr, exactly', async () => {
    mount()
    pickOs('Windows')
    fireEvent.click(screen.getByTestId('gs-agent-generate'))
    expect((await screen.findByTestId('gs-agent-command')).textContent).toBe(
      `${TLS}$env:CORTEXSIM_TOKEN='${TOKEN.token}'; iwr -useb "${ORIGIN}/api/agents/install?os=windows&interval=10" | iex`,
    )
  })

  it('tells the DC to paste it into an elevated Windows PowerShell', async () => {
    mount()
    pickOs('Windows')
    fireEvent.click(screen.getByTestId('gs-agent-generate'))
    await screen.findByTestId('gs-agent-command')
    expect(screen.getByText('Open Windows PowerShell as Administrator, then paste')).toBeInTheDocument()
    expect(agentStep().textContent).toMatch(/not yet proven on a Windows host/i)
  })
})

describe('GetStartedView — installer failures replace the endless wait (UI-06)', () => {
  beforeEach(() => { window.localStorage.clear() })

  it('a failure after the mint is an alert and the waiting line goes away', async () => {
    agentRoutes([{ stage: 'download', code: 'BINARY_FETCH_FAILED', detail: '404 Not Found', hostname: 'jb', os: 'linux', arch: 'amd64', reported_at: '2026-11-05T10:00:06' }])
    mount()
    fireEvent.click(screen.getByTestId('gs-agent-generate'))
    await screen.findByTestId('gs-agent-command')
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('BINARY_FETCH_FAILED')
    expect(alert.textContent).toContain('404 Not Found')
    expect(screen.queryByText(/Waiting for the agent to check in/)).toBeNull()
  })

  it('a report the installer carried on from (CHECKSUM_SKIPPED) is not a stop: it still waits', async () => {
    agentRoutes([{ stage: 'verify', code: 'CHECKSUM_SKIPPED', detail: "expect='' actual_tool_present=no", hostname: 'jb', os: 'linux', arch: 'amd64', reported_at: '2026-11-05T10:00:06' }])
    mount()
    fireEvent.click(screen.getByTestId('gs-agent-generate'))
    await screen.findByTestId('gs-agent-command')
    const live = await screen.findByTestId('gs-attempt-latest')
    expect(live.getAttribute('role')).toBe('status')
    expect(live.textContent).toMatch(/not verified/i)
    expect(screen.queryByRole('alert')).toBeNull()
    expect(screen.getByText(/Waiting for the agent to check in/)).toBeInTheDocument()
  })

  it('with nothing reported yet it still waits, and says where failures would appear', async () => {
    agentRoutes([])
    mount()
    fireEvent.click(screen.getByTestId('gs-agent-generate'))
    await screen.findByTestId('gs-agent-command')
    expect(screen.getByText(/Waiting for the agent to check in/)).toBeInTheDocument()
    await waitFor(() => expect(screen.getByTestId('deploy-attempts').textContent).toMatch(/in-memory/i))
  })
})

describe('GetStartedView — copy (UI-14)', () => {
  const restore = []
  function setEnv({ secure, clipboard, execCommand }) {
    Object.defineProperty(window, 'isSecureContext', { configurable: true, value: secure })
    Object.defineProperty(window.navigator, 'clipboard', { configurable: true, value: clipboard })
    document.execCommand = execCommand
    restore.push(() => { delete window.isSecureContext; delete window.navigator.clipboard; delete document.execCommand })
  }
  beforeEach(() => { window.localStorage.clear(); agentRoutes() })
  afterEach(() => { while (restore.length) restore.pop()() })

  async function generated() {
    mount()
    fireEvent.click(screen.getByTestId('gs-agent-generate'))
    await screen.findByTestId('gs-agent-command')
    return screen.getByRole('button', { name: /copy command/i })
  }

  it('says Copied only after the clipboard accepted it', async () => {
    setEnv({ secure: true, clipboard: { writeText: vi.fn().mockRejectedValue(new Error('denied')) }, execCommand: vi.fn(() => false) })
    const btn = await generated()
    fireEvent.click(btn)
    await waitFor(() => expect(screen.getByTestId('gs-copy-live').textContent).toMatch(/select/i))
    expect(btn.textContent).not.toMatch(/^Copied/)
  })

  it('falls back to execCommand on http:// and says Copied when it worked', async () => {
    setEnv({ secure: false, clipboard: undefined, execCommand: vi.fn(() => true) })
    const btn = await generated()
    fireEvent.click(btn)
    await waitFor(() => expect(btn.textContent).toMatch(/Copied/))
  })
})

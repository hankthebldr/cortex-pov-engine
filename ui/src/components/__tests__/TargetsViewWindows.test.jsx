/**
 * TargetsView — what the Agents surface tells a DC about Windows, liveness and
 * removal, before a customer is watching.
 *
 * What was observably wrong on base (cc26ddf):
 *   - Choosing Windows always said "No Windows beacon ships yet … use the push
 *     bundle", although the image ships windows/amd64 and the installer only
 *     refuses (WINDOWS_AGENT_UNAVAILABLE) on a SimCore built without it.
 *   - The loopback warning cited INSTALLER_SERVER_LOOPBACK, a code that exists
 *     nowhere in SimCore; the endpoint deliberately does not refuse loopback.
 *   - The pill was a browser-clock 60 s threshold with no offline state, so a
 *     row SimCore calls stale or offline read "live" on a skewed laptop, and a
 *     row the installer had only ENROLLED read "live" before any beacon polled.
 *   - installerOS used /win/i, so a macOS agent's "↓ installer" fetched the
 *     PowerShell script.
 *   - "Delete" implied the beacon was gone; it removes the registry row and a
 *     running beacon re-registers on its next poll.
 *
 * jsdom only — real layout is not exercised here.
 */
import React from 'react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, within, cleanup } from '@testing-library/react'
import { installRoutes } from '../../test/mockFetch.js'
import { EnvironmentContext, DEFAULT_ENV } from '../../context/EnvironmentContext.jsx'
import { buildHealthModel } from '../console/readiness/healthModel.js'
import TargetsView from '../console/TargetsView.jsx'
import { WINDOWS_MANUAL_UNINSTALL } from '../console/deployCommands.js'

void React

const MINTED = {
  id: 7,
  token: 'cxs_WinT0kenAbCdEfGhIjKlMnOpQrStUvWx',
  label: null,
  created_at: '2026-11-05T10:00:00',
  expires_at: '2026-11-05T11:00:00',
  max_uses: 1,
  used_count: 0,
  remaining_uses: 1,
  revoked: false,
}

// jsdom's origin under this vitest config (http://localhost:3000 today).
const ORIGIN = window.location.origin
const TLS = '[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072; '

// The server's shape: `missing[]` speaks for <goos>/amd64 only, `available[]`
// lists everything on the shelf.
const MATRIX = ['darwin/amd64', 'darwin/arm64', 'linux/amd64', 'linux/arm64', 'windows/amd64']
function healthWith(missing, { alsoAbsent = [] } = {}) {
  const available = MATRIX.filter((t) => !missing.includes(t) && !alsoAbsent.includes(t))
  const comp = missing.length
    ? { status: 'degraded', code: 'AGENT_DIST_INCOMPLETE', detail: `missing ${missing.join(', ')}`, missing, available }
    : { status: 'ok', code: 'OK', missing: [], available }
  return buildHealthModel({ status: missing.length ? 'degraded' : 'ok', components: { agent_binaries: comp } })
}

function mount(env = {}) {
  installRoutes({
    'GET /api/infra/bundles': { bundles: [] },
    'POST /api/agents/enroll/tokens': MINTED,
    'GET /api/agents/install/attempts': { attempts: [], total: 0, retained_max: 100 },
  })
  return render(
    <EnvironmentContext.Provider value={{ ...DEFAULT_ENV, refreshAgents: vi.fn(), ...env }}>
      <TargetsView />
    </EnvironmentContext.Provider>,
  )
}

async function openDeploy(os, env) {
  mount(env)
  fireEvent.click(screen.getAllByRole('button', { name: /Deploy agent/i })[0])
  const dialog = await screen.findByRole('dialog')
  if (os) fireEvent.click(within(dialog).getByRole('button', { name: new RegExp(os) }))
  return dialog
}

async function mintIn(dialog) {
  fireEvent.click(within(dialog).getByRole('button', { name: /Mint enrollment token/i }))
  await within(dialog).findByTestId('deploy-token')
}

describe('Deploy modal · Windows status is read, not asserted (UI-02 / DOCS-07)', () => {
  beforeEach(() => { window.localStorage.clear() })

  it('a SimCore that ships windows/amd64 does not say no Windows beacon ships', async () => {
    const dialog = await openDeploy('Windows', { healthModel: healthWith([]) })
    expect(dialog.textContent).not.toMatch(/No Windows beacon ships yet/)
    expect(dialog.textContent).not.toMatch(/WINDOWS_AGENT_UNAVAILABLE/)
  })

  it('instead it states the true Windows caveats', async () => {
    const dialog = await openDeploy('Windows', { healthModel: healthWith([]) })
    const caveats = within(dialog).getByTestId('deploy-windows-caveats')
    expect(caveats.textContent).toMatch(/not yet proven on a Windows host/i)
    expect(caveats.textContent).toContain('IDENTITY NOT HONOURED')
    expect(caveats.textContent).toContain('C:\\ProgramData\\CortexSim\\cortexsim-agent.exe')
    expect(caveats.textContent).toMatch(/unsigned/i)
    expect(caveats.textContent).toContain('CORTEXSIM_ALLOW_UNSUPERVISED=1')
  })

  it('a SimCore built without the windows beacon says so, as a packaging gap', async () => {
    const dialog = await openDeploy('Windows', { healthModel: healthWith(['windows/amd64']) })
    const gap = within(dialog).getByTestId('deploy-windows-missing')
    expect(gap.textContent).toMatch(/this SimCore was built without the windows beacon — rebuild with make agent-dist/i)
    expect(gap.textContent).toContain('WINDOWS_AGENT_UNAVAILABLE')
    expect(within(dialog).queryByTestId('deploy-windows-caveats')).toBeNull()
  })

  it('a SimCore that does not report agent_binaries is "cannot confirm", not "ships"', async () => {
    const dialog = await openDeploy('Windows', { healthModel: null })
    expect(within(dialog).getByTestId('deploy-windows-caveats').textContent)
      .toMatch(/cannot confirm/i)
  })

  it('the Linux branch carries none of the Windows copy', async () => {
    const dialog = await openDeploy(null, { healthModel: healthWith(['windows/amd64']) })
    expect(within(dialog).queryByTestId('deploy-windows-missing')).toBeNull()
    expect(within(dialog).queryByTestId('deploy-windows-caveats')).toBeNull()
    expect(within(dialog).queryByTestId('deploy-posix-partial')).toBeNull()
  })

  it('macOS on an amd64-only shelf names the Apple-silicon gap the modal used to be silent about', async () => {
    const dialog = await openDeploy('macOS', { healthModel: healthWith([], { alsoAbsent: ['darwin/arm64', 'linux/arm64'] }) })
    const gap = within(dialog).getByTestId('deploy-posix-partial')
    expect(gap.textContent).toContain('darwin/arm64 (Apple silicon)')
    expect(gap.textContent).toContain('darwin/amd64 (Intel)')
    expect(gap.textContent).toMatch(/build from source/)
  })
})

describe('Deploy modal · copy that matches the code', () => {
  it('the loopback warning does not cite a code SimCore never emits', async () => {
    // jsdom's origin is a localhost URL, so the warning renders here.
    const dialog = await openDeploy(null, { healthModel: healthWith([]) })
    expect(within(dialog).getByTestId('deploy-loopback-warn')).toBeTruthy()
    expect(dialog.textContent).not.toContain('INSTALLER_SERVER_LOOPBACK')
  })

  it('the prerequisites are per-OS — Windows needs no curl', async () => {
    let dialog = await openDeploy(null, { healthModel: healthWith([]) })
    expect(within(dialog).getByTestId('deploy-prereqs').textContent).toMatch(/curl/)
    fireEvent.click(within(dialog).getByRole('button', { name: /Windows/ }))
    dialog = screen.getByRole('dialog')
    const prereqs = within(dialog).getByTestId('deploy-prereqs').textContent
    expect(prereqs).not.toMatch(/curl/)
    expect(prereqs).toMatch(/Windows PowerShell 5\.1/)
  })
})

describe('Deploy modal · the Windows command a DC actually pastes', () => {
  it('opts into TLS 1.2 before iwr and keeps the token in an env var (INST-12)', async () => {
    const dialog = await openDeploy('Windows', { healthModel: healthWith([]) })
    await mintIn(dialog)
    expect(within(dialog).getByTestId('deploy-one-liner').textContent).toBe(
      `${TLS}$env:CORTEXSIM_TOKEN='${MINTED.token}'; iwr -useb "${ORIGIN}/api/agents/install?os=windows&interval=10" | iex`,
    )
  })

  it('labels the snippet with the elevation it needs (UI-11)', async () => {
    const dialog = await openDeploy('Windows', { healthModel: healthWith([]) })
    await mintIn(dialog)
    expect(within(dialog).getByText('Open Windows PowerShell as Administrator, then paste')).toBeTruthy()
  })

  it('beside the .ps1 download, gives the exact line that runs it (UI-15)', async () => {
    const dialog = await openDeploy('Windows', { healthModel: healthWith([]) })
    await mintIn(dialog)
    expect(within(dialog).getByTestId('deploy-download-run').textContent).toBe(
      `Unblock-File .\\install-cortexsim-agent.ps1; $env:CORTEXSIM_TOKEN='${MINTED.token}'; `
      + 'powershell -NoProfile -ExecutionPolicy Bypass -File .\\install-cortexsim-agent.ps1',
    )
    expect(dialog.textContent).toMatch(/without the token it falls back to the legacy shared id/i)
  })

  it('the legacy Windows line keeps the TLS opt-in too', async () => {
    const dialog = await openDeploy('Windows', { healthModel: healthWith([]) })
    fireEvent.click(within(dialog).getByRole('button', { name: /Legacy: self-asserted agent id/i }))
    expect(within(dialog).getByTestId('deploy-legacy-one-liner').textContent).toBe(
      `${TLS}iwr -useb "${ORIGIN}/api/agents/install?os=windows&interval=10&id=jumpbox-01" | iex`,
    )
  })
})

const naiveUtc = (msAgo) => new Date(Date.now() - msAgo).toISOString().replace('Z', '')

describe('Agent cards · server liveness, not a browser-clock guess (UI-13 / UI-05)', () => {
  function card(id) { return screen.getByText(id).closest('.target-card') }

  it('renders SimCore\'s offline, with an offline pill', () => {
    mount({ agents: [{ agent_id: 'jb-off', hostname: 'jb', os: 'linux', status: 'offline', last_seen: naiveUtc(900_000), last_seen_age_seconds: 900 }] })
    const c = card('jb-off')
    expect(c.querySelector('.target-card__pill--offline')).not.toBeNull()
    expect(c.querySelector('.target-card__pill--live')).toBeNull()
  })

  it('trusts SimCore\'s stale and its age over a skewed browser clock', () => {
    // The laptop clock says 2 s ago; SimCore measured 95 s and said stale.
    mount({ agents: [{ agent_id: 'jb-skew', hostname: 'jb', os: 'linux', status: 'stale', last_seen: naiveUtc(2_000), last_seen_age_seconds: 95 }] })
    const c = card('jb-skew')
    expect(c.querySelector('.target-card__pill--stale')).not.toBeNull()
    expect(c.querySelector('.target-card__pill--live')).toBeNull()
    expect(c.textContent).toContain('seen 1m ago')
  })

  it('a row the installer only enrolled is "enrolled — waiting for first beacon poll", not live', () => {
    const t = naiveUtc(3_000)
    mount({ agents: [{ agent_id: 'jb-new', hostname: 'jb', os: 'linux', status: 'online', registered_at: t, last_seen: t, last_seen_age_seconds: 3 }] })
    const c = card('jb-new')
    expect(c.querySelector('.target-card__pill--enrolled')).not.toBeNull()
    expect(c.querySelector('.target-card__pill--live')).toBeNull()
    expect(c.textContent).toContain('enrolled — waiting for first beacon poll')
  })

  it('a Windows row without the powershell capability is enrolled, with one that has it online', () => {
    mount({ agents: [
      { agent_id: 'win-enr', hostname: 'W1', os: 'windows', status: 'online', capabilities: ['shell'], registered_at: naiveUtc(20_000), last_seen: naiveUtc(4_000), last_seen_age_seconds: 4 },
      { agent_id: 'win-run', hostname: 'W2', os: 'windows', status: 'online', capabilities: ['shell', 'powershell', 'artifact-fetch'], registered_at: naiveUtc(20_000), last_seen: naiveUtc(4_000), last_seen_age_seconds: 4 },
    ] })
    expect(card('win-enr').querySelector('.target-card__pill--enrolled')).not.toBeNull()
    expect(card('win-run').querySelector('.target-card__pill--live')).not.toBeNull()
  })
})

describe('Agent cards · installer OS is an exact match (UI-12)', () => {
  it.each([
    ['darwin', 'darwin'],
    ['macos', 'darwin'],
    ['windows', 'windows'],
    ['win32', 'windows'],
    ['linux', 'linux'],
  ])('a %s agent re-downloads the %s installer', (os, expected) => {
    mount({ agents: [{ agent_id: `a-${os}`, hostname: 'h', os, status: 'online', last_seen_age_seconds: 1 }] })
    const href = within(card(`a-${os}`)).getByRole('link', { name: /installer/ }).getAttribute('href')
    expect(new URL(href, 'http://localhost').searchParams.get('os')).toBe(expected)
  })

  function card(id) { return screen.getByText(id).closest('.target-card') }
})

describe('Agent cards · delete says what it does and how to really remove a beacon (UI-10)', () => {
  function confirmFor(agent) {
    mount({ agents: [agent] })
    const c = screen.getByText(agent.agent_id).closest('.target-card')
    fireEvent.click(within(c).getByRole('button', { name: /delete/i }))
    // Same element: the card keeps its key across the confirm re-render.
    return c
  }

  it('Linux: registry row only, beacon re-registers, and the removal line runs as the installing user (RG-UI-02)', () => {
    const c = confirmFor({ agent_id: 'jb-l', hostname: 'h', os: 'linux', status: 'online', last_seen_age_seconds: 1 })
    expect(c.textContent).toMatch(/removes the registry row only/i)
    expect(c.textContent).toMatch(/re-registers/i)
    // No sudo: under sudo the script resolves per-user paths from root's
    // environment, misses the per-user unit and still prints "done".
    expect(within(c).getByTestId('agent-uninstall-cmd').textContent)
      .toBe(`curl -fsSL '${ORIGIN}/api/agents/install?uninstall=1' | bash`)
    const note = within(c).getByTestId('agent-uninstall-note').textContent
    expect(note).toMatch(/same user who ran the installer/i)
    expect(note).toContain('NEEDS_ROOT')
    expect(within(c).getByTestId('agent-uninstall-root-cmd').textContent)
      .toBe(`curl -fsSL '${ORIGIN}/api/agents/install?uninstall=1' | sudo bash`)
  })

  it('macOS gets the same per-user removal line; Windows gets no POSIX note', () => {
    let c = confirmFor({ agent_id: 'mac-l', hostname: 'm', os: 'darwin', status: 'online', last_seen_age_seconds: 1 })
    expect(within(c).getByTestId('agent-uninstall-cmd').textContent)
      .toBe(`curl -fsSL '${ORIGIN}/api/agents/install?uninstall=1' | bash`)
    cleanup()
    c = confirmFor({ agent_id: 'win-n', hostname: 'W', os: 'windows', status: 'online', capabilities: ['shell', 'powershell'], last_seen_age_seconds: 1 })
    expect(within(c).queryByTestId('agent-uninstall-note')).toBeNull()
  })

  it('Windows: the PowerShell removal line plus the manual fallback', () => {
    const c = confirmFor({ agent_id: 'win-l', hostname: 'W', os: 'windows', status: 'online', capabilities: ['shell', 'powershell'], last_seen_age_seconds: 1 })
    expect(within(c).getByTestId('agent-uninstall-cmd').textContent)
      .toBe(`${TLS}iwr -useb '${ORIGIN}/api/agents/install?os=windows&uninstall=1' | iex`)
    expect(within(c).getByTestId('agent-uninstall-manual').textContent)
      .toBe(WINDOWS_MANUAL_UNINSTALL)
  })
})

describe('Agent cards · a refused delete says why and offers the abort (OPS-08)', () => {
  // DELETE /api/agents/{id} answers 409 AGENT_HAS_OPEN_RUNS while a run is
  // owed to the agent or in flight on it. removeAgent used to swallow every
  // error ("surfaced via list refresh"), and the list does not change on a
  // refusal, so Delete row looked like a button that did nothing.
  const REFUSAL = {
    detail: {
      error: 'Agent has runs that are not finished',
      code: 'AGENT_HAS_OPEN_RUNS',
      detail: "agent 'jb-stuck' is still owed, or is running, 1 run(s): run-abc. Deleting it now would leave them running with no agent to deliver to and nothing to time them out. Let each one finish or abort it (POST /api/runs/run-abc/abort), then delete the agent.",
      run_ids: ['run-abc'],
    },
  }

  function mountWith(routes) {
    installRoutes({
      'GET /api/infra/bundles': { bundles: [] },
      'GET /api/agents/install/attempts': { attempts: [], total: 0, retained_max: 100 },
      ...routes,
    })
    const refreshAgents = vi.fn()
    render(
      <EnvironmentContext.Provider value={{ ...DEFAULT_ENV, refreshAgents, agents: [
        { agent_id: 'jb-stuck', hostname: 'h', os: 'linux', status: 'offline', last_seen_age_seconds: 900 },
      ] }}>
        <TargetsView />
      </EnvironmentContext.Provider>,
    )
    const c = screen.getByText('jb-stuck').closest('.target-card')
    fireEvent.click(within(c).getByRole('button', { name: /delete/i }))
    fireEvent.click(within(c).getByRole('button', { name: /Delete row/i }))
    return { c, refreshAgents }
  }

  it('renders the server detail and each open run, with an abort control', async () => {
    let aborted = null
    mountWith({
      'DELETE /api/agents/jb-stuck': () => new Response(JSON.stringify(REFUSAL), {
        status: 409, headers: { 'content-type': 'application/json' },
      }),
      'POST /api/runs/run-abc/abort': (url) => { aborted = url; return { status: 'aborted', run_id: 'run-abc' } },
    })
    const alert = await screen.findByTestId('agent-delete-refused')
    expect(alert.getAttribute('role')).toBe('alert')
    expect(alert.textContent).toContain('AGENT_HAS_OPEN_RUNS')
    expect(alert.textContent).toMatch(/still owed, or is running, 1 run/)
    const link = within(alert).getByRole('link', { name: 'run-abc' })
    expect(link.getAttribute('href')).toBe('#/runs?run=run-abc')
    fireEvent.click(within(alert).getByRole('button', { name: /Abort run-abc/i }))
    await within(alert).findByText(/run-abc aborted/i)
    expect(aborted).toMatch(/\/api\/runs\/run-abc\/abort$/)
  })

  it('any other refusal is shown, not swallowed', async () => {
    mountWith({
      'DELETE /api/agents/jb-stuck': () => new Response(JSON.stringify({ error: 'boom', code: 'INTERNAL' }), {
        status: 500, headers: { 'content-type': 'application/json' },
      }),
    })
    const alert = await screen.findByTestId('agent-delete-refused')
    expect(alert.textContent).toMatch(/boom/)
  })
})

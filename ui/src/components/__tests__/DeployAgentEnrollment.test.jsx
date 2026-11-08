/**
 * Agent onboarding through the ENROLLMENT-TOKEN front door.
 *
 * The documented flow (mint a revocable token → one line on the jumpbox →
 * SimCore assigns the id) had zero UI: the console shipped only the legacy
 * self-asserted `?id=` installer and `grep -rni enroll ui/src` found nothing
 * but a comment. These tests pin the console-side flow to the real API.
 */
import React from 'react'
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { installRoutes } from '../../test/mockFetch.js'
import TargetsView from '../console/TargetsView.jsx'

void React

const MINTED = {
  id: 3,
  token: 'cxs_HvJ2n8Qq0wZ4pL7yT1xR9bK3mC6dF5sA',
  label: 'acme-pov jumpbox',
  expires_at: '2026-08-01T12:00:00',
  max_uses: 1,
  used_count: 0,
  remaining_uses: 1,
  revoked: false,
}

function routes(overrides = {}) {
  return installRoutes({
    'GET /api/agents': { agents: [] },
    'GET /api/infra/bundles': { bundles: [] },
    'POST /api/agents/enroll/tokens': MINTED,
    ...overrides,
  })
}

describe('TargetsView — enrollment-token deploy flow', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    routes()
  })
  afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks() })

  async function openDeploy() {
    render(<TargetsView />)
    const openers = await screen.findAllByRole('button', { name: /Deploy agent/i })
    fireEvent.click(openers[0])
    return screen.findByRole('dialog')
  }

  it('offers minting from the cold-start empty state, not just a buried button', async () => {
    render(<TargetsView />)
    // The cold-start card must itself carry the CTA — a first-time DC should
    // not have to find the small column action to get their first beacon up.
    await screen.findByText('No agents registered')
    await waitFor(() =>
      expect(screen.getAllByRole('button', { name: /Deploy agent/i }).length)
        .toBeGreaterThanOrEqual(2))
    expect(screen.getByText(/Mint an enrollment token/i)).toBeTruthy()
  })

  it('mints a token via POST /api/agents/enroll/tokens and reveals it once', async () => {
    const fetchMock = routes()
    await openDeploy()
    fireEvent.click(screen.getByRole('button', { name: /Mint enrollment token/i }))

    const tokenEl = await screen.findByTestId('deploy-token')
    expect(tokenEl.textContent).toBe(MINTED.token)

    const call = fetchMock.mock.calls.find(([u, i]) =>
      String(u).includes('/api/agents/enroll/tokens') && i?.method === 'POST')
    expect(call).toBeTruthy()
    expect(JSON.parse(call[1].body)).toMatchObject({ ttl_seconds: 3600, max_uses: 1 })
  })

  it('emits a one-liner that carries the token and lets SimCore assign the id', async () => {
    await openDeploy()
    fireEvent.click(screen.getByRole('button', { name: /Mint enrollment token/i }))

    const line = (await screen.findByTestId('deploy-one-liner')).textContent
    expect(line).toContain('CORTEXSIM_TOKEN=')
    expect(line).toContain(MINTED.token)
    expect(line).toContain('/api/agents/install')
    // The self-asserted id is exactly what the token flow removes.
    expect(line).not.toContain('id=jumpbox-01')
  })

  it('keeps the token out of the URL (shell history / proxy logs)', async () => {
    await openDeploy()
    fireEvent.click(screen.getByRole('button', { name: /Mint enrollment token/i }))
    const line = (await screen.findByTestId('deploy-one-liner')).textContent
    const url = line.match(/https?:\/\/[^\s"']+/)[0]
    expect(url).not.toContain(MINTED.token)
  })

  it('honours the chosen TTL and host count', async () => {
    const fetchMock = routes()
    await openDeploy()
    fireEvent.change(screen.getByLabelText(/Valid for/i), { target: { value: '86400' } })
    fireEvent.change(screen.getByLabelText(/Max uses/i), { target: { value: '25' } })
    fireEvent.click(screen.getByRole('button', { name: /Mint enrollment token/i }))

    await screen.findByTestId('deploy-token')
    const call = fetchMock.mock.calls.find(([u, i]) =>
      String(u).includes('/api/agents/enroll/tokens') && i?.method === 'POST')
    expect(JSON.parse(call[1].body)).toMatchObject({ ttl_seconds: 86400, max_uses: 25 })
  })

  it('surfaces a mint failure instead of leaving a dead button', async () => {
    routes({
      'POST /api/agents/enroll/tokens': () => new Response(
        JSON.stringify({ detail: { error: 'Vault locked', code: 'VAULT_LOCKED' } }),
        { status: 503, headers: { 'content-type': 'application/json' } },
      ),
    })
    await openDeploy()
    fireEvent.click(screen.getByRole('button', { name: /Mint enrollment token/i }))

    const err = await screen.findByRole('alert')
    expect(err.textContent).toContain('Vault locked')
    expect(err.textContent).toContain('VAULT_LOCKED')
  })

  it('still offers the legacy explicit-id installer for hosts that cannot enroll', async () => {
    await openDeploy()
    fireEvent.click(screen.getByRole('button', { name: /Legacy: self-asserted agent id/i }))
    expect(screen.getByPlaceholderText('jumpbox-01')).toBeTruthy()
  })

  it('drops the token from state when the dialog closes', async () => {
    await openDeploy()
    fireEvent.click(screen.getByRole('button', { name: /Mint enrollment token/i }))
    await screen.findByTestId('deploy-token')

    fireEvent.click(screen.getByRole('button', { name: /Close/i }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())

    const openers = screen.getAllByRole('button', { name: /Deploy agent/i })
    fireEvent.click(openers[0])
    await screen.findByRole('dialog')
    expect(screen.queryByTestId('deploy-token')).toBeNull()
  })
})

// ── Install telemetry in the modal (UI-06) ───────────────────────────────────
// The installer POSTs a stable code at every stage and SimCore keeps the last
// 100 in memory — but nothing in the deploy modal read it, so a DC whose
// one-liner died at ENROLL_DENIED watched "appears within ~10s" forever.
const MINTED_AT = { ...MINTED, created_at: '2026-11-05T10:00:00' }
const attempt = (o) => ({ stage: 'enroll', code: 'OK', detail: '', hostname: 'jb', os: 'linux', arch: 'amd64', agent_id: null, ...o })

describe('TargetsView — install attempts while the deploy modal is open', () => {
  beforeEach(() => { vi.useFakeTimers({ shouldAdvanceTime: true }) })
  afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks() })

  function withAttempts(list) {
    return routes({
      'POST /api/agents/enroll/tokens': MINTED_AT,
      'GET /api/agents/install/attempts': { attempts: list, total: list.length, retained_max: 100 },
    })
  }
  async function openAndMint() {
    render(<TargetsView />)
    fireEvent.click((await screen.findAllByRole('button', { name: /Deploy agent/i }))[0])
    const dialog = await screen.findByRole('dialog')
    fireEvent.click(screen.getByRole('button', { name: /Mint enrollment token/i }))
    await screen.findByTestId('deploy-token')
    return dialog
  }

  it('a failure reported after the mint replaces the "appears within ~10s" promise, as an alert', async () => {
    withAttempts([
      attempt({ stage: 'enroll', code: 'ENROLL_DENIED', detail: 'HTTP 403 Forbidden', reported_at: '2026-11-05T10:00:07' }),
      attempt({ stage: 'detect', code: 'OK', reported_at: '2026-11-05T10:00:05' }),
    ])
    await openAndMint()
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('ENROLL_DENIED')
    expect(alert.textContent).toContain('enroll')
    expect(alert.textContent).toContain('HTTP 403 Forbidden')
    expect(screen.queryByText(/appears in this list within one poll interval/)).toBeNull()
  })

  it('OK and DEGRADED_NO_SUPERVISOR are reported as status, not alerts', async () => {
    withAttempts([attempt({ stage: 'install', code: 'DEGRADED_NO_SUPERVISOR', detail: 'not elevated', reported_at: '2026-11-05T10:00:09' })])
    await openAndMint()
    const live = await screen.findByTestId('deploy-attempt-latest')
    expect(live.getAttribute('role')).toBe('status')
    expect(live.textContent).toContain('DEGRADED_NO_SUPERVISOR')
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('a failure from BEFORE this token was minted is not presented as its outcome', async () => {
    withAttempts([attempt({ stage: 'verify', code: 'CHECKSUM_MISMATCH', reported_at: '2026-11-05T09:58:00' })])
    await openAndMint()
    // It is still in the history list, just not the live verdict.
    const list = await screen.findByTestId('deploy-attempts-list')
    expect(list.textContent).toContain('CHECKSUM_MISMATCH')
    expect(screen.queryByRole('alert')).toBeNull()
    expect(screen.queryByTestId('deploy-attempt-latest')).toBeNull()
  })

  it('lists recent attempts and says the list is in-memory and resets on restart', async () => {
    withAttempts([attempt({ stage: 'download', code: 'BINARY_FETCH_FAILED', reported_at: '2026-11-05T10:00:04' })])
    await openAndMint()
    const section = await screen.findByTestId('deploy-attempts')
    expect(section.textContent).toMatch(/Recent install attempts/)
    expect(section.textContent).toMatch(/in-memory/i)
    expect(section.textContent).toMatch(/restart/i)
  })

  it('polls every 5 s while open, and stops when the modal closes', async () => {
    const fetchMock = withAttempts([])
    render(<TargetsView />)
    fireEvent.click((await screen.findAllByRole('button', { name: /Deploy agent/i }))[0])
    await screen.findByRole('dialog')
    const calls = () => fetchMock.mock.calls.filter(([u]) => String(u).includes('/api/agents/install/attempts')).length
    await waitFor(() => expect(calls()).toBe(1))
    vi.advanceTimersByTime(5000)
    await waitFor(() => expect(calls()).toBe(2))
    fireEvent.click(screen.getByRole('button', { name: /Close/i }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    vi.advanceTimersByTime(20_000)
    expect(calls()).toBe(2)
  })

  it('an older SimCore without the endpoint is named as a gap, not "no attempts"', async () => {
    routes({ 'POST /api/agents/enroll/tokens': MINTED_AT }) // attempts route unmocked → 404
    await openAndMint()
    const section = await screen.findByTestId('deploy-attempts')
    await waitFor(() => expect(section.textContent).toMatch(/does not report install attempts/i))
  })
})

// ── Copy that only says "copied" when it did (UI-14) ─────────────────────────
// Base called navigator.clipboard.writeText without awaiting it and flipped to
// "✓ copied" immediately; on http:// (no Clipboard API) it threw, was swallowed,
// and the operator got no signal at all. jsdom only — no real clipboard here.
describe('TargetsView — copy buttons', () => {
  const restore = []
  function setEnv({ secure, clipboard, execCommand }) {
    Object.defineProperty(window, 'isSecureContext', { configurable: true, value: secure })
    Object.defineProperty(window.navigator, 'clipboard', { configurable: true, value: clipboard })
    document.execCommand = execCommand
    restore.push(() => { delete window.isSecureContext; delete window.navigator.clipboard; delete document.execCommand })
  }
  beforeEach(() => { vi.useFakeTimers({ shouldAdvanceTime: true }); routes() })
  afterEach(() => { while (restore.length) restore.pop()(); vi.useRealTimers(); vi.restoreAllMocks() })

  async function mintedDialog() {
    render(<TargetsView />)
    fireEvent.click((await screen.findAllByRole('button', { name: /Deploy agent/i }))[0])
    await screen.findByRole('dialog')
    fireEvent.click(screen.getByRole('button', { name: /Mint enrollment token/i }))
    await screen.findByTestId('deploy-one-liner')
    return screen.getByTestId('deploy-one-liner').parentElement.querySelector('button')
  }

  it('waits for writeText before saying copied', async () => {
    let resolve
    const writeText = vi.fn(() => new Promise((r) => { resolve = r }))
    setEnv({ secure: true, clipboard: { writeText }, execCommand: vi.fn(() => false) })
    const btn = await mintedDialog()
    fireEvent.click(btn)
    await waitFor(() => expect(writeText).toHaveBeenCalled())
    expect(btn.textContent).not.toContain('✓ copied')
    resolve()
    await waitFor(() => expect(btn.textContent).toContain('✓ copied'))
    expect(screen.getByTestId('deploy-copy-live').textContent).toMatch(/copied/i)
  })

  it('a rejected writeText with no working fallback never says copied', async () => {
    setEnv({ secure: true, clipboard: { writeText: vi.fn().mockRejectedValue(new Error('denied')) }, execCommand: vi.fn(() => false) })
    const btn = await mintedDialog()
    fireEvent.click(btn)
    await waitFor(() => expect(screen.getByTestId('deploy-copy-live').textContent).toMatch(/select/i))
    expect(btn.textContent).not.toContain('✓ copied')
  })

  it('on http:// it falls back to execCommand and reports real success', async () => {
    const execCommand = vi.fn(() => true)
    setEnv({ secure: false, clipboard: undefined, execCommand })
    const btn = await mintedDialog()
    fireEvent.click(btn)
    await waitFor(() => expect(btn.textContent).toContain('✓ copied'))
    expect(execCommand).toHaveBeenCalledWith('copy')
  })

  it('when nothing can copy, says so and selects the command for the operator', async () => {
    setEnv({ secure: false, clipboard: undefined, execCommand: vi.fn(() => false) })
    const btn = await mintedDialog()
    fireEvent.click(btn)
    await waitFor(() => expect(screen.getByTestId('deploy-copy-live').textContent)
      .toBe('Copy is unavailable on http:// — select the text'))
    expect(btn.textContent).not.toContain('✓ copied')
    expect(window.getSelection().toString()).toBe(screen.getByTestId('deploy-one-liner').textContent)
  })
})

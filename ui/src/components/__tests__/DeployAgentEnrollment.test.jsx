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

import React from 'react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'

import TenantManager from '../console/TenantManager.jsx'
import { EnvironmentContext, DEFAULT_ENV } from '../../context/EnvironmentContext.jsx'
import { installRoutes } from '../../test/mockFetch.js'

// ── Fixtures ─────────────────────────────────────────────────────────────────
const TENANTS = [
  {
    name: 'acme-prod',
    config: { base_url: 'https://api-acme.xdr.us.paloaltonetworks.com', region: 'us', auth_mode: 'standard', api_key_id: '42' },
    last_verified_ok: true,
    last_verified_at: '2026-07-20T10:00:00Z',
  },
  {
    name: 'beta-lab',
    config: { base_url: 'https://api-beta.xdr.eu.paloaltonetworks.com', region: 'eu', auth_mode: 'standard', api_key_id: '7' },
    last_verified_ok: false,
    last_verified_error: 'timeout',
  },
]

/**
 * Render TenantManager inside a controllable EnvironmentContext so we can assert
 * the shared active-tenant contract (setTenant / refreshTenants) without wiring
 * the whole provider. The active tenant is derived from `activeName`.
 */
function renderWithEnv(overrides = {}) {
  const setTenant = vi.fn()
  const refreshTenants = vi.fn(() => Promise.resolve())
  const tenants = overrides.tenants ?? TENANTS
  const activeName = overrides.activeName === undefined ? 'acme-prod' : overrides.activeName
  const activeTenant = tenants.find((t) => t.name === activeName) || null

  const value = {
    ...DEFAULT_ENV,
    tenants,
    tenant: activeTenant,
    setTenant,
    refreshTenants,
    loading: { ...DEFAULT_ENV.loading, tenants: false },
    ...overrides.value,
  }

  const utils = render(
    <EnvironmentContext.Provider value={value}>
      <TenantManager />
    </EnvironmentContext.Provider>,
  )
  return { ...utils, setTenant, refreshTenants, value }
}

// Walk the register wizard with a valid tenant.
function completeWizard({ name = 'gamma-prod' } = {}) {
  fireEvent.change(screen.getByPlaceholderText('acme-prod'), { target: { value: name } })
  fireEvent.change(screen.getByPlaceholderText(/api-<tenant>/), {
    target: { value: 'https://api-gamma.xdr.us.paloaltonetworks.com' },
  })
  fireEvent.change(screen.getByPlaceholderText('us'), { target: { value: 'us' } })
  fireEvent.click(screen.getByRole('button', { name: /Next/ }))

  fireEvent.change(screen.getByPlaceholderText('42'), { target: { value: '99' } })
  fireEvent.change(screen.getByPlaceholderText(/Paste API key/), { target: { value: 'super-secret-key-value' } })
  fireEvent.click(screen.getByRole('button', { name: /Review/ }))
}

describe('TenantManager (context-driven)', () => {
  beforeEach(() => {
    installRoutes({
      'GET /api/credentials/integrations': TENANTS,
      'PUT /api/credentials/integrations': { name: 'gamma-prod', kind: 'xsiam_tenant' },
      'DELETE /api/credentials/integrations/:name': null,
      'POST /api/xsiam/tenants/:name/test': { ok: true, status: { status: 'ok' } },
    })
  })

  it('renders registered tenants from the provider', () => {
    renderWithEnv()
    // acme-prod is the active tenant, so it appears in both the header scope
    // line and its list row — assert presence, not uniqueness.
    expect(screen.getAllByText('acme-prod').length).toBeGreaterThan(0)
    expect(screen.getByText('beta-lab')).toBeInTheDocument()
  })

  it('marks the active tenant and does not offer a Set-active button for it', () => {
    renderWithEnv({ activeName: 'acme-prod' })
    // The active tenant shows an ACTIVE badge and the header reflects the scope.
    expect(screen.getByLabelText('Active tenant')).toBeInTheDocument()
    expect(screen.getAllByText('acme-prod').length).toBeGreaterThan(0)
    // Only the non-active tenant offers a Set-active affordance.
    const setActive = screen.getAllByRole('button', { name: /Set .* as active tenant/ })
    expect(setActive).toHaveLength(1)
    expect(setActive[0]).toHaveAttribute('aria-label', 'Set beta-lab as active tenant')
  })

  it('Set active writes through to provider.setTenant (shared with header)', () => {
    const { setTenant } = renderWithEnv({ activeName: 'acme-prod' })
    fireEvent.click(screen.getByRole('button', { name: 'Set beta-lab as active tenant' }))
    expect(setTenant).toHaveBeenCalledWith('beta-lab')
  })

  it('reflects a null active scope in the header', () => {
    renderWithEnv({ activeName: null })
    expect(screen.getByText('none selected')).toBeInTheDocument()
    // With no active tenant, both rows expose a Set-active button.
    expect(screen.getAllByRole('button', { name: /Set .* as active tenant/ })).toHaveLength(2)
  })

  it('save-without-testing registers the tenant then refreshes the shared list', async () => {
    const { refreshTenants } = renderWithEnv()
    completeWizard({ name: 'gamma-prod' })
    fireEvent.click(screen.getByRole('button', { name: /Save without testing/ }))
    await waitFor(() => expect(refreshTenants).toHaveBeenCalled())
  })

  it('registering the first tenant makes it the active scope', async () => {
    const { setTenant } = renderWithEnv({ tenants: [], activeName: null })
    completeWizard({ name: 'gamma-prod' })
    fireEvent.click(screen.getByRole('button', { name: /Save without testing/ }))
    await waitFor(() => expect(setTenant).toHaveBeenCalledWith('gamma-prod'))
  })

  it('delete flow confirms then calls the API and refreshes the shared list', async () => {
    const { refreshTenants } = renderWithEnv()
    fireEvent.click(screen.getByRole('button', { name: 'Remove tenant beta-lab' }))
    // Confirmation dialog
    const dialog = screen.getByText('Remove tenant?').closest('div')
    expect(dialog).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }))
    await waitFor(() => expect(refreshTenants).toHaveBeenCalled())
  })

  it('Test runs a liveness probe against the tenant', async () => {
    const fetchSpy = installRoutes({
      'POST /api/xsiam/tenants/:name/test': { ok: true, status: { status: 'ok' } },
    })
    renderWithEnv()
    const rows = screen.getAllByRole('button', { name: /Test/ })
    fireEvent.click(rows[0])
    await waitFor(() => {
      const called = fetchSpy.mock.calls.some(([url, init]) =>
        String(url).includes('/api/xsiam/tenants/') &&
        String(url).includes('/test') &&
        (init?.method || 'GET').toUpperCase() === 'POST',
      )
      expect(called).toBe(true)
    })
  })

  it('renders standalone (no provider) without crashing via DEFAULT_ENV', () => {
    render(<TenantManager />)
    expect(screen.getByText('XSIAM Tenants')).toBeInTheDocument()
    expect(screen.getByText('none selected')).toBeInTheDocument()
  })
})

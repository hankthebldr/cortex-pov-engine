/**
 * The Tenant surface renders the design prototype's seed (povdata/) on its
 * first three tabs. Two ways that became a claim about a real tenant:
 *
 *  1. With NO credential registered, the page fell back to the seed's "bound"
 *     row and opened on "Acme Financial — acme-prod.xdr.us, the one tenant this
 *     instance is bound to".
 *  2. With a REAL tenant bound, its name went over nine hard-coded PASS checks
 *     ("Credential accepted · PASS", "Clock agreement +0.4s") that nothing ran.
 */
import React from 'react'
import { describe, it, expect } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import TenantView from '../console/TenantView.jsx'
import { EnvironmentContext, DEFAULT_ENV } from '../../context/EnvironmentContext.jsx'

void React

function renderTenant(over = {}) {
  return render(
    <EnvironmentContext.Provider value={{ ...DEFAULT_ENV, ...over }}>
      <TenantView />
    </EnvironmentContext.Provider>,
  )
}

describe('Tenant surface — a sample is never presented as a measurement', () => {
  it('with no tenant registered, does not present the seed tenant as bound', () => {
    renderTenant({ tenant: null })
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('No tenant bound')
    expect(document.body.textContent).not.toMatch(/acme-prod\.xdr\.us\s*— the one tenant this instance is bound to/)
  })

  it('with a real tenant bound, leads with its measured state and marks the nine checks as sample', () => {
    renderTenant({
      tenant: { name: 'customer-prod', config: { base_url: 'https://api-customer.xdr.us' } },
      health: { ...DEFAULT_ENV.health, tenantHealth: { name: 'customer-prod', last_verified_ok: null, last_verified_at: null } },
    })
    const measured = screen.getByTestId('tenant-measured-health')
    expect(measured.textContent).toMatch(/Never verified/)
    expect(within(measured).queryByText('PASS')).toBeNull()

    const notice = screen.getByTestId('tenant-sample-notice')
    expect(notice.textContent).toMatch(/not measured/i)
    expect(notice.textContent).toContain('customer-prod')
    // The seeded "Credential accepted · PASS" row comes AFTER the notice, not
    // before it as if it were this tenant's result.
    const cred = screen.getByText('Credential accepted')
    expect(notice.compareDocumentPosition(cred) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('a failed liveness probe reads FAIL with the server\'s reason', () => {
    renderTenant({
      tenant: { name: 'customer-prod' },
      health: { ...DEFAULT_ENV.health, tenantHealth: { last_verified_ok: false, last_verified_at: '2026-10-04T12:00:00', last_verified_error: '401 Unauthorized' } },
    })
    const measured = screen.getByTestId('tenant-measured-health')
    expect(measured.textContent).toContain('FAIL')
    expect(measured.textContent).toContain('401 Unauthorized')
  })
})

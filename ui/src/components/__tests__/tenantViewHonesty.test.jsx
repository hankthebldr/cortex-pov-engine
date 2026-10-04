/**
 * The Tenant surface must never present a design-prototype sample as a real
 * tenant. The original page fell back to the seed catalog's "bound" row (so a
 * SimCore with no credential opened on "Acme Financial — the one tenant this
 * instance is bound to") and rendered nine hard-coded PASS checks under a real
 * tenant's name that nothing ran.
 *
 * main rewrote TenantView from scratch to render real state only — the
 * registered tenants and the add-a-tenant form (TenantManager) — and deleted
 * the seed-backed tabs entirely. This guard follows the component to its new
 * shape: it pins the honesty guarantee (no seed tenant presented as bound, no
 * canned PASS) against the code that ships today, not the deleted surface.
 */
import React from 'react'
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
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
  it('with no tenant registered, says so plainly and shows no seeded tenant', () => {
    renderTenant({ tenants: [] })
    // The empty state is explicit, not a fabricated "bound" tenant.
    expect(screen.getByTestId('tenant-view').textContent).toMatch(/No tenant is connected yet/i)
    // None of the deleted seed catalog's canned content leaks back in.
    expect(document.body.textContent).not.toMatch(/Acme Financial/i)
    expect(document.body.textContent).not.toMatch(/the one tenant this instance is bound to/i)
  })

  it('does not render hard-coded PASS checks attributed to a tenant', () => {
    renderTenant({ tenants: [{ name: 'customer-prod', config: { base_url: 'https://api-customer.xdr.us' } }] })
    // The nine canned "… · PASS" rows the old page invented are gone; nothing
    // on this page asserts a check result the engine did not run.
    expect(document.body.textContent).not.toMatch(/Credential accepted/i)
    expect(document.body.textContent).not.toMatch(/Clock agreement/i)
  })
})

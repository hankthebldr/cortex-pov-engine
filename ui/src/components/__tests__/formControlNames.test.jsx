/**
 * Every form control has a programmatic name.
 *
 * The tenant credential form (Tenant and Get started), the Quick-launch
 * Identity and Agent selects and the TTP JSON editor each drew a visible
 * label that nothing pointed at: a `<label>` with no `htmlFor` and no nested
 * control, or no label at all. A screen reader announced "edit text" or
 * "combo box" with no name, and a field in error said nothing about why.
 * These tests reach each control the way assistive technology does: by its
 * accessible name.
 */
import React from 'react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import TenantManager from '../console/TenantManager.jsx'
import ScenarioInspector from '../console/ScenarioInspector.jsx'
import TtpEditorView from '../console/TtpEditorView.jsx'
import { EnvironmentContext, DEFAULT_ENV } from '../../context/EnvironmentContext.jsx'
import { installRoutes } from '../../test/mockFetch.js'

void React

function renderTenantForm(props = {}) {
  const value = {
    ...DEFAULT_ENV,
    tenants: [],
    tenant: null,
    setTenant: vi.fn(),
    refreshTenants: vi.fn(() => Promise.resolve()),
    loading: { ...DEFAULT_ENV.loading, tenants: false },
  }
  return render(
    <EnvironmentContext.Provider value={value}>
      <TenantManager {...props} />
    </EnvironmentContext.Provider>,
  )
}

describe('tenant credential form', () => {
  beforeEach(() => {
    installRoutes({ 'GET /api/credentials/integrations': [] })
  })

  it.each([[{}], [{ embedded: true }]])('every field is reached by its label (%o)', (props) => {
    renderTenantForm(props)
    fireEvent.change(screen.getByRole('textbox', { name: 'Tenant Name' }), { target: { value: 'gamma-prod' } })
    fireEvent.change(screen.getByRole('textbox', { name: 'Base URL' }), {
      target: { value: 'https://api-gamma.xdr.us.paloaltonetworks.com' },
    })
    fireEvent.change(screen.getByRole('textbox', { name: 'Region' }), { target: { value: 'us' } })
    fireEvent.click(screen.getByRole('button', { name: /Next/ }))

    expect(screen.getByRole('radiogroup', { name: 'Auth Mode' })).toBeInTheDocument()
    fireEvent.change(screen.getByRole('textbox', { name: 'API Key ID' }), { target: { value: '99' } })
    // A password input has no ARIA role, so it is reached by placeholder and
    // its accessible name (what a screen reader announces) is asserted.
    const key = screen.getByPlaceholderText(/Paste API key/)
    expect(key).toHaveAccessibleName('API Key')
    fireEvent.change(key, { target: { value: 'super-secret-key-value' } })
    fireEvent.click(screen.getByRole('button', { name: /Review/ }))

    // The values typed through the labels are the ones the review step shows.
    expect(screen.getByText('gamma-prod')).toBeInTheDocument()
    expect(screen.getByText('…alue')).toBeInTheDocument()
  })

  it('announces required fields as required, not as a trailing asterisk', () => {
    renderTenantForm()
    expect(screen.getByRole('textbox', { name: 'Tenant Name' })).toBeRequired()
    expect(screen.getByRole('textbox', { name: 'Base URL' })).toBeRequired()
  })

  it('a field in error is invalid and described by its error; fixing it clears both', () => {
    renderTenantForm()
    const baseUrl = screen.getByRole('textbox', { name: 'Base URL' })
    expect(baseUrl).not.toBeInvalid()
    expect(baseUrl).toHaveAccessibleDescription(/API base URL format/)

    fireEvent.change(baseUrl, { target: { value: 'http://plain.example' } })
    fireEvent.click(screen.getByRole('button', { name: /Next/ }))
    expect(baseUrl).toBeInvalid()
    expect(baseUrl).toHaveAccessibleDescription('Must start with https://')
    expect(screen.getByRole('textbox', { name: 'Tenant Name' })).toHaveAccessibleDescription('Lowercase letters, numbers, hyphens, underscores only')

    fireEvent.change(baseUrl, { target: { value: 'https://api-gamma.xdr.us.paloaltonetworks.com' } })
    expect(baseUrl).not.toBeInvalid()
    expect(baseUrl).toHaveAccessibleDescription(/API base URL format/)
  })
})

describe('Quick launch selects', () => {
  const launch = {
    mode: 'pull',
    identity: 'www-data',
    identityOptions: ['www-data', 'root'],
    agents: [{ id: 'a-1', hostname: 'jumpbox-01' }, { id: 'a-2', hostname: 'jumpbox-02' }],
    selectedAgent: 'a-1',
    pushFormat: 'bash',
    supportsPull: true,
    supportsPush: true,
    launching: false,
    launchDisabled: false,
    downloading: false,
    lastRun: null,
    launch: vi.fn(),
    downloadPushBundle: vi.fn(),
    setMode: () => {},
    setIdentity: vi.fn(),
    setSelectedAgent: vi.fn(),
    setPushFormat: () => {},
  }
  const scenario = { scenario_id: 'SIM-EDR-001', name: 'AWS Cred Hunt', plane: 'EDR', pull_supported: true, push_supported: true, steps: [] }

  it('Identity and Agent are reached by their labels', () => {
    render(<ScenarioInspector scenario={scenario} open launch={launch} />)
    fireEvent.change(screen.getByRole('combobox', { name: 'Identity' }), { target: { value: 'root' } })
    expect(launch.setIdentity).toHaveBeenCalledWith('root')
    fireEvent.change(screen.getByRole('combobox', { name: 'Agent' }), { target: { value: 'a-2' } })
    expect(launch.setSelectedAgent).toHaveBeenCalledWith('a-2')
  })
})

describe('TTP editor', () => {
  it('the JSON textarea has a name', async () => {
    installRoutes({ 'GET /api/ttps/_schema': { type: 'object' } })
    render(<TtpEditorView onClose={() => {}} />)
    await waitFor(() => expect(screen.getByRole('textbox', { name: 'TTP card JSON' })).toBeInTheDocument())
  })
})

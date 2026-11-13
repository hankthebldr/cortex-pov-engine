import React, { useState } from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, within, fireEvent, waitFor, cleanup } from '@testing-library/react'
import { installRoutes } from '../../../../test/mockFetch.js'
import { EnvironmentContext, DEFAULT_ENV } from '../../../../context/EnvironmentContext.jsx'
import { __resetComponentSources } from '../../useComponentStatus.js'
import AgentsComponent from '../AgentsComponent.jsx'
import TenantComponent from '../TenantComponent.jsx'
import FirewallComponent from '../FirewallComponent.jsx'

void React

/**
 * Agents, Tenant and Firewall on the one component page (design v26 §4.2,
 * slice 3): their management tabs keep today's surfaces (Fleet, Read-back &
 * integrations, Sources), and their Setup tabs are the Configure frame with
 * steps that read real state or say they cannot.
 */
// Params behave as the hash router's do: setParams merges, and the page
// re-renders with the result.
function Routed({ Comp, initial, setParamsSpy, onNavigate }) {
  const [params, setP] = useState(initial)
  const setParams = (p) => {
    setParamsSpy(p)
    setP((prev) => {
      const next = { ...prev, ...p }
      Object.keys(next).forEach((k) => { if (next[k] == null) delete next[k] })
      return next
    })
  }
  return <Comp params={params} setParams={setParams} onNavigate={onNavigate} />
}

function mount(Comp, { env = {}, params = {} } = {}) {
  const setParams = vi.fn()
  const onNavigate = vi.fn()
  const value = { ...DEFAULT_ENV, refreshAgents: vi.fn(), refreshTenants: vi.fn(), ...env }
  const utils = render(
    <EnvironmentContext.Provider value={value}>
      <Routed Comp={Comp} initial={params} setParamsSpy={setParams} onNavigate={onNavigate} />
    </EnvironmentContext.Provider>,
  )
  return { setParams, onNavigate, ...utils }
}

const panel = () => screen.getByRole('region', { name: /step/i })
const stepList = () => screen.getByRole('list', { name: /steps/i })
const ONLINE = { agent_id: 'jb-01', hostname: 'jb-01', os: 'linux', status: 'online', last_seen_age_seconds: 3 }

let fetchMock
beforeEach(() => {
  window.localStorage.clear()
  __resetComponentSources()
  fetchMock = installRoutes({
    'GET /api/collector-targets': { targets: [], count: 0 },
    'GET /api/ngfw': { firewalls: [], write_enabled: false },
    'GET /api/agents/install/attempts': { attempts: [] },
    'GET /api/infra/bundles': { bundles: [] },
    'GET /api/credentials/integrations': { integrations: [] },
    'POST /api/agents/enroll/tokens': { id: 7, token: 'cxs_testtoken123', created_at: '2026-11-13T17:00:00', ttl_seconds: 3600, max_uses: 1 },
  })
})
afterEach(cleanup)

describe('Agents component', () => {
  it('reads Off with Configure as the one primary when no agent has enrolled', () => {
    mount(AgentsComponent)
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('Agents')
    expect(screen.getByRole('img', { name: 'Off' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Configure' })).toHaveClass('kit-btn--primary')
  })

  it('lists each agent as a service with its liveness', () => {
    mount(AgentsComponent, { env: { agents: [ONLINE, { agent_id: 'w7', hostname: 'w7', os: 'windows', capabilities: ['shell', 'powershell'], status: 'offline' }] } })
    const services = screen.getByRole('region', { name: 'Services' })
    expect(within(within(services).getByRole('button', { name: /jb-01/ })).getByRole('img', { name: 'Up' })).toBeInTheDocument()
    expect(within(within(services).getByRole('button', { name: /w7/ })).getByRole('img', { name: 'Down' })).toBeInTheDocument()
  })

  it('Fleet is today\'s agent list, under the one h1', async () => {
    mount(AgentsComponent, { params: { tab: 'fleet' } })
    await waitFor(() => expect(document.querySelector('[data-tour-id="agent-enroll"]')).not.toBeNull())
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1)
  })

  it('Setup: Test on the token step creates one, and the installer command carries it', async () => {
    mount(AgentsComponent, { params: { tab: 'setup', step: 'token' } })
    expect(within(stepList()).getAllByRole('listitem').map((li) => li.textContent)).toEqual([
      expect.stringContaining('Create an enrollment token'),
      expect.stringContaining('Check the host can reach POVengine'),
      expect.stringContaining('Run the installer'),
      expect.stringContaining('Confirm the service'),
      expect.stringContaining('Check the service accounts'),
      expect.stringContaining('Confirm it is online'),
    ])
    fireEvent.click(within(panel()).getByRole('button', { name: 'Test' }))
    await waitFor(() => expect(within(panel()).getByRole('button', { name: 'Passed' })).toBeInTheDocument())
    const mint = fetchMock.mock.calls.find(([u, i]) => String(u).includes('/api/agents/enroll/tokens') && i?.method === 'POST')
    expect(JSON.parse(mint[1].body)).toMatchObject({ ttl_seconds: 3600, max_uses: 1 })
    fireEvent.click(within(stepList()).getByRole('button', { name: /run the installer/i }))
    expect(panel()).toHaveTextContent('cxs_testtoken123')
    expect(panel()).toHaveTextContent('/api/agents/install?os=linux')
  })

  it('Windows swaps the account check for the identity limit', () => {
    mount(AgentsComponent, { params: { tab: 'setup', path: 'windows' } })
    expect(within(stepList()).getByText('Know the identity limit')).toBeInTheDocument()
    expect(within(stepList()).queryByText('Check the service accounts')).toBeNull()
  })

  it('"Confirm it is online" passes from the live agent list, without a Test', () => {
    mount(AgentsComponent, { env: { agents: [ONLINE] }, params: { tab: 'setup', step: 'linux.online' } })
    const item = within(stepList()).getByRole('button', { name: /confirm it is online/i })
    expect(within(item).getByRole('img', { name: 'Passed' })).toBeInTheDocument()
  })
})

describe('Tenant component', () => {
  const TENANT = { name: 'lab', kind: 'xsiam_tenant', config: { base_url: 'https://api-lab.xdr.us.paloaltonetworks.com' }, last_verified_ok: false, last_verified_error: 'HTTP 401' }

  it('reads Down when the last connection test failed, and its config row says so', () => {
    mount(TenantComponent, { env: { tenants: [TENANT], tenant: TENANT } })
    expect(within(screen.getByRole('banner')).getByRole('img', { name: 'Down' })).toBeInTheDocument()
    const cfg = screen.getByRole('region', { name: 'Configuration' })
    const row = within(cfg).getByRole('button', { name: /last connection test passed/i })
    expect(within(row).getByRole('img', { name: 'Missing' })).toBeInTheDocument()
  })

  it('Read-back & integrations keeps the tenant form and the collector targets', async () => {
    mount(TenantComponent, { params: { tab: 'readback' } })
    const tabs = screen.getByRole('group', { name: 'Component pages' })
    expect(within(tabs).getByRole('button', { name: 'Read-back & integrations' })).toHaveAttribute('aria-pressed', 'true')
    await waitFor(() => expect(screen.getByTestId('collector-targets')).toBeInTheDocument())
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1)
  })

  it('"Test the connection" runs the staged preflight and reports each stage', async () => {
    fetchMock = installRoutes({
      'GET /api/collector-targets': { targets: [] },
      'GET /api/ngfw': { firewalls: [] },
      'POST /api/connectors/xsiam_tenant/preflight': {
        overall: 'degraded', queries_issued: 3,
        stages: [
          { id: 'dns_tls', status: 'ok', detail: 'TLS ok' },
          { id: 'auth', status: 'ok', detail: 'signed in' },
          { id: 'scope', status: 'degraded', code: 'PF_SCOPE', detail: 'cannot read cases' },
          { id: 'clock', status: 'ok', detail: '0.4 s' },
        ],
      },
    })
    mount(TenantComponent, { env: { tenants: [TENANT], tenant: TENANT }, params: { tab: 'setup', step: 'test' } })
    fireEvent.click(within(panel()).getByRole('button', { name: 'Test' }))
    const rows = await within(panel()).findByRole('list', { name: /test result/i })
    expect(within(rows).getAllByRole('listitem').map((li) => li.textContent)).toEqual([
      expect.stringContaining('dns_tls'), expect.stringContaining('auth'),
      expect.stringContaining('scope'), expect.stringContaining('clock'),
    ])
    const call = fetchMock.mock.calls.find(([u]) => String(u).includes('/api/connectors/xsiam_tenant/preflight'))
    expect(String(call[0])).toContain('integration=lab')
    expect(within(panel()).queryByRole('button', { name: 'Passed' })).toBeNull()
  })

  it('a preflight no tenant answered never reads Passed', async () => {
    installRoutes({
      'GET /api/collector-targets': { targets: [] },
      'GET /api/ngfw': { firewalls: [] },
      'POST /api/connectors/xsiam_tenant/preflight': { overall: 'ok', queries_issued: 0, stages: [{ id: 'auth', status: 'ok' }] },
    })
    mount(TenantComponent, { env: { tenants: [TENANT], tenant: TENANT }, params: { tab: 'setup', step: 'test' } })
    fireEvent.click(within(panel()).getByRole('button', { name: 'Test' }))
    await waitFor(() => expect(within(stepList()).getByRole('img', { name: "Can't check" })).toBeInTheDocument())
    expect(screen.queryByRole('button', { name: 'Passed' })).toBeNull()
  })
})

describe('Firewall component', () => {
  const FW = { name: 'pa-lab', host: 'pa-lab.local', last_verified_ok: null, forwarding_path: 'syslog' }
  const report = (stages) => ({ firewall: 'pa-lab', overall: 'blocked', queries_issued: 9, stages, sources: [] })

  function withFirewall(extra = {}) {
    return installRoutes({
      'GET /api/collector-targets': { targets: [] },
      'GET /api/ngfw': { firewalls: [FW] },
      ...extra,
    })
  }

  it('Sources is today\'s Sources step', async () => {
    withFirewall()
    mount(FirewallComponent, { params: { tab: 'sources' } })
    expect(await screen.findByTestId('fw-sources-live')).toHaveTextContent(/run the check/i)
  })

  it('"Forward the rule\'s logs" reads the log-forwarding stage of the read-only check', async () => {
    withFirewall({
      'POST /api/connectors/panos_ngfw/preflight': report([
        { id: 'auth', status: 'ok' },
        { id: 'log_forwarding_profiles', status: 'blocked', code: 'PN_NO_LFP', detail: 'no log forwarding profile' },
      ]),
    })
    mount(FirewallComponent, { params: { tab: 'setup', path: 'syslog', step: 'syslog.forward' } })
    await waitFor(() => expect(within(panel()).getByRole('heading', { name: "Forward the rule's logs" })).toBeInTheDocument())
    fireEvent.click(within(panel()).getByRole('button', { name: 'Test' }))
    expect(await within(panel()).findByRole('button', { name: /PN_NO_LFP/ })).toBeInTheDocument()
    expect(panel()).toHaveTextContent('no log forwarding profile')
  })

  it('a step only Cortex can show, with no read, is ticked by the DC', async () => {
    withFirewall()
    mount(FirewallComponent, { params: { tab: 'setup', path: 'syslog', step: 'syslog.format' } })
    await waitFor(() => expect(within(panel()).getByRole('heading', { name: "Match the broker's format" })).toBeInTheDocument())
    fireEvent.click(within(panel()).getByRole('checkbox'))
    const item = within(stepList()).getByRole('button', { name: /match the broker/i })
    expect(within(item).getByRole('img', { name: 'You confirmed' })).toBeInTheDocument()
  })

  it('never writes: every request it makes is a read or the read-only check', async () => {
    const f = withFirewall({ 'POST /api/connectors/panos_ngfw/preflight': report([{ id: 'auth', status: 'ok' }]) })
    mount(FirewallComponent)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Test' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: 'Test' }))
    await waitFor(() => expect(f.mock.calls.some(([u]) => String(u).includes('/preflight'))).toBe(true))
    const writes = f.mock.calls.filter(([u, i]) => (i?.method || 'GET') !== 'GET' && !String(u).includes('/preflight'))
    expect(writes).toEqual([])
  })
})

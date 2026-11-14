import React, { useState } from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, within, fireEvent, waitFor, cleanup } from '@testing-library/react'
import { installRoutes } from '../../../../test/mockFetch.js'
import { EnvironmentContext, DEFAULT_ENV } from '../../../../context/EnvironmentContext.jsx'
import { __resetComponentSources } from '../../useComponentStatus.js'
import { buildHealthModel } from '../../readiness/healthModel.js'
import PovengineComponent from '../PovengineComponent.jsx'
import HttpCollectorComponent from '../HttpCollectorComponent.jsx'
import { BrokerVmComponent, XdrCollectorComponent, CortexEngineComponent } from '../ConfirmedComponents.jsx'
import GetStartedView from '../../GetStartedView.jsx'

void React

/**
 * The remaining five components on the one page shape (design v26 slice 4).
 * Where SimCore has an API the page reads it; where it has none, every row
 * and step reads Can't check until the DC ticks it, then "You confirmed".
 */
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
  render(
    <EnvironmentContext.Provider value={{ ...DEFAULT_ENV, refreshAgents: vi.fn(), refreshTenants: vi.fn(), ...env }}>
      <Routed Comp={Comp} initial={params} setParamsSpy={setParams} onNavigate={onNavigate} />
    </EnvironmentContext.Provider>,
  )
  return { setParams, onNavigate }
}

const panel = () => screen.getByRole('region', { name: /step/i })
const stepList = () => screen.getByRole('list', { name: /steps/i })
const tabs = () => screen.getByRole('group', { name: 'Component pages' })

let fetchMock
function routes(extra = {}) {
  fetchMock = installRoutes({
    'GET /api/collector-targets': { targets: [] },
    'GET /api/ngfw': { firewalls: [] },
    ...extra,
  })
  return fetchMock
}

beforeEach(() => {
  window.localStorage.clear()
  __resetComponentSources()
  routes()
})
afterEach(cleanup)

describe('POVengine', () => {
  const health = buildHealthModel({
    status: 'degraded',
    version: '1.2.0',
    components: {
      db: { status: 'ok' },
      payload_shelf: { status: 'degraded', staged: 0, detail: 'nothing staged' },
      task_queue: { status: 'ok', queued: 0 },
      master_key: { status: 'ok' },
      agent_binaries: { status: 'ok' },
    },
    degraded_components: ['payload_shelf'],
  })

  it('services are what /api/health reports, by name', () => {
    mount(PovengineComponent, { env: { healthModel: health } })
    const services = screen.getByRole('region', { name: 'Services' })
    expect(within(within(services).getByRole('button', { name: /database/i })).getByRole('img', { name: 'Up' })).toBeInTheDocument()
    expect(within(within(services).getByRole('button', { name: /tool readiness/i })).getByRole('img', { name: 'Degraded' })).toBeInTheDocument()
  })

  it('the API tab is generated from this POVengine\'s own OpenAPI document', async () => {
    routes({
      'GET /api/openapi.json': {
        paths: {
          '/api/runs': { get: { summary: 'List runs' }, post: { summary: 'Launch' } },
          '/api/runs/{run_id}': { get: { summary: 'Get a run' } },
          '/api/agents': { get: { summary: 'List agents' } },
          '/healthz': { get: {} },
        },
      },
    })
    mount(PovengineComponent, { env: { healthModel: health }, params: { tab: 'api' } })
    expect(within(tabs()).getByRole('button', { name: 'API' })).toHaveAttribute('aria-pressed', 'true')
    const table = await screen.findByRole('table', { name: /route groups/i })
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows.map((r) => r.textContent)).toEqual([
      expect.stringMatching(/\/api\/runs.*3/),
      expect.stringMatching(/\/api\/agents.*1/),
    ])
  })

  it('an API document that cannot be read is said, not replaced with a list', async () => {
    routes({ 'GET /api/openapi.json': new Response('nope', { status: 500 }) })
    mount(PovengineComponent, { params: { tab: 'api' } })
    expect(await screen.findByText(/could not read this POVengine's API description/i)).toBeInTheDocument()
    expect(screen.queryByRole('table')).toBeNull()
  })

  it('"Check health" passes only when nothing is degraded, and lists what is', async () => {
    mount(PovengineComponent, { env: { healthModel: health, refreshHealth: vi.fn() }, params: { tab: 'setup', step: 'health' } })
    const item = within(stepList()).getByRole('button', { name: /check health/i })
    expect(within(item).getByRole('img', { name: 'Failed' })).toBeInTheDocument()
    expect(panel()).toHaveTextContent(/tool readiness/i)
  })
})

describe('HTTP collector', () => {
  it('reads Off with no collector target, and its management tab is the collector targets', async () => {
    mount(HttpCollectorComponent, { params: { tab: 'targets' } })
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('HTTP collector')
    // Off only once the target list has arrived and is empty — before that it is Unknown.
    expect(await within(screen.getByRole('banner')).findByRole('img', { name: 'Off' })).toBeInTheDocument()
    expect(await screen.findByTestId('collector-targets')).toBeInTheDocument()
  })

  it('"Choose format and dataset" says uncompressed JSON and names the dataset from vendor and product', () => {
    mount(HttpCollectorComponent, { params: { tab: 'setup', step: 'format' } })
    expect(panel()).toHaveTextContent(/uncompressed/i)
    expect(panel()).toHaveTextContent('JSON')
    fireEvent.change(within(panel()).getByLabelText('Vendor'), { target: { value: 'Duo' } })
    fireEvent.change(within(panel()).getByLabelText('Product'), { target: { value: 'auth' } })
    expect(panel()).toHaveTextContent('duo_auth_raw')
  })

  it('"Add it to POVengine": Test stores the target, then checks it without writing', async () => {
    const f = routes({
      'PUT /api/collector-targets/:name': { name: 'duo-auth', kind: 'xsiam_http_collector', has_key: true, warnings: [] },
      'POST /api/collector-targets/:name/preflight': { verdict: 'reachable', code: null, rungs: [{ rung: 'configured', status: 'ok' }, { rung: 'reachable', status: 'ok' }] },
    })
    mount(HttpCollectorComponent, { params: { tab: 'setup', step: 'add' } })
    fireEvent.change(within(panel()).getByLabelText('Target name'), { target: { value: 'duo-auth' } })
    fireEvent.change(within(panel()).getByLabelText('Collector URL'), { target: { value: 'https://api-lab.xdr.us.paloaltonetworks.com/logs/v1/event' } })
    fireEvent.change(within(panel()).getByLabelText('API key'), { target: { value: 'secret-key-1' } })
    fireEvent.click(within(panel()).getByRole('button', { name: 'Test' }))
    await waitFor(() => expect(within(panel()).getByRole('button', { name: 'Passed' })).toBeInTheDocument())
    const put = f.mock.calls.find(([u, i]) => i?.method === 'PUT' && String(u).includes('/api/collector-targets/duo-auth'))
    expect(JSON.parse(put[1].body)).toMatchObject({
      kind: 'xsiam_http_collector', url: 'https://api-lab.xdr.us.paloaltonetworks.com/logs/v1/event',
      auth_header: 'Authorization', auth_scheme: '', token: 'secret-key-1',
    })
    const pf = f.mock.calls.find(([u, i]) => i?.method === 'POST' && String(u).includes('/preflight'))
    expect(JSON.parse(pf[1].body)).toEqual({ write_canary: false })
    // The key is write-only: once stored it is not on screen.
    expect(within(panel()).getByLabelText('API key')).toHaveValue('')
    expect(panel()).not.toHaveTextContent('secret-key-1')
  })

  it('"Add it to POVengine" is not passed while the store warns', async () => {
    routes({
      'PUT /api/collector-targets/:name': { name: 'x', warnings: ['path is not /logs/v1/event'] },
      'POST /api/collector-targets/:name/preflight': { verdict: 'reachable', rungs: [] },
    })
    mount(HttpCollectorComponent, { params: { tab: 'setup', step: 'add' } })
    fireEvent.change(within(panel()).getByLabelText('Target name'), { target: { value: 'x' } })
    fireEvent.change(within(panel()).getByLabelText('Collector URL'), { target: { value: 'https://api-lab/other' } })
    fireEvent.click(within(panel()).getByRole('button', { name: 'Test' }))
    expect(await within(panel()).findByRole('button', { name: /path is not/i })).toBeInTheDocument()
    expect(within(panel()).queryByRole('button', { name: 'Passed' })).toBeNull()
  })

  it('"Send a test event" posts one canary and reports it as its own row', async () => {
    const f = routes({
      'GET /api/collector-targets': { targets: [{ name: 'duo-auth', kind: 'xsiam_http_collector', is_default: true, dataset: 'duo_auth_raw', has_key: true }] },
      'POST /api/collector-targets/:name/preflight': { verdict: 'refused', code: 'HTTP_401', remediation: 'wrong key or disabled collector', rungs: [] },
    })
    mount(HttpCollectorComponent, { params: { tab: 'setup', step: 'send' } })
    await waitFor(() => expect(within(panel()).getByRole('button', { name: 'Test' })).toBeEnabled())
    fireEvent.click(within(panel()).getByRole('button', { name: 'Test' }))
    expect(await within(panel()).findByRole('button', { name: /HTTP_401/ })).toBeInTheDocument()
    const pf = f.mock.calls.find(([u, i]) => i?.method === 'POST' && String(u).includes('/preflight'))
    expect(JSON.parse(pf[1].body)).toEqual({ write_canary: true })
    const rows = within(panel()).getByRole('list', { name: /test result/i })
    expect(within(rows).getAllByRole('listitem')[0]).toHaveTextContent(/canary/i)
  })

  it('"Confirm it arrived" has no read on this build, so the DC ticks it', () => {
    mount(HttpCollectorComponent, { params: { tab: 'setup', step: 'arrived' } })
    expect(within(panel()).queryByRole('button', { name: 'Test' })).toBeNull()
    fireEvent.click(within(panel()).getByRole('checkbox'))
    expect(within(within(stepList()).getByRole('button', { name: /confirm it arrived/i })).getByRole('img', { name: 'You confirmed' })).toBeInTheDocument()
  })
})

describe('components POVengine cannot see', () => {
  it.each([
    ['Broker VM', BrokerVmComponent, 6],
    ['XDR Collector', XdrCollectorComponent, 5],
    ['Cortex Engine', CortexEngineComponent, 3],
  ])('%s: Can\'t check, no Test to press, no management tab, and its steps to tick', (name, Comp, steps) => {
    mount(Comp, { params: { tab: 'setup' } })
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe(name)
    expect(within(screen.getByRole('banner')).getByRole('img', { name: "Can't check" })).toBeInTheDocument()
    expect(within(screen.getByRole('banner')).queryByRole('button', { name: 'Test' })).toBeNull()
    expect(within(tabs()).getAllByRole('button').map((b) => b.textContent)).toEqual(['Overview', 'Setup'])
    expect(within(stepList()).getAllByRole('listitem')).toHaveLength(steps)
  })

  it('Broker VM reads "You confirmed" once every step is ticked — still never Up', () => {
    mount(BrokerVmComponent, { params: { tab: 'setup' } })
    for (const item of within(stepList()).getAllByRole('button')) {
      fireEvent.click(item)
      fireEvent.click(within(panel()).getByRole('checkbox'))
    }
    const banner = screen.getByRole('banner')
    expect(within(banner).getByRole('img', { name: 'You confirmed' })).toBeInTheDocument()
    expect(within(banner).queryByRole('img', { name: 'Up' })).toBeNull()
  })
})

describe('Get started points at Configure', () => {
  it('every step opens its component\'s Setup', () => {
    installRoutes({ 'GET /api/tenants': [] })
    const onNavigate = vi.fn()
    render(
      <EnvironmentContext.Provider value={{ ...DEFAULT_ENV, refreshAgents: vi.fn() }}>
        <GetStartedView onNavigate={onNavigate} />
      </EnvironmentContext.Provider>,
    )
    for (const [step, dest] of [['agent', 'component-agents'], ['tenant', 'component-tenant'], ['collector', 'component-http'], ['bvm', 'component-bvm']]) {
      fireEvent.click(within(screen.getByTestId(`gs-step-${step}`)).getByRole('button', { name: 'Configure' }))
      expect(onNavigate).toHaveBeenLastCalledWith(dest, { tab: 'setup' })
    }
  })
})

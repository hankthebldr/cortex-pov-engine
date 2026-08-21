import React from 'react'
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import '@testing-library/jest-dom'
import { installRoutes } from '../../test/mockFetch.js'
import ReadinessView from '../console/ReadinessView.jsx'
import ReadinessBanner from '../console/ReadinessBanner.jsx'
import { EnvironmentContext } from '../../context/EnvironmentContext.jsx'
import { buildHealthModel } from '../console/readiness/healthModel.js'

const HEALTH_OK = {
  status: 'ok', version: '1.0.0', commit: 'abc1234',
  components: {
    db: { status: 'ok' },
    scenario_catalog: { status: 'ok', count: 170 },
    ttp_catalog: { status: 'ok', count: 170 },
    adapter_catalog: { status: 'ok', count: 91 },
    eal: { status: 'ok', plugins: 21 },
  },
}

const env = (over = {}) => ({
  tenant: null, tenants: [], agent: null, agents: [],
  health: {}, apiError: null, healthModel: null,
  scenarios: [], planes: [], runs: [], activeRun: null, lastRun: null,
  pinnedIds: [], loading: {},
  isPinned: () => false, togglePin: () => {}, unpin: () => {},
  setTenant: () => {}, setAgent: () => {},
  refreshHealth: () => {}, refreshRuns: () => {}, refreshScenarios: () => {},
  refreshAgents: () => {}, refreshTenants: () => {},
  ...over,
})

const renderView = (envOver = {}, props = {}) => render(
  <EnvironmentContext.Provider value={env(envOver)}>
    <ReadinessView {...props} />
  </EnvironmentContext.Provider>,
)

const ROUTES = (over = {}) => ({
  'GET /api/health': HEALTH_OK,
  'GET /api/credentials/integrations': { integrations: [] },
  'GET /api/connectors': { connectors: [] },
  'GET /api/assertions/runs': { runs: [] },
  'GET /api/assertions': { assertions: [], total: 18 },
  ...over,
})

beforeEach(() => { installRoutes(ROUTES()) })
afterEach(() => { vi.restoreAllMocks() })

describe('ReadinessView', () => {
  it('renders an explicit loading state, then the ladder', async () => {
    renderView()
    expect(screen.getByText(/probing SimCore/i)).toBeInTheDocument()
    await waitFor(() => expect(screen.getByTestId('connector-ladder')).toBeInTheDocument())
  })

  it('shows all four rungs separately — never one collapsed badge', async () => {
    renderView()
    await waitFor(() => expect(screen.getByTestId('rung-authored')).toBeInTheDocument())
    for (const id of ['authored', 'configured', 'reachable', 'verified']) {
      expect(screen.getByTestId(`rung-${id}`)).toBeInTheDocument()
    }
  })

  it('states tenant-verified: 0 when nothing has been proven against a tenant', async () => {
    installRoutes(ROUTES({
      'GET /api/credentials/integrations': {
        integrations: [{
          name: 'cust-lab', kind: 'xsiam_tenant', config: { base_url: 'https://api-x.xsiam.us.paloaltonetworks.com' },
          last_verified_ok: true, last_verified_at: '2026-08-05T09:00:00',
        }],
      },
    }))
    renderView({ runs: [] })
    const stmt = await screen.findByTestId('tenant-verified-statement')
    expect(stmt).toHaveTextContent('tenant-verified: 0')
    // The reachable rung is YES and the verified rung is NO, side by side.
    expect(screen.getByTestId('rung-reachable-state')).toHaveTextContent('YES')
    expect(screen.getByTestId('rung-verified-state')).toHaveTextContent('NO')
  })

  it('a reachable tenant carries the caveat that nothing is proven through it', async () => {
    installRoutes(ROUTES({
      'GET /api/credentials/integrations': {
        integrations: [{ name: 'cust-lab', kind: 'xsiam_tenant', config: {}, last_verified_ok: true }],
      },
    }))
    renderView()
    const caveat = await screen.findByTestId('rdy-tenant-caveat-cust-lab')
    expect(caveat).toHaveTextContent(/nothing about the customer's detections has been proven/i)
  })

  it('names the components this SimCore does not report instead of showing zeros', async () => {
    renderView()
    const gaps = await screen.findByTestId('readiness-gaps')
    expect(gaps).toBeInTheDocument()
    expect(screen.getByTestId('gap-payload_shelf')).toBeInTheDocument()
    expect(screen.getByTestId('gap-agent_binaries')).toBeInTheDocument()
    // No "0" is rendered for an unreported component.
    expect(screen.queryByTestId('health-payload_shelf')).not.toBeInTheDocument()
  })

  it('renders a degraded adapter_catalog even when the server called it ok', async () => {
    installRoutes(ROUTES({
      'GET /api/health': {
        ...HEALTH_OK,
        components: { ...HEALTH_OK.components, adapter_catalog: { status: 'ok', count: 0 } },
      },
    }))
    renderView()
    const card = await screen.findByTestId('health-adapter_catalog')
    expect(card).toHaveAttribute('data-status', 'degraded')
    expect(screen.getByTestId('health-adapter_catalog-disagreement')).toHaveTextContent(/count of 0/)
    expect(screen.getByTestId('readiness-overall')).toHaveTextContent('DEGRADED')
  })

  it('does not blank-screen when /api/health 500s', async () => {
    installRoutes(ROUTES({
      'GET /api/health': new Response('boom', { status: 500 }),
    }))
    renderView()
    const down = await screen.findByTestId('readiness-unreachable')
    expect(down).toHaveTextContent(/SIMCORE UNREACHABLE/)
    // And it must not have rendered a green ladder next to that.
    expect(screen.queryByTestId('connector-ladder')).not.toBeInTheDocument()
  })

  it('distinguishes "no preflight endpoint" from "preflight failed"', async () => {
    installRoutes(ROUTES({
      'GET /api/credentials/integrations': {
        integrations: [{ name: 'cust-lab', kind: 'xsiam_tenant', config: {}, last_verified_ok: true }],
      },
      'POST /api/connectors/xsiam_tenant/preflight':
        new Response(JSON.stringify({ error: 'nope', code: 'NOT_FOUND' }), { status: 404 }),
    }))
    renderView()
    const btn = await screen.findByRole('button', { name: /Preflight/i })
    await userEvent.click(btn)
    const gap = await screen.findByTestId('preflight-unavailable')
    expect(gap).toHaveTextContent(/has NOT been checked/)
  })

  it('prints queries_issued so a mocked green preflight cannot pass as tenant proof', async () => {
    installRoutes(ROUTES({
      'GET /api/credentials/integrations': {
        integrations: [{ name: 'cust-lab', kind: 'xsiam_tenant', config: {}, last_verified_ok: true }],
      },
      'POST /api/connectors/xsiam_tenant/preflight': {
        tenant: 'cust-lab', overall: 'ready', queries_issued: 0,
        stages: [{ id: 'auth', status: 'ok', code: 'PF_OK', detail: '/healthcheck 200' }],
      },
    }))
    renderView()
    await userEvent.click(await screen.findByRole('button', { name: /Preflight/i }))
    const res = await screen.findByTestId('preflight-result')
    expect(res).toHaveTextContent('0 tenant queries issued')
  })

  it('offers a route to register a tenant when none exists', async () => {
    const onNavigate = vi.fn()
    renderView({}, { onNavigate })
    const empty = await screen.findByTestId('readiness-no-tenants')
    expect(empty).toHaveTextContent(/still owed/)
    await userEvent.click(screen.getByRole('button', { name: /Register a tenant/i }))
    expect(onNavigate).toHaveBeenCalledWith('tenants')
  })
})

describe('ReadinessBanner', () => {
  const degraded = buildHealthModel({
    ...HEALTH_OK,
    components: { ...HEALTH_OK.components, adapter_catalog: { status: 'ok', count: 0 } },
  })

  it('is silent on a healthy deployment', () => {
    const { container } = render(<ReadinessBanner model={buildHealthModel(HEALTH_OK)} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('is silent when health has never been probed — absence is not degradation', () => {
    const { container } = render(<ReadinessBanner model={null} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('defers to the louder unreachable banner rather than double-reporting', () => {
    const { container } = render(
      <ReadinessBanner model={buildHealthModel(null, { error: 'Failed to fetch' })} />,
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('names the fault AND the fix for a silently broken deployment', () => {
    render(<ReadinessBanner model={degraded} />)
    const row = screen.getByTestId('readiness-banner-adapter_catalog')
    expect(row).toHaveTextContent(/count of 0/)
    expect(row).toHaveTextContent(/tools\//)
  })

  it('routes to the readiness surface', async () => {
    const onNavigate = vi.fn()
    render(<ReadinessBanner model={degraded} onNavigate={onNavigate} />)
    await userEvent.click(screen.getByTestId('readiness-banner-open'))
    expect(onNavigate).toHaveBeenCalledWith('readiness')
  })

  it('dismisses only the fault it was showing — a NEW fault re-raises it', async () => {
    const { rerender } = render(<ReadinessBanner model={degraded} />)
    await userEvent.click(screen.getByRole('button', { name: /Dismiss readiness warning/i }))
    expect(screen.queryByTestId('readiness-banner')).not.toBeInTheDocument()

    const worse = buildHealthModel({
      ...HEALTH_OK,
      components: {
        ...HEALTH_OK.components,
        adapter_catalog: { status: 'ok', count: 0 },
        scenario_catalog: { status: 'ok', count: 0 },
      },
    })
    rerender(<ReadinessBanner model={worse} />)
    expect(screen.getByTestId('readiness-banner')).toBeInTheDocument()
  })
})

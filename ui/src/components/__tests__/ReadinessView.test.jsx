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

  it('states the degradation WITHOUT being expanded — folding detail must not fold the claim', () => {
    // The detail is collapsed by default to buy back viewport height, so the
    // collapsed line is the one a DC actually sees. If the claim could hide
    // with the detail, a degraded deployment would render as a healthy one —
    // the exact Gate-A5 failure this banner exists to prevent.
    render(<ReadinessBanner model={degraded} />)
    expect(screen.getByTestId('readiness-banner')).toHaveTextContent(/SIMCORE DEGRADED/)
    expect(screen.getByTestId('readiness-banner-count')).toHaveTextContent(/1 component/)
    expect(screen.queryByTestId('readiness-banner-adapter_catalog')).not.toBeInTheDocument()
  })

  it('names the fault AND the fix for a silently broken deployment, one click away', async () => {
    render(<ReadinessBanner model={degraded} />)
    await userEvent.click(screen.getByTestId('readiness-banner-toggle'))
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

describe('ReadinessView — attested-only facts', () => {
  const RUN_OLD = {
    run_id: 'r-old', started_at: '2026-10-25T09:00:00', agent_profile_mode: 'report',
    bas_mode_attested: false, soakless_attested: false,
  }
  const RUN_NEW = {
    run_id: 'r-new', started_at: '2026-10-26T09:00:00', agent_profile_mode: 'block',
    bas_mode_attested: true, soakless_attested: null,
  }

  it('lists profile mode, BAS mode and soakless beside the ladder, each as not verified', async () => {
    renderView({ runs: [RUN_OLD, RUN_NEW] })
    const group = await screen.findByTestId('attested-only-facts')
    expect(screen.getByTestId('attested-profile-mode')).toBeInTheDocument()
    expect(screen.getByTestId('attested-bas-mode')).toBeInTheDocument()
    expect(screen.getByTestId('attested-soakless')).toBeInTheDocument()
    expect(group).toHaveTextContent(/not verified/i)
    // never a fifth rung of the ladder
    expect(screen.getAllByTestId(/^rung-(authored|configured|reachable|verified)$/)).toHaveLength(4)
    expect(screen.getByTestId('connector-ladder')).not.toContainElement(group)
  })

  it('states what would verify each fact', async () => {
    renderView({ runs: [RUN_NEW] })
    await screen.findByTestId('attested-only-facts')
    expect(screen.getByTestId('attested-profile-mode')).toHaveTextContent('a canary run with a tenant-confirmed prevention')
    expect(screen.getByTestId('attested-bas-mode')).toHaveTextContent('nothing — attested only')
    expect(screen.getByTestId('attested-soakless')).toHaveTextContent('nothing — no public dataset exposes it')
  })

  it('takes values from the newest run by started_at', async () => {
    renderView({ runs: [RUN_NEW, RUN_OLD] })
    await screen.findByTestId('attested-only-facts')
    expect(screen.getByTestId('attested-profile-mode')).toHaveTextContent(/block/)
    expect(screen.getByTestId('attested-bas-mode')).toHaveTextContent(/enabled/)
    expect(screen.getByTestId('attested-soakless')).toHaveTextContent(/not attested/)
  })

  it('says "no runs yet" when there are none', async () => {
    renderView({ runs: [] })
    const group = await screen.findByTestId('attested-only-facts')
    expect(group).toHaveTextContent(/no runs yet/i)
  })
})

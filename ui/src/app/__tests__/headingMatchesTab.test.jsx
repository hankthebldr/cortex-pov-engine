/**
 * The h1 is the tab label. One surface carried three names — rail "Simulate",
 * h1 "Library", tab "Scenarios"; rail "Catalog", h1 "TTP Cards", tab
 * "TTP cards" — so a DC reading the rail could not predict the page title,
 * and copy across the console used all three. The rule: the rail names the
 * TASK, the tab names the PAGE, and the page's h1 repeats the tab verbatim.
 * This guard reads the label from the registry so a rename in one place
 * fails here instead of drifting.
 *
 * Pages reached off the tab strip (the guided launch, Lab, Traffic / EAL)
 * follow the same rule against their registry label, and own exactly one h1:
 * a page that mounts another page inside it must not inherit that page's
 * title as a second h1.
 */
import React from 'react'
import { describe, it, expect } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { installRoutes } from '../../test/mockFetch.js'
import { sectionFor, getDestination } from '../destinations.jsx'
import OperationsView from '../../components/console/OperationsView.jsx'
import TtpBrowserView from '../../components/console/TtpBrowserView.jsx'
import ReadinessView from '../../components/console/ReadinessView.jsx'
import EvidenceView from '../../components/console/EvidenceView.jsx'
import CoverageView from '../../components/console/CoverageView.jsx'
import DataStreamsView from '../../components/console/DataStreamsView.jsx'
import UcTcIndexView from '../../components/console/UcTcIndexView.jsx'
import LabView from '../../components/console/LabView.jsx'
import EalConsole from '../../components/EalConsole.jsx'

void React

const tabLabel = (id) => sectionFor(id).tabs.find(([tab]) => tab === id)[1]
// Scenarios and Launch gate are rail items now (design v26 §1), so their h1 is
// the rail label — the same rule, read from the registry.
const railLabel = (id) => getDestination(id).label
const routeLabel = (id) => getDestination(id).label
const h1Texts = () => screen.queryAllByRole('heading', { level: 1 }).map((h) => h.textContent)

describe('page h1 is the rail or tab label', () => {
  it('Scenarios', async () => {
    installRoutes({ 'GET /api/scenarios': { scenarios: [] }, 'GET /api/runs': { runs: [] }, 'GET /api/agents': { agents: [] } })
    render(<OperationsView />)
    await waitFor(() => expect(screen.getByRole('heading', { level: 1 }).textContent).toBe(railLabel('library')))
  })

  it('Launch gate', async () => {
    installRoutes({
      'GET /api/health': { status: 'ok', components: {} },
      'GET /api/credentials/integrations': { integrations: [] },
      'GET /api/connectors': { connectors: [] },
      'GET /api/assertions/runs': { runs: [] },
      'GET /api/assertions': { assertions: [], total: 0 },
    })
    render(<ReadinessView />)
    await waitFor(() => expect(screen.getByRole('heading', { level: 1 }).textContent).toBe(railLabel('preflight')))
  })

  it('Results › Report', async () => {
    installRoutes({ 'GET /api/results/run-A': { results: [] } })
    render(<EvidenceView pinnedRun={{ runId: 'run-A', scenarioId: 'SIM-EDR-001', status: 'completed' }} />)
    await waitFor(() => expect(screen.getByRole('heading', { level: 1 }).textContent).toBe(tabLabel('proof')))
  })

  it('Results › Coverage, in every view mode', async () => {
    installRoutes({ 'GET /api/mitre/coverage': { tactics: [], techniques: [], summary: null }, 'GET /api/scenarios': { scenarios: [] } })
    render(<CoverageView />)
    await waitFor(() => expect(screen.getByRole('heading', { level: 1 }).textContent).toBe(tabLabel('coverage')))
    // The lens is named by the view-mode toggle; switching it does not rename the page.
    for (const mode of [/PANW Stack/, /Competitive/, /ATT&CK/]) {
      fireEvent.click(screen.getByRole('tab', { name: mode }))
      expect(screen.getByRole('heading', { level: 1 }).textContent).toBe(tabLabel('coverage'))
    }
  })

  it('Catalog › TTP cards', async () => {
    installRoutes({ 'GET /api/ttps': { ttps: [] } })
    render(<TtpBrowserView />)
    await waitFor(() => expect(screen.getByRole('heading', { level: 1 }).textContent).toBe(tabLabel('ttps')))
  })

  it('Catalog › Data streams, as plain text', async () => {
    installRoutes({ 'GET /api/eal/data-streams': { counts: { total: 0 }, sources: [], emitters: [] } })
    render(<DataStreamsView onMessage={() => {}} />)
    const h1 = await screen.findByRole('heading', { level: 1 })
    expect(h1.textContent).toBe(tabLabel('streams'))
    // Orange is the one primary action only; no word of a title is painted.
    expect(h1.children).toHaveLength(0)
  })

  it('Catalog › UC / TC index', async () => {
    render(<UcTcIndexView params={{}} setParams={() => {}} />)
    await waitFor(() => expect(screen.getByRole('heading', { level: 1 }).textContent).toBe(tabLabel('uctc')))
  })
})

describe('off-tab pages: one h1, and it is the registry label', () => {
  it('the Launch gate keeps one h1 with a scenario armed: the panel and this POVengine are h2s', async () => {
    installRoutes({
      'GET /api/scenarios/SIM-EDR-001': {
        scenario_id: 'SIM-EDR-001', name: 'Credential dumping', plane: 'EDR',
        push_supported: true, pull_supported: true, steps: [{ id: 'step-01', platforms: ['linux'] }],
      },
      'GET /api/agents': { agents: [], total: 0 },
      'GET /api/tools/adapters': { adapters: [], total: 0 },
    })
    const Gate = getDestination('preflight').Component
    render(<Gate params={{ arm: 'SIM-EDR-001' }} onNavigate={() => {}} />)
    await screen.findByTestId('new-run')
    expect(h1Texts()).toEqual([routeLabel('preflight')])
    expect(screen.getByRole('heading', { level: 2, name: 'New POV run' })).toBeInTheDocument()
  })

  it('Lab', async () => {
    installRoutes({
      'GET /api/infra/modules': { modules: [], total: 0 },
      'GET /api/infra/bundles': { bundles: [], total: 0 },
      'GET /api/tools/adapters': { adapters: [], total: 0 },
    })
    render(<LabView />)
    await waitFor(() => expect(h1Texts()).toEqual([routeLabel('environments')]))
    // "Infrastructure" was the retired nav group; the page no longer wears it.
    expect(screen.queryByText('Infrastructure')).toBeNull()
  })

  it('Traffic / EAL, as plain text', async () => {
    installRoutes({
      'GET /api/eal/campaigns': { campaigns: [], total: 0 },
      'GET /api/eal/runs': { runs: [], total: 0 },
    })
    render(<EalConsole onMessage={() => {}} />)
    await waitFor(() => expect(h1Texts()).toEqual([routeLabel('eal')]))
    expect(screen.getByRole('heading', { level: 1 }).children).toHaveLength(0)
    // "Traffic" was the retired nav group; the page no longer wears it.
    expect(screen.queryByText('Traffic')).toBeNull()
  })
})

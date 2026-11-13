/**
 * ⌘K search across the console (design v26 §4.5): pages, components,
 * scenarios and TTP cards, each result naming its kind, each one landing on
 * the place it names.
 *
 * Mounted on the whole console against a mocked SimCore, so the scenario list
 * and the TTP cards are the ones SimCore served, not fixtures in the palette.
 */
import React from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, within, fireEvent, waitFor } from '@testing-library/react'
import { installRoutes } from '../../test/mockFetch.js'

void React

vi.mock('../console/GetStartedView.jsx', () => ({
  default: function StartStub() { return <div data-testid="start-stub"><h1>Get started</h1></div> },
}))
vi.mock('../console/ReadinessView.jsx', () => ({
  default: function ReadinessStub() { return <div data-testid="readiness-stub" /> },
}))
vi.mock('../console/OperationsView.jsx', () => ({
  default: function OperationsStub({ requestOpenScenarioId }) {
    return <div data-testid="ops-stub">{requestOpenScenarioId || '(none)'}</div>
  },
}))
vi.mock('../console/TtpBrowserView.jsx', () => ({
  default: function TtpStub({ initialTtpId }) { return <div data-testid="ttp-stub">{initialTtpId || '(none)'}</div> },
}))
vi.mock('../console/TargetsView.jsx', () => ({
  default: function AgentsStub() { return <div data-testid="agents-stub" /> },
}))

function serve() {
  return installRoutes({
    'GET /api/health': { status: 'ok', version: '1.1.1', uptime_seconds: 60, components: {}, degraded_components: [] },
    'GET /api/runs': { runs: [], total: 0 },
    'GET /api/agents': [],
    'GET /api/scenarios': { scenarios: [{ scenario_id: 'SIM-EDR-001', name: 'Credential dumping', plane: 'EDR', steps: [{}, {}] }] },
    'GET /api/ttps': { ttps: [{ id: 'TTP-2026-0007', name: 'LSASS memory read' }], total: 1 },
  })
}

let AppConsole
beforeEach(async () => {
  window.localStorage.clear()
  window.sessionStorage.clear()
  window.location.hash = '#/start'
  AppConsole = (await import('../../AppConsole.jsx')).default
})
afterEach(() => {
  window.location.hash = ''
})

async function openSearch() {
  fireEvent.keyDown(document, { key: 'k', metaKey: true })
  return screen.findByRole('dialog', { name: /^search$/i })
}

function search(dialog, text) {
  fireEvent.change(within(dialog).getByRole('searchbox'), { target: { value: text } })
}

describe('⌘K search', () => {
  it('finds pages, components, scenarios and TTP cards, each with its kind', async () => {
    serve()
    render(<AppConsole />)
    const dialog = await openSearch()

    search(dialog, 'Runs')
    expect(within(dialog).getByRole('button', { name: /Runs.*Page/ })).toBeInTheDocument()

    search(dialog, 'Agents')
    expect(within(dialog).getByRole('button', { name: /Agents.*Component/ })).toBeInTheDocument()

    search(dialog, 'SIM-EDR-001')
    expect(await within(dialog).findByRole('button', { name: /Credential dumping.*Scenario/ })).toBeInTheDocument()

    search(dialog, 'TTP-2026-0007')
    expect(await within(dialog).findByRole('button', { name: /LSASS memory read.*TTP card/ })).toBeInTheDocument()
  })

  it('opens a TTP card on the Catalog', async () => {
    serve()
    render(<AppConsole />)
    const dialog = await openSearch()
    search(dialog, 'LSASS')
    fireEvent.click(await within(dialog).findByRole('button', { name: /LSASS memory read/ }))
    expect(await screen.findByTestId('ttp-stub')).toHaveTextContent('TTP-2026-0007')
    expect(screen.queryByRole('dialog', { name: /^search$/i })).not.toBeInTheDocument()
  })

  it('opens a scenario on Scenarios', async () => {
    serve()
    render(<AppConsole />)
    const dialog = await openSearch()
    search(dialog, 'Credential')
    fireEvent.click(await within(dialog).findByRole('button', { name: /Credential dumping/ }))
    expect(await screen.findByTestId('ops-stub')).toHaveTextContent('SIM-EDR-001')
  })

  it('opens a component where the rail opens it', async () => {
    serve()
    render(<AppConsole />)
    let dialog = await openSearch()
    search(dialog, 'Agents')
    fireEvent.click(within(dialog).getByRole('button', { name: /Agents.*Component/ }))
    await waitFor(() => expect(window.location.hash).toBe('#/component-agents'))

    dialog = await openSearch()
    search(dialog, 'POVengine')
    fireEvent.click(within(dialog).getByRole('button', { name: /^POVengine.*Component/ }))
    await waitFor(() => expect(window.location.hash).toBe('#/components?open=pov'))
  })

  it('lists every component, and each opens where the rail opens it', async () => {
    serve()
    render(<AppConsole />)
    const dialog = await openSearch()
    search(dialog, 'Cortex Engine')
    // Every component has a place now: its own page, or its row on Components.
    fireEvent.click(within(dialog).getByRole('button', { name: /Cortex Engine.*Component/ }))
    await waitFor(() => expect(window.location.hash).toBe('#/components?open=engine'))
  })

  it('closes on Escape', async () => {
    serve()
    render(<AppConsole />)
    const dialog = await openSearch()
    fireEvent.keyDown(within(dialog).getByRole('searchbox'), { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog', { name: /^search$/i })).not.toBeInTheDocument())
  })
})

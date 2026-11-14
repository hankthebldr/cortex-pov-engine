/**
 * The compose → simulate stepper (design v33, handoff item 6):
 * Scenarios › Build a chain › Launch gate › Run, on each of those pages.
 *
 * The page it is on is the current step; every other step links to its page,
 * carrying the chosen scenario where that page accepts it (`?open=` for the
 * Composer and the Library, `?arm=` for the launch, `?run=` for the run).
 */
import React from 'react'
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, within, waitFor } from '@testing-library/react'
import FlowStepper, { flowTarget, flowHref } from '../FlowStepper.jsx'
import OperationsView from '../OperationsView.jsx'
import { EnvironmentContext, DEFAULT_ENV } from '../../../context/EnvironmentContext.jsx'
import { installRoutes } from '../../../test/mockFetch.js'

void React

const RUNS = [
  { run_id: 'r-new', scenario_id: 'SIM-EDR-001', status: 'running' },
  { run_id: 'r-other', scenario_id: 'SIM-NDR-001', status: 'complete' },
  { run_id: 'r-old', scenario_id: 'SIM-EDR-001', status: 'complete' },
]

function withEnv(ui, env = {}) {
  return render(<EnvironmentContext.Provider value={{ ...DEFAULT_ENV, runs: RUNS, ...env }}>{ui}</EnvironmentContext.Provider>)
}
const link = (name) => screen.getByRole('link', { name: new RegExp(name) })

describe('FlowStepper', () => {
  it('names the four steps in order and marks the page it is on', () => {
    withEnv(<FlowStepper current="composer" scenarioId="SIM-EDR-001" />)
    const nav = screen.getByRole('navigation', { name: 'Compose to simulate' })
    const steps = within(nav).getAllByRole('listitem').map((li) => li.getAttribute('data-step'))
    expect(steps).toEqual(['library', 'composer', 'preflight', 'runs'])
    const current = nav.querySelector('[aria-current="step"]')
    expect(current).toHaveTextContent('Build a chain')
    // The current step is not a link; the other three are.
    expect(within(nav).getAllByRole('link').map((a) => a.textContent.replace(/^\d/, ''))).toEqual(['Scenarios', 'Launch gate', 'Run'])
  })

  it('carries the scenario: ?open= to the Library and the Composer, ?arm= to the launch, ?run= to its newest run', () => {
    withEnv(<FlowStepper current="library" scenarioId="SIM-EDR-001" />)
    expect(link('Build a chain')).toHaveAttribute('href', '#/composer?open=SIM-EDR-001')
    expect(link('Launch gate')).toHaveAttribute('href', '#/preflight?arm=SIM-EDR-001')
    expect(link('Run')).toHaveAttribute('href', '#/runs?run=r-new&tab=live')
  })

  it('with no scenario chosen every step is a plain link to its page', () => {
    withEnv(<FlowStepper current="runs" />)
    expect(link('Scenarios')).toHaveAttribute('href', '#/library')
    expect(link('Build a chain')).toHaveAttribute('href', '#/composer')
    expect(link('Launch gate')).toHaveAttribute('href', '#/preflight')
  })

  it('a click navigates through the console router with the carried id', () => {
    const onNavigate = vi.fn()
    withEnv(<FlowStepper current="runs" scenarioId="SIM-EDR-001" runId="r-old" onNavigate={onNavigate} />)
    fireEvent.click(link('Launch gate'))
    expect(onNavigate).toHaveBeenLastCalledWith('preflight', { arm: 'SIM-EDR-001' })
    fireEvent.click(link('Build a chain'))
    expect(onNavigate).toHaveBeenLastCalledWith('composer', { open: 'SIM-EDR-001' })
    fireEvent.click(link('Scenarios'))
    expect(onNavigate).toHaveBeenLastCalledWith('library', { open: 'SIM-EDR-001' })
  })

  it('withholds the launch id the Composer has not saved, and says why', () => {
    withEnv(
      <FlowStepper current="composer" scenarioId="SIM-EDR-001" launchId={null}
        launchTitle="Save the chain first" />,
    )
    expect(link('Launch gate')).toHaveAttribute('href', '#/preflight')
    expect(link('Launch gate')).toHaveAttribute('title', 'Save the chain first')
    // The chain's origin still rides to the Library.
    expect(link('Scenarios')).toHaveAttribute('href', '#/library?open=SIM-EDR-001')
  })

  it('flowTarget/flowHref build the hash the router reads', () => {
    expect(flowHref(flowTarget('preflight', { scenarioId: 'SIM-A' }))).toBe('#/preflight?arm=SIM-A')
    expect(flowHref(flowTarget('runs', { runId: 'r 1' }))).toBe('#/runs?run=r+1&tab=live')
    expect(flowHref(flowTarget('composer', {}))).toBe('#/composer')
  })
})

describe('FlowStepper on the Scenarios page', () => {
  it('is the current step there, and carries the scenario a card opened', async () => {
    installRoutes({
      'GET /api/scenarios': { scenarios: [{ scenario_id: 'SIM-EDR-001', name: 'Credential Dumping', plane: 'EDR', steps: [] }] },
      'GET /api/scenarios/staged': { loaded: true, scenarios: [], total: 0 },
      'GET /api/scenarios/SIM-EDR-001': { scenario_id: 'SIM-EDR-001', name: 'Credential Dumping', plane: 'EDR', steps: [] },
      'GET /api/runs': { runs: [] },
      'GET /api/agents': { agents: [] },
      'GET /api/doors': { doors: [] },
      'GET /api/doors/readiness': { registry_loaded: true, scenarios: {} },
    })
    withEnv(<OperationsView />)
    const nav = screen.getByRole('navigation', { name: 'Compose to simulate' })
    expect(nav.querySelector('[aria-current="step"]')).toHaveTextContent('Scenarios')
    expect(link('Launch gate')).toHaveAttribute('href', '#/preflight')
    fireEvent.click(await screen.findByRole('button', { name: /open credential dumping/i }))
    await waitFor(() => expect(link('Launch gate')).toHaveAttribute('href', '#/preflight?arm=SIM-EDR-001'))
    expect(link('Build a chain')).toHaveAttribute('href', '#/composer?open=SIM-EDR-001')
    expect(link('Run')).toHaveAttribute('href', '#/runs?run=r-new&tab=live')
  })
})

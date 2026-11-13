/**
 * Scenario cards and the Scenarios filters (design v26 §4.1, §4.4).
 *
 * A card is a header (id, technique), a title, a tag row and a footer that
 * carries the verbs. Its body opens the sheet; the footer's Run goes to the
 * launch step with the scenario loaded and Customize opens Build a chain with
 * its chain loaded. The grid filters by plane, component, use case, test case
 * and status — every option read from what SimCore returned, none invented.
 */
import React from 'react'
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, within, waitFor } from '@testing-library/react'
import ScenarioGrid from '../ScenarioGrid.jsx'
import OperationsView from '../OperationsView.jsx'
import { installRoutes } from '../../../test/mockFetch.js'

void React

const EDR = {
  scenario_id: 'SIM-EDR-001', name: 'Credential Dumping', plane: 'EDR',
  mitre_technique: 'T1003', uc_ref: 'UCS-EDR-01', uc_name: 'Endpoint credential theft',
  tc_ref: 'TC-EDR-03', tc_refs: ['TC-EDR-03', 'TC-EDR-05'],
  steps: [{ id: 'step-01', expected_detections: [{ plane: 'EDR', type: 'BIOC' }] },
    { id: 'step-02', expected_detections: [{ plane: 'EDR', type: 'XQL' }, { plane: 'EDR', type: 'BIOC' }] }],
}
const NDR = {
  scenario_id: 'SIM-NDR-001', name: 'C2 Beacon', plane: 'NDR',
  mitre_technique: 'T1071', uc_ref: 'UCS-NDR-02', uc_name: 'Network C2',
  tc_ref: 'TC-NDR-01', tc_refs: ['TC-NDR-01'],
  steps: [{ id: 'step-01', expected_detections: [{ plane: 'NDR', type: 'XQL' }] }],
}
const CDR = {
  scenario_id: 'SIM-CDR-002', name: 'Container Escape', plane: 'CDR',
  mitre_technique: 'T1611', uc_ref: 'UCS-CDR-04', uc_name: 'Container runtime',
  tc_ref: 'TC-CDR-07', tc_refs: ['TC-CDR-07', 'TC-EDR-05'],
  steps: [],
}

describe('ScenarioCard anatomy', () => {
  it('a header with id and technique, a title, a tag row and a footer with Run and Customize', () => {
    render(<ScenarioGrid scenarios={[EDR]} onRunScenario={() => {}} onCustomizeScenario={() => {}} />)
    const card = screen.getByTestId('scenario-card-SIM-EDR-001')
    const head = within(card).getByTestId('scenario-card-head')
    expect(head).toHaveTextContent('SIM-EDR-001')
    expect(head).toHaveTextContent('T1003')
    expect(within(card).getByText('Credential Dumping')).toBeInTheDocument()
    const tags = within(card).getByTestId('scenario-card-tags')
    expect(tags).toHaveTextContent('EDR')
    expect(tags).toHaveTextContent('UCS-EDR-01')
    expect(tags).toHaveTextContent('TC-EDR-03')
    const foot = within(card).getByTestId('scenario-card-foot')
    expect(foot).toHaveTextContent(/steps\s*2/)
    expect(foot).toHaveTextContent(/detections\s*3/)
    const verbs = within(foot).getAllByRole('button').map((b) => b.textContent)
    expect(verbs).toEqual(['Customize', 'Run'])
    expect(within(foot).getByRole('button', { name: 'Run' }).className).toMatch(/kit-btn--primary/)
  })

  it('the body opens the sheet; Run and Customize act on their own and do not open it', () => {
    const onSelect = vi.fn()
    const onRun = vi.fn()
    const onCustomize = vi.fn()
    render(<ScenarioGrid scenarios={[EDR]} onSelectScenario={onSelect} onRunScenario={onRun} onCustomizeScenario={onCustomize} />)
    const card = screen.getByTestId('scenario-card-SIM-EDR-001')
    fireEvent.click(within(card).getByRole('button', { name: /open credential dumping/i }))
    expect(onSelect).toHaveBeenCalledWith(EDR)
    fireEvent.click(within(card).getByRole('button', { name: 'Run' }))
    expect(onRun).toHaveBeenCalledWith('SIM-EDR-001')
    fireEvent.click(within(card).getByRole('button', { name: 'Customize' }))
    expect(onCustomize).toHaveBeenCalledWith('SIM-EDR-001')
    expect(onSelect).toHaveBeenCalledTimes(1)
  })
})

describe('Scenarios — filters', () => {
  function mount(props = {}) {
    installRoutes({
      'GET /api/scenarios': { scenarios: [EDR, NDR, CDR] },
      'GET /api/runs': { runs: [{ run_id: 'r-1', scenario_id: 'SIM-NDR-001', status: 'complete', started_at: '2026-11-12T10:00:00Z' }] },
      'GET /api/agents': { agents: [] },
      'GET /api/doors': { doors: [
        { door_id: 'ngfw_traffic', label: 'NGFW traffic logs', scope: 'tenant', state: 'unknown' },
        { door_id: 'xdr_agent', label: 'XDR agent', scope: 'agent', state: 'unknown' },
      ] },
      'GET /api/doors/readiness': { registry_loaded: true, scenarios: {
        'SIM-EDR-001': { scenario_id: 'SIM-EDR-001', state: 'ready', scorable: 3, total: 3, not_open: [] },
        'SIM-NDR-001': { scenario_id: 'SIM-NDR-001', state: 'needs_doors', scorable: 0, total: 1,
          not_open: [{ door_id: 'ngfw_traffic', detections: 1 }] },
        'SIM-CDR-002': { scenario_id: 'SIM-CDR-002', state: 'not_evaluated', reason: 'REGISTRY_ABSENT' },
      } },
    })
    return render(<OperationsView {...props} />)
  }
  const shown = () => screen.queryAllByTestId(/^scenario-card-SIM-/).map((c) => c.getAttribute('data-testid').replace('scenario-card-', ''))
  const select = (name) => screen.getByRole('combobox', { name })

  it('filters by use case and by test case, read from the real index refs', async () => {
    mount()
    await waitFor(() => expect(shown()).toHaveLength(3))
    const uc = select('Use case')
    expect(within(uc).getByRole('option', { name: /UCS-EDR-01 · Endpoint credential theft/ })).toBeInTheDocument()
    fireEvent.change(uc, { target: { value: 'UCS-NDR-02' } })
    expect(shown()).toEqual(['SIM-NDR-001'])
    fireEvent.change(uc, { target: { value: '' } })
    // A test case evidenced by two scenarios matches both, through tc_refs.
    fireEvent.change(select('Test case'), { target: { value: 'TC-EDR-05' } })
    expect(shown().sort()).toEqual(['SIM-CDR-002', 'SIM-EDR-001'])
    expect(screen.getByTestId('scenario-filter-count')).toHaveTextContent('2 of 3 shown')
  })

  it('filters by the component a scenario is still waiting on', async () => {
    mount()
    await waitFor(() => expect(shown()).toHaveLength(3))
    const comp = select('Component')
    expect(within(comp).getByRole('option', { name: 'Waiting on NGFW traffic logs' })).toBeInTheDocument()
    fireEvent.change(comp, { target: { value: 'ngfw_traffic' } })
    expect(shown()).toEqual(['SIM-NDR-001'])
  })

  it('filters by status: ready to run, needs a component, never run, already run', async () => {
    mount()
    await waitFor(() => expect(shown()).toHaveLength(3))
    const status = select('Status')
    fireEvent.change(status, { target: { value: 'ready' } })
    expect(shown()).toEqual(['SIM-EDR-001'])
    fireEvent.change(status, { target: { value: 'needs' } })
    expect(shown()).toEqual(['SIM-NDR-001'])
    await waitFor(() => {
      fireEvent.change(status, { target: { value: 'run' } })
      expect(shown()).toEqual(['SIM-NDR-001'])
    })
    fireEvent.change(status, { target: { value: 'never' } })
    expect(shown().sort()).toEqual(['SIM-CDR-002', 'SIM-EDR-001'])
  })

  it('filters by plane, and Clear restores every card', async () => {
    const onSelectPlane = vi.fn()
    mount({ onSelectPlane })
    await waitFor(() => expect(shown()).toHaveLength(3))
    fireEvent.change(select('Plane'), { target: { value: 'CDR' } })
    expect(onSelectPlane).toHaveBeenCalledWith('CDR')
    expect(shown()).toEqual(['SIM-CDR-002'])
    // Filters compose: a CDR plane and an EDR use case leave nothing.
    fireEvent.change(select('Use case'), { target: { value: 'UCS-EDR-01' } })
    expect(shown()).toEqual([])
    fireEvent.click(screen.getByRole('button', { name: 'Clear filters' }))
    expect(shown()).toHaveLength(3)
    expect(onSelectPlane).toHaveBeenLastCalledWith(null)
  })

  it('a card\'s Run goes to the launch step with it loaded; Customize opens Build a chain with it', async () => {
    const onNavigate = vi.fn()
    const onContinueToLaunch = vi.fn()
    const onArmScenario = vi.fn()
    mount({ onNavigate, onContinueToLaunch, onArmScenario })
    await waitFor(() => expect(shown()).toHaveLength(3))
    const card = screen.getByTestId('scenario-card-SIM-EDR-001')
    fireEvent.click(within(card).getByRole('button', { name: 'Customize' }))
    expect(onNavigate).toHaveBeenCalledWith('composer', { open: 'SIM-EDR-001' })
    fireEvent.click(within(card).getByRole('button', { name: 'Run' }))
    expect(onArmScenario).toHaveBeenCalledWith('SIM-EDR-001')
    expect(onContinueToLaunch).toHaveBeenCalledWith('SIM-EDR-001')
  })
})

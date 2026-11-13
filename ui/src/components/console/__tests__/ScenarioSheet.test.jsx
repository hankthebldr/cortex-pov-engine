/**
 * The scenario sheet (design v26 §4.1 Sheet, §4.4).
 *
 * A scenario's deep-dive opens on its CHAIN — the same stage the Composer
 * edits and Run detail draws live, here in preview — then its tabs. Its action
 * row carries the card's two verbs: Run (to the launch step with the scenario
 * loaded) and Customize (to Build a chain with its chain loaded).
 */
import React from 'react'
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, within, waitFor } from '@testing-library/react'
import ScenarioInspector from '../ScenarioInspector.jsx'
import OperationsView from '../OperationsView.jsx'
import { installRoutes } from '../../../test/mockFetch.js'

void React

const launch = {
  mode: 'pull', identity: 'www-data', identityOptions: ['www-data'],
  agents: [], selectedAgent: null, pushFormat: 'bash',
  supportsPull: true, supportsPush: true, launching: false, launchDisabled: false,
  downloading: false, lastRun: null, launch: vi.fn(), downloadPushBundle: vi.fn(),
  setMode: () => {}, setIdentity: () => {}, setSelectedAgent: () => {}, setPushFormat: () => {},
}

const scenario = {
  scenario_id: 'SIM-EDR-001',
  name: 'Credential Dumping',
  plane: 'EDR',
  mitre_technique: 'T1003',
  pull_supported: true,
  push_supported: true,
  execution_identity: { default: 'www-data', options: ['www-data'] },
  steps: [
    { id: 'step-01', name: 'Read shadow', command: 'cat /etc/shadow', mitre_technique: 'T1003',
      expected_detections: [{ plane: 'EDR', type: 'BIOC', description: 'shadow read' }] },
    { id: 'step-02', name: 'List buckets', command: 'aws s3 ls', mitre_technique: 'T1530',
      causality: { parent_step: 'step-01', pivot: 'process_lineage' },
      expected_detections: [{ plane: 'CDR', type: 'XQL', description: 'bucket list' }] },
  ],
}

describe('Scenario sheet', () => {
  it('opens with the chain preview, then the tabs', () => {
    render(<ScenarioInspector scenario={scenario} open launch={launch} onRun={() => {}} onCustomize={() => {}} />)
    const stage = screen.getByTestId('chain-stage')
    expect(stage).toHaveAttribute('data-mode', 'preview')
    const tabs = screen.getByRole('tablist', { name: /scenario sections/i })
    // The stage comes first in reading order.
    expect(stage.compareDocumentPosition(tabs) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    // It draws the scenario's real steps in their lanes.
    expect(screen.getByTestId('stage-card-step-01')).toHaveAttribute('data-lane', 'ENDPOINT')
    expect(screen.getByTestId('stage-card-step-02')).toHaveAttribute('data-lane', 'CLOUD')
  })

  it('names the kind, the id, the scenario and its run status as a word', () => {
    render(<ScenarioInspector scenario={scenario} open launch={launch} onRun={() => {}} />)
    const head = screen.getByTestId('scenario-sheet-head')
    expect(within(head).getByText('Scenario')).toBeInTheDocument()
    expect(within(head).getByText('SIM-EDR-001')).toBeInTheDocument()
    expect(within(head).getByRole('heading', { level: 2, name: 'Credential Dumping' })).toBeInTheDocument()
    expect(within(head).getByRole('img', { name: 'Never run' })).toBeInTheDocument()
  })

  it('carries the card\'s two verbs: Run first and primary, then Customize', () => {
    const onRun = vi.fn()
    const onCustomize = vi.fn()
    render(<ScenarioInspector scenario={scenario} open launch={launch} onRun={onRun} onCustomize={onCustomize} />)
    const actions = within(screen.getByTestId('scenario-sheet-actions')).getAllByRole('button')
    expect(actions.slice(0, 2).map((b) => b.textContent)).toEqual(['Run', 'Customize'])
    expect(actions[0].className).toMatch(/kit-btn--primary/)
    fireEvent.click(actions[0])
    expect(onRun).toHaveBeenCalledTimes(1)
    fireEvent.click(actions[1])
    expect(onCustomize).toHaveBeenCalledTimes(1)
  })

  it('without a launch step to hand off to, Run launches in place', () => {
    const local = { ...launch, launch: vi.fn() }
    render(<ScenarioInspector scenario={scenario} open launch={local} />)
    fireEvent.click(screen.getByRole('button', { name: 'Run' }))
    expect(local.launch).toHaveBeenCalledTimes(1)
  })

  it('switches tabs: Overview, Detections, Run history, Launch options', () => {
    render(<ScenarioInspector scenario={scenario} open launch={launch} onRun={() => {}} />)
    const tabs = within(screen.getByRole('tablist', { name: /scenario sections/i })).getAllByRole('tab')
    expect(tabs.map((t) => t.textContent)).toEqual(['Overview', 'Detections', 'Run history', 'Launch options'])
    expect(tabs[0]).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tabpanel')).toHaveTextContent('Metadata')
    fireEvent.click(tabs[1])
    expect(screen.getByRole('tabpanel')).toHaveTextContent('Expected detection matrix')
    expect(screen.getByRole('tabpanel')).toHaveTextContent('shadow read')
    fireEvent.click(tabs[3])
    expect(within(screen.getByRole('tabpanel')).getByRole('button', { name: /push bundle/i })).toBeInTheDocument()
  })

  it('a scenario with no steps shows the stage\'s empty state', () => {
    render(<ScenarioInspector scenario={{ ...scenario, steps: [] }} open launch={launch} onRun={() => {}} />)
    expect(screen.getByTestId('chain-stage-empty')).toBeInTheDocument()
  })
})

describe('Scenarios → the sheet\'s verbs', () => {
  it('Run goes to the launch step with the scenario loaded; Customize opens Build a chain with it', async () => {
    const onNavigate = vi.fn()
    installRoutes({
      'GET /api/scenarios': { scenarios: [scenario] },
      'GET /api/scenarios/SIM-EDR-001': scenario,
      'GET /api/runs': { runs: [] },
      'GET /api/agents': { agents: [] },
    })
    render(<OperationsView onNavigate={onNavigate} onContinueToLaunch={() => onNavigate('guided', { arm: 'SIM-EDR-001' })} />)
    fireEvent.click(await screen.findByText('Credential Dumping'))
    const actions = await waitFor(() => screen.getByTestId('scenario-sheet-actions'))
    fireEvent.click(within(actions).getByRole('button', { name: 'Customize' }))
    expect(onNavigate).toHaveBeenCalledWith('composer', { open: 'SIM-EDR-001' })
    fireEvent.click(within(actions).getByRole('button', { name: 'Run' }))
    expect(onNavigate).toHaveBeenCalledWith('guided', { arm: 'SIM-EDR-001' })
  })
})

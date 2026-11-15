/**
 * The scenario sheet (design v26 §4.1 Sheet, §4.4; v33 C10).
 *
 * A scenario's deep-dive opens with "Why this matters" (Thesis · Why we
 * simulate it · What proves it · What it needs, read off the scenario), then
 * its CHAIN — the same stage the Composer edits and Run detail draws live, here
 * in preview — then its tabs. Its action row carries the card's two verbs, each
 * with its icon: Run (to the launch step with the scenario loaded) and
 * Customize (to Build a chain with its chain loaded). Anything that only
 * navigates is a link.
 */
import React from 'react'
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, within, waitFor } from '@testing-library/react'
import ScenarioInspector from '../ScenarioInspector.jsx'
import OperationsView from '../OperationsView.jsx'
import { installRoutes } from '../../../test/mockFetch.js'
import { VERBS, VERB_ICONS } from '../kit/index.js'

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

describe('Scenario sheet — why this matters (design v33 C10)', () => {
  const written = {
    ...scenario,
    mitre_technique_name: 'OS Credential Dumping',
    mitre_tactic_name: 'Credential Access',
    threat_report: 'Unit42 - Credential Theft Tradecraft',
    uc_ref: 'UCS-EDR-01',
    uc_name: 'Endpoint Threat Detection',
    tc_ref: 'TC-EDR-03',
    tc_name: 'Credential dumping is detected',
    validation_methodology: 'Signal Injection & Detection Accuracy',
    success_criteria: 'The BIOC fires on the shadow read.',
    threshold: { kpi: 'MTTD', op: '≤', value: 300, unit: 'seconds' },
  }

  it('opens with the four parts, before the chain', () => {
    render(<ScenarioInspector scenario={written} open launch={launch} onRun={() => {}} />)
    const why = screen.getByTestId('scenario-why-matters')
    expect(within(why).getByRole('heading', { level: 3, name: 'Why this matters' })).toBeInTheDocument()
    const parts = within(why).getAllByRole('listitem')
    expect(parts.map((li) => li.getAttribute('data-part'))).toEqual(['thesis', 'why', 'proves', 'needs'])
    expect(parts.map((li) => li.querySelector('.why-matters__label').textContent))
      .toEqual(['Thesis', 'Why we simulate it', 'What proves it', 'What it needs'])
    // Read off the scenario document, not written by the console.
    expect(parts[0]).toHaveTextContent('T1003 OS Credential Dumping (Credential Access).')
    expect(parts[0]).toHaveTextContent('Modeled on Unit42 - Credential Theft Tradecraft.')
    expect(parts[1]).toHaveTextContent('Evidences test case TC-EDR-03 Credential dumping is detected, under use case UCS-EDR-01 Endpoint Threat Detection.')
    expect(parts[2]).toHaveTextContent('The BIOC fires on the shadow read.')
    expect(parts[2]).toHaveTextContent('Passes when MTTD ≤ 300 seconds.')
    expect(parts[2]).toHaveTextContent('2 expected detections across 2 steps.')
    expect(parts[3]).toHaveTextContent('Runs as www-data.')
    // It comes first: before the chain stage in reading order.
    const stage = screen.getByTestId('chain-stage')
    expect(why.compareDocumentPosition(stage) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('a part the scenario does not carry says so plainly instead of inventing one', () => {
    render(<ScenarioInspector scenario={scenario} open launch={launch} onRun={() => {}} />)
    const why = screen.getByTestId('scenario-why-matters')
    const thesis = why.querySelector('[data-part="thesis"]')
    // `scenario` has a technique id but no name, tactic or report.
    expect(thesis).toHaveAttribute('data-written', 'true')
    const reasons = why.querySelector('[data-part="why"]')
    expect(reasons).toHaveAttribute('data-written', 'false')
    expect(reasons).toHaveTextContent('Not written for this scenario yet.')
    expect(within(why).getAllByText('Not written for this scenario yet.')).toHaveLength(1)
  })

  it('the action row\'s buttons are verbs from the closed set, each with its icon', () => {
    render(<ScenarioInspector scenario={scenario} open launch={launch} onRun={() => {}} onCustomize={() => {}} />)
    const row = screen.getByTestId('scenario-sheet-actions')
    const verbs = within(row).getAllByRole('button').filter((b) => b.hasAttribute('data-verb'))
    expect(verbs.map((b) => b.textContent)).toEqual(['Run', 'Customize'])
    for (const b of verbs) {
      expect(VERBS).toContain(b.textContent)
      expect(b.querySelector('svg')).toHaveAttribute('data-icon', VERB_ICONS[b.textContent])
    }
    // The only other control dismisses the sheet; it is not an action.
    const others = within(row).getAllByRole('button').filter((b) => !b.hasAttribute('data-verb'))
    expect(others.map((b) => b.textContent)).toEqual(['Close'])
  })

  it('a navigation-only action is a link with a trailing arrow, not a button', async () => {
    installRoutes({
      'GET /api/tools/adapters': { adapters: [{ adapter_id: 'TOOL-HYDRA', name: 'hydra', tier: 4 }] },
      'GET /api/shelf/payloads': { payloads: [], total: 0, declared: [] },
    })
    const withTool = { ...scenario, external_tools: [{ name: 'hydra', adapter_ref: 'TOOL-HYDRA' }] }
    render(<ScenarioInspector scenario={withTool} open launch={launch} onRun={() => {}} />)
    const warn = await screen.findByTestId('insp-tools-warn')
    const link = within(warn).getByRole('link', { name: 'Packages →' })
    expect(link).toHaveAttribute('href', '#/adapters?scenario=SIM-EDR-001&supply=unstaged')
    expect(within(warn).queryByRole('button')).toBeNull()
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

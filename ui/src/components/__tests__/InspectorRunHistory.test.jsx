/**
 * Tests for the ScenarioInspector "Run history" section.
 *
 * We render the inspector directly with a stub launch object and the
 * runHistory prop populated — avoids spinning up OperationsView's
 * scenario fetch / launch wiring.
 */
import React from 'react'
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import ScenarioInspector from '../console/ScenarioInspector.jsx'

void React

const stubLaunch = {
  mode: 'pull',
  identity: 'www-data',
  identityOptions: ['www-data'],
  agents: [{ id: 'a-1', hostname: 'jumpbox-01' }],
  selectedAgent: 'a-1',
  pushFormat: 'bash',
  supportsPull: true,
  supportsPush: true,
  launching: false,
  launchDisabled: false,
  downloading: false,
  lastRun: null,
  launch: () => {},
  downloadPushBundle: () => {},
  setMode: () => {},
  setIdentity: () => {},
  setSelectedAgent: () => {},
  setPushFormat: () => {},
}

const scenario = {
  scenario_id: 'SIM-EDR-001',
  name: 'AWS Cred Hunt',
  plane: 'EDR',
  mitre_tactic: 'TA0006',
  mitre_tactic_name: 'Credential Access',
  mitre_technique: 'T1552.001',
  mitre_technique_name: 'Credentials In Files',
  pull_supported: true,
  push_supported: true,
  steps: [],
}

describe('ScenarioInspector — Run history section', () => {
  it('shows "never run" empty state when runHistory is empty', () => {
    render(
      <ScenarioInspector
        scenario={scenario}
        open
        launch={stubLaunch}
        runHistory={[]}
      />,
    )
    expect(screen.getByText(/never run/i)).toBeInTheDocument()
    expect(screen.getByText(/no runs on record/i)).toBeInTheDocument()
  })

  it('renders up to 5 most-recent runs', () => {
    const runs = Array.from({ length: 7 }, (_, i) => ({
      id: `r-${i}`,
      status: 'completed',
      started_at: new Date(Date.now() - i * 60_000).toISOString(),
    }))
    const { container } = render(
      <ScenarioInspector
        scenario={scenario}
        open
        launch={stubLaunch}
        runHistory={runs}
      />,
    )
    const rows = container.querySelectorAll('.insp-history__row')
    expect(rows.length).toBe(5)
    expect(screen.getByText(/7 total/i)).toBeInTheDocument()
  })

  it('applies status modifier classes per run', () => {
    const runs = [
      { id: 'r-ok',   status: 'completed', started_at: new Date().toISOString() },
      { id: 'r-bad',  status: 'failed',    started_at: new Date().toISOString() },
      { id: 'r-busy', status: 'running',   started_at: new Date().toISOString() },
    ]
    const { container } = render(
      <ScenarioInspector
        scenario={scenario}
        open
        launch={stubLaunch}
        runHistory={runs}
      />,
    )
    expect(container.querySelector('.insp-history__row--completed')).toBeTruthy()
    expect(container.querySelector('.insp-history__row--failed')).toBeTruthy()
    expect(container.querySelector('.insp-history__row--running')).toBeTruthy()
  })

  it('truncates run id to 10 chars in the row', () => {
    const runs = [{
      id: 'run-abcdefghijklmnop',
      status: 'completed',
      started_at: new Date().toISOString(),
    }]
    const { container } = render(
      <ScenarioInspector
        scenario={scenario}
        open
        launch={stubLaunch}
        runHistory={runs}
      />,
    )
    const idEl = container.querySelector('.insp-history__id')
    expect(idEl.textContent).toBe('run-abcdef')
  })

  it('renders relative time for recent runs', () => {
    const runs = [{
      id: 'r-recent',
      status: 'completed',
      started_at: new Date(Date.now() - 3 * 60 * 1000).toISOString(),
    }]
    render(
      <ScenarioInspector
        scenario={scenario}
        open
        launch={stubLaunch}
        runHistory={runs}
      />,
    )
    expect(screen.getByText(/3m ago/)).toBeInTheDocument()
  })
})

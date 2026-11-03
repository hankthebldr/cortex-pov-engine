/**
 * The Library → Composer link. The inspector's actions were Continue to
 * Launch · Pull · Push · bash · Close — no way to take the scenario you had
 * just read into the Composer and edit it. The only path was typing
 * `#/composer?from=<id>` by hand (`ScenarioTable.jsx` carried an "Open in
 * Simulation Composer" shortcut, but nothing mounts that component), so for a
 * DC the Composer could open 24 scenarios: the ones its palette listed.
 *
 * The inspector is a dumb view: it renders the action only when the parent
 * wires it, and the parent (`OperationsView`) owns the navigation.
 */
import React from 'react'
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import ScenarioInspector from '../console/ScenarioInspector.jsx'
import OperationsView from '../console/OperationsView.jsx'
import { installRoutes } from '../../test/mockFetch.js'

void React

const stubLaunch = {
  mode: 'pull', identity: 'www-data', identityOptions: ['www-data'],
  agents: [], selectedAgent: null, pushFormat: 'bash',
  supportsPull: true, supportsPush: true, launching: false, launchDisabled: true,
  downloading: false, lastRun: null, launch: vi.fn(), downloadPushBundle: vi.fn(),
  setMode: () => {}, setIdentity: () => {}, setSelectedAgent: () => {}, setPushFormat: () => {},
}

const scenario = {
  scenario_id: 'SIM-EDR-001', name: 'Credential Dumping', plane: 'EDR',
  pull_supported: true, push_supported: true, steps: [],
  execution_identity: { default: 'www-data', options: ['www-data'] },
}

describe('ScenarioInspector — Edit in Composer', () => {
  it('offers "Edit in Composer" when the parent wires it, and calls it', () => {
    const onOpenInComposer = vi.fn()
    render(<ScenarioInspector scenario={scenario} open launch={stubLaunch} onOpenInComposer={onOpenInComposer} />)
    fireEvent.click(screen.getByRole('button', { name: /edit in composer/i }))
    expect(onOpenInComposer).toHaveBeenCalledTimes(1)
  })

  it('renders no such action when nothing is wired (isolated drawer)', () => {
    render(<ScenarioInspector scenario={scenario} open launch={stubLaunch} />)
    expect(screen.queryByRole('button', { name: /edit in composer/i })).toBeNull()
  })
})

describe('Library — the selected scenario opens in the Composer', () => {
  it('navigates to the Composer carrying the selected id', async () => {
    const onNavigate = vi.fn()
    installRoutes({
      'GET /api/scenarios': { scenarios: [scenario] },
      'GET /api/scenarios/SIM-EDR-001': scenario,
      'GET /api/runs': { runs: [] },
      'GET /api/agents': { agents: [] },
    })
    render(<OperationsView onNavigate={onNavigate} />)
    fireEvent.click(await screen.findByText('Credential Dumping'))
    const btn = await waitFor(() => screen.getByRole('button', { name: /edit in composer/i }))
    fireEvent.click(btn)
    expect(onNavigate).toHaveBeenCalledWith('composer', { open: 'SIM-EDR-001' })
  })
})

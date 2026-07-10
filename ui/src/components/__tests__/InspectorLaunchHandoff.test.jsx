/**
 * Tests for the ScenarioInspector launch-convergence handoff (redesign v2).
 *
 * The canonical launch surface is the dedicated ③ Launch step. When an
 * `onContinueToLaunch` handler is wired, the drawer's primary CTA becomes
 * "Continue to Launch ▸" and the inline launch is demoted to a secondary
 * "Quick launch". With no handoff (isolated render) the drawer falls back to
 * its own inline "Launch" so it stays functional.
 */
import React from 'react'
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
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
  launch: vi.fn(),
  downloadPushBundle: vi.fn(),
  setMode: () => {},
  setIdentity: () => {},
  setSelectedAgent: () => {},
  setPushFormat: () => {},
}

const scenario = {
  scenario_id: 'SIM-EDR-001',
  name: 'AWS Cred Hunt',
  plane: 'EDR',
  mitre_technique: 'T1552.001',
  pull_supported: true,
  push_supported: true,
  steps: [],
}

describe('ScenarioInspector — launch convergence', () => {
  it('shows "Continue to Launch" and hands off when onContinueToLaunch is wired', () => {
    const onContinue = vi.fn()
    render(
      <ScenarioInspector
        scenario={scenario}
        open
        launch={stubLaunch}
        onContinueToLaunch={onContinue}
      />,
    )
    const cta = screen.getByRole('button', { name: /continue to launch/i })
    expect(cta).toBeInTheDocument()
    fireEvent.click(cta)
    expect(onContinue).toHaveBeenCalledTimes(1)
  })

  it('demotes the inline launch to a "Quick launch" secondary action', () => {
    render(
      <ScenarioInspector
        scenario={scenario}
        open
        launch={stubLaunch}
        onContinueToLaunch={() => {}}
      />,
    )
    // The Quick-launch secondary path is present and drives launch.launch.
    const quick = screen.getByRole('button', { name: /quick launch/i })
    expect(quick).toBeInTheDocument()
    fireEvent.click(quick)
    expect(stubLaunch.launch).toHaveBeenCalled()
  })

  it('falls back to an inline "Launch" when no handoff is provided', () => {
    render(
      <ScenarioInspector
        scenario={scenario}
        open
        launch={stubLaunch}
      />,
    )
    expect(screen.queryByRole('button', { name: /continue to launch/i })).toBeNull()
    expect(screen.getByRole('button', { name: /launch/i })).toBeInTheDocument()
  })
})

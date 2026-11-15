/**
 * Tests for the scenario sheet's Run handoff (design v26 §4.4).
 *
 * The canonical launch surface is the launch step. When an `onRun` handler is
 * wired, the sheet's primary verb "Run" hands the scenario off to it, and the
 * inline launch is demoted to a secondary "Quick launch" on the Launch options
 * tab. With no handoff (isolated render) Run launches in place so the sheet
 * stays functional.
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

describe('ScenarioInspector — Run handoff', () => {
  it('Run hands off to the launch step when onRun is wired, without launching in place', () => {
    const onRun = vi.fn()
    const launch = { ...stubLaunch, launch: vi.fn() }
    render(<ScenarioInspector scenario={scenario} open launch={launch} onRun={onRun} />)
    fireEvent.click(screen.getByRole('button', { name: 'Run' }))
    expect(onRun).toHaveBeenCalledTimes(1)
    expect(launch.launch).not.toHaveBeenCalled()
  })

  it('demotes the inline launch to a "Quick launch" on the Launch options tab', () => {
    const launch = { ...stubLaunch, launch: vi.fn() }
    render(<ScenarioInspector scenario={scenario} open launch={launch} onRun={() => {}} />)
    expect(screen.queryByRole('button', { name: /quick launch/i })).toBeNull()
    fireEvent.click(screen.getByRole('tab', { name: 'Launch options' }))
    fireEvent.click(screen.getByRole('button', { name: /quick launch/i }))
    expect(launch.launch).toHaveBeenCalledTimes(1)
  })

  it('launches in place when no handoff is provided', () => {
    const launch = { ...stubLaunch, launch: vi.fn() }
    render(<ScenarioInspector scenario={scenario} open launch={launch} />)
    fireEvent.click(screen.getByRole('button', { name: 'Run' }))
    expect(launch.launch).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByRole('tab', { name: 'Launch options' }))
    expect(screen.queryByRole('button', { name: /quick launch/i })).toBeNull()
  })
})

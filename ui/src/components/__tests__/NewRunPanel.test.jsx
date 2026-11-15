/**
 * NewRunPanel (design C1): the authorisation tick starts unticked, Run is the
 * one primary action, and an offline agent cannot be chosen.
 */
import React from 'react'
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import NewRunPanel from '../console/NewRunPanel.jsx'

void React

const targets = [
  { kind: 'agent', id: 'a1', label: 'web-01', sub: 'linux · agent', status: 'up', word: 'Online', choosable: true },
  { kind: 'agent', id: 'a3', label: 'old-03', sub: 'windows · agent', status: 'down', word: 'Offline', choosable: false },
]

describe('NewRunPanel', () => {
  it('starts unticked with Run disabled and the reasons written under it', () => {
    render(<NewRunPanel scenario={{ scenario_id: 'SIM-EDR-001', name: 'Cred' }} targets={targets}
      blockers={['Choose where it runs', 'Confirm you are authorised to run it on this tenant']} />)
    expect(screen.getByTestId('new-run-authorised')).not.toBeChecked()
    expect(screen.getByRole('button', { name: 'Run' })).toBeDisabled()
    expect(screen.getByTestId('launch-blockers')).toHaveTextContent('Choose where it runs')
  })

  it('an offline agent is listed but picking it does nothing', () => {
    const onSelect = vi.fn()
    render(<NewRunPanel scenario={{ scenario_id: 'X' }} targets={targets} onSelect={onSelect} />)
    const radios = screen.getAllByRole('radio')
    fireEvent.click(radios[1])
    expect(onSelect).not.toHaveBeenCalled()
    expect(radios[1]).toHaveAttribute('aria-disabled', 'true')
    fireEvent.click(radios[0])
    expect(onSelect).toHaveBeenCalledWith({ kind: 'agent', id: 'a1', label: 'web-01' })
  })

  it('with nothing blocking, Run is enabled and fires onRun', () => {
    const onRun = vi.fn()
    render(<NewRunPanel scenario={{ scenario_id: 'X' }} targets={targets} selected={{ kind: 'agent', id: 'a1' }}
      authorised blockers={[]} onRun={onRun} />)
    expect(screen.getAllByRole('radio')[0]).toHaveAttribute('aria-checked', 'true')
    fireEvent.click(screen.getByRole('button', { name: 'Run' }))
    expect(onRun).toHaveBeenCalled()
  })
})

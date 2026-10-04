/**
 * GetStartedView — the console's front door.
 *
 * Pinned: it opens on the first unfinished REQUIRED step, every "done" comes
 * from provider state, the self-confirmed steps say so, and the simulate step
 * hands off to the guided launch with a real scenario id.
 */
import React from 'react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { installRoutes } from '../../test/mockFetch.js'
import { EnvironmentContext, DEFAULT_ENV } from '../../context/EnvironmentContext.jsx'
import GetStartedView from '../console/GetStartedView.jsx'

void React

function mount(env = {}, onNavigate = vi.fn()) {
  render(
    <EnvironmentContext.Provider value={{ ...DEFAULT_ENV, refreshAgents: vi.fn(), ...env }}>
      <GetStartedView onNavigate={onNavigate} />
    </EnvironmentContext.Provider>,
  )
  return onNavigate
}

const ONLINE = { agent_id: 'local-jumpbox', hostname: 'vm', os: 'linux', status: 'online' }

describe('GetStartedView', () => {
  beforeEach(() => {
    window.localStorage.clear()
    installRoutes({ 'GET /api/tenants': [], 'GET /api/xsiam/tenants': [] })
  })

  it('opens on the agent step for an empty instance, with one primary action', () => {
    mount()
    expect(screen.getByText('0 of 3 required steps done')).toBeInTheDocument()
    expect(screen.getByTestId('gs-step-agent').querySelector('.gs-step__body')).not.toBeNull()
    expect(screen.getByTestId('gs-agent-generate')).toHaveClass('pov-btn--primary')
  })

  it('marks the agent done from provider state and moves on to the tenant', () => {
    mount({ agents: [ONLINE] })
    expect(screen.getByTestId('gs-step-agent')).toHaveAttribute('data-done', 'true')
    expect(screen.getByTestId('gs-step-tenant').querySelector('.gs-step__body')).not.toBeNull()
    expect(screen.getByText('1 of 3 required steps done')).toBeInTheDocument()
  })

  it('says a confirmed step was confirmed, not verified', () => {
    mount({ agents: [ONLINE] })
    fireEvent.click(screen.getByTestId('gs-step-bvm').querySelector('.gs-step__row'))
    fireEvent.click(screen.getByRole('button', { name: /I've set this up/i }))
    expect(screen.getByTestId('gs-step-bvm')).toHaveAttribute('data-done', 'true')
    expect(screen.getByText(/POVengine cannot check this one/)).toBeInTheDocument()
  })

  it('hands the suggested first run to the guided launch', () => {
    const onNavigate = mount({
      agents: [ONLINE],
      tenants: [{ name: 'lab' }],
      agent: ONLINE,
      scenarios: [{ scenario_id: 'SIM-CDR-001', plane: 'CDR', name: 'Cloud' }, { scenario_id: 'SIM-EDR-001', plane: 'EDR', name: 'Credential Dumping' }],
    })
    fireEvent.click(screen.getByTestId('gs-simulate-launch'))
    expect(onNavigate).toHaveBeenCalledWith('guided', { arm: 'SIM-EDR-001' })
  })

  it('surfaces a failed first run with a way into it', () => {
    const onNavigate = mount({
      agents: [ONLINE],
      tenants: [{ name: 'lab' }],
      runs: [{ run_id: 'r-9', status: 'failed' }],
    })
    expect(screen.getByTestId('gs-simulate-failed')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /open the failed run/i }))
    expect(onNavigate).toHaveBeenCalledWith('runs', { run: 'r-9', tab: 'live' })
  })
})

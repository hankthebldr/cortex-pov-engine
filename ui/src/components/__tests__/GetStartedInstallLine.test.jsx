/**
 * Get started names an installer-managed SimCore in one plain line under the
 * h1, and draws nothing when /api/health carries no `install` block.
 */
import React from 'react'
import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { installRoutes } from '../../test/mockFetch.js'
import { EnvironmentContext, DEFAULT_ENV } from '../../context/EnvironmentContext.jsx'
import GetStartedView from '../console/GetStartedView.jsx'

void React

function mount(install) {
  render(
    <EnvironmentContext.Provider
      value={{ ...DEFAULT_ENV, health: { ...DEFAULT_ENV.health, install }, refreshAgents: () => {} }}
    >
      <GetStartedView onNavigate={() => {}} />
    </EnvironmentContext.Provider>,
  )
}

describe('GetStartedView install line', () => {
  beforeEach(() => {
    window.localStorage.clear()
    installRoutes({ 'GET /api/tenants': [], 'GET /api/xsiam/tenants': [] })
  })

  it('renders install line when health.install is set', () => {
    mount({ installed_by: 'povengine-setup/1.3.0', address: '192.0.2.10:8888', listen: 'all', platform: 'linux' })
    expect(screen.getByText('Installed by setup 1.3.0 · listening at 192.0.2.10:8888')).toBeInTheDocument()
  })

  it('renders nothing when health.install is null', () => {
    mount(null)
    expect(screen.queryByText(/Installed by setup/)).toBeNull()
  })
})

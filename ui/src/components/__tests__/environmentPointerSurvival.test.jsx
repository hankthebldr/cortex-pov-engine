/**
 * A transient /api/agents (or tenant-list) failure — SimCore restarting, a
 * 20 s timeout at page load — used to ERASE the operator's saved selection:
 * the fetcher folded the failure into [], the stale-pointer guard then saw the
 * persisted id "missing" from that empty list and wrote null over it
 * (localStorage.removeItem). When SimCore came back, auto-select picked the
 * first ONLINE agent, which need not be the one the DC chose — so the next
 * pull-mode launch went to a different jumpbox without anyone switching it.
 */
import React from 'react'
import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen, renderHook, waitFor } from '@testing-library/react'
import { EnvironmentProvider, useEnvironment, EnvironmentContext, DEFAULT_ENV } from '../../context/EnvironmentContext.jsx'
import TargetsView from '../console/TargetsView.jsx'
import { installRoutes } from '../../test/mockFetch.js'

void React

const down = () => new Response(JSON.stringify({ detail: { error: 'SimCore restarting', code: 'UNAVAILABLE' } }),
  { status: 503, headers: { 'content-type': 'application/json' } })

beforeEach(() => { window.localStorage.clear() })

describe('active agent / tenant survive a failed list fetch', () => {
  it('keeps the persisted agent when /api/agents fails', async () => {
    window.localStorage.setItem('cortexsim.activeAgent', 'jb-2')
    window.localStorage.setItem('cortexsim.activeTenant', 'customer-prod')
    installRoutes({
      'GET /api/agents': down,
      'GET /api/credentials/integrations': down,
      'GET /api/scenarios': { scenarios: [] },
      'GET /api/runs': { runs: [] },
      'GET /api/health': { status: 'ok' },
    })
    const wrapper = ({ children }) => <EnvironmentProvider runPollMs={60_000}>{children}</EnvironmentProvider>
    const { result } = renderHook(() => useEnvironment(), { wrapper })
    await waitFor(() => expect(result.current.loading.agents).toBe(false))
    await waitFor(() => expect(result.current.loading.tenants).toBe(false))
    // let the stale-pointer effects run
    await new Promise((r) => setTimeout(r, 20))
    expect(window.localStorage.getItem('cortexsim.activeAgent')).toBe('jb-2')
    expect(window.localStorage.getItem('cortexsim.activeTenant')).toBe('customer-prod')
    expect(result.current.errors.agents).toMatch(/SimCore restarting/)
  })

  it('still clears a pointer that a SUCCESSFUL list proves stale', async () => {
    window.localStorage.setItem('cortexsim.activeAgent', 'jb-gone')
    installRoutes({
      'GET /api/agents': { agents: [{ agent_id: 'jb-1', status: 'online' }] },
      'GET /api/scenarios': { scenarios: [] },
      'GET /api/runs': { runs: [] },
      'GET /api/health': { status: 'ok' },
    })
    const wrapper = ({ children }) => <EnvironmentProvider runPollMs={60_000}>{children}</EnvironmentProvider>
    renderHook(() => useEnvironment(), { wrapper })
    await waitFor(() => expect(window.localStorage.getItem('cortexsim.activeAgent')).toBe('jb-1'))
  })

  it('the Agents surface says the list failed instead of "No agents registered"', () => {
    render(
      <EnvironmentContext.Provider value={{ ...DEFAULT_ENV, agents: [], errors: { agents: 'SimCore restarting' } }}>
        <TargetsView />
      </EnvironmentContext.Provider>,
    )
    expect(screen.queryByText('No agents registered')).toBeNull()
    expect(screen.getByTestId('agents-load-error').textContent).toContain('SimCore restarting')
  })
})

/**
 * A fresh browser (no persisted pointer) on a SimCore with ONE registered
 * tenant sat on "no tenant bound" in the header while Get started — which
 * reads the tenant LIST, not the pointer — said "Connected to deathstar". The
 * stale-pointer guard only ever re-pointed an id that no longer resolved; it
 * never bound anything when nothing was selected. Same shape as the agent
 * branch directly below it, which auto-selects the one ONLINE agent.
 *
 * Two tenants is deliberately NOT auto-bound: picking one silently would name
 * a tenant the DC did not choose, which is worse than an honest "none".
 */
import React from 'react'
import { describe, it, expect, beforeEach } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { EnvironmentProvider, useEnvironment } from '../../context/EnvironmentContext.jsx'
import { installRoutes } from '../../test/mockFetch.js'

void React

const routes = (tenants) => installRoutes({
  'GET /api/credentials/integrations': tenants,
  'GET /api/agents': { agents: [] },
  'GET /api/scenarios': { scenarios: [] },
  'GET /api/runs': { runs: [] },
  'GET /api/health': { status: 'ok' },
})
const wrapper = ({ children }) => <EnvironmentProvider runPollMs={60_000}>{children}</EnvironmentProvider>

beforeEach(() => { window.localStorage.clear() })

describe('active tenant with nothing persisted', () => {
  it('binds the only registered tenant and persists the choice', async () => {
    routes([{ name: 'deathstar', kind: 'xsiam_tenant', base_url: 'https://api-deathstar.example' }])
    const { result } = renderHook(() => useEnvironment(), { wrapper })
    await waitFor(() => expect(result.current.loading.tenants).toBe(false))
    await waitFor(() => expect(result.current.tenant?.name).toBe('deathstar'))
    expect(window.localStorage.getItem('cortexsim.activeTenant')).toBe('deathstar')
  })

  it('stays unbound when more than one tenant is registered — the operator chooses', async () => {
    routes([
      { name: 'deathstar', kind: 'xsiam_tenant' },
      { name: 'endor', kind: 'xsiam_tenant' },
    ])
    const { result } = renderHook(() => useEnvironment(), { wrapper })
    await waitFor(() => expect(result.current.loading.tenants).toBe(false))
    await new Promise((r) => setTimeout(r, 20))
    expect(result.current.tenant).toBeNull()
    expect(window.localStorage.getItem('cortexsim.activeTenant')).toBeNull()
  })
})

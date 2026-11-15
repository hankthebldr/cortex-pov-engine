/**
 * The launch surface re-reads the agent list (RG-UI-04).
 *
 * The preflight's Target row answers "is this beacon polling right now?" from
 * `useLaunchScenario().agents`. That list was read once, when pull mode began:
 * a beacon that went offline afterwards still read online, and the BLOCK for a
 * just-enrolled row ("enrolled — waiting for first beacon poll") could never
 * clear without leaving the view. These pin the refresh the BLOCK relies on.
 */
import React from 'react'
import { describe, it, expect, vi, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { installRoutes } from '../../test/mockFetch.js'
import useLaunchScenario from '../console/useLaunchScenario.js'
import { AGENT_REFRESH_HINT_S, preflightScenario, PF } from '../console/readiness/scenarioPreflight.js'

void React

const SCENARIO = {
  scenario_id: 'SIM-EDR-001',
  name: 'Credential dumping',
  plane: 'EDR',
  execution_identity: { default: 'root', options: ['root'] },
  steps: [{ id: 'step-01', platforms: ['linux'] }],
  bundle_targets: ['posix'],
}
const T0 = '2026-11-10T10:00:00.123456'
const ENROLLED = {
  agent_id: 'jb-1a2b3c', hostname: 'jb', os: 'linux', status: 'online',
  capabilities: ['shell', 'identity-harness'], registered_at: T0, last_seen: T0, last_seen_age_seconds: 4,
}
const POLLED = { ...ENROLLED, last_seen: '2026-11-10T10:00:14', last_seen_age_seconds: 2 }
const TARGET = { kind: 'agent', id: 'jb-1a2b3c', label: 'jb' }

const targetRow = (agents) => preflightScenario({ scenario: SCENARIO, target: TARGET, agents })
  .checks.find((c) => c.id === 'target')

afterEach(() => { vi.useRealTimers() })

describe('useLaunchScenario — agent list refresh in pull mode', () => {
  it('re-reads the list on the advertised interval, so a just-enrolled BLOCK clears itself', async () => {
    vi.useFakeTimers()
    let rows = [ENROLLED]
    const fetchMock = installRoutes({ 'GET /api/agents': () => ({ agents: rows }), 'GET /api/runs': { runs: [] } })
    const { result, unmount } = renderHook(() => useLaunchScenario(SCENARIO))
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(result.current.agents).toHaveLength(1)
    expect(targetRow(result.current.agents).status).toBe(PF.BLOCK)

    rows = [POLLED]
    await act(async () => { await vi.advanceTimersByTimeAsync(AGENT_REFRESH_HINT_S * 1000) })
    expect(result.current.agents[0].last_seen).toBe(POLLED.last_seen)
    expect(targetRow(result.current.agents).status).toBe(PF.PASS)

    const agentCalls = () => fetchMock.mock.calls.filter(([u]) => String(u).includes('/api/agents')).length
    const before = agentCalls()
    unmount()
    await vi.advanceTimersByTimeAsync(AGENT_REFRESH_HINT_S * 3000)
    expect(agentCalls()).toBe(before)
  })

  it('a refresh that fails keeps the last list rather than emptying the picker', async () => {
    vi.useFakeTimers()
    let fail = false
    installRoutes({
      'GET /api/agents': () => (fail
        ? new Response(JSON.stringify({ error: 'boom', code: 'INTERNAL' }), { status: 500, headers: { 'content-type': 'application/json' } })
        : { agents: [POLLED] }),
      'GET /api/runs': { runs: [] },
    })
    const { result, unmount } = renderHook(() => useLaunchScenario(SCENARIO))
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(result.current.agents).toHaveLength(1)
    fail = true
    await act(async () => { await vi.advanceTimersByTimeAsync(AGENT_REFRESH_HINT_S * 1000) })
    expect(result.current.agents).toHaveLength(1)
    unmount()
  })

  it('does not poll agents in push mode', async () => {
    vi.useFakeTimers()
    const fetchMock = installRoutes({ 'GET /api/agents': { agents: [POLLED] }, 'GET /api/runs': { runs: [] } })
    const { result, unmount } = renderHook(() => useLaunchScenario(SCENARIO))
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    act(() => { result.current.setMode('push') })
    const agentCalls = () => fetchMock.mock.calls.filter(([u]) => String(u).includes('/api/agents')).length
    const before = agentCalls()
    await act(async () => { await vi.advanceTimersByTimeAsync(AGENT_REFRESH_HINT_S * 3000) })
    expect(agentCalls()).toBe(before)
    unmount()
  })
})

/**
 * The pull-mode launch path, end to end at the client seam.
 *
 * Regression guard for the console's core action: a launch against an enrolled
 * beacon posted `target_agent_id: 1` (the DB PK) where SimCore's RunRequest
 * declares a string, so every launch from the console returned HTTP 422. The
 * agent fixture below deliberately carries BOTH `id` and `agent_id` — the
 * condition the old fixtures never reproduced.
 */
import React from 'react'
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { installRoutes } from '../../test/mockFetch.js'
import useLaunchScenario from '../console/useLaunchScenario.js'

void React

const AGENTS = [{ id: 1, agent_id: 'jumpbox-01', hostname: 'ip-10-0-1-24', os: 'linux', status: 'online' }]

const SCENARIO = {
  scenario_id: 'SIM-EDR-001',
  name: 'Credential Dumping',
  plane: 'EDR',
  execution_identity: { default: 'www-data', options: ['www-data', 'root'] },
}

describe('useLaunchScenario — agent identity', () => {
  let posted

  beforeEach(() => {
    posted = []
    installRoutes({
      'GET /api/agents': { agents: AGENTS },
      'POST /api/run': (url, init) => {
        const body = JSON.parse(init.body)
        posted.push(body)
        // Mirror SimCore's contract: a non-string target_agent_id is a 422.
        if (typeof body.target_agent_id !== 'string') {
          return new Response(JSON.stringify({
            detail: [{
              type: 'string_type',
              loc: ['body', 'target_agent_id'],
              msg: 'Input should be a valid string',
              input: body.target_agent_id,
            }],
          }), { status: 422, headers: { 'content-type': 'application/json' } })
        }
        return { id: 7, run_id: 'run-uuid-1', scenario_id: body.scenario_id, status: 'running' }
      },
    })
  })

  it('auto-selects the agent by its string agent_id, not its integer PK', async () => {
    const { result } = renderHook(() => useLaunchScenario(SCENARIO, {}))
    await waitFor(() => expect(result.current.agents).toHaveLength(1))
    expect(result.current.selectedAgent).toBe('jumpbox-01')
  })

  it('posts a launch SimCore accepts (no 422)', async () => {
    const onRunComplete = vi.fn()
    const { result } = renderHook(() => useLaunchScenario(SCENARIO, { onRunComplete }))
    await waitFor(() => expect(result.current.selectedAgent).toBe('jumpbox-01'))

    await act(async () => { await result.current.launch() })

    expect(posted[0].target_agent_id).toBe('jumpbox-01')
    expect(result.current.lastRun.status).toBe('success')
    expect(onRunComplete).toHaveBeenCalled()
  })

  it('reports the run by its uuid run_id, not its PK', async () => {
    const { result } = renderHook(() => useLaunchScenario(SCENARIO, {}))
    await waitFor(() => expect(result.current.selectedAgent).toBe('jumpbox-01'))
    await act(async () => { await result.current.launch() })
    expect(result.current.lastRun.message).toContain('run-uuid-1')
  })

  it('names the offending field when the API rejects the body', async () => {
    installRoutes({
      'GET /api/agents': { agents: AGENTS },
      'POST /api/run': () => new Response(JSON.stringify({
        detail: [{
          type: 'string_type',
          loc: ['body', 'target_agent_id'],
          msg: 'Input should be a valid string',
        }],
      }), { status: 422, headers: { 'content-type': 'application/json' } }),
    })
    const { result } = renderHook(() => useLaunchScenario(SCENARIO, {}))
    await waitFor(() => expect(result.current.selectedAgent).toBe('jumpbox-01'))
    await act(async () => { await result.current.launch() })

    expect(result.current.lastRun.status).toBe('error')
    // The whole point: not "HTTP 422 — Unprocessable Entity".
    expect(result.current.lastRun.message).toBe('target_agent_id: Input should be a valid string')
    expect(result.current.lastRun.message).not.toContain('422')
  })
})

describe('useLaunchScenario — launch blockers', () => {
  it('names "no agent enrolled" instead of silently disabling launch', async () => {
    installRoutes({ 'GET /api/agents': { agents: [] } })
    const { result } = renderHook(() => useLaunchScenario(SCENARIO, {}))
    await waitFor(() => expect(result.current.blockers.length).toBeGreaterThan(0))
    expect(result.current.blockers.join(' ')).toMatch(/No agent enrolled/i)
    expect(result.current.launchDisabled).toBe(true)
  })

  it('clears its blockers once an agent is available', async () => {
    installRoutes({ 'GET /api/agents': { agents: AGENTS } })
    const { result } = renderHook(() => useLaunchScenario(SCENARIO, {}))
    await waitFor(() => expect(result.current.selectedAgent).toBe('jumpbox-01'))
    expect(result.current.blockers).toEqual([])
    expect(result.current.launchDisabled).toBe(false)
  })

  it('blocks with a named reason when no scenario is armed', async () => {
    installRoutes({ 'GET /api/agents': { agents: AGENTS } })
    const { result } = renderHook(() => useLaunchScenario(null, {}))
    await waitFor(() => expect(result.current.blockers.length).toBeGreaterThan(0))
    expect(result.current.blockers.join(' ')).toMatch(/Arm a scenario/i)
  })
})

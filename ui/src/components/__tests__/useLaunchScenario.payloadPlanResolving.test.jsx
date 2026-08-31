/**
 * I-1 — the launch guard itself.
 *
 * destinations.jsx's `useDecodedPlan` can report a `?plan=` deep link's
 * payload plan as still RESOLVING (see destinations.payloadPlan.test.jsx).
 * This is the load-bearing assertion for that fix: `launch()` must refuse
 * to run — and in particular must never POST — while that flag is true, no
 * matter what UI state got it called (a disabled button is not a substitute
 * for the hook refusing on its own, since callers other than the rendered
 * button can still invoke `launch()`).
 */
import React from 'react'
import { describe, it, expect, vi } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { installRoutes } from '../../test/mockFetch.js'
import useLaunchScenario from '../console/useLaunchScenario.js'

void React

const SCENARIO = {
  scenario_id: 'SIM-CDR-001',
  name: 'Container enumeration',
  plane: 'CDR',
  execution_identity: { default: 'container-runtime', options: ['container-runtime'] },
}

const PLAN = {
  v: 1,
  scenario_id: 'SIM-CDR-001',
  artifacts: [{ adapter_id: 'TOOL-LINPEAS', payload_name: 'linpeas.sh', sha256: 'a'.repeat(64), dest_path: '/tmp/linpeas.sh' }],
}

describe('useLaunchScenario — I-1 payload-plan-resolving guard', () => {
  it('refuses to launch, and never POSTs, while payloadPlanResolving is true — even with a plan already present', async () => {
    const posted = []
    installRoutes({
      'GET /api/agents': { agents: [{ agent_id: 'jump-01', hostname: 'jump-01', status: 'online' }] },
      'POST /api/run': (url, init) => {
        posted.push(JSON.parse(init.body))
        return { run_id: 'should-never-happen', status: 'running' }
      },
    })
    const onRunComplete = vi.fn()
    const onError = vi.fn()
    const { result } = renderHook(() => useLaunchScenario(SCENARIO, {
      onRunComplete, onError, payloadPlan: PLAN, payloadPlanResolving: true,
    }))
    await waitFor(() => expect(result.current.agents).toHaveLength(1))

    let run
    await act(async () => { run = await result.current.launch() })

    expect(run).toBeNull()
    // The load-bearing assertion: no POST went out at all, so there is no
    // way a run executed without its payload_plan silently missing.
    expect(posted).toHaveLength(0)
    expect(onRunComplete).not.toHaveBeenCalled()
    expect(result.current.lastRun.status).toBe('error')
    expect(result.current.lastRun.message).toMatch(/still resolving/i)
  })

  it('surfaces the resolving state as a named launch blocker (disables the button, not silently)', async () => {
    installRoutes({ 'GET /api/agents': { agents: [{ agent_id: 'jump-01', status: 'online' }] } })
    const { result } = renderHook(() => useLaunchScenario(SCENARIO, {
      payloadPlan: PLAN, payloadPlanResolving: true,
    }))
    await waitFor(() => expect(result.current.agents).toHaveLength(1))
    expect(result.current.blockers.some((b) => /resolving/i.test(b))).toBe(true)
    expect(result.current.launchDisabled).toBe(true)
  })

  it('launches normally (POSTs) once payloadPlanResolving flips to false', async () => {
    const posted = []
    installRoutes({
      'GET /api/agents': { agents: [{ agent_id: 'jump-01', status: 'online' }] },
      'POST /api/run': (url, init) => {
        posted.push(JSON.parse(init.body))
        return { run_id: 'run-1', status: 'running' }
      },
    })
    const { result, rerender } = renderHook(
      ({ resolving }) => useLaunchScenario(SCENARIO, { payloadPlan: null, payloadPlanResolving: resolving }),
      { initialProps: { resolving: true } },
    )
    await waitFor(() => expect(result.current.agents).toHaveLength(1))

    rerender({ resolving: false })
    await act(async () => { await result.current.launch() })

    expect(posted).toHaveLength(1)
    expect(result.current.lastRun.status).toBe('success')
  })
})

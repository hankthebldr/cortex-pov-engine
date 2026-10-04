/**
 * setupProgress — the Get started checklist model.
 *
 * The rules pinned are the honesty ones: a step is "done" only on real SimCore
 * state or an explicit confirmation (and the two are labelled differently),
 * optional steps never block "ready", and a failed first run is not a
 * finished setup.
 */
import { describe, it, expect } from 'vitest'
import { agentIsOnline, runFailed, runSucceeded, setupProgress, STEP_IDS } from '../console/setupProgress.js'

const online = { agent_id: 'a1', hostname: 'jb', status: 'online' }
const stale = { agent_id: 'a2', hostname: 'old', status: 'offline', last_seen_age_seconds: 900 }

describe('setupProgress', () => {
  it('lists the five steps in order, three required', () => {
    const { steps } = setupProgress()
    expect(steps.map((s) => s.id)).toEqual(STEP_IDS)
    expect(steps.filter((s) => s.required).map((s) => s.id)).toEqual(['agent', 'tenant', 'simulate'])
  })

  it('an empty instance has three required steps left and points at the agent first', () => {
    const p = setupProgress()
    expect(p.requiredLeft).toBe(3)
    expect(p.next.id).toBe('agent')
    expect(p.ready).toBe(false)
  })

  it('an agent counts only when it is online — registered is not enough', () => {
    expect(setupProgress({ agents: [stale] }).steps[0]).toMatchObject({ done: false, status: '1 registered, none online' })
    expect(setupProgress({ agents: [online] }).steps[0]).toMatchObject({ done: true, source: 'verified' })
  })

  it('collector and Broker VM are confirmed by the DC, never verified, and never block ready', () => {
    const p = setupProgress({ agents: [online], tenants: [{ name: 't' }], runs: [{ status: 'completed' }] })
    expect(p.ready).toBe(true)
    const collector = p.steps.find((s) => s.id === 'collector')
    expect(collector).toMatchObject({ required: false, done: false, source: 'confirmed' })
    const confirmed = setupProgress({ confirmed: { collector: true } }).steps.find((s) => s.id === 'collector')
    expect(confirmed).toMatchObject({ done: true, source: 'confirmed', status: 'You confirmed this is set up' })
  })

  it('a failed first run is not done, and says so with the run attached', () => {
    const failed = { run_id: 'r1', status: 'failed' }
    const sim = setupProgress({ agents: [online], runs: [failed] }).steps.find((s) => s.id === 'simulate')
    expect(sim.done).toBe(false)
    expect(sim.failedRun).toBe(failed)
    expect(sim.status).toMatch(/stopped before it finished/)
  })

  it('one completed run finishes the step, even after earlier failures', () => {
    const sim = setupProgress({ runs: [{ status: 'complete' }, { status: 'failed' }] }).steps.find((s) => s.id === 'simulate')
    expect(sim).toMatchObject({ done: true, failedRun: null, status: '1 run completed' })
  })
})

describe('status helpers', () => {
  it('agentIsOnline falls back to last-seen age when status is absent', () => {
    expect(agentIsOnline({ last_seen_age_seconds: 5 })).toBe(true)
    expect(agentIsOnline({ last_seen_age_seconds: 300 })).toBe(false)
    expect(agentIsOnline(null)).toBe(false)
  })

  it('classifies run statuses case-insensitively', () => {
    expect(runSucceeded({ status: 'COMPLETED' })).toBe(true)
    expect(runSucceeded({ status: 'partial' })).toBe(true)
    expect(runFailed({ status: 'Aborted' })).toBe(true)
    expect(runSucceeded({ status: 'running' })).toBe(false)
    expect(runFailed({ status: 'running' })).toBe(false)
  })
})

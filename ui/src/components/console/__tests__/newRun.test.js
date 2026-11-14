/**
 * The New POV run panel's model (design C1): where a run can go, what it will
 * do, and why Run is not allowed yet.
 */
import { describe, it, expect } from 'vitest'
import { runTargets, runPlan, runBlockers, OFFLINE_BUNDLE } from '../newRun.js'

const online = { agent_id: 'a1', hostname: 'web-01', os: 'linux', status: 'online' }
const stale = { agent_id: 'a2', hostname: 'web-02', os: 'linux', status: 'stale' }
const offline = { agent_id: 'a3', hostname: 'old-03', os: 'linux', status: 'offline' }

describe('runTargets', () => {
  it('lists every agent with its state, and an offline agent is listed but not choosable', () => {
    const t = runTargets([online, stale, offline])
    expect(t.map((x) => [x.label, x.word, x.choosable])).toEqual([
      ['web-01', 'Online', true],
      ['web-02', 'Stale', true],
      ['old-03', 'Offline', false],
      ['Offline bundle', 'Unknown', true],
    ])
  })

  it('always offers the offline bundle, as Unknown until it reports in', () => {
    const [bundle] = runTargets([])
    expect(bundle).toMatchObject({ kind: OFFLINE_BUNDLE.kind, id: OFFLINE_BUNDLE.id, status: 'unknown' })
  })
})

describe('runPlan', () => {
  const scenario = {
    steps: [
      { id: 's1', expected_detections: [{}, {}] },
      { id: 's2', expected_detections: [{}] },
    ],
    cleanup: { commands: ['rm -f /tmp/x', 'userdel t'] },
  }

  it('counts steps and detections and says it never writes to Cortex or the firewall', () => {
    const rows = Object.fromEntries(runPlan(scenario, { mode: 'pull', identity: 'www-data' }).map((r) => [r.k, r.v]))
    expect(rows.Steps).toBe('2')
    expect(rows['Detections it should raise']).toBe('3')
    expect(rows['Runs as']).toBe('www-data')
    expect(rows['Writes to Cortex or the firewall']).toBe('never')
  })

  it('says who tears down: the bundle does, an agent run does not', () => {
    const teardown = (mode) => runPlan(scenario, { mode }).find((r) => r.k === 'Teardown').v
    expect(teardown('push')).toBe('2 commands, run by the bundle')
    expect(teardown('pull')).toMatch(/not run by the agent/)
    expect(runPlan({ steps: [] }, { mode: 'pull' }).find((r) => r.k === 'Teardown').v).toBe('nothing to tear down')
  })
})

describe('runBlockers', () => {
  it('needs a target and the authorisation tick, then any gate reasons', () => {
    expect(runBlockers({ base: ['Tick the C2 consent'] })).toEqual([
      'Choose where it runs',
      'Confirm you are authorised to run it on this tenant',
      'Tick the C2 consent',
    ])
  })

  it('is empty once a target is chosen, the tick given and nothing else blocks', () => {
    expect(runBlockers({ target: { kind: 'agent', id: 'a1' }, authorised: true })).toEqual([])
  })
})

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

  // A bundle (bash, PowerShell or the k8s bootstrap) always runs its cleanup.
  // A beacon runs it only when it advertises `cleanup` (#361); an older one is
  // sent the commands and ignores them, so the DC cleans the host by hand.
  describe('says who tears down', () => {
    const teardown = (s, opts) => runPlan(s, opts).find((r) => r.k === 'Teardown').v
    const current = { agent_id: 'a1', capabilities: ['shell', 'identity-harness', 'cleanup'] }
    const older = { agent_id: 'a2', capabilities: ['shell', 'identity-harness'] }

    it('an offline bundle runs it', () => {
      expect(teardown(scenario, { mode: 'push' })).toBe('2 commands, run by the bundle')
      expect(teardown(scenario, { mode: 'push', agent: older })).toBe('2 commands, run by the bundle')
    })

    it('a beacon that advertises cleanup runs it after the run', () => {
      expect(teardown(scenario, { mode: 'pull', agent: current })).toBe('2 commands, run by the agent after the run')
    })

    it('a beacon without it leaves it to the DC', () => {
      expect(teardown(scenario, { mode: 'pull', agent: older })).toBe('2 commands, run them yourself')
      expect(teardown({ steps: [], cleanup: { commands: ['rm -f /tmp/x'] } }, { mode: 'pull', agent: older }))
        .toBe('1 command, run it yourself')
    })

    it('an agent whose record is not loaded is not credited with running it', () => {
      expect(teardown(scenario, { mode: 'pull' })).toBe('2 commands, run them yourself')
      expect(teardown(scenario, { mode: 'pull', agent: { agent_id: 'a3' } })).toBe('2 commands, run them yourself')
    })

    it('nothing to run is said once, whoever runs it', () => {
      expect(teardown({ steps: [] }, { mode: 'pull', agent: current })).toBe('nothing to tear down')
      expect(teardown({ steps: [] }, { mode: 'push' })).toBe('nothing to tear down')
    })
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

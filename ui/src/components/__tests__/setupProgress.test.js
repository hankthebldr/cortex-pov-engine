/**
 * setupProgress — the Get started checklist model.
 *
 * The rules pinned are the honesty ones: a step is "done" only on real SimCore
 * state or an explicit confirmation (and the two are labelled differently),
 * optional steps never block "ready", and a failed first run is not a
 * finished setup.
 */
import { describe, it, expect } from 'vitest'
import { agentIsOnline, agentLiveness, runFailed, runSucceeded, setupProgress, STEP_IDS } from '../console/setupProgress.js'

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

  it('an explicit server status wins over the age<60 fallback (UI-13)', () => {
    // The age fallback exists for builds that ship no status. A SimCore that
    // SAYS stale or offline has answered; a fresh-looking age (clock skew, a
    // cached row) must not overrule it.
    expect(agentIsOnline({ status: 'stale', last_seen_age_seconds: 5 })).toBe(false)
    expect(agentIsOnline({ status: 'offline', last_seen_age_seconds: 5 })).toBe(false)
    // An unrecognised status is not a reason to fall back to "online".
    expect(agentIsOnline({ status: 'zombie', last_seen_age_seconds: 5 })).toBe(false)
  })

  it('classifies run statuses case-insensitively', () => {
    expect(runSucceeded({ status: 'COMPLETED' })).toBe(true)
    expect(runSucceeded({ status: 'partial' })).toBe(true)
    expect(runFailed({ status: 'Aborted' })).toBe(true)
    expect(runSucceeded({ status: 'running' })).toBe(false)
    expect(runFailed({ status: 'running' })).toBe(false)
  })
})

// ── Enrolled is not online (UI-05 / INST-15) ────────────────────────────────
// `POST /api/agents/enroll` writes the row with registered_at == last_seen and
// status 'online', and `GET /api/agents` derives 'online' for the next 30 s —
// all before a beacon has polled once. The Windows installer enrols with
// capabilities ['shell']; only a running beacon re-registers with 'powershell'.
// A DC told "online" at that moment proceeds to launch onto nothing.
const T0 = '2026-11-05T10:00:00.123456'
const enrolledOnly = {
  agent_id: 'jb-1a2b3c', hostname: 'jb', os: 'linux', status: 'online',
  capabilities: ['shell', 'identity-harness'], registered_at: T0, last_seen: T0, last_seen_age_seconds: 4,
}
const winInstallerOnly = {
  agent_id: 'win-4d5e6f', hostname: 'WIN-LAB', os: 'windows', status: 'online', capabilities: ['shell'],
  registered_at: '2026-11-05T10:00:00', last_seen: '2026-11-05T10:00:12', last_seen_age_seconds: 3,
}
const winBeacon = { ...winInstallerOnly, capabilities: ['shell', 'powershell', 'artifact-fetch'] }

describe('enrolled — waiting for first beacon poll', () => {
  it('a row whose last_seen equals registered_at is not online, whatever the server status says', () => {
    expect(agentIsOnline(enrolledOnly)).toBe(false)
  })

  it('equal to the second counts — the two writes share one utcnow()', () => {
    expect(agentIsOnline({ ...enrolledOnly, registered_at: '2026-11-05T10:00:00.100000', last_seen: '2026-11-05T10:00:00.900000' })).toBe(false)
    expect(agentIsOnline({ ...enrolledOnly, last_seen: '2026-11-05T10:00:11.000000' })).toBe(true)
  })

  it('a Windows row without the powershell capability was enrolled by the installer, not registered by a beacon', () => {
    expect(agentIsOnline(winInstallerOnly)).toBe(false)
    expect(agentIsOnline(winBeacon)).toBe(true)
  })

  it('the agent step stays open and never claims "verified" from such a row', () => {
    const step = setupProgress({ agents: [enrolledOnly] }).steps[0]
    expect(step.done).toBe(false)
    expect(step.source).not.toBe('verified')
    expect(step.status).toMatch(/1 enrolled — waiting for first beacon poll/)
  })

  it('one polling beacon still finishes the step beside a waiting one', () => {
    const step = setupProgress({ agents: [enrolledOnly, winBeacon] }).steps[0]
    expect(step).toMatchObject({ done: true, source: 'verified' })
    expect(step.status).toMatch(/^1 online/)
  })
})

describe('agentLiveness — one state, and where it came from', () => {
  it('server status is authoritative and carries the server-side age', () => {
    expect(agentLiveness({ status: 'online', last_seen_age_seconds: 2 })).toMatchObject({ state: 'online', source: 'server-status', ageSeconds: 2 })
    expect(agentLiveness({ status: 'STALE', last_seen_age_seconds: 95 })).toMatchObject({ state: 'stale', source: 'server-status' })
    expect(agentLiveness({ status: 'offline', last_seen_age_seconds: 900 })).toMatchObject({ state: 'offline', ageSeconds: 900 })
  })

  it('an unrecognised status is unknown and names the value, never online', () => {
    const l = agentLiveness({ status: 'zombie', last_seen_age_seconds: 1 })
    expect(l.state).toBe('unknown')
    expect(l.label).toContain('zombie')
  })

  it('enrolled wins over a server "online"', () => {
    expect(agentLiveness(enrolledOnly)).toMatchObject({ state: 'enrolled', label: 'enrolled — waiting for first beacon poll' })
    expect(agentLiveness(winInstallerOnly).state).toBe('enrolled')
  })

  it('without a status it falls back to the server age, then the browser clock', () => {
    expect(agentLiveness({ last_seen_age_seconds: 5 })).toMatchObject({ state: 'online', source: 'server-age' })
    expect(agentLiveness({ last_seen_age_seconds: 300 })).toMatchObject({ state: 'stale', source: 'server-age' })
    const fresh = new Date(Date.now() - 3000).toISOString().replace('Z', '')
    expect(agentLiveness({ last_seen: fresh })).toMatchObject({ state: 'online', source: 'browser-clock' })
    expect(agentLiveness({}).state).toBe('unknown')
    expect(agentLiveness(null).state).toBe('unknown')
  })
})

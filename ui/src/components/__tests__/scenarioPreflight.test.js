import { describe, it, expect } from 'vitest'
import { preflightScenario, PF } from '../console/readiness/scenarioPreflight.js'
import { normalizeShelf } from '../console/supplyState.js'

/**
 * Each case below is a failure this product has actually shipped. The preflight
 * exists so the DC reads it BEFORE the run rather than reading "0/14 detections
 * confirmed (0.0%)" afterwards and having to explain that it was not the
 * customer's coverage that failed.
 */

const AGENT_ONLINE = {
  agent_id: 'jumpbox-01', hostname: 'ip-10-0-1-24', os: 'linux',
  capabilities: ['shell', 'identity-harness', 'artifact-fetch'],
  status: 'online', last_seen_age_seconds: 3,
}
const AGENT_OFFLINE = { ...AGENT_ONLINE, status: 'offline', last_seen_age_seconds: 430675 }
const TARGET = { kind: 'agent', id: 'jumpbox-01', label: 'jumpbox-01' }
const PUSH_TARGET = { kind: 'push', id: 'bundle', label: 'offline bundle' }

const ADAPTERS = {
  'TOOL-LINPEAS': { adapter_id: 'TOOL-LINPEAS', tier: 4, name: 'LinPEAS' },
  'TOOL-NMAP': { adapter_id: 'TOOL-NMAP', tier: 4, name: 'Nmap' },
}

const shelfWith = ({ staged = [], declared = [] } = {}) => normalizeShelf(
  { payloads: staged, dist_dir: '/app/payloads', declared },
  null,
)

const SCENARIO = {
  scenario_id: 'SIM-EDR-022',
  push_supported: true,
  pull_supported: true,
  execution_identity: { default: 'www-data', options: ['www-data', 'root'] },
  external_tools: [{ adapter_ref: 'TOOL-LINPEAS' }],
  steps: [{ id: 'step-01', identity: 'www-data', platforms: ['linux'] }],
  primary_kpi: 'MTTD',
  threshold: { kpi: 'MTTD', op: '<=', value: 300 },
}

const base = (over = {}) => preflightScenario({
  scenario: SCENARIO, target: TARGET, agents: [AGENT_ONLINE],
  shelf: shelfWith({
    staged: [{ name: 'linpeas.sh', sha256: 'a'.repeat(64) }],
    declared: [{ name: 'linpeas.sh', adapter_id: 'TOOL-LINPEAS', url: 'https://example.invalid/linpeas.sh' }],
  }),
  adapterIndex: ADAPTERS,
  integrations: [{ name: 't', kind: 'xsiam_tenant', last_verified_ok: true }],
  identity: 'www-data',
  ...over,
})

const check = (pf, id) => pf.checks.find((c) => c.id === id)

describe('tool supply', () => {
  it('warns when a tool is fetched by the TARGET at run time', () => {
    const pf = base({
      shelf: shelfWith({
        staged: [],
        declared: [{ name: 'linpeas.sh', adapter_id: 'TOOL-LINPEAS', url: 'https://github.com/x/linpeas.sh' }],
      }),
    })
    const tools = check(pf, 'tools')
    expect(tools.status).toBe(PF.WARN)
    expect(tools.detail).toMatch(/default-deny/)
    expect(pf.verdict).toBe(PF.WARN)
  })

  it('is UNKNOWN — not a pass — when the SimCore does not report shelf state', () => {
    const pf = base({ shelf: normalizeShelf(null, null) })
    const tools = check(pf, 'tools')
    expect(tools.status).toBe(PF.UNKNOWN)
    expect(tools.detail).toMatch(/UNKNOWN is not/)
    expect(pf.verdict).not.toBe(PF.PASS)
  })

  it('BLOCKS when a declared cluster payload is not staged (SimCore 409s)', () => {
    const pf = base({
      scenario: { ...SCENARIO, cluster_posture: { payloads: ['kubescape'] } },
    })
    const tools = check(pf, 'tools')
    expect(tools.status).toBe(PF.BLOCK)
    expect(tools.detail).toMatch(/PAYLOAD_NOT_STAGED/)
    expect(pf.blocking.map((b) => b.id)).toContain('tools')
  })

  it('names the empty-adapter-catalog signature rather than shrugging', () => {
    const pf = base({ adapterIndex: {} })
    const tools = check(pf, 'tools')
    expect(tools.status).toBe(PF.UNKNOWN)
    expect(tools.detail).toMatch(/booted without tools\//)
  })

  it('passes cleanly when everything is served from SimCore', () => {
    expect(check(base(), 'tools').status).toBe(PF.PASS)
  })
})

describe('target', () => {
  it('BLOCKS on an offline beacon — a queued task nobody collects is not a run', () => {
    const pf = base({ agents: [AGENT_OFFLINE] })
    const t = check(pf, 'target')
    expect(t.status).toBe(PF.BLOCK)
    expect(t.summary).toMatch(/offline/)
    expect(t.remediation).toMatch(/systemctl/)
  })

  it('warns on a stale beacon', () => {
    const pf = base({ agents: [{ ...AGENT_ONLINE, status: 'stale', last_seen_age_seconds: 95 }] })
    expect(check(pf, 'target').status).toBe(PF.WARN)
  })

  it('BLOCKS a push target on a scenario that declares push_supported: false', () => {
    const pf = base({ scenario: { ...SCENARIO, push_supported: false }, target: PUSH_TARGET })
    expect(check(pf, 'target').status).toBe(PF.BLOCK)
  })

  it('is UNKNOWN when the selected beacon is not in the registry', () => {
    const pf = base({ agents: [] })
    expect(check(pf, 'target').status).toBe(PF.UNKNOWN)
  })
})

describe('platform', () => {
  it('BLOCKS a windows-only scenario against a linux beacon', () => {
    const pf = base({
      scenario: { ...SCENARIO, steps: [{ id: 'step-01', platforms: ['windows'] }] },
    })
    const p = check(pf, 'platform')
    expect(p.status).toBe(PF.BLOCK)
    expect(p.detail).toMatch(/wrong command interpreter/)
    expect(p.detail).toMatch(/passed as green while doing nothing the scenario claimed/)
  })

  it('warns when only SOME steps declare the target OS', () => {
    const pf = base({
      scenario: {
        ...SCENARIO,
        steps: [
          { id: 'step-01', platforms: ['linux'] },
          { id: 'step-02', platforms: ['windows'] },
        ],
      },
    })
    expect(check(pf, 'platform').status).toBe(PF.WARN)
  })

  it('BLOCKS a windows-only scenario emitted as a bash bundle', () => {
    const pf = base({
      scenario: { ...SCENARIO, steps: [{ id: 'step-01', platforms: ['windows'] }] },
      target: PUSH_TARGET, pushFormat: 'bash',
    })
    const p = check(pf, 'platform')
    expect(p.status).toBe(PF.BLOCK)
    expect(p.detail).toMatch(/BUNDLE_TARGET_UNSATISFIABLE/)
  })

  it('accepts container/k8s steps on a linux beacon', () => {
    const pf = base({ scenario: { ...SCENARIO, steps: [{ id: 's', platforms: ['container'] }] } })
    expect(check(pf, 'platform').status).toBe(PF.PASS)
  })

  it('is UNKNOWN when no step declares a platform', () => {
    const pf = base({ scenario: { ...SCENARIO, steps: [{ id: 's' }] } })
    expect(check(pf, 'platform').status).toBe(PF.UNKNOWN)
  })
})

describe('execution identity', () => {
  it('is UNKNOWN — and says what breaks — when the account has not been probed', () => {
    const i = check(base(), 'identity')
    expect(i.status).toBe(PF.UNKNOWN)
    expect(i.detail).toMatch(/nologin/)
    expect(i.detail).toMatch(/This account is currently not available/)
    expect(i.remediation).toMatch(/getent passwd www-data/)
  })

  it('BLOCKS when SimCore probed the target and the account cannot run', () => {
    const pf = base({
      agents: [{ ...AGENT_ONLINE, identity_probe: { 'www-data': { runnable: false } } }],
    })
    const i = check(pf, 'identity')
    expect(i.status).toBe(PF.BLOCK)
    expect(i.detail).toMatch(/WOULD NOT RUN/)
    expect(i.remediation).toMatch(/usermod -s/)
  })

  it('passes when the probe says the account is runnable', () => {
    const pf = base({
      agents: [{ ...AGENT_ONLINE, identity_probe: { 'www-data': { runnable: true } } }],
    })
    expect(check(pf, 'identity').status).toBe(PF.PASS)
  })

  it('warns that Windows will not honour a service identity', () => {
    const pf = base({
      agents: [{ ...AGENT_ONLINE, os: 'windows' }],
      scenario: { ...SCENARIO, steps: [{ id: 's', identity: 'svc-account', platforms: ['windows'] }] },
      identity: 'svc-account',
    })
    const i = check(pf, 'identity')
    expect(i.status).toBe(PF.WARN)
    expect(i.detail).toMatch(/IDENTITY NOT HONOURED/)
  })

  it('passes with no wrapper when the scenario runs direct/root', () => {
    const pf = base({
      scenario: {
        ...SCENARIO,
        execution_identity: { default: 'root', options: ['root'] },
        steps: [{ id: 's', identity: 'root', platforms: ['linux'] }],
      },
      identity: 'root',
    })
    expect(check(pf, 'identity').status).toBe(PF.PASS)
  })

  it('warns on push, where the account precondition is unverifiable from here', () => {
    const pf = base({ target: PUSH_TARGET })
    const i = check(pf, 'identity')
    expect(i.status).toBe(PF.WARN)
    expect(i.detail).toMatch(/NOT a gap in the customer's detection coverage/)
  })
})

describe('measurement', () => {
  it('warns that a scoreable scenario with no credential resolves pending, NOT fail', () => {
    const pf = base({ integrations: [] })
    const m = check(pf, 'measurement')
    expect(m.status).toBe(PF.WARN)
    expect(m.detail).toMatch(/still owed, NOT a failed detection/)
  })

  it('warns when a credential exists but no tenant has ever answered', () => {
    const pf = base({ integrations: [{ name: 't', kind: 'xsiam', last_verified_ok: null }] })
    expect(check(pf, 'measurement').status).toBe(PF.WARN)
  })

  it('calls an unscoreable scenario evidence, not a failure', () => {
    const pf = base({ scenario: { ...SCENARIO, threshold: null } })
    const m = check(pf, 'measurement')
    expect(m.status).toBe(PF.PASS)
    expect(m.detail).toMatch(/not_applicable/)
  })

  it('is UNKNOWN when this SimCore does not report the KPI contract at all', () => {
    const { threshold, primary_kpi, ...noKpi } = SCENARIO
    const pf = base({ scenario: noKpi })
    const m = check(pf, 'measurement')
    expect(m.status).toBe(PF.UNKNOWN)
    expect(m.detail).toMatch(/Unknown, not "no KPI"/)
  })

  it('never claims verification from a reachable tenant', () => {
    const m = check(base(), 'measurement')
    expect(m.status).toBe(PF.PASS)
    expect(m.detail).toMatch(/a reachable tenant is not a verified one/)
  })
})

describe('overall verdict', () => {
  it('is the worst of its parts, and BLOCK outranks everything', () => {
    const pf = base({ agents: [AGENT_OFFLINE] })
    expect(pf.verdict).toBe(PF.BLOCK)
  })

  it('unknown outranks pass — an unchecked precondition is not a ready one', () => {
    const pf = base({ shelf: normalizeShelf(null, null) })
    expect(pf.verdict).toBe(PF.UNKNOWN)
  })

  it('returns UNKNOWN with no checks when there is no scenario', () => {
    const pf = preflightScenario({})
    expect(pf.verdict).toBe(PF.UNKNOWN)
    expect(pf.checks).toEqual([])
  })
})

describe('measurement — the KPI producer gap (found by driving a live SimCore)', () => {
  // CLAUDE.md: only 59 of 169 scenarios declare an MTTD-shaped primary KPI, the
  // only KPI the engine measures natively. SIM-EDR-022 on the live build
  // declares "Causality Chain Completeness" — nothing produces a measured_value
  // for it, so the verdict is `pending` forever, credential or not. Telling a DC
  // "ready to measure" because a tenant is configured promises a verdict that
  // cannot arrive.
  const withKpi = (primary_kpi, threshold) => base({
    scenario: { ...SCENARIO, primary_kpi, threshold },
    integrations: [{ name: 't', kind: 'xsiam_tenant', last_verified_ok: true }],
  })

  it('warns on a KPI the engine cannot produce, even with a verified tenant', () => {
    const m = check(
      withKpi('Causality Chain Completeness', { kpi: 'Causality Chain Completeness', op: '>=', value: 90 }),
      'measurement',
    )
    expect(m.status).toBe(PF.WARN)
    expect(m.detail).toMatch(/resolves `pending` whether or not a tenant is configured/)
  })

  it('accepts MTTD however the corpus spells it', () => {
    for (const kpi of ['MTTD', 'Mean Time to Detect', 'mttd_seconds', 'Detection Latency']) {
      const m = check(withKpi(kpi, { kpi, op: '<=', value: 300 }), 'measurement')
      expect(m.status, `${kpi} should be measurable`).toBe(PF.PASS)
    }
  })

  it('still warns first about a missing credential — that is the fixable one', () => {
    const pf = base({
      scenario: { ...SCENARIO, primary_kpi: 'MTTD', threshold: { kpi: 'MTTD', op: '<=', value: 300 } },
      integrations: [],
    })
    expect(check(pf, 'measurement').detail).toMatch(/still owed, NOT a failed detection/)
  })
})

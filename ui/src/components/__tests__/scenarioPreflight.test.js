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
  it('warns when a tool needs internet access on the TARGET', () => {
    const pf = base({
      shelf: shelfWith({
        staged: [],
        declared: [{ name: 'linpeas.sh', adapter_id: 'TOOL-LINPEAS', url: 'https://github.com/x/linpeas.sh' }],
      }),
    })
    const tools = check(pf, 'tools')
    expect(tools.status).toBe(PF.WARN)
    expect(tools.detail).toMatch(/installed by the target itself/)
    expect(tools.detail).toMatch(/runs without its tool/)
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
    // A current SimCore (it reports bundle_targets), so the platform verdict is
    // server truth and adds no label-based warning of its own.
    const pf = base({
      scenario: { ...SCENARIO, bundle_targets: ['posix'] },
      shelf: normalizeShelf(null, null),
    })
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

// ─── UI-08 · platform from SERVER truth, not step labels ─────────────────────
//
// ~100 corpus steps are labelled `windows` while carrying POSIX text. SIM-EDR-014
// is the sharpest case: every step says `platforms: [windows]`, and the push
// generator — reading the command TEXT — can only emit it for POSIX. The label
// heuristic therefore BLOCKED the one target that runs it (a Linux beacon) and
// cleared the one that cannot (a Windows beacon). `bundle_targets` comes from
// the same resolver the download route gates on.
describe('platform — server truth (bundle_targets)', () => {
  const EDR_014 = {
    ...SCENARIO,
    scenario_id: 'SIM-EDR-014',
    steps: [
      { id: 'step-01', identity: 'www-data', platforms: ['windows'] },
      { id: 'step-02', identity: 'www-data', platforms: ['windows'] },
    ],
    bundle_targets: ['posix'],
  }
  const WIN_AGENT = { ...AGENT_ONLINE, os: 'windows', capabilities: ['shell', 'powershell'] }

  it('SIM-EDR-014 shape: a Linux beacon PASSes on server truth', () => {
    const pf = base({ scenario: EDR_014 })
    const p = check(pf, 'platform')
    expect(p.status).toBe(PF.PASS)
    expect(p.summary).toMatch(/posix/)
  })

  it('SIM-EDR-014 shape: a Windows beacon BLOCKS — SimCore has no Windows text for it', () => {
    const pf = base({ scenario: EDR_014, agents: [WIN_AGENT] })
    const p = check(pf, 'platform')
    expect(p.status).toBe(PF.BLOCK)
    expect(p.detail).toMatch(/PULL_TARGET_UNSATISFIABLE/)
    expect(pf.blocking.map((b) => b.id)).toContain('platform')
  })

  it('SIM-EDR-014 shape: push + bash PASSes', () => {
    const pf = base({ scenario: EDR_014, target: PUSH_TARGET, pushFormat: 'bash' })
    expect(check(pf, 'platform').status).toBe(PF.PASS)
  })

  it('SIM-EDR-014 shape: push + powershell BLOCKS, naming the 409 SimCore will send', () => {
    const pf = base({ scenario: EDR_014, target: PUSH_TARGET, pushFormat: 'powershell' })
    const p = check(pf, 'platform')
    expect(p.status).toBe(PF.BLOCK)
    expect(p.detail).toMatch(/BUNDLE_TARGET_UNSATISFIABLE/)
  })

  it('labels that disagree with server truth are a SECONDARY warning, not the verdict', () => {
    const pf = base({ scenario: EDR_014 })
    expect(check(pf, 'platform').status).toBe(PF.PASS)
    const labels = check(pf, 'platform-labels')
    expect(labels).toBeTruthy()
    expect(labels.status).toBe(PF.WARN)
    expect(labels.detail).toMatch(/windows/)
  })

  it('labels that agree add no row', () => {
    const pf = base({ scenario: { ...SCENARIO, bundle_targets: ['posix'] } })
    expect(check(pf, 'platform').status).toBe(PF.PASS)
    expect(check(pf, 'platform-labels')).toBeUndefined()
  })

  it('a windows-only scenario PASSes push with powershell or auto, and BLOCKS with bash', () => {
    const win = {
      ...SCENARIO,
      steps: [{ id: 'step-01', platforms: ['windows'] }],
      bundle_targets: ['windows'],
    }
    for (const fmt of ['powershell', 'auto']) {
      const p = check(base({ scenario: win, target: PUSH_TARGET, pushFormat: fmt }), 'platform')
      expect(p.status, fmt).toBe(PF.PASS)
    }
    expect(check(base({ scenario: win, target: PUSH_TARGET, pushFormat: 'bash' }), 'platform').status)
      .toBe(PF.BLOCK)
  })

  it('a Windows beacon PASSes when SimCore can emit Windows text', () => {
    const pf = base({
      scenario: { ...SCENARIO, steps: [{ id: 'step-01', platforms: ['windows'] }], bundle_targets: ['windows'] },
      agents: [WIN_AGENT],
    })
    expect(check(pf, 'platform').status).toBe(PF.PASS)
  })

  it('an empty bundle_targets on a push-capable scenario BLOCKS both modes', () => {
    const none = { ...SCENARIO, bundle_targets: [] }
    expect(check(base({ scenario: none }), 'platform').status).toBe(PF.BLOCK)
    expect(check(base({ scenario: none, target: PUSH_TARGET, pushFormat: 'auto' }), 'platform').status)
      .toBe(PF.BLOCK)
  })

  it('an empty bundle_targets on push_supported:false says nothing about pull — falls back to labels', () => {
    const agentOnly = { ...SCENARIO, push_supported: false, bundle_targets: [] }
    const pf = base({ scenario: agentOnly })
    expect(check(pf, 'platform').status).toBe(PF.PASS)
    expect(check(pf, 'platform-labels').status).toBe(PF.WARN)
  })

  it('an unrecognised bundle_targets shape is not read as server truth', () => {
    const pf = base({ scenario: { ...SCENARIO, bundle_targets: 'posix' } })
    expect(check(pf, 'platform-labels').status).toBe(PF.WARN)
    expect(check(pf, 'platform-labels').detail).toMatch(/unrecognised/)
  })

  it('an unknown agent OS stays UNKNOWN even with server truth', () => {
    const pf = base({
      scenario: { ...SCENARIO, bundle_targets: ['posix'] },
      agents: [{ ...AGENT_ONLINE, os: '' }],
    })
    expect(check(pf, 'platform').status).toBe(PF.UNKNOWN)
  })
})

describe('platform — older SimCore without bundle_targets (label heuristic)', () => {
  it('keeps the label verdict and adds a WARN that it is label-based', () => {
    const pf = base()
    expect(check(pf, 'platform').status).toBe(PF.PASS)
    const labels = check(pf, 'platform-labels')
    expect(labels.status).toBe(PF.WARN)
    expect(labels.summary).toMatch(/label-based/)
    expect(pf.verdict).not.toBe(PF.PASS)
  })

  it('a windows-only scenario PASSes push with powershell or auto on labels alone', () => {
    const win = { ...SCENARIO, steps: [{ id: 'step-01', platforms: ['windows'] }] }
    for (const fmt of ['powershell', 'auto']) {
      expect(check(base({ scenario: win, target: PUSH_TARGET, pushFormat: fmt }), 'platform').status, fmt)
        .toBe(PF.PASS)
    }
  })

  it('no labels and no server truth stays UNKNOWN with no label-based row', () => {
    const pf = base({ scenario: { ...SCENARIO, steps: [{ id: 's' }] } })
    expect(check(pf, 'platform').status).toBe(PF.UNKNOWN)
    expect(check(pf, 'platform-labels')).toBeUndefined()
  })
})

// ── Inside the POSIX family (RG-UI-01) ──────────────────────────────────────
// `bundle_targets` separates POSIX from Windows and nothing else: SimCore maps a
// darwin beacon to `posix` and dispatches the Linux primary to it with no
// marker. Round 1 took `bundle_targets` as the whole verdict, so a macOS beacon
// read PASS on every Linux-only scenario (and SIM-KOI-009, macOS-only, read PASS
// on a Linux beacon). The step labels are the only linux-versus-macOS signal
// there is, so they stay the verdict INSIDE the POSIX family.
describe('platform — inside the POSIX family (RG-UI-01)', () => {
  const MAC = { ...AGENT_ONLINE, os: 'darwin' }
  const linuxOnly = {
    ...SCENARIO,
    steps: [
      { id: 'step-01', identity: 'www-data', platforms: ['linux'] },
      { id: 'step-02', identity: 'www-data', platforms: ['linux', 'container'] },
    ],
    bundle_targets: ['posix'],
  }
  const macOnly = {
    ...SCENARIO, scenario_id: 'SIM-KOI-009',
    steps: [{ id: 'step-01', platforms: ['macos'] }, { id: 'step-02', platforms: ['macos'] }],
    bundle_targets: ['posix'],
  }

  it('a macOS beacon BLOCKS on a Linux-only scenario, whatever name the beacon gave its OS', () => {
    for (const os of ['darwin', 'macos']) {
      const pf = base({ scenario: linuxOnly, agents: [{ ...MAC, os }] })
      const p = check(pf, 'platform')
      expect(p.status, os).toBe(PF.BLOCK)
      expect(p.summary, os).toBe(`no step declares ${os}`)
      expect(pf.blocking.map((b) => b.id), os).toContain('platform')
    }
  })

  it('says SimCore will not refuse it — the console is the only guard', () => {
    const p = check(base({ scenario: linuxOnly, agents: [MAC] }), 'platform')
    expect(p.detail).toMatch(/SimCore does not refuse/)
    expect(p.detail).not.toMatch(/PULL_TARGET_UNSATISFIABLE/)
  })

  it('says SimCore marks each such step PLATFORM MISMATCH on the run (server half)', () => {
    for (const scenario of [linuxOnly, {
      ...linuxOnly, steps: [...linuxOnly.steps, { id: 'step-03', platforms: ['linux', 'macos'] }],
    }]) {
      const p = check(base({ scenario, agents: [MAC] }), 'platform')
      expect(p.detail).toMatch(/PLATFORM MISMATCH/)
      expect(p.detail).not.toMatch(/no marker/)
    }
  })

  it('a Linux beacon BLOCKS on a macOS-only scenario (SIM-KOI-009 shape)', () => {
    const p = check(base({ scenario: macOnly }), 'platform')
    expect(p.status).toBe(PF.BLOCK)
    expect(p.summary).toBe('no step declares linux')
  })

  it('WARNs when only some POSIX-labelled steps declare the beacon OS', () => {
    const mixed = {
      ...SCENARIO,
      steps: [
        { id: 's1', platforms: ['linux'] }, { id: 's2', platforms: ['linux'] },
        { id: 's3', platforms: ['macos'] },
      ],
      bundle_targets: ['posix'],
    }
    const pf = base({ scenario: mixed, agents: [MAC] })
    const p = check(pf, 'platform')
    expect(p.status).toBe(PF.WARN)
    expect(p.summary).toBe('2 of 3 platform-bearing steps do not declare darwin')
    expect(pf.blocking.map((b) => b.id)).not.toContain('platform')
  })

  it('PASSes when every POSIX-labelled step declares the beacon OS', () => {
    expect(check(base({ scenario: macOnly, agents: [MAC] }), 'platform').status).toBe(PF.PASS)
    const both = { ...linuxOnly, steps: [{ id: 's', platforms: ['linux', 'macos'] }] }
    expect(check(base({ scenario: both, agents: [MAC] }), 'platform').status).toBe(PF.PASS)
    expect(check(base({ scenario: both }), 'platform').status).toBe(PF.PASS)
  })

  it('a windows-only LABEL says nothing about linux versus macOS, so it cannot block a POSIX beacon', () => {
    // SIM-EDR-014 shape: POSIX text under `platforms: [windows]`. The label
    // disagreement stays the secondary WARN; it is not an intra-POSIX verdict.
    const mislabelled = { ...SCENARIO, steps: [{ id: 's', platforms: ['windows'] }], bundle_targets: ['posix'] }
    for (const agent of [AGENT_ONLINE, MAC]) {
      const pf = base({ scenario: mislabelled, agents: [agent] })
      expect(check(pf, 'platform').status, agent.os).toBe(PF.PASS)
      expect(check(pf, 'platform-labels').status, agent.os).toBe(PF.WARN)
    }
  })

  it('a push bundle has no beacon OS to compare, so the label check does not apply', () => {
    const p = check(base({ scenario: macOnly, target: PUSH_TARGET, pushFormat: 'bash' }), 'platform')
    expect(p.status).toBe(PF.PASS)
  })
})

// ── Target liveness is the ONE model (RG-UI-04) ─────────────────────────────
// POST /api/agents/enroll writes registered_at == last_seen and status 'online';
// GET /api/agents then says 'online' for 30 s before any beacon has polled. The
// Agents card called that row "enrolled — waiting for first beacon poll" and
// said "do not launch onto it" while this row said PASS "12s since last poll".
describe('target — an enrolled-only row is not a beacon (RG-UI-04)', () => {
  const T0 = '2026-11-10T10:00:00.123456'
  const ENROLLED_WIN = {
    agent_id: 'jumpbox-01', hostname: 'WIN22', os: 'windows', status: 'online',
    capabilities: ['shell', 'powershell'], registered_at: T0, last_seen: T0, last_seen_age_seconds: 12,
  }

  it('BLOCKS, in the words every other surface uses, and claims no poll', () => {
    const pf = base({ agents: [ENROLLED_WIN] })
    const t = check(pf, 'target')
    expect(t.status).toBe(PF.BLOCK)
    expect(t.summary).toMatch(/enrolled — waiting for first beacon poll/)
    expect(`${t.summary} ${t.detail}`).not.toMatch(/since last poll/)
    expect(t.summary).not.toMatch(/online/)
    expect(pf.blocking.map((b) => b.id)).toContain('target')
  })

  it('the same for a POSIX row, with a POSIX fix line', () => {
    const t = check(base({ agents: [{ ...ENROLLED_WIN, hostname: 'jb', os: 'linux', capabilities: ['shell', 'identity-harness'] }] }), 'target')
    expect(t.status).toBe(PF.BLOCK)
    expect(t.remediation).toMatch(/systemctl/)
    expect(t.remediation).not.toMatch(/sc\.exe/)
  })

  it('a row whose beacon has polled since enrolment still PASSes, with its poll age', () => {
    const t = check(base({ agents: [{ ...ENROLLED_WIN, last_seen: '2026-11-10T10:00:20', last_seen_age_seconds: 4 }] }), 'target')
    expect(t.status).toBe(PF.PASS)
    expect(t.detail).toMatch(/4s since last poll/)
  })

  it('an unrecognised status is UNKNOWN, never read as online', () => {
    const t = check(base({ agents: [{ ...AGENT_ONLINE, status: 'zombie' }] }), 'target')
    expect(t.status).toBe(PF.UNKNOWN)
    expect(t.summary).toMatch(/zombie/)
  })
})

// ── A .ps1 bundle is not told about getent and nologin (UI-19) ──────────────
// The push identity row described a POSIX wrapper for every bundle. A .ps1
// bundle cannot switch accounts at all: a service-account step runs as whoever
// runs the bundle (logged `cortexsim-identity-degraded`), and a root /
// administrator step is skipped NOT_ELEVATED from a non-elevated session.
describe('execution identity — a PowerShell bundle gets Windows words (UI-19)', () => {
  const WIN = {
    ...SCENARIO,
    steps: [{ id: 'step-01', identity: 'www-data', platforms: ['windows'] }],
    bundle_targets: ['windows'],
  }

  it('says the account will not be honoured, and names the bundle\'s own markers', () => {
    for (const fmt of ['powershell', 'auto']) {
      const i = check(base({ scenario: WIN, target: PUSH_TARGET, pushFormat: fmt }), 'identity')
      expect(i.status, fmt).toBe(PF.WARN)
      expect(i.summary, fmt).toMatch(/will NOT be honoured by a \.ps1 bundle/)
      expect(i.detail, fmt).toContain('cortexsim-identity-degraded')
      expect(i.detail, fmt).toContain('NOT_ELEVATED')
      expect(`${i.detail} ${i.remediation}`, fmt).not.toMatch(/getent|nologin|runuser/)
      expect(i.detail, fmt).toMatch(/no bundle has run on a Windows host/i)
    }
  })

  it('a bash bundle keeps the POSIX account advice', () => {
    const i = check(base({ target: PUSH_TARGET, pushFormat: 'bash' }), 'identity')
    expect(i.status).toBe(PF.WARN)
    expect(i.remediation).toMatch(/getent passwd www-data/)
  })
})

// ── Remediation names the target's own supervisor (UI-19) ───────────────────
describe('target — the fix line matches the beacon OS (UI-19)', () => {
  it('an offline Windows beacon is not told to run systemctl', () => {
    const t = check(base({ agents: [{ ...AGENT_OFFLINE, os: 'windows', capabilities: ['shell', 'powershell', 'artifact-fetch'] }] }), 'target')
    expect(t.status).toBe(PF.BLOCK)
    expect(t.remediation).toMatch(/sc\.exe query CortexSimAgent/)
    expect(t.remediation).toMatch(/Restart-Service CortexSimAgent/)
    expect(t.remediation).not.toMatch(/systemctl/)
  })

  it('an offline macOS beacon is pointed at launchd', () => {
    const t = check(base({ agents: [{ ...AGENT_OFFLINE, os: 'darwin' }] }), 'target')
    expect(t.remediation).toMatch(/launchctl/)
    expect(t.remediation).not.toMatch(/systemctl/)
  })

  it('an offline Linux beacon keeps systemctl, including the per-user unit a non-root install creates', () => {
    const t = check(base({ agents: [AGENT_OFFLINE] }), 'target')
    expect(t.remediation).toMatch(/systemctl status cortexsim-agent/)
    expect(t.remediation).toMatch(/systemctl --user status cortexsim-agent/)
  })
})

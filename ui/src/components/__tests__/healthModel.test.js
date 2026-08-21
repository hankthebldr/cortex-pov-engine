import { describe, it, expect } from 'vitest'
import {
  buildHealthModel, normalizeComponent, healthHeadline, HS,
} from '../console/readiness/healthModel.js'

/**
 * The manufactured-green guard for platform readiness.
 *
 * Two deployments break hardest AND report `status: "ok"`: booted without
 * `tools/` (adapter_catalog count 0 → every adapter_ref unresolvable, the shelf
 * can stage nothing) and booted without `scenarios/` (nothing to launch). Every
 * assertion below exists to make "ok with a count of 0" unreachable in the UI.
 */

const OK_BODY = {
  status: 'ok',
  version: '1.0.0',
  commit: 'abc1234',
  components: {
    db: { status: 'ok' },
    scenario_catalog: { status: 'ok', count: 170 },
    ttp_catalog: { status: 'ok', count: 170 },
    adapter_catalog: { status: 'ok', count: 91 },
    uctc_registry: { status: 'ok', count: 266, version: 'v2.2' },
    eal: { status: 'ok', plugins: 21 },
  },
}

describe('buildHealthModel — the zero-count rule', () => {
  it('downgrades an adapter_catalog the SERVER called ok with count 0', () => {
    const body = {
      ...OK_BODY,
      components: { ...OK_BODY.components, adapter_catalog: { status: 'ok', count: 0 } },
    }
    const m = buildHealthModel(body)
    const adapters = m.components.find((c) => c.key === 'adapter_catalog')

    expect(adapters.status).toBe(HS.DEGRADED)
    // The disagreement is preserved, not hidden — nobody should have to guess
    // which side is lying.
    expect(adapters.serverSaidOk).toBe(true)
    expect(adapters.disagreement).toMatch(/count of 0/)
    expect(adapters.remediation).toMatch(/tools\//)
    expect(m.overall).toBe(HS.DEGRADED)
    expect(m.degraded).toContain('adapter_catalog')
  })

  it('downgrades scenario_catalog at 0 — an empty Library is not a healthy one', () => {
    const body = {
      ...OK_BODY,
      components: { ...OK_BODY.components, scenario_catalog: { status: 'ok', count: 0 } },
    }
    const m = buildHealthModel(body)
    expect(m.components.find((c) => c.key === 'scenario_catalog').status).toBe(HS.DEGRADED)
    expect(m.overall).toBe(HS.DEGRADED)
  })

  it('leaves a healthy deployment alone', () => {
    const m = buildHealthModel(OK_BODY)
    expect(m.overall).toBe(HS.OK)
    expect(m.degraded).toEqual([])
    expect(m.components.find((c) => c.key === 'eal').count).toBe(21)
  })

  it('does not apply the zero rule where zero is legitimate (db has no count)', () => {
    const m = buildHealthModel(OK_BODY)
    expect(m.components.find((c) => c.key === 'db').status).toBe(HS.OK)
  })
})

describe('buildHealthModel — absent components are gaps, never zeros', () => {
  it('reports payload_shelf / agent_binaries / integrations as NOT REPORTED', () => {
    const m = buildHealthModel(OK_BODY)
    const keys = m.gaps.map((g) => g.key)
    expect(keys).toEqual(expect.arrayContaining([
      'payload_shelf', 'agent_binaries', 'integrations', 'xsiam_operation_catalog',
    ]))
    for (const g of m.gaps) expect(g.why).toBeTruthy()
  })

  it('a gap does NOT turn the deployment amber — unknown is not degraded', () => {
    const m = buildHealthModel(OK_BODY)
    expect(m.overall).toBe(HS.OK)
    expect(m.degraded).toEqual([])
    // …but it is never silent.
    expect(m.gaps.length).toBeGreaterThan(0)
    expect(healthHeadline(m)).toMatch(/not reported/)
  })

  it('an unreported component is never given a count', () => {
    const c = normalizeComponent('payload_shelf', undefined)
    expect(c.status).toBe(HS.UNREPORTED)
    expect(c.count).toBeNull()
    expect(c.serverSaidOk).toBe(false)
  })
})

describe('buildHealthModel — payload_shelf ok-condition', () => {
  it('is degraded when staged < declared even if the server said ok', () => {
    const m = buildHealthModel({
      ...OK_BODY,
      components: {
        ...OK_BODY.components,
        payload_shelf: { status: 'ok', declared: 8, staged: 0, unpinned: 0 },
      },
    })
    const shelf = m.components.find((c) => c.key === 'payload_shelf')
    expect(shelf.status).toBe(HS.DEGRADED)
    expect(shelf.disagreement).toMatch(/0\/8/)
  })

  it('is degraded when an artifact is unpinned', () => {
    const m = buildHealthModel({
      ...OK_BODY,
      components: {
        ...OK_BODY.components,
        payload_shelf: { status: 'ok', declared: 8, staged: 8, unpinned: 1 },
      },
    })
    expect(m.components.find((c) => c.key === 'payload_shelf').status).toBe(HS.DEGRADED)
  })

  it('accepts a fully staged, fully pinned shelf', () => {
    const m = buildHealthModel({
      ...OK_BODY,
      components: {
        ...OK_BODY.components,
        payload_shelf: { status: 'ok', declared: 8, staged: 8, unpinned: 0 },
      },
    })
    expect(m.components.find((c) => c.key === 'payload_shelf').status).toBe(HS.OK)
  })
})

describe('buildHealthModel — server-declared degradation', () => {
  it('unions the server degraded_components list with our own derivation', () => {
    const m = buildHealthModel({
      ...OK_BODY,
      status: 'degraded',
      degraded_components: ['uctc_registry'],
      components: {
        ...OK_BODY.components,
        uctc_registry: { status: 'degraded', count: 0, detail: 'snapshot absent' },
        adapter_catalog: { status: 'ok', count: 0 },
      },
    })
    expect(m.degraded).toEqual(expect.arrayContaining(['uctc_registry', 'adapter_catalog']))
  })

  it('an errored component outranks a degraded one', () => {
    const m = buildHealthModel({
      ...OK_BODY,
      components: { ...OK_BODY.components, db: { status: 'error', detail: 'file is not a database' } },
    })
    expect(m.overall).toBe(HS.ERROR)
    expect(m.components.find((c) => c.key === 'db').detail).toMatch(/not a database/)
  })
})

describe('buildHealthModel — unreachable', () => {
  it('never returns ok when the probe itself failed', () => {
    const m = buildHealthModel(null, { error: 'Failed to fetch' })
    expect(m.reachable).toBe(false)
    expect(m.overall).toBe(HS.ERROR)
    expect(m.unreachableReason).toBe('Failed to fetch')
    expect(healthHeadline(m)).toBe('SimCore unreachable')
  })

  it('surfaces an unmodelled component the server invents rather than dropping it', () => {
    const m = buildHealthModel({
      ...OK_BODY,
      components: { ...OK_BODY.components, brand_new_probe: { status: 'degraded', count: 3 } },
    })
    expect(m.components.map((c) => c.key)).toContain('brand_new_probe')
    expect(m.degraded).toContain('brand_new_probe')
  })
})

describe('the CURRENT SimCore health contract (captured live, 2026-08-05)', () => {
  // Verbatim shape from a running build with the readiness work landed. Pinned
  // here so a console change cannot silently stop rendering the fields the
  // platform side is producing — the "two tracks built the two ends of one wire
  // and disagreed on field names" trap.
  const LIVE = {
    status: 'ok',
    version: '1.0.0',
    components: {
      db: { status: 'ok', code: 'OK' },
      scenario_catalog: { status: 'ok', code: 'OK', count: 170 },
      ttp_catalog: { status: 'ok', code: 'OK', count: 170 },
      adapter_catalog: { status: 'ok', code: 'OK', count: 91 },
      uctc_registry: { status: 'ok', code: 'OK', count: 266, version: '2.2', strict_refs: true },
      eal: { status: 'ok', code: 'OK', plugins: 21 },
      xsiam_operation_catalog: { status: 'ok', code: 'OK', count: 116 },
      payload_shelf: {
        status: 'ok', code: 'OK', declared: 8, staged: 8, unpinned: 0,
        dist_dir: '/app/payloads', missing: [], blocks_scenarios: [],
      },
      agent_binaries: {
        status: 'ok', code: 'OK', available: ['linux/amd64', 'windows/amd64'], missing: [],
      },
      agents: {
        status: 'ok', code: 'OK', code_hint: 'NO_AGENTS_ENROLLED',
        detail: 'no agents are enrolled. Pull-mode launches have nowhere to go until one is;',
        registered: 0, online: 0, derived_from: 'last_seen age; no outbound probe is made',
      },
      integrations: {
        status: 'ok', code: 'OK', configured: 0, verified: 0,
        detail: 'NOT a liveness probe. No tenant call is made by /api/health.',
      },
      task_queue: { status: 'ok', code: 'OK', queued: 0, stranded: {} },
      eal_collectors: { status: 'ok', code: 'OK', campaigns: 0, reachability_checked: false },
    },
    degraded_components: [],
    not_checked: [
      {
        what: 'xsiam_tenant_reachability',
        why: 'a health probe must not spend the customer\'s API quota',
        checked_by: 'POST /api/xsiam/tenants/{name}/test',
      },
      {
        what: 'detection_efficacy',
        why: 'whether a detection fired is a measurement against a tenant',
        checked_by: 'POST /api/runs/{id}/reconcile',
      },
    ],
  }

  it('reports the deployment healthy with no invented gaps', () => {
    const m = buildHealthModel(LIVE)
    expect(m.overall).toBe(HS.OK)
    expect(m.degraded).toEqual([])
    // Every component the contract requires is reported on this build.
    expect(m.gaps).toEqual([])
  })

  it('carries the server\'s not_checked declaration through to the surface', () => {
    const m = buildHealthModel(LIVE)
    expect(m.notChecked.map((n) => n.what)).toEqual([
      'xsiam_tenant_reachability', 'detection_efficacy',
    ])
    expect(m.notChecked[0].checkedBy).toBe('POST /api/xsiam/tenants/{name}/test')
  })

  it('keeps the per-component code_hint rather than paraphrasing it', () => {
    const agents = buildHealthModel(LIVE).components.find((c) => c.key === 'agents')
    expect(agents.codeHint).toBe('NO_AGENTS_ENROLLED')
    expect(agents.code).toBe('OK')
    expect(agents.count).toBe(0)
    // 0 enrolled beacons is a legitimate configuration, not a fault.
    expect(agents.status).toBe(HS.OK)
  })

  it('labels the newer components instead of dumping raw keys', () => {
    const m = buildHealthModel(LIVE)
    const labels = Object.fromEntries(m.components.map((c) => [c.key, c.label]))
    expect(labels.agents).toBe('Enrolled beacons')
    expect(labels.task_queue).toBe('Task queue')
    expect(labels.eal_collectors).toBe('EAL collectors')
  })

  it('renders the fully staged shelf as ok, and reads `staged` as its count', () => {
    const shelf = buildHealthModel(LIVE).components.find((c) => c.key === 'payload_shelf')
    expect(shelf.status).toBe(HS.OK)
    expect(shelf.count).toBe(8)
    expect(shelf.extra.declared).toBe(8)
  })
})

describe('one fault, one description', () => {
  // Found by driving a SimCore booted with an empty /app/tools: the card printed
  // the server's precise remediation AND our generic fallback, back to back.
  const withDetail = buildHealthModel({
    status: 'degraded',
    degraded_components: ['adapter_catalog'],
    components: {
      db: { status: 'ok' },
      adapter_catalog: {
        status: 'degraded', code: 'CATALOG_EMPTY', count: 0,
        detail: '0 adapter packs loaded — set CORTEXSIM_BASE_DIR at a checkout with tools/packs/.',
      },
    },
  })

  it('suppresses the generic fallback when the server shipped a specific one', () => {
    const c = withDetail.components.find((x) => x.key === 'adapter_catalog')
    expect(c.detail).toMatch(/CORTEXSIM_BASE_DIR/)
    expect(c.remediation).toBe('')
    expect(c.code).toBe('CATALOG_EMPTY')
  })

  it('still supplies a fallback when the server shipped nothing', () => {
    const m = buildHealthModel({
      status: 'ok',
      components: { adapter_catalog: { status: 'ok', count: 0 } },
    })
    const c = m.components.find((x) => x.key === 'adapter_catalog')
    expect(c.detail).toBeNull()
    expect(c.remediation).toMatch(/tools\//)
  })
})

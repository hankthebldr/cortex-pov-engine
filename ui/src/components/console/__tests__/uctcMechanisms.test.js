/**
 * Unit tests for the UC/TC mechanism model.
 *
 * These pin the three separations the surface exists to protect. Each one, if
 * it silently collapsed, would produce a coverage readout that reads fine and
 * means nothing:
 *
 *   1. proven-by-scenario     vs  proven-by-assertion
 *   2. open-and-measurable    vs  open-but-blocked-on-a-data-path
 *   3. mechanism-deployed     vs  content-authored
 *
 * …plus the one absolute ceiling: a row with no measurable threshold can be
 * evidenced but can never return ``pass``.
 */
import { describe, it, expect } from 'vitest'
import {
  MECHANISM,
  REACH,
  assertionAvailability,
  classCoverage,
  coverageTotals,
  indexAssertionRuns,
  indexAssertions,
  latestVerdict,
  proofScope,
  readPath,
  tallySurfaces,
} from '../uctcMechanisms.js'

const tc = (over = {}) => ({
  tc_id: 'TC-X-01',
  validation_class: 'DET',
  is_scoreable: true,
  detection_source: 'XSIAM UI + XQL',
  evidence: { evidenced: false, scenario_count: 0, scenario_ids: [], planes: [] },
  ...over,
})

describe('readPath', () => {
  it('reads an XQL surface as directly measurable', () => {
    expect(readPath(tc()).reach).toBe(REACH.DIRECT)
    expect(readPath(tc({ detection_source: 'XSIAM Causality View (XQL: dataset=xdr_data)' })).reach)
      .toBe(REACH.DIRECT)
  })

  it('reads a Cortex Cloud / Xpanse / AgentiX console as off-platform', () => {
    for (const src of [
      'Cortex Cloud Posture Dashboard + REST API',
      'Cortex Cloud Inventory (/api/v1/inventory)',
      'ASPM Command Center (/api/v1/asoc/findings)',
      'Xpanse Asset Inventory + XTI exploitation intel',
      'AgentiX governance policy engine + audit log',
    ]) {
      expect(readPath(tc({ detection_source: src })).reach).toBe(REACH.INGEST)
    }
  })

  it('keeps a compound source direct when any part is XSIAM-side', () => {
    // "partly queryable" must not be rounded down — that would suppress work
    // that could in fact land.
    const r = readPath(tc({ detection_source: 'XSIAM UI + XQL · Cortex Cloud Posture Dashboard + REST API' }))
    expect(r.reach).toBe(REACH.DIRECT)
    expect(r.surfaces).toHaveLength(2)
    expect(r.via).toEqual(['XSIAM UI + XQL'])
  })

  it('never infers off-platform from silence', () => {
    expect(readPath(tc({ detection_source: '' })).reach).toBe(REACH.UNKNOWN)
    expect(readPath(tc({ detection_source: 'Some unfamiliar console' })).reach).toBe(REACH.UNKNOWN)
  })
})

describe('proofScope', () => {
  it('blocks an off-platform POS row on a data path — a prerequisite, not a wall', () => {
    // POS-CSPM-001 proves exactly such a row by querying an ingested
    // `cortex_cloud_posture` dataset, so calling it unprovable would be false.
    const s = proofScope(tc({
      validation_class: 'POS',
      detection_source: 'Cortex Cloud Posture Dashboard + REST API',
    }))
    expect(s.blocked).toBe(true)
    expect(s.needsDataPath).toBe(true)
    expect(s.reason).toMatch(/once that source is ingested/)
  })

  it('leaves an off-platform DET row unblocked but manual-only', () => {
    // The engine proves a DET row by generating the signal. An off-platform
    // console costs the auto-reconcile, not the proof.
    const s = proofScope(tc({
      validation_class: 'DET',
      detection_source: 'Cortex Cloud Runtime Alerts + /api/v1/alerts',
    }))
    expect(s.blocked).toBe(false)
    expect(s.autoVerifiable).toBe(false)
    expect(s.reason).toMatch(/validated by hand/)
  })

  it('reports the verdict ceiling separately from the data path', () => {
    // An unscoreable row is measurable and still can never be certified — two
    // independent facts that must not collapse into one.
    expect(proofScope(tc({ validation_class: 'PLT' })))
      .toMatchObject({ blocked: false, autoVerifiable: true, certifiable: true, reason: '' })
    expect(proofScope(tc({ validation_class: 'PLT', is_scoreable: false })))
      .toMatchObject({ blocked: false, certifiable: false })
  })
})

describe('indexAssertions', () => {
  it('indexes every tc_ref an assertion claims', () => {
    const byTc = indexAssertions({
      assertions: [
        { assertion_id: 'PLT-IR-008', tc_ref: 'TC-IR-08', tc_refs: ['TC-IR-08', 'TC-IR-09'] },
        { assertion_id: 'POS-CSPM-001', tc_ref: 'TC-CSPM-01', tc_refs: [] },
      ],
    })
    expect(byTc.get('TC-IR-08')).toHaveLength(1)
    expect(byTc.get('TC-IR-09')).toHaveLength(1)
    expect(byTc.get('TC-CSPM-01')[0].assertion_id).toBe('POS-CSPM-001')
  })

  it('returns an empty index for every failure shape', () => {
    expect(indexAssertions(null).size).toBe(0)
    expect(indexAssertions({}).size).toBe(0)
    expect(indexAssertions({ assertions: null }).size).toBe(0)
  })
})

describe('assertionAvailability', () => {
  it('separates "not deployed" from "deployed, nothing authored"', () => {
    const absent = assertionAvailability({ error: { status: 404, message: 'no such route' } })
    expect(absent.endpoint).toBe('absent')
    expect(absent.detail).toMatch(/not deployed/)

    const empty = assertionAvailability({ payload: { assertions: [], total: 0, rejected: [] } })
    expect(empty.endpoint).toBe('empty')
    expect(empty.detail).toMatch(/no assertion artifacts are authored/)
  })

  it('reports a real transport failure as an error, not as absence', () => {
    const err = assertionAvailability({ error: { status: 500, message: 'boom' } })
    expect(err.endpoint).toBe('error')
    expect(err.detail).toBe('boom')
  })

  it('surfaces the refused artifacts, not just their count', () => {
    const live = assertionAvailability({
      payload: {
        assertions: [{ assertion_id: 'PLT-IR-008', tc_refs: ['TC-IR-08'] }],
        total: 1,
        by_validation_class: { PLT: 1 },
        rejected: [{ source: 'assertions/pos/bad.yml', diagnostics: [{ code: 'A-17', detail: 'can never fail' }] }],
        probes: [{ name: 'xql_rows' }],
      },
    })
    expect(live.endpoint).toBe('live')
    expect(live.authored).toBe(1)
    expect(live.rejected).toBe(1)
    expect(live.rejectedRows[0].diagnostics[0].code).toBe('A-17')
  })
})

describe('authored vs measured', () => {
  const assertions = indexAssertions({
    assertions: [{ assertion_id: 'PLT-IR-008', tc_ref: 'TC-IR-08', tc_refs: ['TC-IR-08'] }],
  })
  const row = tc({ tc_id: 'TC-IR-08', validation_class: 'PLT' })

  it('does not count an authored-but-never-run assertion as measured', () => {
    // The crosswalk reports "4 artifacts AUTHORED | 0 PROVEN (no tenant run
    // recorded)". Presenting authorship as measurement is the exact inflation
    // this surface exists to prevent.
    const [plt] = classCoverage([row], assertions, new Map())
    expect(plt).toMatchObject({ proven: 1, by_assertion: 1, measured: 0 })
  })

  it('does not count a dry run or a pending verdict as measured', () => {
    const runs = indexAssertionRuns({
      runs: [{ assertion_id: 'PLT-IR-008', run_id: 'AR-1', tc_verdict: 'pending', reason: 'dry_run' }],
    })
    expect(classCoverage([row], assertions, runs)[0].measured).toBe(0)
    expect(latestVerdict(runs.get('PLT-IR-008'))).toMatchObject({ verdict: 'pending', reason: 'dry_run' })
  })

  it('counts a recorded pass OR fail as measured — a fail is a real measurement', () => {
    for (const v of ['pass', 'fail']) {
      const runs = indexAssertionRuns({
        runs: [{ assertion_id: 'PLT-IR-008', run_id: 'AR-2', tc_verdict: v, tenant: 'acme' }],
      })
      expect(classCoverage([row], assertions, runs)[0].measured).toBe(1)
    }
  })

  it('reports no verdict at all when the assertion has never been run', () => {
    expect(latestVerdict(undefined)).toBeNull()
    expect(latestVerdict([])).toBeNull()
  })
})

describe('classCoverage', () => {
  const rows = [
    tc({ tc_id: 'TC-EDR-03', validation_class: 'DET', evidence: { evidenced: true, scenario_count: 2 } }),
    tc({ tc_id: 'TC-EDR-06', validation_class: 'DET' }),
    tc({ tc_id: 'TC-EDR-07', validation_class: 'DET', detection_source: 'Cortex Cloud Runtime Alerts + /api/v1/alerts' }),
    tc({ tc_id: 'TC-IR-08', validation_class: 'PLT' }),
    tc({ tc_id: 'TC-CSPM-01', validation_class: 'POS', is_scoreable: false, detection_source: 'Cortex Cloud Posture Dashboard + REST API' }),
    tc({ tc_id: 'TC-CSPM-02', validation_class: 'POS', evidence: { evidenced: true, scenario_count: 1 } }),
  ]
  const assertions = indexAssertions({
    assertions: [{ assertion_id: 'PLT-IR-008', tc_ref: 'TC-IR-08', tc_refs: ['TC-IR-08'] }],
  })

  it('scores each class against its own mechanism', () => {
    const by = Object.fromEntries(classCoverage(rows, assertions).map((r) => [r.validation_class, r]))

    expect(by.DET).toMatchObject({
      total: 3, proven: 1, by_scenario: 1, by_assertion: 0,
      open_direct: 2, open_needs_data_path: 0, manual_only: 1, certifiable: 3,
    })
    // The PLT row is proven by an ASSERTION, not a scenario — the whole point.
    expect(by.PLT).toMatchObject({ total: 1, proven: 1, by_assertion: 1, by_scenario: 0 })
    // One open POS row points off-platform, so it is sized apart from the
    // backlog rather than counted beside it.
    expect(by.POS).toMatchObject({
      total: 2, proven: 1, open_needs_data_path: 1, open_direct: 0, certifiable: 1,
    })
  })

  it('names a POS row bound by an attack scenario as a class mismatch', () => {
    const by = Object.fromEntries(classCoverage(rows, assertions).map((r) => [r.validation_class, r]))
    expect(by.POS.class_mismatch).toBe(1)
    expect(by.DET.class_mismatch).toBe(0)
  })

  it('keeps the segments summing to the total so no row can vanish', () => {
    for (const r of classCoverage(rows, assertions)) {
      expect(r.proven + r.open_direct + r.open_needs_data_path).toBe(r.total)
    }
  })

  it('counts unscoreable rows so the surface can never imply they pass', () => {
    const by = Object.fromEntries(classCoverage(rows, assertions).map((r) => [r.validation_class, r]))
    expect(by.POS.unscoreable).toBe(1)
    expect(by.DET.unscoreable).toBe(0)
  })

  it('drops classes the index does not carry rather than showing 0/0', () => {
    expect(classCoverage(rows, assertions).map((r) => r.validation_class))
      .toEqual(['DET', 'POS', 'PLT'])
  })

  it('treats an absent assertion catalog as zero assertion evidence, never as coverage', () => {
    const by = Object.fromEntries(classCoverage(rows, new Map()).map((r) => [r.validation_class, r]))
    expect(by.PLT).toMatchObject({ proven: 0, by_assertion: 0, open_direct: 1 })
  })
})

describe('coverageTotals', () => {
  it('derives the whole-index numbers from the same rows so they cannot disagree', () => {
    const rows = classCoverage([
      tc({ tc_id: 'a', validation_class: 'DET', evidence: { evidenced: true } }),
      tc({ tc_id: 'b', validation_class: 'POS', detection_source: 'Cortex Cloud Inventory (/api/v1/inventory)' }),
    ], new Map())
    expect(coverageTotals(rows)).toMatchObject({
      total: 2, proven: 1, open_needs_data_path: 1, certifiable: 2, certifiable_pct: 100,
    })
  })
})

describe('tallySurfaces', () => {
  it('counts which off-platform console the rows actually point at', () => {
    expect(tallySurfaces([
      tc({ detection_source: 'Cortex Cloud Posture Dashboard + REST API' }),
      tc({ detection_source: 'Cortex Cloud Posture Dashboard + REST API · Xpanse Asset Inventory + Issues' }),
    ])).toEqual([
      { surface: 'Cortex Cloud Posture Dashboard + REST API', count: 2 },
      { surface: 'Xpanse Asset Inventory + Issues', count: 1 },
    ])
  })
})

describe('MECHANISM', () => {
  it('maps DET/HNT to scenarios and POS/PLT/AUT to assertions', () => {
    expect(MECHANISM.DET.id).toBe('scenario')
    expect(MECHANISM.HNT.id).toBe('scenario')
    expect(MECHANISM.POS.id).toBe('assertion')
    expect(MECHANISM.PLT.id).toBe('assertion')
    expect(MECHANISM.AUT.id).toBe('assertion')
  })
})

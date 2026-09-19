/**
 * The seed catalogs load, and the topology generator derives rather than draws.
 *
 * WHY THIS SUITE EXISTS AT ALL
 * These modules were lifted out of the design prototype, where they were a
 * single file of module-level constants and class methods. Split into real
 * modules, a lifted function can reference a symbol that is no longer in scope
 * — and nothing catches it until something renders. `topology()` shipped with
 * exactly that defect: it referenced RUN_LOG, RUN_SHAPES and LANE_CATALOG with
 * no imports, and every other test passed because no test mounted the tab that
 * calls it. Importing each module and exercising its exports is what makes that
 * class of bug loud.
 *
 * The tallies below are asserted because they are quoted on more than one
 * surface. When the rail badge, the summary strip and the flow-bar hint all
 * derive from one catalog, they cannot disagree; these numbers are what
 * "derive" has to keep producing.
 */
import { describe, it, expect } from 'vitest'
import * as planes from '../planes.js'
import * as corpus from '../corpus.js'
import * as catalogs from '../catalogs.js'
import * as detections from '../detections.js'
import * as tenantApi from '../tenantApi.js'
import * as scope from '../scope.js'
import * as evidence from '../evidence.js'
import * as wizard from '../wizard.js'
import { topology } from '../topology.js'

describe('seed catalogs', () => {
  it('every module loads and its functions run without a missing binding', () => {
    // Calling each exported function is the point — a module can IMPORT
    // cleanly and still reference an out-of-scope symbol inside a function
    // body, which is the exact shape of the topology defect.
    const fns = [
      catalogs.toolCatalog, catalogs.componentCatalog, catalogs.cliCatalog,
      catalogs.tenantCatalog, catalogs.ttpCatalog, catalogs.streamCatalog,
      catalogs.componentTally, detections.detectionLib,
      tenantApi.apiCatalog, tenantApi.validationSheets,
      wizard.detectionTypes, wizard.targetTypes,
    ]
    for (const fn of fns) expect(() => fn()).not.toThrow()

    const state = { wizDets: ['BIOC', 'Correlation'], wizTargets: ['host', 'net'], scopeChoice: {} }
    for (const fn of [scope.constraintRows, scope.resources, scope.requirements]) {
      expect(() => fn(state)).not.toThrow()
    }
  })

  it('holds the corpus the design was drawn against', () => {
    expect(planes.PLANES).toHaveLength(16)
    expect(planes.SOURCES).toHaveLength(6)
    expect(Object.keys(planes.MATRIX)).toHaveLength(16)
    // Every plane has a verdict for every door — a short row would render as a
    // silently missing cell rather than as an error.
    for (const [code] of planes.PLANES) {
      expect(planes.MATRIX[code], `no matrix row for ${code}`).toHaveLength(6)
    }
    expect(corpus.WORKFLOWS).toHaveLength(4)
    expect(corpus.RUN_LOG).toHaveLength(6)
    expect(evidence.COLL_GROUPS.reduce((a, g) => a + g[2], 0)).toBe(206)
    expect(evidence.ANALYTICS_SOURCES.reduce((a, s) => a + s[3], 0)).toBe(184)
  })

  it('tallies components from the rows, never from a literal', () => {
    // The strip once quoted 6/3/2 against a catalog that was really 4/6/2.
    const t = catalogs.componentTally()
    expect(t).toEqual({ total: 12, ready: 4, partial: 6, missing: 2 })
    expect(t.ready + t.partial + t.missing).toBe(t.total)
  })

  it('derives requirements from the wizard choices rather than listing them', () => {
    const all = { wizDets: ['BIOC', 'ABIOC', 'XQL', 'Correlation'], wizTargets: ['host', 'cloud', 'net', 'stream'], scopeChoice: {} }
    const fewer = { ...all, wizTargets: ['host', 'cloud', 'stream'] }
    const a = scope.requirements(all)
    const b = scope.requirements(fewer)
    expect(b.length).toBeLessThan(a.length)
    // Dropping network targets must drop the Broker VM relay rule with them.
    expect(a.some((r) => /relay rule/i.test(r.detail))).toBe(true)
    expect(b.some((r) => /relay rule/i.test(r.detail))).toBe(false)
    expect(b.filter((r) => r.state === 'blocked').length)
      .toBeLessThan(a.filter((r) => r.state === 'blocked').length)
  })

  it('scores a goal OUT OF SCOPE rather than failed when its class is unselected', () => {
    // These are different claims. A goal nobody chose to pursue is not a goal
    // the stack failed, and a readout that conflated them would be wrong in
    // the customer's favour one way and against it the other.
    const fewer = { wizDets: ['BIOC', 'ABIOC', 'XQL', 'Correlation'], wizTargets: ['host', 'cloud', 'stream'], scopeChoice: {} }
    const goals = wizard.scoreGoals(fewer.wizDets, fewer.wizTargets, scope.requirements(fewer))
    const g3 = goals.find((g) => g.id === 'GOAL-03')
    expect(g3.state).toBe('out of scope')
    expect(g3.note).toMatch(/network \/ Broker VM/)
  })
})

describe('topology is generated, not drawn', () => {
  // The three shapes the design verified, plus the three run records that
  // reuse them. Every number here is derived from the run's own step list.
  const CASES = [
    ['4ee09860', 4, 10, 1170, 380, true],
    ['a71b2c04', 3, 8, 1170, 256, false],
    ['c0d93f17', 3, 4, 974, 256, false],
    ['7c22b910', 4, 10, 1170, 380, false],
    ['b1e4470a', 3, 8, 1170, 256, false],
    ['d83f1c66', 3, 4, 974, 256, false],
  ]

  it.each(CASES)('%s derives its own lanes, geometry and pulse', (runOpen, lanes, nodes, w, h, live) => {
    const t = topology({ runOpen })
    // A run that never touches a door gets no band for it.
    expect(t.lanes).toHaveLength(lanes)
    expect(t.nodes).toHaveLength(nodes)
    expect(t.w).toBe(w)
    expect(t.h).toBe(h)
    // Only a RUNNING record animates. The pulse used to be a property of the
    // drawing, so completed runs pulsed as though they were still in flight.
    expect(t.live).toBe(live)
    // No NaN anywhere in the geometry — a single one silently removes a path.
    expect(JSON.stringify(t)).not.toMatch(/NaN/)
  })

  it('orders lanes causally, with the Engine pinned terminal', () => {
    // Lanes sort by the column where flow enters them, so a run reads
    // top-down. Pure causal sort floated the Engine into the middle of the
    // endpoint run, because its first alert fires early — but the Engine is
    // derived signal, not an entry door, and alerts above their own causes
    // read backwards.
    expect(topology({ runOpen: 'c0d93f17' }).lanes.map((l) => l.key)).toEqual(['DC', 'BVM', 'ENG'])
    expect(topology({ runOpen: '4ee09860' }).lanes.map((l) => l.key)).toEqual(['AGT', 'CC', 'BVM', 'ENG'])
    expect(topology({ runOpen: 'a71b2c04' }).lanes.map((l) => l.key)).toEqual(['CC', 'AGT', 'ENG'])
    for (const id of ['4ee09860', 'a71b2c04', 'c0d93f17']) {
      const keys = topology({ runOpen: id }).lanes.map((l) => l.key)
      expect(keys[keys.length - 1], `${id} does not end on the Engine`).toBe('ENG')
    }
  })

  it('falls back to a real shape for an unknown run rather than throwing', () => {
    const t = topology({ runOpen: 'does-not-exist' })
    expect(t.nodes.length).toBeGreaterThan(0)
  })
})

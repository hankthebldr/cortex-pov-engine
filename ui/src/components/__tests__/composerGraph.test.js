import { describe, it, expect } from 'vitest'
import {
  buildGraph,
  stepDepths,
  reparentOnDelete,
  CGO_ID,
  PROOF_ID,
  GRAPH_LAYOUT,
} from '../console/composerGraph.js'

// Minimal step factory — only the fields buildGraph/stepDepths read.
const step = (id, extra = {}) => ({
  id,
  name: `step ${id}`,
  command: `echo ${id}`,
  identity: null,
  technique: null,
  platforms: [],
  channel: null,
  target: null,
  eal: null,
  causalityParent: null,
  causalityPivot: 'process_lineage',
  detections: [{ plane: 'EDR', type: 'BIOC', description: 'x' }],
  authored: false,
  ...extra,
})

const draft = (steps, extra = {}) => ({
  originId: 'SIM-EDR-001', name: 'Credential dumping', plane: 'EDR',
  cgo: 'apache2 / www-data', teardown: ['pkill x'], layout: null,
  steps, ...extra,
})

describe('stepDepths', () => {
  it('roots are depth 1, children deepen by longest path', () => {
    const steps = [
      step('step-01'),
      step('step-02', { causalityParent: 'step-01' }),
      step('step-03', { causalityParent: 'step-02' }),
    ]
    const { depthById, maxDepth } = stepDepths(steps)
    expect(depthById.get('step-01')).toBe(1)
    expect(depthById.get('step-02')).toBe(2)
    expect(depthById.get('step-03')).toBe(3)
    expect(maxDepth).toBe(3)
  })

  it('a dangling parent ref reads as a root (depth 1), never throws', () => {
    const steps = [step('step-02', { causalityParent: 'ghost' })]
    const { depthById, maxDepth } = stepDepths(steps)
    expect(depthById.get('step-02')).toBe(1)
    expect(maxDepth).toBe(1)
  })

  it('a hand-fed cycle is tolerated (treated as a root), not a stack overflow', () => {
    const steps = [
      step('a', { causalityParent: 'b' }),
      step('b', { causalityParent: 'a' }),
    ]
    expect(() => stepDepths(steps)).not.toThrow()
  })

  it('empty steps → maxDepth 0', () => {
    expect(stepDepths([]).maxDepth).toBe(0)
  })
})

describe('buildGraph node set', () => {
  it('emits exactly one cgo, one proof, and one step node per step', () => {
    const steps = [step('step-01'), step('step-02', { causalityParent: 'step-01' })]
    const { nodes } = buildGraph(draft(steps), steps)
    const kinds = nodes.map((n) => n.type)
    expect(kinds.filter((k) => k === 'cgo')).toHaveLength(1)
    expect(kinds.filter((k) => k === 'proof')).toHaveLength(1)
    expect(kinds.filter((k) => k === 'step')).toHaveLength(2)
    expect(nodes.find((n) => n.id === CGO_ID).type).toBe('cgo')
    expect(nodes.find((n) => n.id === PROOF_ID).type).toBe('proof')
  })

  it('the cgo and proof nodes are not deletable, selectable or draggable', () => {
    const { nodes } = buildGraph(draft([step('step-01')]), [step('step-01')])
    for (const id of [CGO_ID, PROOF_ID]) {
      const n = nodes.find((x) => x.id === id)
      expect(n.deletable).toBe(false)
      expect(n.selectable).toBe(false)
      expect(n.draggable).toBe(false)
    }
  })

  it('cgo node carries the draft CGO anchor; proof carries the teardown', () => {
    const { nodes } = buildGraph(draft([step('step-01')]), [step('step-01')])
    expect(nodes.find((n) => n.id === CGO_ID).data.cgo).toBe('apache2 / www-data')
    expect(nodes.find((n) => n.id === PROOF_ID).data.teardown).toEqual(['pkill x'])
  })

  it('lays steps out left-to-right by depth', () => {
    const steps = [
      step('step-01'),
      step('step-02', { causalityParent: 'step-01' }),
    ]
    const { nodes } = buildGraph(draft(steps), steps)
    const s1 = nodes.find((n) => n.id === 'step-01').position
    const s2 = nodes.find((n) => n.id === 'step-02').position
    const proof = nodes.find((n) => n.id === PROOF_ID).position
    const cgo = nodes.find((n) => n.id === CGO_ID).position
    expect(cgo.x).toBeLessThan(s1.x)
    expect(s1.x).toBeLessThan(s2.x)
    expect(s2.x).toBeLessThan(proof.x)
  })

  it('a stored layout position overrides the computed slot for that step only', () => {
    const steps = [step('step-01'), step('step-02', { causalityParent: 'step-01' })]
    const g = buildGraph(draft(steps, { layout: { 'step-01': { x: 999, y: 123 } } }), steps)
    expect(g.nodes.find((n) => n.id === 'step-01').position).toEqual({ x: 999, y: 123 })
    // step-02 keeps its computed column position (depth 2).
    expect(g.nodes.find((n) => n.id === 'step-02').position.x)
      .toBe(GRAPH_LAYOUT.padX + 2 * GRAPH_LAYOUT.colW)
  })

  it('run-lens makes step nodes non-deletable and carries the real run state', () => {
    const steps = [step('step-01')]
    const g = buildGraph(draft(steps), steps, {
      runLens: true,
      causalityStates: { 'step-01': { state: 'BROKEN' } },
    })
    const s = g.nodes.find((n) => n.id === 'step-01')
    expect(s.deletable).toBe(false)
    expect(s.data.runState).toBe('BROKEN')
  })

  it('run-lens defaults a missing state to EXPECTED, never a fabricated CONFIRMED', () => {
    const steps = [step('step-01')]
    const g = buildGraph(draft(steps), steps, { runLens: true, causalityStates: {} })
    expect(g.nodes.find((n) => n.id === 'step-01').data.runState).toBe('EXPECTED')
  })

  it('selectedId flags exactly that step node', () => {
    const steps = [step('step-01'), step('step-02', { causalityParent: 'step-01' })]
    const g = buildGraph(draft(steps), steps, { selectedId: 'step-02' })
    expect(g.nodes.find((n) => n.id === 'step-01').data.selected).toBe(false)
    expect(g.nodes.find((n) => n.id === 'step-02').data.selected).toBe(true)
  })
})

describe('buildGraph edge set', () => {
  it('a parentless step gets a derived, non-deletable cgo→step edge', () => {
    const steps = [step('step-01')]
    const { edges } = buildGraph(draft(steps), steps)
    const root = edges.find((e) => e.source === CGO_ID && e.target === 'step-01')
    expect(root).toBeTruthy()
    expect(root.type).toBe('derived')
    expect(root.deletable).toBe(false)
    expect(root.data.kind).toBe('root')
  })

  it('a leaf step gets a derived, non-deletable step→proof edge', () => {
    const steps = [step('step-01')]
    const { edges } = buildGraph(draft(steps), steps)
    const term = edges.find((e) => e.source === 'step-01' && e.target === PROOF_ID)
    expect(term).toBeTruthy()
    expect(term.type).toBe('derived')
    expect(term.deletable).toBe(false)
    expect(term.data.kind).toBe('terminal')
  })

  it('an intermediate step is NOT linked to proof (only leaves are)', () => {
    const steps = [step('step-01'), step('step-02', { causalityParent: 'step-01' })]
    const { edges } = buildGraph(draft(steps), steps)
    expect(edges.find((e) => e.source === 'step-01' && e.target === PROOF_ID)).toBeFalsy()
    expect(edges.find((e) => e.source === 'step-02' && e.target === PROOF_ID)).toBeTruthy()
  })

  it('a process_lineage causality edge has id `parent->child`, no label, no dash', () => {
    const steps = [step('step-01'), step('step-02', { causalityParent: 'step-01' })]
    const { edges } = buildGraph(draft(steps), steps)
    const e = edges.find((x) => x.id === 'step-01->step-02')
    expect(e).toBeTruthy()
    expect(e.type).toBe('causality')
    expect(e.label).toBeUndefined()
    expect(e.style.strokeDasharray).toBeUndefined()
    expect(e.data.pivot).toBe('process_lineage')
    expect(e.deletable).toBe(true)
  })

  it('a non-process pivot edge carries its pivot as label + a dashed stroke', () => {
    const steps = [
      step('step-01'),
      step('step-02', { causalityParent: 'step-01', causalityPivot: 'network_session' }),
    ]
    const { edges } = buildGraph(draft(steps), steps)
    const e = edges.find((x) => x.id === 'step-01->step-02')
    expect(e.label).toBe('network_session')
    expect(e.style.strokeDasharray).toBeTruthy()
    expect(e.data.pivot).toBe('network_session')
  })

  it('run-lens causality edges are not deletable or reconnectable', () => {
    const steps = [step('step-01'), step('step-02', { causalityParent: 'step-01' })]
    const { edges } = buildGraph(draft(steps), steps, { runLens: true })
    const e = edges.find((x) => x.id === 'step-01->step-02')
    expect(e.deletable).toBe(false)
    expect(e.reconnectable).toBe(false)
  })
})

describe('reparentOnDelete', () => {
  it('reparents a deleted middle step’s children onto its parent, keeping pivots', () => {
    const steps = [
      step('step-01'),
      step('step-02', { causalityParent: 'step-01' }),
      step('step-03', { causalityParent: 'step-02', causalityPivot: 'network_session' }),
    ]
    const next = reparentOnDelete(steps, 'step-02')
    expect(next.map((s) => s.id)).toEqual(['step-01', 'step-03'])
    const s3 = next.find((s) => s.id === 'step-03')
    expect(s3.causalityParent).toBe('step-01')
    expect(s3.causalityPivot).toBe('network_session') // pivot preserved
  })

  it('deleting a root makes its children roots (parent → null)', () => {
    const steps = [
      step('step-01'),
      step('step-02', { causalityParent: 'step-01' }),
    ]
    const next = reparentOnDelete(steps, 'step-01')
    expect(next).toHaveLength(1)
    expect(next[0].id).toBe('step-02')
    expect(next[0].causalityParent).toBeNull()
  })

  it('leaves no step pointing at the deleted id (no dangling ref)', () => {
    const steps = [
      step('step-01'),
      step('step-02', { causalityParent: 'step-01' }),
      step('step-03', { causalityParent: 'step-01' }),
    ]
    const next = reparentOnDelete(steps, 'step-01')
    expect(next.some((s) => s.causalityParent === 'step-01')).toBe(false)
  })

  it('returns the same array reference for an unknown id (no-op)', () => {
    const steps = [step('step-01')]
    expect(reparentOnDelete(steps, 'ghost')).toBe(steps)
  })
})

describe('buildGraph — off-chain flag', () => {
  it('flags a second root on its card and on its trigger edge', () => {
    const steps = [
      step('step-01'),
      step('step-02', { causalityParent: 'step-01' }),
      step('step-03'), // dropped on the canvas, not yet connected
    ]
    const { nodes, edges } = buildGraph(draft(steps), steps)
    expect(nodes.find((n) => n.id === 'step-03').data.offChain).toBe(true)
    expect(nodes.find((n) => n.id === 'step-01').data.offChain).toBe(false)
    expect(edges.find((e) => e.id === `${CGO_ID}->step-03`).data.offChain).toBe(true)
    expect(edges.find((e) => e.id === `${CGO_ID}->step-01`).data.offChain).toBe(false)
  })

  it('does not flag the roots of a legacy star (no declared causality)', () => {
    const steps = [step('step-01'), step('step-02')]
    const { nodes } = buildGraph(draft(steps), steps)
    expect(nodes.filter((n) => n.type === 'step').every((n) => !n.data.offChain)).toBe(true)
  })
})

import { describe, it, expect } from 'vitest'
import {
  buildGraph,
  stepDepths,
  reparentOnDelete,
  CGO_ID,
  PROOF_ID,
  GRAPH_LAYOUT,
} from '../console/composerGraph.js'
import { deriveTenantContent } from '../console/composerContent.js'
import {
  draftToApi, draftSnapshot, emitDraftYaml, isDraftDirty, spineProblems, validateDraft,
} from '../console/composerDraft.js'

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

// ─── Tenant content: correlation rules as derived nodes ──────────────────────
// A rule is something the TENANT must hold, composed from several steps'
// detections. It is drawn beside the chain and is never part of it.

describe('buildGraph — tenant-content rule nodes', () => {
  const det = (type, ttpRef, detectionId) => ({ plane: 'EDR', type, description: 'x', ttpRef, detectionId })
  const chain = () => [
    step('step-01', { detections: [det('XQL', 'TTP-T-0001', 'xql-q-one')] }),
    step('step-02', {
      causalityParent: 'step-01',
      detections: [det('BIOC', 'TTP-T-0001', 'bioc-b-one'), det('XQL', 'TTP-T-0001', 'xql-q-two')],
    }),
    step('step-03', {
      causalityParent: 'step-02',
      detections: [det('Correlation', 'TTP-T-0001', 'CR-T-0001')],
    }),
  ]
  const CARDS = {
    'TTP-T-0001': {
      id: 'TTP-T-0001',
      detections: {
        xql_queries: [{ name: 'Q One' }, { name: 'Q Two' }],
        biocs: [{ name: 'B One' }],
        correlation_rules: [
          { name: 'Rule Declared', rule_id: 'CR-T-0001', logic: 'XQL(Q One) AND BIOC(B One)' },
          { name: 'Rule Card Only', rule_id: 'CR-T-0002', logic: 'XQL(Q Two) OR BIOC(B One)' },
          { name: 'Rule Prose', rule_id: 'CR-T-0003', logic: 'Fires when both share a host' },
        ],
      },
    },
  }
  const build = (steps, opts = {}) => buildGraph(
    draft(steps), steps, { content: deriveTenantContent(steps, CARDS), ...opts },
  )
  const rules = (g) => g.nodes.filter((n) => n.type === 'rule')
  const contentEdges = (g) => g.edges.filter((e) => e.type === 'content')

  it('emits no rule node, no content edge and no rail when no content is given', () => {
    const steps = chain()
    const g = buildGraph(draft(steps), steps)
    expect(rules(g)).toEqual([])
    expect(contentEdges(g)).toEqual([])
    expect(g.rail).toBeNull()
  })

  it('emits one rule node per rule, keyed so it cannot collide with a step', () => {
    const g = build(chain())
    expect(rules(g).map((n) => n.id)).toEqual([
      'rule:TTP-T-0001:CR-T-0001', 'rule:TTP-T-0001:CR-T-0002', 'rule:TTP-T-0001:CR-T-0003',
    ])
  })

  it('carries both statuses on the node, as data the card prints in words', () => {
    const [declared, cardOnly] = rules(build(chain()))
    expect(declared.data.rule.contentStatus).toBe('intent-only')
    expect(declared.data.rule.binding).toBe('declared')
    expect(cardOnly.data.rule.binding).toBe('card-only')
  })

  it('makes a rule node selectable but never draggable or deletable', () => {
    for (const n of rules(build(chain()))) {
      expect(n.selectable).toBe(true)
      expect(n.draggable).toBe(false)
      expect(n.deletable).toBe(false)
    }
  })

  it('draws a feeds edge from every step whose detection the rule names', () => {
    const g = build(chain())
    const into = contentEdges(g).filter((e) => e.target === 'rule:TTP-T-0001:CR-T-0001')
    expect(into.map((e) => [e.source, e.data.kind, e.data.inputs, e.data.declares])).toEqual([
      ['step-01', 'feeds', 1, false],
      ['step-02', 'feeds', 1, false],
      ['step-03', 'feeds', 0, true],
    ])
  })

  it('labels a feeds edge with its input count, and a declaring step as expecting the rule', () => {
    const g = build(chain())
    const label = (source, target) => contentEdges(g)
      .find((e) => e.source === source && e.target === target).data.label
    expect(label('step-01', 'rule:TTP-T-0001:CR-T-0001')).toBe('feeds · 1 input')
    expect(label('step-02', 'rule:TTP-T-0001:CR-T-0002')).toBe('feeds · 2 inputs')
    expect(label('step-03', 'rule:TTP-T-0001:CR-T-0001')).toBe('expects this rule')
  })

  it('falls back to one "same card" edge when no input resolves', () => {
    const g = build(chain())
    const into = contentEdges(g).filter((e) => e.target === 'rule:TTP-T-0001:CR-T-0003')
    expect(into).toHaveLength(1)
    expect(into[0].source).toBe('step-03')
    expect(into[0].data).toMatchObject({ kind: 'card', label: 'same card' })
  })

  it('makes content edges inert: not selectable, deletable or reconnectable', () => {
    for (const e of contentEdges(build(chain()))) {
      expect(e.selectable).toBe(false)
      expect(e.deletable).toBe(false)
      expect(e.reconnectable).toBe(false)
      expect(e.sourceHandle).toBe('content')
    }
  })

  it('places every rule on a rail below the lowest step', () => {
    const g = build(chain())
    const lowest = Math.max(...g.nodes.filter((n) => n.type === 'step').map((n) => n.position.y))
    expect(g.rail.y).toBeGreaterThanOrEqual(lowest + GRAPH_LAYOUT.rowH)
    for (const n of rules(g)) expect(n.position.y).toBeGreaterThan(g.rail.y)
  })

  it('puts each rule under its right-most feeding step', () => {
    const g = build(chain())
    const x = (id) => g.nodes.find((n) => n.id === id).position.x
    expect(x('rule:TTP-T-0001:CR-T-0001')).toBe(x('step-03'))
    expect(x('rule:TTP-T-0001:CR-T-0002')).toBe(x('step-02'))
  })

  it('never overlaps two rule cards that share an anchor', () => {
    const g = build(chain())
    const [a, , c] = rules(g) // CR-T-0001 and CR-T-0003 both anchor on step-03
    expect(a.position.x).toBe(c.position.x)
    expect(Math.abs(a.position.y - c.position.y)).toBeGreaterThanOrEqual(GRAPH_LAYOUT.ruleRowH)
  })

  it('moves the rail down when a step has been dragged below the chain', () => {
    const steps = chain()
    const plain = build(steps)
    const dragged = build(steps, { layout: { 'step-02': { x: 400, y: 900 } } })
    expect(dragged.rail.y).toBeGreaterThan(plain.rail.y)
    expect(dragged.rail.y).toBeGreaterThanOrEqual(900 + GRAPH_LAYOUT.rowH)
  })

  it('clears the real height of a tall step card, not a nominal row', () => {
    // A card's height is only known once the browser has measured it: a long
    // name and a second chip row make it taller than the row pitch, and a rail
    // placed by the pitch alone was drawn THROUGH the card (seen in Chrome).
    const steps = chain()
    const y = build(steps).nodes.find((n) => n.id === 'step-02').position.y
    const tall = build(steps, { stepHeights: { 'step-02': 260 } })
    expect(tall.rail.y).toBeGreaterThanOrEqual(y + 260)
    for (const n of rules(tall)) expect(n.position.y).toBeGreaterThan(tall.rail.y)
  })

  it('assumes a full-height card until one is measured', () => {
    const g = build(chain())
    const lowest = Math.max(...g.nodes.filter((n) => n.type === 'step').map((n) => n.position.y))
    expect(g.rail.y).toBeGreaterThanOrEqual(lowest + GRAPH_LAYOUT.cardH)
  })

  it('grows the bounds to hold the rail', () => {
    const steps = chain()
    const without = buildGraph(draft(steps), steps)
    expect(build(steps).bounds.height).toBeGreaterThan(without.bounds.height)
  })

  it('marks the selected rule', () => {
    const g = build(chain(), { selectedRuleKey: 'rule:TTP-T-0001:CR-T-0002' })
    expect(rules(g).map((n) => n.data.selected)).toEqual([false, true, false])
  })

  it('draws nothing in the Run lens: a rule is never lent a run verdict', () => {
    const g = build(chain(), { runLens: true })
    expect(rules(g)).toEqual([])
    expect(contentEdges(g)).toEqual([])
    expect(g.rail).toBeNull()
  })

  it('leaves the chain itself exactly as it was', () => {
    const steps = chain()
    const before = buildGraph(draft(steps), steps)
    const after = build(steps)
    const spine = (g) => g.edges.filter((e) => e.type !== 'content')
    expect(spine(after)).toEqual(spine(before))
    const pos = (g) => g.nodes.filter((n) => n.type !== 'rule').map((n) => [n.id, n.position])
    expect(pos(after)).toEqual(pos(before))
  })

  // The rule is DERIVED. These pin that it can never reach what is saved, what
  // is launched, or what the loader validates.
  describe('never persisted', () => {
    const freeze = (o) => {
      if (o && typeof o === 'object' && !Object.isFrozen(o)) {
        Object.freeze(o)
        Object.values(o).forEach(freeze)
      }
      return o
    }

    it('builds from a frozen draft: it writes to nothing it was given', () => {
      const steps = freeze(chain())
      const d = freeze(draft(steps))
      const content = freeze(deriveTenantContent(steps, CARDS))
      expect(() => buildGraph(d, steps, { content })).not.toThrow()
    })

    it('is absent from the save body, the launch snapshot and the YAML', () => {
      const steps = chain()
      const d = draft(steps)
      build(steps)
      for (const text of [
        JSON.stringify(draftToApi(d)), JSON.stringify(draftSnapshot(d)), emitDraftYaml(d),
      ]) {
        expect(text).not.toMatch(/rule:TTP|CR-T-0002|Rule Card Only|"content"/)
      }
      expect(draftToApi(d).steps).toHaveLength(3)
    })

    it('cannot make a draft read as edited', () => {
      const steps = chain()
      const d = draft(steps)
      const saved = draftSnapshot(d)
      build(steps)
      expect(isDraftDirty(draftSnapshot(d), saved)).toBe(false)
    })

    it('is invisible to the spine check the loader mirrors', () => {
      const steps = chain()
      const before = spineProblems(steps)
      build(steps)
      expect(spineProblems(steps)).toEqual(before)
      expect(validateDraft(steps).ok).toBe(validateDraft(chain()).ok)
    })
  })
})

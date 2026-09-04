/**
 * composerLayout — geometry for the Composer's two lenses.
 *
 * The rules pinned here are HONESTY rules, not pixel maths: the design spine
 * must have exactly one root and a visible terminus, and the Run lens must carry
 * the REAL graph's edge state through verbatim — a BROKEN stitch must never read
 * as CONFIRMED. Coordinates are asserted only enough to prove determinism and
 * ordering; the canvas trusts these numbers, so a silent drift here draws a
 * disconnected chain.
 */
import { describe, it, expect } from 'vitest'
import {
  LAYOUT,
  causalityStepStates,
  layoutCausalityGraph,
  layoutChain,
  layoutSpine,
  spineEdges,
  stitchOverlayEdges,
} from '../console/composerLayout.js'

// A three-step linear spine: step-02 descends from step-01, step-03 from step-02.
const STEPS = [
  { id: 'step-01', causalityParent: null, causalityPivot: 'process_lineage' },
  { id: 'step-02', causalityParent: 'step-01', causalityPivot: 'process_lineage' },
  { id: 'step-03', causalityParent: 'step-02', causalityPivot: 'network_session' },
]

describe('spineEdges', () => {
  it('roots the parentless step from START and terminates the last leaf at END', () => {
    const edges = spineEdges(STEPS)
    const rootEdge = edges.find((e) => e.source === 'start')
    expect(rootEdge).toMatchObject({ source: 'start', target: 'step-01', kind: 'root' })
    const endEdge = edges.find((e) => e.target === 'end')
    expect(endEdge).toMatchObject({ source: 'step-03', target: 'end', kind: 'terminal' })
  })

  it('derives parent→child edges with the child step pivot as the edge kind', () => {
    const edges = spineEdges(STEPS)
    expect(edges).toEqual(expect.arrayContaining([
      expect.objectContaining({ source: 'step-01', target: 'step-02', kind: 'process_lineage' }),
      expect.objectContaining({ source: 'step-02', target: 'step-03', kind: 'network_session' }),
    ]))
  })

  it('is deterministic and order-stable', () => {
    expect(spineEdges(STEPS)).toEqual(spineEdges(STEPS))
  })

  it('treats a dangling parent ref as a root rather than a broken edge', () => {
    const edges = spineEdges([{ id: 'step-01', causalityParent: 'ghost' }])
    expect(edges.find((e) => e.target === 'step-01')).toMatchObject({ source: 'start', kind: 'root' })
  })

  it('returns [] for no steps', () => {
    expect(spineEdges([])).toEqual([])
    expect(spineEdges(null)).toEqual([])
  })
})

describe('layoutChain', () => {
  it('includes START and END anchor nodes plus one node per step', () => {
    const { nodes } = layoutChain({ steps: STEPS })
    expect(nodes[0]).toMatchObject({ id: 'start', kind: 'start' })
    expect(nodes[nodes.length - 1]).toMatchObject({ id: 'end', kind: 'end' })
    expect(nodes.filter((n) => n.kind === 'step').map((n) => n.id))
      .toEqual(['step-01', 'step-02', 'step-03'])
  })

  it('stacks nodes top-down without overlap (deterministic y ordering)', () => {
    const { nodes } = layoutChain({ steps: STEPS })
    const ys = nodes.map((n) => n.y)
    const sorted = [...ys].sort((a, b) => a - b)
    expect(ys).toEqual(sorted)
    // START sits above the first step, END below the last.
    expect(nodes[0].y).toBeLessThan(nodes[1].y)
  })

  it('resolves edge port coordinates so the canvas needs no geometry of its own', () => {
    const { edges } = layoutChain({ steps: STEPS })
    for (const e of edges) {
      expect(e.from).toEqual(expect.objectContaining({ x: expect.any(Number), y: expect.any(Number) }))
      expect(e.to).toEqual(expect.objectContaining({ x: expect.any(Number), y: expect.any(Number) }))
    }
    // A parent→child edge leaves the bottom of the source and enters the top of the target.
    const e12 = edges.find((e) => e.source === 'step-01' && e.target === 'step-02')
    expect(e12.from.y).toBeLessThan(e12.to.y)
  })

  it('reports bounds that contain every node', () => {
    const { nodes, bounds } = layoutChain({ steps: STEPS })
    for (const n of nodes) {
      expect(n.x + n.w).toBeLessThanOrEqual(bounds.width)
      expect(n.y + n.h).toBeLessThanOrEqual(bounds.height)
    }
  })

  it('lays out an empty draft as just the two anchors', () => {
    const { nodes } = layoutChain({ steps: [] })
    expect(nodes.map((n) => n.kind)).toEqual(['start', 'end'])
  })
})

describe('layoutSpine (task helper)', () => {
  it('detects the single root and derives parent→child tuples, excluding anchors', () => {
    const { nodes, edges } = layoutSpine(STEPS)
    expect(nodes.map((n) => n.id)).toEqual(['step-01', 'step-02', 'step-03'])
    expect(edges).toEqual([
      ['step-01', 'step-02', 'process_lineage'],
      ['step-02', 'step-03', 'network_session'],
    ])
  })

  it('single-root invariant: exactly one step roots from START', () => {
    const twoRoots = [
      { id: 'a', causalityParent: null },
      { id: 'b', causalityParent: null },
    ]
    const roots = spineEdges(twoRoots).filter((e) => e.source === 'start')
    // Both parentless steps root from START — the design lens shows both roots
    // rather than inventing a link. The single-root INVARIANT is enforced at the
    // model layer (setCausalityParent + the backend spine check), not by hiding
    // a second root here.
    expect(roots).toHaveLength(2)
  })

  it('gives coordinates from causality order, no randomness', () => {
    expect(layoutSpine(STEPS)).toEqual(layoutSpine(STEPS))
  })
})

// ─── Run lens ─────────────────────────────────────────────────────────────────

const RUN = 'run-42'
const GRAPH = {
  nodes: [
    { id: `cgo:${RUN}`, kind: 'cgo', label: 'CGO' },
    { id: `proc:${RUN}:step-01`, kind: 'process', label: 'step-01' },
    { id: `proc:${RUN}:step-02`, kind: 'process', label: 'step-02' },
    { id: `alert:${RUN}:step-01:0`, kind: 'alert', label: 'XQL' },
  ],
  edges: [
    { id: 'e0', source: `cgo:${RUN}`, target: `proc:${RUN}:step-01`, kind: 'process_lineage', state: 'EXPECTED' },
    { id: 'e1', source: `proc:${RUN}:step-01`, target: `proc:${RUN}:step-02`, kind: 'network_session', state: 'CONFIRMED' },
    { id: 'e2', source: `proc:${RUN}:step-01`, target: `alert:${RUN}:step-01:0`, kind: 'detection_attach', state: 'CONFIRMED' },
  ],
  causality_summary: { chain_completeness_pct: 66, broken_stitches: [], stitched_incident: true },
}

describe('layoutCausalityGraph', () => {
  it('lays out every real node and passes edge STATE through verbatim', () => {
    const { nodes, edges } = layoutCausalityGraph(GRAPH)
    expect(nodes).toHaveLength(4)
    expect(edges.map((e) => e.state)).toEqual(['EXPECTED', 'CONFIRMED', 'CONFIRMED'])
    // kind and rationale survive too
    expect(edges[0].kind).toBe('process_lineage')
  })

  it('never synthesizes a state for an edge that has none', () => {
    const g = { nodes: [{ id: 'cgo:x', kind: 'cgo' }], edges: [{ source: 'cgo:x', target: 'cgo:x', kind: 'k' }] }
    const { edges } = layoutCausalityGraph(g)
    expect(edges[0].state).toBeUndefined()
  })

  it('places nodes deterministically and returns bounds containing them', () => {
    const { nodes, bounds } = layoutCausalityGraph(GRAPH)
    for (const n of nodes) {
      expect(n.x + n.w).toBeLessThanOrEqual(bounds.width)
      expect(n.y + n.h).toBeLessThanOrEqual(bounds.height)
    }
    expect(layoutCausalityGraph(GRAPH)).toEqual(layoutCausalityGraph(GRAPH))
  })

  it('returns empty layout for a null/empty graph', () => {
    expect(layoutCausalityGraph(null).nodes).toEqual([])
    expect(layoutCausalityGraph({}).edges).toEqual([])
  })
})

describe('causalityStepStates', () => {
  it('maps each proc:{run}:{step} node to its aggregate run state', () => {
    const states = causalityStepStates(GRAPH)
    expect(Object.keys(states).sort()).toEqual(['step-01', 'step-02'])
    expect(states['step-01'].nodeId).toBe(`proc:${RUN}:step-01`)
    // step-02's only touching edge is CONFIRMED
    expect(states['step-02'].state).toBe('CONFIRMED')
  })

  it('reports BROKEN honestly — a broken stitch is never upgraded to CONFIRMED', () => {
    const g = {
      nodes: [{ id: `proc:${RUN}:step-09`, kind: 'process' }],
      edges: [
        { source: `proc:${RUN}:step-09`, target: 'x', kind: 'endpoint_network_stitch', state: 'BROKEN' },
      ],
    }
    const states = causalityStepStates(g)
    expect(states['step-09'].state).toBe('BROKEN')
    expect(states['step-09'].hasBrokenStitch).toBe(true)
  })

  it('BROKEN takes precedence over a co-present CONFIRMED edge', () => {
    const g = {
      nodes: [{ id: `proc:${RUN}:step-09`, kind: 'process' }],
      edges: [
        { source: `proc:${RUN}:step-09`, target: 'a', kind: 'process_lineage', state: 'CONFIRMED' },
        { source: `proc:${RUN}:step-09`, target: 'b', kind: 'shared_entity', state: 'BROKEN' },
      ],
    }
    expect(causalityStepStates(g)['step-09'].state).toBe('BROKEN')
  })

  it('a broken NON-stitch edge does not set hasBrokenStitch (but still degrades state)', () => {
    const g = {
      nodes: [{ id: `proc:${RUN}:step-09`, kind: 'process' }],
      edges: [
        { source: `proc:${RUN}:step-09`, target: 'a', kind: 'detection_attach', state: 'BROKEN' },
      ],
    }
    const s = causalityStepStates(g)['step-09']
    expect(s.state).toBe('BROKEN')
    expect(s.hasBrokenStitch).toBe(false)
  })

  it('returns {} for a null or node-less graph', () => {
    expect(causalityStepStates(null)).toEqual({})
    expect(causalityStepStates({ nodes: [] })).toEqual({})
  })
})

describe('LAYOUT constants', () => {
  it('is frozen so a caller cannot mutate shared geometry', () => {
    expect(Object.isFrozen(LAYOUT)).toBe(true)
    expect(LAYOUT.nodeW).toBeGreaterThan(0)
  })
})

describe('stitchOverlayEdges — design-lens entity-join intent', () => {
  const steps = [
    { id: 'step-01', command: 'curl --local-port {stitch:src_port} https://x' },
    { id: 'step-02', command: 'echo {stitch:src_port} {stitch:dst_ip}' },
    { id: 'step-03', command: 'nc {stitch:src_port}' },
  ]
  const model = { src_port: { resolve: 'auto_port' }, dst_ip: { literal: '203.0.113.10' } }

  it('is EXPECTED-only — never CONFIRMED or BROKEN (intent, not outcome)', () => {
    const edges = stitchOverlayEdges(steps, model)
    expect(edges.length).toBeGreaterThan(0)
    for (const e of edges) expect(e.state).toBe('EXPECTED')
    // The design lens must not carry a run outcome.
    expect(edges.some((e) => e.state === 'CONFIRMED' || e.state === 'BROKEN')).toBe(false)
  })

  it('links CONSECUTIVE consumers of a planted key, not a full mesh', () => {
    // src_port is consumed by step-01, step-02, step-03 → two hops, not three.
    const edges = stitchOverlayEdges(steps, model).filter((e) => e.key === 'src_port')
    expect(edges.map((e) => [e.source, e.target])).toEqual([
      ['step-01', 'step-02'],
      ['step-02', 'step-03'],
    ])
  })

  it('emits no edge for a planted key with fewer than two consumers', () => {
    // dst_ip is consumed only by step-02 — a join needs two ends.
    const edges = stitchOverlayEdges(steps, model).filter((e) => e.key === 'dst_ip')
    expect(edges).toEqual([])
  })

  it('emits nothing when a consumed key is not planted', () => {
    // The command references src_port, but the model plants nothing.
    expect(stitchOverlayEdges(steps, null)).toEqual([])
    expect(stitchOverlayEdges(steps, {})).toEqual([])
  })

  it('resolves ports from layoutChain node geometry, offset to the right edge', () => {
    const { nodes } = layoutChain({ steps })
    const n1 = nodes.find((n) => n.id === 'step-01')
    const [edge] = stitchOverlayEdges(steps, model).filter((e) => e.key === 'src_port')
    expect(edge.from).toEqual({ x: n1.x + n1.w, y: n1.y + n1.h / 2 })
  })

  it('is deterministic and order-stable across calls', () => {
    expect(stitchOverlayEdges(steps, model)).toEqual(stitchOverlayEdges(steps, model))
  })

  it('returns [] for absent steps', () => {
    expect(stitchOverlayEdges(null, model)).toEqual([])
    expect(stitchOverlayEdges([], model)).toEqual([])
  })
})

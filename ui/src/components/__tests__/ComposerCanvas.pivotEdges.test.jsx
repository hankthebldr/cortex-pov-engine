/**
 * ComposerCanvas — non-process_lineage pivots render a typed edge, not a
 * flattened process chain (Important 3, 2026-09 final-fix wave).
 *
 * Spec §5.5: "A `process_lineage` pivot chains parent→child process nodes;
 * any non-process pivot emits its own typed edge and leaves the step rooted
 * at the CGO... the canvas must render that difference, not flatten it into
 * a process chain." `composerLayout.js`'s `spineEdges` already carries the
 * authored pivot as edge `kind` (defaulting `process_lineage`) — Task 8's own
 * plan snippet labelled a non-lineage edge (`e.pivot !== 'process_lineage' ?
 * e.pivot : undefined`), but that line was lost when the field was corrected
 * from the never-real `e.pivot` to the actual `e.kind`, leaving `rfEdges`
 * (ComposerCanvas.jsx) style every edge identically regardless of pivot.
 *
 * WHY THIS IS ITS OWN FILE, NOT APPENDED TO ComposerCanvas.test.jsx
 * -------------------------------------------------------------------
 * React Flow never renders an actual `.react-flow__edge` between two nodes
 * jsdom cannot measure — its internal `NodeWrapper` needs a working
 * `ResizeObserver` (this codebase's stub is a deliberate no-op, see
 * ComposerCanvas.fitViewGuard.test.jsx's header) AND `window.DOMMatrixReadOnly`
 * (absent from jsdom) to resolve handle bounds; supplying both to force real
 * edge DOM is a rabbit hole with no payoff, since the actual bug here is in
 * OUR OWN prop construction (`rfEdges`), not in React Flow's rendering. Same
 * fix as the fitView guard: a partial `@xyflow/react` mock that RECORDS the
 * `edges` prop `<ReactFlow>` receives, instead of trying to render the real
 * graph. `vi.mock` is file-scoped in Vitest, so this lives in its own file —
 * applying it inside ComposerCanvas.test.jsx would silently break the ~40
 * tests there that assert against REAL React Flow DOM.
 */
import { describe, it, expect, beforeAll, vi } from 'vitest'
import { render } from '@testing-library/react'

const { capturedEdges } = vi.hoisted(() => ({ capturedEdges: [] }))

vi.mock('@xyflow/react', async () => {
  const actual = await vi.importActual('@xyflow/react')
  return {
    ...actual,
    ReactFlow: (props) => {
      capturedEdges.push(props.edges)
      return null
    },
  }
})

import ComposerCanvas from '../console/ComposerCanvas.jsx'

beforeAll(() => {
  window.ResizeObserver = window.ResizeObserver || class {
    observe() {} unobserve() {} disconnect() {}
  }
})

function render2Step(causalityPivot) {
  const draft = { steps: [
    { id: 's1', name: 'a', detections: [] },
    { id: 's2', name: 'b', detections: [], causalityParent: 's1', causalityPivot },
  ] }
  capturedEdges.length = 0
  render(
    <ComposerCanvas
      draft={draft}
      steps={draft.steps}
      lens="design"
      validation={{ counts: { steps: 2, detections: 0 } }}
    />
  )
  return capturedEdges.at(-1)
}

describe('ComposerCanvas — non-process_lineage pivots render a typed edge (Important 3, spec §5.5 test 12)', () => {
  it('labels and dashes a non-process_lineage pivot edge, instead of flattening it into the process chain', () => {
    const edges = render2Step('network_session')
    const edge = edges.find((e) => e.id === 's1->s2')
    expect(edge).toBeTruthy()
    // React Flow's own edge `label` prop — "gives it for free" per the
    // review ruling — renders the pivot kind as visible text.
    expect(edge.label).toBe('network_session')
    // Visually distinguished from a plain process-lineage edge.
    expect(edge.style.strokeDasharray).toBeTruthy()
  })

  it('draws a plain, unlabeled solid edge for the default process_lineage pivot', () => {
    const edges = render2Step(undefined) // omitted → defaults process_lineage
    const edge = edges.find((e) => e.id === 's1->s2')
    expect(edge).toBeTruthy()
    expect(edge.label).toBeUndefined()
    expect(edge.style.strokeDasharray).toBeUndefined()
  })

  it('also types a non-process pivot when explicitly authored as process_lineage\'s sibling kinds', () => {
    // Spread beyond network_session — exposure_exploit is one of the other
    // non-process pivots `causality_graph.py` recognizes (composerLayout.js's
    // STITCH_KINDS).
    const edges = render2Step('exposure_exploit')
    const edge = edges.find((e) => e.id === 's1->s2')
    expect(edge.label).toBe('exposure_exploit')
    expect(edge.style.strokeDasharray).toBeTruthy()
  })
})

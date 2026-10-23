/**
 * ComposerCanvas — the graph gestures React Flow drives, through the props it
 * would call (Composer mode, review fix pass).
 *
 * jsdom cannot run React Flow's pointer pipeline (no layout, no DOMMatrix), so
 * — like ComposerCanvas.pivotEdges.test.jsx — this file replaces the named
 * `ReactFlow` export with a recorder and calls the REAL handlers the canvas
 * hands it: onConnectEnd, onReconnect(+Start/End), the edge callbacks. The
 * recorder also calls onInit with a minimal instance so create-on-drop can
 * convert a pointer to flow coordinates. `vi.mock` is file-scoped, which is
 * why this cannot live in ComposerCanvas.test.jsx (that suite asserts against
 * real React Flow DOM).
 */
import { describe, it, expect, beforeAll, vi } from 'vitest'
import { render, screen, act } from '@testing-library/react'

const { captured } = vi.hoisted(() => ({ captured: { props: null } }))

vi.mock('@xyflow/react', async () => {
  const actual = await vi.importActual('@xyflow/react')
  const { useEffect } = await vi.importActual('react')
  return {
    ...actual,
    ReactFlow: (props) => {
      captured.props = props
      useEffect(() => {
        props.onInit?.({
          screenToFlowPosition: (p) => ({ x: p.x, y: p.y }),
          getZoom: () => 1, fitView() {}, fitBounds() {}, zoomIn() {}, zoomOut() {}, zoomTo() {},
        })
        // eslint-disable-next-line react-hooks/exhaustive-deps
      }, [])
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

// s1 → s2 → s3, one spine.
const STEPS = [
  { id: 's1', name: 'a', detections: [] },
  { id: 's2', name: 'b', detections: [], causalityParent: 's1', causalityPivot: 'process_lineage' },
  { id: 's3', name: 'c', detections: [], causalityParent: 's2', causalityPivot: 'process_lineage' },
]

function mount(extra = {}) {
  const spies = {
    onConnectSteps: vi.fn(), onDropCreate: vi.fn(), onReparent: vi.fn(),
    onSetPivot: vi.fn(), onEdgeDelete: vi.fn(), onDeleteStepId: vi.fn(),
  }
  const draft = { steps: STEPS, cgo: 'apache2 / www-data' }
  captured.props = null
  render(
    <ComposerCanvas
      draft={draft}
      steps={STEPS}
      lens="design"
      validation={{ counts: { steps: 3, detections: 0 } }}
      {...spies}
      {...extra}
    />,
  )
  return spies
}

/** A pointer-up over an element carrying `className` (and optionally a node id). */
function pointerOver(className, nodeId = null) {
  const host = document.createElement('div')
  host.className = className
  if (nodeId) host.setAttribute('data-id', nodeId)
  const child = document.createElement('span')
  host.appendChild(child)
  document.body.appendChild(host)
  return { clientX: 40, clientY: 60, target: child }
}

describe('ComposerCanvas — edges handed to React Flow', () => {
  it('Design lens: the trigger and proof links are derived edges in the graph', () => {
    mount()
    const ids = captured.props.edges.map((e) => `${e.type}:${e.id}`)
    expect(ids).toContain('derived:__cgo__->s1')
    expect(ids).toContain('derived:s3->__proof__')
    expect(ids).toContain('causality:s1->s2')
  })

  it('Lanes lens: only step-to-step links (the bands stand in for the anchors)', () => {
    mount({ lens: 'lanes' })
    expect(captured.props.edges.every((e) => e.type === 'causality')).toBe(true)
  })

  it('Design causality edges change the pivot and delete through their own callbacks', () => {
    const spies = mount()
    const edge = captured.props.edges.find((e) => e.id === 's1->s2')
    act(() => { edge.data.onCyclePivot() })
    expect(spies.onSetPivot).toHaveBeenCalledWith('s2', 'network_session')
    act(() => { edge.data.onDelete() })
    expect(spies.onEdgeDelete).toHaveBeenCalledWith('s2')
  })

  it('Run lens: edges carry no edit callbacks — the read-only spine cannot rewrite the draft', () => {
    mount({ lens: 'run' })
    const causality = captured.props.edges.filter((e) => e.type === 'causality')
    expect(causality.length).toBeGreaterThan(0)
    for (const e of causality) {
      expect(e.data?.onCyclePivot).toBeUndefined()
      expect(e.data?.onDelete).toBeUndefined()
    }
  })

  it('turns React Flow’s document-wide delete key and node tab stops off', () => {
    mount()
    expect(captured.props.deleteKeyCode).toBeNull()
    expect(captured.props.nodesFocusable).toBe(false)
  })
})

describe('ComposerCanvas — releasing a connection React Flow did not accept', () => {
  const FROM_S3_OUT = { fromNode: { id: 's3', type: 'step' }, fromHandle: { type: 'source' } }

  it('over empty canvas from an OUTPUT port: creates a step there, parented to the source', () => {
    const spies = mount()
    act(() => {
      captured.props.onConnectEnd(pointerOver('react-flow__pane'), { ...FROM_S3_OUT, isValid: false, toNode: null })
    })
    expect(spies.onDropCreate).toHaveBeenCalledWith({ position: { x: 40, y: 60 }, parentId: 's3' })
  })

  it('over empty canvas from an INPUT port: creates nothing (the gesture had no parent)', () => {
    const spies = mount()
    act(() => {
      captured.props.onConnectEnd(pointerOver('react-flow__pane'), {
        fromNode: { id: 's2', type: 'step' }, fromHandle: { type: 'target' }, isValid: false, toNode: null,
      })
    })
    expect(spies.onDropCreate).not.toHaveBeenCalled()
  })

  it('over a card, refused: shows WHY instead of ending silently', () => {
    const spies = mount()
    act(() => {
      captured.props.onConnectEnd(pointerOver('react-flow__node', 's1'), {
        ...FROM_S3_OUT, isValid: false, toNode: { id: 's1', type: 'step' },
      })
    })
    expect(spies.onConnectSteps).not.toHaveBeenCalled()
    expect(spies.onDropCreate).not.toHaveBeenCalled()
    expect(screen.getByTestId('canvas-refusal').textContent).toMatch(/cycle/i)
  })

  it('over a card BODY (no port in reach), allowed: connects instead of spawning a step', () => {
    const spies = mount()
    act(() => {
      captured.props.onConnectEnd(pointerOver('react-flow__node', 's3'), {
        fromNode: { id: 's1', type: 'step' }, fromHandle: { type: 'source' }, isValid: false, toNode: null,
      })
    })
    expect(spies.onConnectSteps).toHaveBeenCalledWith('s1', 's3')
    expect(spies.onDropCreate).not.toHaveBeenCalled()
  })

  it('an abandoned edge reconnect never spawns a phantom step', () => {
    const spies = mount()
    act(() => {
      captured.props.onReconnectStart()
      captured.props.onConnectEnd(pointerOver('react-flow__pane'), { ...FROM_S3_OUT, isValid: false, toNode: null })
      captured.props.onReconnectEnd({}, { source: 's2', target: 's3' }, 'source', { isValid: false, toNode: null })
    })
    expect(spies.onDropCreate).not.toHaveBeenCalled()
  })
})

describe('ComposerCanvas — reconnecting an edge re-parents its child', () => {
  it('moving the TAIL to another step changes the same child’s parent', () => {
    const spies = mount()
    act(() => {
      captured.props.onReconnect({ source: 's2', target: 's3' }, { source: 's1', target: 's3' })
    })
    expect(spies.onReparent).toHaveBeenCalledWith('s3', 's1')
  })

  it('a connection naming a DIFFERENT child is not a reconnect of this edge (no stolen steps)', () => {
    const spies = mount()
    act(() => {
      captured.props.onReconnect({ source: 's1', target: 's2' }, { source: 's1', target: 's3' })
    })
    expect(spies.onReparent).not.toHaveBeenCalled()
  })

  it('a reconnect React Flow refused still explains itself', () => {
    mount()
    act(() => {
      // Re-parenting s2 under s3 would close a cycle (s3 descends from s2).
      captured.props.onReconnectEnd({}, { source: 's1', target: 's2' }, 'source', {
        isValid: false, toNode: { id: 's3', type: 'step' },
      })
    })
    expect(screen.getByTestId('canvas-refusal').textContent).toMatch(/cycle/i)
  })
})

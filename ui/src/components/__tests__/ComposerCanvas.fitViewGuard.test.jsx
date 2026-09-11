/**
 * ComposerCanvas — fitView zero-size mount guard (Task 11, fix round 1).
 *
 * Task 11's original guard test only asserted `render(...).not.toThrow()`
 * against a 0x0 container. That never discriminated, for TWO independent
 * reasons (task-11-report.md, Finding 1):
 *
 *   1. @xyflow/react 12.11.6 logs its own error004 ("The parent container
 *      needs a width and a height…") via `console.warn` rather than
 *      throwing on a 0x0 container, so `expect(...).not.toThrow()` could
 *      never fail on that alone.
 *   2. Both `src/test/setup.js` and ComposerCanvas.test.jsx's own
 *      `beforeAll` stub `window.ResizeObserver` as a TOTAL no-op —
 *      `observe(){}` never invokes its callback. `DesignGraph`'s `measured`
 *      state therefore never became `true` in ANY existing test, including
 *      the pre-existing 1200x800 ones — the guard's actual payoff (fitView
 *      engaging once real dimensions are observed) was exercised by ZERO
 *      tests in the whole suite.
 *
 * This file replaces that test with one that actually discriminates, both
 * halves:
 *   - a CONTROLLABLE ResizeObserver mock that CAPTURES the constructor's
 *     callback instead of swallowing it, so the test can invoke it directly
 *     with a chosen `contentRect`;
 *   - a partial `@xyflow/react` mock that RECORDS the `fitView` prop
 *     `<ReactFlow>` receives on every render.
 *
 * Kept in its OWN file, not appended to ComposerCanvas.test.jsx: the
 * `vi.mock('@xyflow/react', ...)` below replaces the real `<ReactFlow>`
 * with a prop-capturing stub — correct for this guard, but `vi.mock` is
 * module-wide, and applying it inside ComposerCanvas.test.jsx would
 * silently break the ~30 other tests there that assert against REAL React
 * Flow DOM (`.react-flow__node`, node transforms, Handles, drag/connect
 * simulation via `window.__rfOnNodesChange` / `window.__rfOnConnect`).
 * `vi.mock` is file-scoped in Vitest, so isolating this here keeps both
 * suites honest without trading one guard for another.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, act } from '@testing-library/react'

// `vi.mock` factories are hoisted above imports; `vi.hoisted` is the
// sanctioned way to share mutable state with a hoisted factory.
const { capturedFitViews } = vi.hoisted(() => ({ capturedFitViews: [] }))

vi.mock('@xyflow/react', async () => {
  const actual = await vi.importActual('@xyflow/react')
  return {
    ...actual,
    // The real <ReactFlow> measures its own container, syncs nodes/edges
    // into its internal store, and renders the whole node/edge graph —
    // none of that is what this guard is about. Recording the one prop
    // under test and rendering nothing keeps this file from depending on
    // React Flow's internal store wiring (which a stand-alone
    // ReactFlowProvider does not fully initialize on its own).
    ReactFlow: (props) => {
      capturedFitViews.push(props.fitView)
      return null
    },
  }
})

import ComposerCanvas from '../console/ComposerCanvas.jsx'

/** Captures the ResizeObserver constructor's callback so a test can fire it directly. */
class ControllableResizeObserver {
  constructor(cb) {
    this.cb = cb
    ControllableResizeObserver.instances.push(this)
  }
  observe() {}
  unobserve() {}
  disconnect() {}
}
ControllableResizeObserver.instances = []

const DRAFT = { originId: 'SIM-EDR-001', name: 'Credential Dumping', plane: 'EDR', steps: [] }
const STEPS = [{
  id: 'step-01', name: 'Read shadow', authored: false,
  command: 'cat /etc/shadow', identity: 'www-data', technique: 'T1003',
  platforms: [], causalityParent: null, causalityPivot: null,
  detections: [{ plane: 'EDR', type: 'BIOC', description: 'dump' }],
}]
const VALIDATION = { counts: { steps: 1, detections: 1 } }

function baseProps(over = {}) {
  const draft = { ...DRAFT, steps: STEPS }
  return {
    draft, steps: draft.steps, validation: VALIDATION, yamlText: '',
    lens: 'design', ...over,
  }
}

beforeEach(() => {
  capturedFitViews.length = 0
  ControllableResizeObserver.instances.length = 0
  window.ResizeObserver = ControllableResizeObserver
})

describe('ComposerCanvas — fitView zero-size mount guard (Task 11, fix round 1)', () => {
  it('withholds fitView while the pane measures 0x0, then engages it once real dimensions are observed', () => {
    render(<ComposerCanvas {...baseProps()} />)

    // Mount: no ResizeObserver callback has fired yet, so `measured` starts
    // false. A lazily-hidden 0x0 mount must NOT get the bare `fitView`
    // that used to be passed unconditionally.
    expect(capturedFitViews.at(-1)).toBe(false)

    const ro = ControllableResizeObserver.instances.at(-1)
    expect(ro).toBeTruthy()

    // The pane is still 0x0 — an explicit zero contentRect must not flip it.
    act(() => { ro.cb([{ contentRect: { width: 0, height: 0 } }]) })
    expect(capturedFitViews.at(-1)).toBe(false)

    // Real dimensions arrive (e.g. the Composer's tab becomes visible) —
    // fitView must now engage on the next render.
    act(() => { ro.cb([{ contentRect: { width: 960, height: 640 } }]) })
    expect(capturedFitViews.at(-1)).toBe(true)
  })
})

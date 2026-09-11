/**
 * ComposerCanvas — smoke coverage for the standalone centre-column view.
 *
 * The integration behaviours (launch gate, dirty tracking, palette wiring) live
 * in the ComposerView unit. Here we pin only what this component owns: the
 * preserved DOM contract the ComposerView tests depend on, and the ONE honesty
 * rule that is this component's alone — the Run lens never fabricates a
 * CONFIRMED, and a BROKEN stitch renders BROKEN.
 */
import { describe, it, expect, vi, beforeAll } from 'vitest'
import { render, screen, within, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import ComposerCanvas from '../console/ComposerCanvas.jsx'

// jsdom has no layout engine — every element reports 0x0 for offsetWidth /
// offsetHeight, and React Flow (Composer Design lens, Task 8) measures its
// container on mount, refusing to render any node until it gets a non-zero
// size. Scoped to THIS file (and ComposerView.test.jsx, which mounts
// ComposerCanvas indirectly and carries the identical stub) rather than the
// shared `src/test/setup.js` — each test file gets its own fresh jsdom
// environment, so a `beforeAll` here cannot leak into unrelated suites.
beforeAll(() => {
  window.ResizeObserver = window.ResizeObserver || class {
    observe() {} unobserve() {} disconnect() {}
  }
  Object.defineProperty(HTMLElement.prototype, 'offsetWidth', { configurable: true, value: 1200 })
  Object.defineProperty(HTMLElement.prototype, 'offsetHeight', { configurable: true, value: 800 })
})

const DRAFT = {
  originId: 'SIM-EDR-001',
  name: 'Credential Dumping',
  plane: 'EDR',
  cgo: 'apache2 / www-data',
  teardown: ['rm -f /tmp/x'],
  steps: [],
}

const STEPS = [
  {
    id: 'step-01', name: 'Read shadow', authored: false,
    command: 'cat /etc/shadow', identity: 'www-data', technique: 'T1003',
    platforms: [], causalityParent: null, causalityPivot: null,
    detections: [{ plane: 'EDR', type: 'BIOC', description: 'dump' }],
  },
  {
    id: 'step-02', name: 'Exfil', authored: true,
    command: 'curl x', identity: 'www-data', technique: 'T1041',
    platforms: [], causalityParent: 'step-01', causalityPivot: 'process_lineage',
    detections: [],
  },
]

const VALIDATION = { counts: { steps: 2, detections: 1 } }

// Same shape as the other Run-lens graph fixtures below — just needs enough
// structure for `layoutCausalityGraph` to run without throwing.
const GRAPH_FIXTURE = {
  run_id: 'run-9',
  nodes: [{ id: 'proc:run-9:step-01', kind: 'process', label: 'curl' }],
  edges: [],
  causality_summary: { chain_completeness_pct: 100, broken_stitches: [] },
}

function baseProps(over = {}) {
  // `steps` defaults from `over.draft.steps` (falling back to the module
  // STEPS) BEFORE `...over` is spread, so a caller overriding only `draft`
  // (the Task 8 React Flow tests below) gets a `steps` prop that matches it,
  // while every existing call that overrides `steps` explicitly is unchanged.
  const draft = over.draft || { ...DRAFT, steps: STEPS }
  return {
    draft,
    steps: draft.steps ?? STEPS,
    validation: VALIDATION,
    yamlText: 'scenario:\n  - id: step-01\n    # NO EXPECTED DETECTION',
    tenantName: 'acme-xsiam',
    agentName: 'jumpbox-1',
    ...over,
  }
}

describe('ComposerCanvas — preserved DOM', () => {
  it('renders the chain with START/END anchors, step cards and add-step', () => {
    render(<ComposerCanvas {...baseProps()} />)
    expect(screen.getByTestId('composer-chain')).toBeInTheDocument()
    const start = screen.getByTestId('chain-start')
    expect(within(start).getByText('Tenant')).toBeInTheDocument()
    expect(within(start).getByText('Agent')).toBeInTheDocument()
    expect(screen.getByTestId('chain-end')).toBeInTheDocument()
    expect(screen.getByTestId('chain-step-step-01')).toBeInTheDocument()
    expect(screen.getByTestId('composer-add-step')).toBeInTheDocument()
    // the gap marker lives INSIDE the detection-less step node
    const s2 = screen.getByTestId('chain-step-step-02')
    expect(within(s2.closest('.chain-node')).getByText(/no expected detection/i)).toBeInTheDocument()
  })

  it('shows the CGO on the meta line', () => {
    render(<ComposerCanvas {...baseProps()} />)
    expect(screen.getByText(/CGO apache2 \/ www-data/)).toBeInTheDocument()
  })

  it('renders the YAML pre with the passed yamlText when canvasView=yaml', () => {
    render(<ComposerCanvas {...baseProps({ canvasView: 'yaml' })} />)
    const pre = screen.getByTestId('composer-yaml')
    expect(pre.textContent).toContain('- id: step-01')
    expect(pre.textContent).toContain('NO EXPECTED DETECTION')
  })

  it('shows the first-run empty state with the three exact labels', () => {
    render(<ComposerCanvas {...baseProps({ draft: { ...DRAFT, steps: [] }, steps: [], validation: { counts: { steps: 0, detections: 0 } } })} />)
    const fr = screen.getByTestId('composer-firstrun')
    expect(within(fr).getByText('Start from a library scenario')).toBeInTheDocument()
    expect(within(fr).getByText('Start from a TTP card')).toBeInTheDocument()
    expect(within(fr).getByText('Start from a blank step')).toBeInTheDocument()
  })

  it('renders the origin-error banner honestly', () => {
    render(<ComposerCanvas {...baseProps({ originError: 'HTTP 404', fromId: 'SIM-X' })} />)
    expect(screen.getByTestId('composer-origin-error').textContent)
      .toMatch(/not because the scenario has no steps/i)
  })

  it('wires reorder / duplicate / remove / add / select callbacks by index', async () => {
    const onMoveStep = vi.fn()
    const onDuplicateStep = vi.fn()
    const onRemoveStep = vi.fn()
    const onAddStep = vi.fn()
    const onSelect = vi.fn()
    render(<ComposerCanvas {...baseProps({ onMoveStep, onDuplicateStep, onRemoveStep, onAddStep, onSelect })} />)
    const user = userEvent.setup()
    await user.click(screen.getByLabelText('Move step-01 later'))
    expect(onMoveStep).toHaveBeenCalledWith(0, 1)
    await user.click(screen.getByLabelText('Duplicate step-02'))
    expect(onDuplicateStep).toHaveBeenCalledWith(1)
    await user.click(screen.getByLabelText('Remove step-02'))
    expect(onRemoveStep).toHaveBeenCalledWith(1)
    await user.click(screen.getByTestId('composer-add-step'))
    expect(onAddStep).toHaveBeenCalled()
    await user.click(screen.getByTestId('chain-step-step-01'))
    expect(onSelect).toHaveBeenCalledWith('step-01')
  })

  it('preserves step DOM order (first card is step-01)', () => {
    render(<ComposerCanvas {...baseProps()} />)
    const cards = screen.getAllByTestId(/^chain-step-/)
    expect(cards[0]).toHaveAttribute('data-testid', 'chain-step-step-01')
    expect(cards[1]).toHaveAttribute('data-testid', 'chain-step-step-02')
  })
})

describe('ComposerCanvas — lens toggle', () => {
  it('exposes Design / Run lens buttons and Chain / YAML view buttons', () => {
    render(<ComposerCanvas {...baseProps()} />)
    expect(screen.getByTestId('composer-lens-design')).toBeInTheDocument()
    expect(screen.getByTestId('composer-lens-run')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Chain' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'YAML' })).toBeInTheDocument()
  })

  it('calls onLens when a lens button is clicked', async () => {
    const onLens = vi.fn()
    render(<ComposerCanvas {...baseProps({ onLens })} />)
    await userEvent.setup().click(screen.getByTestId('composer-lens-run'))
    expect(onLens).toHaveBeenCalledWith('run')
  })
})

describe('ComposerCanvas — Stitch overlay (design intent)', () => {
  // Two steps consume the SAME planted key, so a join edge exists to draw.
  const STITCH_STEPS = [
    { ...STEPS[0], command: 'curl --local-port {stitch:src_port} https://x' },
    { ...STEPS[1], command: 'nc -p {stitch:src_port} 10.0.0.9' },
  ]
  const MODEL = { src_port: { resolve: 'auto_port' } }

  it('exposes a stitch overlay toggle in the head', () => {
    render(<ComposerCanvas {...baseProps()} />)
    expect(screen.getByTestId('composer-stitch-toggle')).toBeInTheDocument()
  })

  it('calls onToggleStitch when the toggle is clicked', async () => {
    const onToggleStitch = vi.fn()
    render(<ComposerCanvas {...baseProps({ onToggleStitch })} />)
    await userEvent.setup().click(screen.getByTestId('composer-stitch-toggle'))
    expect(onToggleStitch).toHaveBeenCalled()
  })

  it('does NOT draw the overlay by default (off keeps existing contracts green)', () => {
    render(<ComposerCanvas {...baseProps({ steps: STITCH_STEPS, draft: { ...DRAFT, steps: STITCH_STEPS } })} />)
    expect(screen.queryByTestId('composer-stitch-overlay')).not.toBeInTheDocument()
  })

  it('draws the EXPECTED-only overlay when showStitch is on and a model plants a consumed key', () => {
    render(<ComposerCanvas {...baseProps({
      steps: STITCH_STEPS,
      draft: { ...DRAFT, steps: STITCH_STEPS },
      stitchModel: MODEL,
      showStitch: true,
    })} />)
    const overlay = screen.getByTestId('composer-stitch-overlay')
    expect(overlay).toBeInTheDocument()
    // The join path names its key, and reads as EXPECTED intent — never a run state.
    expect(overlay.querySelector('[data-stitch-key="src_port"]')).toBeTruthy()
    expect(overlay.textContent).toMatch(/EXPECTED/)
    expect(overlay.textContent).not.toMatch(/CONFIRMED|BROKEN/)
  })

  it('draws no overlay layer when the toggle is on but nothing is planted', () => {
    render(<ComposerCanvas {...baseProps({
      steps: STITCH_STEPS,
      draft: { ...DRAFT, steps: STITCH_STEPS },
      stitchModel: null,
      showStitch: true,
    })} />)
    expect(screen.queryByTestId('composer-stitch-overlay')).not.toBeInTheDocument()
  })

  it('quotes the run\'s REAL persisted binding on the Run lens, nothing invented', () => {
    const graph = {
      run_id: 'run-9', nodes: [{ id: 'proc:run-9:step-01', kind: 'process', label: 'curl' }],
      edges: [], causality_summary: { chain_completeness_pct: 100, broken_stitches: [] },
    }
    render(<ComposerCanvas {...baseProps({
      lens: 'run',
      causalityGraph: graph,
      activeRun: { run_id: 'run-9', status: 'completed', stitch_binding: { src_port: 51234, dst_ip: '203.0.113.10' } },
    })} />)
    const readout = screen.getByTestId('composer-stitch-binding')
    expect(readout.textContent).toMatch(/src_port=51234/)
    expect(readout.textContent).toMatch(/dst_ip=203\.0\.113\.10/)
  })
})

describe('ComposerCanvas — channel badge (Phase-3a)', () => {
  it('draws NO channel badge for agent-default nodes (byte-identical to today)', () => {
    render(<ComposerCanvas {...baseProps()} />)
    // both STEPS are agent-default (no channel, no target)
    expect(screen.queryByTestId('chain-step-channel-step-01')).not.toBeInTheDocument()
    expect(screen.queryByTestId('chain-step-target-step-01')).not.toBeInTheDocument()
    expect(screen.queryByTestId('chain-step-channel-step-02')).not.toBeInTheDocument()
    expect(screen.queryByTestId('chain-step-target-step-02')).not.toBeInTheDocument()
  })

  it('badges an eal step EAL, naming its emitter in the title', () => {
    const ealSteps = [
      { ...STEPS[0], channel: 'eal', eal: { plugin: 'ngfw_eal_emitter', params: {} } },
      STEPS[1],
    ]
    render(<ComposerCanvas {...baseProps({ steps: ealSteps, draft: { ...DRAFT, steps: ealSteps } })} />)
    const badge = screen.getByTestId('chain-step-channel-step-01')
    expect(badge.textContent).toBe('EAL')
    expect(badge.getAttribute('title')).toMatch(/emitter ngfw_eal_emitter/)
    // step-02 stays an unbadged agent node
    expect(screen.queryByTestId('chain-step-channel-step-02')).not.toBeInTheDocument()
  })

  it('badges an agent step with a second-endpoint target, showing where it runs', () => {
    const targetSteps = [
      { ...STEPS[0], target: 'db-prod-02' },
      STEPS[1],
    ]
    render(<ComposerCanvas {...baseProps({ steps: targetSteps, draft: { ...DRAFT, steps: targetSteps } })} />)
    const badge = screen.getByTestId('chain-step-target-step-01')
    expect(badge.textContent).toBe('→ db-prod-02')
    expect(badge.getAttribute('title')).toMatch(/second endpoint db-prod-02/)
    // it is NOT an EAL badge
    expect(screen.queryByTestId('chain-step-channel-step-01')).not.toBeInTheDocument()
  })

  it('keeps step DOM order and node identity when a badge is present', () => {
    const ealSteps = [
      { ...STEPS[0], channel: 'eal', eal: { plugin: 'x', params: {} } },
      STEPS[1],
    ]
    render(<ComposerCanvas {...baseProps({ steps: ealSteps, draft: { ...DRAFT, steps: ealSteps } })} />)
    const cards = screen.getAllByTestId(/^chain-step-step-/)
    expect(cards[0]).toHaveAttribute('data-testid', 'chain-step-step-01')
    expect(cards[1]).toHaveAttribute('data-testid', 'chain-step-step-02')
  })
})

describe('ComposerCanvas — Run lens honesty', () => {
  it('shows "no run yet — EXPECTED only" when there is no causality graph', () => {
    render(<ComposerCanvas {...baseProps({ lens: 'run', causalityGraph: null })} />)
    expect(screen.getByTestId('composer-run-graph').textContent).toMatch(/EXPECTED only/i)
  })

  it('renders the real graph and keeps a BROKEN stitch BROKEN — never CONFIRMED', () => {
    const graph = {
      run_id: 'run-9', scenario_id: 'SIM-EDR-001', run_status: 'completed',
      nodes: [
        { id: 'cgo:run-9', kind: 'cgo', label: 'apache2' },
        { id: 'proc:run-9:step-01', kind: 'process', label: 'cat' },
        { id: 'proc:run-9:step-02', kind: 'process', label: 'curl' },
      ],
      edges: [
        { id: 'e1', source: 'cgo:run-9', target: 'proc:run-9:step-01', kind: 'process_lineage', state: 'CONFIRMED' },
        { id: 'e2', source: 'proc:run-9:step-01', target: 'proc:run-9:step-02', kind: 'temporal', state: 'BROKEN', rationale: 'fired outside 10s window' },
      ],
      causality_summary: { chain_completeness_pct: 50, broken_stitches: ['step-01→step-02'], stitched_incident: false },
    }
    // states derived by the layout module — step-02 must read BROKEN
    const causalityStates = { 'step-02': { state: 'BROKEN', nodeId: 'proc:run-9:step-02', hasBrokenStitch: true } }
    render(<ComposerCanvas {...baseProps({ lens: 'run', causalityGraph: graph, causalityStates })} />)
    const rg = screen.getByTestId('composer-run-graph')
    expect(within(rg).getByText(/50%/)).toBeInTheDocument()
    expect(screen.getByTestId('composer-broken-stitches').textContent).toMatch(/step-01→step-02/)
    // the authored spine card for step-02 carries the REAL BROKEN badge
    const s2 = screen.getByTestId('chain-step-step-02')
    expect(within(s2).getByText('BROKEN')).toBeInTheDocument()
    // and never a fabricated CONFIRMED for a step with no confirmed stitch
    expect(within(s2).queryByText('CONFIRMED')).not.toBeInTheDocument()
  })
})

describe('ComposerCanvas — Design lens through React Flow (Task 8, render only)', () => {
  // React Flow measures its container; jsdom reports 0x0, which makes it
  // refuse to render nodes. The stub lives globally in `src/test/setup.js`
  // (every test that mounts a `<ReactFlow>` needs it, including
  // ComposerView.test.jsx, which renders this component indirectly) — no
  // local beforeAll needed here.

  it('renders one node per step through React Flow, at stored positions', () => {
    const draft = { steps: [
      { id: 's1', name: 'drop', detections: [] },
      { id: 's2', name: 'dump', detections: [], causalityParent: 's1' },
    ] }
    const { container } = render(
      <ComposerCanvas {...baseProps({ draft, lens: 'design',
                                      storedLayout: { s2: { x: 400, y: 300 } } })} />
    )
    expect(container.querySelectorAll('.react-flow__node')).toHaveLength(2)
    const s2 = container.querySelector('[data-id="s2"]')
    expect(s2.style.transform).toContain('400')
    expect(s2.style.transform).toContain('300')
  })

  it('falls back to computed positions for a step with no stored position', () => {
    const draft = { steps: [{ id: 's1', name: 'drop', detections: [] }] }
    const { container } = render(
      <ComposerCanvas {...baseProps({ draft, lens: 'design', storedLayout: null })} />
    )
    expect(container.querySelector('[data-id="s1"]')).toBeTruthy()
  })

  it('keeps START/END as plain anchors outside React Flow\'s own node graph', () => {
    // "one node per step" above only holds if START/END are never counted as
    // React Flow nodes — pin that directly against the 2-step fixture too.
    render(<ComposerCanvas {...baseProps()} />)
    expect(screen.getByTestId('chain-start')).toBeInTheDocument()
    expect(screen.getByTestId('chain-end')).toBeInTheDocument()
    expect(document.querySelectorAll('.react-flow__node')).toHaveLength(2)
  })

  it('draws the START->first-step and last-step->END dashed connectors (review round 1, finding 1)', () => {
    // START/END are excluded from `rfEdges` (they aren't React Flow nodes),
    // so the `root`/`terminal` spine edges that used to draw the dashed
    // connector + endpoint dots into/out of them were silently dropped with
    // nothing replacing them. Without this the canvas is visually three
    // disconnected blocks (START card / React Flow box / END card).
    render(<ComposerCanvas {...baseProps()} />)
    const root = screen.getByTestId('composer-connector-root')
    const terminal = screen.getByTestId('composer-connector-terminal')
    // Dashed line + a dot at each end — the same visual language the removed
    // SVG root/terminal edges used (`strokeDasharray`, steel endpoint dots).
    for (const connector of [root, terminal]) {
      const line = connector.querySelector('line')
      expect(line).toBeTruthy()
      expect(line.getAttribute('stroke-dasharray')).toBe('4 4')
      expect(connector.querySelectorAll('circle')).toHaveLength(2)
    }
  })
})

describe('ComposerCanvas — draggable nodes persist position (Task 9)', () => {
  /** Emit the node-position change React Flow produces after a drag. */
  function fireNodeDrag(id, position) {
    // React Flow's onNodesChange receives {id, type:'position', position,
    // dragging:false} on drag end. Driving the handler directly is the stable
    // seam — synthesising pointer events against a transformed pane is not.
    const handler = window.__rfOnNodesChange
    handler([{ id, type: 'position', position, dragging: false }])
  }

  it('reports a moved node in FLOW coordinates, not screen pixels', () => {
    // React Flow already divides screen delta by zoom before emitting a position
    // change. The bug this guards is someone "helpfully" re-applying zoom on top,
    // which double-scales every drag. See spec §5.4.
    //
    // NOTE (deviation from the task-9 brief, documented in task-9-report.md):
    // the brief's literal fixture used {x:300,y:150}, asserting the callback
    // receives that pair unchanged. 300/8 and 150/8 are not whole numbers —
    // under ANY 8px-grid-snap implementation (which the very next test below
    // requires), 300 must move to 296 or 304 and 150 to 152; it can never come
    // back as 300/150. Using grid-aligned input here instead (320,160 — both
    // exact multiples of SNAP=8) keeps the test's real intent — the value is
    // NOT zoom-doubled — provable without colliding with the snap-grid test.
    const onNodeMoved = vi.fn()
    const draft = { steps: [{ id: 's1', name: 'a', detections: [] }] }
    render(<ComposerCanvas {...baseProps({ draft, lens: 'design', onNodeMoved })} />)

    // Simulate the change React Flow emits after a drag at zoom 2.
    fireNodeDrag('s1', { x: 320, y: 160 })
    expect(onNodeMoved).toHaveBeenCalledWith('s1', 320, 160)   // NOT 640/320
  })

  it('snaps a stored position to the 8px grid', () => {
    const onNodeMoved = vi.fn()
    const draft = { steps: [{ id: 's1', name: 'a', detections: [] }] }
    render(<ComposerCanvas {...baseProps({ draft, lens: 'design', onNodeMoved })} />)
    fireNodeDrag('s1', { x: 301, y: 149 })
    expect(onNodeMoved).toHaveBeenCalledWith('s1', 304, 152)
  })

  // Review round 1, finding 2: the three tests above all drive
  // `window.__rfOnNodesChange` directly and never touch `nodesDraggable`, so
  // none of them notice if the pane-level prop that actually turns dragging
  // on regresses back to `false`. This is the missing POSITIVE guard —
  // proven a real guard by temporarily reverting `nodesDraggable={!runLens}`
  // to `nodesDraggable={false}` in ComposerCanvas.jsx, re-running this exact
  // test, and confirming it fails; before/after output is in
  // task-9-report.md.
  it('is actually draggable in the Design lens (positive guard on nodesDraggable)', () => {
    const draft = { steps: [{ id: 's1', name: 'a', detections: [] }] }
    const { container } = render(
      <ComposerCanvas {...baseProps({ draft, lens: 'design' })} />
    )
    const node = container.querySelector('.react-flow__node')
    expect(node).toBeTruthy()
    expect(node.classList.contains('draggable')).toBe(true)
  })

  // Finding 1's fix: dragging is scoped to a small, discoverable grip
  // (`dragHandle: '.chain-node__grip'` on the node object) rather than the
  // near-invisible card-border sliver `nodrag`/`nopan` scoping alone left
  // behind. This pins that the selector `dragHandle` actually names exists
  // in the rendered DOM (so the handle isn't pointed at nothing) and is
  // discoverable via an aria-label, independent of the draggable-class
  // guard above.
  it('renders a discoverable grip handle matching the dragHandle selector', () => {
    const draft = { steps: [{ id: 's1', name: 'a', detections: [] }] }
    const { container } = render(
      <ComposerCanvas {...baseProps({ draft, lens: 'design' })} />
    )
    const grip = container.querySelector('.chain-node__grip')
    expect(grip).toBeTruthy()
    expect(grip.getAttribute('aria-label')).toBe('Drag to reposition')
  })

  it('exposes no drag affordance in the run lens', () => {
    const graph = {
      run_id: 'run-9', nodes: [{ id: 'proc:run-9:step-01', kind: 'process', label: 'curl' }],
      edges: [], causality_summary: { chain_completeness_pct: 100, broken_stitches: [] },
    }
    const { container } = render(
      <ComposerCanvas {...baseProps({ lens: 'run', causalityGraph: graph })} />
    )
    container.querySelectorAll('.react-flow__node').forEach((n) => {
      expect(n.classList.contains('draggable')).toBe(false)
    })
  })
})

describe('ComposerCanvas — storedLayout wiring (additional requirement, Task 8)', () => {
  // Task 5 produces `draft.layout`; nothing before this task read it back —
  // positions would persist to the backend and then never apply. This pins
  // that ComposerCanvas actually threads a `storedLayout` prop down into the
  // React Flow node it produces (the ComposerView → ComposerCanvas leg of the
  // same wire is pinned in ComposerView.test.jsx, against the real draft).
  it('reaches the canvas: a step with a stored position renders there, not at its computed one', () => {
    const draft = { ...DRAFT, steps: STEPS }
    const { container } = render(
      <ComposerCanvas {...baseProps({ draft, storedLayout: { 'step-02': { x: 555, y: 111 } } })} />
    )
    const node = container.querySelector('[data-id="step-02"]')
    expect(node.style.transform).toContain('555')
    expect(node.style.transform).toContain('111')
  })
})

describe('ComposerCanvas — drawing causality edges (Task 10, direct-manipulation)', () => {
  // `window.__rfOnConnect` is the same test-seam pattern Task 9 established
  // for `window.__rfOnNodesChange` — jsdom cannot reliably drive React
  // Flow's own pointer-based connect gesture, so tests call the handler
  // React Flow would call directly.
  it('re-parents a step when a legal edge is drawn', () => {
    const onConnectSteps = vi.fn()
    const draft = { steps: [
      { id: 's1', name: 'a', detections: [] },
      { id: 's2', name: 'b', detections: [], causalityParent: 's1' },
      { id: 's3', name: 'c', detections: [], causalityParent: 's2' },
    ] }
    render(<ComposerCanvas {...baseProps({ draft, lens: 'design', onConnectSteps })} />)
    window.__rfOnConnect({ source: 's1', target: 's3' })
    expect(onConnectSteps).toHaveBeenCalledWith('s1', 's3')
  })

  it('REFUSES a cycle and renders the reason — a silent no-op reads as a broken canvas', () => {
    const onConnectSteps = vi.fn()
    const draft = { steps: [
      { id: 's1', name: 'a', detections: [] },
      { id: 's2', name: 'b', detections: [], causalityParent: 's1' },
    ] }
    const { getByTestId } = render(
      <ComposerCanvas {...baseProps({ draft, lens: 'design', onConnectSteps })} />
    )
    // DEVIATION from the brief's literal (unwrapped) call, documented in
    // task-10-report.md: unlike the success path above (whose `setRefusal(null)`
    // is a same-value bail-out React never schedules a render for), this path
    // sets a NEW `refusal` object — a real state update outside any React
    // event, which React 18's `createRoot` does not guarantee is committed
    // before this synchronous call returns. Without `act()` the assertion
    // below intermittently races the commit (observed here as a real,
    // reproducible failure, not a flake) and prints the standard "not wrapped
    // in act(...)" warning. `act()` changes nothing about what is asserted.
    act(() => { window.__rfOnConnect({ source: 's2', target: 's1' }) })
    expect(onConnectSteps).not.toHaveBeenCalled()
    expect(getByTestId('canvas-refusal').textContent).toMatch(/cycle|loop/i)
  })

  it('exposes no connect affordance in the run lens', () => {
    const { container } = render(
      <ComposerCanvas {...baseProps({ lens: 'run', causalityGraph: GRAPH_FIXTURE })} />
    )
    expect(container.querySelectorAll('.react-flow__handle')).toHaveLength(0)
  })

  it('dismisses the refusal banner on click, without touching onConnectSteps', async () => {
    const onConnectSteps = vi.fn()
    const draft = { steps: [
      { id: 's1', name: 'a', detections: [] },
      { id: 's2', name: 'b', detections: [], causalityParent: 's1' },
    ] }
    render(<ComposerCanvas {...baseProps({ draft, lens: 'design', onConnectSteps })} />)
    act(() => { window.__rfOnConnect({ source: 's2', target: 's1' }) }) // see note above
    const banner = screen.getByTestId('canvas-refusal')
    await userEvent.setup().click(within(banner).getByLabelText('Dismiss'))
    expect(screen.queryByTestId('canvas-refusal')).not.toBeInTheDocument()
    expect(onConnectSteps).not.toHaveBeenCalled()
  })

  it('offers the connect affordance (Handles) in the design lens', () => {
    const { container } = render(<ComposerCanvas {...baseProps({ lens: 'design' })} />)
    // Two steps in the default fixture, each with a target (top) and source
    // (bottom) handle.
    expect(container.querySelectorAll('.react-flow__handle').length).toBe(4)
  })
})

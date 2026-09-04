/**
 * ComposerCanvas — smoke coverage for the standalone centre-column view.
 *
 * The integration behaviours (launch gate, dirty tracking, palette wiring) live
 * in the ComposerView unit. Here we pin only what this component owns: the
 * preserved DOM contract the ComposerView tests depend on, and the ONE honesty
 * rule that is this component's alone — the Run lens never fabricates a
 * CONFIRMED, and a BROKEN stitch renders BROKEN.
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import ComposerCanvas from '../console/ComposerCanvas.jsx'

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

function baseProps(over = {}) {
  return {
    draft: { ...DRAFT, steps: STEPS },
    steps: STEPS,
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

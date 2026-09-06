/**
 * ComposerInspector — the editable right column.
 *
 * The behaviours pinned here are the ones the ComposerView contract depends on
 * when this component is wired in:
 *   - the four TEXT renderings the ComposerView suite queries survive the shift
 *     to an editable inspector (command, causality summary, `not declared`
 *     fallbacks, scenario teardown)
 *   - every edit is a callback — the component holds no source-of-truth state
 *   - a step with no detection tells the DC the honest consequence (it becomes
 *     a gap), and offers the bind path that satisfies the launch gate
 *   - no step selected renders editable workflow meta, not a dead panel
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import ComposerInspector from '../console/ComposerInspector.jsx'

const STEP_02 = {
  id: 'step-02',
  name: 'Attempt to read /etc/shadow',
  command: 'cat /etc/shadow',
  identity: 'www-data',
  technique: 'T1003.008',
  platforms: [],
  causalityParent: 'step-01',
  causalityPivot: 'process_lineage',
  detections: [],
  authored: false,
}

const STEP_01 = {
  id: 'step-01',
  name: 'Read /etc/passwd',
  command: 'cat /etc/passwd',
  identity: 'www-data',
  technique: 'T1087.001',
  platforms: ['linux'],
  causalityParent: null,
  causalityPivot: 'process_lineage',
  detections: [
    { plane: 'EDR', type: 'XQL', description: 'passwd read', ttpRef: 'TTP-2026-0032', detectionId: 'xql-1' },
  ],
  authored: false,
}

const DRAFT = {
  name: 'Credential Dumping',
  plane: 'EDR',
  tcRef: 'TC-EDR-03',
  ucRef: 'UCS-EDR-02',
  cgo: 'apache2 / www-data',
  teardown: ['rm -f /tmp/mimipenguin.sh'],
  steps: [STEP_01, STEP_02],
}

function renderInspector(overrides = {}) {
  const handlers = {
    onEditStep: vi.fn(),
    onAddDetection: vi.fn(),
    onRemoveDetection: vi.fn(),
    onSetCausalityParent: vi.fn(),
    onBindTtp: vi.fn(),
    onEditMeta: vi.fn(),
    onNavigate: vi.fn(),
  }
  render(
    <ComposerInspector
      selected={STEP_02}
      draft={DRAFT}
      steps={DRAFT.steps}
      {...handlers}
      {...overrides}
    />,
  )
  return handlers
}

describe('ComposerInspector — preserved text renderings', () => {
  it('renders the command as a queryable text node', () => {
    renderInspector()
    expect(screen.getByText('cat /etc/shadow')).toBeInTheDocument()
  })

  it('renders the causality summary as a text node above the selects', () => {
    renderInspector()
    expect(screen.getByText(/parent step-01 · pivot process_lineage/)).toBeInTheDocument()
  })

  it('renders "not declared" for an absent field (platforms on step-02)', () => {
    renderInspector()
    expect(screen.getAllByText(/not declared/i).length).toBeGreaterThan(0)
  })

  it('explains the consequence of a step with no expected detection', () => {
    renderInspector()
    expect(screen.getByText(/will execute and then be\s+reported as a gap/i)).toBeInTheDocument()
  })

  it('shows scenario-level teardown, labelled as scenario-level', () => {
    renderInspector()
    expect(screen.getByText(/the schema has no per-step cleanup/i)).toBeInTheDocument()
    expect(screen.getByText(/rm -f \/tmp\/mimipenguin\.sh/)).toBeInTheDocument()
  })
})

describe('ComposerInspector — edits call back, hold no state', () => {
  it('command edits call onEditStep with the new text', async () => {
    const user = userEvent.setup()
    const h = renderInspector()
    const pre = screen.getByText('cat /etc/shadow')
    pre.focus()
    await user.type(pre, ' extra')
    expect(h.onEditStep).toHaveBeenCalled()
    expect(h.onEditStep.mock.calls[0][0]).toBe('step-02')
    expect(h.onEditStep.mock.calls[0][1]).toHaveProperty('command')
  })

  it('changing the parent select calls onSetCausalityParent', async () => {
    const user = userEvent.setup()
    const h = renderInspector()
    await user.selectOptions(screen.getByLabelText(/Causality parent for step-02/), '')
    expect(h.onSetCausalityParent).toHaveBeenCalledWith('step-02', null, 'process_lineage')
  })

  it('changing the pivot select calls onSetCausalityParent with the pivot', async () => {
    const user = userEvent.setup()
    const h = renderInspector()
    await user.selectOptions(screen.getByLabelText(/Causality pivot for step-02/), 'temporal')
    expect(h.onSetCausalityParent).toHaveBeenCalledWith('step-02', 'step-01', 'temporal')
  })

  it('Browse TTP cards binds via onBindTtp, not a bare navigate', async () => {
    const user = userEvent.setup()
    const h = renderInspector()
    await user.click(screen.getByRole('button', { name: /Browse TTP cards/i }))
    expect(h.onBindTtp).toHaveBeenCalledWith('step-02')
  })

  it('adding a detection commits a {plane,type,description} object', async () => {
    const user = userEvent.setup()
    const h = renderInspector()
    await user.type(screen.getByLabelText('New detection description'), 'shadow read')
    await user.click(screen.getByRole('button', { name: /Add detection/i }))
    expect(h.onAddDetection).toHaveBeenCalledTimes(1)
    const [id, det] = h.onAddDetection.mock.calls[0]
    expect(id).toBe('step-02')
    expect(det).toMatchObject({ plane: expect.any(String), type: expect.any(String), description: 'shadow read' })
  })

  it('removing a detection calls onRemoveDetection with the index', async () => {
    const user = userEvent.setup()
    const h = renderInspector({ selected: STEP_01 })
    await user.click(screen.getByRole('button', { name: /Remove detection 1 from step-01/i }))
    expect(h.onRemoveDetection).toHaveBeenCalledWith('step-01', 0)
  })

  it('a TTP ref on a detection deep-links into the TTP surface', async () => {
    const user = userEvent.setup()
    const h = renderInspector({ selected: STEP_01 })
    await user.click(screen.getByRole('button', { name: 'TTP-2026-0032' }))
    expect(h.onNavigate).toHaveBeenCalledWith('ttps', { ttp: 'TTP-2026-0032' })
  })
})

describe('ComposerInspector — per-step stitch consume', () => {
  const STEP_CONSUMES = {
    ...STEP_02,
    command: 'curl --local-port {stitch:src_port} https://x:{stitch:dst_port}/beacon',
  }

  it('lists the {stitch:*} keys the command references, badged planted/unplanted', () => {
    // src_port is planted; dst_port is not → one planted, one unplanted warning.
    renderInspector({ selected: STEP_CONSUMES, stitchModel: { src_port: { resolve: 'auto_port' } } })
    const section = screen.getByTestId('step-stitch-consume')
    expect(within(section).getByTestId('step-stitch-key-src_port').textContent).toMatch(/planted/)
    const dst = within(section).getByTestId('step-stitch-key-dst_port')
    expect(dst.textContent).toMatch(/unplanted/)
    // the unplanted badge is the honest "left verbatim at launch" warning
    expect(dst.getAttribute('title')).toMatch(/left verbatim at launch/i)
  })

  it('offers insert buttons for planted keys the command does not yet consume', async () => {
    const user = userEvent.setup()
    const onInsertStitch = vi.fn()
    renderInspector({
      selected: STEP_02, // command 'cat /etc/shadow' — consumes nothing
      stitchModel: { account: { resolve: 'canary_principal' } },
      onInsertStitch,
    })
    await user.click(screen.getByRole('button', { name: /Insert stitch account into step-02/i }))
    expect(onInsertStitch).toHaveBeenCalledWith('step-02', 'account')
  })

  it('says a command references no shared entity when it has no placeholders', () => {
    renderInspector({ selected: STEP_02, stitchModel: null })
    expect(within(screen.getByTestId('step-stitch-consume'))
      .getByText(/references no shared entity/i)).toBeInTheDocument()
  })
})

describe('ComposerInspector — workflow meta hosts the stitch panel', () => {
  it('renders the ComposerStitchPanel after the CGO anchor', () => {
    renderInspector({ selected: null, stitchModel: null })
    expect(screen.getByTestId('composer-stitch-panel')).toBeInTheDocument()
    // and it sits after the CGO input in DOM order
    const cgo = screen.getByLabelText('CGO anchor')
    const panel = screen.getByTestId('composer-stitch-panel')
    expect(cgo.compareDocumentPosition(panel) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('wires a stitch edit through onSetStitchEntity', async () => {
    const user = userEvent.setup()
    const onSetStitchEntity = vi.fn()
    renderInspector({ selected: null, onSetStitchEntity })
    await user.selectOptions(screen.getByLabelText('Resolve directive for account'), 'canary_principal')
    expect(onSetStitchEntity).toHaveBeenCalledWith('account', { resolve: 'canary_principal' })
  })
})

describe('ComposerInspector — workflow meta (no step selected)', () => {
  it('edits the plane through onEditMeta', async () => {
    const user = userEvent.setup()
    const h = renderInspector({ selected: null })
    await user.selectOptions(screen.getByLabelText('Plane'), 'CDR')
    expect(h.onEditMeta).toHaveBeenCalledWith({ plane: 'CDR' })
  })

  it('offers a deep link into the UC/TC Index for tc_ref binding', async () => {
    const user = userEvent.setup()
    const h = renderInspector({ selected: null })
    await user.click(screen.getByRole('button', { name: /Browse UC\/TC Index/i }))
    expect(h.onNavigate).toHaveBeenCalledWith('uctc')
  })

  it('edits tc_ref through onEditMeta', async () => {
    const user = userEvent.setup()
    const h = renderInspector({ selected: null })
    const input = screen.getByLabelText('Bind tc_ref')
    await user.type(input, 'X')
    expect(h.onEditMeta).toHaveBeenCalled()
    expect(h.onEditMeta.mock.calls[0][0]).toHaveProperty('tcRef')
  })
})

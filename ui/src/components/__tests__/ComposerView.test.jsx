/**
 * ComposerView — the surface on which a chain is BUILT.
 *
 * The behaviours pinned here are the ones that make the Composer safe to put
 * in front of a customer:
 *   - it seeds from a REAL scenario, never a built-in demo chain
 *   - a failed load says so, instead of rendering as "this scenario is empty"
 *   - a step with no expected detection is visible on the canvas, not just in
 *     the inspector, because that step becomes a gap in the POV readout
 *   - a hand-edited draft CANNOT be launched, because SimCore would run the
 *     original chain while the canvas showed the edited one
 */
import { describe, it, expect, beforeEach, beforeAll, vi } from 'vitest'
import { render, screen, waitFor, within, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { installRoutes } from '../../test/mockFetch.js'
import { EnvironmentProvider } from '../../context/EnvironmentContext.jsx'
import ComposerView from '../console/ComposerView.jsx'
// getRunCausality is imported directly by ComposerView (not prop-injected), so
// the Task 13 "Run lens scoping" tests below spy on it via a partial module
// mock — every other export stays the real implementation.
import { getRunCausality as mockGetRunCausality } from '../../api/client.js'

vi.mock('../../api/client.js', async (importOriginal) => {
  const actual = await importOriginal()
  return { ...actual, getRunCausality: vi.fn() }
})

// ComposerView renders ComposerCanvas, whose Design lens mounts React Flow
// (Task 8). jsdom reports 0x0 for offsetWidth/offsetHeight, which makes
// React Flow refuse to render any node — see the identical stub and
// rationale in ComposerCanvas.test.jsx. Scoped to this file rather than the
// shared `src/test/setup.js`: each test file gets its own fresh jsdom
// environment, so this cannot leak into unrelated suites.
beforeAll(() => {
  window.ResizeObserver = window.ResizeObserver || class {
    observe() {} unobserve() {} disconnect() {}
  }
  Object.defineProperty(HTMLElement.prototype, 'offsetWidth', { configurable: true, value: 1200 })
  Object.defineProperty(HTMLElement.prototype, 'offsetHeight', { configurable: true, value: 800 })
})

const SCENARIO = {
  scenario_id: 'SIM-EDR-001',
  name: 'Credential Dumping — /etc/shadow',
  plane: 'EDR',
  uc_ref: 'UCS-EDR-02',
  tc_ref: 'TC-EDR-03',
  cgo_anchor: { image_name: 'apache2', primary_username: 'www-data' },
  execution_identity: { default: 'www-data', options: ['www-data', 'root'] },
  pull_supported: true,
  push_supported: true,
  cleanup: { commands: ['rm -f /tmp/mimipenguin.sh'] },
  steps: [
    {
      id: 'step-01',
      name: 'Read /etc/passwd for user enumeration',
      command: 'cat /etc/passwd',
      identity: 'www-data',
      mitre_technique: 'T1087.001',
      expected_detections: [
        { plane: 'EDR', type: 'XQL', description: 'passwd read', ttp_ref: 'TTP-2026-0032', detection_id: 'xql-1' },
      ],
    },
    {
      id: 'step-02',
      name: 'Attempt to read /etc/shadow',
      command: 'cat /etc/shadow',
      identity: 'www-data',
      mitre_technique: 'T1003.008',
      causality: { parent_step: 'step-01', pivot: 'process_lineage' },
      expected_detections: [],
    },
  ],
}

function baseRoutes(extra = {}) {
  return installRoutes({
    'GET /api/health': { status: 'ok', components: {} },
    'GET /api/scenarios': { scenarios: [SCENARIO] },
    'GET /api/runs': [],
    'GET /api/agents': [{ id: 'a1', hostname: 'web-prod-01', os: 'linux', status: 'online' }],
    'GET /api/credentials/integrations': [],
    'GET /api/shelf/payloads': { payloads: [] },
    'GET /api/shelf/artifacts': { artifacts: [] },
    'GET /api/scenarios/SIM-EDR-001': SCENARIO,
    ...extra,
  })
}

function mount(params = {}, props = {}) {
  return render(
    <EnvironmentProvider>
      <ComposerView params={params} {...props} />
    </EnvironmentProvider>,
  )
}

beforeEach(() => {
  window.localStorage.clear()
})

describe('ComposerView — empty state', () => {
  it('offers three real ways to begin instead of a blank canvas', async () => {
    baseRoutes()
    mount()
    await waitFor(() => expect(screen.getByTestId('composer-firstrun')).toBeInTheDocument())
    expect(screen.getByText(/Start from a library scenario/i)).toBeInTheDocument()
    expect(screen.getByText(/Start from a TTP card/i)).toBeInTheDocument()
    expect(screen.getByText(/Start from a blank step/i)).toBeInTheDocument()
  })

  it('an empty chain reports itself INCOMPLETE, never "valid"', async () => {
    baseRoutes()
    mount()
    await waitFor(() => expect(screen.getByTestId('composer-validation')).toBeInTheDocument())
    expect(screen.getByTestId('composer-validation')).toHaveTextContent(/Chain incomplete/i)
    expect(screen.getByTestId('composer-validation')).toHaveTextContent(/empty/i)
  })

  it('routes the three start options to the surfaces that do them', async () => {
    const user = userEvent.setup()
    const onNavigate = vi.fn()
    baseRoutes()
    mount({}, { onNavigate })
    await waitFor(() => expect(screen.getByTestId('composer-firstrun')).toBeInTheDocument())
    await user.click(screen.getByText(/Start from a library scenario/i))
    expect(onNavigate).toHaveBeenCalledWith('library')
    await user.click(screen.getByText(/Start from a TTP card/i))
    expect(onNavigate).toHaveBeenCalledWith('ttps')
  })
})

describe('ComposerView — seeded from a real scenario', () => {
  it('builds the chain from the API, with START and END anchors around it', async () => {
    baseRoutes()
    mount({ from: 'SIM-EDR-001' })
    await waitFor(() => expect(screen.getByTestId('composer-chain')).toBeInTheDocument())
    expect(screen.getByTestId('chain-start')).toBeInTheDocument()
    expect(screen.getByTestId('chain-end')).toBeInTheDocument()
    expect(screen.getByTestId('chain-step-step-01')).toBeInTheDocument()
    expect(screen.getByTestId('chain-step-step-02')).toBeInTheDocument()
  })

  it('names what the draft proves — test case, use case, plane', async () => {
    baseRoutes()
    mount({ from: 'SIM-EDR-001' })
    await waitFor(() => expect(screen.getByText('TC-EDR-03')).toBeInTheDocument())
    expect(screen.getByText('UCS-EDR-02')).toBeInTheDocument()
    expect(screen.getByText('EDR')).toBeInTheDocument()
  })

  it('shows the CGO anchor, so the chain reads as one causal spine', async () => {
    baseRoutes()
    mount({ from: 'SIM-EDR-001' })
    await waitFor(() => expect(screen.getByTestId('composer-chain')).toBeInTheDocument())
    expect(screen.getByText(/CGO apache2 \/ www-data/)).toBeInTheDocument()
  })

  it('marks the step with NO expected detection on the canvas itself', async () => {
    // The whole point: a DC must see the future gap without opening anything.
    baseRoutes()
    mount({ from: 'SIM-EDR-001' })
    await waitFor(() => expect(screen.getByTestId('chain-step-step-02')).toBeInTheDocument())
    expect(within(screen.getByTestId('chain-step-step-02'))
      .getByText(/no expected detection/i)).toBeInTheDocument()
    expect(screen.getByTestId('composer-validation')).toHaveTextContent(/step-02/)
  })

  it('carries the real scope into the START node', async () => {
    baseRoutes()
    mount({ from: 'SIM-EDR-001' })
    await waitFor(() => expect(screen.getByTestId('chain-start')).toBeInTheDocument())
    const start = screen.getByTestId('chain-start')
    expect(within(start).getByText('Tenant')).toBeInTheDocument()
    expect(within(start).getByText('Agent')).toBeInTheDocument()
  })

  it('wires a stored composer_layout through to the React Flow canvas (gap fix, Task 8)', async () => {
    // Task 5 produces `draft.layout` (from the scenario's `composer_layout`);
    // nothing before Task 8 read it back — a DC-dragged position would
    // persist to the backend and then never re-apply, so the feature would
    // look correct and be inert. This is the ComposerView -> ComposerCanvas
    // leg of that wire, against a REAL scenario load (not a prop stub).
    baseRoutes({
      'GET /api/scenarios/SIM-EDR-001': { ...SCENARIO, composer_layout: { 'step-02': { x: 555, y: 111 } } },
    })
    mount({ from: 'SIM-EDR-001' })
    await waitFor(() => expect(screen.getByTestId('chain-step-step-02')).toBeInTheDocument())
    const node = document.querySelector('[data-id="step-02"]')
    expect(node).toBeTruthy()
    expect(node.style.transform).toContain('555')
    expect(node.style.transform).toContain('111')
  })
})

describe('ComposerView — a failed load is not an empty scenario', () => {
  it('says the scenario could not be READ, rather than showing an empty canvas', async () => {
    baseRoutes({
      'GET /api/scenarios/SIM-EDR-001': () => new Response(
        JSON.stringify({ detail: 'boom' }),
        { status: 500, headers: { 'content-type': 'application/json' } },
      ),
    })
    mount({ from: 'SIM-EDR-001' })
    await waitFor(() => expect(screen.getByTestId('composer-origin-error')).toBeInTheDocument())
    expect(screen.getByTestId('composer-origin-error'))
      .toHaveTextContent(/not because the scenario has no steps/i)
  })
})

describe('ComposerView — editing the chain', () => {
  it('adds a step, and the new step is missing both command and detection', async () => {
    const user = userEvent.setup()
    baseRoutes()
    mount({ from: 'SIM-EDR-001' })
    await waitFor(() => expect(screen.getByTestId('composer-chain')).toBeInTheDocument())
    await user.click(screen.getByTestId('composer-add-step'))
    await waitFor(() => expect(screen.getByTestId('chain-step-step-03')).toBeInTheDocument())
    expect(screen.getByTestId('composer-validation')).toHaveTextContent(/step-03/)
  })

  it('reorders steps with the node controls', async () => {
    const user = userEvent.setup()
    baseRoutes()
    mount({ from: 'SIM-EDR-001' })
    await waitFor(() => expect(screen.getByTestId('composer-chain')).toBeInTheDocument())
    const order = () => Array.from(document.querySelectorAll('[data-testid^="chain-step-"]'))
      .map((el) => el.getAttribute('data-testid'))
    expect(order()).toEqual(['chain-step-step-01', 'chain-step-step-02'])
    await user.click(screen.getByLabelText('Move step-01 later'))
    expect(order()).toEqual(['chain-step-step-02', 'chain-step-step-01'])
  })

  it('removes a step', async () => {
    const user = userEvent.setup()
    baseRoutes()
    mount({ from: 'SIM-EDR-001' })
    await waitFor(() => expect(screen.getByTestId('chain-step-step-02')).toBeInTheDocument())
    await user.click(screen.getByLabelText('Remove step-02'))
    await waitFor(() =>
      expect(screen.queryByTestId('chain-step-step-02')).not.toBeInTheDocument())
    // Removing the only offending step makes the chain valid.
    expect(screen.getByTestId('composer-validation')).toHaveTextContent(/Chain valid/i)
  })

  it('refuses to launch an UNSAVED or UN-TC-BOUND draft, and says why', async () => {
    // Launch runs the SAVED draft row, not the canvas. So an edited-but-unsaved
    // chain is refused (leg 1), and a saved-but-un-tc-bound one is refused with
    // the server's own reason (leg 2) — never posted as a false claim in a
    // customer-facing report.
    const user = userEvent.setup()
    baseRoutes({
      // Leg 2: the save persists, but the server's launch gate says the draft
      // is not tc-bound and names the fix.
      'POST /api/scenarios/drafts': () => new Response(
        JSON.stringify({
          scenario_id: 'SIM-DRAFT-credential-dumping',
          status: 'draft',
          launchable: {
            launchable: false,
            chain_valid: true,
            tc_bound: false,
            refusal_code: 'DRAFT_NOT_TC_BOUND',
            reasons: ['bind tc_ref to a real FY27 index test case'],
          },
        }),
        { status: 201, headers: { 'content-type': 'application/json' } },
      ),
    })
    mount({ from: 'SIM-EDR-001' })
    await waitFor(() => expect(screen.getByTestId('composer-chain')).toBeInTheDocument())

    // Leg 1 — an unsaved edit cannot launch, and the reason names the save.
    await user.click(screen.getByTestId('composer-add-step'))
    await user.click(screen.getByTestId('composer-preflight'))
    const launch = screen.getByTestId('composer-launch')
    expect(launch).toBeDisabled()
    expect(launch.getAttribute('title')).toMatch(/save the draft|not launchable|un-?bound|tc_ref/i)

    // Leg 2 — after saving, the server reports the draft is not tc-bound; the
    // button stays disabled and the title names the tc_ref fix.
    await user.click(screen.getByTestId('composer-save-draft'))
    await waitFor(() => {
      const l = screen.getByTestId('composer-launch')
      expect(l).toBeDisabled()
      expect(l.getAttribute('title')).toMatch(/tc_ref|not launchable|un-?bound/i)
    })
  })

  it('keeps Launch disabled until preflight has actually run', async () => {
    baseRoutes()
    mount({ from: 'SIM-EDR-001' })
    await waitFor(() => expect(screen.getByTestId('composer-chain')).toBeInTheDocument())
    const launch = screen.getByTestId('composer-launch')
    expect(launch).toBeDisabled()
    expect(launch.getAttribute('title')).toMatch(/Run preflight first/i)
  })
})

describe('ComposerView — inspector', () => {
  it('shows the selected step config, and "not declared" where a field is absent', async () => {
    const user = userEvent.setup()
    baseRoutes()
    mount({ from: 'SIM-EDR-001' })
    await waitFor(() => expect(screen.getByTestId('chain-step-step-02')).toBeInTheDocument())
    await user.click(screen.getByTestId('chain-step-step-02'))
    expect(screen.getByText('cat /etc/shadow')).toBeInTheDocument()
    expect(screen.getByText(/parent step-01 · pivot process_lineage/)).toBeInTheDocument()
    // step-02 declares no platforms — absent must read as absent, not blank.
    expect(screen.getAllByText(/not declared/i).length).toBeGreaterThan(0)
  })

  it('explains the consequence of a step with no expected detection', async () => {
    const user = userEvent.setup()
    baseRoutes()
    mount({ from: 'SIM-EDR-001' })
    await waitFor(() => expect(screen.getByTestId('chain-step-step-02')).toBeInTheDocument())
    await user.click(screen.getByTestId('chain-step-step-02'))
    expect(screen.getByText(/will execute and then be\s+reported as a gap/i)).toBeInTheDocument()
  })

  it('shows scenario-level teardown, labelled as scenario-level', async () => {
    baseRoutes()
    mount({ from: 'SIM-EDR-001' })
    await waitFor(() => expect(screen.getByTestId('composer-chain')).toBeInTheDocument())
    expect(screen.getByText(/the schema has no per-step cleanup/i)).toBeInTheDocument()
    expect(screen.getByText(/rm -f \/tmp\/mimipenguin\.sh/)).toBeInTheDocument()
  })
})

describe('ComposerView — Stitch Context wiring', () => {
  it('opens the stitch panel from the workflow-meta view and authors an entity', async () => {
    const user = userEvent.setup()
    baseRoutes()
    mount({ from: 'SIM-EDR-001' })
    await waitFor(() => expect(screen.getByTestId('composer-chain')).toBeInTheDocument())
    // NoSelectionAside → open the editable workflow meta, which hosts the panel.
    await user.click(screen.getByRole('button', { name: /edit the workflow meta/i }))
    await waitFor(() => expect(screen.getByTestId('composer-stitch-panel')).toBeInTheDocument())
    // Author a canary principal on account; the panel is a pure view, the model
    // lives in ComposerView's draftMeta overlay.
    await user.selectOptions(
      screen.getByLabelText('Resolve directive for account'),
      'canary_principal',
    )
    // The choice sticks (the select now reflects the authored value).
    expect(screen.getByLabelText('Resolve directive for account')).toHaveValue('canary_principal')
  })

  it('toggles the design-lens stitch overlay from the canvas head', async () => {
    const user = userEvent.setup()
    baseRoutes()
    mount({ from: 'SIM-EDR-001' })
    await waitFor(() => expect(screen.getByTestId('composer-chain')).toBeInTheDocument())
    const toggle = screen.getByTestId('composer-stitch-toggle')
    expect(toggle).toHaveAttribute('aria-pressed', 'false')
    await user.click(toggle)
    expect(toggle).toHaveAttribute('aria-pressed', 'true')
  })
})

describe('ComposerView — YAML view and workstream', () => {
  it('emits real YAML for the current chain', async () => {
    const user = userEvent.setup()
    baseRoutes()
    mount({ from: 'SIM-EDR-001' })
    await waitFor(() => expect(screen.getByTestId('composer-chain')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'YAML' }))
    const yaml = screen.getByTestId('composer-yaml')
    expect(yaml).toHaveTextContent('- id: step-01')
    expect(yaml).toHaveTextContent('NO EXPECTED DETECTION')
  })

  it('opens the workstream on a tab click and reports the real shelf state', async () => {
    const user = userEvent.setup()
    baseRoutes()
    mount({ from: 'SIM-EDR-001' })
    await waitFor(() => expect(screen.getByTestId('ws-tab-payload')).toBeInTheDocument())
    await user.click(screen.getByTestId('ws-tab-payload'))
    await waitFor(() => expect(screen.getByTestId('composer-ws-body')).toBeInTheDocument())
    expect(screen.getByText(/Nothing staged on this SimCore/i)).toBeInTheDocument()
  })

  it('preflight reports the chain verdict alongside component health', async () => {
    const user = userEvent.setup()
    baseRoutes()
    mount({ from: 'SIM-EDR-001' })
    await waitFor(() => expect(screen.getByTestId('composer-preflight')).toBeInTheDocument())
    await user.click(screen.getByTestId('composer-preflight'))
    await waitFor(() => expect(screen.getByTestId('composer-ws-body')).toBeInTheDocument())
    expect(screen.getByTestId('composer-ws-body')).toHaveTextContent(/Chain:/)
    expect(screen.getByTestId('composer-ws-body')).toHaveTextContent(/step-02/)
  })

  it('history is empty-but-honest when no run exists', async () => {
    const user = userEvent.setup()
    baseRoutes()
    mount({ from: 'SIM-EDR-001' })
    await waitFor(() => expect(screen.getByTestId('ws-tab-history')).toBeInTheDocument())
    await user.click(screen.getByTestId('ws-tab-history'))
    expect(screen.getByText(/No runs yet/i)).toBeInTheDocument()
  })
})

describe('ComposerView — drawing causality edges maintains array order (Task 10, addition A)', () => {
  // A fork off a single root (step-02 and step-03 both children of step-01)
  // — the shape needed to prove the "spine parity gap": `canConnect`
  // approves re-parenting step-02 onto step-03 (no cycle, no self-ref), but
  // step-03 sits AFTER step-02 in `steps[]`, which is exactly what
  // `setCausalityParent`'s own forward-ref guard would otherwise silently
  // refuse. `handleConnectSteps` must apply the edge AND restore array order,
  // or the drag would visually succeed (no refusal banner) while doing
  // nothing — the failure mode this whole task exists to prevent.
  const FORK_SCENARIO = {
    ...SCENARIO,
    scenario_id: 'SIM-EDR-FORK',
    steps: [
      {
        id: 'step-01', name: 'Root', command: 'true', identity: 'www-data',
        expected_detections: [],
      },
      {
        id: 'step-02', name: 'Branch A', command: 'true', identity: 'www-data',
        causality: { parent_step: 'step-01', pivot: 'process_lineage' },
        expected_detections: [],
      },
      {
        id: 'step-03', name: 'Branch B', command: 'true', identity: 'www-data',
        causality: { parent_step: 'step-01', pivot: 'process_lineage' },
        expected_detections: [],
      },
    ],
  }

  it('re-parenting step-02 onto step-03 (which sits AFTER it) reorders the array instead of silently dropping the edge', async () => {
    baseRoutes({
      'GET /api/scenarios': { scenarios: [FORK_SCENARIO] },
      'GET /api/scenarios/SIM-EDR-FORK': FORK_SCENARIO,
    })
    mount({ from: 'SIM-EDR-FORK' })
    await waitFor(() => expect(screen.getByTestId('chain-step-step-03')).toBeInTheDocument())

    const order = () => Array.from(document.querySelectorAll('[data-testid^="chain-step-"]'))
      .map((el) => el.getAttribute('data-testid'))
    // Before: authored order is step-01, step-02, step-03 (step-03 after step-02).
    expect(order()).toEqual(['chain-step-step-01', 'chain-step-step-02', 'chain-step-step-03'])

    // Draw the canvas edge: step-03 becomes step-02's parent.
    act(() => { window.__rfOnConnect({ source: 'step-03', target: 'step-02' }) })

    // After: the edge applied (no refusal banner) AND the array was
    // re-sorted so the new parent (step-03) precedes its child (step-02) —
    // the exact rule core/engine/scenario_loader.py:394 enforces.
    expect(screen.queryByTestId('canvas-refusal')).not.toBeInTheDocument()
    await waitFor(() => {
      const ids = order()
      expect(ids.indexOf('chain-step-step-03')).toBeLessThan(ids.indexOf('chain-step-step-02'))
    })
    // step-01 (the untouched root) still precedes everything.
    expect(order().indexOf('chain-step-step-01')).toBe(0)

    // The inspector confirms the actual causalityParent field, not just DOM order.
    await userEvent.setup().click(screen.getByTestId('chain-step-step-02'))
    expect(screen.getByText(/parent step-03/)).toBeInTheDocument()
  })

  // Task 10 fixed the CANVAS drag path (above) by composing `skipOrderCheck:
  // true` with `topologicallySortSteps`. `onSetCausalityParent` — the
  // INSPECTOR's manual "parent step" picker (`ComposerInspector.jsx`) — wired
  // straight to `setCausalityParent` with no such composition, so the exact
  // same forward-ref guard (`composerDraft.js`'s `setCausalityParent`) that
  // the canvas path works around would silently no-op the Inspector path
  // instead: no change, no error, no explanation. This is the asymmetry
  // fixed here — same composition, same guarantee, on both affordances.
  // (Fix round 1: `ComposerInspector.jsx`'s own option-list filter was ALSO
  // broadened from array-position to graph legality, so this now goes
  // through step-03 as a genuinely OFFERED <option>, not an injected one —
  // see `ComposerInspector.test.jsx` for the option-list-level coverage.)
  it('Inspector re-parenting step-02 onto step-03 (which sits AFTER it) reorders the array instead of silently dropping the edit', async () => {
    baseRoutes({
      'GET /api/scenarios': { scenarios: [FORK_SCENARIO] },
      'GET /api/scenarios/SIM-EDR-FORK': FORK_SCENARIO,
    })
    mount({ from: 'SIM-EDR-FORK' })
    await waitFor(() => expect(screen.getByTestId('chain-step-step-03')).toBeInTheDocument())

    const order = () => Array.from(document.querySelectorAll('[data-testid^="chain-step-"]'))
      .map((el) => el.getAttribute('data-testid'))
    expect(order()).toEqual(['chain-step-step-01', 'chain-step-step-02', 'chain-step-step-03'])

    const user = userEvent.setup()
    await user.click(screen.getByTestId('chain-step-step-02'))
    const parentSelect = screen.getByLabelText('Causality parent for step-02')

    // ComposerInspector.jsx's "parent step" <option> list is graph-legal
    // (`canConnect`), not array-position-limited (fix round 1) — step-03 is
    // a legitimate sibling of step-02 (both children of step-01: no
    // self-ref, no cycle), so it is genuinely OFFERED here despite sitting
    // AFTER step-02 in `steps[]`. Selecting it through the real rendered
    // <option>, with no DOM workaround, proves both halves end-to-end: the
    // option reaches the DC, and the composed callback
    // (onSetCausalityParent -> setCausalityParent + resort) applies it.
    expect(within(parentSelect).getByRole('option', { name: 'step-03' })).toBeInTheDocument()
    await user.selectOptions(parentSelect, 'step-03')

    await waitFor(() => {
      const ids = order()
      expect(ids.indexOf('chain-step-step-03')).toBeLessThan(ids.indexOf('chain-step-step-02'))
    })
    expect(order().indexOf('chain-step-step-01')).toBe(0)
    expect(screen.getByText(/parent step-03/)).toBeInTheDocument()
  })
})

describe('ComposerView — Re-layout control (Task 11)', () => {
  const LAYOUT_SCENARIO = {
    ...SCENARIO,
    scenario_id: 'SIM-EDR-LAYOUT',
    composer_layout: { 'step-02': { x: 480, y: 260 } },
  }

  it('is disabled with no stored layout, and clears stored positions back to computed on click', async () => {
    const user = userEvent.setup()
    baseRoutes()
    mount({ from: 'SIM-EDR-001' })
    await waitFor(() => expect(screen.getByTestId('composer-relayout')).toBeInTheDocument())
    expect(screen.getByTestId('composer-relayout')).toBeDisabled()
    expect(screen.getByTestId('composer-canvas').dataset.storedLayout).toBe('none')
  })

  it('Re-layout clears stored positions back to computed', async () => {
    const user = userEvent.setup()
    baseRoutes({
      'GET /api/scenarios': { scenarios: [LAYOUT_SCENARIO] },
      'GET /api/scenarios/SIM-EDR-LAYOUT': LAYOUT_SCENARIO,
    })
    mount({ from: 'SIM-EDR-LAYOUT' })
    await waitFor(() => expect(screen.getByTestId('composer-relayout')).toBeInTheDocument())
    expect(screen.getByTestId('composer-relayout')).not.toBeDisabled()
    expect(screen.getByTestId('composer-canvas').dataset.storedLayout).toBe('set')

    await user.click(screen.getByTestId('composer-relayout'))
    expect(screen.getByTestId('composer-canvas').dataset.storedLayout).toBe('none')
    expect(screen.getByTestId('composer-relayout')).toBeDisabled()
  })
})

describe('ComposerView — Run lens scoped to the open draft (Task 13)', () => {
  // Diagnosis: docs/superpowers/plans/2026-09-08-run-lens-diagnosis.md
  // Brief:     .superpowers/sdd/2026-09-08-composer-direct-manipulation/task-13-brief.md
  //
  // Field-name note (brief Step 1, verified live against localhost:8888, a
  // running SimCore — not assumed): `curl -s localhost:8888/api/runs` rows
  // carry run_id / scenario_id / status / started_at, exactly as the brief
  // guessed. What the brief could NOT know: `env.activeRun` (the value these
  // tests exercise) is not a raw run row at all — it's a DERIVED view-model
  // built by EnvironmentContext.jsx's `activeRun` useMemo, and that object
  // carries runId / scenarioId (camelCase) with NO run_id/scenario_id/status
  // fields (confirmed by grep across AppConsole.jsx, ConsoleHeader.jsx,
  // RunDetailView.jsx, and ComposerView.jsx's own existing `activeRun.runId`
  // reads at ~line 1033). `renderComposer` below drives the REAL
  // EnvironmentProvider + a mocked GET /api/runs, so `env.activeRun` in the
  // component under test is the real derived shape, not the brief's guessed
  // raw-row shape — the brief's test bodies are adapted (helper + async
  // waitFor) to run against that reality, not copied as literally-sync
  // pseudocode, since ComposerView loads its origin scenario over a mocked
  // network fetch and cannot render synchronously.
  const SCEN_ID = SCENARIO.scenario_id // 'SIM-EDR-001' — matches the brief's SCENARIO const

  function runRow(over = {}) {
    return {
      run_id: 'r1', scenario_id: SCEN_ID, status: 'completed',
      started_at: '2026-09-08T12:00:00Z', ...over,
    }
  }

  // Folds a test's `activeRun` (in-flight run) description into a `status:
  // 'running'` row for the mocked GET /api/runs — EnvironmentContext derives
  // its own real `env.activeRun` from exactly that list, so this is what
  // drives the REAL derivation rather than injecting a fake shape directly.
  function activeRunRow(activeRun) {
    if (!activeRun) return null
    return {
      run_id: activeRun.run_id || 'active',
      scenario_id: activeRun.scenario_id,
      status: activeRun.status || 'running',
      started_at: activeRun.started_at || '2026-09-08T12:00:00Z',
      step: activeRun.step,
      detected: activeRun.detected,
    }
  }

  function renderComposer({ draft = {}, env = {}, getRunCausality: impl } = {}) {
    const scenarioId = draft.originId || SCEN_ID
    const runs = [...(env.runs || [])]
    const activeRow = activeRunRow(env.activeRun)
    if (activeRow) runs.push(activeRow)
    baseRoutes({ 'GET /api/runs': runs })
    mockGetRunCausality.mockReset()
    if (impl) mockGetRunCausality.mockImplementation((...args) => impl(...args))
    return mount({ from: scenarioId })
  }

  it('fetches causality for a TERMINAL run of the open scenario', async () => {
    // Fails today: env.activeRun is running-only, so a completed/failed run
    // never triggers the fetch and the canvas claims no run exists.
    const getRunCausalityImpl = vi.fn().mockResolvedValue({ nodes: [], edges: [] })
    renderComposer({
      draft: { originId: SCEN_ID },
      env: { runs: [runRow()], activeRun: null },
      getRunCausality: getRunCausalityImpl,
    })
    await waitFor(() => expect(getRunCausalityImpl).toHaveBeenCalledWith('r1'))
  })

  it("does NOT paint this canvas with another scenario's in-flight run", async () => {
    // Defect B: evidence contamination across scenarios.
    const getRunCausalityImpl = vi.fn()
    renderComposer({
      draft: { originId: SCEN_ID },
      env: {
        runs: [],
        activeRun: { run_id: 'other', scenario_id: 'SIM-CDR-009', status: 'running', step: 1, detected: 0 },
      },
      getRunCausality: getRunCausalityImpl,
    })
    // Give every pending async effect (scenario load, runs poll, the
    // causality effect itself) room to settle before asserting the negative.
    await waitFor(() => expect(screen.getByTestId('composer-chain')).toBeInTheDocument())
    expect(getRunCausalityImpl).not.toHaveBeenCalled()
  })

  it('prefers the in-flight run when it belongs to THIS scenario', async () => {
    const getRunCausalityImpl = vi.fn().mockResolvedValue({ nodes: [], edges: [] })
    renderComposer({
      draft: { originId: SCEN_ID },
      env: {
        runs: [runRow({ run_id: 'old' })],
        activeRun: { run_id: 'live', scenario_id: SCEN_ID, status: 'running', step: 2, detected: 1 },
      },
      getRunCausality: getRunCausalityImpl,
    })
    await waitFor(() => expect(getRunCausalityImpl).toHaveBeenCalledWith('live'))
  })

  it('shows "no run yet" only when NO run exists for this scenario', async () => {
    const user = userEvent.setup()
    renderComposer({
      draft: { originId: SCEN_ID },
      env: { runs: [runRow({ scenario_id: 'SIM-CDR-009' })], activeRun: null },
    })
    await waitFor(() => expect(screen.getByTestId('composer-chain')).toBeInTheDocument())
    await user.click(screen.getByTestId('composer-lens-run'))
    await waitFor(() => expect(screen.getByTestId('composer-run-graph')).toBeInTheDocument())
    expect(screen.getByTestId('composer-run-graph').textContent).toMatch(/no run yet/i)
  })

  it('renders no drag grip in the Run lens — the lens is strictly read-only', async () => {
    // Reviewer-found gap folded into Task 13: nodesDraggable is false in the
    // Run lens, but the grip DOM rendered regardless (harmless, but reads as
    // a live affordance that does nothing).
    const user = userEvent.setup()
    renderComposer({
      draft: { originId: SCEN_ID },
      env: { runs: [runRow()], activeRun: null },
      getRunCausality: vi.fn().mockResolvedValue({ nodes: [], edges: [] }),
    })
    await waitFor(() => expect(screen.getByTestId('composer-chain')).toBeInTheDocument())
    expect(screen.getByTestId('chain-node-grip-step-01')).toBeInTheDocument()
    await user.click(screen.getByTestId('composer-lens-run'))
    await waitFor(() => expect(screen.getByTestId('composer-run-graph')).toBeInTheDocument())
    expect(screen.queryByTestId('chain-node-grip-step-01')).not.toBeInTheDocument()
  })
})

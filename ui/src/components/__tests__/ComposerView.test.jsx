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
})

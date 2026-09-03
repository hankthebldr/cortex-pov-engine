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
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { installRoutes } from '../../test/mockFetch.js'
import { EnvironmentProvider } from '../../context/EnvironmentContext.jsx'
import ComposerView from '../console/ComposerView.jsx'

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

  it('REFUSES to launch an edited draft, and says why', async () => {
    // SimCore would execute the ORIGINAL chain while the canvas showed the
    // edited one — a false claim in a customer-facing report.
    const user = userEvent.setup()
    baseRoutes()
    mount({ from: 'SIM-EDR-001' })
    await waitFor(() => expect(screen.getByTestId('composer-chain')).toBeInTheDocument())
    await user.click(screen.getByTestId('composer-add-step'))
    await user.click(screen.getByTestId('composer-preflight'))
    const launch = screen.getByTestId('composer-launch')
    expect(launch).toBeDisabled()
    expect(launch.getAttribute('title')).toMatch(/hand-edits SimCore does not have/i)
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
